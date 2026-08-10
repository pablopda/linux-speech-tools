"""Tests for the common agent adapter lifecycle."""

import time
from dataclasses import dataclass

import pytest

from src.voice.agents import AgentContractError, AgentRegistry
from src.voice.models import (
    AgentEvent,
    AgentEventKind,
    AgentRequest,
    AgentRun,
    Capability,
    ContextItem,
    ContextKind,
    ContextSnapshot,
    Sensitivity,
    TargetBinding,
    VoiceMode,
)


class FakeAdapter:
    adapter_id = "fake-agent"

    def __init__(self, events=(), capabilities=None):
        self.events = list(events)
        self._capabilities = capabilities or frozenset(
            (Capability.READ_CONTEXT, Capability.DRAFT)
        )
        self.cancelled = []
        self.sent = []

    def capabilities(self):
        return self._capabilities

    def discover_sessions(self, request):
        return ()

    def send(self, request):
        self.sent.append(request)
        return AgentRun("run-1", self.adapter_id, "running")

    def stream(self, run_id):
        yield from self.events

    def cancel(self, run_id):
        self.cancelled.append(run_id)


def request(capabilities=None, *, now=None, items=(), target=None):
    created_at = time.time() if now is None else now
    context = ContextSnapshot.create(
        target=target,
        items=tuple(items),
        permissions=frozenset((Capability.READ_CONTEXT,)),
        now=created_at,
    )
    return AgentRequest(
        request_id="request-1",
        voice_session_id="voice-1",
        mode=VoiceMode.ASK,
        prompt="private question",
        context=context,
        capabilities=capabilities or frozenset((Capability.READ_CONTEXT,)),
    )


def assert_code(code, callback):
    with pytest.raises(AgentContractError) as raised:
        callback()
    assert raised.value.code == code
    assert str(raised.value) == "agent contract failed: " + code


def test_registry_rejects_invalid_duplicate_and_incomplete_adapters():
    registry = AgentRegistry()
    adapter = FakeAdapter()
    registry.register(adapter)
    assert registry.adapter_ids() == ("fake-agent",)
    assert_code("duplicate-adapter", lambda: registry.register(adapter))

    bad = FakeAdapter()
    bad.adapter_id = "/private/path"
    assert_code("invalid-adapter-id", lambda: AgentRegistry().register(bad))

    class Incomplete:
        adapter_id = "incomplete"

    assert_code("incomplete-adapter", lambda: AgentRegistry().register(Incomplete()))


def test_registration_redacts_adapter_property_failures():
    class HostileAdapter:
        @property
        def adapter_id(self):
            raise RuntimeError("/home/alice/private adapter configuration")

    assert_code(
        "adapter-introspection-failed",
        lambda: AgentRegistry().register(HostileAdapter()),
    )


def test_collect_accepts_monotonic_events_and_exactly_one_terminal():
    events = (
        AgentEvent("run-1", 1, AgentEventKind.STARTED),
        AgentEvent("run-1", 2, AgentEventKind.OUTPUT_DELTA, text="secret answer"),
        AgentEvent("run-1", 3, AgentEventKind.FINAL),
    )
    adapter = FakeAdapter(events)
    registry = AgentRegistry()
    registry.register(adapter)

    run, returned = registry.collect("fake-agent", request())
    assert run.run_id == "run-1"
    assert returned == events
    assert adapter.cancelled == []


def test_dispatch_strips_private_target_and_non_shareable_context():
    private = ContextItem(
        kind=ContextKind.SELECTION,
        provider="selection",
        sensitivity=Sensitivity.SECRET,
        captured_at=100.0,
        expires_at=160.0,
        content="must stay local",
        shareable=False,
    )
    shared = ContextItem(
        kind=ContextKind.REPOSITORY,
        provider="repository",
        sensitivity=Sensitivity.PRIVATE,
        captured_at=100.0,
        expires_at=160.0,
        content="explicitly shared",
        shareable=True,
    )
    original = request(
        now=100.0,
        items=(private, shared),
        target=TargetBinding(
            kind="window",
            source="gnome",
            stable_id="private-window-id",
            captured_at=100.0,
            generation=7,
        ),
    )
    adapter = FakeAdapter((AgentEvent("run-1", 1, AgentEventKind.FINAL),))
    registry = AgentRegistry(clock=lambda: 110.0)
    registry.register(adapter)

    registry.collect("fake-agent", original)
    dispatched = adapter.sent[0]
    assert dispatched is not original
    assert dispatched.context is not original.context
    assert dispatched.context.target.stable_id == ""
    assert dispatched.context.target.generation is None
    assert tuple(value.content for value in dispatched.context.items) == (
        "explicitly shared",
    )
    assert dispatched.context.fingerprint != original.context.fingerprint


