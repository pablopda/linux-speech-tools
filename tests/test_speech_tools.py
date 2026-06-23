#!/usr/bin/env python3
"""Non-invasive tests for launchers, installer profiles, and setup helpers."""

import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CANONICAL_DICTATION_BINDING_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/"
)
LEGACY_DICTATION_BINDING_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/faster-dictation/"
)


def make_fake_gnome_commands(fakebin: Path, gsettings_log: Path) -> None:
    fakebin.mkdir(parents=True, exist_ok=True)
    gsettings = fakebin / "gsettings"
    gsettings.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${FAKE_GSETTINGS_LOG:?}"
if [ "${1:-}" = "get" ]; then
    printf '%s\\n' "${FAKE_GSETTINGS_GET:-[]}"
elif [ "${1:-}" = "list-schemas" ]; then
    printf '%s\\n' "org.gnome.settings-daemon.plugins.media-keys"
fi
"""
    )
    gsettings.chmod(0o755)

    for name in ("notify-send", "gnome-extensions"):
        tool = fakebin / name
        tool.write_text("#!/usr/bin/env bash\nexit 0\n")
        tool.chmod(0o755)

    gsettings_log.parent.mkdir(parents=True, exist_ok=True)


class TestLaunchers(unittest.TestCase):
    def test_expected_launchers_exist(self):
        for name in [
            "say",
            "say-local",
            "say-read",
            "say-read-es",
            "talk2claude",
            "talk2claude-faster",
            "linux-speech-tools-env",
            "linux-speech-tools-setup",
        ]:
            path = ROOT / "bin" / name
            self.assertTrue(path.exists(), f"{name} not found")
            self.assertTrue(os.access(path, os.X_OK), f"{name} is not executable")

    def test_shell_syntax(self):
        scripts = [
            ROOT / "installer.sh",
            ROOT / "scripts/install/install-with-uv.sh",
            ROOT / "bin/say",
            ROOT / "bin/say-local",
            ROOT / "bin/say-read",
            ROOT / "bin/say-read-es",
            ROOT / "bin/talk2claude",
            ROOT / "bin/talk2claude-faster",
            ROOT / "bin/talk2claude-faster-toggle",
            ROOT / "bin/linux-speech-tools-env",
            ROOT / "bin/linux-speech-tools-setup",
            ROOT / "bin/say-read-continuous",
            ROOT / "bin/say-read-gnome",
            ROOT / "bin/gnome-dictation",
            ROOT / "scripts/install/install-gnome-integration.sh",
            ROOT / "scripts/setup/setup-faster-hotkey.sh",
            ROOT / "scripts/setup/setup-uinput-permissions.sh",
        ]
        for script in scripts:
            with self.subTest(script=script):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_say_help(self):
        result = subprocess.run(
            [str(ROOT / "bin/say"), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout.lower())

    def test_common_help_paths_are_side_effect_free(self):
        commands = [
            [str(ROOT / "bin/say-local"), "--help"],
            [str(ROOT / "bin/talk2claude"), "--help"],
            [str(ROOT / "bin/talk2claude-faster-toggle"), "--help"],
            [str(ROOT / "bin/gnome-dictation"), "--help"],
            [str(ROOT / "scripts/setup/setup-faster-hotkey.sh"), "--help"],
            [str(ROOT / "scripts/setup/setup-uinput-permissions.sh"), "--help"],
            [str(ROOT / "scripts/install/install-gnome-integration.sh"), "--help"],
        ]
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage", (result.stdout + result.stderr).lower())

    def test_missing_option_values_fail_cleanly(self):
        commands = [
            [str(ROOT / "bin/say"), "-v"],
            [str(ROOT / "bin/say"), "-o"],
            [str(ROOT / "bin/say-local"), "-v"],
            [str(ROOT / "bin/talk2claude"), "--lang"],
        ]
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("unbound variable", result.stderr)

    def test_unknown_dictation_args_do_not_start_recording(self):
        for command in (
            [str(ROOT / "bin/talk2claude"), "--definitely-unknown"],
            [str(ROOT / "bin/gnome-dictation"), "definitely-unknown"],
            [str(ROOT / "bin/talk2claude-faster-toggle"), "--definitely-unknown"],
        ):
            with self.subTest(command=command):
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2)


class TestInstallerProfiles(unittest.TestCase):
    def test_streamed_installer_dry_run_bootstraps_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            streamed = Path(tmpdir) / "installer.sh"
            shutil.copy2(ROOT / "installer.sh", streamed)
            result = subprocess.run(
                ["bash", str(streamed), "--dry-run"],
                env={**os.environ, "LST_SOURCE_DIR": str(Path(tmpdir) / "source")},
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No adjacent checkout found", result.stdout)
        self.assertIn("would download", result.stdout)

    def test_installer_dry_run_profiles(self):
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "installer.sh"),
                "--dry-run",
                "--with-kokoro",
                "--with-stt",
                "--download-models",
                "--whisper-model",
                "tiny",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("uv sync", result.stdout)
        self.assertIn("--locked", result.stdout)
        self.assertIn("--extra kokoro", result.stdout)
        self.assertIn("--extra stt", result.stdout)
        self.assertIn("src.utils.setup_models", result.stdout)
        self.assertIn("--whisper-model tiny", result.stdout)

    def test_streamed_installer_requires_checksum_for_custom_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            streamed = Path(tmpdir) / "installer.sh"
            shutil.copy2(ROOT / "installer.sh", streamed)
            result = subprocess.run(
                ["bash", str(streamed), "--dry-run"],
                env={
                    **os.environ,
                    "LST_INSTALLER_REF": "v9.9.9",
                    "LST_SOURCE_DIR": str(Path(tmpdir) / "source"),
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LST_INSTALLER_SHA256", result.stderr)

    def test_streamed_installer_rejects_dangerous_source_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            streamed = Path(tmpdir) / "installer.sh"
            shutil.copy2(ROOT / "installer.sh", streamed)
            result = subprocess.run(
                ["bash", str(streamed), "--dry-run"],
                env={**os.environ, "LST_SOURCE_DIR": os.environ["HOME"]},
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing unsafe LST_SOURCE_DIR", result.stderr)

    def test_pyproject_declares_runtime_extras(self):
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn("stt = [", pyproject)
        self.assertIn("faster-whisper", pyproject)
        self.assertIn("webrtcvad", pyproject)
        self.assertIn("reader = [", pyproject)
        self.assertIn("[tool.pytest.ini_options]", pyproject)
        self.assertIn('testpaths = ["tests"]', pyproject)

    def test_installer_dry_run_does_not_sudo_by_default(self):
        result = subprocess.run(
            ["bash", str(ROOT / "installer.sh"), "--dry-run"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("sudo apt", result.stdout)
        self.assertNotIn("sudo dnf", result.stdout)
        self.assertNotIn("sudo pacman", result.stdout)

    def test_installer_system_deps_are_explicit(self):
        result = subprocess.run(
            ["bash", str(ROOT / "installer.sh"), "--dry-run", "--install-system-deps"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sudo", result.stdout)


class TestModelSetup(unittest.TestCase):
    def test_model_setup_help(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "src/utils/setup_models.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--kokoro", result.stdout)
        self.assertIn("--stt", result.stdout)

    def test_model_setup_dry_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src/utils/setup_models.py"),
                    "--kokoro",
                    "--stt",
                    "--dry-run",
                    "--kokoro-model",
                    str(Path(tmpdir) / "kokoro-v1.0.onnx"),
                    "--kokoro-voices",
                    str(Path(tmpdir) / "voices-v1.0.bin"),
                    "--whisper-model",
                    "tiny",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry-run: would download", result.stdout)
        self.assertIn("dry-run: would prefetch faster-whisper model tiny", result.stdout)

    def test_model_setup_clear_state_dry_run(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "src/utils/setup_models.py"), "--clear-state", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_model_setup_rejects_unallowlisted_whisper_model(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "src/utils/setup_models.py"),
                "--stt",
                "--dry-run",
                "--whisper-model",
                "someone/custom-model",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported faster-whisper model", result.stderr)

    def test_model_setup_has_default_kokoro_checksums(self):
        setup_models = (ROOT / "src/utils/setup_models.py").read_text()
        self.assertRegex(setup_models, r'KOKORO_MODEL_SHA256 = "[0-9a-f]{64}"')
        self.assertRegex(setup_models, r'KOKORO_VOICES_SHA256 = "[0-9a-f]{64}"')


class TestRuntimeSafety(unittest.TestCase):
    def test_private_state_paths_replace_shared_tmp(self):
        checked_files = [
            ROOT / "bin/talk2claude-faster-toggle",
            ROOT / "src/stt/faster_whisper_clipboard.py",
            ROOT / "bin/talk2claude",
        ]
        for path in checked_files:
            with self.subTest(path=path):
                text = path.read_text()
                self.assertNotIn("/tmp/talk2claude", text)
                self.assertNotIn("/tmp/dictation.txt", text)
                self.assertNotIn("/tmp/.ydotool_socket", text)

    def test_ci_does_not_mask_main_test_failure(self):
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text()
                self.assertNotIn("|| echo", text)
                self.assertNotIn("|| true", text)

    def test_release_workflows_do_not_reference_removed_modern_installer(self):
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                self.assertNotIn("--modern", workflow.read_text())

    def test_release_scripts_resolve_project_root_from_scripts_release(self):
        for script in [
            ROOT / "scripts/release/pre-release-check.sh",
            ROOT / "scripts/release/release.sh",
        ]:
            with self.subTest(script=script):
                self.assertIn('../.." && pwd', script.read_text())

    def test_source_packages_are_built_from_tracked_archive(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        release = (ROOT / ".github/workflows/release.yml").read_text()
        package_test = (ROOT / ".github/workflows/package-test.yml").read_text()
        self.assertIn("git archive --format=tar.gz", ci)
        self.assertIn("git archive --format=tar --prefix", release)
        self.assertIn("git archive --format=tar --prefix", package_test)

    def test_say_uses_uv_project_environment(self):
        say = (ROOT / "bin/say").read_text()
        self.assertIn('uv --project "$PROJECT_ROOT" run edge-tts', say)
        self.assertIn('uv --project "$PROJECT_ROOT" run edge-playback', say)
        self.assertIn("Text-to-speech using Edge TTS (cloud service).", say)

    def test_say_read_wrapper_documents_defaults_and_respects_player(self):
        say_read = (ROOT / "bin/say-read").read_text()
        self.assertIn("Installed say-read defaults", say_read)
        self.assertRegex(say_read, r"--out\|-o\|--player")

    def test_packaged_launchers_can_find_usr_share_runtime(self):
        for launcher in [
            "say",
            "say-local",
            "say-read",
            "say-read-es",
            "say-read-gnome",
            "say-read-mvp",
            "talk2claude-faster",
            "talk2claude-faster-toggle",
            "linux-speech-tools-setup",
        ]:
            with self.subTest(launcher=launcher):
                text = (ROOT / "bin" / launcher).read_text()
                self.assertIn("/usr/share/linux-speech-tools", text)

    def test_faster_toggle_finalizes_in_clipboard_mode(self):
        toggle = (ROOT / "bin/talk2claude-faster-toggle").read_text()
        self.assertIn('kill -INT "$pid"', toggle)
        self.assertIn("transcribing buffered speech", toggle)
        self.assertIn('cd "$PROJECT_ROOT"', toggle)
        self.assertIn("STT_STOP_HINT=", toggle)
        self.assertIn("faster_whisper_auto --clipboard", toggle)

        clipboard = (ROOT / "src/stt/faster_whisper_clipboard.py").read_text()
        self.assertIn("STT_STOP_HINT", clipboard)

    def test_installer_verify_respects_selected_uv_profiles(self):
        installer = (ROOT / "scripts/install/install-with-uv.sh").read_text()
        self.assertIn("uv_sync_args", installer)
        self.assertIn("sync --locked", installer)
        self.assertIn("args+=(--check)", installer)
        self.assertIn('uv "${args[@]}"', installer)
        self.assertIn("linux-speech-tools-env", installer)
        self.assertIn("NO_PATH_EDIT", installer)

    def test_config_helper_rejects_unsafe_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config" / "linux-speech-tools"
            config_dir.mkdir(parents=True)
            config = config_dir / "install.env"
            config.write_text("LST_PROJECT_ROOT=/tmp/example\n")
            config.chmod(0o666)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"source {shlex_quote(str(ROOT / 'bin/linux-speech-tools-env'))}",
                ],
                env={**os.environ, "XDG_CONFIG_HOME": str(Path(tmpdir) / "config")},
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing unsafe config permissions", result.stderr)

    def test_config_helper_rejects_symlinked_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config" / "linux-speech-tools"
            config_dir.mkdir(parents=True)
            target = Path(tmpdir) / "target.env"
            target.write_text("LST_PROJECT_ROOT=/tmp/example\n")
            target.chmod(0o600)
            (config_dir / "install.env").symlink_to(target)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"source {shlex_quote(str(ROOT / 'bin/linux-speech-tools-env'))}",
                ],
                env={**os.environ, "XDG_CONFIG_HOME": str(Path(tmpdir) / "config")},
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing symlinked config", result.stderr)

    def test_gnome_profile_installs_system_python_bindings(self):
        installer = (ROOT / "scripts/install/install-with-uv.sh").read_text()
        self.assertIn("python3-dbus", installer)
        self.assertIn("python3-gi", installer)
        self.assertIn("python3-gobject", installer)

    def test_gnome_reader_notification_actions_are_handled(self):
        control = (ROOT / "src/gnome/gnome-reader-control.py").read_text()
        self.assertIn("--wait", control)
        self.assertIn("--action={action}={label}", control)
        self.assertIn("_handle_notification_action", control)
        self.assertIn("in_signature='ssis'", control)
        self.assertIn("in_signature='is'", control)
        self.assertIn("_pid_is_reader", control)

    def test_lockfile_is_tracked_for_locked_installs(self):
        self.assertTrue((ROOT / "uv.lock").exists())
        gitignore = (ROOT / ".gitignore").read_text()
        self.assertNotIn("uv.lock", gitignore)
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("uv lock --check", ci)


class TestGnomeDictationSetup(unittest.TestCase):
    def gnome_env(self, tmpdir: str, get_value: str = "[]"):
        root = Path(tmpdir)
        fakebin = root / "fakebin"
        log = root / "gsettings.log"
        home = root / "home"
        config_home = root / "config"
        home.mkdir()
        config_home.mkdir()
        config_home.chmod(0o700)
        make_fake_gnome_commands(fakebin, log)
        env = {
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(config_home),
            # Simulate a real GNOME session so setup paths gated on
            # XDG_CURRENT_DESKTOP (e.g. gnome-dictation setup) run here.
            "XDG_CURRENT_DESKTOP": "GNOME",
            "FAKE_GSETTINGS_LOG": str(log),
            "FAKE_GSETTINGS_GET": get_value,
            "PATH": f"{fakebin}:{os.environ['PATH']}",
        }
        return env, log, home, config_home

    def test_gnome_installer_basic_uses_canonical_keybinding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env, log, home, _config_home = self.gnome_env(tmpdir)
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts/install/install-gnome-integration.sh"),
                    "--basic",
                    "--noninteractive",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            calls = log.read_text()
            install_dir = home / ".local/bin"
            self.assertIn(CANONICAL_DICTATION_BINDING_PATH, calls)
            self.assertNotIn(LEGACY_DICTATION_BINDING_PATH, calls)
            self.assertIn("Speech Dictation (Toggle)", calls)
            self.assertIn(f"command {install_dir / 'talk2claude-faster-toggle'}", calls)
            self.assertTrue((install_dir / "setup-faster-hotkey.sh").exists())

    def test_gnome_installer_preserves_existing_runtime_config_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env, _log, home, config_home = self.gnome_env(tmpdir)
            config_dir = config_home / "linux-speech-tools"
            config_dir.mkdir()
            config_dir.chmod(0o700)
            config = config_dir / "install.env"
            config.write_text(
                "\n".join(
                    [
                        "WHISPER_MODEL=base",
                        "WHISPER_DEVICE=cuda",
                        "ASR_LANG=es",
                        "LST_PROJECT_ROOT=/old/root",
                        "LST_INSTALL_DIR=/old/bin",
                        "",
                    ]
                )
            )
            config.chmod(0o600)

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts/install/install-gnome-integration.sh"),
                    "--basic",
                    "--noninteractive",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            text = config.read_text()
            self.assertIn("WHISPER_MODEL=base", text)
            self.assertIn("WHISPER_DEVICE=cuda", text)
            self.assertIn("ASR_LANG=es", text)
            self.assertIn(f"LST_INSTALL_DIR={home / '.local/bin'}", text)
            self.assertNotIn("/old/root", text)
            self.assertNotIn("/old/bin", text)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_gnome_installer_dry_run_does_not_mutate_files_or_gsettings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env, log, home, config_home = self.gnome_env(tmpdir)
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts/install/install-gnome-integration.sh"),
                    "--basic",
                    "--noninteractive",
                    "--dry-run",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dry-run: would", result.stdout)
            self.assertFalse((home / ".local/bin/gnome-dictation").exists())
            self.assertFalse((config_home / "linux-speech-tools/install.env").exists())
            self.assertFalse(log.exists())

    def test_gnome_installer_uninstall_removes_current_and_legacy_keybindings(self):
        existing = (
            f"['/unrelated/', '{CANONICAL_DICTATION_BINDING_PATH}', "
            f"'{LEGACY_DICTATION_BINDING_PATH}']"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env, log, _home, _config_home = self.gnome_env(tmpdir, existing)
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts/install/install-gnome-integration.sh"),
                    "--uninstall",
                    "--noninteractive",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            calls = log.read_text()
            self.assertIn("custom-keybindings ['/unrelated/']", calls)
            for path in (CANONICAL_DICTATION_BINDING_PATH, LEGACY_DICTATION_BINDING_PATH):
                self.assertIn(f"reset org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{path} name", calls)
                self.assertIn(f"reset org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{path} command", calls)
                self.assertIn(f"reset org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{path} binding", calls)

    def test_setup_faster_hotkey_uses_installed_env_and_canonical_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env, log, home, config_home = self.gnome_env(tmpdir)
            install_dir = home / ".local/bin"
            install_dir.mkdir(parents=True)
            for source in (
                ROOT / "scripts/setup/setup-faster-hotkey.sh",
                ROOT / "bin/linux-speech-tools-env",
                ROOT / "bin/talk2claude-faster-toggle",
            ):
                target = install_dir / source.name
                shutil.copy2(source, target)
                target.chmod(0o755)

            config_dir = config_home / "linux-speech-tools"
            config_dir.mkdir()
            config_dir.chmod(0o700)
            config = config_dir / "install.env"
            config.write_text(
                f"LST_PROJECT_ROOT=/not/the/repo\nLST_INSTALL_DIR={install_dir}\n"
            )
            config.chmod(0o600)

            result = subprocess.run(
                [
                    "bash",
                    str(install_dir / "setup-faster-hotkey.sh"),
                    "--binding",
                    "<Super><Ctrl>space",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            calls = log.read_text()
            self.assertIn(CANONICAL_DICTATION_BINDING_PATH, calls)
            self.assertNotIn(LEGACY_DICTATION_BINDING_PATH, calls)
            self.assertIn(f"command {install_dir / 'talk2claude-faster-toggle'}", calls)
            self.assertIn("binding <Super><Ctrl>space", calls)

    def test_gnome_dictation_setup_uses_canonical_toggle_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env, log, _home, _config_home = self.gnome_env(tmpdir)
            result = subprocess.run(
                [str(ROOT / "bin/gnome-dictation"), "setup"],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            calls = log.read_text()
            self.assertIn(CANONICAL_DICTATION_BINDING_PATH, calls)
            self.assertIn(f"command {ROOT / 'bin/talk2claude-faster-toggle'}", calls)
            self.assertNotIn("gnome-dictation toggle", calls)


class TestFasterSTTBehavior(unittest.TestCase):
    def import_clipboard_module_with_fakes(self):
        fake_fw = types.SimpleNamespace(WhisperModel=object)
        fake_vad = types.SimpleNamespace(Vad=lambda *args, **kwargs: object())
        with mock.patch.dict(
            sys.modules,
            {"faster_whisper": fake_fw, "webrtcvad": fake_vad},
        ):
            sys.modules.pop("src.stt.faster_whisper_clipboard", None)
            sys.modules.pop("src.stt.session", None)
            return importlib.import_module("src.stt.faster_whisper_clipboard")

    def test_auto_mode_check_reports_clipboard_default(self):
        result = subprocess.run(
            [sys.executable, "-m", "src.stt.faster_whisper_auto", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("Default mode: clipboard", output)
        self.assertIn("Direct typing available:", output)
        self.assertIn("Preview mode:", output)

    def test_auto_mode_help_exposes_preview(self):
        result = subprocess.run(
            [sys.executable, "-m", "src.stt.faster_whisper_auto", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--preview", result.stdout)
        self.assertIn("--diagnose", result.stdout)
        self.assertIn("--warm-model", result.stdout)

    def test_diagnostics_are_side_effect_light_without_warm_model(self):
        result = subprocess.run(
            [sys.executable, "-m", "src.stt.faster_whisper_auto", "--diagnose"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("Model:", output)
        self.assertIn("Device:", output)
        self.assertIn("Compute type:", output)
        self.assertIn("Audio backends:", output)
        self.assertIn("Clipboard output:", output)
        self.assertNotIn("Model load time:", output)

    def test_warm_model_timing_is_explicit_and_mockable(self):
        from src.stt import faster_whisper_auto

        calls = []

        class FakeWhisperModel:
            def __init__(self, model_size, device, compute_type):
                calls.append((model_size, device, compute_type))

        fake_fw = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_fw}):
            with mock.patch.object(
                faster_whisper_auto.time,
                "monotonic",
                side_effect=[10.0, 12.5],
            ):
                elapsed = faster_whisper_auto.warm_model("tiny", "cpu")

        self.assertEqual(elapsed, 2.5)
        self.assertEqual(calls, [("tiny", "cpu", "int8")])

    def test_preview_prompt_accepts_edits_and_rejects_noninteractive(self):
        from src.stt.runtime import NonInteractivePreviewError, preview_transcription

        accepted, stop = preview_transcription(
            "hello world",
            "clipboard",
            input_stream=io.StringIO("e\nedited text\n"),
            output_stream=io.StringIO(),
            interactive=True,
        )
        self.assertFalse(stop)
        self.assertEqual(accepted, "edited text")

        skipped, stop = preview_transcription(
            "hello world",
            "typing",
            input_stream=io.StringIO("s\n"),
            output_stream=io.StringIO(),
            interactive=True,
        )
        self.assertFalse(stop)
        self.assertIsNone(skipped)

        with self.assertRaises(NonInteractivePreviewError):
            preview_transcription(
                "hello world",
                "typing",
                input_stream=io.StringIO("a\n"),
                output_stream=io.StringIO(),
                interactive=False,
            )

    def test_clipboard_file_fallback_is_explicit_and_overwrites(self):
        module = self.import_clipboard_module_with_fakes()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "linux-speech-tools" / "dictation.txt"
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}, clear=True):
                with mock.patch.object(
                    module.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 1),
                ):
                    manager = module.ClipboardManager()
                    self.assertEqual(manager.clipboard_tool, "file")
                    self.assertFalse(manager.copy_to_clipboard("secret one"))
                    self.assertFalse(state_path.exists())

            with mock.patch.dict(
                os.environ,
                {"XDG_RUNTIME_DIR": tmpdir, "STT_TRANSCRIPT_FALLBACK": "1"},
                clear=True,
            ):
                with mock.patch.object(
                    module.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 1),
                ):
                    manager = module.ClipboardManager()
                    self.assertTrue(manager.copy_to_clipboard("secret one"))
                    self.assertTrue(manager.copy_to_clipboard("secret two"))

            self.assertEqual(state_path.read_text(), "secret two\n")
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)

    def test_clipboard_mode_does_not_log_transcript_snippets(self):
        text = (ROOT / "src/stt/faster_whisper_clipboard.py").read_text()
        self.assertNotIn("text[:60]", text)
        self.assertIn("Transcription ready", text)

    def test_toggle_status_plain_json_and_purge_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {**os.environ, "XDG_RUNTIME_DIR": tmpdir}
            plain = subprocess.run(
                [str(ROOT / "bin/talk2claude-faster-toggle"), "status", "--plain"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertEqual(plain.stdout.strip(), "idle")

            status = subprocess.run(
                [str(ROOT / "bin/talk2claude-faster-toggle"), "status", "--json"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            data = json.loads(status.stdout)
            self.assertEqual(data["state"], "idle")
            self.assertIn("log_file", data)
            self.assertIn("fallback_file", data)

            state_dir = Path(tmpdir) / "linux-speech-tools"
            state_dir.mkdir(exist_ok=True)
            for name in [
                "talk2claude-faster.pid",
                "talk2claude-faster.log",
                "talk2claude-faster.status.json",
                "dictation.txt",
            ]:
                (state_dir / name).write_text("stale\n")

            purge = subprocess.run(
                [str(ROOT / "bin/talk2claude-faster-toggle"), "purge-state"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(purge.returncode, 0, purge.stderr)
            for name in [
                "talk2claude-faster.pid",
                "talk2claude-faster.log",
                "talk2claude-faster.status.json",
                "dictation.txt",
            ]:
                self.assertFalse((state_dir / name).exists(), name)

    def test_gnome_dictation_machine_readable_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {**os.environ, "XDG_RUNTIME_DIR": tmpdir}
            plain = subprocess.run(
                [str(ROOT / "bin/gnome-dictation"), "status", "--plain"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertEqual(plain.stdout.strip(), "idle")

            status = subprocess.run(
                [str(ROOT / "bin/gnome-dictation"), "status", "--json"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["state"], "idle")

    def test_manual_stt_qa_checklist_covers_required_desktop_paths(self):
        checklist = (ROOT / "docs/developer/STT_MANUAL_QA_CHECKLIST.md").read_text()
        for phrase in [
            "Clipboard Mode",
            "Preview Mode",
            "Typing Mode On X11",
            "Typing Mode On Wayland",
            "GNOME Hotkey Start/Stop/Finalize",
            "Missing Clipboard Tool Fallback",
        ]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, checklist)

    def test_audio_capture_defaults_to_pulse_then_alsa(self):
        from src.stt.runtime import audio_capture_candidates

        with mock.patch.dict(os.environ, {}, clear=True):
            candidates = audio_capture_candidates(16000)

        self.assertEqual(candidates[0][5], "pulse")
        self.assertEqual(candidates[1][5], "alsa")

    def test_audio_capture_backend_and_device_overrides(self):
        from src.stt.runtime import audio_capture_candidates

        with mock.patch.dict(
            os.environ,
            {"STT_AUDIO_BACKEND": "alsa", "STT_AUDIO_DEVICE": "hw:1,0"},
            clear=True,
        ):
            candidates = audio_capture_candidates(16000)

        self.assertEqual(len(candidates), 1)
        self.assertIn("alsa", candidates[0])
        self.assertIn("hw:1,0", candidates[0])

    def test_language_auto_normalizes_to_none(self):
        from src.stt.runtime import normalize_language

        self.assertIsNone(normalize_language("auto"))
        self.assertIsNone(normalize_language(""))
        self.assertEqual(normalize_language("en_US.UTF-8"), "en")
        self.assertEqual(normalize_language("en-US"), "en")
        self.assertEqual(normalize_language("pt-BR"), "pt")
        self.assertEqual(normalize_language("zh-Hant"), "zh")

    def test_auto_dispatcher_uses_package_imports(self):
        auto = (ROOT / "src/stt/faster_whisper_auto.py").read_text()
        self.assertNotIn("spec_from_file_location", auto)
        self.assertIn("import_mode_module", auto)

    def test_clipboard_and_typing_modes_use_shared_session_core(self):
        for path in [
            ROOT / "src/stt/faster_whisper_clipboard.py",
            ROOT / "src/stt/faster_whisper_typing.py",
        ]:
            with self.subTest(path=path):
                text = path.read_text()
                self.assertIn("FasterWhisperSession", text)
                self.assertNotIn("def audio_capture_thread", text)
                self.assertNotIn("def process_audio", text)
                self.assertNotIn("WhisperModel(", text)
                self.assertNotIn("webrtcvad", text)

    def test_stt_buffers_have_absolute_limits(self):
        for path in [
            ROOT / "src/stt/session.py",
        ]:
            with self.subTest(path=path):
                text = path.read_text()
                self.assertIn("STT_MAX_UTTERANCE_SECONDS", text)
                self.assertIn("STT_MAX_BUFFER_SECONDS", text)


class TestSayReadChunking(unittest.TestCase):
    def test_canonical_chunks_enforces_hard_max(self):
        from src.tts.say_read import canonical_chunks

        text = " ".join(["word"] * 500)
        chunks = canonical_chunks(text, target_size=80, lang="en-us")
        self.assertTrue(chunks)
        self.assertLessEqual(max(len(chunk) for chunk in chunks), 160)

    def test_parentheticals_are_not_forced_sentence_boundaries(self):
        from src.tts.say_read import canonical_chunks

        text = "This sentence has a short aside (not a sentence ending) and continues naturally."
        chunks = canonical_chunks(text, target_size=120, lang="en-us")
        self.assertEqual(" ".join(chunks), text)


class TestPythonSyntax(unittest.TestCase):
    def test_core_python_files_compile(self):
        files = [
            ROOT / "src/utils/setup_models.py",
            ROOT / "src/utils/simple-audio-test.py",
            ROOT / "src/tts/say_read.py",
            ROOT / "src/tts/simple_parallel.py",
            ROOT / "src/stt/runtime.py",
            ROOT / "src/stt/session.py",
            ROOT / "src/stt/faster_whisper_auto.py",
            ROOT / "src/stt/faster_whisper_clipboard.py",
            ROOT / "src/stt/faster_whisper_typing.py",
        ]
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", *map(str, files)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
