"""Deterministic ownership and orchestration tests for SessionManager."""

from __future__ import annotations

import json
import threading

import pytest

from src.voice.manager import SessionManager
from src.voice.models import (
    FailureCode,
    Outcome,
    Result,
    SessionActivity,
    SessionLifecycle,
    VoiceMode,
)
from src.voice.session import VoiceSession


TOKEN = "fixed-owner-token-with-sufficient-test-entropy"


def fixed_token(_bytes):
    return TOKEN


def make_session(mode=VoiceMode.DICTATE):
    return VoiceSession(mode, input_category="microphone", clock=lambda: 100.0)


def start(manager, monkeypatch, session=None):
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)
    selected = session or make_session()
    started = manager.start(selected)
    assert started.ok
    assert started.owner_token == TOKEN
    return started, selected


def test_start_owns_preconfigured_session_and_never_preempts(monkeypatch):
    manager = SessionManager()
    first, session = start(manager, monkeypatch)
    second = manager.start(make_session(VoiceMode.READ))

    assert second.result.outcome is Outcome.REJECTED
    assert second.result.failure_code is FailureCode.BUSY
    assert second.result.retryable
    assert second.owner_token is None
    assert manager.session_for_owner(first.owner_token) is session
    assert manager.status.session_status["mode"] == "dictate"
    assert manager.cancel(first.owner_token).failure_code is FailureCode.CANCELLED


def test_start_rejects_unconfigured_or_nonpending_inputs(monkeypatch):
    manager = SessionManager()
    invalid = manager.start(VoiceMode.DICTATE)  # type: ignore[arg-type]
    assert invalid.result.failure_code is FailureCode.INVALID_REQUEST

    running = make_session()
    assert running.start().ok
    assert manager.start(running).result.failure_code is FailureCode.INVALID_REQUEST

    closed = make_session()
    closed.close()
    assert manager.start(closed).result.failure_code is FailureCode.INVALID_REQUEST


