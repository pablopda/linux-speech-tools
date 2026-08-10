"""In-process ownership and audio-resource supervision for voice sessions.

The manager owns at most one preconfigured :class:`VoiceSession`.  It never
constructs an underconfigured Ask or Act session, and starting a session does
not implicitly open the microphone or speaker.  Client mutations require the
opaque token returned once by :meth:`SessionManager.start`.
"""

from __future__ import annotations

import hmac
import secrets
import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Iterable, List, Mapping, Optional, Tuple

from .models import (
    FailureCode,
    Outcome,
    Result,
    SessionLifecycle,
    SessionStatus,
    TERMINAL_LIFECYCLES,
)
from .session import VoiceSession


Observer = Callable[["ManagerStatus"], None]


def _base_status(session: VoiceSession) -> SessionStatus:
    """Read the base session state without invoking a subclass override."""
    return VoiceSession.status.__get__(session, VoiceSession)


def _base_closed(session: VoiceSession) -> bool:
    return bool(VoiceSession.closed.__get__(session, VoiceSession))


@dataclass(frozen=True)
class ManagerStatus:
    """A content-free snapshot suitable for status APIs and observers."""

    closed: bool
    active: bool
    sequence: int
    session_status: Optional[Mapping[str, Any]] = field(default=None, repr=False)
    microphone_leased: bool = False
    speaker_leased: bool = False

    def to_status_dict(self) -> Mapping[str, Any]:
        session = dict(self.session_status) if self.session_status is not None else None
        return {
            "schema_version": 1,
            "closed": self.closed,
            "active": self.active,
            "sequence": self.sequence,
            "session": session,
            "resources": {
                "microphone_leased": self.microphone_leased,
                "speaker_leased": self.speaker_leased,
            },
        }


class StartResult:
    """Start result whose explicit serializer and representation omit authority."""

    __slots__ = ("result", "status", "_owner_token")

    def __init__(
        self,
        result: Result,
        status: ManagerStatus,
        owner_token: Optional[str] = None,
    ) -> None:
        self.result = result
        self.status = status
        self._owner_token = owner_token

    @property
    def owner_token(self) -> Optional[str]:
        return self._owner_token

    @property
    def ok(self) -> bool:
        return self.result.ok

    def to_status_dict(self) -> Mapping[str, Any]:
        return {
            "result": dict(self.result.to_status_dict()),
            "status": dict(self.status.to_status_dict()),
        }

    def __repr__(self) -> str:
        state = "present" if self._owner_token is not None else "absent"
        return "StartResult(result={!r}, status={!r}, owner_token=<{}>)".format(
            self.result, self.status, state
        )


