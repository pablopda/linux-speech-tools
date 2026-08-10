"""Deterministic contract tests for backend-neutral VoiceSession policy."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from src.voice.context import ContextBroker
from src.voice.models import (
    ActionProposal,
    ApprovalMethod,
    Capability,
    ContextItem,
    ContextKind,
    ContextRequest,
    FailureCode,
    Outcome,
    Result,
    Sensitivity,
    SessionActivity,
    SessionLifecycle,
    TargetBinding,
    VoiceMode,
)
from src.voice.permissions import PermissionBroker, PermissionDenied
from src.voice.session import VoiceSession, VoiceSessionError


NOW = 1_000.0


class ManualClock:
    def __init__(self, value=NOW):
        self.value = float(value)

    def __call__(self):
        return self.value


class Provider:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    def capture(self, requested_kinds, *, target, now, deadline):
        self.calls.append((requested_kinds, target, now, deadline))
        if self.error is not None:
            raise self.error
        return [
            ContextItem(
                kind=ContextKind.REPOSITORY,
                provider="repository",
                sensitivity=Sensitivity.PRIVATE,
                captured_at=now,
                expires_at=min(deadline, now + 30),
                content="private repository context",
                shareable=True,
                required_capability=Capability.READ_CONTEXT,
            )
        ]


class BlockingProvider(Provider):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def capture(self, requested_kinds, *, target, now, deadline):
        self.entered.set()
        assert self.release.wait(2), "test did not release blocking provider"
        return super().capture(
            requested_kinds, target=target, now=now, deadline=deadline
        )


def context_request(*, request_id="context-1"):
    return ContextRequest(
        request_id=request_id,
        kinds=frozenset((ContextKind.REPOSITORY,)),
        required_kinds=frozenset((ContextKind.REPOSITORY,)),
        shareable_kinds=frozenset((ContextKind.REPOSITORY,)),
        provider_names=frozenset(("repository",)),
        permissions=frozenset((Capability.READ_CONTEXT,)),
        created_at=NOW - 1,
        deadline=NOW + 20,
        max_total_bytes=16 * 1024,
    )


def target():
    return TargetBinding(
        kind="ide",
        source="gnome-focus",
        stable_id="private-window-id",
        captured_at=NOW,
        generation=7,
    )


def context_broker(provider=None, *, clock=lambda: NOW):
    actual = provider or Provider()
    broker = ContextBroker(clock=clock)
    broker.register("repository", actual, kinds=(ContextKind.REPOSITORY,))
    return broker, actual


def prepared_act_session(clock=None, permission_broker=None):
    session_clock = clock or ManualClock()
    contexts, _ = context_broker()
    permissions = permission_broker or PermissionBroker(
        {"test-adapter": tuple(Capability)}, clock=session_clock
    )
    session = VoiceSession(
        VoiceMode.ACT,
        context_broker=contexts,
        permission_broker=permissions,
        clock=session_clock,
        session_id="voice-session-1",
    )
    snapshot = session.capture_context(context_request(), target=target())
    assert session.accept_transcript("do the bounded task", 1, final=True).ok
    assert session.dispatch().ok
    proposal = ActionProposal.create(
        proposal_id="proposal-1",
        voice_session_id=session.session_id,
        adapter="test-adapter",
        agent_session_id="agent-session-1",
        capability=Capability.EXECUTE,
        destination="terminal-project",
        operation={"argv": ["true"]},
        context_fingerprint=snapshot.fingerprint,
        summary="Run a harmless command",
        created_at=session_clock(),
        expires_at=session_clock() + 30,
    )
    return session, permissions, proposal


def successful_execution(_proposal):
    return Result(Outcome.COMPLETED)


@pytest.mark.parametrize("mode", tuple(VoiceMode))
def test_all_modes_have_legal_default_start_and_terminal_success(mode):
    events = []
    session = VoiceSession(mode, status_callback=events.append, clock=lambda: NOW)

    assert session.start().ok
    assert session.status.lifecycle is SessionLifecycle.RUNNING
    assert session.status.activity is (
        SessionActivity.SYNTHESIZING
        if mode is VoiceMode.READ
        else SessionActivity.LISTENING
    )
    assert session.complete().ok
    assert session.status.lifecycle is SessionLifecycle.COMPLETED
    assert session.status.activity is SessionActivity.NONE
    assert [event.sequence for event in events] == [1, 2]


def test_lifecycle_activity_rules_return_closed_failure_codes():
    session = VoiceSession(VoiceMode.DICTATE, clock=lambda: NOW)

    assert session.pause().failure_code is FailureCode.INVALID_TRANSITION
    assert (
        session.start(SessionActivity.EXECUTING).failure_code
        is FailureCode.INVALID_REQUEST
    )
    assert session.start(SessionActivity.RECORDING).ok
    assert (
        session.start(SessionActivity.EXECUTING).failure_code
        is FailureCode.INVALID_REQUEST
    )
    assert session.pause().ok
    assert session.status.lifecycle is SessionLifecycle.PAUSED
    assert session.resume(SessionActivity.RECORDING).ok
    assert session.stop().ok
    assert session.complete().ok
    assert (
        session.set_activity(SessionActivity.LISTENING).failure_code
        is FailureCode.INVALID_TRANSITION
    )


def test_transcript_is_bounded_monotonic_idempotent_and_absent_from_status():
    secret = "transcript secret sk-do-not-serialize"
    statuses = []
    session = VoiceSession(
        VoiceMode.ASK, status_callback=statuses.append, clock=lambda: NOW
    )

    first = session.accept_transcript(secret, 1, final=False)
    duplicate = session.accept_transcript(secret, 1, final=False)
    conflict = session.accept_transcript("different", 1, final=False)
    stale = session.accept_transcript("older", 0, final=False)
    final = session.accept_transcript(secret + " final", 2, final=True)
    repeated_final = session.accept_transcript(secret + " final", 2, final=True)
    late = session.accept_transcript("late", 3, final=True)

    assert first.ok and duplicate.ok and final.ok and repeated_final.ok
    assert conflict.failure_code is FailureCode.STALE_REVISION
    assert stale.failure_code is FailureCode.STALE_REVISION
    assert late.failure_code is FailureCode.INVALID_TRANSITION
    assert session.transcript_revision == 2
    assert session.transcript_final
    assert session.transcript == secret + " final"
    assert all(secret not in repr(status.to_status_dict()) for status in statuses)
    assert (
        session.accept_transcript("x" * (1024 * 1024 + 1), 3).failure_code
        is FailureCode.INVALID_REQUEST
    )


def test_concurrent_revisions_and_status_callbacks_are_serialized_monotonically():
    statuses = []
    session = VoiceSession(
        VoiceMode.DICTATE, status_callback=statuses.append, clock=lambda: NOW
    )
    barrier = threading.Barrier(17)

    def update(revision):
        barrier.wait()
        session.accept_transcript("revision-{}".format(revision), revision)

    workers = [threading.Thread(target=update, args=(value,)) for value in range(1, 17)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()

    assert session.transcript_revision == 16
    sequences = [status.sequence for status in statuses]
    revisions = [status.revision for status in statuses]
    assert sequences == sorted(set(sequences))
    assert revisions == sorted(revisions)


def test_status_callback_failure_is_best_effort_and_does_not_change_state():
    calls = []

    def broken(status):
        calls.append(status.sequence)
        raise RuntimeError("observer secret")

    session = VoiceSession(VoiceMode.READ, status_callback=broken, clock=lambda: NOW)

    assert session.start().ok
    assert session.set_activity(SessionActivity.SPEAKING).ok
    assert calls == [1, 2]
    assert session.status.activity is SessionActivity.SPEAKING


def test_status_callback_base_exception_does_not_poison_publication_queue():
    observed = []

    def broken(_status):
        raise KeyboardInterrupt()

    session = VoiceSession(VoiceMode.READ, status_callback=broken, clock=lambda: NOW)
    session.add_status_callback(observed.append)

    assert session.start().ok
    assert session.set_activity(SessionActivity.SPEAKING).ok
    assert [status.sequence for status in observed] == [1, 2]


def test_status_callback_may_read_session_state_reentrantly_without_deadlock():
    observed = []
    holder = {}

    def observer(status):
        observed.append((status.sequence, holder["session"].status.sequence))

    session = VoiceSession(
        VoiceMode.DICTATE, status_callback=observer, clock=lambda: NOW
    )
    holder["session"] = session

    worker = threading.Thread(target=session.start)
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert observed == [(1, 1)]


def test_status_callback_may_transition_reentrantly_with_monotonic_delivery():
    observed = []
    holder = {}

    def observer(status):
        observed.append(status.sequence)
        if status.sequence == 1:
            assert holder["session"].set_activity(SessionActivity.RECORDING).ok

    session = VoiceSession(
        VoiceMode.DICTATE, status_callback=observer, clock=lambda: NOW
    )
    holder["session"] = session

    initial = session.start()

    assert initial.failure_code is FailureCode.INVALID_TRANSITION
    assert session.status.activity is SessionActivity.RECORDING
    assert observed == [1, 2]


def test_status_subscriptions_are_identity_unique_ordered_and_removable():
    first = []
    second = []
    first_callback = first.append
    second_callback = second.append
    session = VoiceSession(
        VoiceMode.READ, status_callback=first_callback, clock=lambda: NOW
    )

    session.add_status_callback(first_callback)
    session.add_status_callback(second_callback)
    assert session.start().ok
    assert [status.sequence for status in first] == [1]
    assert [status.sequence for status in second] == [1]

    assert session.remove_status_callback(second_callback)
    assert not session.remove_status_callback(second_callback)
    assert session.set_activity(SessionActivity.SPEAKING).ok
    assert [status.sequence for status in first] == [1, 2]
    assert [status.sequence for status in second] == [1]


def test_blocking_status_callback_does_not_hold_session_lock_and_stays_ordered():
    entered = threading.Event()
    release = threading.Event()
    sequences = []

    def observer(status):
        sequences.append(status.sequence)
        if status.sequence == 1:
            entered.set()
            assert release.wait(2), "test did not release status callback"

    session = VoiceSession(
        VoiceMode.DICTATE, status_callback=observer, clock=lambda: NOW
    )
    starter = threading.Thread(target=session.start)
    starter.start()
    assert entered.wait(1)

    # If the callback held the state lock, both of these calls would block.
    assert session.status.sequence == 1
    cancelled = session.cancel()
    assert cancelled.outcome is Outcome.CANCELLED
    release.set()
    starter.join(timeout=2)

    assert not starter.is_alive()
    assert sequences == [1, 2]


def test_nested_rlock_publication_fully_releases_state_lock_for_cancellation():
    entered = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    outcome = {}

    def observer(status):
        if status.sequence == 1:
            entered.set()
            assert release.wait(2), "test did not release nested callback"

    session = VoiceSession(
        VoiceMode.DICTATE, status_callback=observer, clock=lambda: NOW
    )

    def nested_start():
        with session._lock:
            outcome["start"] = session.start()

    starter = threading.Thread(target=nested_start)
    starter.start()
    assert entered.wait(1)

    def cancel():
        outcome["cancel"] = session.cancel()
        cancelled.set()

    canceller = threading.Thread(target=cancel)
    canceller.start()
    assert cancelled.wait(1), "callback retained a recursive state-lock level"
    release.set()
    starter.join(timeout=2)
    canceller.join(timeout=2)

    assert not starter.is_alive() and not canceller.is_alive()
    assert outcome["cancel"].outcome is Outcome.CANCELLED
    assert outcome["start"].outcome is Outcome.CANCELLED


@pytest.mark.parametrize("terminal", ("cancel", "close"))
def test_blocking_context_provider_cannot_delay_terminal_control_or_commit_late(
    terminal,
):
    provider = BlockingProvider()
    broker, _ = context_broker(provider)
    session = VoiceSession(VoiceMode.ASK, context_broker=broker, clock=lambda: NOW)
    outcome = {}

    def capture():
        try:
            outcome["snapshot"] = session.capture_context(
                context_request(), target=target()
            )
        except VoiceSessionError as error:
            outcome["failure"] = error.failure_code

    worker = threading.Thread(target=capture)
    worker.start()
    assert provider.entered.wait(1)

    if terminal == "cancel":
        assert session.cancel().outcome is Outcome.CANCELLED
    else:
        session.close()
        assert session.closed
    assert worker.is_alive()

    provider.release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert "snapshot" not in outcome
    assert outcome["failure"] is FailureCode.CANCELLED
    assert session.context_snapshot is None


def test_reentrant_close_prevents_provider_call_and_any_later_callback():
    events = []
    holder = {}
    broker, provider = context_broker()

    def close_on_capture(status):
        events.append(status.sequence)
        if status.activity is SessionActivity.CAPTURING_CONTEXT:
            holder["session"].close()

    session = VoiceSession(
        VoiceMode.ASK,
        context_broker=broker,
        status_callback=close_on_capture,
        clock=lambda: NOW,
    )
    holder["session"] = session

    with pytest.raises(VoiceSessionError) as raised:
        session.capture_context(context_request(), target=target())

    assert raised.value.failure_code is FailureCode.CLOSED
    assert session.closed
    assert provider.calls == []
    callbacks_at_close = len(events)
    assert session.start().failure_code is FailureCode.CLOSED
    assert len(events) == callbacks_at_close


def test_context_capture_uses_exact_request_and_target_then_retains_snapshot_volatilely():
    broker, provider = context_broker()
    binding = target()
    request = context_request()
    session = VoiceSession(VoiceMode.ASK, context_broker=broker, clock=lambda: NOW)

    snapshot = session.capture_context(request, target=binding)

    assert session.context_snapshot is snapshot
    assert provider.calls == [
        (
            frozenset((ContextKind.REPOSITORY.value,)),
            binding,
            NOW,
            NOW + 20,
        )
    ]
    assert session.status.activity is SessionActivity.NONE
    assert "private repository context" not in repr(session.status.to_status_dict())


def test_required_provider_failure_is_redacted_and_terminal():
    provider = Provider(error=RuntimeError("/private/path token=secret"))
    broker, _ = context_broker(provider)
    session = VoiceSession(VoiceMode.ASK, context_broker=broker, clock=lambda: NOW)

    with pytest.raises(VoiceSessionError) as raised:
        session.capture_context(context_request(), target=target())

    assert raised.value.failure_code is FailureCode.PROVIDER_FAILURE
    assert "private/path" not in str(raised.value)
    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert session.status.failure_code is FailureCode.PROVIDER_FAILURE


@pytest.mark.parametrize("control_error", (KeyboardInterrupt(), SystemExit(3)))
def test_provider_base_exception_fails_cleanly_then_reraises(control_error):
    provider = Provider(error=control_error)
    broker, _ = context_broker(provider)
    session = VoiceSession(VoiceMode.ASK, context_broker=broker, clock=lambda: NOW)

    with pytest.raises(type(control_error)):
        session.capture_context(context_request(), target=target())

    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert session.status.failure_code is FailureCode.PROVIDER_FAILURE
    assert session.context_snapshot is None
    assert session.start().failure_code is FailureCode.INVALID_TRANSITION


def test_missing_or_expired_context_uses_stable_failure_codes():
    missing = VoiceSession(VoiceMode.ASK, clock=lambda: NOW)
    with pytest.raises(VoiceSessionError) as unavailable:
        missing.capture_context(context_request(), target=target())
    assert unavailable.value.failure_code is FailureCode.UNAVAILABLE

    clock = ManualClock(NOW + 21)
    broker, _ = context_broker(clock=clock)
    expired = VoiceSession(VoiceMode.ASK, context_broker=broker, clock=clock)
    with pytest.raises(VoiceSessionError) as timed_out:
        expired.capture_context(context_request(), target=target())
    assert timed_out.value.failure_code is FailureCode.CONTEXT_EXPIRED


def test_act_requires_visible_approval_and_executes_exact_bound_proposal_once():
    session, broker, proposal = prepared_act_session()
    executed = []

    request = session.propose_action(proposal)
    assert session.status.lifecycle is SessionLifecycle.AWAITING_APPROVAL
    assert request.expires_at == session.context_snapshot.expires_at
    assert request.expires_at < proposal.expires_at
    assert (
        session.approve(request.request_id).failure_code is FailureCode.APPROVAL_DENIED
    )
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok

    def executor(bound_proposal):
        executed.append(bound_proposal)
        return Result(Outcome.COMPLETED)

    result = session.execute_approved(request.request_id, executor)

    assert result.ok
    assert len(executed) == 1
    assert executed[0].operation_digest == proposal.operation_digest
    assert executed[0].operation == proposal.operation
    assert executed[0].expires_at == request.expires_at
    assert session.status.lifecycle is SessionLifecycle.COMPLETED
    retry = session.execute_approved(request.request_id, executor)
    assert retry.failure_code is FailureCode.APPROVAL_REPLAYED
    assert len(executed) == 1
    assert not hasattr(session, "consume_approval")
    assert not hasattr(session, "action_authorized")
    exact = [
        event
        for event in broker.audit_events()
        if event.event == "grant-consumed" and event.decision == "authorized"
    ]
    assert len(exact) == 1


def test_context_freshness_is_rechecked_after_live_guard_before_consumption():
    clock = ManualClock()
    session, broker, proposal = prepared_act_session(clock)
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok
    executed = []

    def expires_context(_proposal):
        clock.value = request.expires_at
        return True

    result = session.execute_approved(
        request.request_id,
        lambda value: executed.append(value) or Result(Outcome.COMPLETED),
        live_guard=expires_context,
    )

    assert result.failure_code is FailureCode.CONTEXT_EXPIRED
    assert executed == []
    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert not any(
        event.event == "grant-consumed" and event.decision == "authorized"
        for event in broker.audit_events()
    )


@pytest.mark.parametrize("guard_behavior", ("false", "exception"))
def test_live_guard_failure_burns_authority_without_invoking_executor(guard_behavior):
    session, broker, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok
    executed = []

    def guard(_proposal):
        if guard_behavior == "exception":
            raise RuntimeError("private target detail")
        return False

    result = session.execute_approved(
        request.request_id,
        lambda value: executed.append(value) or Result(Outcome.COMPLETED),
        live_guard=guard,
    )

    assert result.failure_code is FailureCode.FOCUS_DRIFT
    assert executed == []
    assert (
        session.execute_approved(request.request_id, successful_execution).failure_code
        is FailureCode.INVALID_TRANSITION
    )
    assert not any(
        event.event == "grant-consumed" and event.decision == "authorized"
        for event in broker.audit_events()
    )


@pytest.mark.parametrize("control_error", (KeyboardInterrupt(), SystemExit(5)))
def test_live_guard_base_exception_burns_authority_before_reraising(control_error):
    session, broker, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok
    executed = []

    def guard(_proposal):
        raise control_error

    with pytest.raises(type(control_error)):
        session.execute_approved(
            request.request_id,
            lambda value: executed.append(value) or Result(Outcome.COMPLETED),
            live_guard=guard,
        )

    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert session.status.failure_code is FailureCode.FOCUS_DRIFT
    assert (
        session.execute_approved(request.request_id, successful_execution).failure_code
        is FailureCode.INVALID_TRANSITION
    )
    assert executed == []
    assert not any(
        event.event == "grant-consumed" and event.decision == "authorized"
        for event in broker.audit_events()
    )


def test_forged_request_is_rejected_and_real_request_can_execute_only_once():
    session, _, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok
    executed = []

    def executor(value):
        executed.append(value)
        return Result(Outcome.COMPLETED)

    class Forged(str):
        def __eq__(self, other):
            return True

    forged = session.execute_approved(Forged("forged-request"), executor)
    real = session.execute_approved(request.request_id, executor)
    retried = session.execute_approved(request.request_id, executor)

    assert forged.failure_code is FailureCode.APPROVAL_MISMATCH
    assert real.ok
    assert retried.failure_code is FailureCode.APPROVAL_REPLAYED
    assert len(executed) == 1


def test_executor_exception_is_ambiguous_and_never_retried():
    session, _, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok
    calls = []

    def executor(bound):
        calls.append(bound)
        raise RuntimeError("unknown external completion")

    result = session.execute_approved(request.request_id, executor)

    assert result.outcome is Outcome.AMBIGUOUS
    assert result.failure_code is FailureCode.AMBIGUOUS_DISPATCH
    assert (
        session.execute_approved(request.request_id, executor).failure_code
        is FailureCode.AMBIGUOUS_DISPATCH
    )
    assert len(calls) == 1


@pytest.mark.parametrize("control_error", (KeyboardInterrupt(), SystemExit(4)))
def test_executor_base_exception_marks_ambiguous_before_reraising(control_error):
    session, _, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok
    calls = []

    def executor(bound):
        calls.append(bound)
        raise control_error

    with pytest.raises(type(control_error)):
        session.execute_approved(request.request_id, executor)

    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert session.status.failure_code is FailureCode.AMBIGUOUS_DISPATCH
    assert (
        session.execute_approved(request.request_id, executor).failure_code
        is FailureCode.AMBIGUOUS_DISPATCH
    )
    assert len(calls) == 1


def test_cancel_during_live_guard_invalidates_generation_before_consumption():
    session, broker, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok
    guard_entered = threading.Event()
    release_guard = threading.Event()
    executed = []
    result = {}

    def guard(_proposal):
        guard_entered.set()
        assert release_guard.wait(2)
        return True

    worker = threading.Thread(
        target=lambda: result.setdefault(
            "value",
            session.execute_approved(
                request.request_id,
                lambda value: executed.append(value) or Result(Outcome.COMPLETED),
                live_guard=guard,
            ),
        )
    )
    worker.start()
    assert guard_entered.wait(1)
    assert session.cancel().outcome is Outcome.CANCELLED
    release_guard.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert result["value"].outcome is Outcome.CANCELLED
    assert executed == []
    assert not any(
        event.event == "grant-consumed" and event.decision == "authorized"
        for event in broker.audit_events()
    )


def test_cancel_during_external_execution_is_ambiguous_and_executor_runs_once():
    session, _, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok
    entered = threading.Event()
    release = threading.Event()
    calls = []
    result = {}

    def executor(bound):
        calls.append(bound)
        entered.set()
        assert release.wait(2)
        return Result(Outcome.COMPLETED)

    worker = threading.Thread(
        target=lambda: result.setdefault(
            "value", session.execute_approved(request.request_id, executor)
        )
    )
    worker.start()
    assert entered.wait(1)
    cancelled = session.cancel()
    release.set()
    worker.join(timeout=2)

    assert cancelled.outcome is Outcome.AMBIGUOUS
    assert result["value"].outcome is Outcome.AMBIGUOUS
    assert len(calls) == 1


def test_cancel_during_execution_stays_ambiguous_when_clock_fails():
    class FaultClock(ManualClock):
        fail = False

        def __call__(self):
            if self.fail:
                raise RuntimeError("private clock failure")
            return super().__call__()

    clock = FaultClock()
    session, _, proposal = prepared_act_session(clock)
    approval = session.propose_action(proposal)
    assert session.mark_proposal_visible(approval.request_id).ok
    assert session.approve(approval.request_id).ok
    entered = threading.Event()
    release = threading.Event()
    completed = {}

    def executor(_proposal):
        entered.set()
        assert release.wait(2)
        return Result(Outcome.COMPLETED)

    worker = threading.Thread(
        target=lambda: completed.setdefault(
            "result", session.execute_approved(approval.request_id, executor)
        )
    )
    worker.start()
    assert entered.wait(1)
    clock.fail = True

    cancelled = session.cancel()
    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert cancelled.outcome is Outcome.AMBIGUOUS
    assert cancelled.failure_code is FailureCode.AMBIGUOUS_DISPATCH
    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert session.status.failure_code is FailureCode.AMBIGUOUS_DISPATCH
    assert completed["result"].outcome is Outcome.AMBIGUOUS


def test_post_dispatch_clock_failure_is_terminally_ambiguous():
    class FaultClock(ManualClock):
        fail = False

        def __call__(self):
            if self.fail:
                raise RuntimeError("private post-dispatch clock")
            return super().__call__()

    clock = FaultClock()
    session, _, proposal = prepared_act_session(clock)
    approval = session.propose_action(proposal)
    assert session.mark_proposal_visible(approval.request_id).ok
    assert session.approve(approval.request_id).ok

    def executor(_proposal):
        clock.fail = True
        return Result(Outcome.COMPLETED)

    result = session.execute_approved(approval.request_id, executor)

    assert result.outcome is Outcome.AMBIGUOUS
    assert result.failure_code is FailureCode.AMBIGUOUS_DISPATCH
    assert session.status.lifecycle is SessionLifecycle.FAILED
    clock.fail = False
    assert session.cancel().outcome is Outcome.AMBIGUOUS


def test_capture_transition_clock_failure_does_not_wedge_capture_token():
    class FaultClock(ManualClock):
        fail = False

        def __call__(self):
            if self.fail:
                raise RuntimeError("private capture clock")
            return super().__call__()

    clock = FaultClock()
    contexts, _provider = context_broker()
    session = VoiceSession(
        VoiceMode.ASK,
        context_broker=contexts,
        clock=clock,
    )
    clock.fail = True

    with pytest.raises(VoiceSessionError) as raised:
        session.capture_context(context_request(), target=target())
    assert raised.value.failure_code is FailureCode.INTERNAL
    assert session._active_capture is None

    clock.fail = False
    assert session.capture_context(context_request(), target=target()).items


def test_proposal_transition_clock_failure_revokes_partial_authority():
    class FaultClock(ManualClock):
        fail = False

        def __call__(self):
            if self.fail:
                raise RuntimeError("private proposal clock")
            return super().__call__()

    class FlipAfterRequest(PermissionBroker):
        flip = True

        def create_request(self, proposal):
            request = super().create_request(proposal)
            if self.flip:
                self.flip = False
                clock.fail = True
            return request

    clock = FaultClock()
    contexts, _provider = context_broker()
    permissions = FlipAfterRequest({"test-adapter": tuple(Capability)}, clock=clock)
    session = VoiceSession(
        VoiceMode.ACT,
        context_broker=contexts,
        permission_broker=permissions,
        clock=clock,
        session_id="voice-session-1",
    )
    context = session.capture_context(context_request(), target=target())
    assert session.accept_transcript("do it", 1, final=True).ok
    assert session.dispatch().ok
    proposal = ActionProposal.create(
        proposal_id="proposal-clock",
        voice_session_id=session.session_id,
        adapter="test-adapter",
        agent_session_id="agent-session-1",
        capability=Capability.EXECUTE,
        destination="terminal-project",
        operation={"argv": ["true"]},
        context_fingerprint=context.fingerprint,
        summary="Run harmless command",
        created_at=clock(),
        expires_at=clock() + 30,
    )

    with pytest.raises(VoiceSessionError) as raised:
        session.propose_action(proposal)
    assert raised.value.failure_code is FailureCode.INTERNAL
    assert session.approval_request is None

    clock.fail = False
    assert session.propose_action(proposal).proposal_id == proposal.proposal_id


def test_transcript_with_non_utf8_surrogate_is_closed_invalid_request():
    session = VoiceSession(VoiceMode.DICTATE, clock=lambda: NOW)

    result = session.accept_transcript("secret\ud800tail", 1, final=True)

    assert result.failure_code is FailureCode.INVALID_REQUEST
    assert session.transcript == ""


def test_transcript_string_subclass_cannot_bypass_bounds_or_retain_state():
    class Sneaky(str):
        hidden = "x" * 100_000

        def encode(self, *args, **kwargs):
            return b"ok"

    session = VoiceSession(VoiceMode.DICTATE, clock=lambda: NOW)
    oversized = Sneaky("x" * (1024 * 1024 + 1))
    assert (
        session.accept_transcript(oversized, 1).failure_code
        is FailureCode.INVALID_REQUEST
    )

    assert session.accept_transcript(Sneaky("ok"), 1).ok
    assert type(session.transcript) is str
    assert session.transcript == "ok"


def test_consumed_grant_clock_failure_terminalizes_before_executor():
    class FaultClock(ManualClock):
        fail = False

        def __call__(self):
            if self.fail:
                raise RuntimeError("private pre-dispatch clock")
            return super().__call__()

    class FlipAfterConsume(PermissionBroker):
        def consume(self, grant, **bindings):
            authorized = super().consume(grant, **bindings)
            clock.fail = True
            return authorized

    clock = FaultClock()
    contexts, _provider = context_broker()
    permissions = FlipAfterConsume({"test-adapter": tuple(Capability)}, clock=clock)
    session = VoiceSession(
        VoiceMode.ACT,
        context_broker=contexts,
        permission_broker=permissions,
        clock=clock,
        session_id="voice-session-1",
    )
    context = session.capture_context(context_request(), target=target())
    assert session.accept_transcript("do it", 1, final=True).ok
    assert session.dispatch().ok
    proposal = ActionProposal.create(
        proposal_id="proposal-consume-clock",
        voice_session_id=session.session_id,
        adapter="test-adapter",
        agent_session_id="agent-session-1",
        capability=Capability.EXECUTE,
        destination="terminal-project",
        operation={"argv": ["true"]},
        context_fingerprint=context.fingerprint,
        summary="Run harmless command",
        created_at=clock(),
        expires_at=clock() + 30,
    )
    approval = session.propose_action(proposal)
    assert session.mark_proposal_visible(approval.request_id).ok
    assert session.approve(approval.request_id).ok
    executed = []

    result = session.execute_approved(
        approval.request_id,
        lambda value: executed.append(value) or Result(Outcome.COMPLETED),
    )

    assert result.failure_code is FailureCode.INTERNAL
    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert executed == []


def test_provider_control_exception_survives_concurrent_capture_cancel():
    class InterruptedProvider(Provider):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def capture(self, requested_kinds, *, target, now, deadline):
            self.entered.set()
            assert self.release.wait(2)
            raise KeyboardInterrupt

    provider = InterruptedProvider()
    broker, _ = context_broker(provider)
    session = VoiceSession(VoiceMode.ASK, context_broker=broker, clock=lambda: NOW)
    outcome = {}

    def capture():
        try:
            session.capture_context(context_request(), target=target())
        except BaseException as error:
            outcome["error"] = error

    worker = threading.Thread(target=capture)
    worker.start()
    assert provider.entered.wait(1)
    assert session.cancel().outcome is Outcome.CANCELLED
    provider.release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert isinstance(outcome["error"], KeyboardInterrupt)


def test_context_broker_invalid_snapshot_type_fails_closed():
    class InvalidSnapshotBroker(ContextBroker):
        def capture(self, request, *, target, now=None):
            class FakeSnapshot:
                def is_fresh(self, at):
                    return True

            return FakeSnapshot()

    session = VoiceSession(
        VoiceMode.ASK,
        context_broker=InvalidSnapshotBroker(clock=lambda: NOW),
        clock=lambda: NOW,
    )

    with pytest.raises(VoiceSessionError) as raised:
        session.capture_context(context_request(), target=target())

    assert raised.value.failure_code is FailureCode.PROVIDER_FAILURE
    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert session.context_snapshot is None


def test_permission_broker_invalid_request_type_fails_closed():
    class InvalidRequestBroker(PermissionBroker):
        def create_request(self, proposal):
            return object()

    clock = ManualClock()
    broker = InvalidRequestBroker({"test-adapter": tuple(Capability)}, clock=clock)
    session, _permissions, proposal = prepared_act_session(clock, broker)

    with pytest.raises(VoiceSessionError) as raised:
        session.propose_action(proposal)

    assert raised.value.failure_code is FailureCode.INTERNAL
    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert session.approval_request is None


def test_permission_broker_invalid_grant_type_fails_closed():
    class InvalidGrantBroker(PermissionBroker):
        def approve(self, request_id, *, method="visible"):
            super().approve(request_id, method=method)
            return object()

    clock = ManualClock()
    broker = InvalidGrantBroker({"test-adapter": tuple(Capability)}, clock=clock)
    session, _permissions, proposal = prepared_act_session(clock, broker)
    approval = session.propose_action(proposal)
    assert session.mark_proposal_visible(approval.request_id).ok

    result = session.approve(approval.request_id)

    assert result.failure_code is FailureCode.INTERNAL
    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert session.approval_request is None


def test_permission_broker_grant_method_must_match_visible_approval():
    class WrongMethodBroker(PermissionBroker):
        def approve(self, request_id, *, method="visible"):
            grant = super().approve(request_id, method=method)
            return replace(grant, method=ApprovalMethod.SPOKEN)

    clock = ManualClock()
    broker = WrongMethodBroker({"test-adapter": tuple(Capability)}, clock=clock)
    session, _permissions, proposal = prepared_act_session(clock, broker)
    approval = session.propose_action(proposal)
    assert session.mark_proposal_visible(approval.request_id).ok

    result = session.approve(approval.request_id, method=ApprovalMethod.VISIBLE)

    assert result.failure_code is FailureCode.INTERNAL
    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert session.approval_request is None


def test_action_authority_rejects_wrong_mode_session_context_and_spoken_high_impact():
    ask = VoiceSession(VoiceMode.ASK, clock=lambda: NOW, session_id="ask-session")
    wrong_mode = ActionProposal.create(
        proposal_id="wrong-mode",
        voice_session_id=ask.session_id,
        adapter="test-adapter",
        agent_session_id="agent",
        capability=Capability.EXECUTE,
        destination="terminal",
        operation={"argv": ["true"]},
        context_fingerprint="f" * 64,
        summary="wrong mode",
        created_at=NOW,
        expires_at=NOW + 10,
    )
    with pytest.raises(VoiceSessionError) as raised:
        ask.propose_action(wrong_mode)
    assert raised.value.failure_code is FailureCode.INVALID_REQUEST

    session, _, proposal = prepared_act_session()
    mismatched = ActionProposal.create(
        proposal_id="mismatched",
        voice_session_id="some-other-session",
        adapter=proposal.adapter,
        agent_session_id=proposal.agent_session_id,
        capability=proposal.capability,
        destination=proposal.destination,
        operation=proposal.operation,
        context_fingerprint=proposal.context_fingerprint,
        summary="mismatch",
        created_at=NOW,
        expires_at=NOW + 10,
    )
    with pytest.raises(VoiceSessionError) as mismatch:
        session.propose_action(mismatched)
    assert mismatch.value.failure_code is FailureCode.APPROVAL_MISMATCH

    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    spoken = session.approve(request.request_id, method="spoken")
    assert spoken.failure_code is FailureCode.APPROVAL_DENIED
    assert session.status.lifecycle is SessionLifecycle.FAILED


def test_ambiguity_is_terminal_and_burns_pending_action_authority():
    session, broker, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok

    ambiguous = session.mark_ambiguous()

    assert ambiguous.outcome is Outcome.AMBIGUOUS
    assert ambiguous.failure_code is FailureCode.AMBIGUOUS_DISPATCH
    assert session.status.lifecycle is SessionLifecycle.FAILED
    assert (
        session.approve(request.request_id).failure_code
        is FailureCode.AMBIGUOUS_DISPATCH
    )
    assert not any(
        event.event == "grant-consumed" and event.decision == "authorized"
        for event in broker.audit_events()
    )


def test_terminal_fallback_burns_an_outstanding_grant_before_completion():
    session, broker, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok

    result = session.target_drift(fallback_succeeded=True)

    assert result.outcome is Outcome.COMPLETED_WITH_FALLBACK
    assert session.status.lifecycle is SessionLifecycle.COMPLETED
    denied = [
        event
        for event in broker.audit_events()
        if event.event == "approval" and event.decision == "denied"
    ]
    assert denied[-1].reason == "user-denied"
    assert not any(
        event.event == "grant-consumed" and event.decision == "authorized"
        for event in broker.audit_events()
    )


@pytest.mark.parametrize("terminal", ("drift", "failure"))
def test_terminal_failure_revokes_grant_and_all_late_approval_controls(terminal):
    session, broker, proposal = prepared_act_session()
    request = session.propose_action(proposal)
    assert session.mark_proposal_visible(request.request_id).ok
    assert session.approve(request.request_id).ok
    grant = session._grant
    assert grant is not None

    if terminal == "drift":
        result = session.target_drift()
    else:
        result = session.fail(FailureCode.PROVIDER_FAILURE)

    assert not result.ok
    assert (
        session.mark_proposal_visible(request.request_id).failure_code
        is FailureCode.INVALID_TRANSITION
    )
    assert (
        session.approve(request.request_id).failure_code
        is FailureCode.INVALID_TRANSITION
    )
    assert (
        session.execute_approved(request.request_id, successful_execution).failure_code
        is FailureCode.INVALID_TRANSITION
    )
    with pytest.raises(PermissionDenied) as replay:
        broker.consume(
            grant,
            voice_session_id=session.session_id,
            adapter=proposal.adapter,
            agent_session_id=proposal.agent_session_id,
            capability=proposal.capability,
            destination=proposal.destination,
            operation=proposal.operation,
            context_fingerprint=proposal.context_fingerprint,
        )
    assert replay.value.code == "replay"


def test_target_drift_timeout_and_failure_have_stable_terminal_results():
    drift = VoiceSession(VoiceMode.DICTATE, clock=lambda: NOW)
    result = drift.target_drift()
    assert result.outcome is Outcome.REJECTED
    assert result.failure_code is FailureCode.FOCUS_DRIFT

    fallback = VoiceSession(VoiceMode.DICTATE, clock=lambda: NOW)
    result = fallback.target_drift(fallback_succeeded=True)
    assert result.outcome is Outcome.COMPLETED_WITH_FALLBACK
    assert result.fallback_used

    timed_out = VoiceSession(VoiceMode.READ, clock=lambda: NOW)
    result = timed_out.timeout()
    assert result.outcome is Outcome.FAILED
    assert result.failure_code is FailureCode.TIMEOUT
    assert result.retryable


def test_cancel_and_close_are_idempotent_clear_private_state_and_stop_callbacks():
    events = []
    broker, _ = context_broker()
    session = VoiceSession(
        VoiceMode.ASK,
        context_broker=broker,
        status_callback=events.append,
        clock=lambda: NOW,
    )
    session.capture_context(context_request(), target=target())
    session.accept_transcript("private transcript", 1, final=True)

    first = session.cancel()
    count_after_cancel = len(events)
    second = session.cancel()
    session.close()
    session.close()
    count_after_close = len(events)

    assert first == second
    assert first.outcome is Outcome.CANCELLED
    assert session.closed
    assert session.transcript == ""
    assert session.context_snapshot is None
    assert count_after_close == count_after_cancel
    assert (
        session.accept_transcript("late secret", 2).failure_code is FailureCode.CLOSED
    )
    assert session.start().failure_code is FailureCode.CLOSED
    assert len(events) == count_after_close


def test_terminal_result_remains_stable_after_rejected_late_operations():
    session = VoiceSession(VoiceMode.READ, clock=lambda: NOW)
    completed = session.complete(fallback_used=True)

    assert (
        session.set_activity(SessionActivity.SPEAKING).failure_code
        is FailureCode.INVALID_TRANSITION
    )
    assert session.cancel() == completed
    assert session.complete() == completed
