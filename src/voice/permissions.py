"""Fail-closed, proposal-bound authorization for consequential voice actions.

The broker deliberately keeps policy in process.  Approval grants are volatile,
bound to one exact proposal, and consumed before an adapter is allowed to
dispatch.  Restarting the broker therefore invalidates every outstanding grant.

Only opaque identifiers and closed reason codes enter the bounded audit.  Raw
prompts, operation payloads, context, destinations, paths, titles, and audio are
never copied into it.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from itertools import islice
from typing import (
    Callable,
    Deque,
    Dict,
    FrozenSet,
    Iterable,
    Mapping,
    Optional,
    Tuple,
    Union,
)

from .models import (
    ActionProposal,
    ApprovalGrant,
    ApprovalMethod,
    ApprovalRequest,
    Capability,
    HIGH_IMPACT_CAPABILITIES,
    canonical_operation_digest,
)


_SPOKEN_DENIED_CAPABILITIES = HIGH_IMPACT_CAPABILITIES
_MAX_IDENTIFIER_LENGTH = 512


class PermissionDenied(RuntimeError):
    """A closed-code authorization failure which never echoes private input."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("permission denied: {0}".format(code))


@dataclass(frozen=True)
class PermissionAuditEvent:
    """A deliberately small, privacy-safe authorization audit record."""

    sequence: int
    occurred_at: float
    event: str
    decision: str
    reason: str
    capability: str
    request_ref: str


@dataclass
class _RequestRecord:
    request: ApprovalRequest
    visible_at: Optional[float] = None
    grant_key: Optional[str] = None


@dataclass(frozen=True)
class _GrantRecord:
    request_id: str
    proposal_id: str
    voice_session_id: str
    adapter: str
    agent_session_id: str
    capability: Capability
    destination: str
    operation_digest: str
    context_fingerprint: str
    granted_at: float
    expires_at: float
    method: ApprovalMethod


def _coerce_capability(value: Union[Capability, str]) -> Capability:
    try:
        return value if isinstance(value, Capability) else Capability(value)
    except (TypeError, ValueError):
        raise PermissionDenied("unknown-capability")


