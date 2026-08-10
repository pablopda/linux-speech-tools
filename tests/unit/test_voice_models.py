"""Contract tests for the backend-neutral voice workspace domain models."""

import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from src.stt.target_context import TargetContext
from src.voice.models import (
    ActionProposal,
    AgentEvent,
    AgentEventKind,
    AgentRequest,
    AgentRun,
    ApprovalGrant,
    ApprovalMethod,
    ApprovalRequest,
    Capability,
    ContextItem,
    ContextKind,
    ContextRequest,
    ContextSnapshot,
    FailureCode,
    Outcome,
    Result,
    Sensitivity,
    SessionActivity,
    SessionLifecycle,
    SessionStatus,
    TargetBinding,
    VoiceMode,
    canonical_operation_digest,
)


def item(
    content="private selected text",
    *,
    kind=ContextKind.SELECTION,
    provider="selection",
    captured_at=100.0,
    expires_at=160.0,
    shareable=True,
):
    return ContextItem(
        kind=kind,
        provider=provider,
        sensitivity=Sensitivity.PRIVATE,
        captured_at=captured_at,
        expires_at=expires_at,
        content=content,
        metadata={"private_path": "/home/alice/project/secret.py"},
        shareable=shareable,
        required_capability=Capability.READ_CONTEXT,
    )


def snapshot(*, content="private selected text", window_id="gnome:secret:42"):
    return ContextSnapshot.create(
        target=TargetContext(
            kind="browser",
            source="gnome-focus",
            app_id="secret.app",
            title="Private customer title",
            window_id=window_id,
            focus_generation=7,
        ),
        items=(item(content),),
        permissions=frozenset((Capability.READ_CONTEXT,)),
        now=100.0,
        snapshot_id="snapshot-1",
    )


def test_context_item_is_immutable_bounded_and_status_redacts_content_metadata():
    value = item()

    with pytest.raises(FrozenInstanceError):
        value.content = "changed"
    with pytest.raises(TypeError):
        value.metadata["new"] = "value"

    status = repr(value.to_status_dict(at=110.0))
    assert value.is_fresh(110.0)
    assert "private selected text" not in status
    assert "/home/alice" not in status
    assert "private_path" not in status
    assert value.to_payload()["content"] == "private selected text"


def test_context_item_requires_explicit_share_authorization():
    value = item(shareable=False)
    with pytest.raises(ValueError, match="not shareable"):
        value.to_payload()


def test_context_provider_and_categories_are_privacy_safe_tokens():
    with pytest.raises(ValueError, match="privacy-safe token"):
        item(provider="/home/alice/private-provider")
    with pytest.raises(ValueError, match="privacy-safe token"):
        SessionStatus(mode=VoiceMode.ASK, input_category="private path")


def test_context_request_requires_declared_required_and_shareable_kinds():
    with pytest.raises(ValueError, match="must be requested"):
        ContextRequest(
            request_id="request-1",
            kinds=frozenset((ContextKind.REPOSITORY,)),
            permissions=frozenset((Capability.READ_CONTEXT,)),
            required_kinds=frozenset((ContextKind.SELECTION,)),
            created_at=100.0,
            deadline=110.0,
        )


def test_context_fingerprint_binds_content_target_and_permissions():
    base = snapshot()
    changed_content = snapshot(content="different")
    changed_target = snapshot(window_id="gnome:secret:43")
    changed_permissions = ContextSnapshot.create(
        target=TargetContext(window_id="gnome:secret:42"),
        items=(item(),),
        permissions=frozenset((Capability.READ_CONTEXT, Capability.DRAFT)),
        now=100.0,
        snapshot_id="snapshot-2",
    )

    assert len(base.fingerprint) == 64
    assert base.fingerprint != changed_content.fingerprint
    assert base.fingerprint != changed_target.fingerprint
    assert base.fingerprint != changed_permissions.fingerprint