def test_concurrent_starts_have_exactly_one_owner(monkeypatch):
    manager = SessionManager()
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)
    barrier = threading.Barrier(17)
    results = []
    results_lock = threading.Lock()

    def race_start(index):
        barrier.wait()
        result = manager.start(
            VoiceSession(
                VoiceMode.DICTATE,
                agent_category="agent-{}".format(index),
                clock=lambda: 100.0,
            )
        )
        with results_lock:
            results.append(result)

    workers = [
        threading.Thread(target=race_start, args=(index,)) for index in range(16)
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()

    winners = [result for result in results if result.ok]
    busy = [
        result for result in results if result.result.failure_code is FailureCode.BUSY
    ]
    assert len(winners) == 1
    assert len(busy) == 15
    assert winners[0].owner_token == TOKEN
    assert all(result.owner_token is None for result in busy)


def test_token_is_exact_opaque_and_absent_from_status(monkeypatch):
    observed = []
    manager = SessionManager(status_callback=observed.append)
    started, session = start(manager, monkeypatch)

    for wrong in (None, TOKEN.encode(), "", TOKEN.upper(), TOKEN + "x", TOKEN[:-1]):
        assert (
            manager.acquire_microphone(wrong).failure_code
            is FailureCode.INVALID_REQUEST
        )
        assert manager.session_for_owner(wrong) is None

    assert manager.session_for_owner(TOKEN) is session
    assert TOKEN not in repr(started)
    assert TOKEN not in json.dumps(started.to_status_dict(), sort_keys=True)
    assert TOKEN not in repr(manager.status)
    assert TOKEN not in json.dumps(manager.status.to_status_dict(), sort_keys=True)
    assert all(TOKEN not in repr(status.to_status_dict()) for status in observed)
    assert not hasattr(manager, "owner_token")


def test_session_transitions_propagate_to_manager_observers(monkeypatch):
    observed = []
    manager = SessionManager(status_callback=observed.append)
    started, session = start(manager, monkeypatch)
    start_sequence = manager.status.sequence

    result = session.accept_transcript("bounded text", revision=1, final=True)

    assert result.ok
    assert manager.status.sequence > start_sequence
    assert manager.status.session_status["activity"] == "transcribing"
    assert manager.status.session_status["revision"] == 1
    assert observed[-1].session_status["sequence"] == session.status.sequence
    assert manager.session_for_owner(started.owner_token) is session


def test_reentrant_session_events_keep_exact_manager_order(monkeypatch):
    manager_events = []
    session = None

    def reentrant_session_observer(status):
        if status.sequence == 1:
            assert session is not None
            assert session.set_activity(SessionActivity.RECORDING).ok

    session = VoiceSession(
        VoiceMode.DICTATE,
        status_callback=reentrant_session_observer,
        clock=lambda: 100.0,
    )
    manager = SessionManager(status_callback=manager_events.append)
    start(manager, monkeypatch, session)

    assert [event.session_status["activity"] for event in manager_events] == [
        "listening",
        "recording",
    ]
    assert [event.session_status["sequence"] for event in manager_events] == [1, 2]


def test_resources_are_explicit_and_idempotent(monkeypatch):
    manager = SessionManager()
    started, _session = start(manager, monkeypatch)
    token = started.owner_token

    assert not manager.status.microphone_leased
    assert not manager.status.speaker_leased
    assert manager.acquire_microphone(token).ok
    acquired_sequence = manager.status.sequence
    assert manager.acquire_microphone(token).ok
    assert manager.status.sequence == acquired_sequence

    assert manager.acquire_speaker(token).ok
    assert manager.release_resource(token, "microphone").ok
    released_sequence = manager.status.sequence
    assert manager.release_microphone(token).ok
    assert manager.status.sequence == released_sequence
    assert not manager.status.microphone_leased
    assert manager.status.speaker_leased
    assert (
        manager.acquire_resource(token, "camera").failure_code
        is FailureCode.INVALID_REQUEST
    )


def test_terminal_session_releases_resources_without_spurious_cancel_events(
    monkeypatch,
):
    manager = SessionManager()
    started, session = start(manager, monkeypatch)
    token = started.owner_token
    assert manager.acquire_microphone(token).ok
    assert manager.acquire_speaker(token).ok

    terminal = session.complete()
    terminal_sequence = manager.status.sequence
    first = manager.cancel(token)
    second = manager.cancel(token)

    assert first is terminal
    assert second is terminal
    assert manager.status.sequence == terminal_sequence
    assert not manager.status.microphone_leased
    assert not manager.status.speaker_leased
    assert manager.release_microphone(token).ok
    assert manager.release_speaker(token).ok
    assert manager.status.sequence == terminal_sequence
    assert (
        manager.acquire_microphone(token).failure_code is FailureCode.INVALID_TRANSITION
    )


def test_cancel_releases_resources_and_is_idempotent(monkeypatch):
    manager = SessionManager()
    started, _session = start(manager, monkeypatch)
    token = started.owner_token
    manager.acquire_microphone(token)
    manager.acquire_speaker(token)

    first = manager.cancel(token)
    sequence = manager.status.sequence
    second = manager.cancel(token)

    assert first is second
    assert first.outcome is Outcome.CANCELLED
    assert first.failure_code is FailureCode.CANCELLED
    assert manager.status.sequence == sequence
    assert not manager.status.microphone_leased
    assert not manager.status.speaker_leased


def test_release_forgets_session_replays_exactly_and_allows_reuse(monkeypatch):
    manager = SessionManager()
    started, session = start(manager, monkeypatch)
    token = started.owner_token
    manager.acquire_microphone(token)

    first = manager.release(token)
    sequence = manager.status.sequence
    second = manager.release(token)

    assert first is second
    assert first.ok
    assert session.closed
    assert manager.status.sequence == sequence
    assert not manager.status.active
    assert manager.release(token + "wrong").failure_code is FailureCode.INVALID_REQUEST

    monkeypatch.setattr(
        "src.voice.manager.secrets.token_urlsafe", lambda _bytes: TOKEN + "-new"
    )
    restarted = manager.start(make_session(VoiceMode.READ))
    assert restarted.ok
    assert restarted.owner_token != token
    assert manager.cancel(token).failure_code is FailureCode.INVALID_REQUEST


def test_observers_run_outside_lock_and_reentrant_events_stay_ordered(monkeypatch):
    callback_entered = threading.Event()
    allow_callback_to_finish = threading.Event()
    events = []
    manager = SessionManager()

    def first_observer(status):
        events.append(("first", status.sequence))
        if status.sequence == 1:
            assert manager.acquire_microphone(TOKEN).ok
            callback_entered.set()
            assert allow_callback_to_finish.wait(2)

    def second_observer(status):
        events.append(("second", status.sequence))

    def broken_observer(_status):
        raise KeyboardInterrupt("observer must be best effort")

    manager.add_observer(first_observer)
    manager.add_observer(second_observer)
    manager.add_observer(broken_observer)
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)
    worker = threading.Thread(target=lambda: manager.start(make_session(VoiceMode.ASK)))
    worker.start()
    assert callback_entered.wait(2)

    reader = threading.Thread(target=lambda: manager.status.to_status_dict())
    reader.start()
    reader.join(timeout=1)
    assert not reader.is_alive()

    allow_callback_to_finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert events == [("first", 1), ("second", 1), ("first", 2), ("second", 2)]


