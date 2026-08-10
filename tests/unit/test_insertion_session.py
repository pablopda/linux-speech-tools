"""State-machine coverage for backend-neutral insertion safety policy."""

from types import SimpleNamespace
from unittest import mock

from src.stt.insertion_session import (
    FinalOnlyInsertionAdapter,
    InsertionSession,
    InsertionState,
    result,
)


class FakeTransport:
    mode = "fake-insert"
    backend = "fake"
    supports_submit = True
    safe_fallback = True

    def __init__(self):
        self.calls = []
        self.update_state = InsertionState.DISPATCHED_UNCONFIRMED
        self.final_state = InsertionState.DISPATCHED_UNCONFIRMED
        self.submit_state = InsertionState.DISPATCHED_UNCONFIRMED

    def update(self, text, revision=None):
        self.calls.append(("update", revision))
        return result(self.update_state, self.backend, revision=revision)

    def finalize(self, text, revision=None):
        self.calls.append(("finalize", revision))
        return result(self.final_state, self.backend, revision=revision)

    def fallback(self, text, revision=None):
        self.calls.append(("fallback", revision))
        return result(
            InsertionState.CLIPBOARD_FALLBACK,
            "clipboard",
            revision=revision,
        )

    def submit(self, revision=None):
        self.calls.append(("submit", revision))
        return result(self.submit_state, self.backend, revision=revision)

    def cancel(self, revision=None):
        self.calls.append(("cancel", revision))
        return result(InsertionState.CANCELLED, self.backend, revision=revision)

    def close(self):
        self.calls.append(("close", None))


def test_stale_and_duplicate_updates_have_no_transport_side_effect():
    transport = FakeTransport()
    session = InsertionSession(transport)

    assert session.update("first", 2).state == InsertionState.DISPATCHED_UNCONFIRMED
    assert session.update("duplicate", 2).state == InsertionState.STALE
    assert session.update("older", 1).state == InsertionState.STALE

    assert transport.calls == [("update", 2)]


def test_same_revision_can_transition_from_update_to_idempotent_finalize():
    transport = FakeTransport()
    session = InsertionSession(transport)

    session.update("partial", 7)
    first = session.finalize("final", 7)
    repeated = session.finalize("must not be dispatched", 7)

    assert first is repeated
    assert first.state == InsertionState.DISPATCHED_UNCONFIRMED
    assert transport.calls == [("update", 7), ("finalize", 7)]
    assert session.finalize("newer final", 8).state == InsertionState.REJECTED
    assert transport.calls == [("update", 7), ("finalize", 7)]


def test_failed_before_dispatch_uses_safe_fallback_once():
    transport = FakeTransport()
    transport.final_state = InsertionState.FAILED_BEFORE_DISPATCH
    session = InsertionSession(transport)

    first = session.finalize("private prompt", 1)
    repeated = session.finalize("private prompt", 1)

    assert first.state == InsertionState.CLIPBOARD_FALLBACK
    assert repeated is first
    assert transport.calls == [("finalize", 1), ("fallback", 1)]
    assert session.can_submit() is False
    assert session.submit().state == InsertionState.REJECTED
    assert ("submit", 1) not in transport.calls


def test_ambiguous_dispatch_locks_insertion_and_never_submits():
    transport = FakeTransport()
    transport.update_state = InsertionState.AMBIGUOUS_AFTER_DISPATCH
    session = InsertionSession(transport)

    ambiguous = session.update("private prompt", 1)
    fallback = session.finalize("private final", 2)

    assert ambiguous.state == InsertionState.AMBIGUOUS_AFTER_DISPATCH
    assert session.insertion_locked is True
    assert fallback.state == InsertionState.CLIPBOARD_FALLBACK
    assert transport.calls == [("update", 1), ("fallback", 2)]
    assert session.can_submit() is False
    assert session.submit().state == InsertionState.REJECTED
    assert not any(call[0] == "submit" for call in transport.calls)


def test_unknown_exception_after_possible_dispatch_is_ambiguous_without_fallback():
    class RaisingTransport(FakeTransport):
        def finalize(self, text, revision=None):
            self.calls.append(("possible-dispatch", revision))
            raise RuntimeError("completion was lost")

    transport = RaisingTransport()
    session = InsertionSession(transport)

    operation = session.finalize("private prompt", 1)

    assert operation.state == InsertionState.AMBIGUOUS_AFTER_DISPATCH
    assert session.insertion_locked is True
    assert transport.calls == [("possible-dispatch", 1)]


