"""Backend-neutral, privacy-conscious domain models for the voice workspace.

The models in this module deliberately contain no platform calls.  They form
the stable boundary between the session router, context providers, permission
broker, and agent adapters.  Rich content is available only through explicit
payload methods; status serializers are metadata-only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import islice
from types import MappingProxyType
from typing import Any, FrozenSet, Mapping, Optional, Tuple, Type, TypeVar, Union


MAX_CONTEXT_ITEM_BYTES = 256 * 1024
MAX_CONTEXT_TOTAL_BYTES = 1024 * 1024
MAX_CONTEXT_ITEMS = 1024
MAX_METADATA_ENTRIES = 64
MAX_METADATA_KEY_BYTES = 128
MAX_METADATA_VALUE_BYTES = 4096
MAX_AGENT_TEXT_BYTES = 1024 * 1024
MAX_OPERATION_BYTES = 256 * 1024
MAX_IDENTIFIER_CHARS = 512
MAX_CONTEXT_PROVIDERS = 64
DEFAULT_CONTEXT_TTL_SECONDS = 60.0

_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_EnumT = TypeVar("_EnumT", bound=Enum)


class _StatusRepr:
    """Keep accidental repr/logging on the metadata-only status boundary."""

    def __repr__(self) -> str:
        try:
            status = dict(self.to_status_dict())  # type: ignore[attr-defined]
        except Exception:
            return "{}(<redacted>)".format(type(self).__name__)
        return "{}(status={!r})".format(type(self).__name__, status)


class VoiceMode(str, Enum):
    DICTATE = "dictate"
    ASK = "ask"
    ACT = "act"
    READ = "read"


class SessionLifecycle(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting-approval"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SessionActivity(str, Enum):
    NONE = "none"
    CAPTURING_CONTEXT = "capturing-context"
    LISTENING = "listening"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    AGENT_RUNNING = "agent-running"
    DELIVERING = "delivering"
    SYNTHESIZING = "synthesizing"
    SPEAKING = "speaking"
    EXECUTING = "executing"


class Outcome(str, Enum):
    COMPLETED = "completed"
    COMPLETED_WITH_FALLBACK = "completed-with-fallback"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ContextKind(str, Enum):
    REPOSITORY = "repository"
    SELECTION = "selection"
    BROWSER_PAGE = "browser-page"
    TERMINAL = "terminal"
    AGENT_SESSION = "agent-session"
    OPEN_FILE = "open-file"
    ATTACHED_FILE = "attached-file"
    DOCUMENT = "document"
    SCREEN_HISTORY = "screen-history"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class Capability(str, Enum):
    READ_CONTEXT = "read-context"
    DRAFT = "draft"
    INSERT = "insert"
    EXECUTE = "execute"
    SEND = "send"
    MODIFY = "modify"
    DELETE = "delete"


class FailureCode(str, Enum):
    NONE = "none"
    INVALID_REQUEST = "invalid-request"
    INVALID_TRANSITION = "invalid-transition"
    STALE_REVISION = "stale-revision"
    UNAVAILABLE = "unavailable"
    BUSY = "busy"
    CONTEXT_UNAVAILABLE = "context-unavailable"
    CONTEXT_EXPIRED = "context-expired"
    CONTEXT_TOO_LARGE = "context-too-large"
    FOCUS_DRIFT = "focus-drift"
    AMBIGUOUS_DISPATCH = "ambiguous-dispatch"
    PROVIDER_FAILURE = "provider-failure"
    TIMEOUT = "timeout"
    APPROVAL_DENIED = "approval-denied"
    APPROVAL_EXPIRED = "approval-expired"
    APPROVAL_REPLAYED = "approval-replayed"
    APPROVAL_MISMATCH = "approval-mismatch"
    CANCELLED = "cancelled"
    CLOSED = "closed"
    INTERNAL = "internal"


class AgentEventKind(str, Enum):
    STARTED = "started"
    PROGRESS = "progress"
    OUTPUT_DELTA = "output-delta"
    ACTION_PROPOSAL = "action-proposal"
    FINAL = "final"
    ERROR = "error"
    CANCELLED = "cancelled"


class ApprovalMethod(str, Enum):
    VISIBLE = "visible"
    SPOKEN = "spoken"


TERMINAL_LIFECYCLES = frozenset(
    (SessionLifecycle.COMPLETED, SessionLifecycle.CANCELLED, SessionLifecycle.FAILED)
)
TERMINAL_AGENT_EVENTS = frozenset(
    (AgentEventKind.FINAL, AgentEventKind.ERROR, AgentEventKind.CANCELLED)
)
HIGH_IMPACT_CAPABILITIES = frozenset(
    (Capability.EXECUTE, Capability.SEND, Capability.MODIFY, Capability.DELETE)
)

_LIFECYCLE_TRANSITIONS = {
    SessionLifecycle.PENDING: frozenset(
        (SessionLifecycle.RUNNING, SessionLifecycle.CANCELLED, SessionLifecycle.FAILED)
    ),
    SessionLifecycle.RUNNING: frozenset(
        (
            SessionLifecycle.AWAITING_APPROVAL,
            SessionLifecycle.PAUSED,
            SessionLifecycle.STOPPING,
            SessionLifecycle.COMPLETED,
            SessionLifecycle.CANCELLED,
            SessionLifecycle.FAILED,
        )
    ),
    SessionLifecycle.AWAITING_APPROVAL: frozenset(
        (
            SessionLifecycle.RUNNING,
            SessionLifecycle.STOPPING,
            SessionLifecycle.CANCELLED,
            SessionLifecycle.FAILED,
        )
    ),
    SessionLifecycle.PAUSED: frozenset(
        (
            SessionLifecycle.RUNNING,
            SessionLifecycle.STOPPING,
            SessionLifecycle.CANCELLED,
            SessionLifecycle.FAILED,
        )
    ),
    SessionLifecycle.STOPPING: frozenset(
        (
            SessionLifecycle.COMPLETED,
            SessionLifecycle.CANCELLED,
            SessionLifecycle.FAILED,
        )
    ),
    SessionLifecycle.COMPLETED: frozenset(),
    SessionLifecycle.CANCELLED: frozenset(),
    SessionLifecycle.FAILED: frozenset(),
}


def _enum(value: Any, enum_type: Type[_EnumT], name: str) -> _EnumT:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise ValueError("unknown {}: {!r}".format(name, value))


def _text(
    value: Any, name: str, *, maximum: int = MAX_IDENTIFIER_CHARS, empty: bool = False
) -> str:
    if not isinstance(value, str):
        raise TypeError("{} must be text".format(name))
    try:
        encoded = str.encode(value, "utf-8")
    except UnicodeEncodeError:
        raise ValueError("{} must be valid UTF-8 text".format(name))
    normalized = bytes.decode(encoded, "utf-8")
    if not empty and not normalized:
        raise ValueError("{} cannot be empty".format(name))
    if len(normalized) > maximum:
        raise ValueError("{} exceeds its limit".format(name))
    return normalized


def _bounded_utf8_text(value: Any, name: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError("{} must be text".format(name))
    try:
        encoded = str.encode(value, "utf-8")
    except UnicodeEncodeError:
        raise ValueError("{} must be valid UTF-8 text".format(name))
    if len(encoded) > maximum:
        raise ValueError("{} exceeds its limit".format(name))
    return bytes.decode(encoded, "utf-8")


def _token(value: Any, name: str) -> str:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str):
        raise ValueError("{} must be a privacy-safe token".format(name))
    try:
        value = bytes.decode(str.encode(value, "utf-8"), "utf-8")
    except UnicodeEncodeError:
        raise ValueError("{} must be a privacy-safe token".format(name))
    if not _SAFE_TOKEN.fullmatch(value):
        raise ValueError("{} must be a privacy-safe token".format(name))
    return value


def _timestamp(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("{} must be a timestamp".format(name))
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("{} must be finite and non-negative".format(name))
    return result


def _positive_int(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("{} must be an integer".format(name))
    if value <= 0 or value > maximum:
        raise ValueError("{} is outside its allowed range".format(name))
    return int(value)


def _freeze_metadata(value: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("metadata must be a mapping")
    try:
        entries = tuple(islice(iter(value.items()), MAX_METADATA_ENTRIES + 1))
    except Exception:
        raise TypeError("metadata entries must be readable")
    if len(entries) > MAX_METADATA_ENTRIES:
        raise ValueError("metadata has too many entries")
    copied = {}
    for key, item in entries:
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError("metadata keys and values must be text")
        try:
            key_bytes = str.encode(key, "utf-8")
            item_bytes = str.encode(item, "utf-8")
        except UnicodeEncodeError:
            raise ValueError("metadata must be valid UTF-8 text")
        if len(key_bytes) > MAX_METADATA_KEY_BYTES:
            raise ValueError("metadata key exceeds its limit")
        if len(item_bytes) > MAX_METADATA_VALUE_BYTES:
            raise ValueError("metadata value exceeds its limit")
        copied[bytes.decode(key_bytes, "utf-8")] = bytes.decode(item_bytes, "utf-8")
    return MappingProxyType(copied)


def _canonical_bytes(value: Any, *, maximum: int) -> bytes:
    try:
        encoded = json.dumps(
            _json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError):
        raise ValueError("value is not canonical JSON")
    if len(encoded) > maximum:
        raise ValueError("canonical payload exceeds its limit")
    return encoded


def _json_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded-shape JSON value without invoking arbitrary encoders."""
    if depth > 32:
        raise ValueError("canonical payload is too deeply nested")
    if isinstance(value, str):
        try:
            encoded = str.encode(value, "utf-8")
        except UnicodeEncodeError:
            raise ValueError("canonical text must be valid UTF-8")
        return bytes.decode(encoded, "utf-8")
    if value is None:
        return value
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical numbers must be finite")
        return float(value)
    if isinstance(value, Enum):
        return _json_value(value.value, depth=depth + 1)
    if isinstance(value, Mapping):
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("canonical mapping keys must be text")
            try:
                normalized_key = bytes.decode(str.encode(key, "utf-8"), "utf-8")
            except UnicodeEncodeError:
                raise ValueError("canonical keys must be valid UTF-8")
            normalized[normalized_key] = _json_value(item, depth=depth + 1)
        return normalized
    if isinstance(value, (tuple, list)):
        return [_json_value(item, depth=depth + 1) for item in value]
    raise ValueError("value is not canonical JSON")


