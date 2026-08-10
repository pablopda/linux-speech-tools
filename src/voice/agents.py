"""Provider-neutral agent contracts and fail-closed lifecycle validation."""

from __future__ import annotations

import re
import threading
import time
from itertools import islice
from typing import Callable, Dict, FrozenSet, Iterator, Protocol, Sequence, Tuple

from .models import (
    ActionProposal,
    AgentEvent,
    AgentEventKind,
    AgentRequest,
    AgentRun,
    AgentSession,
    ApprovalGrant,
    Capability,
    VoiceMode,
)


DEFAULT_MAX_EVENTS = 10_000
ABSOLUTE_MAX_EVENTS = 100_000
DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
ABSOLUTE_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_SAFE_ADAPTER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _adapter_id(value: object) -> str:
    if not isinstance(value, str):
        raise AgentContractError("invalid-adapter-id")
    try:
        normalized = bytes.decode(str.encode(value, "utf-8"), "utf-8")
    except UnicodeEncodeError:
        raise AgentContractError("invalid-adapter-id")
    if not _SAFE_ADAPTER_ID.fullmatch(normalized):
        raise AgentContractError("invalid-adapter-id")
    return normalized


def _bounded_capabilities(adapter: AgentAdapter) -> FrozenSet[Capability]:
    try:
        values = tuple(islice(iter(adapter.capabilities()), len(Capability) + 1))
    except Exception:
        raise AgentContractError("capability-discovery-failed")
    if len(values) > len(Capability):
        raise AgentContractError("invalid-capability")
    if any(not isinstance(value, Capability) for value in values):
        raise AgentContractError("invalid-capability")
    declared = frozenset(values)
    if not declared:
        raise AgentContractError("empty-capabilities")
    return declared


class AgentContractError(RuntimeError):
    """Closed-code provider failure that never includes agent output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("agent contract failed: {}".format(code))


class AgentAdapter(Protocol):
    adapter_id: str

    def capabilities(self) -> FrozenSet[Capability]: ...

    def discover_sessions(self, request: AgentRequest) -> Sequence[AgentSession]: ...

    def send(self, request: AgentRequest) -> AgentRun: ...

    def stream(self, run_id: str) -> Iterator[AgentEvent]: ...

    def cancel(self, run_id: str) -> None: ...


class ActionAdapter(AgentAdapter, Protocol):
    """An adapter with an enforceable typed proposal/execution boundary."""

    def propose(self, request: AgentRequest) -> ActionProposal: ...

    def execute(self, proposal: ActionProposal, grant: ApprovalGrant) -> AgentRun: ...


class AgentRegistry:
    """Register explicit adapters and validate their event lifecycle."""

    def __init__(
        self,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or not 1 <= max_events <= ABSOLUTE_MAX_EVENTS
        ):
            raise ValueError("max_events is outside its safe range")
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or not 1 <= max_output_bytes <= ABSOLUTE_MAX_OUTPUT_BYTES
        ):
            raise ValueError("max_output_bytes is outside its safe range")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.max_events = max_events
        self.max_output_bytes = max_output_bytes
        self._clock = clock
        self._adapters: Dict[str, AgentAdapter] = {}
        self._lock = threading.RLock()

    def register(self, adapter: AgentAdapter) -> None:
        try:
            adapter_id = getattr(adapter, "adapter_id", "")
            methods = {
                method: getattr(adapter, method, None)
                for method in (
                    "capabilities",
                    "discover_sessions",
                    "send",
                    "stream",
                    "cancel",
                )
            }
        except Exception:
            raise AgentContractError("adapter-introspection-failed")
        adapter_id = _adapter_id(adapter_id)
        for method, implementation in methods.items():
            if not callable(implementation):
                raise AgentContractError("incomplete-adapter")
        _bounded_capabilities(adapter)
        with self._lock:
            if adapter_id in self._adapters:
                raise AgentContractError("duplicate-adapter")
            self._adapters[adapter_id] = adapter

    def adapter_ids(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._adapters))

    def get(self, adapter_id: str) -> AgentAdapter:
        adapter_id = _adapter_id(adapter_id)
        with self._lock:
            adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise AgentContractError("adapter-unavailable")
        return adapter

    def collect(
        self, adapter_id: str, request: AgentRequest
    ) -> Tuple[AgentRun, Tuple[AgentEvent, ...]]:
        """Compatibility collector with strict exactly-one-terminal semantics."""
        normalized_adapter_id = _adapter_id(adapter_id)
        adapter = self.get(normalized_adapter_id)
        if type(request) is not AgentRequest:
            raise TypeError("request must be an AgentRequest")
        if request.mode is not VoiceMode.ASK:
            raise AgentContractError("action-requires-proposal")
        declared = _bounded_capabilities(adapter)
        required_context_capabilities = frozenset(
            item.required_capability for item in request.context.shareable_items()
        )
        if not required_context_capabilities.issubset(request.capabilities):
            raise AgentContractError("context-capability-required")
        if not request.capabilities.issubset(declared):
            raise AgentContractError("capability-not-declared")
        try:
            dispatch_request = request.for_adapter(at=self._clock())
        except (TypeError, ValueError):
            raise AgentContractError("context-expired")
        try:
            run = adapter.send(dispatch_request)
        except Exception:
            raise AgentContractError("send-failed")
        if type(run) is not AgentRun:
            raise AgentContractError("invalid-run")
        if run.adapter_id != normalized_adapter_id:
            self._cancel_quietly(adapter, run.run_id)
            raise AgentContractError("invalid-run")

        events = []
        last_sequence = -1
        terminal_seen = False
        output_bytes = 0
        try:
            iterator = adapter.stream(run.run_id)
            for event in iterator:
                if len(events) >= self.max_events:
                    raise AgentContractError("event-limit")
                if type(event) is not AgentEvent or event.run_id != run.run_id:
                    raise AgentContractError("invalid-event")
                if event.sequence <= last_sequence:
                    raise AgentContractError("stale-event")
                if terminal_seen:
                    raise AgentContractError("event-after-terminal")
                if event.kind is AgentEventKind.ACTION_PROPOSAL:
                    raise AgentContractError("action-proposal-in-ask")
                output_bytes += len(event.text.encode("utf-8"))
                if output_bytes > self.max_output_bytes:
                    raise AgentContractError("output-byte-limit")
                events.append(event)
                last_sequence = event.sequence
                terminal_seen = event.terminal
        except AgentContractError:
            self._cancel_quietly(adapter, run.run_id)
            raise
        except BaseException as error:
            self._cancel_quietly(adapter, run.run_id)
            if not isinstance(error, Exception):
                raise
            raise AgentContractError("stream-failed")

        if not terminal_seen:
            self._cancel_quietly(adapter, run.run_id)
            raise AgentContractError("missing-terminal-event")
        return run, tuple(events)

    @staticmethod
    def _cancel_quietly(adapter: AgentAdapter, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id:
            return
        try:
            adapter.cancel(run_id)
        except BaseException:
            pass


__all__ = [
    "ActionAdapter",
    "AgentAdapter",
    "AgentContractError",
    "AgentRegistry",
    "DEFAULT_MAX_OUTPUT_BYTES",
]