def test_submit_requires_eligible_final_result_and_current_target():
    transport = FakeTransport()
    focus = {"matches": True}
    session = InsertionSession(
        transport,
        target_token="window-1",
        target_guard=lambda: focus["matches"],
    )
    session.finalize("private prompt", 1)

    focus["matches"] = False
    rejected = session.submit()
    assert rejected.state == InsertionState.REJECTED
    assert rejected.target_token_match is False
    assert not any(call[0] == "submit" for call in transport.calls)


def test_submit_is_dispatched_at_most_once_by_the_session():
    transport = FakeTransport()
    session = InsertionSession(transport, target_guard=lambda: True)
    session.finalize("private prompt", 1)

    assert session.submit().state == InsertionState.DISPATCHED_UNCONFIRMED
    assert session.submit().state == InsertionState.REJECTED
    assert [call[0] for call in transport.calls].count("submit") == 1


def test_second_submit_guard_failure_permanently_latches_focus_drift():
    transport = FakeTransport()
    focus_results = iter([True, False])
    session = InsertionSession(
        transport,
        target_token="x11-window-1",
        target_guard=lambda: next(focus_results),
    )
    session.finalize("private prompt", 1)

    first = session.submit()
    second = session.submit()

    assert first.state == InsertionState.REJECTED
    assert first.target_token_match is False
    assert session.insertion_locked is True
    assert second.state == InsertionState.REJECTED
    assert not any(call[0] == "submit" for call in transport.calls)


def test_submit_without_a_target_guard_fails_closed():
    transport = FakeTransport()
    session = InsertionSession(transport, target_token="window-1")
    session.finalize("private prompt", 1)

    assert session.can_submit() is False
    assert session.submit().state == InsertionState.REJECTED
    assert not any(call[0] == "submit" for call in transport.calls)


def test_cancel_and_close_prevent_later_transport_calls():
    transport = FakeTransport()
    session = InsertionSession(transport)

    assert session.cancel().state == InsertionState.CANCELLED
    assert session.update("private prompt", 1).state == InsertionState.CANCELLED
    assert session.finalize("private prompt", 1).state == InsertionState.CANCELLED
    session.close()
    session.close()

    assert transport.calls == [("cancel", None), ("close", None)]


def test_result_serialization_contains_no_target_or_text_fields():
    transport = FakeTransport()
    session = InsertionSession(transport, target_token="sensitive-window-title")
    serialized = session.finalize("secret transcript", 1).to_dict()

    assert serialized["state"] == "dispatched-unconfirmed"
    assert "text" not in serialized
    assert "target_token" not in serialized
    assert "secret transcript" not in repr(serialized)
    assert "sensitive-window-title" not in repr(serialized)


def test_authorized_update_and_finalize_stamp_target_match():
    update_transport = FakeTransport()
    update_transport.requires_target_match = True
    update_transport.destructive_updates = True
    update_session = InsertionSession(
        update_transport,
        target_token="window-1",
        target_guard=lambda: True,
    )
    assert update_session.update("partial", 1).target_token_match is True

    final_transport = FakeTransport()
    final_transport.requires_target_match = True
    final_session = InsertionSession(
        final_transport,
        target_token="window-1",
        target_guard=lambda: True,
    )
    assert final_session.finalize("final", 1).target_token_match is True


def test_paste_rechecks_focus_after_clipboard_io_and_never_dispatches_to_new_target():
    from src.stt.prompt_delivery import PasteRenderer

    focus = {"matches": True}

    class Clipboard:
        tool = "fake"

        def write(self, text):
            focus["matches"] = False
            return True

    input_controller = mock.Mock()
    renderer = PasteRenderer(
        "ctrl-v",
        clipboard=Clipboard(),
        input_controller=input_controller,
        focus_guard=lambda: focus["matches"],
    )
    session = InsertionSession(
        renderer,
        target_token="window-1",
        target_guard=lambda: focus["matches"],
    )

    operation = session.finalize("private prompt", 1)

    assert operation.state == InsertionState.CLIPBOARD_FALLBACK
    assert operation.target_token_match is False
    assert session.insertion_locked is True
    input_controller.send_paste_key_result.assert_not_called()