def _freeze_json(value: Any, *, depth: int = 0) -> Any:
    normalized = _json_value(value, depth=depth)
    if isinstance(normalized, dict):
        return MappingProxyType(
            {
                key: _freeze_json(item, depth=depth + 1)
                for key, item in normalized.items()
            }
        )
    if isinstance(normalized, list):
        return tuple(_freeze_json(item, depth=depth + 1) for item in normalized)
    return normalized


def canonical_operation_digest(operation: Any) -> str:
    """Return the stable digest used to bind an approval to an operation."""
    return hashlib.sha256(
        b"linux-speech-tools:operation:v1\0"
        + _canonical_bytes(operation, maximum=MAX_OPERATION_BYTES)
    ).hexdigest()


@dataclass(frozen=True, repr=False)
class TargetBinding:
    """Private stable target identity retained only for local authorization."""

    kind: str = "unknown"
    source: str = "unknown"
    stable_id: str = ""
    captured_at: float = 0.0
    generation: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _token(self.kind, "target kind"))
        object.__setattr__(self, "source", _token(self.source, "target source"))
        object.__setattr__(
            self, "stable_id", _text(self.stable_id, "target stable id", empty=True)
        )
        object.__setattr__(
            self, "captured_at", _timestamp(self.captured_at, "target captured_at")
        )
        if self.generation is not None:
            if isinstance(self.generation, bool) or not isinstance(
                self.generation, int
            ):
                raise TypeError("target generation must be an integer")
            if self.generation < 0:
                raise ValueError("target generation cannot be negative")
            object.__setattr__(self, "generation", int(self.generation))

    @classmethod
    def from_target(cls, target: Any, *, captured_at: float) -> "TargetBinding":
        if target is None:
            return cls(captured_at=captured_at)
        if type(target) is cls:
            return target
        if isinstance(target, Mapping):
            getter = target.get
        else:

            def getter(name: str, default: Any = None) -> Any:
                return getattr(target, name, default)

        stable_id = getter("stable_id", None) or getter("window_id", "") or ""
        generation = getter("focus_generation", None)
        return cls(
            kind=str(getter("kind", "unknown") or "unknown"),
            source=str(getter("source", "unknown") or "unknown"),
            stable_id=str(stable_id),
            captured_at=captured_at,
            generation=generation,
        )

    def _canonical(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "stable_id": self.stable_id,
            "captured_at": self.captured_at,
            "generation": self.generation,
        }

    def __repr__(self) -> str:
        return (
            "TargetBinding(kind={!r}, source={!r}, has_stable_id={!r}, generation={!r})"
        ).format(self.kind, self.source, bool(self.stable_id), self.generation)


