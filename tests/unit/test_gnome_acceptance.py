"""Hermetic tests for the safety-first GNOME live acceptance harness."""

import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from src.stt import gnome_acceptance as acceptance
from src.stt.target_context import TargetContext


def desktop_snapshot(*, locked=False, engine="xkb:us::eng", enabled=True):
    return {
        "captured_at": "2026-08-10T12:00:00+00:00",
        "session_type": "wayland",
        "desktop": "GNOME",
        "shell_version": "GNOME Shell 50.1",
        "locked": locked,
        "extension": {
            "registered": True,
            "enabled": enabled,
            "state": "active" if enabled else "inactive",
            "version": 2,
        },
        "extension_settings": {
            "enabled": [acceptance.EXTENSION_UUID] if enabled else [],
            "disabled": [],
            "disable_user_extensions": False,
        },
        "input": {
            "engine": engine,
            "sources": [["xkb", "us"]],
            "mru_sources": [["xkb", "us"]],
        },
        "provider": {"owned": enabled, "owned_by_shell": enabled},
    }


def focus_context(
    *,
    session="shell-a",
    sequence=17,
    generation=4,
    app_id="org.gnome.TextEditor.desktop",
    wm_class="org.gnome.TextEditor",
    locked=False,
):
    return TargetContext(
        source="gnome-focus",
        schema_version=1,
        shell_session_id=session,
        window_sequence=0 if locked else sequence,
        focus_generation=generation,
        locked=locked,
        app_id="" if locked else app_id,
        wm_class="" if locked else wm_class,
        title="",
        pid=None,
        window_id="" if locked else "gnome:{}:{}".format(session, sequence),
    )


def sample(**kwargs):
    return acceptance.FocusSample.from_context(focus_context(**kwargs))


class FakeProvider:
    def __init__(self, samples):
        self.samples = list(samples)
        self.calls = 0

    def read(self):
        if not self.samples:
            raise acceptance.AcceptanceError("fake-provider-exhausted")
        self.calls += 1
        return self.samples.pop(0)


def state_path(tmp_path):
    path = tmp_path / "acceptance.json"
    os.chmod(str(tmp_path), 0o700)
    state = acceptance._new_state(
        desktop_snapshot(), {"present": False, "manifest": {}}
    )
    acceptance.save_state(path, state)
    return path


def test_focus_sample_discards_sensitive_fields_from_public_evidence():
    context = focus_context(sequence=424242)
    context.title = "Sensitive repository title"
    context.pid = 4242
    context.shell_session_id = "session-secret"
    context.window_id = "gnome:session-secret:424242"

    public = acceptance.FocusSample.from_context(context).public()
    serialized = json.dumps(public)

    assert "Sensitive repository title" not in serialized
    assert "session-secret" not in serialized
    assert "window_sequence" not in serialized
    assert "pid" not in public
    assert public["session_fingerprint"]
    assert public["window_fingerprint"]


def test_locked_provider_metadata_fails_closed():
    context = focus_context(locked=True)
    context.app_id = "org.gnome.TextEditor"

    with pytest.raises(acceptance.AcceptanceError, match="disclosed"):
        acceptance.FocusSample.from_context(context)


def test_boolean_generation_and_forged_window_id_fail_closed():
    boolean_generation = focus_context()
    boolean_generation.focus_generation = True
    forged_window = focus_context()
    forged_window.window_id = "gnome:other:17"

    with pytest.raises(acceptance.AcceptanceError):
        acceptance.FocusSample.from_context(boolean_generation)
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.FocusSample.from_context(forged_window)


def test_provider_reader_uses_bounded_strict_payload(monkeypatch):
    payload = {
        "schema_version": 1,
        "shell_session_id": "shell-a",
        "window_sequence": 17,
        "focus_generation": 4,
        "locked": False,
        "app_id": "org.gnome.TextEditor.desktop",
        "wm_class": "org.gnome.TextEditor",
        "title": "Sensitive title",
        "pid": 4242,
        "window_id": "gnome:shell-a:17",
    }
    monkeypatch.setattr(
        acceptance, "_run_text", lambda *_args, **_kwargs: repr((json.dumps(payload),))
    )
    monkeypatch.setattr(acceptance, "_name_owner", lambda _name: ":1.shell")

    public = acceptance.ProviderReader().read().public()

    assert public["provider_class"] == "gtk-editor"
    assert "Sensitive title" not in json.dumps(public)
    assert "window_sequence" not in public