def test_context_snapshot_status_redacts_target_and_payload():
    value = snapshot()
    encoded = repr(value.to_status_dict(at=110.0))

    for secret in (
        "private selected text",
        "Private customer title",
        "secret.app",
        "gnome:secret:42",
        "/home/alice",
    ):
        assert secret not in encoded
    assert value.fingerprint not in encoded


def test_context_snapshot_detects_fingerprint_tampering():
    value = snapshot()
    with pytest.raises(ValueError, match="does not match"):
        replace(value, fingerprint="0" * 64)


def test_session_status_uses_independent_lifecycle_activity_and_sequence():
    initial = SessionStatus(
        mode=VoiceMode.DICTATE,
        input_category="microphone",
        output_category="clipboard",
        created_at=100.0,
        updated_at=100.0,
    )
    listening = initial.transition(
        lifecycle=SessionLifecycle.RUNNING,
        activity=SessionActivity.LISTENING,
        revision=0,
        now=101.0,
    )
    recording = listening.transition(
        lifecycle=SessionLifecycle.RUNNING,
        activity=SessionActivity.RECORDING,
        revision=1,
        now=102.0,
    )

    assert recording.lifecycle is SessionLifecycle.RUNNING
    assert recording.activity is SessionActivity.RECORDING
    assert recording.revision == 1
    assert recording.sequence == 2


def test_session_status_rejects_stale_revision_and_invalid_terminal_axes():
    running = SessionStatus(
        mode=VoiceMode.ASK,
        lifecycle=SessionLifecycle.RUNNING,
        activity=SessionActivity.AGENT_RUNNING,
        revision=2,
    )
    with pytest.raises(ValueError, match=FailureCode.STALE_REVISION.value):
        running.transition(
            lifecycle=SessionLifecycle.RUNNING,
            activity=SessionActivity.AGENT_RUNNING,
            revision=1,
        )
    with pytest.raises(ValueError, match="terminal lifecycle"):
        SessionStatus(
            mode=VoiceMode.READ,
            lifecycle=SessionLifecycle.COMPLETED,
            activity=SessionActivity.SPEAKING,
            outcome=Outcome.COMPLETED,
        )
    with pytest.raises(ValueError, match="requires a failure code"):
        SessionStatus(
            mode=VoiceMode.ACT,
            lifecycle=SessionLifecycle.FAILED,
            outcome=Outcome.FAILED,
        )


def test_session_status_serializer_has_only_coarse_fields():
    status = SessionStatus(
        mode=VoiceMode.ASK,
        input_category="microphone",
        output_category="overlay",
        agent_category="codex",
    ).to_status_dict()
    assert set(status) == {
        "schema_version",
        "mode",
        "lifecycle",
        "activity",
        "revision",
        "sequence",
        "input_category",
        "output_category",
        "agent_category",
        "cancellable",
        "created_at",
        "updated_at",
        "outcome",
        "failure_code",
    }


def test_result_success_and_failure_invariants():
    assert Result(Outcome.COMPLETED).ok
    assert Result(Outcome.COMPLETED_WITH_FALLBACK, fallback_used=True).ok
    assert not Result(Outcome.FAILED, FailureCode.PROVIDER_FAILURE).ok
    with pytest.raises(ValueError, match="successful result"):
        Result(Outcome.COMPLETED, FailureCode.INTERNAL)
    with pytest.raises(ValueError, match="requires a failure code"):
        Result(Outcome.AMBIGUOUS)


def test_ask_request_rejects_consequential_capability_and_redacts_prompt():
    context = snapshot()
    with pytest.raises(ValueError, match="cannot request consequential"):
        AgentRequest(
            request_id="request-1",
            voice_session_id="voice-1",
            mode=VoiceMode.ASK,
            prompt="run the private command",
            context=context,
            capabilities=frozenset((Capability.EXECUTE,)),
        )

    request = AgentRequest(
        request_id="request-2",
        voice_session_id="voice-1",
        mode=VoiceMode.ASK,
        prompt="private question",
        context=context,
        capabilities=frozenset((Capability.READ_CONTEXT,)),
    )
    encoded = repr(request.to_status_dict())
    assert "private question" not in encoded
    assert request.request_id not in encoded
    assert request.context.fingerprint not in encoded