@dataclass(frozen=True, repr=False)
class ContextItem(_StatusRepr):
    kind: ContextKind
    provider: str
    sensitivity: Sensitivity
    captured_at: float
    expires_at: float
    content: str
    metadata: Mapping[str, str] = field(default_factory=dict)
    shareable: bool = False
    required_capability: Capability = Capability.READ_CONTEXT

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(self.kind, ContextKind, "context kind"))
        object.__setattr__(
            self, "sensitivity", _enum(self.sensitivity, Sensitivity, "sensitivity")
        )
        object.__setattr__(
            self,
            "required_capability",
            _enum(self.required_capability, Capability, "capability"),
        )
        object.__setattr__(self, "provider", _token(self.provider, "provider"))
        captured = _timestamp(self.captured_at, "captured_at")
        expires = _timestamp(self.expires_at, "expires_at")
        if expires <= captured:
            raise ValueError("context expiry must follow capture")
        object.__setattr__(self, "captured_at", captured)
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(
            self,
            "content",
            _bounded_utf8_text(
                self.content, "context content", maximum=MAX_CONTEXT_ITEM_BYTES
            ),
        )
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))
        if not isinstance(self.shareable, bool):
            raise TypeError("shareable must be boolean")
        if self.payload_size > MAX_CONTEXT_ITEM_BYTES:
            raise ValueError("context payload exceeds its limit")

    @property
    def payload_size(self) -> int:
        return len(
            _canonical_bytes(self._canonical(), maximum=MAX_CONTEXT_ITEM_BYTES + 8192)
        )

    def is_fresh(self, at: Optional[float] = None) -> bool:
        checked = time.time() if at is None else _timestamp(at, "freshness time")
        return self.captured_at <= checked < self.expires_at

    def assert_fresh(self, at: Optional[float] = None) -> None:
        if not self.is_fresh(at):
            raise ValueError("context item is not fresh")

    def _canonical(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind.value,
            "provider": self.provider,
            "sensitivity": self.sensitivity.value,
            "captured_at": self.captured_at,
            "expires_at": self.expires_at,
            "content": self.content,
            "metadata": dict(self.metadata),
            "shareable": self.shareable,
            "required_capability": self.required_capability.value,
        }

    def to_payload(self) -> Mapping[str, Any]:
        """Serialize rich context for an already-authorized adapter boundary."""
        if not self.shareable:
            raise ValueError("context item is not shareable")
        return self._canonical()

    def to_status_dict(self, *, at: Optional[float] = None) -> Mapping[str, Any]:
        checked = time.time() if at is None else _timestamp(at, "status time")
        return {
            "kind": self.kind.value,
            "provider": self.provider,
            "sensitivity": self.sensitivity.value,
            "shareable": self.shareable,
            "payload_bytes": self.payload_size,
            "fresh": self.is_fresh(checked),
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, repr=False)
class ContextRequest:
    request_id: str
    kinds: FrozenSet[ContextKind]
    permissions: FrozenSet[Capability]
    provider_names: Union[FrozenSet[str], Mapping[str, str]] = field(
        default_factory=frozenset
    )
    created_at: float = 0.0
    deadline: float = 0.0
    max_total_bytes: int = MAX_CONTEXT_TOTAL_BYTES
    required_kinds: FrozenSet[ContextKind] = field(default_factory=frozenset)
    shareable_kinds: FrozenSet[ContextKind] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        kinds = _enum_set(self.kinds, ContextKind, "context kind")
        permissions = _enum_set(self.permissions, Capability, "capability")
        required = _enum_set(self.required_kinds, ContextKind, "context kind")
        shareable = _enum_set(self.shareable_kinds, ContextKind, "context kind")
        if not required.issubset(kinds) or not shareable.issubset(kinds):
            raise ValueError("required/shareable context kinds must be requested")
        object.__setattr__(self, "request_id", _text(self.request_id, "request id"))
        object.__setattr__(self, "kinds", kinds)
        object.__setattr__(self, "permissions", permissions)
        object.__setattr__(self, "required_kinds", required)
        object.__setattr__(self, "shareable_kinds", shareable)
        if isinstance(self.provider_names, Mapping):
            selected = {}
            try:
                selections = tuple(
                    islice(
                        iter(self.provider_names.items()),
                        MAX_CONTEXT_PROVIDERS + 1,
                    )
                )
            except Exception:
                raise TypeError("provider_names mapping could not be read")
            if len(selections) > MAX_CONTEXT_PROVIDERS:
                raise ValueError("provider_names has too many entries")
            for kind, provider in selections:
                normalized_kind = _enum(kind, ContextKind, "context kind")
                selected[normalized_kind.value] = _text(
                    provider, "provider", maximum=128
                )
            object.__setattr__(self, "provider_names", MappingProxyType(selected))
        else:
            try:
                selected_providers = tuple(
                    islice(iter(self.provider_names), MAX_CONTEXT_PROVIDERS + 1)
                )
                if len(selected_providers) > MAX_CONTEXT_PROVIDERS:
                    raise ValueError("provider_names has too many entries")
                providers = frozenset(
                    _text(value, "provider", maximum=128)
                    for value in selected_providers
                )
            except TypeError:
                raise TypeError("provider_names must be a mapping or iterable")
            object.__setattr__(self, "provider_names", providers)
        created = _timestamp(self.created_at, "created_at")
        deadline = _timestamp(self.deadline, "deadline")
        if deadline <= created:
            raise ValueError("context request deadline must follow creation")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "deadline", deadline)
        object.__setattr__(
            self,
            "max_total_bytes",
            _positive_int(
                self.max_total_bytes, "max_total_bytes", MAX_CONTEXT_TOTAL_BYTES
            ),
        )

    def __repr__(self) -> str:
        return (
            "ContextRequest(kinds={!r}, permissions={!r}, provider_count={!r}, "
            "created_at={!r}, deadline={!r}, max_total_bytes={!r})"
        ).format(
            tuple(sorted(value.value for value in self.kinds)),
            tuple(sorted(value.value for value in self.permissions)),
            len(self.provider_names),
            self.created_at,
            self.deadline,
            self.max_total_bytes,
        )