def _validate_identifier(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_IDENTIFIER_LENGTH:
        raise PermissionDenied("invalid-binding")
    try:
        encoded = str.encode(value, "utf-8")
    except UnicodeEncodeError:
        raise PermissionDenied("invalid-binding")
    return bytes.decode(encoded, "utf-8")


def _safe_ref(value: str) -> str:
    return hashlib.sha256(
        b"linux-speech-tools:audit-ref:v1\0" + value.encode("utf-8")
    ).hexdigest()[:20]


def _constant_string_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class PermissionBroker:
    """Issue and atomically consume exact, volatile approval grants.

    The required flow is ``create_request`` -> ``mark_visible`` -> ``approve``
    -> ``consume``.  A request cannot be approved until a UI has explicitly
    marked its proposal visible.  The returned ``ApprovalGrant.grant_id`` is the
    one-use nonce; the broker stores only a keyed digest of that nonce.
    """

    def __init__(
        self,
        adapter_capabilities: Optional[
            Mapping[str, Iterable[Union[Capability, str]]]
        ] = None,
        *,
        clock: Optional[Callable[[], float]] = None,
        token_factory: Optional[Callable[[], str]] = None,
        audit_capacity: int = 256,
        max_active_records: int = 512,
        max_ttl_seconds: float = 300.0,
    ) -> None:
        if not isinstance(audit_capacity, int) or not 1 <= audit_capacity <= 4096:
            raise ValueError("audit_capacity must be between 1 and 4096")
        if (
            not isinstance(max_active_records, int)
            or not 1 <= max_active_records <= 4096
        ):
            raise ValueError("max_active_records must be between 1 and 4096")
        if not math.isfinite(max_ttl_seconds) or max_ttl_seconds <= 0:
            raise ValueError("max_ttl_seconds must be finite and positive")

        self._clock = clock or time.time
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._audit: Deque[PermissionAuditEvent] = deque(maxlen=audit_capacity)
        self._max_active_records = max_active_records
        self._max_ttl_seconds = float(max_ttl_seconds)
        self._lock = threading.RLock()
        self._sequence = 0
        self._last_clock_value = 0.0
        self._instance_id = self._fresh_token()
        self._nonce_key = secrets.token_bytes(32)
        self._adapters: Dict[str, FrozenSet[Capability]] = {}
        self._requests: Dict[str, _RequestRecord] = {}
        self._grants: Dict[str, _GrantRecord] = {}
        self._tombstone_order: Deque[str] = deque()
        self._tombstones = set()  # type: ignore[var-annotated]
        self._tombstone_limit = max_active_records * 2

        for adapter, capabilities in (adapter_capabilities or {}).items():
            self.declare_adapter(adapter, capabilities)

    def declare_adapter(
        self, adapter: str, capabilities: Iterable[Union[Capability, str]]
    ) -> FrozenSet[Capability]:
        """Declare the closed capabilities an adapter may request.

        Changing a declaration invalidates that adapter's outstanding requests
        and grants.  This prevents a stale grant crossing a capability change.
        """

        adapter = _validate_identifier(adapter)
        try:
            bounded = tuple(islice(iter(capabilities), len(Capability) + 1))
            if len(bounded) > len(Capability):
                raise PermissionDenied("invalid-capability-declaration")
            declared = frozenset(_coerce_capability(value) for value in bounded)
        except PermissionDenied:
            raise
        except TypeError:
            raise PermissionDenied("invalid-capability-declaration")
        except Exception:
            raise PermissionDenied("invalid-capability-declaration")
        if not declared:
            raise PermissionDenied("empty-capability-declaration")

        with self._lock:
            previous = self._adapters.get(adapter)
            if previous is not None and previous != declared:
                self._invalidate_adapter_locked(adapter)
                self._record_locked(
                    "adapter-declared", "invalidated", "capabilities-changed", None
                )
            elif previous is None:
                self._record_locked("adapter-declared", "accepted", "registered", None)
            self._adapters[adapter] = declared
        return declared

    def create_request(self, proposal: ActionProposal) -> ApprovalRequest:
        """Register a proposal and return the exact record the UI must show."""

        if type(proposal) is not ActionProposal:
            raise TypeError("proposal must be an ActionProposal")

        with self._lock:
            now = self._now()
            capability = _coerce_capability(proposal.capability)
            self._assert_proposal_valid_locked(proposal, capability, now)
            self._prune_expired_locked(now)
            if len(self._requests) >= self._max_active_records:
                self._record_locked(
                    "request-created",
                    "denied",
                    "capacity-exceeded",
                    capability,
                    proposal.proposal_id,
                )
                raise PermissionDenied("capacity-exceeded")

            request_id = self._unique_request_id_locked()
            request = ApprovalRequest.from_proposal(proposal, request_id=request_id)
            self._requests[request_id] = _RequestRecord(request=request)
            self._record_locked(
                "request-created", "accepted", "proposal-bound", capability, request_id
            )
            return request

    # A readable synonym for callers which model this step as proposing.
    propose = create_request

    def mark_visible(self, request_id: str) -> ApprovalRequest:
        """Record that the complete approval proposal is visible to the user."""

        request_id = _validate_identifier(request_id)
        with self._lock:
            now = self._now()
            record = self._request_locked(request_id)
            self._assert_request_fresh_locked(record, now)
            if record.visible_at is None:
                record.visible_at = now
                self._record_locked(
                    "proposal-visible",
                    "accepted",
                    "presented",
                    record.request.capability,
                    request_id,
                )
            return record.request

    # UI code can use the more natural verb without maintaining another path.
    present = mark_visible

    def approve(
        self,
        request_id: str,
        *,
        method: Union[ApprovalMethod, str] = ApprovalMethod.VISIBLE,
    ) -> ApprovalGrant:
        """Approve a visible request and return its one-use volatile grant."""

        request_id = _validate_identifier(request_id)
        try:
            approval_method = (
                method if isinstance(method, ApprovalMethod) else ApprovalMethod(method)
            )
        except (TypeError, ValueError):
            raise PermissionDenied("unknown-approval-method")

        with self._lock:
            now = self._now()
            record = self._request_locked(request_id)
            self._assert_request_fresh_locked(record, now)
            capability = _coerce_capability(record.request.capability)
            if record.visible_at is None:
                self._record_locked(
                    "approval", "denied", "proposal-not-visible", capability, request_id
                )
                raise PermissionDenied("proposal-not-visible")
            if record.grant_key is not None:
                self._record_locked(
                    "approval", "denied", "already-approved", capability, request_id
                )
                raise PermissionDenied("already-approved")
            if (
                approval_method is ApprovalMethod.SPOKEN
                and capability in _SPOKEN_DENIED_CAPABILITIES
            ):
                self._drop_request_locked(request_id, tombstone_grant=False)
                self._record_locked(
                    "approval",
                    "denied",
                    "spoken-approval-forbidden",
                    capability,
                    request_id,
                )
                raise PermissionDenied("spoken-approval-forbidden")

            grant_id = self._unique_grant_id_locked()
            request = record.request
            grant = ApprovalGrant(
                grant_id=grant_id,
                request_id=request.request_id,
                proposal_id=request.proposal_id,
                voice_session_id=request.voice_session_id,
                adapter=request.adapter,
                agent_session_id=request.agent_session_id,
                capability=request.capability,
                destination=request.destination,
                operation_digest=request.operation_digest,
                context_fingerprint=request.context_fingerprint,
                granted_at=now,
                expires_at=request.expires_at,
                method=approval_method,
            )
            grant_key = self._grant_key(grant_id)
            # Keep only the keyed nonce digest plus proposal bindings.  The raw
            # nonce exists solely in the grant returned to the approving client.
            self._grants[grant_key] = _GrantRecord(
                request_id=request_id,
                proposal_id=request.proposal_id,
                voice_session_id=request.voice_session_id,
                adapter=request.adapter,
                agent_session_id=request.agent_session_id,
                capability=capability,
                destination=request.destination,
                operation_digest=request.operation_digest,
                context_fingerprint=request.context_fingerprint,
                granted_at=now,
                expires_at=request.expires_at,
                method=approval_method,
            )
            record.grant_key = grant_key
            self._record_locked(
                "approval", "granted", approval_method.value, capability, request_id
            )
            return grant

    def deny(self, request_id: str) -> None:
        """Terminally deny a request; any grant already issued is invalidated."""

        request_id = _validate_identifier(request_id)
        with self._lock:
            record = self._request_locked(request_id)
            capability = _coerce_capability(record.request.capability)
            self._drop_request_locked(request_id, tombstone_grant=True)
            self._record_locked(
                "approval", "denied", "user-denied", capability, request_id
            )

    def consume(
        self,
        grant: ApprovalGrant,
        *,
        voice_session_id: str,
        adapter: str,
        agent_session_id: str,
        capability: Union[Capability, str],
        destination: str,
        operation: object,
        context_fingerprint: str,
        prior_dispatch_ambiguous: bool = False,
    ) -> bool:
        """Atomically consume a grant for one exact adapter dispatch.

        A known grant is burned on expiry, ambiguity, declaration changes, or
        any binding mismatch.  Consequently a failed authorization attempt can
        never be corrected by reusing the same authority.
        """

        if type(grant) is not ApprovalGrant:
            raise TypeError("grant must be an ApprovalGrant")
        if not isinstance(prior_dispatch_ambiguous, bool):
            raise PermissionDenied("invalid-ambiguity-state")

        with self._lock:
            now = self._now()
            grant_key = self._grant_key(grant.grant_id)
            if grant_key in self._tombstones:
                self._record_locked(
                    "grant-consumed",
                    "denied",
                    "replay",
                    grant.capability,
                    grant.request_id,
                )
                raise PermissionDenied("replay")
            stored = self._grants.get(grant_key)
            if stored is None:
                reason = (
                    "broker-restarted"
                    if not grant.grant_id.startswith(self._instance_id + ".")
                    else "unknown-grant"
                )
                self._record_locked(
                    "grant-consumed",
                    "denied",
                    reason,
                    grant.capability,
                    grant.request_id,
                )
                raise PermissionDenied(reason)

            expected = stored
            request_id = stored.request_id
            expected_capability = _coerce_capability(expected.capability)

            if prior_dispatch_ambiguous:
                self._burn_grant_locked(grant_key)
                self._record_locked(
                    "grant-consumed",
                    "denied",
                    "prior-dispatch-ambiguous",
                    expected_capability,
                    request_id,
                )
                raise PermissionDenied("prior-dispatch-ambiguous")

            if now < expected.granted_at:
                self._burn_grant_locked(grant_key)
                self._record_locked(
                    "grant-consumed",
                    "denied",
                    "not-yet-valid",
                    expected_capability,
                    request_id,
                )
                raise PermissionDenied("not-yet-valid")

            if not math.isfinite(expected.expires_at) or now >= expected.expires_at:
                self._burn_grant_locked(grant_key)
                self._record_locked(
                    "grant-consumed",
                    "denied",
                    "expired",
                    expected_capability,
                    request_id,
                )
                raise PermissionDenied("expired")

            try:
                actual_capability = _coerce_capability(capability)
                actual_voice_session = _validate_identifier(voice_session_id)
                actual_adapter = _validate_identifier(adapter)
                actual_agent_session = _validate_identifier(agent_session_id)
                actual_destination = _validate_identifier(destination)
                actual_fingerprint = _validate_identifier(context_fingerprint)
                actual_digest = canonical_operation_digest(operation)
            except (PermissionDenied, TypeError, ValueError, OverflowError):
                self._burn_grant_locked(grant_key)
                self._record_locked(
                    "grant-consumed",
                    "denied",
                    "binding-mismatch",
                    expected_capability,
                    request_id,
                )
                raise PermissionDenied("binding-mismatch")

            declared = self._adapters.get(expected.adapter)
            if declared is None or expected_capability not in declared:
                self._burn_grant_locked(grant_key)
                self._record_locked(
                    "grant-consumed",
                    "denied",
                    "undeclared-capability",
                    expected_capability,
                    request_id,
                )
                raise PermissionDenied("undeclared-capability")

            matches = (
                grant.request_id == expected.request_id
                and grant.proposal_id == expected.proposal_id
                and grant.capability == expected_capability
                and _constant_string_equal(
                    grant.voice_session_id, expected.voice_session_id
                )
                and _constant_string_equal(grant.adapter, expected.adapter)
                and _constant_string_equal(
                    grant.agent_session_id, expected.agent_session_id
                )
                and _constant_string_equal(grant.destination, expected.destination)
                and _constant_string_equal(
                    grant.operation_digest, expected.operation_digest
                )
                and _constant_string_equal(
                    grant.context_fingerprint, expected.context_fingerprint
                )
                and grant.granted_at == expected.granted_at
                and grant.expires_at == expected.expires_at
                and grant.method == expected.method
                and actual_capability == expected_capability
                and _constant_string_equal(
                    actual_voice_session, expected.voice_session_id
                )
                and _constant_string_equal(actual_adapter, expected.adapter)
                and _constant_string_equal(
                    actual_agent_session, expected.agent_session_id
                )
                and _constant_string_equal(actual_destination, expected.destination)
                and _constant_string_equal(
                    actual_fingerprint, expected.context_fingerprint
                )
                and _constant_string_equal(actual_digest, expected.operation_digest)
            )
            if not matches:
                self._burn_grant_locked(grant_key)
                self._record_locked(
                    "grant-consumed",
                    "denied",
                    "binding-mismatch",
                    expected_capability,
                    request_id,
                )
                raise PermissionDenied("binding-mismatch")

            # This state transition happens while holding the broker lock and
            # before authority is returned to the adapter caller.
            self._burn_grant_locked(grant_key)
            self._record_locked(
                "grant-consumed",
                "authorized",
                "exact-once",
                expected_capability,
                request_id,
            )
            return True

    def restart(self) -> None:
        """Invalidate all volatile approvals, as a daemon restart must do."""

        with self._lock:
            self._requests.clear()
            self._grants.clear()
            self._tombstone_order.clear()
            self._tombstones.clear()
            self._instance_id = self._fresh_token()
            self._nonce_key = secrets.token_bytes(32)
            self._record_locked(
                "broker-restarted", "invalidated", "volatile-state-cleared", None
            )

    def audit_events(self) -> Tuple[PermissionAuditEvent, ...]:
        """Return a stable snapshot of the bounded metadata-only audit."""

        with self._lock:
            return tuple(self._audit)

    def _assert_proposal_valid_locked(
        self, proposal: ActionProposal, capability: Capability, now: float
    ) -> None:
        adapter = _validate_identifier(proposal.adapter)
        _validate_identifier(proposal.proposal_id)
        _validate_identifier(proposal.voice_session_id)
        _validate_identifier(proposal.agent_session_id)
        _validate_identifier(proposal.destination)
        _validate_identifier(proposal.context_fingerprint)

        declared = self._adapters.get(adapter)
        if declared is None or capability not in declared:
            self._record_locked(
                "request-created",
                "denied",
                "undeclared-capability",
                capability,
                proposal.proposal_id,
            )
            raise PermissionDenied("undeclared-capability")
        if (
            not math.isfinite(proposal.created_at)
            or not math.isfinite(proposal.expires_at)
            or proposal.created_at > now
            or proposal.expires_at <= now
        ):
            reason = "not-yet-valid" if proposal.created_at > now else "expired"
            self._record_locked(
                "request-created", "denied", reason, capability, proposal.proposal_id
            )
            raise PermissionDenied(reason)
        if proposal.expires_at - now > self._max_ttl_seconds:
            self._record_locked(
                "request-created",
                "denied",
                "expiry-too-distant",
                capability,
                proposal.proposal_id,
            )
            raise PermissionDenied("expiry-too-distant")
        try:
            digest = canonical_operation_digest(proposal.operation)
        except (TypeError, ValueError, OverflowError):
            raise PermissionDenied("invalid-operation")
        if not _constant_string_equal(digest, proposal.operation_digest):
            self._record_locked(
                "request-created",
                "denied",
                "operation-digest-mismatch",
                capability,
                proposal.proposal_id,
            )
            raise PermissionDenied("operation-digest-mismatch")

    def _assert_request_fresh_locked(self, record: _RequestRecord, now: float) -> None:
        if now < record.request.created_at:
            request_id = record.request.request_id
            capability = _coerce_capability(record.request.capability)
            self._drop_request_locked(request_id, tombstone_grant=True)
            self._record_locked(
                "approval", "denied", "not-yet-valid", capability, request_id
            )
            raise PermissionDenied("not-yet-valid")
        if (
            not math.isfinite(record.request.expires_at)
            or now >= record.request.expires_at
        ):
            request_id = record.request.request_id
            capability = _coerce_capability(record.request.capability)
            self._drop_request_locked(request_id, tombstone_grant=True)
            self._record_locked("approval", "denied", "expired", capability, request_id)
            raise PermissionDenied("expired")

    def _request_locked(self, request_id: str) -> _RequestRecord:
        record = self._requests.get(request_id)
        if record is None:
            self._record_locked(
                "approval", "denied", "unknown-request", None, request_id
            )
            raise PermissionDenied("unknown-request")
        return record

    def _drop_request_locked(self, request_id: str, *, tombstone_grant: bool) -> None:
        record = self._requests.pop(request_id, None)
        if record is None or record.grant_key is None:
            return
        self._grants.pop(record.grant_key, None)
        if tombstone_grant:
            self._add_tombstone_locked(record.grant_key)

    def _burn_grant_locked(self, grant_key: str) -> None:
        stored = self._grants.pop(grant_key, None)
        if stored is not None:
            self._requests.pop(stored.request_id, None)
        self._add_tombstone_locked(grant_key)

    def _add_tombstone_locked(self, grant_key: str) -> None:
        if grant_key in self._tombstones:
            return
        while len(self._tombstone_order) >= self._tombstone_limit:
            oldest = self._tombstone_order.popleft()
            self._tombstones.discard(oldest)
        self._tombstone_order.append(grant_key)
        self._tombstones.add(grant_key)

    def _invalidate_adapter_locked(self, adapter: str) -> None:
        doomed = [
            request_id
            for request_id, record in self._requests.items()
            if record.request.adapter == adapter
        ]
        for request_id in doomed:
            self._drop_request_locked(request_id, tombstone_grant=True)

    def _prune_expired_locked(self, now: float) -> None:
        expired = [
            request_id
            for request_id, record in self._requests.items()
            if not math.isfinite(record.request.expires_at)
            or now >= record.request.expires_at
        ]
        for request_id in expired:
            self._drop_request_locked(request_id, tombstone_grant=True)

    def _unique_request_id_locked(self) -> str:
        for _ in range(16):
            request_id = "req_" + self._fresh_token()
            if request_id not in self._requests:
                return request_id
        raise RuntimeError("secure token source produced repeated request identifiers")

    def _unique_grant_id_locked(self) -> str:
        for _ in range(16):
            grant_id = self._instance_id + "." + self._fresh_token()
            grant_key = self._grant_key(grant_id)
            if grant_key not in self._grants and grant_key not in self._tombstones:
                return grant_id
        raise RuntimeError("secure token source produced repeated grant identifiers")

    def _fresh_token(self) -> str:
        token = self._token_factory()
        if not isinstance(token, str) or not token or len(token) > 256:
            raise RuntimeError("token_factory returned an invalid token")
        return token

    def _grant_key(self, grant_id: str) -> str:
        if not isinstance(grant_id, str):
            return "invalid"
        return hmac.new(
            self._nonce_key, grant_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _now(self) -> float:
        now = float(self._clock())
        if not math.isfinite(now):
            raise RuntimeError("clock returned a non-finite timestamp")
        self._last_clock_value = now
        return now

    def _record_locked(
        self,
        event: str,
        decision: str,
        reason: str,
        capability: Optional[Union[Capability, str]],
        request_id: Optional[str] = None,
    ) -> None:
        self._sequence += 1
        if isinstance(capability, Capability):
            capability_value = capability.value
        elif isinstance(capability, str) and capability in {
            item.value for item in Capability
        }:
            capability_value = capability
        else:
            capability_value = "none"
        try:
            occurred_at = self._now()
        except BaseException:
            # Auditing is metadata-only and must not turn a completed state
            # mutation into a hidden grant/request or leak collaborator error
            # text.  Reuse the last validated logical time when a secondary
            # audit timestamp call fails.
            occurred_at = self._last_clock_value
        self._audit.append(
            PermissionAuditEvent(
                sequence=self._sequence,
                occurred_at=occurred_at,
                event=event,
                decision=decision,
                reason=reason,
                capability=capability_value,
                request_ref=_safe_ref(request_id) if request_id else "none",
            )
        )


__all__ = ["PermissionAuditEvent", "PermissionBroker", "PermissionDenied"]