def test_provider_reader_rejects_invalid_or_oversize_payload(monkeypatch):
    monkeypatch.setattr(acceptance, "_name_owner", lambda _name: ":1.shell")
    monkeypatch.setattr(acceptance, "_run_text", lambda *_args, **_kwargs: "not-json")
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.ProviderReader().read()

    monkeypatch.setattr(
        acceptance,
        "_run_text",
        lambda *_args, **_kwargs: repr(("x" * (32 * 1024 + 1),)),
    )
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.ProviderReader().read()


def test_provider_reader_rejects_non_shell_and_replaced_owner(monkeypatch):
    payload = json.dumps(
        {
            "schema_version": 1,
            "shell_session_id": "shell-a",
            "window_sequence": 17,
            "focus_generation": 4,
            "locked": False,
            "app_id": "org.gnome.TextEditor.desktop",
            "wm_class": "org.gnome.TextEditor",
            "title": "",
            "pid": None,
            "window_id": "gnome:shell-a:17",
        }
    )
    monkeypatch.setattr(
        acceptance, "_run_text", lambda *_args, **_kwargs: repr((payload,))
    )
    monkeypatch.setattr(
        acceptance,
        "_name_owner",
        lambda name: ":1.spoof"
        if name == acceptance.FOCUS_BUS_NAME
        else ":1.shell",
    )
    with pytest.raises(acceptance.AcceptanceError, match="owner-is-not-stable"):
        acceptance.ProviderReader().read()

    owners = iter((":1.shell", ":1.shell", ":1.replacement", ":1.shell"))
    monkeypatch.setattr(acceptance, "_name_owner", lambda _name: next(owners))
    with pytest.raises(acceptance.AcceptanceError, match="owner-is-not-stable"):
        acceptance.ProviderReader().read()


def test_identity_class_requires_nonconflicting_exact_identity():
    assert acceptance._identity_class(
        "org.gnome.TextEditor.desktop", "org.gnome.TextEditor"
    ) == "gtk-editor"
    assert acceptance._identity_class(
        "org.gnome.TextEditor.desktop", "org.gnome.Ptyxis"
    ) == "conflict"
    assert acceptance._identity_class("org.example.CodeRunner", "") == "other"


def test_start_refuses_locked_session_before_extension_backup(tmp_path, monkeypatch):
    path = tmp_path / "acceptance.json"
    os.chmod(str(tmp_path), 0o700)
    backup = mock.Mock()
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot(locked=True)
    )
    monkeypatch.setattr(acceptance, "_backup_extension", backup)

    with pytest.raises(acceptance.AcceptanceError, match="unlocked"):
        acceptance.start_run(path, acceptance.CONFIRMATIONS["start"])

    backup.assert_not_called()
    assert not path.exists()


def test_missing_confirmation_fails_closed_without_writing(tmp_path):
    path = tmp_path / "acceptance.json"
    with mock.patch.object(acceptance.sys.stdin, "isatty", return_value=False):
        with pytest.raises(acceptance.AcceptanceError, match="confirmation-required"):
            acceptance.start_run(path, "")
    assert not path.exists()


def test_symlink_state_file_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "state"
    link.symlink_to(target)

    with pytest.raises(acceptance.AcceptanceError, match="private-owned-regular"):
        acceptance.load_state(link)