def test_dispatch_rejects_expired_context_before_send():
    adapter = FakeAdapter((AgentEvent("run-1", 1, AgentEventKind.FINAL),))
    registry = AgentRegistry(clock=lambda: 161.0)
    registry.register(adapter)

    assert_code(
        "context-expired",
        lambda: registry.collect("fake-agent", request(now=100.0)),
    )
    assert adapter.sent == []


def test_request_model_rejects_shareable_context_without_required_capability():
    item = ContextItem(
        kind=ContextKind.SELECTION,
        provider="selection",
        sensitivity=Sensitivity.SECRET,
        captured_at=100.0,
        expires_at=160.0,
        content="private context",
        shareable=True,
        required_capability=Capability.READ_CONTEXT,
    )
    context = ContextSnapshot.create(
        target=None,
        items=(item,),
        permissions=frozenset((Capability.READ_CONTEXT,)),
        now=100.0,
    )

    with pytest.raises(ValueError, match="undeclared request capability"):
        AgentRequest(
            request_id="request-1",
            voice_session_id="voice-1",
            mode=VoiceMode.ASK,
            prompt="draft only",
            context=context,
            capabilities=frozenset((Capability.DRAFT,)),
        )


def test_request_model_rejects_arbitrary_required_context_capability():
    item = ContextItem(
        kind=ContextKind.SELECTION,
        provider="selection",
        sensitivity=Sensitivity.SECRET,
        captured_at=100.0,
        expires_at=160.0,
        content="private context",
        shareable=True,
        required_capability=Capability.DELETE,
    )
    context = ContextSnapshot.create(
        target=None,
        items=(item,),
        permissions=frozenset((Capability.DELETE,)),
        now=100.0,
    )

    with pytest.raises(ValueError, match="undeclared request capability"):
        AgentRequest(
            request_id="request-1",
            voice_session_id="voice-1",
            mode=VoiceMode.ACT,
            prompt="delete",
            context=context,
            capabilities=frozenset((Capability.EXECUTE,)),
        )


@pytest.mark.parametrize(
    ("events", "code"),
    [
        ((AgentEvent("other", 1, AgentEventKind.FINAL),), "invalid-event"),
        (
            (
                AgentEvent("run-1", 2, AgentEventKind.STARTED),
                AgentEvent("run-1", 2, AgentEventKind.FINAL),
            ),
            "stale-event",
        ),
        (
            (
                AgentEvent("run-1", 1, AgentEventKind.FINAL),
                AgentEvent("run-1", 2, AgentEventKind.PROGRESS),
            ),
            "event-after-terminal",
        ),
        ((AgentEvent("run-1", 1, AgentEventKind.STARTED),), "missing-terminal-event"),
    ],
)
def test_invalid_event_lifecycles_fail_closed_and_cancel(events, code):
    adapter = FakeAdapter(events)
    registry = AgentRegistry()
    registry.register(adapter)

    assert_code(code, lambda: registry.collect("fake-agent", request()))
    assert adapter.cancelled == ["run-1"]


def test_event_limit_is_bounded_and_cancels():
    adapter = FakeAdapter(
        (
            AgentEvent("run-1", 1, AgentEventKind.STARTED),
            AgentEvent("run-1", 2, AgentEventKind.PROGRESS),
            AgentEvent("run-1", 3, AgentEventKind.FINAL),
        )
    )
    registry = AgentRegistry(max_events=2)
    registry.register(adapter)
    assert_code("event-limit", lambda: registry.collect("fake-agent", request()))
    assert adapter.cancelled == ["run-1"]


def test_aggregate_output_byte_limit_is_bounded_and_cancels():
    adapter = FakeAdapter(
        (
            AgentEvent("run-1", 1, AgentEventKind.OUTPUT_DELTA, text="123456"),
            AgentEvent("run-1", 2, AgentEventKind.OUTPUT_DELTA, text="78901"),
            AgentEvent("run-1", 3, AgentEventKind.FINAL),
        )
    )
    registry = AgentRegistry(max_output_bytes=10)
    registry.register(adapter)

    assert_code("output-byte-limit", lambda: registry.collect("fake-agent", request()))
    assert adapter.cancelled == ["run-1"]


def test_ask_rejects_action_proposal_event_and_cancels():
    from src.voice.models import ActionProposal

    context = request().context
    proposal = ActionProposal.create(
        proposal_id="proposal-1",
        voice_session_id="voice-1",
        adapter="fake-agent",
        agent_session_id="agent-session-1",
        capability=Capability.EXECUTE,
        destination="terminal",
        operation={"argv": ["true"]},
        context_fingerprint=context.fingerprint,
        summary="Run command",
        created_at=100.0,
        expires_at=130.0,
    )
    adapter = FakeAdapter(
        (AgentEvent("run-1", 1, AgentEventKind.ACTION_PROPOSAL, proposal=proposal),)
    )
    registry = AgentRegistry()
    registry.register(adapter)

    assert_code(
        "action-proposal-in-ask",
        lambda: registry.collect("fake-agent", request()),
    )
    assert adapter.cancelled == ["run-1"]