def test_operation_digest_is_canonical_and_rejects_non_json_values():
    left = canonical_operation_digest(
        {"environment": {"B": "2", "A": "1"}, "argv": ["true"]}
    )
    right = canonical_operation_digest(
        {"argv": ["true"], "environment": {"A": "1", "B": "2"}}
    )
    assert left == right
    with pytest.raises(ValueError, match="canonical JSON"):
        canonical_operation_digest({"bad": object()})


def proposal(operation=None):
    return ActionProposal.create(
        proposal_id="proposal-1",
        voice_session_id="voice-1",
        adapter="test-adapter",
        agent_session_id="agent-session-1",
        capability=Capability.EXECUTE,
        destination="terminal:/private/project",
        operation=operation or {"argv": ["pytest", "tests/unit"]},
        context_fingerprint="f" * 64,
        summary="Run private tests",
        created_at=100.0,
        expires_at=130.0,
    )


def test_action_proposal_freezes_operation_and_redacts_destination_summary():
    value = proposal()
    with pytest.raises(TypeError):
        value.operation["argv"] = ("false",)
    status = repr(value.to_status_dict())
    assert "terminal:/private" not in status
    assert "Run private tests" not in status
    assert "pytest" not in status
    assert value.proposal_id not in status
    assert value.context_fingerprint not in status
    assert value.operation_digest not in status


def test_approval_request_exposes_the_exact_frozen_operation_only_in_memory():
    value = proposal(
        {
            "argv": ["sh", "-c", "destructive-command"],
            "cwd": "/private/project",
        }
    )
    request = ApprovalRequest.from_proposal(value, request_id="approval-1")

    assert request.operation == value.operation
    assert request.operation_digest == value.operation_digest
    with pytest.raises(TypeError):
        request.operation["argv"] = ("true",)
    status = repr(request.to_status_dict())
    assert "destructive-command" not in status
    assert "/private/project" not in status


def test_adapter_snapshot_minimizes_permissions_to_shared_items():
    private = item(shareable=False)
    shared = item(content="shared", shareable=True)
    context = ContextSnapshot.create(
        target=TargetBinding(captured_at=100.0),
        items=(private, shared),
        permissions=frozenset((Capability.READ_CONTEXT, Capability.DELETE)),
        now=100.0,
    )

    adapted = context.for_adapter(at=101.0)

    assert adapted.items == (shared,)
    assert adapted.permissions == frozenset((Capability.READ_CONTEXT,))


def test_models_and_operations_reject_non_utf8_surrogates():
    with pytest.raises(ValueError, match="canonical JSON"):
        proposal({"argv": ["echo", "bad-\udcff"]})
    with pytest.raises(ValueError, match="UTF-8"):
        ActionProposal.create(
            proposal_id="proposal-surrogate",
            voice_session_id="voice-1",
            adapter="test-adapter",
            agent_session_id="agent-session-1",
            capability=Capability.EXECUTE,
            destination="terminal:\udcff",
            operation={"argv": ["true"]},
            context_fingerprint="f" * 64,
            summary="invalid destination",
            created_at=100.0,
            expires_at=130.0,
        )
    with pytest.raises(ValueError, match="UTF-8"):
        ContextItem(
            kind=ContextKind.SELECTION,
            provider="selection",
            sensitivity=Sensitivity.SECRET,
            captured_at=100.0,
            expires_at=130.0,
            content="secret\ud800tail",
        )
    with pytest.raises(ValueError, match="UTF-8"):
        AgentRequest(
            request_id="request-surrogate",
            voice_session_id="voice-1",
            mode=VoiceMode.ASK,
            prompt="secret\ud800tail",
            context=ContextSnapshot.create(
                target=None,
                items=(),
                permissions=frozenset(),
                now=100.0,
            ),
            capabilities=frozenset(),
        )
    with pytest.raises(ValueError, match="UTF-8"):
        AgentEvent(
            run_id="run-1",
            sequence=1,
            kind=AgentEventKind.OUTPUT_DELTA,
            text="secret\ud800tail",
        )