def test_paste_submit_rechecks_inside_transport_and_latches_failure():
    from src.stt.prompt_delivery import PasteRenderer

    focus_results = iter([True, True, True, True, True, False])

    class Clipboard:
        tool = "fake"

        def write(self, text):
            return True

    input_controller = mock.Mock()
    input_controller.send_paste_key_result.return_value = result(
        InsertionState.DISPATCHED_UNCONFIRMED, "fake"
    )
    renderer = PasteRenderer(
        "ctrl-v",
        clipboard=Clipboard(),
        input_controller=input_controller,
        focus_guard=lambda: next(focus_results),
    )
    session = InsertionSession(
        renderer,
        target_token="window-1",
        target_guard=lambda: next(focus_results),
    )
    assert session.finalize("private prompt", 1)

    submitted = session.submit()

    assert submitted.state == InsertionState.REJECTED
    assert submitted.target_token_match is False
    assert session.insertion_locked is True
    input_controller.send_key_combo_result.assert_not_called()


def test_live_type_restores_only_while_it_still_owns_the_clipboard():
    from src.stt.prompt_delivery import LiveTypeRenderer

    class Clipboard:
        tool = "fake"

        def __init__(self):
            self.value = "original"
            self.writes = []

        def read(self):
            return self.value

        def write(self, text):
            self.value = text
            self.writes.append(text)
            return True

    input_controller = mock.Mock()
    input_controller.send_paste_key_result.return_value = result(
        InsertionState.DISPATCHED_UNCONFIRMED, "fake"
    )
    clipboard = Clipboard()
    renderer = LiveTypeRenderer(
        "ctrl-v",
        clipboard=clipboard,
        input_controller=input_controller,
        focus_guard=lambda: True,
    )
    session = InsertionSession(
        renderer, target_token="window-1", target_guard=lambda: True
    )
    session.update("dictated", 1)
    clipboard.value = "new user clipboard"

    session.close()

    assert clipboard.value == "new user clipboard"
    assert clipboard.writes == ["dictated"]


def test_live_type_transport_focus_failure_is_latched_by_session():
    from src.stt.prompt_delivery import LiveTypeRenderer

    focus = {"matches": True}

    class Clipboard:
        tool = "fake"

        def read(self):
            return "original"

        def write(self, text):
            focus["matches"] = False
            return True

    input_controller = mock.Mock()
    renderer = LiveTypeRenderer(
        "ctrl-v",
        clipboard=Clipboard(),
        input_controller=input_controller,
        focus_guard=lambda: focus["matches"],
    )
    session = InsertionSession(
        renderer,
        target_token="window-1",
        target_guard=lambda: focus["matches"],
    )

    operation = session.update("dictated", 1)

    assert operation.state == InsertionState.CLIPBOARD_FALLBACK
    assert operation.target_token_match is False
    assert session.insertion_locked is True
    input_controller.send_paste_key_result.assert_not_called()


def test_live_type_restores_original_clipboard_when_owned_value_is_unchanged():
    from src.stt.prompt_delivery import LiveTypeRenderer

    clipboard = mock.Mock(tool="fake")
    clipboard.read.side_effect = ["original", "dictated"]
    clipboard.write.return_value = True
    input_controller = mock.Mock()
    input_controller.send_paste_key_result.return_value = result(
        InsertionState.DISPATCHED_UNCONFIRMED, "fake"
    )
    renderer = LiveTypeRenderer(
        "ctrl-v",
        clipboard=clipboard,
        input_controller=input_controller,
        focus_guard=lambda: True,
    )
    session = InsertionSession(
        renderer, target_token="window-1", target_guard=lambda: True
    )
    session.update("dictated", 1)

    session.close()

    assert clipboard.write.call_args_list == [
        mock.call("dictated"),
        mock.call("original"),
    ]


def test_final_only_adapter_uses_independent_session_per_utterance():
    transport = FakeTransport()
    adapter = FinalOnlyInsertionAdapter(transport)

    first = adapter.insert("first private utterance")
    second = adapter.insert("second private utterance")

    assert first.revision == 1
    assert second.revision == 2
    assert first.session_id != second.session_id
    assert [call for call in transport.calls if call[0] == "finalize"] == [
        ("finalize", 1),
        ("finalize", 2),
    ]