def test_reentrant_host_shutdown_during_start_returns_no_stale_authority(monkeypatch):
    manager = SessionManager()

    def shutdown_on_start(status):
        if status.sequence == 1:
            assert manager.shutdown().ok

    manager.add_observer(shutdown_on_start)
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)
    started = manager.start(make_session())

    assert not started.ok
    assert started.owner_token is None
    assert started.result.failure_code is FailureCode.CLOSED
    assert started.status.closed
    assert not started.status.active


def test_close_requires_owner_token_and_shutdown_is_host_only(monkeypatch):
    manager = SessionManager()
    started, session = start(manager, monkeypatch)
    token = started.owner_token

    assert manager.close(token + "wrong").failure_code is FailureCode.INVALID_REQUEST
    assert not manager.closed
    first = manager.close(token)
    second = manager.close(token)

    assert first is second
    assert first.ok
    assert session.closed
    assert manager.status.closed
    assert not manager.status.active
    assert (
        manager.start(make_session(VoiceMode.READ)).result.failure_code
        is FailureCode.CLOSED
    )
    assert manager.cancel(token).failure_code is FailureCode.CLOSED
    assert manager.release(token).failure_code is FailureCode.CLOSED
    assert manager.acquire_resource(token, "invalid").failure_code is FailureCode.CLOSED

    empty = SessionManager()
    assert empty.shutdown().ok
    assert empty.shutdown().ok
    assert empty.status.closed


def test_manager_status_uses_only_closed_metadata(monkeypatch):
    manager = SessionManager()
    _started, session = start(manager, monkeypatch)
    assert session.set_activity(SessionActivity.RECORDING).ok

    encoded = json.dumps(manager.status.to_status_dict(), sort_keys=True)
    assert session.session_id not in encoded
    assert "recording" in encoded
    assert manager.status.session_status["lifecycle"] == SessionLifecycle.RUNNING.value


def test_shutdown_cannot_reentrantly_start_an_unreachable_session(monkeypatch):
    manager = SessionManager()
    replacement = make_session(VoiceMode.READ)
    nested_results = []

    def start_during_close(status):
        if status.lifecycle is SessionLifecycle.CANCELLED:
            nested_results.append(manager.start(replacement))

    session = VoiceSession(
        VoiceMode.DICTATE,
        status_callback=start_during_close,
        clock=lambda: 100.0,
    )
    start(manager, monkeypatch, session)

    assert manager.shutdown().ok
    assert manager.status.closed
    assert not manager.status.active
    assert len(nested_results) == 1
    assert nested_results[0].result.failure_code is FailureCode.CLOSED
    assert nested_results[0].owner_token is None
    assert replacement.status.lifecycle is SessionLifecycle.PENDING


