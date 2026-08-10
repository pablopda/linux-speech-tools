"""Thread-safe, backend-neutral orchestration for one voice interaction.

``VoiceSession`` owns policy and volatile state; it does not record audio,
insert text, run agents, synthesize speech, or execute proposed operations.
Those effects remain behind callers and provider interfaces.  The session
does compose the context and permission brokers so their least-privilege
contracts cannot be bypassed by a product client.

Status notifications deliberately contain only :class:`SessionStatus`.
Transcript text, context payloads, targets, proposals, and grants remain
volatile process memory and are erased when the session is closed.
"""

from __future__ import annotations

import math
import hmac
import threading
import time
import uuid
from typing import Callable, List, Optional, Union

from .context import (
    ContextBroker,
    ContextBrokerError,
    ContextExpired,
    RequiredContextUnavailable,
)
from .models import (
    MAX_AGENT_TEXT_BYTES,
    ActionProposal,
    ApprovalGrant,
    ApprovalMethod,
    ApprovalRequest,
    ContextRequest,
    ContextSnapshot,
    FailureCode,
    HIGH_IMPACT_CAPABILITIES,
    Outcome,
    Result,
    SessionActivity,
    SessionLifecycle,
    SessionStatus,
    TERMINAL_LIFECYCLES,
    VoiceMode,
)
from .permissions import PermissionBroker, PermissionDenied


StatusCallback = Callable[[SessionStatus], None]
ActionExecutor = Callable[[ActionProposal], Result]
LiveExecutionGuard = Callable[[ActionProposal], bool]


_MODE_ACTIVITIES = {
    VoiceMode.DICTATE: frozenset(
        (
            SessionActivity.NONE,
            SessionActivity.CAPTURING_CONTEXT,
            SessionActivity.LISTENING,
            SessionActivity.RECORDING,
            SessionActivity.TRANSCRIBING,
            SessionActivity.DELIVERING,
        )
    ),
    VoiceMode.ASK: frozenset(
        (
            SessionActivity.NONE,
            SessionActivity.CAPTURING_CONTEXT,
            SessionActivity.LISTENING,
            SessionActivity.RECORDING,
            SessionActivity.TRANSCRIBING,
            SessionActivity.AGENT_RUNNING,
            SessionActivity.DELIVERING,
            SessionActivity.SYNTHESIZING,
            SessionActivity.SPEAKING,
        )
    ),
    VoiceMode.ACT: frozenset(
        (
            SessionActivity.NONE,
            SessionActivity.CAPTURING_CONTEXT,
            SessionActivity.LISTENING,
            SessionActivity.RECORDING,
            SessionActivity.TRANSCRIBING,
            SessionActivity.AGENT_RUNNING,
            SessionActivity.DELIVERING,
            SessionActivity.SYNTHESIZING,
            SessionActivity.SPEAKING,
            SessionActivity.EXECUTING,
        )
    ),
    VoiceMode.READ: frozenset(
        (
            SessionActivity.NONE,
            SessionActivity.CAPTURING_CONTEXT,
            SessionActivity.LISTENING,
            SessionActivity.RECORDING,
            SessionActivity.TRANSCRIBING,
            SessionActivity.SYNTHESIZING,
            SessionActivity.SPEAKING,
        )
    ),
}

_START_ACTIVITY = {
    VoiceMode.DICTATE: SessionActivity.LISTENING,
    VoiceMode.ASK: SessionActivity.LISTENING,
    VoiceMode.ACT: SessionActivity.LISTENING,
    VoiceMode.READ: SessionActivity.SYNTHESIZING,
}

_DISPATCH_ACTIVITY = {
    VoiceMode.DICTATE: SessionActivity.DELIVERING,
    VoiceMode.ASK: SessionActivity.AGENT_RUNNING,
    VoiceMode.ACT: SessionActivity.AGENT_RUNNING,
    VoiceMode.READ: SessionActivity.SYNTHESIZING,
}


class VoiceSessionError(RuntimeError):
    """A redacted session error with a stable machine-readable result."""

    def __init__(self, result: Result) -> None:
        if not isinstance(result, Result) or result.ok:
            raise ValueError("voice session errors require an unsuccessful result")
        self.result = result
        self.failure_code = result.failure_code
        super().__init__("voice session failed: {}".format(result.failure_code.value))


