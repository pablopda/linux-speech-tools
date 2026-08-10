"""Explicit, bounded context collection for voice sessions.

The broker is deliberately policy-heavy and I/O-free.  Providers own any
platform access and receive only the context kinds named by the request.  The
broker validates their output, applies freshness and payload limits, and makes
sharing a caller decision rather than a provider decision.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import OrderedDict
from enum import Enum
from itertools import islice
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
)

from src.voice.models import (
    Capability,
    ContextItem,
    ContextRequest,
    ContextSnapshot,
    DEFAULT_CONTEXT_TTL_SECONDS,
    TargetBinding,
)


DEFAULT_MAX_ITEM_BYTES = 16 * 1024
DEFAULT_MAX_PROVIDER_BYTES = 32 * 1024
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024
DEFAULT_MAX_ITEMS = 32
DEFAULT_MAX_AGE_SECONDS = 60.0
DEFAULT_MAX_STATUS_RECORDS = 128
ABSOLUTE_MAX_ITEM_BYTES = 256 * 1024
ABSOLUTE_MAX_TOTAL_BYTES = 1024 * 1024
ABSOLUTE_MAX_ITEMS = 1024
ABSOLUTE_MAX_SNAPSHOT_AGE_SECONDS = 3600.0
MAX_METADATA_ENTRIES = 64
MAX_METADATA_KEY_BYTES = 128
MAX_METADATA_VALUE_BYTES = 2048

_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class ContextBrokerError(RuntimeError):
    """Base class for fail-closed context errors."""


class ContextRegistrationError(ContextBrokerError):
    """A provider registration is invalid or ambiguous."""


class RequiredContextUnavailable(ContextBrokerError):
    """A required context kind could not be captured safely."""

    def __init__(self, kind: str, provider: str, reason: str) -> None:
        self.kind = _safe_token(kind, "context kind")
        self.provider = _safe_token(provider, "provider")
        self.reason = _safe_token(reason, "reason")
        super().__init__(
            "required context unavailable: {} ({}/{})".format(
                self.kind, self.provider, self.reason
            )
        )


class ContextExpired(ContextBrokerError):
    """The snapshot is no longer authorized for adapter delivery."""


class ContextProvider(Protocol):
    """I/O boundary implemented by context integrations.

    Providers must return a finite ``Sequence``.  They receive only their
    explicitly requested subset and may use ``target`` for collection, but
    target authorization metadata never crosses the adapter boundary.
    """

    def capture(
        self,
        requested_kinds: FrozenSet[str],
        *,
        target: Any,
        now: float,
        deadline: float,
    ) -> Sequence[ContextItem]: ...


class _Registration:
    __slots__ = (
        "name",
        "provider",
        "kinds",
        "max_items",
        "max_item_bytes",
        "max_provider_bytes",
        "max_age_seconds",
    )

    def __init__(
        self,
        *,
        name: str,
        provider: ContextProvider,
        kinds: FrozenSet[str],
        max_items: int,
        max_item_bytes: int,
        max_provider_bytes: int,
        max_age_seconds: float,
    ) -> None:
        self.name = name
        self.provider = provider
        self.kinds = kinds
        self.max_items = max_items
        self.max_item_bytes = max_item_bytes
        self.max_provider_bytes = max_provider_bytes
        self.max_age_seconds = max_age_seconds


class _CaptureRecord:
    __slots__ = ("requested", "omissions", "created_at")

    def __init__(
        self,
        requested: Tuple[Mapping[str, object], ...],
        omissions: Tuple[Mapping[str, str], ...],
        created_at: float,
    ) -> None:
        # These mappings contain only validated tokens, booleans, counts, and
        # timestamps.  The broker never retains content or target details.
        self.requested = requested
        self.omissions = omissions
        self.created_at = created_at


class ContextBroker:
    """Collect context only from explicitly registered providers.

    Each kind has exactly one registered provider.  This intentionally avoids
    fallback after a failure: trying a broader or different provider would
    widen access beyond the request that the user reviewed.
    """

    def __init__(
        self,
        *,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_status_records: int = DEFAULT_MAX_STATUS_RECORDS,
        max_snapshot_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        clock: Any = time.time,
    ) -> None:
        self.max_total_bytes = _positive_int(
            max_total_bytes, "max_total_bytes", ABSOLUTE_MAX_TOTAL_BYTES
        )
        self.max_status_records = _positive_int(
            max_status_records, "max_status_records", 4096
        )
        self.max_snapshot_age_seconds = _positive_float(
            max_snapshot_age_seconds, "max_snapshot_age_seconds"
        )
        if self.max_snapshot_age_seconds > ABSOLUTE_MAX_SNAPSHOT_AGE_SECONDS:
            raise ValueError("max_snapshot_age_seconds is outside its safe range")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._clock = clock
        self._by_kind: Dict[str, _Registration] = {}
        self._by_name: Dict[str, _Registration] = {}
        self._records: "OrderedDict[str, _CaptureRecord]" = OrderedDict()

    def register(
        self,
        name: str,
        provider: ContextProvider,
        *,
        kinds: Iterable[object],
        max_items: int = DEFAULT_MAX_ITEMS,
        max_item_bytes: int = DEFAULT_MAX_ITEM_BYTES,
        max_provider_bytes: int = DEFAULT_MAX_PROVIDER_BYTES,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        """Register one provider for a non-empty, exclusive set of kinds."""
        provider_name = _safe_token(name, "provider")
        normalized_kinds = _token_set(kinds, "context kind")
        if not normalized_kinds:
            raise ContextRegistrationError("provider kinds cannot be empty")
        if provider_name in self._by_name:
            raise ContextRegistrationError("provider is already registered")
        duplicate = normalized_kinds.intersection(self._by_kind)
        if duplicate:
            raise ContextRegistrationError(
                "context kind is already registered: {}".format(sorted(duplicate)[0])
            )
        try:
            capture = getattr(provider, "capture", None)
        except Exception:
            raise ContextRegistrationError("provider capture interface is unavailable")
        if not callable(capture):
            raise ContextRegistrationError("provider must implement capture")

        item_limit = _positive_int(max_items, "max_items", ABSOLUTE_MAX_ITEMS)
        per_item_limit = _positive_int(
            max_item_bytes, "max_item_bytes", ABSOLUTE_MAX_ITEM_BYTES
        )
        provider_limit = _positive_int(
            max_provider_bytes,
            "max_provider_bytes",
            ABSOLUTE_MAX_TOTAL_BYTES,
        )
        if per_item_limit > provider_limit:
            raise ContextRegistrationError(
                "max_item_bytes cannot exceed max_provider_bytes"
            )
        age_limit = _positive_float(max_age_seconds, "max_age_seconds")
        registration = _Registration(
            name=provider_name,
            provider=provider,
            kinds=normalized_kinds,
            max_items=item_limit,
            max_item_bytes=per_item_limit,
            max_provider_bytes=provider_limit,
            max_age_seconds=age_limit,
        )
        self._by_name[provider_name] = registration
        for kind in normalized_kinds:
            self._by_kind[kind] = registration

    def capture(
        self,
        request: ContextRequest,
        *,
        target: Any,
        now: Optional[float] = None,
    ) -> ContextSnapshot:
        """Capture a fresh, bounded snapshot.

        Required and shareable kinds come only from the validated request.
        A provider cannot grant itself adapter access by setting
        ``ContextItem.shareable``.
        """
        if type(request) is not ContextRequest:
            raise TypeError("request must be a ContextRequest")
        captured_at = self._now(now)
        if request.created_at > captured_at:
            raise ContextExpired("context request is not yet valid")
        try:
            target_binding = TargetBinding.from_target(target, captured_at=captured_at)
        except Exception:
            raise ContextBrokerError("invalid target binding")
        if target_binding.captured_at > captured_at:
            raise ContextExpired("target binding is not yet valid")
        if captured_at - target_binding.captured_at >= DEFAULT_CONTEXT_TTL_SECONDS:
            raise ContextExpired("target binding expired")
        requested = _token_set(request.kinds, "context kind")
        required = _token_set(request.required_kinds, "context kind")
        shareable = _token_set(request.shareable_kinds, "context kind")

        deadline = _finite_float(request.deadline, "request deadline")
        if deadline <= captured_at:
            raise ContextExpired("context request expired")
        request_limit = _positive_int(
            request.max_total_bytes,
            "request max_total_bytes",
            ABSOLUTE_MAX_TOTAL_BYTES,
        )
        total_limit = min(self.max_total_bytes, request_limit)
        allowed_providers, selected_by_kind = _provider_selection(request)
        permission_tokens = {
            _token(value, "permission") for value in request.permissions
        }

        omissions: List[Mapping[str, str]] = []
        groups: Dict[str, Tuple[_Registration, Set[str]]] = {}
        for kind in sorted(requested):
            registration = self._by_kind.get(kind)
            chosen_name = selected_by_kind.get(kind)
            if registration is None:
                if kind in required:
                    raise RequiredContextUnavailable(
                        kind, "unavailable", "not-registered"
                    )
                omissions.append(_omission(kind, "unavailable", "not-registered"))
                continue
            if chosen_name is not None and chosen_name != registration.name:
                if kind in required:
                    raise RequiredContextUnavailable(
                        kind, chosen_name, "not-registered"
                    )
                omissions.append(_omission(kind, chosen_name, "not-registered"))
                continue
            if (
                allowed_providers is not None
                and registration.name not in allowed_providers
            ):
                if kind in required:
                    raise RequiredContextUnavailable(
                        kind, registration.name, "not-authorized"
                    )
                omissions.append(_omission(kind, registration.name, "not-authorized"))
                continue
            if registration.name not in groups:
                groups[registration.name] = (registration, set())
            groups[registration.name][1].add(kind)

        # Reading context is itself privileged.  Refuse before provider I/O so
        # a denied request cannot cause a provider to inspect the target.
        if Capability.READ_CONTEXT.value not in permission_tokens:
            for registration, group_kinds in groups.values():
                self._omit_or_fail(
                    group_kinds,
                    required,
                    registration.name,
                    "permission-denied",
                    omissions,
                )
            groups.clear()

        accepted: List[ContextItem] = []
        accepted_sizes: List[int] = []
        total_bytes = 0
        # Required groups run before optional-only groups so optional context
        # cannot consume the total budget needed by a required provider.
        ordered_groups = sorted(
            groups.values(),
            key=lambda value: (
                not bool(value[1].intersection(required)),
                value[0].name,
            ),
        )
        for registration, group_kinds in ordered_groups:
            provider_items, reason = self._capture_provider(
                registration,
                frozenset(group_kinds),
                target=target,
                now=captured_at,
                deadline=deadline,
                shareable=shareable,
                permissions=request.permissions,
            )
            if reason is not None:
                self._omit_or_fail(
                    group_kinds,
                    required,
                    registration.name,
                    reason,
                    omissions,
                )
                continue

            present = {
                _token(item.kind, "context kind") for item, _size in provider_items
            }
            missing = group_kinds.difference(present)
            if missing.intersection(required):
                missing_kind = sorted(missing.intersection(required))[0]
                raise RequiredContextUnavailable(
                    missing_kind, registration.name, "empty"
                )
            for kind in sorted(missing):
                omissions.append(_omission(kind, registration.name, "empty"))

            items_by_kind: Dict[str, List[Tuple[ContextItem, int]]] = {}
            for item, size in provider_items:
                kind = _token(item.kind, "context kind")
                items_by_kind.setdefault(kind, []).append((item, size))
            # A provider may serve required and optional kinds together.  Do
            # not make that group atomic: reserve the remaining budget for
            # each required kind first, then admit optional kinds only when
            # their complete per-kind payload fits.
            ordered_kinds = sorted(
                items_by_kind,
                key=lambda kind: (kind not in required, kind),
            )
            for kind in ordered_kinds:
                kind_items = items_by_kind[kind]
                kind_bytes = sum(size for _item, size in kind_items)
                if total_bytes + kind_bytes > total_limit:
                    if kind in required:
                        raise RequiredContextUnavailable(
                            kind, registration.name, "total-limit"
                        )
                    omissions.append(_omission(kind, registration.name, "total-limit"))
                    continue
                for item, size in kind_items:
                    accepted.append(item)
                    accepted_sizes.append(size)
                total_bytes += kind_bytes

        # Provider order and provider return order must not make approval
        # fingerprints unstable.
        paired = sorted(
            zip(accepted, accepted_sizes),
            key=lambda value: _canonical_item(value[0]),
        )
        accepted = [item for item, _size in paired]
        accepted_sizes = [size for _item, size in paired]

        snapshot = ContextSnapshot.create(
            target=target_binding,
            items=tuple(accepted),
            permissions=request.permissions,
            now=captured_at,
            ttl_seconds=min(
                deadline - captured_at,
                self.max_snapshot_age_seconds,
            ),
        )
        requested_status = self._requested_status(
            requested=requested,
            required=required,
            shareable=shareable,
            accepted=accepted,
            sizes=accepted_sizes,
            omissions=omissions,
        )
        self._remember(
            snapshot.snapshot_id,
            _CaptureRecord(
                requested=tuple(requested_status),
                omissions=tuple(omissions),
                created_at=captured_at,
            ),
        )
        return snapshot

    def adapter_items(
        self, snapshot: ContextSnapshot, *, now: Optional[float] = None
    ) -> Tuple[ContextItem, ...]:
        """Return only fresh items explicitly authorized for sharing."""
        if type(snapshot) is not ContextSnapshot:
            raise TypeError("snapshot must be a ContextSnapshot")
        checked_at = self._now(now)
        if not snapshot.is_fresh(checked_at):
            raise ContextExpired("context snapshot expired")
        shareable = tuple(snapshot.shareable_items())
        for item in shareable:
            if not item.is_fresh(checked_at):
                raise ContextExpired("context item expired")
        return shareable

    def adapter_payload(
        self, snapshot: ContextSnapshot, *, now: Optional[float] = None
    ) -> Tuple[Mapping[str, object], ...]:
        """Build the adapter boundary payload; target metadata is absent."""
        return tuple(
            item.to_payload() for item in self.adapter_items(snapshot, now=now)
        )

    def manifest(
        self, snapshot: ContextSnapshot, *, now: Optional[float] = None
    ) -> Mapping[str, object]:
        """Return privacy-safe, visibly degraded capture status.

        Raw content, metadata values and keys, target identity/title/path, and
        provider exception text are intentionally never serialized here.
        """
        checked_at = self._now(now)
        record = self._records.get(snapshot.snapshot_id)
        requested = tuple(record.requested) if record is not None else ()
        omissions = tuple(record.omissions) if record is not None else ()
        expired = checked_at >= float(snapshot.expires_at)
        if expired:
            state = "expired"
        elif omissions:
            state = "degraded"
        else:
            state = "ready"
        return {
            "schema_version": 1,
            "state": state,
            "created_at": float(snapshot.created_at),
            "expires_at": float(snapshot.expires_at),
            "item_count": len(snapshot.items),
            "requested": [dict(value) for value in requested],
            "omissions": [dict(value) for value in omissions],
        }

    status = manifest

    def _capture_provider(
        self,
        registration: _Registration,
        requested: FrozenSet[str],
        *,
        target: Any,
        now: float,
        deadline: float,
        shareable: FrozenSet[str],
        permissions: FrozenSet[object],
    ) -> Tuple[List[Tuple[ContextItem, int]], Optional[str]]:
        try:
            result = registration.provider.capture(
                requested,
                target=target,
                now=now,
                deadline=deadline,
            )
        except Exception:
            # Provider messages may contain paths, titles, selections or tokens.
            return [], "provider-failed"
        try:
            if isinstance(result, (str, bytes)) or not isinstance(result, Sequence):
                return [], "invalid-result"
            if len(result) > registration.max_items:
                return [], "item-limit"
            # Copy at most one entry beyond the quota.  Iteration is still
            # provider code and a Sequence can report a dishonest length, so
            # both work and retained memory stay bounded here.
            result = tuple(islice(iter(result), registration.max_items + 1))
        except Exception:
            return [], "invalid-result"
        if len(result) > registration.max_items:
            # Defend against unusual Sequence implementations whose reported
            # length changes while being copied.
            return [], "item-limit"

        permission_tokens = {_token(value, "permission") for value in permissions}
        checked: List[Tuple[ContextItem, int]] = []
        provider_bytes = 0
        for item in result:
            if not isinstance(item, ContextItem):
                return [], "invalid-item"
            try:
                kind = _token(item.kind, "context kind")
                provider_name = _token(item.provider, "provider")
            except Exception:
                return [], "invalid-item"
            if kind not in requested or provider_name != registration.name:
                return [], "scope-mismatch"
            try:
                captured_at = _finite_float(item.captured_at, "captured_at")
                expires_at = _finite_float(item.expires_at, "expires_at")
            except Exception:
                return [], "invalid-freshness"
            if captured_at > now or captured_at > expires_at:
                return [], "invalid-freshness"
            if now - captured_at > registration.max_age_seconds or expires_at <= now:
                return [], "stale"
            bounded_expiry = min(
                expires_at,
                deadline,
                captured_at + registration.max_age_seconds,
            )
            if bounded_expiry <= now:
                return [], "stale"
            try:
                capability = _token(item.required_capability, "permission")
            except Exception:
                return [], "invalid-item"
            if (
                capability != Capability.READ_CONTEXT.value
                or capability not in permission_tokens
            ):
                return [], "permission-denied"
            try:
                # Never retain the provider's subclass or call polymorphic
                # payload methods.  Reconstruct the exact closed base model
                # from guarded fields before measuring or sharing it.
                copied = ContextItem(
                    kind=kind,
                    provider=provider_name,
                    sensitivity=item.sensitivity,
                    captured_at=captured_at,
                    expires_at=bounded_expiry,
                    content=item.content,
                    metadata=item.metadata,
                    shareable=kind in shareable,
                    required_capability=capability,
                )
                size = _item_payload_bytes(copied)
            except Exception:
                return [], "invalid-item"
            if size > registration.max_item_bytes:
                return [], "item-limit"
            provider_bytes += size
            if provider_bytes > registration.max_provider_bytes:
                return [], "provider-limit"
            checked.append((copied, size))
        return checked, None

    @staticmethod
    def _omit_or_fail(
        kinds: Iterable[str],
        required: FrozenSet[str],
        provider: str,
        reason: str,
        omissions: List[Mapping[str, str]],
    ) -> None:
        required_failure = sorted(set(kinds).intersection(required))
        if required_failure:
            raise RequiredContextUnavailable(required_failure[0], provider, reason)
        for kind in sorted(kinds):
            omissions.append(_omission(kind, provider, reason))

    def _requested_status(
        self,
        *,
        requested: FrozenSet[str],
        required: FrozenSet[str],
        shareable: FrozenSet[str],
        accepted: Sequence[ContextItem],
        sizes: Sequence[int],
        omissions: Sequence[Mapping[str, str]],
    ) -> List[Mapping[str, object]]:
        count_by_kind: Dict[str, int] = {}
        bytes_by_kind: Dict[str, int] = {}
        expiry_by_kind: Dict[str, float] = {}
        for item, size in zip(accepted, sizes):
            kind = _token(item.kind, "context kind")
            count_by_kind[kind] = count_by_kind.get(kind, 0) + 1
            bytes_by_kind[kind] = bytes_by_kind.get(kind, 0) + size
            expiry_by_kind[kind] = min(
                expiry_by_kind.get(kind, float(item.expires_at)),
                float(item.expires_at),
            )
        omitted = {value["kind"]: value for value in omissions}
        values: List[Mapping[str, object]] = []
        for kind in sorted(requested):
            registration = self._by_kind.get(kind)
            omission = omitted.get(kind)
            entry: Dict[str, object] = {
                "kind": kind,
                "provider": (
                    omission["provider"]
                    if omission is not None
                    else registration.name
                    if registration
                    else "unavailable"
                ),
                "required": kind in required,
                "shareable": kind in shareable,
                "state": "omitted" if omission is not None else "available",
                "item_count": count_by_kind.get(kind, 0),
                "payload_bytes": bytes_by_kind.get(kind, 0),
            }
            if kind in expiry_by_kind:
                entry["expires_at"] = expiry_by_kind[kind]
            values.append(entry)
        return values

    def _remember(self, snapshot_id: str, record: _CaptureRecord) -> None:
        self._records[snapshot_id] = record
        self._records.move_to_end(snapshot_id)
        while len(self._records) > self.max_status_records:
            self._records.popitem(last=False)

    def _now(self, explicit: Optional[float]) -> float:
        value = self._clock() if explicit is None else explicit
        return _finite_float(value, "current time")


def _provider_selection(
    request: ContextRequest,
) -> Tuple[Optional[FrozenSet[str]], Mapping[str, str]]:
    raw = request.provider_names
    if isinstance(raw, Mapping):
        selected: Dict[str, str] = {}
        for kind, provider in raw.items():
            selected[_token(kind, "context kind")] = _safe_token(provider, "provider")
        return None, selected
    values = _token_set(raw, "provider")
    return (values if values else None), {}


def _item_payload_bytes(item: ContextItem) -> int:
    if not isinstance(item.content, str):
        raise TypeError("context content must be text")
    metadata = item.metadata
    if not isinstance(metadata, Mapping) or len(metadata) > MAX_METADATA_ENTRIES:
        raise ValueError("invalid metadata")
    checked_metadata: Dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("context metadata must contain text")
        if len(key.encode("utf-8")) > MAX_METADATA_KEY_BYTES:
            raise ValueError("metadata key too large")
        if len(value.encode("utf-8")) > MAX_METADATA_VALUE_BYTES:
            raise ValueError("metadata value too large")
        checked_metadata[key] = value
    # ``ContextItem.payload_size`` covers the complete adapter payload,
    # including provenance and policy fields.  The checks above additionally
    # impose broker-local metadata limits that are stricter than the domain
    # model's absolute limits.
    json.dumps(
        {"content": item.content, "metadata": checked_metadata},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return item.payload_size


def _canonical_item(item: ContextItem) -> str:
    return json.dumps(
        {
            "captured_at": float(item.captured_at),
            "content": item.content,
            "expires_at": float(item.expires_at),
            "kind": _token(item.kind, "context kind"),
            "metadata": dict(item.metadata),
            "provider": _token(item.provider, "provider"),
            "required_capability": _token(item.required_capability, "permission"),
            "sensitivity": _token(item.sensitivity, "sensitivity"),
            "shareable": bool(item.shareable),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _omission(kind: str, provider: str, reason: str) -> Mapping[str, str]:
    return {
        "kind": _safe_token(kind, "context kind"),
        "provider": _safe_token(provider, "provider"),
        "reason": _safe_token(reason, "reason"),
    }


def _token(value: object, label: str) -> str:
    if isinstance(value, Enum):
        value = value.value
    return _safe_token(value, label)


def _safe_token(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError("{} must be a privacy-safe token".format(label))
    try:
        value = bytes.decode(str.encode(value, "utf-8"), "utf-8")
    except UnicodeEncodeError:
        raise ValueError("{} must be a privacy-safe token".format(label))
    if not _SAFE_TOKEN.fullmatch(value):
        raise ValueError("{} must be a privacy-safe token".format(label))
    return value


def _token_set(
    values: Iterable[object], label: str, *, maximum: int = 64
) -> FrozenSet[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("{} collection cannot be text".format(label))
    try:
        bounded = tuple(islice(iter(values), maximum + 1))
    except Exception:
        raise ValueError("{} collection could not be read".format(label))
    if len(bounded) > maximum:
        raise ValueError("{} collection has too many entries".format(label))
    return frozenset(_token(value, label) for value in bounded)


def _finite_float(value: object, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("{} must be finite".format(label))
    if not math.isfinite(parsed):
        raise ValueError("{} must be finite".format(label))
    return parsed


def _positive_float(value: object, label: str) -> float:
    parsed = _finite_float(value, label)
    if parsed <= 0:
        raise ValueError("{} must be positive".format(label))
    return parsed


def _positive_int(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError("{} must be an integer".format(label))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("{} must be an integer".format(label))
    if parsed <= 0 or parsed > maximum:
        raise ValueError("{} is outside its safe range".format(label))
    return parsed


__all__ = [
    "ContextBroker",
    "ContextBrokerError",
    "ContextExpired",
    "ContextProvider",
    "ContextRegistrationError",
    "RequiredContextUnavailable",
]