def test_release_during_cancel_does_not_dereference_forgotten_session(monkeypatch):
    manager = SessionManager()
    started, _session = start(manager, monkeypatch)
    token = started.owner_token
    assert manager.acquire_microphone(token).ok

    def release_when_cancelled(status):
        session_status = status.session_status
        if session_status and session_status["lifecycle"] == "cancelled":
            manager.release(token)

    manager.add_observer(release_when_cancelled)
    result = manager.cancel(token)

    assert result.outcome is Outcome.CANCELLED
    assert not manager.status.active


def test_resource_acquire_does_not_return_stale_success_after_reentrant_release(
    monkeypatch,
):
    manager = SessionManager()
    started, session = start(manager, monkeypatch)
    token = started.owner_token

    def release_when_leased(status):
        if status.microphone_leased:
            manager.release(token)

    manager.add_observer(release_when_leased)
    acquired = manager.acquire_microphone(token)

    assert not acquired.ok
    assert acquired.failure_code is FailureCode.INVALID_REQUEST
    assert not manager.status.active
    assert session.closed


def test_non_ascii_tokens_fail_closed_without_compare_digest_errors(monkeypatch):
    manager = SessionManager()
    started, _session = start(manager, monkeypatch)
    for returns_session, operation in (
        (True, manager.session_for_owner),
        (False, manager.acquire_microphone),
        (False, manager.cancel),
        (False, manager.release),
        (False, manager.close),
    ):
        result = operation("é")
        if returns_session:
            assert result is None
        else:
            assert result.failure_code is FailureCode.INVALID_REQUEST
    assert manager.release(started.owner_token).ok
    assert manager.release("é").failure_code is FailureCode.INVALID_REQUEST


def test_start_rolls_back_callback_and_clock_base_exceptions(monkeypatch):
    class InterruptingSession(VoiceSession):
        def add_status_callback(self, callback):
            raise KeyboardInterrupt

    manager = SessionManager()
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)
    with pytest.raises(KeyboardInterrupt):
        manager.start(InterruptingSession(VoiceMode.DICTATE, clock=lambda: 100.0))
    assert not manager.status.active

    values = iter((100.0,))

    def failing_clock():
        try:
            return next(values)
        except StopIteration:
            raise RuntimeError("private clock detail")

    broken = VoiceSession(VoiceMode.DICTATE, clock=failing_clock)
    failed = manager.start(broken)
    assert failed.result.failure_code is FailureCode.INTERNAL
    assert failed.owner_token is None
    assert not manager.status.active
    assert manager.start(make_session()).ok


def test_start_reconciles_a_transition_during_callback_registration(monkeypatch):
    callback_installed = threading.Event()
    allow_registration_to_finish = threading.Event()

    class BlockingRegistrationSession(VoiceSession):
        def add_status_callback(self, callback):
            super().add_status_callback(callback)
            callback_installed.set()
            assert allow_registration_to_finish.wait(2)

    session = BlockingRegistrationSession(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager_events = []
    manager = SessionManager(status_callback=manager_events.append)
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("started", manager.start(session))
    )
    worker.start()
    assert callback_installed.wait(1)
    external = {}
    external_worker = threading.Thread(
        target=lambda: external.setdefault("started", VoiceSession.start(session))
    )
    external_worker.start()
    allow_registration_to_finish.set()
    worker.join(timeout=2)
    external_worker.join(timeout=2)

    assert not worker.is_alive()
    assert not external_worker.is_alive()
    assert external["started"].ok
    assert result["started"].ok
    assert manager.status.sequence >= 1
    assert manager.status.session_status["lifecycle"] == "running"
    assert manager.status.session_status["activity"] == "listening"
    assert manager.status.session_status["sequence"] == session.status.sequence
    seen_session_sequences = [
        event.session_status["sequence"]
        for event in manager_events
        if event.session_status is not None
    ]
    assert seen_session_sequences == sorted(set(seen_session_sequences))