def _enum_set(values: Any, enum_type: Type[_EnumT], name: str) -> FrozenSet[_EnumT]:
    if isinstance(values, (str, bytes)):
        raise TypeError("{} collection cannot be text".format(name))
    try:
        bounded = tuple(islice(iter(values), len(enum_type) + 1))
    except TypeError:
        raise TypeError("{} values must be iterable".format(name))
    except Exception:
        raise ValueError("{} values could not be read".format(name))
    if len(bounded) > len(enum_type):
        raise ValueError("{} has too many values".format(name))
    return frozenset(_enum(value, enum_type, name) for value in bounded)


@dataclass(frozen=True, repr=False)
class ContextSnapshot(_StatusRepr):
    snapshot_id: str
    fingerprint: str
    target: TargetBinding
    items: Tuple[ContextItem, ...]
    permissions: FrozenSet[Capability]
    created_at: float
    expires_at: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _text(self.snapshot_id, "snapshot id"))
        if type(self.target) is not TargetBinding:
            raise TypeError("target must be a TargetBinding")
        if isinstance(self.items, (str, bytes)):
            raise TypeError("items must be context items")
        items = tuple(self.items)
        if len(items) > MAX_CONTEXT_ITEMS or any(
            type(item) is not ContextItem for item in items
        ):
            raise ValueError("invalid context items")
        object.__setattr__(self, "items", items)
        permissions = _enum_set(self.permissions, Capability, "capability")
        object.__setattr__(self, "permissions", permissions)
        created = _timestamp(self.created_at, "created_at")
        expires = _timestamp(self.expires_at, "expires_at")
        if expires <= created:
            raise ValueError("snapshot expiry must follow creation")
        if any(not item.is_fresh(created) for item in items):
            raise ValueError("snapshot contains stale context")
        if any(item.required_capability not in permissions for item in items):
            raise ValueError("snapshot lacks a required context permission")
        if sum(item.payload_size for item in items) > MAX_CONTEXT_TOTAL_BYTES:
            raise ValueError("context snapshot exceeds its limit")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)
        if self.target.captured_at > created:
            raise ValueError("target binding cannot come from the future")
        if created - self.target.captured_at >= DEFAULT_CONTEXT_TTL_SECONDS:
            raise ValueError("target binding is too old")
        if expires > self.target.captured_at + DEFAULT_CONTEXT_TTL_SECONDS:
            raise ValueError("snapshot outlives its target binding")
        expected = self.compute_fingerprint(
            target=self.target, items=items, permissions=permissions
        )
        if not isinstance(self.fingerprint, str) or not _HEX_DIGEST.fullmatch(
            self.fingerprint
        ):
            raise ValueError("invalid context fingerprint")
        if self.fingerprint != expected:
            raise ValueError("context fingerprint does not match the snapshot")

    @classmethod
    def create(
        cls,
        *,
        target: Any,
        items: Tuple[ContextItem, ...],
        permissions: FrozenSet[Capability],
        now: Optional[float] = None,
        snapshot_id: Optional[str] = None,
        ttl_seconds: float = DEFAULT_CONTEXT_TTL_SECONDS,
    ) -> "ContextSnapshot":
        created = time.time() if now is None else _timestamp(now, "current time")
        checked_items = tuple(items)
        if any(type(item) is not ContextItem for item in checked_items):
            raise ValueError("invalid context items")
        checked_permissions = _enum_set(permissions, Capability, "capability")
        binding = TargetBinding.from_target(target, captured_at=created)
        if binding.captured_at > created:
            raise ValueError("target binding cannot come from the future")
        if created - binding.captured_at >= DEFAULT_CONTEXT_TTL_SECONDS:
            raise ValueError("target binding is too old")
        ttl = _timestamp(ttl_seconds, "ttl_seconds")
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        expires = min(
            [
                created + ttl,
                binding.captured_at + DEFAULT_CONTEXT_TTL_SECONDS,
            ]
            + [item.expires_at for item in checked_items]
        )
        fingerprint = cls.compute_fingerprint(
            target=binding, items=checked_items, permissions=checked_permissions
        )
        return cls(
            snapshot_id=snapshot_id or uuid.uuid4().hex,
            fingerprint=fingerprint,
            target=binding,
            items=checked_items,
            permissions=checked_permissions,
            created_at=created,
            expires_at=expires,
        )

    @staticmethod
    def compute_fingerprint(
        *,
        target: TargetBinding,
        items: Tuple[ContextItem, ...],
        permissions: FrozenSet[Capability],
    ) -> str:
        payload = {
            "schema": 1,
            "target": target._canonical(),
            "items": [item._canonical() for item in items],
            "permissions": sorted(item.value for item in permissions),
        }
        return hashlib.sha256(
            b"linux-speech-tools:context:v1\0"
            + _canonical_bytes(payload, maximum=MAX_CONTEXT_TOTAL_BYTES + 256 * 1024)
        ).hexdigest()

    def is_fresh(self, at: Optional[float] = None) -> bool:
        checked = time.time() if at is None else _timestamp(at, "freshness time")
        return self.created_at <= checked < self.expires_at and all(
            item.is_fresh(checked) for item in self.items
        )

    def assert_fresh(self, at: Optional[float] = None) -> None:
        if not self.is_fresh(at):
            raise ValueError("context snapshot is not fresh")

    def shareable_items(self) -> Tuple[ContextItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.shareable and item.required_capability in self.permissions
        )

    def to_agent_payload(
        self, *, at: Optional[float] = None
    ) -> Tuple[Mapping[str, Any], ...]:
        self.assert_fresh(at)
        return tuple(item.to_payload() for item in self.shareable_items())

    def for_adapter(self, *, at: Optional[float] = None) -> "ContextSnapshot":
        """Return a fresh, least-privilege copy safe to cross an adapter boundary.

        The original target binding is an authorization primitive, not agent
        context.  It is therefore replaced with an empty binding.  Context
        items that were not explicitly marked shareable are omitted, and the
        caller-visible snapshot identifier and fingerprint are not reused.
        """
        checked = time.time() if at is None else _timestamp(at, "adapter dispatch time")
        self.assert_fresh(checked)
        remaining_ttl = self.expires_at - checked
        shared_items = self.shareable_items()
        shared_permissions = frozenset(
            item.required_capability for item in shared_items
        )
        return ContextSnapshot.create(
            target=None,
            items=shared_items,
            permissions=shared_permissions,
            now=checked,
            ttl_seconds=remaining_ttl,
        )

    def to_status_dict(self, *, at: Optional[float] = None) -> Mapping[str, Any]:
        checked = time.time() if at is None else _timestamp(at, "status time")
        return {
            "schema_version": 1,
            "item_count": len(self.items),
            "shareable_item_count": len(self.shareable_items()),
            "kinds": sorted({item.kind.value for item in self.items}),
            "permissions": sorted(item.value for item in self.permissions),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "fresh": self.is_fresh(checked),
        }