class SessionManager:
    """Own one foreground session and explicit microphone/speaker leases."""

    def __init__(
        self,
        *,
        status_callback: Optional[Observer] = None,
        observers: Iterable[Observer] = (),
    ) -> None:
        checked = []  # type: List[Observer]
        for observer in observers:
            if not callable(observer):
                raise TypeError("observers must be callable")
            checked.append(observer)
        if status_callback is not None:
            if not callable(status_callback):
                raise TypeError("status_callback must be callable")
            checked.append(status_callback)

        self._lock = threading.RLock()
        self._observers: Tuple[Observer, ...] = tuple(checked)
        self._notification_queue: List[ManagerStatus] = []
        self._publishing = False
        self._session: Optional[VoiceSession] = None
        self._session_callback: Optional[Callable[[SessionStatus], None]] = None
        self._session_status: Optional[Mapping[str, Any]] = None
        self._closing_session = False
        self._owner_token: Optional[str] = None
        self._released_token: Optional[str] = None
        self._released_result: Optional[Result] = None
        self._microphone_leased = False
        self._speaker_leased = False
        self._closed = False
        self._close_result: Optional[Result] = None
        self._sequence = 0

    @property
    def status(self) -> ManagerStatus:
        with self._lock:
            return self._status_locked()

    def get_status(self) -> ManagerStatus:
        return self.status

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def add_observer(self, observer: Observer) -> None:
        if not callable(observer):
            raise TypeError("observer must be callable")
        with self._lock:
            if not any(existing is observer for existing in self._observers):
                self._observers = self._observers + (observer,)

    def remove_observer(self, observer: Observer) -> bool:
        if not callable(observer):
            raise TypeError("observer must be callable")
        with self._lock:
            for index, existing in enumerate(self._observers):
                if existing is observer:
                    self._observers = (
                        self._observers[:index] + self._observers[index + 1 :]
                    )
                    return True
            return False

    def start(self, session: VoiceSession) -> StartResult:
        """Own and start one fully configured pending session."""
        if not isinstance(session, VoiceSession):
            with self._lock:
                return StartResult(
                    self._invalid_request_locked(), self._status_locked()
                )

        with self._lock:
            if self._closed:
                return StartResult(self._closed_failure_locked(), self._status_locked())
            if self._session is not None or self._closing_session:
                return StartResult(self._busy_locked(), self._status_locked())
            if (
                _base_closed(session)
                or _base_status(session).lifecycle is not SessionLifecycle.PENDING
            ):
                return StartResult(
                    self._invalid_request_locked(), self._status_locked()
                )

            owner_token = secrets.token_urlsafe(32)

            def session_changed(
                status: SessionStatus, owned: VoiceSession = session
            ) -> None:
                self._session_changed(owned, status)

            self._session = session
            self._session_callback = session_changed
            self._owner_token = owner_token
            self._released_token = None
            self._released_result = None
            self._microphone_leased = False
            self._speaker_leased = False
            try:
                session.add_status_callback(session_changed)
                # Reconcile after registration.  Another thread may have
                # started the session between the initial pending check and
                # callback installation; reading now closes that missed-event
                # window without replacing later callback updates.
                current_status = _base_status(session)
                self._session_status = MappingProxyType(
                    dict(current_status.to_status_dict())
                )
                if current_status.lifecycle is not SessionLifecycle.PENDING:
                    self._changed_locked()
            except BaseException as error:
                _cleanup_ok, cleanup_control = self._forget_session_locked(
                    close_session=True
                )
                self._changed_locked()
                if not isinstance(error, Exception):
                    raise
                if cleanup_control is not None:
                    raise cleanup_control
                return StartResult(
                    self._internal_failure_locked(), self._status_locked()
                )

        try:
            started = session.start()
        except BaseException as error:
            with self._lock:
                if self._session is session:
                    _cleanup_ok, cleanup_control = self._forget_session_locked(
                        close_session=True
                    )
                    self._changed_locked()
                else:
                    cleanup_control = None
                failed = StartResult(
                    self._internal_failure_locked(), self._status_locked()
                )
            if not isinstance(error, Exception):
                raise
            if cleanup_control is not None:
                raise cleanup_control
            return failed
        with self._lock:
            if type(started) is not Result:
                if self._session is session:
                    _cleanup_ok, cleanup_control = self._forget_session_locked(
                        close_session=True
                    )
                    self._changed_locked()
                else:
                    cleanup_control = None
                failed = StartResult(
                    self._internal_failure_locked(), self._status_locked()
                )
                if cleanup_control is not None:
                    raise cleanup_control
                return failed
            still_owned = (
                not self._closed
                and self._session is session
                and self._owner_token is not None
                and hmac.compare_digest(self._owner_token, owner_token)
            )
            # A pre-existing session observer may synchronously advance the
            # just-started session to another valid non-terminal activity.
            # VoiceSession correctly reports that its original transition was
            # superseded; the manager still owns a successfully running
            # session and must not tear it down as a failed start.
            if (
                not started.ok
                and still_owned
                and _base_status(session).lifecycle is not SessionLifecycle.PENDING
                and _base_status(session).lifecycle not in TERMINAL_LIFECYCLES
            ):
                started = Result(
                    Outcome.COMPLETED,
                    revision=_base_status(session).revision,
                )
            if not started.ok or not still_owned:
                if still_owned:
                    _cleanup_ok, cleanup_control = self._forget_session_locked(
                        close_session=True
                    )
                    self._changed_locked()
                    if cleanup_control is not None:
                        raise cleanup_control
                result = (
                    started
                    if not started.ok
                    else self._closed_failure_locked()
                    if self._closed
                    else self._invalid_transition_locked()
                )
                return StartResult(result, self._status_locked())
            return StartResult(started, self._status_locked(), owner_token)

    def session_for_owner(self, owner_token: object) -> Optional[VoiceSession]:
        """Return the in-process session only to its exact bearer-token owner."""
        with self._lock:
            if self._authorize_locked(owner_token) is not None:
                return None
            return self._session

    def acquire_microphone(self, owner_token: object) -> Result:
        return self._set_resource(owner_token, "microphone", True)

    def release_microphone(self, owner_token: object) -> Result:
        return self._set_resource(owner_token, "microphone", False)

    def acquire_speaker(self, owner_token: object) -> Result:
        return self._set_resource(owner_token, "speaker", True)

    def release_speaker(self, owner_token: object) -> Result:
        return self._set_resource(owner_token, "speaker", False)

    def acquire_resource(self, owner_token: object, resource: object) -> Result:
        if resource == "microphone":
            return self.acquire_microphone(owner_token)
        if resource == "speaker":
            return self.acquire_speaker(owner_token)
        with self._lock:
            return (
                self._closed_failure_locked()
                if self._closed
                else self._invalid_request_locked()
            )

    def release_resource(self, owner_token: object, resource: object) -> Result:
        if resource == "microphone":
            return self.release_microphone(owner_token)
        if resource == "speaker":
            return self.release_speaker(owner_token)
        with self._lock:
            return (
                self._closed_failure_locked()
                if self._closed
                else self._invalid_request_locked()
            )

    def cancel(self, owner_token: object) -> Result:
        with self._lock:
            unavailable = self._authorize_locked(owner_token)
            if unavailable is not None:
                return unavailable
            assert self._session is not None
            session = self._session
        result: Optional[Result] = None
        control_error: Optional[BaseException] = None
        fallback_control: Optional[BaseException] = None
        invalid_result = False
        try:
            result = session.cancel()
        except BaseException as error:
            control_error = error
        if control_error is None and (
            type(result) is not Result
            or _base_status(session).lifecycle not in TERMINAL_LIFECYCLES
        ):
            invalid_result = True
        if control_error is not None or invalid_result:
            if _base_status(session).lifecycle not in TERMINAL_LIFECYCLES:
                try:
                    VoiceSession.cancel(session)
                except BaseException as fallback_error:
                    if not isinstance(fallback_error, Exception):
                        fallback_control = fallback_error
        with self._lock:
            # VoiceSession publishes its terminal status in the normal path,
            # but an injected/subclass collaborator can raise before doing so.
            # Resource ownership is a manager invariant and is cleared after
            # reacquiring the lock.  The arbitrary collaborator ran entirely
            # outside this lock, so status/host controls cannot deadlock on its
            # own callbacks.
            if self._session is session:
                still_leased = self._microphone_leased or self._speaker_leased
                self._microphone_leased = False
                self._speaker_leased = False
                if still_leased:
                    self._changed_locked()
        if control_error is not None:
            if not isinstance(control_error, Exception):
                raise control_error
            if fallback_control is not None:
                raise fallback_control
            return Result(
                Outcome.FAILED,
                FailureCode.INTERNAL,
                revision=_base_status(session).revision,
            )
        if invalid_result:
            return Result(
                Outcome.FAILED,
                FailureCode.INTERNAL,
                revision=_base_status(session).revision,
            )
        assert result is not None
        return result

    def release(self, owner_token: object) -> Result:
        """Close and forget the active session; exact replay is idempotent."""
        with self._lock:
            if self._closed:
                return self._closed_failure_locked()
            if self._session is None:
                if (
                    isinstance(owner_token, str)
                    and owner_token.isascii()
                    and self._released_token is not None
                    and hmac.compare_digest(owner_token, self._released_token)
                ):
                    if self._released_result is not None:
                        return self._released_result
                    if self._closing_session:
                        return self._busy_locked()
                return self._invalid_request_locked()
            unavailable = self._authorize_locked(owner_token)
            if unavailable is not None:
                return unavailable
            assert self._owner_token is not None
            revision = _base_status(self._session).revision
            released_token = self._owner_token
            # Retain the exact authority while close runs outside the manager
            # lock.  A concurrent retry receives BUSY/retryable rather than a
            # misleading authentication failure, then exact replay once the
            # terminal result is installed.
            self._released_token = released_token
            self._released_result = None
            cleanup_ok, close_error = self._forget_session_locked(close_session=True)
            if self._closed:
                return self._closed_failure_locked()
            result = (
                Result(Outcome.COMPLETED, revision=revision)
                if cleanup_ok
                else self._internal_failure_locked()
            )
            self._released_result = result
            self._changed_locked()
            if close_error is not None:
                raise close_error
            return result

    def close(self, owner_token: object) -> Result:
        """Permanently close an active manager for its exact session owner."""
        with self._lock:
            if self._closing_session:
                return self._busy_locked()
            if self._closed:
                if self._close_result is not None:
                    return self._close_result
                return self._busy_locked()
            unavailable = self._authorize_locked(owner_token)
            if unavailable is not None:
                return unavailable
            return self._shutdown_locked()

    def shutdown(self) -> Result:
        """Host-only permanent shutdown; do not expose as an unowned client call."""
        with self._lock:
            if self._closing_session:
                return self._busy_locked()
            if self._closed:
                if self._close_result is not None:
                    return self._close_result
                return self._busy_locked()
            return self._shutdown_locked()

    def _shutdown_locked(self) -> Result:
        revision = self._revision_locked()
        # Publish the permanent state before closing the session.  Session
        # callbacks can re-enter the manager during close; they must observe a
        # closed host and may never start a replacement behind this shutdown.
        self._closed = True
        self._close_result = None
        cleanup_ok, close_error = self._forget_session_locked(close_session=True)
        self._released_token = None
        self._released_result = None
        self._close_result = (
            Result(Outcome.COMPLETED, revision=revision)
            if cleanup_ok
            else self._internal_failure_locked()
        )
        self._changed_locked()
        if close_error is not None:
            raise close_error
        assert self._close_result is not None
        return self._close_result

    def _set_resource(self, owner_token: object, resource: str, leased: bool) -> Result:
        with self._lock:
            unavailable = self._authorize_locked(owner_token)
            if unavailable is not None:
                return unavailable
            assert self._session is not None
            attribute = "_{}_leased".format(resource)
            current = bool(getattr(self, attribute))
            if current is leased:
                return Result(
                    Outcome.COMPLETED, revision=_base_status(self._session).revision
                )
            if leased and _base_status(self._session).lifecycle in TERMINAL_LIFECYCLES:
                return Result(
                    Outcome.REJECTED,
                    FailureCode.INVALID_TRANSITION,
                    revision=_base_status(self._session).revision,
                )
            setattr(self, attribute, leased)
            result = Result(
                Outcome.COMPLETED, revision=_base_status(self._session).revision
            )
            self._changed_locked()
            unavailable = self._authorize_locked(owner_token)
            if unavailable is not None:
                return unavailable
            if bool(getattr(self, attribute)) is not leased:
                return self._invalid_transition_locked()
            return result

    def _session_changed(self, session: VoiceSession, status: SessionStatus) -> None:
        with self._lock:
            if self._closed or self._session is not session:
                return
            effective = _base_status(session) if _base_closed(session) else status
            if self._session_status is not None:
                current_sequence = int(self._session_status.get("sequence", -1))
                if effective.sequence <= current_sequence:
                    return
            self._session_status = MappingProxyType(dict(effective.to_status_dict()))
            if effective.lifecycle in TERMINAL_LIFECYCLES:
                self._microphone_leased = False
                self._speaker_leased = False
            self._changed_locked()

    def _forget_session_locked(
        self, *, close_session: bool
    ) -> Tuple[bool, Optional[BaseException]]:
        session = self._session
        callback = self._session_callback
        self._session = None
        self._session_callback = None
        self._session_status = None
        self._owner_token = None
        self._microphone_leased = False
        self._speaker_leased = False
        if session is not None and callback is not None:
            try:
                session.remove_status_callback(callback)
            except BaseException:
                pass
        cleanup_ok = True
        close_error: Optional[BaseException] = None
        if close_session and session is not None:
            self._closing_session = True
            release_all = getattr(self._lock, "_release_save", None)
            restore_all = getattr(self._lock, "_acquire_restore", None)
            if callable(release_all) and callable(restore_all):
                state = release_all()
                fully_released = True
            else:  # pragma: no cover - alternate Python lock implementation
                self._lock.release()
                state = None
                fully_released = False
            try:
                session.close()
                if not _base_closed(session):
                    VoiceSession.close(session)
                cleanup_ok = _base_closed(session)
            except BaseException as error:
                if not isinstance(error, Exception):
                    close_error = error
                # A subclass may fail before delegating to the base cleanup.
                # Bypass the override once so volatile transcript/context and
                # action authority are still erased.  The caller receives an
                # internal failure if even the base contract cannot close it.
                if not _base_closed(session):
                    try:
                        VoiceSession.close(session)
                    except BaseException as fallback_error:
                        if not isinstance(fallback_error, Exception):
                            close_error = fallback_error
                cleanup_ok = _base_closed(session)
            finally:
                if fully_released:
                    restore_all(state)
                else:  # pragma: no cover
                    self._lock.acquire()
                self._closing_session = False
        return cleanup_ok, close_error

    def _authorize_locked(self, owner_token: object) -> Optional[Result]:
        if self._closed:
            return self._closed_failure_locked()
        if (
            self._session is None
            or self._owner_token is None
            or not isinstance(owner_token, str)
            or not owner_token.isascii()
            or not hmac.compare_digest(owner_token, self._owner_token)
        ):
            return self._invalid_request_locked()
        return None

    def _status_locked(self) -> ManagerStatus:
        return ManagerStatus(
            closed=self._closed,
            active=self._session is not None,
            sequence=self._sequence,
            session_status=self._session_status,
            microphone_leased=self._microphone_leased,
            speaker_leased=self._speaker_leased,
        )

    def _changed_locked(self) -> ManagerStatus:
        self._sequence += 1
        status = self._status_locked()
        self._publish_locked(status)
        return status

    def _publish_locked(self, status: ManagerStatus) -> None:
        self._notification_queue.append(status)
        if self._publishing:
            return
        self._publishing = True
        try:
            while self._notification_queue:
                pending = self._notification_queue.pop(0)
                observers = self._observers
                release_all = getattr(self._lock, "_release_save", None)
                restore_all = getattr(self._lock, "_acquire_restore", None)
                if callable(release_all) and callable(restore_all):
                    state = release_all()
                    full_release = True
                else:  # pragma: no cover - alternate Python lock implementation
                    self._lock.release()
                    state = None
                    full_release = False
                try:
                    for observer in observers:
                        try:
                            observer(pending)
                        except BaseException:
                            pass
                finally:
                    if full_release:
                        restore_all(state)
                    else:  # pragma: no cover
                        self._lock.acquire()
        finally:
            self._publishing = False

    def _revision_locked(self) -> int:
        return _base_status(self._session).revision if self._session is not None else 0

    def _busy_locked(self) -> Result:
        return Result(
            Outcome.REJECTED,
            FailureCode.BUSY,
            revision=self._revision_locked(),
            retryable=True,
        )

    def _closed_failure_locked(self) -> Result:
        return Result(
            Outcome.REJECTED,
            FailureCode.CLOSED,
            revision=self._revision_locked(),
        )

    def _invalid_request_locked(self) -> Result:
        return Result(
            Outcome.REJECTED,
            FailureCode.INVALID_REQUEST,
            revision=self._revision_locked(),
        )

    def _invalid_transition_locked(self) -> Result:
        return Result(
            Outcome.REJECTED,
            FailureCode.INVALID_TRANSITION,
            revision=self._revision_locked(),
        )

    def _internal_failure_locked(self) -> Result:
        return Result(
            Outcome.FAILED,
            FailureCode.INTERNAL,
            revision=self._revision_locked(),
        )


__all__ = ["ManagerStatus", "Observer", "SessionManager", "StartResult"]