def test_reentrant_session_close_cannot_leave_stale_manager_lease(monkeypatch):
    session = None

    def close_on_recording(status):
        if status.activity is SessionActivity.RECORDING:
            assert session is not None
            session.close()

    session = VoiceSession(
        VoiceMode.DICTATE,
        status_callback=close_on_recording,
        clock=lambda: 100.0,
    )
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    assert manager.acquire_microphone(started.owner_token).ok

    session.set_activity(SessionActivity.RECORDING)

    assert session.closed
    assert session.status.lifecycle is SessionLifecycle.CANCELLED
    assert manager.status.session_status["lifecycle"] == "cancelled"
    assert not manager.status.microphone_leased
    assert not manager.status.speaker_leased


def test_close_collaborator_runs_outside_manager_lock(monkeypatch):
    callback_entered = threading.Event()
    allow_callback_to_finish = threading.Event()

    def blocking_close_callback(status):
        if status.lifecycle is SessionLifecycle.CANCELLED:
            callback_entered.set()
            assert allow_callback_to_finish.wait(2)

    session = VoiceSession(
        VoiceMode.DICTATE,
        status_callback=blocking_close_callback,
        clock=lambda: 100.0,
    )
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault(
            "released", manager.release(started.owner_token)
        )
    )
    worker.start()
    assert callback_entered.wait(1)

    reader = threading.Thread(target=lambda: manager.status.to_status_dict())
    reader.start()
    reader.join(timeout=1)
    assert not reader.is_alive()
    assert manager.start(make_session()).result.failure_code is FailureCode.BUSY
    retry = manager.release(started.owner_token)
    assert retry.failure_code is FailureCode.BUSY
    assert retry.retryable
    concurrent_shutdown = manager.shutdown()
    assert concurrent_shutdown.failure_code is FailureCode.BUSY
    assert concurrent_shutdown.retryable

    allow_callback_to_finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result["released"].ok
    assert not manager.status.active
    assert manager.release(started.owner_token) is result["released"]


def test_shutdown_never_reports_complete_before_private_session_is_closed(monkeypatch):
    callback_entered = threading.Event()
    allow_callback_to_finish = threading.Event()

    def blocking_close_callback(status):
        if status.lifecycle is SessionLifecycle.CANCELLED:
            callback_entered.set()
            assert allow_callback_to_finish.wait(2)

    session = VoiceSession(
        VoiceMode.DICTATE,
        status_callback=blocking_close_callback,
        clock=lambda: 100.0,
    )
    manager = SessionManager()
    start(manager, monkeypatch, session)
    assert session.accept_transcript("private transcript", revision=1, final=True).ok
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("shutdown", manager.shutdown())
    )
    worker.start()
    assert callback_entered.wait(1)

    concurrent = manager.shutdown()
    assert concurrent.failure_code is FailureCode.BUSY
    assert concurrent.retryable
    assert not session.closed
    assert session.transcript == "private transcript"

    allow_callback_to_finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result["shutdown"].ok
    assert session.closed
    assert session.transcript == ""
    assert manager.shutdown() is result["shutdown"]


def test_release_erases_rich_session_state_when_clock_fails_during_close(monkeypatch):
    class FailingClock:
        failed = False

        def __call__(self):
            if self.failed:
                raise RuntimeError("private clock detail")
            return 100.0

    clock = FailingClock()
    session = VoiceSession(VoiceMode.DICTATE, clock=clock)
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    assert session.accept_transcript("private transcript", revision=1, final=True).ok
    clock.failed = True

    released = manager.release(started.owner_token)

    assert released.ok
    assert session.closed
    assert session.transcript == ""
    assert session.context_snapshot is None
    assert session.approval_request is None
    assert not manager.status.active