def test_base_exception_during_stream_cancels_then_reraises():
    class Interrupted(FakeAdapter):
        def stream(self, run_id):
            raise KeyboardInterrupt("private interrupt detail")
            yield  # pragma: no cover - make this a generator

    adapter = Interrupted()
    registry = AgentRegistry()
    registry.register(adapter)

    with pytest.raises(KeyboardInterrupt, match="private interrupt detail"):
        registry.collect("fake-agent", request())
    assert adapter.cancelled == ["run-1"]


def test_undeclared_request_capability_is_rejected_before_send():
    adapter = FakeAdapter(capabilities=frozenset((Capability.READ_CONTEXT,)))
    registry = AgentRegistry()
    registry.register(adapter)
    draft = request(frozenset((Capability.DRAFT,)))

    assert_code(
        "capability-not-declared",
        lambda: registry.collect("fake-agent", draft),
    )
    assert adapter.sent == []


def test_act_cannot_bypass_typed_proposal_and_approval_flow():
    adapter = FakeAdapter(
        capabilities=frozenset((Capability.READ_CONTEXT, Capability.EXECUTE))
    )
    registry = AgentRegistry()
    registry.register(adapter)
    act = AgentRequest(
        request_id="request-1",
        voice_session_id="voice-1",
        mode=VoiceMode.ACT,
        prompt="private action",
        context=request().context,
        capabilities=frozenset((Capability.EXECUTE,)),
    )

    assert_code(
        "action-requires-proposal",
        lambda: registry.collect("fake-agent", act),
    )
    assert adapter.sent == []


def test_changed_capability_provider_failure_is_redacted():
    class FailingCapabilities(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def capabilities(self):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("/home/alice/private adapter state")
            return super().capabilities()

    registry = AgentRegistry()
    registry.register(FailingCapabilities())
    assert_code(
        "capability-discovery-failed",
        lambda: registry.collect("fake-agent", request()),
    )


def test_adapter_failure_diagnostics_do_not_echo_private_details():
    class Failing(FakeAdapter):
        def send(self, request):
            raise RuntimeError("/home/alice/private prompt secret")

    registry = AgentRegistry()
    registry.register(Failing())
    assert_code("send-failed", lambda: registry.collect("fake-agent", request()))


def test_invalid_run_does_not_introspect_hostile_provider_output():
    class BadRun:
        @property
        def run_id(self):
            raise RuntimeError("/home/alice/private run identifier")

    class InvalidRunAdapter(FakeAdapter):
        def send(self, request):
            self.sent.append(request)
            return BadRun()

    adapter = InvalidRunAdapter()
    registry = AgentRegistry()
    registry.register(adapter)

    assert_code("invalid-run", lambda: registry.collect("fake-agent", request()))
    assert adapter.cancelled == []


def test_adapter_selector_subclass_cannot_alias_a_registered_adapter():
    class Forged(str):
        def __hash__(self):
            return hash("fake-agent")

        def __eq__(self, other):
            return True

    adapter = FakeAdapter((AgentEvent("run-1", 1, AgentEventKind.FINAL),))
    registry = AgentRegistry()
    registry.register(adapter)

    assert_code(
        "adapter-unavailable",
        lambda: registry.collect(Forged("wrong"), request()),
    )
    assert adapter.sent == []


def test_capability_discovery_is_bounded_for_untrusted_generators():
    class EndlessCapabilities(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.yields = 0

        def capabilities(self):
            while True:
                self.yields += 1
                yield Capability.READ_CONTEXT

    adapter = EndlessCapabilities()
    assert_code("invalid-capability", lambda: AgentRegistry().register(adapter))
    assert adapter.yields == len(Capability) + 1


def test_agent_event_subclass_cannot_smuggle_unmeasured_output():
    @dataclass(frozen=True)
    class SmuggledEvent(AgentEvent):
        hidden: str = ""

    adapter = FakeAdapter(
        (
            SmuggledEvent(
                "run-1",
                1,
                AgentEventKind.FINAL,
                hidden="x" * 100_000,
            ),
        )
    )
    registry = AgentRegistry(max_output_bytes=1)
    registry.register(adapter)

    assert_code("invalid-event", lambda: registry.collect("fake-agent", request()))
    assert adapter.cancelled == ["run-1"]