@dataclass(frozen=True, repr=False)
class Result(_StatusRepr):
    outcome: Outcome
    failure_code: FailureCode = FailureCode.NONE
    revision: int = 0
    fallback_used: bool = False
    retryable: bool = False
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", _enum(self.outcome, Outcome, "outcome"))
        object.__setattr__(
            self,
            "failure_code",
            _enum(self.failure_code, FailureCode, "failure code"),
        )
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("revision must be an integer")
        if self.revision < 0:
            raise ValueError("revision cannot be negative")
        object.__setattr__(self, "revision", int(self.revision))
        if not isinstance(self.fallback_used, bool) or not isinstance(
            self.retryable, bool
        ):
            raise TypeError("result flags must be boolean")
        object.__setattr__(
            self,
            "message",
            _text(self.message, "message", maximum=4096, empty=True),
        )
        successful = self.outcome in (
            Outcome.COMPLETED,
            Outcome.COMPLETED_WITH_FALLBACK,
        )
        if successful and self.failure_code is not FailureCode.NONE:
            raise ValueError("successful result cannot have a failure code")
        if not successful and self.failure_code is FailureCode.NONE:
            raise ValueError("unsuccessful result requires a failure code")

    @property
    def ok(self) -> bool:
        return self.outcome in (Outcome.COMPLETED, Outcome.COMPLETED_WITH_FALLBACK)

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "outcome": self.outcome.value,
            "failure_code": self.failure_code.value,
            "revision": self.revision,
            "fallback_used": self.fallback_used,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, repr=False)