def test_prompt_status_redacts_sensitive_focus_metadata():
    from src.stt.prompt_dictation import PromptDictation

    dictation = PromptDictation.__new__(PromptDictation)
    dictation.profile = "codex"
    dictation.output = "live-type"
    dictation.submit = "never"
    dictation.engine = "faster-whisper"
    dictation.renderer = InsertionSession(FakeTransport())
    dictation.target = SimpleNamespace(
        kind="codex",
        confidence="high",
        source="gnome-focus",
        title="secret repository title",
        pid=4242,
        app_id="secret.app.identifier",
        wm_class="secret-window-class",
        window_id="secret-window-token",
    )

    status = dictation.status_fields()

    assert status["target_kind"] == "codex"
    assert status["target_confidence"] == "high"
    assert status["target_source"] == "gnome-focus"
    serialized = repr(status)
    for sensitive in (
        "secret repository title",
        "4242",
        "secret.app.identifier",
        "secret-window-class",
        "secret-window-token",
    ):
        assert sensitive not in serialized


def test_voice_submit_requires_an_exact_standalone_control_utterance():
    from src.stt.prompt_dictation import PromptDictation

    dictation = PromptDictation.__new__(PromptDictation)
    dictation.submit = "voice-command"
    dictation._voice_submit_requested = False
    dictation.renderer = mock.Mock()
    dictation.renderer.submit.return_value = result(
        InsertionState.DISPATCHED_UNCONFIRMED, "fake"
    )

    assert dictation.maybe_submit("please make sure we do not submit") is None
    assert dictation.maybe_submit("explain the method named submit") is None
    dictation.renderer.submit.assert_not_called()

    submitted = dictation.maybe_submit("Submit!")
    assert submitted.state == InsertionState.DISPATCHED_UNCONFIRMED
    dictation.renderer.submit.assert_called_once_with()


def test_voice_control_utterance_is_not_appended_to_prompt_text():
    from src.stt.prompt_dictation import PromptDictation

    dictation = PromptDictation.__new__(PromptDictation)
    dictation.submit = "voice-command"
    dictation.confirmed_parts = ["keep this prompt"]
    dictation._voice_submit_requested = False
    dictation.renderer = mock.Mock()

    assert dictation.accept_final("send it.") is True
    assert dictation.confirmed_parts == ["keep this prompt"]
    assert dictation._voice_submit_requested is True
    dictation.renderer.update.assert_not_called()


def _prompt_dictation_fixture(*, submit, submit_result=None):
    from src.stt.prompt_dictation import PromptDictation

    dictation = PromptDictation.__new__(PromptDictation)
    dictation.profile = "codex"
    dictation.output = "paste"
    dictation.submit = submit
    dictation.engine = "faster-whisper"
    dictation.target = SimpleNamespace(
        kind="codex", confidence="high", source="test"
    )
    dictation.confirmed_parts = ["completed prompt"]
    dictation.revision = 0
    dictation._voice_submit_requested = False
    dictation.session = mock.Mock()
    dictation.session.run.return_value = True
    dictation.renderer = mock.Mock(mode="paste")
    dictation.renderer.finalize.return_value = result(
        InsertionState.DISPATCHED_UNCONFIRMED, "fake", revision=1
    )
    if submit_result is not None:
        dictation.renderer.submit.return_value = submit_result
    return dictation


def test_submit_ambiguity_warns_returns_nonzero_and_refreshes_status_once():
    from src.stt import prompt_dictation

    dictation = _prompt_dictation_fixture(
        submit="always",
        submit_result=result(
            InsertionState.AMBIGUOUS_AFTER_DISPATCH, "fake", revision=1
        ),
    )

    with mock.patch.object(prompt_dictation, "notify") as notify:
        exit_code = dictation.run()

    assert exit_code == 1
    dictation.renderer.submit.assert_called_once_with()
    dictation.session.set_status.assert_called_once_with(
        "error", error="prompt submission failed"
    )
    assert "was not retried" in notify.call_args.args[1]
    dictation.renderer.close.assert_called_once_with()


def test_no_requested_submit_remains_success_and_refreshes_final_status():
    from src.stt import prompt_dictation

    dictation = _prompt_dictation_fixture(submit="never")

    with mock.patch.object(prompt_dictation, "notify") as notify:
        exit_code = dictation.run()

    assert exit_code == 0
    dictation.renderer.submit.assert_not_called()
    dictation.session.set_status.assert_called_once_with("idle")
    assert notify.call_args.args[1] == "Prompt text is ready."