class VoiceSession:
    """Own the lifecycle, transcript revisions, context, and action authority.

    Public mutators are serialized with one re-entrant lock.  Status callbacks
    are queued under that lock, then invoked in sequence outside it.  This lets
    an observer read or mutate the session re-entrantly without blocking other
    cancellation work.  Callback failures are ignored; callbacks are an
    observation surface and never part of session correctness.
    """

    def __init__(
        self,
        mode: Union[VoiceMode, str],
        *,
        context_broker: Optional[ContextBroker] = None,
        permission_broker: Optional[PermissionBroker] = None,
        status_callback: Optional[StatusCallback] = None,
        input_category: str = "none",
        output_category: str = "none",
        agent_category: str = "none",
        session_id: Optional[str] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        try:
            normalized_mode = mode if isinstance(mode, VoiceMode) else VoiceMode(mode)
        except (TypeError, ValueError):
            raise ValueError("unknown voice mode")
        if context_broker is not None and not isinstance(context_broker, ContextBroker):
            raise TypeError("context_broker must be a ContextBroker")
        if permission_broker is not None and not isinstance(
            permission_broker, PermissionBroker
        ):
            raise TypeError("permission_broker must be a PermissionBroker")
        if status_callback is not None and not callable(status_callback):
            raise TypeError("status_callback must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")

        created_at = _checked_time(clock())
        self._lock = threading.RLock()
        self._clock = clock
        self._context_broker = context_broker
        self._permission_broker = permission_broker
        self._status_callbacks: List[StatusCallback] = (
            [status_callback] if status_callback is not None else []
        )
        self._status = SessionStatus(
            mode=normalized_mode,
            input_category=input_category,
            output_category=output_category,
            agent_category=agent_category,
            session_id=session_id or uuid.uuid4().hex,
            created_at=created_at,
            updated_at=created_at,
        )
        self._closed = False
        self._notification_queue: List[SessionStatus] = []
        self._publishing = False
        self._capture_generation = 0
        self._active_capture: Optional[int] = None
        self._execution_generation = 0
        self._pending_execution: Optional[int] = None
        self._execution_inflight = False
        self._execution_started = False
        self._transcript = ""
        self._has_transcript = False
        self._transcript_final = False
        self._context: Optional[ContextSnapshot] = None
        self._dispatched = False
        self._ambiguous = False
        self._approval_request: Optional[ApprovalRequest] = None
        self._proposal: Optional[ActionProposal] = None
        self._grant: Optional[ApprovalGrant] = None
        self._last_result: Optional[Result] = None
        self._terminal_result_value: Optional[Result] = None

    @property
    def session_id(self) -> str:
        return self._status.session_id

    @property
    def mode(self) -> VoiceMode:
        return self._status.mode

    @property
    def status(self) -> SessionStatus:
        with self._lock:
            return self._status

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def transcript(self) -> str:
        """Return the privileged in-process transcript, never status data."""
        with self._lock:
            return self._transcript

    @property
    def transcript_revision(self) -> int:
        with self._lock:
            return self._status.revision

    @property
    def transcript_final(self) -> bool:
        with self._lock:
            return self._transcript_final

    @property
    def context_snapshot(self) -> Optional[ContextSnapshot]:
        with self._lock:
            return self._context

    @property
    def approval_request(self) -> Optional[ApprovalRequest]:
        with self._lock:
            return self._approval_request

    @property
    def last_result(self) -> Optional[Result]:
        with self._lock:
            return self._last_result

    def add_status_callback(self, callback: StatusCallback) -> None:
        """Subscribe an identity-unique metadata-only status observer."""
        if not callable(callback):
            raise TypeError("status callback must be callable")
        with self._lock:
            if self._closed:
                raise VoiceSessionError(self._closed_failure_locked())
            if not any(existing is callback for existing in self._status_callbacks):
                self._status_callbacks.append(callback)

    def remove_status_callback(self, callback: StatusCallback) -> bool:
        """Remove an observer by identity; return whether it was registered."""
        if not callable(callback):
            raise TypeError("status callback must be callable")
        with self._lock:
            for index, existing in enumerate(self._status_callbacks):
                if existing is callback:
                    del self._status_callbacks[index]
                    return True
            return False

    def start(self, activity: Optional[Union[SessionActivity, str]] = None) -> Result:
        """Start the session without starting any platform resource."""
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if self._status.lifecycle is SessionLifecycle.RUNNING:
                if activity is None:
                    return self._success_locked()
                try:
                    selected = self._activity(activity)
                except (TypeError, ValueError):
                    return self._invalid_request_locked()
                return self._transition_locked(SessionLifecycle.RUNNING, selected)
            if self._status.lifecycle is not SessionLifecycle.PENDING:
                return self._invalid_transition_locked()
            try:
                selected = (
                    _START_ACTIVITY[self.mode]
                    if activity is None
                    else self._activity(activity)
                )
            except (TypeError, ValueError):
                return self._invalid_request_locked()
            return self._transition_locked(SessionLifecycle.RUNNING, selected)

    def transition(
        self,
        *,
        lifecycle: Union[SessionLifecycle, str] = SessionLifecycle.RUNNING,
        activity: Union[SessionActivity, str] = SessionActivity.NONE,
    ) -> Result:
        """Apply a legal non-terminal lifecycle/activity transition."""
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            try:
                normalized_lifecycle = (
                    lifecycle
                    if isinstance(lifecycle, SessionLifecycle)
                    else SessionLifecycle(lifecycle)
                )
                normalized_activity = self._activity(activity)
            except (TypeError, ValueError):
                return self._invalid_request_locked()
            if normalized_lifecycle in TERMINAL_LIFECYCLES:
                return self._invalid_transition_locked()
            return self._transition_locked(normalized_lifecycle, normalized_activity)

    def set_activity(self, activity: Union[SessionActivity, str]) -> Result:
        return self.transition(lifecycle=SessionLifecycle.RUNNING, activity=activity)

    def pause(self) -> Result:
        return self.transition(
            lifecycle=SessionLifecycle.PAUSED, activity=SessionActivity.NONE
        )

    def resume(self, activity: Optional[Union[SessionActivity, str]] = None) -> Result:
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if self._status.lifecycle is not SessionLifecycle.PAUSED:
                return self._invalid_transition_locked()
            try:
                selected = (
                    _START_ACTIVITY[self.mode]
                    if activity is None
                    else self._activity(activity)
                )
            except (TypeError, ValueError):
                return self._invalid_request_locked()
            return self._transition_locked(SessionLifecycle.RUNNING, selected)

    def stop(self) -> Result:
        return self.transition(
            lifecycle=SessionLifecycle.STOPPING, activity=SessionActivity.NONE
        )

    def capture_context(
        self, request: ContextRequest, *, target: object
    ) -> ContextSnapshot:
        """Capture exactly ``request`` against the explicitly supplied target.

        Expected failures are raised as :class:`VoiceSessionError`, whose
        result contains a closed failure code and no provider exception text.
        Provider I/O deliberately runs without the session lock.  Cancellation
        and close therefore remain responsive even if a provider violates its
        deadline.  A capture generation prevents a late provider response from
        resurrecting a cancelled or superseded session.
        """
        with self._lock:
            self._raise_if_closed_locked()
            if type(request) is not ContextRequest or target is None:
                result = self._invalid_request_locked()
                raise VoiceSessionError(result)
            if self._context_broker is None:
                result = self._finish_locked(
                    Outcome.UNAVAILABLE, FailureCode.UNAVAILABLE, retryable=False
                )
                raise VoiceSessionError(result)
            if (
                self._status.lifecycle in TERMINAL_LIFECYCLES
                or self._approval_request
                or self._active_capture is not None
            ):
                result = self._invalid_transition_locked()
                raise VoiceSessionError(result)

            self._capture_generation += 1
            generation = self._capture_generation
            self._active_capture = generation
            try:
                started = self._ensure_running_locked(SessionActivity.CAPTURING_CONTEXT)
            except BaseException as error:
                if self._active_capture == generation:
                    self._active_capture = None
                if isinstance(error, Exception):
                    raise VoiceSessionError(
                        Result(
                            Outcome.FAILED,
                            FailureCode.INTERNAL,
                            revision=self._status.revision,
                        )
                    )
                raise
            if not started.ok:
                if self._active_capture == generation:
                    self._active_capture = None
                raise VoiceSessionError(started)
            broker = self._context_broker

        snapshot: Optional[ContextSnapshot] = None
        provider_error: Optional[BaseException] = None
        try:
            snapshot = broker.capture(request, target=target)
        except BaseException as error:
            # Provider details never cross the redacted session boundary.
            provider_error = error

        with self._lock:
            if self._active_capture != generation:
                if provider_error is not None and not isinstance(
                    provider_error, Exception
                ):
                    raise provider_error
                raise VoiceSessionError(self._superseded_result_locked())
            self._active_capture = None
            if provider_error is not None:
                if isinstance(provider_error, ContextExpired):
                    result = self._finish_locked(
                        Outcome.UNAVAILABLE,
                        FailureCode.CONTEXT_EXPIRED,
                        retryable=True,
                    )
                elif isinstance(provider_error, RequiredContextUnavailable):
                    code = (
                        FailureCode.PROVIDER_FAILURE
                        if provider_error.reason == "provider-failed"
                        else FailureCode.CONTEXT_UNAVAILABLE
                    )
                    result = self._finish_locked(
                        Outcome.UNAVAILABLE, code, retryable=True
                    )
                elif isinstance(provider_error, ContextBrokerError):
                    result = self._finish_locked(
                        Outcome.FAILED,
                        FailureCode.CONTEXT_UNAVAILABLE,
                        retryable=False,
                    )
                else:
                    result = self._finish_locked(
                        Outcome.FAILED,
                        FailureCode.PROVIDER_FAILURE,
                        retryable=True,
                    )
                if isinstance(provider_error, Exception):
                    raise VoiceSessionError(result)
                raise provider_error
            if type(snapshot) is not ContextSnapshot:
                result = self._finish_locked(
                    Outcome.FAILED,
                    FailureCode.PROVIDER_FAILURE,
                    retryable=False,
                )
                raise VoiceSessionError(result)
            if not snapshot.is_fresh(self._now_locked()):
                result = self._finish_locked(
                    Outcome.UNAVAILABLE,
                    FailureCode.CONTEXT_EXPIRED,
                    retryable=True,
                )
                raise VoiceSessionError(result)

            self._context = snapshot
            transitioned = self._transition_locked(
                SessionLifecycle.RUNNING, SessionActivity.NONE
            )
            if not transitioned.ok:
                self._context = None
                raise VoiceSessionError(transitioned)
            return snapshot

    def accept_transcript(
        self, text: str, revision: int, final: bool = False
    ) -> Result:
        """Accept one bounded, monotonic transcript revision.

        Exact duplicates are idempotent.  A conflicting duplicate, an older
        revision, or any update after finalization is rejected without changing
        the stored transcript or publishing status.
        """
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if not isinstance(text, str) or not isinstance(final, bool):
                return self._invalid_request_locked()
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 0
            ):
                return self._invalid_request_locked()
            try:
                encoded = str.encode(text, "utf-8")
            except UnicodeEncodeError:
                return self._invalid_request_locked()
            if len(encoded) > MAX_AGENT_TEXT_BYTES:
                return self._invalid_request_locked()
            normalized_text = bytes.decode(encoded, "utf-8")
            if self._status.lifecycle in TERMINAL_LIFECYCLES:
                return self._invalid_transition_locked()

            current_revision = self._status.revision
            if revision < current_revision:
                return self._rejected_locked(FailureCode.STALE_REVISION)
            if self._transcript_final:
                if (
                    revision == current_revision
                    and final
                    and normalized_text == self._transcript
                ):
                    return self._success_locked(revision=revision)
                failure = (
                    FailureCode.STALE_REVISION
                    if revision <= current_revision
                    else FailureCode.INVALID_TRANSITION
                )
                return self._rejected_locked(failure)
            if self._has_transcript and revision == current_revision:
                if normalized_text != self._transcript:
                    return self._rejected_locked(FailureCode.STALE_REVISION)
                if final == self._transcript_final:
                    return self._success_locked(revision=revision)

            started = self._ensure_running_locked(SessionActivity.TRANSCRIBING)
            if not started.ok:
                return started
            self._transcript = normalized_text
            self._has_transcript = True
            self._transcript_final = final
            return self._transition_locked(
                SessionLifecycle.RUNNING,
                SessionActivity.TRANSCRIBING,
                revision=revision,
            )

    def dispatch(self) -> Result:
        """Mark the final input ready for its mode-specific external adapter."""
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if self._ambiguous:
                return self._rejected_locked(FailureCode.AMBIGUOUS_DISPATCH)
            if not self._has_transcript or not self._transcript_final:
                return self._invalid_transition_locked()
            if self._status.lifecycle is not SessionLifecycle.RUNNING:
                return self._invalid_transition_locked()
            if self._dispatched:
                return self._success_locked()
            self._dispatched = True
            return self._transition_locked(
                SessionLifecycle.RUNNING, _DISPATCH_ACTIVITY[self.mode]
            )

    def propose_action(self, proposal: ActionProposal) -> ApprovalRequest:
        """Delegate proposal creation to the permission broker in Act mode."""
        with self._lock:
            self._raise_if_closed_locked()
            if self.mode is not VoiceMode.ACT or type(proposal) is not ActionProposal:
                result = self._invalid_request_locked()
                raise VoiceSessionError(result)
            if (
                self._ambiguous
                or self._status.lifecycle is not SessionLifecycle.RUNNING
                or not self._dispatched
                or self._approval_request is not None
                or self._permission_broker is None
            ):
                code = (
                    FailureCode.AMBIGUOUS_DISPATCH
                    if self._ambiguous
                    else FailureCode.INVALID_TRANSITION
                    if self._permission_broker is not None
                    else FailureCode.UNAVAILABLE
                )
                result = self._rejected_locked(code)
                raise VoiceSessionError(result)
            if (
                proposal.voice_session_id != self.session_id
                or self._context is None
                or proposal.context_fingerprint != self._context.fingerprint
                or not self._context.is_fresh(self._now_locked())
            ):
                code = (
                    FailureCode.CONTEXT_EXPIRED
                    if self._context is not None
                    and not self._context.is_fresh(self._now_locked())
                    else FailureCode.APPROVAL_MISMATCH
                )
                result = self._rejected_locked(code)
                raise VoiceSessionError(result)
            bounded_expiry = min(proposal.expires_at, self._context.expires_at)
            if bounded_expiry <= proposal.created_at:
                result = self._rejected_locked(FailureCode.CONTEXT_EXPIRED)
                raise VoiceSessionError(result)
            bounded_proposal = ActionProposal.create(
                proposal_id=proposal.proposal_id,
                voice_session_id=proposal.voice_session_id,
                adapter=proposal.adapter,
                agent_session_id=proposal.agent_session_id,
                capability=proposal.capability,
                destination=proposal.destination,
                operation=proposal.operation,
                context_fingerprint=proposal.context_fingerprint,
                summary=proposal.summary,
                created_at=proposal.created_at,
                expires_at=bounded_expiry,
            )
            try:
                request = self._permission_broker.create_request(bounded_proposal)
            except PermissionDenied as error:
                result = self._rejected_locked(_permission_failure(error.code))
                raise VoiceSessionError(result)
            except BaseException as error:
                result = self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
                if not isinstance(error, Exception):
                    raise
                raise VoiceSessionError(result)
            if type(request) is not ApprovalRequest:
                result = self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
                raise VoiceSessionError(result)
            expected_request = ApprovalRequest.from_proposal(
                bounded_proposal, request_id=request.request_id
            )
            if request != expected_request:
                try:
                    self._permission_broker.deny(request.request_id)
                except BaseException:
                    pass
                result = self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
                raise VoiceSessionError(result)
            self._proposal = bounded_proposal
            self._approval_request = request
            self._grant = None
            try:
                transitioned = self._transition_locked(
                    SessionLifecycle.AWAITING_APPROVAL, SessionActivity.NONE
                )
            except BaseException as error:
                self._discard_approval_locked()
                if isinstance(error, Exception):
                    raise VoiceSessionError(
                        Result(
                            Outcome.FAILED,
                            FailureCode.INTERNAL,
                            revision=self._status.revision,
                        )
                    )
                raise
            if not transitioned.ok:
                self._discard_approval_locked()
                raise VoiceSessionError(transitioned)
            return request

    # The PRD uses the shorter verb in its conceptual contract.
    propose = propose_action

    def mark_proposal_visible(self, request_id: str) -> Result:
        """Record that the complete proposal was visibly presented."""
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if self._status.lifecycle is not SessionLifecycle.AWAITING_APPROVAL:
                return self._invalid_transition_locked()
            if (
                not self._matching_request_locked(request_id)
                or self._permission_broker is None
            ):
                return self._rejected_locked(FailureCode.APPROVAL_MISMATCH)
            try:
                self._permission_broker.mark_visible(request_id)
            except PermissionDenied as error:
                return self._rejected_locked(_permission_failure(error.code))
            except BaseException as error:
                result = self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
                if not isinstance(error, Exception):
                    raise
                return result
            return self._success_locked()

    # A client UI may prefer this natural synonym.
    present = mark_proposal_visible

    def approve(
        self,
        request_id: str,
        *,
        method: Union[ApprovalMethod, str] = ApprovalMethod.VISIBLE,
    ) -> Result:
        """Approve a visible request, retaining its volatile grant internally."""
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if self._ambiguous:
                return self._rejected_locked(FailureCode.AMBIGUOUS_DISPATCH)
            if self._status.lifecycle is not SessionLifecycle.AWAITING_APPROVAL:
                return self._invalid_transition_locked()
            if (
                not self._matching_request_locked(request_id)
                or self._permission_broker is None
            ):
                return self._rejected_locked(FailureCode.APPROVAL_MISMATCH)
            if self._grant is not None:
                return self._rejected_locked(FailureCode.APPROVAL_REPLAYED)
            try:
                requested_method = (
                    method
                    if type(method) is ApprovalMethod
                    else ApprovalMethod(
                        bytes.decode(str.encode(method, "utf-8"), "utf-8")
                    )
                    if isinstance(method, str)
                    else None
                )
                if requested_method is None:
                    raise ValueError("invalid approval method")
                approval_time = self._now_locked()
            except (TypeError, ValueError):
                return self._invalid_request_locked()
            except BaseException as error:
                result = self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
                if not isinstance(error, Exception):
                    raise
                return result
            if self._context is None or not self._context.is_fresh(approval_time):
                self._discard_approval_locked()
                return self._finish_locked(
                    Outcome.REJECTED,
                    FailureCode.CONTEXT_EXPIRED,
                    retryable=False,
                )
            try:
                grant = self._permission_broker.approve(
                    request_id, method=requested_method
                )
            except PermissionDenied as error:
                failure = _permission_failure(error.code)
                if error.code in (
                    "broker-restarted",
                    "expired",
                    "spoken-approval-forbidden",
                    "unknown-request",
                ):
                    self._grant = None
                    return self._finish_locked(
                        Outcome.REJECTED, failure, retryable=False
                    )
                return self._rejected_locked(failure)
            except BaseException as error:
                result = self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
                if not isinstance(error, Exception):
                    raise
                return result
            request = self._approval_request
            if (
                type(grant) is not ApprovalGrant
                or request is None
                or grant.request_id != request.request_id
                or grant.proposal_id != request.proposal_id
                or grant.voice_session_id != request.voice_session_id
                or grant.adapter != request.adapter
                or grant.agent_session_id != request.agent_session_id
                or grant.capability is not request.capability
                or grant.destination != request.destination
                or grant.operation_digest != request.operation_digest
                or grant.context_fingerprint != request.context_fingerprint
                or grant.method is not requested_method
                or (
                    grant.method is ApprovalMethod.SPOKEN
                    and grant.capability in HIGH_IMPACT_CAPABILITIES
                )
                or grant.granted_at < request.created_at
                or grant.granted_at > approval_time
                or grant.expires_at > request.expires_at
            ):
                result = self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
                return result
            self._grant = grant
            return self._success_locked()

    def execute_approved(
        self,
        request_id: str,
        executor: ActionExecutor,
        *,
        live_guard: Optional[LiveExecutionGuard] = None,
    ) -> Result:
        """Consume approval and invoke one exact immutable proposal once.

        The raw grant never leaves this session and there is no reusable
        ``authorized`` state.  A caller supplies the only executor which will
        receive the proposal already bound into the visible request.  An
        optional live guard (normally a target/focus closure) runs immediately
        before grant consumption; false or exceptional guards burn authority.

        The executor runs outside the session lock.  Once invoked, an exception
        or concurrent cancellation is ambiguous and can never be retried.
        """
        if not callable(executor) or (
            live_guard is not None and not callable(live_guard)
        ):
            with self._lock:
                return self._invalid_request_locked()

        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if self._ambiguous:
                self._discard_approval_locked()
                return self._rejected_locked(FailureCode.AMBIGUOUS_DISPATCH)
            if self._execution_started or self._pending_execution is not None:
                return self._rejected_locked(FailureCode.APPROVAL_REPLAYED)
            if self._status.lifecycle is not SessionLifecycle.AWAITING_APPROVAL:
                return self._invalid_transition_locked()
            if (
                not self._matching_request_locked(request_id)
                or self._permission_broker is None
                or self._proposal is None
                or self._grant is None
            ):
                return self._rejected_locked(FailureCode.APPROVAL_MISMATCH)
            if self._context is None or not self._context.is_fresh(self._now_locked()):
                self._discard_approval_locked()
                return self._finish_locked(
                    Outcome.REJECTED,
                    FailureCode.CONTEXT_EXPIRED,
                    retryable=False,
                )

            proposal = self._proposal
            grant = self._grant
            self._execution_generation += 1
            generation = self._execution_generation
            self._pending_execution = generation

        guard_passed = True
        if live_guard is not None:
            try:
                guard_passed = live_guard(proposal) is True
            except BaseException as error:
                if isinstance(error, Exception):
                    guard_passed = False
                else:
                    # A control-flow interruption still burns the pending
                    # authority before it crosses the session boundary.  It
                    # is then re-raised so Ctrl+C/SystemExit retain their
                    # normal process semantics without leaving a reusable
                    # approval behind.
                    with self._lock:
                        if self._pending_execution == generation:
                            self._pending_execution = None
                            self._discard_approval_locked()
                            if (
                                not self._closed
                                and self._status.lifecycle not in TERMINAL_LIFECYCLES
                            ):
                                self._finish_locked(
                                    Outcome.REJECTED,
                                    FailureCode.FOCUS_DRIFT,
                                    retryable=False,
                                )
                    raise

        with self._lock:
            if self._pending_execution != generation:
                return self._superseded_result_locked()
            self._pending_execution = None
            if self._closed or self._status.lifecycle in TERMINAL_LIFECYCLES:
                self._discard_approval_locked()
                return self._superseded_result_locked()
            if not guard_passed:
                self._discard_approval_locked()
                return self._finish_locked(
                    Outcome.REJECTED,
                    FailureCode.FOCUS_DRIFT,
                    retryable=False,
                )
            # Freshness is checked after the live platform guard and directly
            # before the broker's atomic one-use consumption.
            if self._context is None or not self._context.is_fresh(self._now_locked()):
                self._discard_approval_locked()
                return self._finish_locked(
                    Outcome.REJECTED,
                    FailureCode.CONTEXT_EXPIRED,
                    retryable=False,
                )
            try:
                authorized = self._permission_broker.consume(
                    grant,
                    voice_session_id=self.session_id,
                    adapter=proposal.adapter,
                    agent_session_id=proposal.agent_session_id,
                    capability=proposal.capability,
                    destination=proposal.destination,
                    operation=proposal.operation,
                    context_fingerprint=proposal.context_fingerprint,
                    prior_dispatch_ambiguous=self._ambiguous,
                )
            except PermissionDenied as error:
                self._grant = None
                return self._rejected_locked(_permission_failure(error.code))
            except BaseException as error:
                result = self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
                if not isinstance(error, Exception):
                    raise
                return result
            if authorized is not True:
                self._grant = None
                return self._rejected_locked(FailureCode.APPROVAL_DENIED)

            self._approval_request = None
            self._proposal = None
            self._grant = None
            try:
                transitioned = self._transition_locked(
                    SessionLifecycle.RUNNING, SessionActivity.EXECUTING
                )
            except BaseException as error:
                result = self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
                if not isinstance(error, Exception):
                    raise
                return result
            if not transitioned.ok:
                return self._synthesize_terminal_locked(
                    Outcome.FAILED, FailureCode.INTERNAL
                )
            if (
                self._closed
                or self._status.lifecycle is not SessionLifecycle.RUNNING
                or self._status.activity is not SessionActivity.EXECUTING
            ):
                return self._superseded_result_locked()
            self._execution_started = True
            self._execution_inflight = True

        execution_error: Optional[BaseException] = None
        try:
            execution_result = executor(proposal)
        except BaseException as error:
            execution_result = None
            execution_error = error

        finalization_error: Optional[BaseException] = None
        with self._lock:
            self._execution_inflight = False
            try:
                if self._closed:
                    final_result = self._terminal_result_locked()
                elif self._status.lifecycle in TERMINAL_LIFECYCLES:
                    final_result = self._terminal_result_locked()
                elif not isinstance(execution_result, Result):
                    self._ambiguous = True
                    final_result = self._finish_locked(
                        Outcome.AMBIGUOUS,
                        FailureCode.AMBIGUOUS_DISPATCH,
                        retryable=False,
                    )
                elif execution_result.outcome is Outcome.AMBIGUOUS:
                    self._ambiguous = True
                    final_result = self._finish_locked(
                        Outcome.AMBIGUOUS,
                        FailureCode.AMBIGUOUS_DISPATCH,
                        retryable=False,
                    )
                else:
                    final_result = self._finish_locked(
                        execution_result.outcome,
                        execution_result.failure_code,
                        fallback_used=execution_result.fallback_used,
                        retryable=execution_result.retryable,
                    )
            except BaseException as error:
                finalization_error = error
                self._ambiguous = True
                final_result = self._synthesize_terminal_locked(
                    Outcome.AMBIGUOUS, FailureCode.AMBIGUOUS_DISPATCH
                )
            if self._status.lifecycle not in TERMINAL_LIFECYCLES:
                # Returning any non-terminal result after the executor was
                # called would make an external side effect look retryable.
                self._ambiguous = True
                final_result = self._synthesize_terminal_locked(
                    Outcome.AMBIGUOUS, FailureCode.AMBIGUOUS_DISPATCH
                )
        if execution_error is not None and not isinstance(execution_error, Exception):
            raise execution_error
        if finalization_error is not None and not isinstance(
            finalization_error, Exception
        ):
            raise finalization_error
        return final_result

    def deny(self, request_id: str) -> Result:
        """Deny a proposal terminally; denial can never authorize execution."""
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if self._status.lifecycle is not SessionLifecycle.AWAITING_APPROVAL:
                return self._invalid_transition_locked()
            if (
                not self._matching_request_locked(request_id)
                or self._permission_broker is None
            ):
                return self._rejected_locked(FailureCode.APPROVAL_MISMATCH)
            try:
                self._permission_broker.deny(request_id)
            except PermissionDenied as error:
                return self._rejected_locked(_permission_failure(error.code))
            except BaseException as error:
                result = self._synthesize_terminal_locked(
                    Outcome.REJECTED, FailureCode.APPROVAL_DENIED
                )
                if not isinstance(error, Exception):
                    raise
                return result
            self._approval_request = None
            self._proposal = None
            self._grant = None
            return self._finish_locked(
                Outcome.REJECTED, FailureCode.APPROVAL_DENIED, retryable=False
            )

    def mark_ambiguous(self) -> Result:
        """Terminally lock retries, approvals, execution, and later submission."""
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if self._ambiguous and self._last_result is not None:
                return self._last_result
            if self._status.lifecycle in TERMINAL_LIFECYCLES:
                return self._terminal_result_locked()
            self._ambiguous = True
            self._invalidate_execution_locked()
            self._discard_approval_locked()
            return self._finish_locked(
                Outcome.AMBIGUOUS,
                FailureCode.AMBIGUOUS_DISPATCH,
                retryable=False,
            )

    def target_drift(self, *, fallback_succeeded: bool = False) -> Result:
        """Finish with an explicit safe fallback or a stable drift failure."""
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if not isinstance(fallback_succeeded, bool):
                return self._invalid_request_locked()
            if fallback_succeeded:
                return self._finish_locked(
                    Outcome.COMPLETED_WITH_FALLBACK,
                    FailureCode.NONE,
                    fallback_used=True,
                )
            return self._finish_locked(
                Outcome.REJECTED, FailureCode.FOCUS_DRIFT, retryable=False
            )

    def timeout(self) -> Result:
        """Record a timeout detected by the external resource owner."""
        return self.fail(FailureCode.TIMEOUT, retryable=True)

    def fail(
        self,
        failure_code: Union[FailureCode, str] = FailureCode.PROVIDER_FAILURE,
        *,
        retryable: bool = False,
    ) -> Result:
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            try:
                code = (
                    failure_code
                    if isinstance(failure_code, FailureCode)
                    else FailureCode(failure_code)
                )
            except (TypeError, ValueError):
                return self._invalid_request_locked()
            if code is FailureCode.NONE or not isinstance(retryable, bool):
                return self._invalid_request_locked()
            return self._finish_locked(Outcome.FAILED, code, retryable=retryable)

    def complete(self, *, fallback_used: bool = False) -> Result:
        with self._lock:
            unavailable = self._closed_result_locked()
            if unavailable is not None:
                return unavailable
            if not isinstance(fallback_used, bool):
                return self._invalid_request_locked()
            outcome = (
                Outcome.COMPLETED_WITH_FALLBACK if fallback_used else Outcome.COMPLETED
            )
            return self._finish_locked(
                outcome, FailureCode.NONE, fallback_used=fallback_used
            )

    def cancel(self) -> Result:
        """Cancel once; repeated cancellation is side-effect free."""
        with self._lock:
            if self._closed:
                if (
                    self._status.lifecycle is SessionLifecycle.CANCELLED
                    and self._last_result is not None
                ):
                    return self._last_result
                return self._closed_failure_locked()
            if self._status.lifecycle in TERMINAL_LIFECYCLES:
                return self._terminal_result_locked()
            if self._execution_inflight:
                self._ambiguous = True
                self._invalidate_execution_locked()
                try:
                    return self._finish_locked(
                        Outcome.AMBIGUOUS,
                        FailureCode.AMBIGUOUS_DISPATCH,
                        retryable=False,
                    )
                except BaseException as error:
                    # Once execution was handed to a provider, cancellation
                    # can never safely report an ordinary cancellation.  A
                    # failing clock/audit collaborator must still publish a
                    # stable ambiguous terminal result so the manager drops
                    # every resource lease before a control exception leaves.
                    result = Result(
                        Outcome.AMBIGUOUS,
                        FailureCode.AMBIGUOUS_DISPATCH,
                        revision=self._status.revision,
                    )
                    if self._status.lifecycle not in TERMINAL_LIFECYCLES:
                        try:
                            self._status = self._status.transition(
                                lifecycle=SessionLifecycle.FAILED,
                                activity=SessionActivity.NONE,
                                revision=self._status.revision,
                                now=self._status.updated_at,
                                outcome=Outcome.AMBIGUOUS,
                                failure_code=FailureCode.AMBIGUOUS_DISPATCH,
                                cancellable=False,
                            )
                        except (TypeError, ValueError):
                            pass
                    self._last_result = result
                    self._terminal_result_value = result
                    self._publish_locked(self._status)
                    if not isinstance(error, Exception):
                        raise
                    return result
            self._invalidate_capture_locked()
            self._invalidate_execution_locked()
            self._discard_approval_locked()
            try:
                return self._finish_locked(
                    Outcome.CANCELLED, FailureCode.CANCELLED, retryable=False
                )
            except BaseException as error:
                # A faulty clock/audit collaborator must not leave a cancelled
                # session active or its manager resources leased.  Synthesize
                # the same terminal result without another external call,
                # publish it, then preserve control-flow exceptions.
                result = Result(
                    Outcome.CANCELLED,
                    FailureCode.CANCELLED,
                    revision=self._status.revision,
                )
                if self._status.lifecycle not in TERMINAL_LIFECYCLES:
                    try:
                        self._status = self._status.transition(
                            lifecycle=SessionLifecycle.CANCELLED,
                            activity=SessionActivity.NONE,
                            revision=self._status.revision,
                            now=self._status.updated_at,
                            outcome=Outcome.CANCELLED,
                            failure_code=FailureCode.CANCELLED,
                            cancellable=False,
                        )
                    except (TypeError, ValueError):
                        pass
                self._last_result = result
                self._terminal_result_value = result
                self._publish_locked(self._status)
                if not isinstance(error, Exception):
                    raise
                return result

    def close(self) -> None:
        """Idempotently cancel active work, erase rich state, and stop events."""
        control_error: Optional[BaseException] = None
        with self._lock:
            if self._closed:
                return
            try:
                if self._status.lifecycle not in TERMINAL_LIFECYCLES:
                    if self._execution_inflight:
                        self._ambiguous = True
                        self._finish_locked(
                            Outcome.AMBIGUOUS,
                            FailureCode.AMBIGUOUS_DISPATCH,
                            retryable=False,
                        )
                    else:
                        self._finish_locked(
                            Outcome.CANCELLED,
                            FailureCode.CANCELLED,
                            retryable=False,
                        )
            except BaseException as error:
                # Cleanup below is unconditional.  If a faulty clock or
                # collaborator prevented the normal transition, retain a
                # coherent terminal status without calling external code.
                control_error = error
                if self._status.lifecycle not in TERMINAL_LIFECYCLES:
                    outcome = (
                        Outcome.AMBIGUOUS
                        if self._execution_inflight
                        else Outcome.CANCELLED
                    )
                    failure = (
                        FailureCode.AMBIGUOUS_DISPATCH
                        if self._execution_inflight
                        else FailureCode.CANCELLED
                    )
                    try:
                        self._status = self._status.transition(
                            lifecycle=(
                                SessionLifecycle.FAILED
                                if outcome is Outcome.AMBIGUOUS
                                else SessionLifecycle.CANCELLED
                            ),
                            activity=SessionActivity.NONE,
                            revision=self._status.revision,
                            now=self._status.updated_at,
                            outcome=outcome,
                            failure_code=failure,
                            cancellable=False,
                        )
                        result = Result(
                            outcome,
                            failure,
                            revision=self._status.revision,
                        )
                        self._last_result = result
                        self._terminal_result_value = result
                        self._publish_locked(self._status)
                    except (TypeError, ValueError):
                        pass
            finally:
                self._invalidate_capture_locked()
                self._invalidate_execution_locked()
                self._discard_approval_locked()
                self._transcript = ""
                self._has_transcript = False
                self._transcript_final = False
                self._context = None
                self._proposal = None
                self._grant = None
                self._approval_request = None
                self._closed = True
                self._status_callbacks = []
                self._notification_queue = []
        if control_error is not None and not isinstance(control_error, Exception):
            raise control_error

    def _activity(self, value: Union[SessionActivity, str]) -> SessionActivity:
        activity = (
            value if isinstance(value, SessionActivity) else SessionActivity(value)
        )
        if activity not in _MODE_ACTIVITIES[self.mode]:
            raise ValueError("activity is not available in this voice mode")
        return activity

    def _ensure_running_locked(self, activity: SessionActivity) -> Result:
        if self._status.lifecycle is SessionLifecycle.PENDING:
            return self._transition_locked(SessionLifecycle.RUNNING, activity)
        if self._status.lifecycle is SessionLifecycle.RUNNING:
            if self._status.activity is activity:
                return self._success_locked()
            return self._transition_locked(SessionLifecycle.RUNNING, activity)
        return self._invalid_transition_locked()

    def _transition_locked(
        self,
        lifecycle: SessionLifecycle,
        activity: SessionActivity,
        *,
        revision: Optional[int] = None,
    ) -> Result:
        if activity not in _MODE_ACTIVITIES[self.mode]:
            return self._invalid_request_locked()
        try:
            next_status = self._status.transition(
                lifecycle=lifecycle,
                activity=activity,
                revision=revision,
                now=self._now_locked(),
            )
        except (TypeError, ValueError):
            return self._invalid_transition_locked()
        self._status = next_status
        result = self._success_locked(revision=next_status.revision)
        self._publish_locked(next_status)
        # A callback may synchronously cancel/close or otherwise transition the
        # same session.  Never report the superseded transition as authority.
        if self._closed:
            return self._closed_failure_locked()
        if self._status is not next_status:
            if self._status.lifecycle in TERMINAL_LIFECYCLES:
                return self._terminal_result_locked()
            return self._invalid_transition_locked()
        return result

    def _finish_locked(
        self,
        outcome: Outcome,
        failure_code: FailureCode,
        *,
        fallback_used: bool = False,
        retryable: bool = False,
    ) -> Result:
        if self._status.lifecycle in TERMINAL_LIFECYCLES:
            return self._terminal_result_locked()
        if self._execution_inflight and outcome is not Outcome.AMBIGUOUS:
            outcome = Outcome.AMBIGUOUS
            failure_code = FailureCode.AMBIGUOUS_DISPATCH
            fallback_used = False
            retryable = False
            self._ambiguous = True
        if self._status.lifecycle is SessionLifecycle.PENDING:
            started = self._transition_locked(
                SessionLifecycle.RUNNING, SessionActivity.NONE
            )
            if not started.ok:
                return started
        self._invalidate_capture_locked()
        self._invalidate_execution_locked()
        self._discard_approval_locked()
        if self._status.lifecycle in (
            SessionLifecycle.AWAITING_APPROVAL,
            SessionLifecycle.PAUSED,
        ):
            resumed = self._transition_locked(
                SessionLifecycle.RUNNING, SessionActivity.NONE
            )
            if not resumed.ok:
                return resumed
        result = Result(
            outcome,
            failure_code,
            revision=self._status.revision,
            fallback_used=fallback_used,
            retryable=retryable,
        )
        lifecycle = (
            SessionLifecycle.COMPLETED
            if result.ok
            else SessionLifecycle.CANCELLED
            if outcome is Outcome.CANCELLED
            else SessionLifecycle.FAILED
        )
        try:
            self._status = self._status.transition(
                lifecycle=lifecycle,
                activity=SessionActivity.NONE,
                revision=self._status.revision,
                now=self._now_locked(),
                outcome=outcome,
                failure_code=failure_code,
                cancellable=False,
            )
        except ValueError:
            return self._invalid_transition_locked()
        self._last_result = result
        self._terminal_result_value = result
        self._publish_locked(self._status)
        return result

    def _synthesize_terminal_locked(
        self, outcome: Outcome, failure_code: FailureCode
    ) -> Result:
        """Finish without consulting clocks/providers after an external fault."""
        if self._status.lifecycle in TERMINAL_LIFECYCLES:
            return self._terminal_result_locked()
        self._invalidate_capture_locked()
        self._invalidate_execution_locked()
        self._discard_approval_locked()
        result = Result(
            outcome,
            failure_code,
            revision=self._status.revision,
            retryable=False,
        )
        lifecycle = (
            SessionLifecycle.CANCELLED
            if outcome is Outcome.CANCELLED
            else SessionLifecycle.FAILED
        )
        self._status = self._status.transition(
            lifecycle=lifecycle,
            activity=SessionActivity.NONE,
            revision=self._status.revision,
            now=self._status.updated_at,
            outcome=outcome,
            failure_code=failure_code,
            cancellable=False,
        )
        self._last_result = result
        self._terminal_result_value = result
        self._publish_locked(self._status)
        return result

    def _discard_approval_locked(self) -> None:
        request = self._approval_request
        broker = self._permission_broker
        try:
            if request is not None and broker is not None:
                broker.deny(request.request_id)
        except BaseException:
            # Revocation is best effort, but local authority and rich proposal
            # state are never allowed to survive collaborator failure.  This
            # path is used by cancellation/close, so cleanup takes precedence
            # over a secondary clock/audit interruption inside the broker.
            pass
        finally:
            self._approval_request = None
            self._proposal = None
            self._grant = None

    def _invalidate_capture_locked(self) -> None:
        self._capture_generation += 1
        self._active_capture = None

    def _invalidate_execution_locked(self) -> None:
        self._execution_generation += 1
        self._pending_execution = None
        self._execution_inflight = False

    def _superseded_result_locked(self) -> Result:
        if self._status.lifecycle in TERMINAL_LIFECYCLES:
            return self._terminal_result_locked()
        if self._closed:
            return self._closed_failure_locked()
        return self._rejected_locked(FailureCode.INVALID_TRANSITION)

    def _matching_request_locked(self, request_id: object) -> bool:
        if not isinstance(request_id, str) or self._approval_request is None:
            return False
        try:
            supplied = str.encode(request_id, "utf-8")
            expected = str.encode(self._approval_request.request_id, "utf-8")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(supplied, expected)

    def _now_locked(self) -> float:
        # SessionStatus forbids time reversal; a wall-clock correction should
        # not turn a valid state update into an internal failure.
        return max(self._status.updated_at, _checked_time(self._clock()))

    def _publish_locked(self, status: SessionStatus) -> None:
        """Queue and drain status notifications in order outside ``_lock``.

        The first publisher becomes the drainer.  Concurrent and re-entrant
        transitions only append to the queue, so callbacks never overlap and
        sequence order is preserved without holding the session state lock.
        """
        if self._closed:
            return
        self._notification_queue.append(status)
        if self._publishing:
            return
        self._publishing = True
        try:
            while self._notification_queue:
                pending = self._notification_queue.pop(0)
                callbacks = tuple(self._status_callbacks)
                release_all = getattr(self._lock, "_release_save", None)
                restore_all = getattr(self._lock, "_acquire_restore", None)
                if callable(release_all) and callable(restore_all):
                    lock_state = release_all()
                    used_full_release = True
                else:  # pragma: no cover - alternate Python lock implementation
                    self._lock.release()
                    lock_state = None
                    used_full_release = False
                try:
                    for callback in callbacks:
                        try:
                            callback(pending)
                        except BaseException:
                            # Observers are best-effort metadata sinks.  A
                            # broken observer cannot corrupt publication state
                            # or suppress later subscribers and transitions.
                            pass
                finally:
                    if used_full_release:
                        restore_all(lock_state)
                    else:  # pragma: no cover - alternate Python lock implementation
                        self._lock.acquire()
        finally:
            self._publishing = False

    def _raise_if_closed_locked(self) -> None:
        result = self._closed_result_locked()
        if result is not None:
            raise VoiceSessionError(result)

    def _closed_result_locked(self) -> Optional[Result]:
        return self._closed_failure_locked() if self._closed else None

    def _closed_failure_locked(self) -> Result:
        return Result(
            Outcome.REJECTED,
            FailureCode.CLOSED,
            revision=self._status.revision,
            retryable=False,
        )

    def _invalid_request_locked(self) -> Result:
        return self._rejected_locked(FailureCode.INVALID_REQUEST)

    def _invalid_transition_locked(self) -> Result:
        return self._rejected_locked(FailureCode.INVALID_TRANSITION)

    def _rejected_locked(self, failure_code: FailureCode) -> Result:
        result = Result(
            Outcome.REJECTED,
            failure_code,
            revision=self._status.revision,
            retryable=False,
        )
        self._last_result = result
        return result

    def _success_locked(self, *, revision: Optional[int] = None) -> Result:
        result = Result(
            Outcome.COMPLETED,
            revision=self._status.revision if revision is None else revision,
        )
        self._last_result = result
        return result

    def _terminal_result_locked(self) -> Result:
        if self._terminal_result_value is not None:
            return self._terminal_result_value
        outcome = self._status.outcome or Outcome.FAILED
        code = self._status.failure_code
        if outcome is Outcome.CANCELLED and code is FailureCode.NONE:
            code = FailureCode.CANCELLED
        return Result(outcome, code, revision=self._status.revision)


def _checked_time(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("clock must return a number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("clock must return a finite non-negative value")
    return result


def _permission_failure(code: str) -> FailureCode:
    if code in ("expired",):
        return FailureCode.APPROVAL_EXPIRED
    if code in ("replay", "already-approved", "unknown-request"):
        return FailureCode.APPROVAL_REPLAYED
    if code in ("prior-dispatch-ambiguous",):
        return FailureCode.AMBIGUOUS_DISPATCH
    if code in (
        "binding-mismatch",
        "broker-restarted",
        "unknown-grant",
        "undeclared-capability",
        "operation-digest-mismatch",
    ):
        return FailureCode.APPROVAL_MISMATCH
    return FailureCode.APPROVAL_DENIED


__all__ = [
    "ActionExecutor",
    "LiveExecutionGuard",
    "StatusCallback",
    "VoiceSession",
    "VoiceSessionError",
]