def test_bounded_command_reader_rejects_oversize_and_timeout():
    oversized = acceptance._run_text(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 40000)"]
    )
    timed_out = acceptance._run_text(
        [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05
    )

    assert oversized == ""
    assert timed_out == ""


def test_bounded_command_reader_kills_leader_exited_pipe_holder(tmp_path):
    marker = tmp_path / "descendant-survived"
    program = (
        "import os,time; pid=os.fork(); "
        "(os._exit(0) if pid else "
        "(time.sleep(0.4), open({!r}, 'w').write('bad')))"
    ).format(str(marker))

    assert acceptance._run_text([sys.executable, "-c", program], timeout=0.05) == ""
    time.sleep(0.5)
    assert not marker.exists()


def test_post_poll_oversize_kills_leader_exited_process_group(
    tmp_path, monkeypatch
):
    marker = tmp_path / "post-poll-descendant-survived"

    class PollOnlySelector:
        def register(self, *_args):
            return None

        def unregister(self, *_args):
            return None

        def select(self, timeout):
            time.sleep(min(timeout, 0.001))
            return []

        def close(self):
            return None

    program = (
        "import os,sys,time; pid=os.fork(); "
        "(sys.stdout.write('x'*32000), sys.stdout.flush(), os._exit(0)) "
        "if pid else "
        "(time.sleep(0.05), sys.stdout.write('y'*4096), sys.stdout.flush(), "
        "time.sleep(0.3), open({!r}, 'w').write('bad'))"
    ).format(str(marker))
    monkeypatch.setattr(
        acceptance.selectors, "DefaultSelector", PollOnlySelector
    )

    assert acceptance._run_text(
        [sys.executable, "-c", program], timeout=0.5
    ) == ""
    time.sleep(0.4)
    assert not marker.exists()


def test_keyboard_interrupt_kills_owned_text_process_group(tmp_path, monkeypatch):
    marker = tmp_path / "interrupted-descendant-survived"

    class InterruptSelector:
        def register(self, *_args):
            return None

        def select(self, _timeout):
            raise KeyboardInterrupt

        def close(self):
            return None

    program = (
        "import os,time; pid=os.fork(); "
        "(time.sleep(5) if pid else "
        "(time.sleep(0.3), open({!r}, 'w').write('bad')))"
    ).format(str(marker))
    monkeypatch.setattr(
        acceptance.selectors, "DefaultSelector", InterruptSelector
    )

    with pytest.raises(KeyboardInterrupt):
        acceptance._run_text([sys.executable, "-c", program])
    time.sleep(0.4)
    assert not marker.exists()


def test_selector_setup_abort_kills_owned_text_process_group(tmp_path, monkeypatch):
    marker = tmp_path / "setup-abort-descendant-survived"

    class SetupInterruptSelector:
        def register(self, *_args):
            raise KeyboardInterrupt

        def close(self):
            return None

    program = (
        "import os,time; pid=os.fork(); "
        "(time.sleep(5) if pid else "
        "(time.sleep(0.3), open({!r}, 'w').write('bad')))"
    ).format(str(marker))
    monkeypatch.setattr(
        acceptance.selectors, "DefaultSelector", SetupInterruptSelector
    )

    with pytest.raises(KeyboardInterrupt):
        acceptance._run_text([sys.executable, "-c", program])
    time.sleep(0.4)
    assert not marker.exists()


def test_keyboard_interrupt_kills_owned_quiet_process_group(tmp_path, monkeypatch):
    marker = tmp_path / "quiet-interrupted-descendant-survived"
    real_popen = acceptance.subprocess.Popen

    class InterruptOnceProcess:
        def __init__(self, process):
            self._process = process
            self.pid = process.pid
            self._interrupted = False

        def wait(self, timeout=None):
            if not self._interrupted:
                self._interrupted = True
                raise KeyboardInterrupt
            return self._process.wait(timeout=timeout)

    program = (
        "import os,time; pid=os.fork(); "
        "(time.sleep(5) if pid else "
        "(time.sleep(0.3), open({!r}, 'w').write('bad')))"
    ).format(str(marker))
    monkeypatch.setattr(
        acceptance.subprocess,
        "Popen",
        lambda *args, **kwargs: InterruptOnceProcess(real_popen(*args, **kwargs)),
    )

    with pytest.raises(KeyboardInterrupt):
        acceptance._run_quiet([sys.executable, "-c", program], timeout=5.0)
    time.sleep(0.4)
    assert not marker.exists()


def test_intermediate_state_parent_symlink_is_rejected(tmp_path):
    real = tmp_path / "real"
    nested = real / "nested"
    nested.mkdir(parents=True)
    os.chmod(str(real), 0o700)
    os.chmod(str(nested), 0o700)
    link = tmp_path / "redirect"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(acceptance.AcceptanceError, match="contains-symlink"):
        acceptance.save_state(
            link / "nested" / "state.json",
            acceptance._new_state(
                desktop_snapshot(), {"present": False, "manifest": {}}
            ),
        )


def test_fifo_lock_path_is_rejected_before_open(tmp_path):
    os.chmod(str(tmp_path), 0o700)
    state = tmp_path / "state.json"
    os.mkfifo(str(state.with_name(state.name + ".lock")), 0o600)

    with pytest.raises(acceptance.AcceptanceError, match="private-owned-regular"):
        with acceptance._state_lock(state):
            raise AssertionError("unsafe lock unexpectedly opened")


def test_override_parent_must_be_strictly_private_and_is_not_changed(
    tmp_path, monkeypatch
):
    override_parent = tmp_path / "override"
    override_parent.mkdir(mode=0o750)
    override_parent.chmod(0o750)
    path = override_parent / "state.json"
    monkeypatch.setenv("LST_GNOME_ACCEPTANCE_STATE", str(path))

    with pytest.raises(acceptance.AcceptanceError, match="parent-must-be-private"):
        acceptance.save_state(
            path,
            acceptance._new_state(
                desktop_snapshot(), {"present": False, "manifest": {}}
            ),
        )

    assert stat.S_IMODE(override_parent.stat().st_mode) == 0o750


def test_strict_parent_protects_mode_preserving_extension_backup(
    tmp_path, monkeypatch
):
    os.chmod(str(tmp_path), 0o700)
    state = tmp_path / "acceptance.json"
    extension = tmp_path / "extension"
    extension.mkdir(mode=0o755)
    original = extension / "extension.js"
    original.write_text("private extension source", encoding="utf-8")
    original.chmod(0o644)
    monkeypatch.setattr(acceptance, "_extension_dir", lambda: extension)

    backup_info = acceptance._backup_extension(state)
    backup = acceptance._backup_dir(state)

    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert backup_info["manifest"]["extension.js"]["mode"] == 0o644
    assert acceptance._manifest(backup) == backup_info["manifest"]


def test_fake_provider_lifecycle_releases_name_and_rotates_session(tmp_path, monkeypatch):
    path = state_path(tmp_path)
    desktop = {"enabled": True, "owner": True, "enable_count": 0}

    def set_enabled(enabled):
        desktop["enabled"] = enabled
        desktop["owner"] = enabled
        if enabled:
            desktop["enable_count"] += 1

    def read_provider():
        session = "shell-a" if desktop["enable_count"] == 1 else "shell-b"
        return sample(session=session)

    fake = mock.Mock()
    fake.read.side_effect = read_provider
    monkeypatch.setattr(acceptance, "_set_extension_enabled", set_enabled)
    monkeypatch.setattr(acceptance, "_name_has_owner", lambda _name: desktop["owner"])
    monkeypatch.setattr(
        acceptance,
        "_name_owner",
        lambda name: ":1.shell" if name in {acceptance.FOCUS_BUS_NAME, "org.gnome.Shell"} and desktop["owner"] else "",
    )
    monkeypatch.setattr(
        acceptance,
        "capture_desktop_snapshot",
        lambda: desktop_snapshot(enabled=desktop["enabled"]),
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    state = acceptance.run_lifecycle(
        path, acceptance.CONFIRMATIONS["lifecycle"], reader=fake
    )

    assert state["lifecycle"]["status"] == "passed"
    assert state["lifecycle"]["name_released"] is True
    assert state["lifecycle"]["session_changed"] is True
    assert state["lifecycle"]["input_unchanged"] is True
    serialized = json.dumps(state["lifecycle"])
    assert "shell-a" not in serialized
    assert "shell-b" not in serialized


def test_lifecycle_fails_and_restores_prior_enable_state_on_input_change(
    tmp_path, monkeypatch
):
    path = state_path(tmp_path)
    desktop = {"enabled": False, "owner": False, "enable_count": 0, "captures": 0}

    def set_enabled(enabled):
        desktop["enabled"] = enabled
        desktop["owner"] = enabled
        if enabled:
            desktop["enable_count"] += 1

    fake = mock.Mock()
    fake.read.side_effect = [sample(session="shell-a"), sample(session="shell-b")]

    def capture():
        desktop["captures"] += 1
        engine = "other-engine" if desktop["captures"] >= 2 else "xkb:us::eng"
        return desktop_snapshot(engine=engine, enabled=desktop["enabled"])

    monkeypatch.setattr(acceptance, "_set_extension_enabled", set_enabled)
    monkeypatch.setattr(acceptance, "_name_has_owner", lambda _name: desktop["owner"])
    monkeypatch.setattr(
        acceptance,
        "_name_owner",
        lambda name: ":1.shell" if name in {acceptance.FOCUS_BUS_NAME, "org.gnome.Shell"} and desktop["owner"] else "",
    )
    monkeypatch.setattr(acceptance, "capture_desktop_snapshot", capture)
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    with pytest.raises(acceptance.AcceptanceError, match="input-state"):
        acceptance.run_lifecycle(
            path, acceptance.CONFIRMATIONS["lifecycle"], reader=fake
        )

    assert desktop["enabled"] is False
    assert acceptance.load_state(path)["status"] == "failed"


def test_lifecycle_rejects_owner_replacement_after_second_sample(
    tmp_path, monkeypatch
):
    path = state_path(tmp_path)
    desktop = {"enabled": True, "owner": True}
    fake = mock.Mock()
    fake.read.side_effect = [sample(session="shell-a"), sample(session="shell-b")]
    owners = iter(
        (
            ":1.shell",
            ":1.shell",
            ":1.shell",
            ":1.shell",
            ":1.shell",
            ":1.shell",
            ":1.replacement",
            ":1.shell",
        )
    )

    monkeypatch.setattr(
        acceptance,
        "_set_extension_enabled",
        lambda enabled: desktop.update(enabled=enabled, owner=enabled),
    )
    monkeypatch.setattr(acceptance, "_name_has_owner", lambda _name: desktop["owner"])
    monkeypatch.setattr(acceptance, "_name_owner", lambda _name: next(owners))
    monkeypatch.setattr(
        acceptance,
        "capture_desktop_snapshot",
        lambda: desktop_snapshot(enabled=desktop["enabled"]),
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    with pytest.raises(acceptance.AcceptanceError, match="owner-is-not-stable"):
        acceptance.run_lifecycle(
            path, acceptance.CONFIRMATIONS["lifecycle"], reader=fake
        )

    assert acceptance.load_state(path)["lifecycle"]["status"] == "failed"


def test_observation_uses_stable_fake_provider_and_never_records_raw_identity(
    tmp_path, monkeypatch
):
    path = state_path(tmp_path)
    provider = FakeProvider([sample(), sample()])
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot()
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    state = acceptance.observe_app(
        path,
        "gtk-editor",
        "OBSERVE_GTK_EDITOR",
        delay_seconds=1.0,
        reader=provider,
    )

    observation = state["observations"][0]
    assert observation["stable"] is True
    assert observation["identity_matches"] is True
    assert observation["provider_class"] == "gtk-editor"
    serialized = json.dumps(observation)
    assert "shell-a" not in serialized
    assert "org.gnome.TextEditor" not in serialized


def test_observation_rejects_non_shell_focus_service_owner(tmp_path, monkeypatch):
    path = state_path(tmp_path)
    payload = json.dumps(
        {
            "schema_version": 1,
            "shell_session_id": "shell-a",
            "window_sequence": 17,
            "focus_generation": 4,
            "locked": False,
            "app_id": "org.gnome.TextEditor.desktop",
            "wm_class": "org.gnome.TextEditor",
            "title": "",
            "pid": None,
            "window_id": "gnome:shell-a:17",
        }
    )
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot()
    )
    monkeypatch.setattr(
        acceptance, "_run_text", lambda *_args, **_kwargs: repr((payload,))
    )
    monkeypatch.setattr(
        acceptance,
        "_name_owner",
        lambda name: ":1.spoof"
        if name == acceptance.FOCUS_BUS_NAME
        else ":1.shell",
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    with pytest.raises(acceptance.AcceptanceError, match="owner-is-not-stable"):
        acceptance.observe_app(
            path,
            "gtk-editor",
            "OBSERVE_GTK_EDITOR",
            delay_seconds=1.0,
        )


def test_conflicting_observed_identity_is_rejected(tmp_path, monkeypatch):
    path = state_path(tmp_path)
    conflict = sample(
        app_id="org.gnome.TextEditor.desktop", wm_class="org.gnome.Ptyxis"
    )
    provider = FakeProvider([conflict, conflict])
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot()
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    with pytest.raises(acceptance.AcceptanceError, match="identity-mismatch"):
        acceptance.observe_app(
            path,
            "gtk-editor",
            "OBSERVE_GTK_EDITOR",
            delay_seconds=1.0,
            reader=provider,
        )

    assert acceptance.load_state(path)["status"] == "failed"


def test_observation_rejects_equal_generation_window_change(tmp_path, monkeypatch):
    path = state_path(tmp_path)
    provider = FakeProvider(
        [
            sample(generation=7),
            sample(
                generation=7,
                sequence=18,
                app_id="org.gnome.Ptyxis.desktop",
                wm_class="org.gnome.Ptyxis",
            ),
        ]
    )
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot()
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    with pytest.raises(acceptance.AcceptanceError, match="generation-transition"):
        acceptance.observe_app(
            path,
            "gtk-editor",
            "OBSERVE_GTK_EDITOR",
            delay_seconds=1.0,
            reader=provider,
        )

    persisted = acceptance.load_state(path)
    assert persisted["status"] == "failed"
    assert persisted["observations"][0]["stable"] is False


def test_100_iteration_fake_focus_race_rejects_every_drift(tmp_path, monkeypatch):
    path = state_path(tmp_path)
    samples = []
    for index in range(acceptance.RACE_ITERATIONS):
        expected = sample(generation=index * 2)
        current = (
            sample(generation=index * 2)
            if index % 2 == 0
            else sample(
                generation=index * 2 + 1,
                sequence=18,
                app_id="org.gnome.Ptyxis.desktop",
                wm_class="org.gnome.Ptyxis",
            )
        )
        samples.extend([expected, current])
    provider = FakeProvider(samples)
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot()
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    state = acceptance.run_focus_race(
        path,
        acceptance.CONFIRMATIONS["race"],
        interval_seconds=0.01,
        reader=provider,
    )

    assert provider.calls == 200
    assert state["race"]["iterations"] == 100
    assert state["race"]["stable_pairs"] == 50
    assert state["race"]["drift_pairs"] == 50
    assert state["race"]["drift_rejected"] == 50
    assert state["race"]["away_back_verified"] is True
    assert state["race"]["status"] == "passed"


def test_focus_race_rejects_equal_generation_authority_change(tmp_path, monkeypatch):
    path = state_path(tmp_path)
    samples = []
    for index in range(acceptance.RACE_ITERATIONS):
        expected = sample(generation=index * 2)
        if index == 1:
            current = sample(
                generation=index * 2,
                sequence=18,
                app_id="org.gnome.Ptyxis.desktop",
                wm_class="org.gnome.Ptyxis",
            )
        elif index % 2:
            current = sample(
                generation=index * 2 + 1,
                sequence=18,
                app_id="org.gnome.Ptyxis.desktop",
                wm_class="org.gnome.Ptyxis",
            )
        else:
            current = expected
        samples.extend([expected, current])
    provider = FakeProvider(samples)
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot()
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    with pytest.raises(acceptance.AcceptanceError, match="race-gate"):
        acceptance.run_focus_race(
            path,
            acceptance.CONFIRMATIONS["race"],
            interval_seconds=0.01,
            reader=provider,
        )

    race = acceptance.load_state(path)["race"]
    assert race["status"] == "failed"
    assert race["generation_regressions"] >= 1


def test_focus_race_counts_spoofed_non_shell_provider_as_invalid(
    tmp_path, monkeypatch
):
    path = state_path(tmp_path)
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot()
    )
    monkeypatch.setattr(acceptance, "_run_text", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        acceptance,
        "_name_owner",
        lambda name: ":1.spoof"
        if name == acceptance.FOCUS_BUS_NAME
        else ":1.shell",
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    with pytest.raises(acceptance.AcceptanceError, match="race-gate"):
        acceptance.run_focus_race(
            path,
            acceptance.CONFIRMATIONS["race"],
            interval_seconds=0.01,
        )

    race = acceptance.load_state(path)["race"]
    assert race["invalid_samples"] == acceptance.RACE_ITERATIONS
    assert race["status"] == "failed"


def test_race_without_any_observed_drift_is_incomplete_and_fails(tmp_path, monkeypatch):
    path = state_path(tmp_path)
    provider = FakeProvider([sample()] * (acceptance.RACE_ITERATIONS * 2))
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot()
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    with pytest.raises(acceptance.AcceptanceError, match="race-gate"):
        acceptance.run_focus_race(
            path,
            acceptance.CONFIRMATIONS["race"],
            interval_seconds=0.01,
            reader=provider,
        )

    assert acceptance.load_state(path)["race"]["status"] == "failed"


def test_record_app_requires_prior_stable_identity_observation(tmp_path):
    path = state_path(tmp_path)

    with pytest.raises(acceptance.AcceptanceError, match="must-be-observed"):
        acceptance.record_app_outcome(
            path,
            "codex-cli",
            "inserted-exactly-once",
            "paste",
            "RECORD_CODEX_CLI",
        )


def test_recorded_command_surface_outcome_is_manual_only(tmp_path):
    path = state_path(tmp_path)
    state = acceptance.load_state(path)
    state["observations"].append(
        {
            "at": "2026-08-10T12:00:01+00:00",
            "app": "codex-cli",
            "expected_provider_class": "terminal",
            "provider_class": "terminal",
            "stable": True,
            "identity_matches": True,
            "session_fingerprint": "a" * 16,
            "window_fingerprint": "b" * 16,
            "focus_generation": 3,
        }
    )
    acceptance.save_state(path, state)

    state = acceptance.record_app_outcome(
        path,
        "codex-cli",
        "inserted-exactly-once",
        "paste",
        "RECORD_CODEX_CLI",
    )

    outcome = state["app_outcomes"]["codex-cli"]
    assert outcome["automatic_typing_by_harness"] is False
    assert outcome["submit_by_harness"] is False
    assert outcome["disposable_buffer_confirmed"] is True


@pytest.mark.parametrize("hard_outcome", sorted(acceptance.HARD_FAILURE_OUTCOMES))
def test_hard_application_outcome_is_permanently_latched_and_reported(
    tmp_path, hard_outcome
):
    path = state_path(tmp_path)
    state = acceptance.load_state(path)
    state["observations"].append(
        {
            "at": "2026-08-10T12:00:01+00:00",
            "app": "gtk-editor",
            "expected_provider_class": "gtk-editor",
            "provider_class": "gtk-editor",
            "stable": True,
            "identity_matches": True,
            "session_fingerprint": "a" * 16,
            "window_fingerprint": "b" * 16,
            "focus_generation": 3,
        }
    )
    acceptance.save_state(path, state)

    failed = acceptance.record_app_outcome(
        path,
        "gtk-editor",
        hard_outcome,
        "paste",
        "RECORD_GTK_EDITOR",
    )
    assert failed["status"] == "failed"
    assert acceptance.build_report(failed)["app_outcomes"]["gtk-editor"][
        "outcome"
    ] == hard_outcome

    with pytest.raises(acceptance.AcceptanceError, match="latched"):
        acceptance.record_app_outcome(
            path,
            "gtk-editor",
            "inserted-exactly-once",
            "paste",
            "RECORD_GTK_EDITOR",
        )

    persisted = acceptance.load_state(path)
    assert persisted["app_outcomes"]["gtk-editor"]["outcome"] == hard_outcome
    assert acceptance._overall(persisted) == "failed"


def test_focus_away_and_back_gate_requires_intervening_window_and_generation():
    observations = [
        {
            "window_fingerprint": "editor",
            "session_fingerprint": "shell",
            "focus_generation": 10,
        },
        {
            "window_fingerprint": "browser",
            "session_fingerprint": "shell",
            "focus_generation": 11,
        },
        {
            "window_fingerprint": "editor",
            "session_fingerprint": "shell",
            "focus_generation": 12,
        },
    ]

    assert acceptance._focus_transition_gate(observations)
    observations[1]["focus_generation"] = 10
    assert not acceptance._focus_transition_gate(observations)
    observations[1]["focus_generation"] = 11
    observations[2]["focus_generation"] = 10
    assert not acceptance._focus_transition_gate(observations)


def test_report_is_transcript_free_and_bounded(tmp_path):
    path = state_path(tmp_path)
    state = acceptance.load_state(path)
    state["observations"] = [
        {
            "at": "2026-08-10T12:00:01+00:00",
            "app": "gtk-editor",
            "expected_provider_class": "gtk-editor",
            "provider_class": "gtk-editor",
            "stable": True,
            "identity_matches": True,
            "session_fingerprint": "a" * 16,
            "window_fingerprint": "b" * 16,
            "focus_generation": 3,
        }
    ]
    report = acceptance.build_report(state)
    encoded = json.dumps(report).encode("utf-8")

    assert len(encoded) < acceptance.MAX_REPORT_BYTES
    assert report["safety"]["transcripts_recorded"] is False
    assert "Sensitive repository title" not in json.dumps(report)
    assert '"pid":' not in json.dumps(report).casefold()
    assert "window_sequence" not in json.dumps(report)


def test_full_evidence_gate_requires_all_apps_exactly_once():
    state = acceptance._new_state(
        desktop_snapshot(), {"present": False, "manifest": {}}
    )
    state["lifecycle"] = {"status": "passed"}
    state["race"] = {"status": "passed"}
    state["observations"] = [
        {"window_fingerprint": "editor", "session_fingerprint": "s", "focus_generation": 1},
        {"window_fingerprint": "browser", "session_fingerprint": "s", "focus_generation": 2},
        {"window_fingerprint": "editor", "session_fingerprint": "s", "focus_generation": 3},
    ]
    state["app_outcomes"] = {
        app: {"outcome": "inserted-exactly-once", "backend": "paste"}
        for app in acceptance.REQUIRED_APPS
    }

    assert acceptance._overall(state) == "complete-manual-evidence"
    state["app_outcomes"]["firefox"]["outcome"] = "clipboard-fallback"
    assert acceptance._overall(state) == "incomplete"
    state["app_outcomes"]["firefox"]["outcome"] = "wrong-target"
    assert acceptance._overall(state) == "failed"


def test_restore_reinstates_exact_extension_settings_and_input(
    tmp_path, monkeypatch
):
    path = tmp_path / "acceptance.json"
    os.chmod(str(tmp_path), 0o700)
    extension = tmp_path / "installed-extension"
    extension.mkdir(mode=0o700)
    original = extension / "extension.js"
    original.write_text("original", encoding="utf-8")
    original.chmod(0o640)
    original_manifest = acceptance._manifest(extension)
    backup = acceptance._backup_dir(path)
    shutil.copytree(str(extension), str(backup))
    baseline = desktop_snapshot(enabled=False)
    state = acceptance._new_state(
        baseline, {"present": True, "manifest": original_manifest}
    )
    original.write_text("repository probe", encoding="utf-8")
    (extension / "focusService.js").write_text("provider", encoding="utf-8")
    state["installed_manifest"] = acceptance._manifest(extension)
    acceptance.save_state(path, state)

    monkeypatch.setattr(acceptance, "_extension_dir", lambda: extension)
    monkeypatch.setattr(acceptance, "_set_extension_enabled", lambda _enabled: None)
    monkeypatch.setattr(acceptance, "_gsettings_set_list", lambda *_args: None)
    monkeypatch.setattr(
        acceptance, "capture_desktop_snapshot", lambda: desktop_snapshot(enabled=False)
    )
    monkeypatch.setattr(
        acceptance,
        "_run_quiet",
        lambda *_args, **_kwargs: True,
    )

    restored = acceptance.restore_run(
        path, acceptance.CONFIRMATIONS["restore"]
    )

    assert restored["status"] == "restored"
    assert acceptance._manifest(extension) == original_manifest
    assert original.read_text(encoding="utf-8") == "original"
    assert stat.S_IMODE(original.stat().st_mode) == 0o640
    assert not (extension / "focusService.js").exists()
    assert restored["restore"]["extension_settings_restored"] is True
    assert restored["restore"]["input_restored"] is True
    quarantine = extension.parent / (
        ".{}-lst-quarantine-{}".format(
            acceptance.EXTENSION_UUID, state["run_id"][:12]
        )
    )
    assert (quarantine / "focusService.js").is_file()
    acceptance._restore_extension_files(path, restored)
    assert acceptance._manifest(extension) == original_manifest


def test_launcher_is_executable_and_installer_covers_it():
    project_root = Path(__file__).resolve().parents[2]
    launcher = project_root / "bin" / "lst-gnome-acceptance"
    installer = project_root / "scripts" / "install" / "install-with-uv.sh"

    assert launcher.is_file()
    assert os.access(str(launcher), os.X_OK)
    assert "lst-gnome-acceptance" in installer.read_text(encoding="utf-8")