def test_rich_model_reprs_are_metadata_only_and_grant_authority_is_hidden():
    value = proposal()
    approval = ApprovalRequest.from_proposal(value, request_id="approval-private")
    grant = ApprovalGrant(
        grant_id="live-secret-grant",
        request_id=approval.request_id,
        proposal_id=value.proposal_id,
        voice_session_id=value.voice_session_id,
        adapter=value.adapter,
        agent_session_id=value.agent_session_id,
        capability=value.capability,
        destination=value.destination,
        operation_digest=value.operation_digest,
        context_fingerprint=value.context_fingerprint,
        granted_at=101.0,
        expires_at=120.0,
        method=ApprovalMethod.VISIBLE,
    )
    rich = (
        item(content="private-context"),
        snapshot(content="private-snapshot"),
        AgentRequest(
            request_id="private-agent-request",
            voice_session_id="private-voice",
            mode=VoiceMode.ASK,
            prompt="private-prompt",
            context=snapshot(),
            capabilities=frozenset((Capability.READ_CONTEXT,)),
        ),
        value,
        approval,
        grant,
        AgentEvent(
            run_id="private-run",
            sequence=1,
            kind=AgentEventKind.OUTPUT_DELTA,
            text="private-output",
        ),
    )
    encoded = repr(rich)
    for secret in (
        "private-context",
        "private-snapshot",
        "private-prompt",
        "private-output",
        "live-secret-grant",
        "terminal:/private/project",
        "pytest",
        value.voice_session_id,
        value.agent_session_id,
    ):
        assert secret not in encoded


def test_approval_grant_status_omits_nonce_destination_and_sessions():
    value = proposal()
    grant = ApprovalGrant(
        grant_id="broker.nonce-secret",
        request_id="request-1",
        proposal_id=value.proposal_id,
        voice_session_id=value.voice_session_id,
        adapter=value.adapter,
        agent_session_id=value.agent_session_id,
        capability=value.capability,
        destination=value.destination,
        operation_digest=value.operation_digest,
        context_fingerprint=value.context_fingerprint,
        granted_at=101.0,
        expires_at=120.0,
        method=ApprovalMethod.VISIBLE,
    )
    status = repr(grant.to_status_dict())
    for secret in (
        "nonce-secret",
        "terminal:/private",
        "voice-1",
        "agent-session-1",
    ):
        assert secret not in status
    assert value.context_fingerprint not in status
    assert value.operation_digest not in status


def test_status_serializers_omit_caller_ids_hashes_and_context_fingerprints():
    value = proposal()
    approval = ApprovalRequest.from_proposal(value, request_id="caller-request-123")
    context = snapshot()
    request = AgentRequest(
        request_id="caller-agent-request-123",
        voice_session_id="caller-voice-session-123",
        mode=VoiceMode.ASK,
        prompt="private prompt",
        context=context,
        capabilities=frozenset((Capability.READ_CONTEXT,)),
    )
    run = AgentRun(
        run_id="caller-run-123",
        adapter_id="test-adapter",
        state="running",
        agent_session_id="caller-agent-session-123",
    )
    event = AgentEvent(
        run_id=run.run_id,
        sequence=1,
        kind=AgentEventKind.OUTPUT_DELTA,
        text="private output",
    )
    statuses = (
        context.to_status_dict(at=110.0),
        request.to_status_dict(),
        run.to_status_dict(),
        value.to_status_dict(),
        approval.to_status_dict(),
        event.to_status_dict(),
    )
    encoded = json.dumps(statuses, sort_keys=True)
    caller_ids = (
        context.snapshot_id,
        request.request_id,
        request.voice_session_id,
        run.run_id,
        run.agent_session_id,
        value.proposal_id,
        approval.request_id,
    )
    for caller_id in caller_ids:
        assert caller_id not in encoded
        assert hashlib.sha256(caller_id.encode("utf-8")).hexdigest() not in encoded
    assert context.fingerprint not in encoded
    assert value.context_fingerprint not in encoded
    assert value.operation_digest not in encoded


