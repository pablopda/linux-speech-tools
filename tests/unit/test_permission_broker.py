"""Hermetic security tests for the volatile permission broker."""

import json
import threading
from dataclasses import asdict, replace

import pytest

from src.voice.models import ActionProposal, Capability
from src.voice.permissions import PermissionBroker, PermissionDenied


class ManualClock:
    def __init__(self, value=1_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value


def make_proposal(
    clock,
    *,
    proposal_id="proposal-1",
    capability=Capability.EXECUTE,
    operation=None,
    expires_in=60.0,
):
    if operation is None:
        operation = {
            "argv": ["pytest", "tests/unit"],
            "cwd": "/private/home/alice/secret-project",
        }
    return ActionProposal.create(
        proposal_id=proposal_id,
        voice_session_id="voice-session-private",
        adapter="test-adapter",
        agent_session_id="external-agent-session-private",
        capability=capability,
        destination="terminal:/private/project",
        operation=operation,
        context_fingerprint="f" * 64,
        summary="Run the private command in the private project",
        created_at=clock(),
        expires_at=clock() + expires_in,
    )


def make_broker(clock, *, audit_capacity=256):
    return PermissionBroker(
        {"test-adapter": tuple(Capability)},
        clock=clock,
        audit_capacity=audit_capacity,
    )


def approve_visible(broker, proposal):
    request = broker.create_request(proposal)
    shown = broker.mark_visible(request.request_id)
    assert shown == request
    return request, broker.approve(request.request_id, method="visible")


def consume_exact(broker, grant, proposal, **overrides):
    values = {
        "voice_session_id": proposal.voice_session_id,
        "adapter": proposal.adapter,
        "agent_session_id": proposal.agent_session_id,
        "capability": proposal.capability,
        "destination": proposal.destination,
        "operation": proposal.operation,
        "context_fingerprint": proposal.context_fingerprint,
    }
    values.update(overrides)
    return broker.consume(grant, **values)


def assert_denied(code, callback):
    with pytest.raises(PermissionDenied) as raised:
        callback()
    assert raised.value.code == code
    # Exceptions must expose only a closed reason, never request data.
    assert str(raised.value) == "permission denied: " + code


def test_closed_capabilities_and_adapter_declarations_are_enforced():
    clock = ManualClock()
    broker = PermissionBroker(clock=clock)
    proposal = make_proposal(clock)

    assert_denied("undeclared-capability", lambda: broker.create_request(proposal))
    assert_denied(
        "unknown-capability", lambda: broker.declare_adapter("bad", ["shell-root"])
    )
    assert_denied(
        "empty-capability-declaration", lambda: broker.declare_adapter("empty", [])
    )

    declared = broker.declare_adapter("test-adapter", [Capability.EXECUTE])
    assert declared == frozenset((Capability.EXECUTE,))
    broker.create_request(proposal)

    send = make_proposal(clock, proposal_id="send", capability=Capability.SEND)
    assert_denied("undeclared-capability", lambda: broker.create_request(send))


def test_request_must_be_presented_before_any_approval():
    clock = ManualClock()
    broker = make_broker(clock)
    request = broker.create_request(make_proposal(clock))

    assert_denied(
        "proposal-not-visible",
        lambda: broker.approve(request.request_id, method="visible"),
    )
    broker.present(request.request_id)
    grant = broker.approve(request.request_id, method="visible")
    assert request.operation == make_proposal(clock).operation
    assert grant.request_id == request.request_id
    assert grant.proposal_id == request.proposal_id
    assert grant.operation_digest == request.operation_digest
    assert grant.destination == request.destination
    assert grant.context_fingerprint == request.context_fingerprint
    assert grant.grant_id


def test_visible_request_contains_exact_operation_not_only_a_summary():
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(
        clock,
        operation={"argv": ["sh", "-c", "destructive-command"]},
    )
    misleading = replace(proposal, summary="List files (read only)")

    request = broker.create_request(misleading)
    shown = broker.mark_visible(request.request_id)

    assert shown.operation == misleading.operation
    assert shown.operation_digest == misleading.operation_digest
    assert shown.summary == "List files (read only)"


def test_future_proposals_and_clock_rollback_fail_closed():
    clock = ManualClock()
    broker = make_broker(clock)
    future = replace(
        make_proposal(clock),
        created_at=clock.value + 10,
        expires_at=clock.value + 20,
    )
    assert_denied("not-yet-valid", lambda: broker.create_request(future))

    request = broker.create_request(
        make_proposal(clock, proposal_id="rollback-request")
    )
    clock.value -= 1
    assert_denied("not-yet-valid", lambda: broker.mark_visible(request.request_id))
    assert_denied("unknown-request", lambda: broker.mark_visible(request.request_id))

    clock.value += 1
    proposal = make_proposal(clock, proposal_id="rollback-grant")
    _, grant = approve_visible(broker, proposal)
    clock.value -= 1
    assert_denied("not-yet-valid", lambda: consume_exact(broker, grant, proposal))
    assert_denied("replay", lambda: consume_exact(broker, grant, proposal))


def test_capability_iterable_failure_is_redacted():
    clock = ManualClock()
    broker = PermissionBroker(clock=clock)

    def broken_capabilities():
        yield Capability.EXECUTE
        raise RuntimeError("/private/adapter/capability-state")

    assert_denied(
        "invalid-capability-declaration",
        lambda: broker.declare_adapter("broken-adapter", broken_capabilities()),
    )


@pytest.mark.parametrize(
    "capability",
    [Capability.EXECUTE, Capability.SEND, Capability.MODIFY, Capability.DELETE],
)
def test_spoken_only_approval_is_terminally_denied_for_consequential_actions(
    capability,
):
    clock = ManualClock()
    broker = make_broker(clock)
    request = broker.create_request(
        make_proposal(
            clock, proposal_id="spoken-" + capability.value, capability=capability
        )
    )
    broker.mark_visible(request.request_id)

    assert_denied(
        "spoken-approval-forbidden",
        lambda: broker.approve(request.request_id, method="spoken"),
    )
    assert_denied(
        "unknown-request", lambda: broker.approve(request.request_id, method="visible")
    )


@pytest.mark.parametrize(
    "capability", [Capability.READ_CONTEXT, Capability.DRAFT, Capability.INSERT]
)
def test_spoken_approval_is_bounded_to_low_risk_capabilities(capability):
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(
        clock, proposal_id="low-" + capability.value, capability=capability
    )
    request = broker.create_request(proposal)
    broker.mark_visible(request.request_id)
    grant = broker.approve(request.request_id, method="spoken")

    assert grant.method == "spoken"
    assert consume_exact(broker, grant, proposal) is True


def test_exact_grant_is_consumed_once_and_replay_fails_closed():
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(clock)
    _, grant = approve_visible(broker, proposal)

    assert consume_exact(broker, grant, proposal) is True
    assert_denied("replay", lambda: consume_exact(broker, grant, proposal))

    authorized = [
        event
        for event in broker.audit_events()
        if event.event == "grant-consumed" and event.decision == "authorized"
    ]
    assert len(authorized) == 1
    assert authorized[0].reason == "exact-once"


@pytest.mark.parametrize(
    "override",
    [
        {"voice_session_id": "different-voice-session"},
        {"adapter": "different-adapter"},
        {"agent_session_id": "different-external-session"},
        {"capability": Capability.SEND},
        {"destination": "terminal:/different-project"},
        {"operation": {"argv": ["pytest", "different-tests"]}},
        {"context_fingerprint": "0" * 64},
    ],
)
def test_every_binding_mismatch_burns_the_grant(override):
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(clock)
    _, grant = approve_visible(broker, proposal)

    assert_denied(
        "binding-mismatch", lambda: consume_exact(broker, grant, proposal, **override)
    )
    assert_denied("replay", lambda: consume_exact(broker, grant, proposal))


def test_forged_grant_with_real_nonce_burns_the_real_grant():
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(clock)
    _, grant = approve_visible(broker, proposal)
    forged = replace(grant, destination="terminal:/forged")

    assert_denied("binding-mismatch", lambda: consume_exact(broker, forged, proposal))
    assert_denied("replay", lambda: consume_exact(broker, grant, proposal))


def test_expired_grant_cannot_be_reused_after_a_failed_attempt():
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(clock, expires_in=5.0)
    _, grant = approve_visible(broker, proposal)
    clock.value += 5.0

    assert_denied("expired", lambda: consume_exact(broker, grant, proposal))
    assert_denied("replay", lambda: consume_exact(broker, grant, proposal))


def test_restart_invalidates_all_grants_but_retains_adapter_policy():
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(clock)
    _, grant = approve_visible(broker, proposal)

    broker.restart()
    assert_denied("broker-restarted", lambda: consume_exact(broker, grant, proposal))

    # Static adapter declarations survive; only volatile authority is cleared.
    replacement = make_proposal(clock, proposal_id="after-restart")
    approve_visible(broker, replacement)


def test_ambiguous_prior_dispatch_burns_authority_without_retry():
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(clock)
    _, grant = approve_visible(broker, proposal)

    assert_denied(
        "prior-dispatch-ambiguous",
        lambda: consume_exact(broker, grant, proposal, prior_dispatch_ambiguous=True),
    )
    assert_denied("replay", lambda: consume_exact(broker, grant, proposal))


def test_capability_change_invalidates_outstanding_grants():
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(clock)
    _, grant = approve_visible(broker, proposal)

    broker.declare_adapter("test-adapter", [Capability.READ_CONTEXT])
    assert_denied("replay", lambda: consume_exact(broker, grant, proposal))


def test_consume_is_atomic_under_concurrency():
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(clock)
    _, grant = approve_visible(broker, proposal)
    barrier = threading.Barrier(12)
    outcomes = []
    outcome_lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            value = consume_exact(broker, grant, proposal)
        except PermissionDenied as error:
            value = error.code
        with outcome_lock:
            outcomes.append(value)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert all(not thread.is_alive() for thread in threads)
    assert outcomes.count(True) == 1
    assert outcomes.count("replay") == 11


def test_audit_is_bounded_and_never_contains_sensitive_payloads():
    clock = ManualClock()
    broker = make_broker(clock, audit_capacity=6)
    operation = {
        "command": "SUPER_SECRET_COMMAND --token hunter2",
        "path": "/home/alice/top-secret/customer.txt",
        "title": "Acquisition targets",
        "audio": "raw microphone bytes",
        "prompt": "private prompt",
        "context": "private selected text",
    }
    proposal = make_proposal(clock, operation=operation)
    _, grant = approve_visible(broker, proposal)
    consume_exact(broker, grant, proposal)
    assert_denied("replay", lambda: consume_exact(broker, grant, proposal))

    events = broker.audit_events()
    assert len(events) == 6
    encoded = json.dumps([asdict(event) for event in events], sort_keys=True)
    for forbidden in (
        "SUPER_SECRET_COMMAND",
        "hunter2",
        "/home/alice",
        "Acquisition targets",
        "raw microphone bytes",
        "private prompt",
        "private selected text",
        proposal.destination,
        proposal.voice_session_id,
        proposal.agent_session_id,
        proposal.summary,
        grant.grant_id,
    ):
        assert forbidden not in encoded
    assert set(asdict(events[0])) == {
        "sequence",
        "occurred_at",
        "event",
        "decision",
        "reason",
        "capability",
        "request_ref",
    }


def test_structurally_equivalent_operation_uses_the_canonical_digest():
    clock = ManualClock()
    broker = make_broker(clock)
    proposal = make_proposal(
        clock,
        operation={"environment": {"B": "2", "A": "1"}, "argv": ["true"]},
    )
    _, grant = approve_visible(broker, proposal)

    reordered = {"argv": ["true"], "environment": {"A": "1", "B": "2"}}
    assert consume_exact(broker, grant, proposal, operation=reordered) is True


def test_request_expiry_and_excessive_ttl_fail_before_approval():
    clock = ManualClock()
    broker = make_broker(clock)
    expired = make_proposal(clock, proposal_id="expired", expires_in=1.0)
    clock.value += 2.0
    too_long = make_proposal(clock, proposal_id="long", expires_in=301.0)

    assert_denied("expired", lambda: broker.create_request(expired))
    assert_denied("expiry-too-distant", lambda: broker.create_request(too_long))


def test_audit_clock_failure_cannot_hide_a_committed_grant_or_leak_details():
    class AuditFaultClock(ManualClock):
        armed = False
        armed_calls = 0

        def __call__(self):
            if self.armed:
                self.armed_calls += 1
                if self.armed_calls == 2:
                    raise RuntimeError("/private/clock internals token=secret")
            return super().__call__()

    clock = AuditFaultClock()
    broker = make_broker(clock)
    proposal = make_proposal(clock)
    request = broker.create_request(proposal)
    broker.mark_visible(request.request_id)
    clock.armed = True

    grant = broker.approve(request.request_id)

    assert grant.request_id == request.request_id
    assert consume_exact(broker, grant, proposal) is True
    granted = [event for event in broker.audit_events() if event.decision == "granted"]
    assert len(granted) == 1


def test_capability_declaration_iterable_is_bounded():
    yielded = []

    def endless():
        while True:
            yielded.append(True)
            yield Capability.READ_CONTEXT

    broker = PermissionBroker(clock=ManualClock())

    assert_denied(
        "invalid-capability-declaration",
        lambda: broker.declare_adapter("endless", endless()),
    )
    assert len(yielded) == len(Capability) + 1