class SessionStatus(_StatusRepr):
    mode: VoiceMode
    lifecycle: SessionLifecycle = SessionLifecycle.PENDING
    activity: SessionActivity = SessionActivity.NONE
    revision: int = 0
    sequence: int = 0
    input_category: str = "none"
    output_category: str = "none"
    agent_category: str = "none"
    cancellable: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    outcome: Optional[Outcome] = None
    failure_code: FailureCode = FailureCode.NONE
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum(self.mode, VoiceMode, "voice mode"))
        lifecycle = _enum(self.lifecycle, SessionLifecycle, "lifecycle")
        activity = _enum(self.activity, SessionActivity, "activity")
        object.__setattr__(self, "lifecycle", lifecycle)
        object.__setattr__(self, "activity", activity)
        object.__setattr__(
            self, "failure_code", _enum(self.failure_code, FailureCode, "failure code")
        )
        if self.outcome is not None:
            object.__setattr__(self, "outcome", _enum(self.outcome, Outcome, "outcome"))
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("revision must be an integer")
        if self.revision < 0:
            raise ValueError("revision cannot be negative")
        object.__setattr__(self, "revision", int(self.revision))
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("sequence cannot be negative")
        object.__setattr__(self, "sequence", int(self.sequence))
        for name in ("input_category", "output_category", "agent_category"):
            object.__setattr__(self, name, _token(getattr(self, name), name))
        if not isinstance(self.cancellable, bool):
            raise TypeError("cancellable must be boolean")
        created = _timestamp(self.created_at, "created_at")
        updated = _timestamp(self.updated_at, "updated_at")
        if updated < created:
            raise ValueError("status update cannot predate creation")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "session_id", _text(self.session_id, "session id"))
        self._validate_axes()

    def _validate_axes(self) -> None:
        if (
            self.lifecycle in TERMINAL_LIFECYCLES
            and self.activity is not SessionActivity.NONE
        ):
            raise ValueError("terminal lifecycle must have no activity")
        if (
            self.lifecycle
            in (
                SessionLifecycle.PENDING,
                SessionLifecycle.AWAITING_APPROVAL,
                SessionLifecycle.PAUSED,
            )
            and self.activity is not SessionActivity.NONE
        ):
            raise ValueError("inactive lifecycle must have no activity")
        if self.lifecycle not in TERMINAL_LIFECYCLES and self.outcome is not None:
            raise ValueError("non-terminal status cannot have an outcome")
        if self.lifecycle in TERMINAL_LIFECYCLES and self.outcome is None:
            raise ValueError("terminal status requires an outcome")
        if self.lifecycle is SessionLifecycle.COMPLETED and self.outcome not in (
            Outcome.COMPLETED,
            Outcome.COMPLETED_WITH_FALLBACK,
        ):
            raise ValueError("completed lifecycle requires a completed outcome")
        if (
            self.lifecycle is SessionLifecycle.CANCELLED
            and self.outcome is not Outcome.CANCELLED
        ):
            raise ValueError("cancelled lifecycle requires cancelled outcome")
        if self.lifecycle is SessionLifecycle.CANCELLED and self.failure_code not in (
            FailureCode.CANCELLED,
            FailureCode.NONE,
        ):
            raise ValueError("cancelled lifecycle has an incompatible failure code")
        if self.lifecycle is SessionLifecycle.FAILED and self.outcome in (
            Outcome.COMPLETED,
            Outcome.COMPLETED_WITH_FALLBACK,
            Outcome.CANCELLED,
        ):
            raise ValueError("failed lifecycle has an incompatible outcome")
        if (
            self.lifecycle is SessionLifecycle.FAILED
            and self.failure_code is FailureCode.NONE
        ):
            raise ValueError("failed lifecycle requires a failure code")
        if (
            self.lifecycle is SessionLifecycle.COMPLETED
            and self.failure_code is not FailureCode.NONE
        ):
            raise ValueError("completed lifecycle cannot have a failure code")

    def transition(
        self,
        *,
        lifecycle: SessionLifecycle,
        activity: SessionActivity,
        revision: Optional[int] = None,
        now: Optional[float] = None,
        outcome: Optional[Outcome] = None,
        failure_code: FailureCode = FailureCode.NONE,
        cancellable: Optional[bool] = None,
    ) -> "SessionStatus":
        next_lifecycle = _enum(lifecycle, SessionLifecycle, "lifecycle")
        next_activity = _enum(activity, SessionActivity, "activity")
        next_revision = self.revision if revision is None else revision
        if isinstance(next_revision, bool) or not isinstance(next_revision, int):
            raise TypeError("revision must be an integer")
        if next_revision < self.revision:
            raise ValueError(FailureCode.STALE_REVISION.value)
        next_revision = int(next_revision)
        same_running_lifecycle = (
            next_lifecycle is self.lifecycle
            and self.lifecycle not in TERMINAL_LIFECYCLES
        )
        if (
            not same_running_lifecycle
            and next_lifecycle not in _LIFECYCLE_TRANSITIONS[self.lifecycle]
        ):
            raise ValueError(FailureCode.INVALID_TRANSITION.value)
        changed_at = time.time() if now is None else _timestamp(now, "transition time")
        if changed_at < self.updated_at:
            raise ValueError("transition cannot move time backwards")
        return replace(
            self,
            lifecycle=next_lifecycle,
            activity=next_activity,
            revision=next_revision,
            sequence=self.sequence + 1,
            updated_at=changed_at,
            outcome=outcome,
            failure_code=failure_code,
            cancellable=self.cancellable if cancellable is None else cancellable,
        )

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "mode": self.mode.value,
            "lifecycle": self.lifecycle.value,
            "activity": self.activity.value,
            "revision": self.revision,
            "sequence": self.sequence,
            "input_category": self.input_category,
            "output_category": self.output_category,
            "agent_category": self.agent_category,
            "cancellable": self.cancellable,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "outcome": self.outcome.value if self.outcome is not None else None,
            "failure_code": self.failure_code.value,
        }


@dataclass(frozen=True, repr=False)
class AgentSession(_StatusRepr):
    adapter_id: str
    session_id: str
    label: str
    resumable: bool
    repository_ref: str = "none"

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _token(self.adapter_id, "adapter id"))
        object.__setattr__(
            self, "session_id", _text(self.session_id, "agent session id")
        )
        object.__setattr__(
            self, "label", _text(self.label, "agent session label", maximum=256)
        )
        object.__setattr__(
            self,
            "repository_ref",
            _text(self.repository_ref, "repository ref", maximum=256),
        )
        if not isinstance(self.resumable, bool):
            raise TypeError("resumable must be boolean")

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "resumable": self.resumable,
        }