def test_agent_event_enforces_proposal_and_error_shapes_and_redacts_text():
    with pytest.raises(ValueError, match="requires an action proposal"):
        AgentEvent("run-1", 1, AgentEventKind.ACTION_PROPOSAL)
    with pytest.raises(ValueError, match="requires a failure code"):
        AgentEvent("run-1", 1, AgentEventKind.ERROR)

    event = AgentEvent(
        "run-1",
        1,
        AgentEventKind.OUTPUT_DELTA,
        text="private model response",
    )
    assert not event.terminal
    status = repr(event.to_status_dict())
    assert "private model response" not in status
    assert event.run_id not in status
    assert AgentEvent("run-1", 2, AgentEventKind.FINAL).terminal


def test_rich_text_subclasses_are_normalized_to_exact_builtin_strings():
    class Sneaky(str):
        hidden = "x" * 100_000

        def encode(self, *args, **kwargs):
            return b"x"

        def __len__(self):
            return 1

    context_item = ContextItem(
        kind=ContextKind.SELECTION,
        provider=Sneaky("selection"),
        sensitivity=Sensitivity.SECRET,
        captured_at=100.0,
        expires_at=130.0,
        content=Sneaky("ok"),
        metadata={Sneaky("label"): Sneaky("value")},
    )
    context = ContextSnapshot.create(
        target=None,
        items=(context_item,),
        permissions=frozenset((Capability.READ_CONTEXT,)),
        now=100.0,
    )
    request = AgentRequest(
        request_id=Sneaky("request-1"),
        voice_session_id=Sneaky("voice-1"),
        mode=VoiceMode.ASK,
        prompt=Sneaky("question"),
        context=context,
        capabilities=frozenset((Capability.READ_CONTEXT,)),
    )
    event = AgentEvent(
        run_id=Sneaky("run-1"),
        sequence=1,
        kind=AgentEventKind.OUTPUT_DELTA,
        text=Sneaky("answer"),
    )

    assert type(context_item.provider) is str
    assert type(context_item.content) is str
    assert all(type(key) is str for key in context_item.metadata)
    assert all(type(value) is str for value in context_item.metadata.values())
    assert type(request.request_id) is str
    assert type(request.prompt) is str
    assert type(event.run_id) is str
    assert type(event.text) is str


def test_result_bounds_normalized_text_and_json_numbers_are_exact_builtins():
    class Liar(str):
        def encode(self, *args, **kwargs):
            return b"x"

        def __len__(self):
            return 1

    class RichInt(int):
        extra = "x" * 100_000

    with pytest.raises(ValueError, match="limit"):
        Result(Outcome.COMPLETED, message=Liar("x" * 5000))

    value = proposal({"count": RichInt(1)})
    assert type(value.operation["count"]) is int

    result = Result(Outcome.COMPLETED, revision=RichInt(2))
    status = SessionStatus(
        VoiceMode.ASK,
        revision=RichInt(3),
        sequence=RichInt(4),
        created_at=100.0,
        updated_at=100.0,
    )
    target = TargetBinding(captured_at=100.0, generation=RichInt(5))
    event = AgentEvent("run-1", RichInt(6), AgentEventKind.FINAL)
    assert type(result.revision) is int
    assert type(status.revision) is int
    assert type(status.sequence) is int
    assert type(target.generation) is int
    assert type(event.sequence) is int


def test_context_request_provider_selection_is_bounded():
    yielded = []

    def endless_providers():
        index = 0
        while True:
            yielded.append(index)
            yield "provider-{}".format(index)
            index += 1

    with pytest.raises(ValueError, match="too many"):
        ContextRequest(
            request_id="request-many-providers",
            kinds=frozenset((ContextKind.REPOSITORY,)),
            permissions=frozenset((Capability.READ_CONTEXT,)),
            provider_names=endless_providers(),
            created_at=100.0,
            deadline=110.0,
        )
    assert len(yielded) == 65