def test_direct_close_fallback_notifies_manager_and_clears_leases(monkeypatch):
    class FailingClock:
        failed = False

        def __call__(self):
            if self.failed:
                raise RuntimeError("private clock detail")
            return 100.0

    clock = FailingClock()
    session = VoiceSession(VoiceMode.DICTATE, clock=clock)
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    assert manager.acquire_microphone(started.owner_token).ok
    clock.failed = True

    session.close()

    assert session.closed
    assert session.status.lifecycle is SessionLifecycle.CANCELLED
    assert manager.status.session_status["lifecycle"] == "cancelled"
    assert not manager.status.microphone_leased


def test_cancel_control_exception_terminalizes_and_releases_manager_resources(
    monkeypatch,
):
    class InterruptingClock:
        interrupted = False

        def __call__(self):
            if self.interrupted:
                raise KeyboardInterrupt
            return 100.0

    clock = InterruptingClock()
    session = VoiceSession(VoiceMode.DICTATE, clock=clock)
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    assert manager.acquire_microphone(started.owner_token).ok
    assert manager.acquire_speaker(started.owner_token).ok
    clock.interrupted = True

    with pytest.raises(KeyboardInterrupt):
        manager.cancel(started.owner_token)

    assert session.status.lifecycle is SessionLifecycle.CANCELLED
    assert not manager.status.microphone_leased
    assert not manager.status.speaker_leased


