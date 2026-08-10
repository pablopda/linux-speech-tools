#!/usr/bin/env python3
"""Backend-neutral policy for safe, revisioned text insertion.

The transport classes live with their platform/output implementations.  This
module deliberately knows nothing about ydotool, the clipboard, GNOME, or a
future IBus backend; it only owns ordering, finalization, ambiguity, fallback,
and submission policy.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Dict, Optional, Protocol


class InsertionState(str, Enum):
    CONFIRMED_INSERTED = "confirmed-inserted"
    DISPATCHED_UNCONFIRMED = "dispatched-unconfirmed"
    CLIPBOARD_FALLBACK = "clipboard-fallback"
    NON_INSERTING = "non-inserting"
    FAILED_BEFORE_DISPATCH = "failed-before-dispatch"
    AMBIGUOUS_AFTER_DISPATCH = "ambiguous-after-dispatch"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    CANCELLED = "cancelled"


SUCCESS_STATES = {
    InsertionState.CONFIRMED_INSERTED,
    InsertionState.DISPATCHED_UNCONFIRMED,
    InsertionState.CLIPBOARD_FALLBACK,
    InsertionState.NON_INSERTING,
}


@dataclass(frozen=True)
class InsertionResult:
    """A non-sensitive description of an insertion operation.

    ``diagnostic`` must describe only the failure category.  It must never
    contain transcript text, clipboard contents, a window title, or a command
    line which embeds dictated text.
    """

    state: InsertionState
    backend: str
    revision: Optional[int] = None
    session_id: str = ""
    target_token_match: Optional[bool] = None
    diagnostic: Optional[str] = None

    def __bool__(self) -> bool:
        return self.state in SUCCESS_STATES

    @property
    def submit_eligible(self) -> bool:
        return self.state in {
            InsertionState.CONFIRMED_INSERTED,
            InsertionState.DISPATCHED_UNCONFIRMED,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Return status-safe fields without target or transcript data."""
        return {
            "state": self.state.value,
            "backend": self.backend,
            "revision": self.revision,
            "session_id": self.session_id,
            "target_token_match": self.target_token_match,
            "diagnostic": self.diagnostic,
        }


class InsertionTransport(Protocol):
    """Mechanism used by :class:`InsertionSession`.

    Implementations may also provide ``fallback(text, revision)``.  That path
    must be non-inserting (normally clipboard-only), because it can be invoked
    after insertion has been permanently locked by an ambiguous result.
    """

    backend: str
    mode: str
    supports_submit: bool
    requires_target_match: bool
    destructive_updates: bool

    def update(self, text: str, revision: Optional[int] = None) -> InsertionResult:
        ...

    def finalize(self, text: str, revision: Optional[int] = None) -> InsertionResult:
        ...

    def submit(self, revision: Optional[int] = None) -> InsertionResult:
        ...

    def cancel(self, revision: Optional[int] = None) -> InsertionResult:
        ...

    def close(self) -> None:
        ...


def result(
    state: InsertionState,
    backend: str,
    *,
    revision: Optional[int] = None,
    diagnostic: Optional[str] = None,
    target_token_match: Optional[bool] = None,
) -> InsertionResult:
    """Small transport-side constructor used by current output adapters."""
    return InsertionResult(
        state=state,
        backend=backend,
        revision=revision,
        target_token_match=target_token_match,
        diagnostic=diagnostic,
    )