@dataclass(frozen=True, repr=False)
class AgentRequest(_StatusRepr):
    request_id: str
    voice_session_id: str
    mode: VoiceMode
    prompt: str
    context: ContextSnapshot
    capabilities: FrozenSet[Capability]
    agent_session_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _text(self.request_id, "request id"))
        object.__setattr__(
            self, "voice_session_id", _text(self.voice_session_id, "voice session id")
        )
        object.__setattr__(self, "mode", _enum(self.mode, VoiceMode, "voice mode"))
        if self.mode not in (VoiceMode.ASK, VoiceMode.ACT):
            raise ValueError("agent requests require ask or act mode")
        object.__setattr__(
            self,
            "prompt",
            _bounded_utf8_text(
                self.prompt, "agent prompt", maximum=MAX_AGENT_TEXT_BYTES
            ),
        )
        if not self.prompt.strip():
            raise ValueError("agent prompt cannot be empty")
        if type(self.context) is not ContextSnapshot:
            raise TypeError("context must be a ContextSnapshot")
        capabilities = _enum_set(self.capabilities, Capability, "capability")
        if self.mode is VoiceMode.ASK and not capabilities.issubset(
            (Capability.READ_CONTEXT, Capability.DRAFT)
        ):
            raise ValueError("ask mode cannot request consequential capabilities")
        required_context_capabilities = frozenset(
            item.required_capability for item in self.context.shareable_items()
        )
        if not required_context_capabilities.issubset(capabilities):
            raise ValueError(
                "shareable context requires an undeclared request capability"
            )
        object.__setattr__(self, "capabilities", capabilities)
        if self.agent_session_id is not None:
            object.__setattr__(
                self,
                "agent_session_id",
                _text(self.agent_session_id, "agent session id"),
            )

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "mode": self.mode.value,
            "capabilities": sorted(value.value for value in self.capabilities),
            "resumes_agent_session": self.agent_session_id is not None,
        }

    def for_adapter(self, *, at: Optional[float] = None) -> "AgentRequest":
        """Copy this request while stripping local-only target/context data."""
        return replace(self, context=self.context.for_adapter(at=at))


@dataclass(frozen=True, repr=False)
class AgentRun(_StatusRepr):
    run_id: str
    adapter_id: str
    state: str
    agent_session_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run id"))
        object.__setattr__(self, "adapter_id", _token(self.adapter_id, "adapter id"))
        object.__setattr__(self, "state", _token(self.state, "run state"))
        if self.agent_session_id is not None:
            object.__setattr__(
                self,
                "agent_session_id",
                _text(self.agent_session_id, "agent session id"),
            )

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "state": self.state,
            "has_agent_session": self.agent_session_id is not None,
        }


@dataclass(frozen=True, repr=False)
class ActionProposal(_StatusRepr):
    proposal_id: str
    voice_session_id: str
    adapter: str
    agent_session_id: str
    capability: Capability
    destination: str
    operation: Any
    operation_digest: str
    context_fingerprint: str
    summary: str
    created_at: float
    expires_at: float

    def __post_init__(self) -> None:
        for name in (
            "proposal_id",
            "voice_session_id",
            "adapter",
            "agent_session_id",
            "destination",
        ):
            validator = _token if name == "adapter" else _text
            object.__setattr__(self, name, validator(getattr(self, name), name))
        object.__setattr__(
            self, "capability", _enum(self.capability, Capability, "capability")
        )
        operation = _freeze_json(self.operation)
        object.__setattr__(self, "operation", operation)
        digest = canonical_operation_digest(operation)
        if not isinstance(self.operation_digest, str) or not _HEX_DIGEST.fullmatch(
            self.operation_digest
        ):
            raise ValueError("invalid operation digest")
        if not hmac_compare(self.operation_digest, digest):
            raise ValueError("operation digest does not match operation")
        if not isinstance(self.context_fingerprint, str) or not _HEX_DIGEST.fullmatch(
            self.context_fingerprint
        ):
            raise ValueError("invalid context fingerprint")
        object.__setattr__(
            self, "summary", _text(self.summary, "proposal summary", maximum=4096)
        )
        created = _timestamp(self.created_at, "proposal created_at")
        expires = _timestamp(self.expires_at, "proposal expires_at")
        if expires <= created:
            raise ValueError("proposal expiry must follow creation")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)

    @classmethod
    def create(
        cls,
        *,
        proposal_id: str,
        voice_session_id: str,
        adapter: str,
        agent_session_id: str,
        capability: Capability,
        destination: str,
        operation: Any,
        context_fingerprint: str,
        summary: str,
        created_at: float,
        expires_at: float,
    ) -> "ActionProposal":
        return cls(
            proposal_id=proposal_id,
            voice_session_id=voice_session_id,
            adapter=adapter,
            agent_session_id=agent_session_id,
            capability=capability,
            destination=destination,
            operation=operation,
            operation_digest=canonical_operation_digest(operation),
            context_fingerprint=context_fingerprint,
            summary=summary,
            created_at=created_at,
            expires_at=expires_at,
        )

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "capability": self.capability.value,
            "expires_at": self.expires_at,
        }


def hmac_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))