def test_cancel_subclass_control_exception_still_releases_manager_resources(
    monkeypatch,
):
    class InterruptedCancel(VoiceSession):
        def cancel(self):
            raise KeyboardInterrupt

    session = InterruptedCancel(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    assert manager.acquire_microphone(started.owner_token).ok
    assert manager.acquire_speaker(started.owner_token).ok

    with pytest.raises(KeyboardInterrupt):
        manager.cancel(started.owner_token)

    assert not manager.status.microphone_leased
    assert not manager.status.speaker_leased


def test_cancel_subclass_transition_then_exception_publishes_resource_release(
    monkeypatch,
):
    class InterruptedCancel(VoiceSession):
        def cancel(self):
            self.set_activity(SessionActivity.RECORDING)
            raise KeyboardInterrupt

    observed = []
    session = InterruptedCancel(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager(status_callback=observed.append)
    started, _session = start(manager, monkeypatch, session)
    assert manager.acquire_microphone(started.owner_token).ok

    with pytest.raises(KeyboardInterrupt):
        manager.cancel(started.owner_token)

    assert not manager.status.microphone_leased
    assert observed[-1].to_status_dict() == manager.status.to_status_dict()


def test_start_exception_after_running_publishes_inactive_and_closes(monkeypatch):
    class StartThenFail(VoiceSession):
        def start(self):
            super().start()
            raise RuntimeError("private start failure")

    observed = []
    session = StartThenFail(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager(status_callback=observed.append)
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)

    started = manager.start(session)

    assert started.result.failure_code is FailureCode.INTERNAL
    assert session.closed
    assert not manager.status.active
    assert observed[-1].to_status_dict() == manager.status.to_status_dict()


def test_callback_registration_exception_after_running_closes_and_publishes(
    monkeypatch,
):
    class RegisterStartThenFail(VoiceSession):
        def add_status_callback(self, callback):
            super().add_status_callback(callback)
            VoiceSession.start(self)
            raise RuntimeError("private registration failure")

    observed = []
    session = RegisterStartThenFail(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager(status_callback=observed.append)
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)

    started = manager.start(session)

    assert started.result.failure_code is FailureCode.INTERNAL
    assert session.closed
    assert not manager.status.active
    assert observed[-1].to_status_dict() == manager.status.to_status_dict()


def test_manager_bypasses_faulty_close_override_before_reporting_success(
    monkeypatch,
):
    class RefuseClose(VoiceSession):
        def close(self):
            raise RuntimeError("private close failure")

    session = RefuseClose(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    assert session.accept_transcript("private transcript", 1, final=True).ok

    released = manager.release(started.owner_token)

    assert released.ok
    assert session.closed
    assert session.transcript == ""
    assert not manager.status.active


def test_release_preserves_close_control_exception_after_guaranteed_cleanup(
    monkeypatch,
):
    class InterruptAfterClose(VoiceSession):
        def close(self):
            super().close()
            raise KeyboardInterrupt

    session = InterruptAfterClose(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    assert session.accept_transcript("private transcript", 1, final=True).ok

    with pytest.raises(KeyboardInterrupt):
        manager.release(started.owner_token)

    assert session.closed
    assert session.transcript == ""
    assert not manager.status.active
    assert manager.release(started.owner_token).ok


def test_blocking_cancel_callback_does_not_hold_manager_lock(monkeypatch):
    callback_entered = threading.Event()
    allow_callback_to_finish = threading.Event()

    def blocking_cancel_callback(status):
        if status.lifecycle is SessionLifecycle.CANCELLED:
            callback_entered.set()
            assert allow_callback_to_finish.wait(2)

    session = VoiceSession(
        VoiceMode.DICTATE,
        status_callback=blocking_cancel_callback,
        clock=lambda: 100.0,
    )
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault(
            "cancelled", manager.cancel(started.owner_token)
        )
    )
    worker.start()
    assert callback_entered.wait(1)

    reader = threading.Thread(target=lambda: manager.status.to_status_dict())
    reader.start()
    reader.join(timeout=1)
    assert not reader.is_alive()

    allow_callback_to_finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result["cancelled"].outcome is Outcome.CANCELLED


def test_failed_start_preserves_cleanup_control_exception(monkeypatch):
    class FailStartInterruptClose(VoiceSession):
        def start(self):
            return Result(Outcome.FAILED, FailureCode.INTERNAL)

        def close(self):
            super().close()
            raise KeyboardInterrupt

    session = FailStartInterruptClose(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager()
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)

    with pytest.raises(KeyboardInterrupt):
        manager.start(session)

    assert session.closed
    assert not manager.status.active


def test_cancel_ordinary_subclass_failure_is_redacted_and_terminal(monkeypatch):
    class FailCancel(VoiceSession):
        def cancel(self):
            raise RuntimeError("/private/cancel/provider-state")

    session = FailCancel(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    assert manager.acquire_microphone(started.owner_token).ok

    result = manager.cancel(started.owner_token)

    assert result.failure_code is FailureCode.INTERNAL
    assert session.status.lifecycle is SessionLifecycle.CANCELLED
    assert not manager.status.microphone_leased


def test_invalid_start_result_is_redacted_closed_and_inactive(monkeypatch):
    class InvalidStart(VoiceSession):
        def start(self):
            return object()

    session = InvalidStart(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager()
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)

    started = manager.start(session)

    assert started.result.failure_code is FailureCode.INTERNAL
    assert session.closed
    assert not manager.status.active


def test_invalid_cancel_result_forces_base_terminal_cleanup(monkeypatch):
    class InvalidCancel(VoiceSession):
        def cancel(self):
            return object()

    session = InvalidCancel(VoiceMode.DICTATE, clock=lambda: 100.0)
    manager = SessionManager()
    started, _session = start(manager, monkeypatch, session)
    assert manager.acquire_microphone(started.owner_token).ok

    result = manager.cancel(started.owner_token)

    assert result.failure_code is FailureCode.INTERNAL
    assert session.status.lifecycle is SessionLifecycle.CANCELLED
    assert not manager.status.microphone_leased


def test_failed_reentrant_start_publishes_final_inactive_status(monkeypatch):
    manager_events = []
    session = None

    def cancel_during_start(status):
        if status.sequence == 1:
            assert session is not None
            session.cancel()

    session = VoiceSession(
        VoiceMode.DICTATE,
        status_callback=cancel_during_start,
        clock=lambda: 100.0,
    )
    manager = SessionManager(status_callback=manager_events.append)
    monkeypatch.setattr("src.voice.manager.secrets.token_urlsafe", fixed_token)
    started = manager.start(session)

    assert not started.ok
    assert started.owner_token is None
    assert not manager.status.active
    assert manager_events[-1].to_status_dict() == manager.status.to_status_dict()