class InsertionSession:
    """Order and authorize operations for one target-bound insertion flow."""

    def __init__(
        self,
        transport: InsertionTransport,
        *,
        target_token: str = "",
        target_guard: Optional[Callable[[], bool]] = None,
        allow_unconfirmed_submit: bool = True,
        session_id: Optional[str] = None,
    ) -> None:
        self.transport = transport
        self.session_id = session_id or uuid.uuid4().hex
        self.target_token = target_token
        self.target_guard = target_guard
        self.allow_unconfirmed_submit = allow_unconfirmed_submit
        self.last_revision = -1
        self.finalized = False
        self.cancelled = False
        self.closed = False
        self.last_error: Optional[str] = None
        self.last_result: Optional[InsertionResult] = None
        self._last_operation = ""
        self._final_result: Optional[InsertionResult] = None
        self._submitted = False
        self._insertion_locked = False
        self._target_invalidated = False
        self._lock = threading.RLock()

    @property
    def mode(self) -> str:
        return getattr(self.transport, "mode", "unknown")

    @property
    def backend(self) -> str:
        return getattr(self.transport, "backend", self.mode)

    @property
    def insertion_locked(self) -> bool:
        return self._insertion_locked

    @property
    def focus_drift_detected(self) -> bool:
        """Whether any focus check permanently invalidated this session."""
        with self._lock:
            return self._target_invalidated

    def _make(
        self,
        state: InsertionState,
        revision: Optional[int],
        diagnostic: Optional[str] = None,
        target_token_match: Optional[bool] = None,
    ) -> InsertionResult:
        return InsertionResult(
            state=state,
            backend=self.backend,
            revision=revision,
            session_id=self.session_id,
            target_token_match=target_token_match,
            diagnostic=diagnostic,
        )

    def _record(self, operation_result: InsertionResult) -> InsertionResult:
        stamped = replace(
            operation_result,
            session_id=self.session_id,
            backend=operation_result.backend or self.backend,
        )
        self.last_result = stamped
        if stamped.diagnostic:
            self.last_error = stamped.diagnostic
        if stamped.state == InsertionState.AMBIGUOUS_AFTER_DISPATCH:
            self._insertion_locked = True
        if stamped.target_token_match is False:
            self._target_invalidated = True
            self._insertion_locked = True
        return stamped

    def _normalize(
        self,
        value: Any,
        revision: Optional[int],
        *,
        success_state: InsertionState,
    ) -> InsertionResult:
        if isinstance(value, InsertionResult):
            if value.revision is None:
                value = replace(value, revision=revision)
            return value
        if value:
            return result(success_state, self.backend, revision=revision)
        return result(
            InsertionState.FAILED_BEFORE_DISPATCH,
            self.backend,
            revision=revision,
            diagnostic="transport reported failure before confirmed dispatch",
        )

    def _validate_revision(self, revision: int) -> Optional[InsertionResult]:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return self._make(
                InsertionState.REJECTED,
                None,
                "revision must be a non-negative integer",
            )
        return None

    def _call(
        self,
        method_name: str,
        *args: Any,
        exception_state: InsertionState = InsertionState.AMBIGUOUS_AFTER_DISPATCH,
    ) -> Any:
        try:
            method = getattr(self.transport, method_name)
            return method(*args)
        except Exception:
            # Exception strings can include argv, and classic typing argv embeds
            # transcript text. Keep diagnostics deliberately generic. For an
            # inserting operation, an unexpected exception cannot prove that no
            # input was dispatched, so callers use the conservative ambiguous
            # default. Non-inserting fallback/cancel paths opt into a proven
            # pre-dispatch failure classification below.
            return result(
                exception_state,
                self.backend,
                diagnostic="transport raised before a result was available",
            )

    def _safe_fallback(self, text: str, revision: int) -> InsertionResult:
        fallback = getattr(self.transport, "fallback", None)
        if fallback is None:
            return self._record(
                self._make(
                    InsertionState.REJECTED,
                    revision,
                    "insertion is locked and no non-inserting fallback is available",
                )
            )
        value = self._call(
            "fallback",
            text,
            revision,
            exception_state=InsertionState.FAILED_BEFORE_DISPATCH,
        )
        normalized = self._normalize(
            value,
            revision,
            success_state=InsertionState.CLIPBOARD_FALLBACK,
        )
        if normalized.state in {
            InsertionState.CONFIRMED_INSERTED,
            InsertionState.DISPATCHED_UNCONFIRMED,
        }:
            normalized = result(
                InsertionState.REJECTED,
                normalized.backend,
                revision=revision,
                diagnostic="unsafe inserting result returned by fallback",
            )
        return self._record(normalized)

    def _target_authorized(self, *, force: bool = False) -> bool:
        if not force and not getattr(self.transport, "requires_target_match", False):
            return True
        matches = self._target_matches()
        if not matches:
            # Once drift is observed, returning to the original target cannot
            # silently resume insertion for this session.
            self._target_invalidated = True
            self._insertion_locked = True
        return matches

    def _stamp_target_match(
        self, operation_result: InsertionResult, *, required: bool
    ) -> InsertionResult:
        if required and operation_result.target_token_match is None:
            return replace(operation_result, target_token_match=True)
        return operation_result

    def _fallback_if_safe(
        self, text: str, revision: int, operation_result: InsertionResult
    ) -> InsertionResult:
        if (
            operation_result.state
            in {
                InsertionState.FAILED_BEFORE_DISPATCH,
                InsertionState.UNAVAILABLE,
            }
            and getattr(self.transport, "safe_fallback", False)
        ):
            return self._safe_fallback(text, revision)
        return operation_result

    def update(self, text: str, revision: int) -> InsertionResult:
        with self._lock:
            invalid = self._validate_revision(revision)
            if invalid:
                return self._record(invalid)
            if self.closed or self.cancelled:
                return self._record(
                    self._make(InsertionState.CANCELLED, revision, "session is closed")
                )
            if self.finalized:
                return self._record(
                    self._make(InsertionState.REJECTED, revision, "session is finalized")
                )
            if revision <= self.last_revision:
                return self._record(
                    self._make(InsertionState.STALE, revision, "revision is not newer")
                )
            self.last_revision = revision
            self._last_operation = "update"
            if self._insertion_locked:
                return self._safe_fallback(text, revision)
            target_required = bool(
                getattr(self.transport, "destructive_updates", False)
                and getattr(self.transport, "requires_target_match", False)
            )
            if target_required and not self._target_authorized():
                operation_result = self._safe_fallback(text, revision)
                return self._record(
                    replace(operation_result, target_token_match=False)
                )
            value = self._call("update", text, revision)
            operation_result = self._normalize(
                value,
                revision,
                success_state=InsertionState.DISPATCHED_UNCONFIRMED,
            )
            operation_result = self._stamp_target_match(
                operation_result, required=target_required
            )
            operation_result = self._fallback_if_safe(
                text, revision, operation_result
            )
            return self._record(operation_result)

    def finalize(self, text: str, revision: int) -> InsertionResult:
        with self._lock:
            invalid = self._validate_revision(revision)
            if invalid:
                return self._record(invalid)
            if self._final_result is not None and revision == self._final_result.revision:
                # The cached structured result is returned without invoking the
                # transport, even when the original operation failed.
                return self._final_result
            if self.closed or self.cancelled:
                return self._record(
                    self._make(InsertionState.CANCELLED, revision, "session is closed")
                )
            if self.finalized:
                return self._record(
                    self._make(InsertionState.REJECTED, revision, "session is finalized")
                )
            if revision < self.last_revision or (
                revision == self.last_revision and self._last_operation != "update"
            ):
                return self._record(
                    self._make(InsertionState.STALE, revision, "revision is stale")
                )

            self.last_revision = revision
            self.finalized = True
            self._last_operation = "finalize"
            target_required = bool(
                getattr(self.transport, "requires_target_match", False)
            )
            if self._insertion_locked:
                operation_result = self._safe_fallback(text, revision)
            elif not self._target_authorized():
                operation_result = self._safe_fallback(text, revision)
                operation_result = self._record(
                    replace(operation_result, target_token_match=False)
                )
            else:
                value = self._call("finalize", text, revision)
                operation_result = self._normalize(
                    value,
                    revision,
                    success_state=InsertionState.DISPATCHED_UNCONFIRMED,
                )
                operation_result = self._stamp_target_match(
                    operation_result, required=target_required
                )
                operation_result = self._record(operation_result)
                operation_result = self._fallback_if_safe(
                    text, revision, operation_result
                )
            self._final_result = operation_result
            return operation_result

    def _target_matches(self) -> bool:
        if self._target_invalidated:
            return False
        if self.target_guard is None:
            return False
        try:
            return bool(self.target_guard())
        except Exception:
            return False

    def can_submit(self) -> bool:
        with self._lock:
            if (
                self.closed
                or self.cancelled
                or not self.finalized
                or self._submitted
                or self._insertion_locked
                or self._final_result is None
                or not getattr(self.transport, "supports_submit", False)
            ):
                return False
            if self._final_result.state == InsertionState.CONFIRMED_INSERTED:
                eligible = True
            else:
                eligible = (
                    self.allow_unconfirmed_submit
                    and self._final_result.state
                    == InsertionState.DISPATCHED_UNCONFIRMED
                )
            if not eligible:
                return False
            return self._target_authorized(force=True)

    def submit(self) -> InsertionResult:
        with self._lock:
            revision = self.last_revision if self.last_revision >= 0 else None
            if not self.can_submit():
                target_match = self._target_matches() if self.target_guard else None
                return self._record(
                    self._make(
                        InsertionState.REJECTED,
                        revision,
                        "final insertion result is not submit-eligible",
                        target_match,
                    )
                )
            target_match = self._target_authorized(force=True)
            if not target_match:
                return self._record(
                    self._make(
                        InsertionState.REJECTED,
                        revision,
                        "target no longer matches",
                        False,
                    )
                )
            self._submitted = True
            value = self._call("submit", revision)
            operation_result = self._normalize(
                value,
                revision,
                success_state=InsertionState.DISPATCHED_UNCONFIRMED,
            )
            operation_result = self._stamp_target_match(
                operation_result, required=True
            )
            return self._record(operation_result)

    def cancel(self) -> InsertionResult:
        with self._lock:
            revision = self.last_revision if self.last_revision >= 0 else None
            if self.cancelled:
                return self.last_result or self._make(
                    InsertionState.CANCELLED, revision, "session is cancelled"
                )
            if self.finalized:
                return self._record(
                    self._make(
                        InsertionState.REJECTED,
                        revision,
                        "finalized session cannot be cancelled",
                    )
                )
            self.cancelled = True
            value = self._call(
                "cancel",
                revision,
                exception_state=InsertionState.FAILED_BEFORE_DISPATCH,
            )
            operation_result = self._normalize(
                value,
                revision,
                success_state=InsertionState.CANCELLED,
            )
            if operation_result.state in SUCCESS_STATES:
                operation_result = result(
                    InsertionState.CANCELLED,
                    operation_result.backend,
                    revision=revision,
                )
            return self._record(operation_result)

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            if not self.finalized and not self.cancelled:
                self.cancel()
            self.closed = True
            try:
                self.transport.close()
            except Exception:
                self.last_error = "transport close failed"

    def status_fields(self) -> Dict[str, Any]:
        with self._lock:
            if self.last_result is None:
                return {
                    "insertion_backend": self.backend,
                    "insertion_result": None,
                    "insertion_locked": self._insertion_locked,
                }
            return {
                "insertion_backend": self.last_result.backend,
                "insertion_result": self.last_result.state.value,
                "insertion_revision": self.last_result.revision,
                "insertion_locked": self._insertion_locked,
            }


class FinalOnlyInsertionAdapter:
    """Give repeated classic utterances the common final-result vocabulary.

    Each emitted utterance is an independent final-only insertion session.  A
    long-running classic dictation process may therefore emit many utterances
    without weakening ``InsertionSession``'s exactly-once finalization rule.
    """

    def __init__(self, transport: InsertionTransport) -> None:
        self.transport = transport
        self.revision = 0
        self.last_result: Optional[InsertionResult] = None

    def insert(self, text: str) -> InsertionResult:
        self.revision += 1
        session = InsertionSession(
            self.transport,
            allow_unconfirmed_submit=False,
            session_id="{}:{}".format(uuid.uuid4().hex, self.revision),
        )
        self.last_result = session.finalize(text, self.revision)
        session.close()
        return self.last_result


# Readable aliases for callers that prefer a fully qualified enum name.
InsertionResultState = InsertionState
ResultState = InsertionState