@dataclass(frozen=True, repr=False)
class ApprovalRequest(_StatusRepr):
    request_id: str
    proposal_id: str
    voice_session_id: str
    adapter: str
    agent_session_id: str
    capability: Capability
    destination: str
    operation: Any
    operation_digest: str
    context_fingerprint: str
    summary: str
    created_at: float
    expires_at: float

    @classmethod
    def from_proposal(
        cls, proposal: ActionProposal, *, request_id: str
    ) -> "ApprovalRequest":
        if type(proposal) is not ActionProposal:
            raise TypeError("proposal must be an ActionProposal")
        return cls(
            request_id=request_id,
            proposal_id=proposal.proposal_id,
            voice_session_id=proposal.voice_session_id,
            adapter=proposal.adapter,
            agent_session_id=proposal.agent_session_id,
            capability=proposal.capability,
            destination=proposal.destination,
            operation=proposal.operation,
            operation_digest=proposal.operation_digest,
            context_fingerprint=proposal.context_fingerprint,
            summary=proposal.summary,
            created_at=proposal.created_at,
            expires_at=proposal.expires_at,
        )

    def __post_init__(self) -> None:
        for name in (
            "request_id",
            "proposal_id",
            "voice_session_id",
            "adapter",
            "agent_session_id",
            "destination",
        ):
            validator = _token if name == "adapter" else _text
            object.__setattr__(self, name, validator(getattr(self, name), name))
        object.__setattr__(
            self, "capability", _enum(self.capability, Capability, "capability")
        )
        operation = _freeze_json(self.operation)
        object.__setattr__(self, "operation", operation)
        if not _HEX_DIGEST.fullmatch(self.operation_digest):
            raise ValueError("invalid operation digest")
        if not hmac_compare(
            self.operation_digest, canonical_operation_digest(operation)
        ):
            raise ValueError("operation digest does not match approval operation")
        if not _HEX_DIGEST.fullmatch(self.context_fingerprint):
            raise ValueError("invalid context fingerprint")
        object.__setattr__(
            self, "summary", _text(self.summary, "approval summary", maximum=4096)
        )
        created = _timestamp(self.created_at, "request created_at")
        expires = _timestamp(self.expires_at, "request expires_at")
        if expires <= created:
            raise ValueError("approval request expiry must follow creation")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "capability": self.capability.value,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, repr=False)
class ApprovalGrant(_StatusRepr):
    grant_id: str
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

    def __post_init__(self) -> None:
        for name in (
            "grant_id",
            "request_id",
            "proposal_id",
            "voice_session_id",
            "adapter",
            "agent_session_id",
            "destination",
        ):
            validator = _token if name == "adapter" else _text
            object.__setattr__(self, name, validator(getattr(self, name), name))
        object.__setattr__(
            self, "capability", _enum(self.capability, Capability, "capability")
        )
        object.__setattr__(
            self, "method", _enum(self.method, ApprovalMethod, "approval method")
        )
        if not _HEX_DIGEST.fullmatch(self.operation_digest):
            raise ValueError("invalid operation digest")
        if not _HEX_DIGEST.fullmatch(self.context_fingerprint):
            raise ValueError("invalid context fingerprint")
        granted = _timestamp(self.granted_at, "grant granted_at")
        expires = _timestamp(self.expires_at, "grant expires_at")
        if expires <= granted:
            raise ValueError("approval grant expiry must follow grant")
        object.__setattr__(self, "granted_at", granted)
        object.__setattr__(self, "expires_at", expires)

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "capability": self.capability.value,
            "expires_at": self.expires_at,
            "method": self.method.value,
        }


@dataclass(frozen=True, repr=False)
class AgentEvent(_StatusRepr):
    run_id: str
    sequence: int
    kind: AgentEventKind
    text: str = ""
    proposal: Optional[ActionProposal] = None
    failure_code: FailureCode = FailureCode.NONE

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run id"))
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("event sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("event sequence cannot be negative")
        object.__setattr__(self, "sequence", int(self.sequence))
        object.__setattr__(
            self, "kind", _enum(self.kind, AgentEventKind, "agent event kind")
        )
        object.__setattr__(
            self,
            "failure_code",
            _enum(self.failure_code, FailureCode, "failure code"),
        )
        object.__setattr__(
            self,
            "text",
            _bounded_utf8_text(
                self.text, "agent event text", maximum=MAX_AGENT_TEXT_BYTES
            ),
        )
        if self.proposal is not None and type(self.proposal) is not ActionProposal:
            raise TypeError("proposal must be an ActionProposal")
        if self.kind is AgentEventKind.ACTION_PROPOSAL and self.proposal is None:
            raise ValueError("proposal event requires an action proposal")
        if (
            self.kind is not AgentEventKind.ACTION_PROPOSAL
            and self.proposal is not None
        ):
            raise ValueError("only proposal events may contain an action proposal")
        if self.kind is AgentEventKind.ERROR and self.failure_code is FailureCode.NONE:
            raise ValueError("error event requires a failure code")
        if (
            self.kind is not AgentEventKind.ERROR
            and self.failure_code is not FailureCode.NONE
        ):
            raise ValueError("non-error event cannot carry a failure code")

    @property
    def terminal(self) -> bool:
        return self.kind in TERMINAL_AGENT_EVENTS

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind.value,
            "has_text": bool(self.text),
            "has_proposal": self.proposal is not None,
            "failure_code": self.failure_code.value,
        }


__all__ = [
    "ActionProposal",
    "AgentEvent",
    "AgentEventKind",
    "AgentRequest",
    "AgentRun",
    "AgentSession",
    "ApprovalGrant",
    "ApprovalMethod",
    "ApprovalRequest",
    "Capability",
    "ContextItem",
    "ContextKind",
    "ContextRequest",
    "ContextSnapshot",
    "FailureCode",
    "HIGH_IMPACT_CAPABILITIES",
    "Outcome",
    "Result",
    "Sensitivity",
    "SessionActivity",
    "SessionLifecycle",
    "SessionStatus",
    "TargetBinding",
    "TERMINAL_AGENT_EVENTS",
    "TERMINAL_LIFECYCLES",
    "VoiceMode",
    "canonical_operation_digest",
]
