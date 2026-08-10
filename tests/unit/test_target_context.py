"""Tests for the fail-closed GNOME focus snapshot contract."""

import json
import subprocess
from unittest import mock

from src.stt import target_context


def focus_payload(**overrides):
    payload = {
        "schema_version": 1,
        "shell_session_id": "3fb94383-b10d-48c3-9430-c94957c13b2b",
        "window_sequence": 42,
        "focus_generation": 7,
        "locked": False,
        "app_id": "org.gnome.Ptyxis.desktop",
        "wm_class": "org.gnome.Ptyxis",
        "title": "Terminal",
        "pid": 1234,
        "window_id": "gnome:3fb94383-b10d-48c3-9430-c94957c13b2b:42",
    }
    payload.update(overrides)
    return payload


def context_from(payload):
    return target_context._focus_context_from_payload(payload, source="gnome-focus")


def test_valid_focus_payload_preserves_strong_identity():
    context = context_from(focus_payload())

    assert context.kind == "terminal"
    assert context.schema_version == 1
    assert context.window_sequence == 42
    assert context.focus_generation == 7
    assert context.locked is False


def test_old_payload_without_schema_fails_closed():
    context = context_from({"window_id": "gnome:old:42", "title": "Terminal"})

    assert context == target_context.TargetContext()


def test_mismatched_composite_window_id_fails_closed():
    context = context_from(focus_payload(window_id="gnome:other-session:42"))

    assert context == target_context.TargetContext()


def test_locked_payload_discards_identity_and_metadata():
    context = context_from(focus_payload(locked=True))

    assert context.source == "gnome-focus"
    assert context.locked is True
    assert context.window_id == ""
    assert context.title == ""


def test_boolean_generation_is_not_accepted_as_an_integer():
    assert context_from(focus_payload(focus_generation=True)) == target_context.TargetContext()


def test_focus_match_requires_same_id_session_and_generation():
    expected = context_from(focus_payload())
    current = context_from(focus_payload())

    with mock.patch.object(target_context, "live_focus_context", return_value=current):
        assert target_context.focus_matches(expected)


def test_focus_away_and_back_remains_invalid():
    expected = context_from(focus_payload(focus_generation=7))
    # The stable window ID is unchanged after returning, but two focus changes
    # advanced the generation.
    returned = context_from(focus_payload(focus_generation=9))

    with mock.patch.object(target_context, "live_focus_context", return_value=returned):
        assert not target_context.focus_matches(expected)


def test_focus_match_rejects_lock_and_new_shell_session():
    expected = context_from(focus_payload())
    locked = context_from(focus_payload(locked=True))
    restarted = context_from(focus_payload(
        shell_session_id="new-session",
        window_id="gnome:new-session:42",
    ))

    with mock.patch.object(target_context, "live_focus_context", return_value=locked):
        assert not target_context.focus_matches(expected)
    with mock.patch.object(target_context, "live_focus_context", return_value=restarted):
        assert not target_context.focus_matches(expected)


def test_focus_match_rejects_missing_service():
    expected = context_from(focus_payload())

    with mock.patch.object(
            target_context, "live_focus_context", return_value=target_context.TargetContext()):
        assert not target_context.focus_matches(expected)


def test_gnome_dbus_payload_is_unwrapped_and_validated():
    completed = subprocess.CompletedProcess(
        args=["gdbus"], returncode=0,
        stdout=repr((json.dumps(focus_payload()),)), stderr="")
    with mock.patch.object(target_context.shutil, "which", return_value="/usr/bin/gdbus"), \
            mock.patch.object(target_context.subprocess, "run", return_value=completed):
        context = target_context.gnome_focus_context()

    assert context.window_id.endswith(":42")
    assert context.focus_generation == 7


def test_gnome_dbus_old_payload_is_rejected():
    old = {"app_id": "org.gnome.Ptyxis.desktop", "window_id": "gnome:old:42"}
    completed = subprocess.CompletedProcess(
        args=["gdbus"], returncode=0,
        stdout=repr((json.dumps(old),)), stderr="")
    with mock.patch.object(target_context.shutil, "which", return_value="/usr/bin/gdbus"), \
            mock.patch.object(target_context.subprocess, "run", return_value=completed):
        context = target_context.gnome_focus_context()

    assert context == target_context.TargetContext()


def test_explicit_profile_preserves_focus_authority_fields():
    focused = context_from(focus_payload())
    with mock.patch.object(target_context, "live_focus_context", return_value=focused), \
            mock.patch.dict(target_context.os.environ, {}, clear=True):
        context = target_context.detect_target("codex")

    assert context.kind == "codex"
    assert context.source == "profile"
    assert context.window_id == focused.window_id
    assert context.shell_session_id == focused.shell_session_id
    assert context.focus_generation == focused.focus_generation
    assert context.locked is False


def test_auto_profile_preserves_unclassified_authoritative_gnome_identity():
    focused = context_from(focus_payload(
        app_id="org.gnome.TextEditor.desktop",
        wm_class="org.gnome.TextEditor",
        title="Untitled Document",
    ))
    assert focused.kind == "unknown"

    with mock.patch.object(target_context, "live_focus_context", return_value=focused), \
            mock.patch.dict(target_context.os.environ, {}, clear=True):
        context = target_context.detect_target("auto")

    assert context == focused
    assert context.window_id.endswith(":42")
    assert context.focus_generation == 7


def test_auto_profile_preserves_unclassified_x11_window_identity():
    focused = target_context.TargetContext(
        kind="unknown", source="x11", window_id="12345")

    with mock.patch.object(target_context, "live_focus_context", return_value=focused), \
            mock.patch.dict(target_context.os.environ, {}, clear=True):
        context = target_context.detect_target("auto")

    assert context == focused


def test_auto_profile_preserves_locked_gnome_authority_over_title_fallback():
    locked = context_from(focus_payload(locked=True))

    with mock.patch.object(target_context, "live_focus_context", return_value=locked), \
            mock.patch.dict(
                target_context.os.environ,
                {"PROMPT_DICTATION_TARGET_TITLE": "Codex"},
                clear=True,
            ):
        context = target_context.detect_target("auto")

    assert context == locked
    assert context.locked is True
    assert context.window_id == ""
    assert context.title == ""


def test_old_injected_gnome_payload_fails_closed():
    old = json.dumps({"window_id": "gnome:old:42", "title": "Terminal"})
    with mock.patch.dict(
            target_context.os.environ, {"LST_TARGET_CONTEXT_JSON": old}, clear=True):
        context = target_context.detect_target()

    assert context == target_context.TargetContext()


def test_x11_window_matching_remains_id_based():
    expected = target_context.TargetContext(window_id="123", source="x11")
    current = target_context.TargetContext(window_id="123", source="x11")

    with mock.patch.object(target_context, "live_focus_context", return_value=current):
        assert target_context.focus_matches(expected)
