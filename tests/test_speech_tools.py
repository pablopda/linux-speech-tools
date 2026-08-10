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
import threading
import time
import types
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CANONICAL_DICTATION_BINDING_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/"
)
PROMPT_DICTATION_BINDING_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/developer-prompt-dictation/"
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
            "lst-dictate",
            "lst-agent",
            "lst-asr-corpus",
            "lst-ibus-check",
            "lst-insertion-metrics",
            "dictate-prompt",
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
            ROOT / "bin/lst-dictate",
            ROOT / "bin/lst-agent",
            ROOT / "bin/lst-asr-corpus",
            ROOT / "bin/lst-ibus-check",
            ROOT / "bin/lst-insertion-metrics",
            ROOT / "bin/dictate-prompt",
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
            [str(ROOT / "bin/lst-dictate"), "--help"],
            [str(ROOT / "bin/lst-agent"), "--help"],
            [str(ROOT / "bin/lst-ibus-check"), "--help"],
            [str(ROOT / "bin/lst-insertion-metrics"), "--help"],
            [str(ROOT / "bin/dictate-prompt"), "--help"],
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
            [str(ROOT / "bin/lst-dictate"), "--definitely-unknown"],
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
        self.assertIn("stt-parakeet = [", pyproject)
        self.assertIn("onnx-asr[cpu,hub]", pyproject)
        self.assertIn("stt-parakeet-gpu = [", pyproject)
        self.assertIn("onnxruntime-gpu[cuda,cudnn]==1.23.2", pyproject)
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

    def test_source_packages_use_canonical_release_path_only(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        release = (ROOT / ".github/workflows/release.yml").read_text()
        package_test = (ROOT / ".github/workflows/package-test.yml").read_text()
        self.assertNotIn("release:\n    types: [ published ]", ci)
        self.assertNotIn("actions/upload-release-asset", ci)
        self.assertNotIn("git archive --format=tar.gz", ci)
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
            "lst-dictate",
            "lst-agent",
            "lst-asr-corpus",
            "lst-ibus-check",
            "lst-insertion-metrics",
            "dictate-prompt",
            "linux-speech-tools-setup",
        ]:
            with self.subTest(launcher=launcher):
                text = (ROOT / "bin" / launcher).read_text()
                self.assertIn("/usr/share/linux-speech-tools", text)

    def test_uv_backed_launchers_load_the_persisted_project_environment(self):
        for launcher in (
            "say",
            "say-local",
            "say-read",
            "say-read-gnome",
            "talk2claude",
            "talk2claude-faster",
            "talk2claude-faster-toggle",
            "lst-dictate",
            "lst-agent",
            "linux-speech-tools-setup",
        ):
            with self.subTest(launcher=launcher):
                text = (ROOT / "bin" / launcher).read_text()
                helper = text.index("linux-speech-tools-env")
                uv_invocation = min(
                    position for position in (
                        text.find("uv --project"),
                        text.find("uv run"),
                        text.find("exec uv"),
                    ) if position >= 0
                )
                self.assertLess(helper, uv_invocation)

        helper = (ROOT / "bin/linux-speech-tools-env").read_text()
        self.assertIn("UV_PROJECT_ENVIRONMENT", helper)

    def test_read_only_packaged_runtime_uses_user_uv_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "package"
            installer_dir = package / "scripts" / "install"
            package_bin = package / "bin"
            fakebin = root / "fakebin"
            home = root / "home"
            data_home = home / "data"
            config_home = home / "config"
            uv_log = root / "uv.log"
            installer_dir.mkdir(parents=True)
            package_bin.mkdir()
            fakebin.mkdir()
            home.mkdir()

            shutil.copy2(
                ROOT / "scripts/install/install-with-uv.sh",
                installer_dir / "install-with-uv.sh",
            )
            for name in ("linux-speech-tools-env", "say"):
                shutil.copy2(ROOT / "bin" / name, package_bin / name)
            shutil.copy2(ROOT / "pyproject.toml", package / "pyproject.toml")
            shutil.copy2(ROOT / "uv.lock", package / "uv.lock")

            fake_uv = fakebin / "uv"
            fake_uv.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "if [ \"${1:-}\" = --version ]; then echo 'uv 0-test'; exit 0; fi\n"
                "printf '%s|%s\\n' \"${UV_PROJECT_ENVIRONMENT:-}\" \"$*\" >> \"$UV_LOG\"\n"
                "mkdir -p \"${UV_PROJECT_ENVIRONMENT:?missing user uv environment}\"\n"
            )
            fake_uv.chmod(0o755)

            for path in sorted(package.rglob("*"), reverse=True):
                path.chmod(0o555 if path.is_dir() or os.access(path, os.X_OK) else 0o444)
            package.chmod(0o555)

            result = subprocess.run(
                [
                    "bash",
                    str(installer_dir / "install-with-uv.sh"),
                    "--no-system-deps",
                    "--no-path-edit",
                    "--noninteractive",
                ],
                env={
                    **os.environ,
                    "HOME": str(home),
                    "XDG_CONFIG_HOME": str(config_home),
                    "XDG_DATA_HOME": str(data_home),
                    "PATH": f"{fakebin}:{os.environ['PATH']}",
                    "UV_LOG": str(uv_log),
                },
                capture_output=True,
                text=True,
                timeout=20,
            )

            expected_environment = data_home / "linux-speech-tools" / "uv-runtime"
            config = config_home / "linux-speech-tools" / "install.env"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(expected_environment.is_dir())
            self.assertFalse((package / ".venv").exists())
            self.assertIn(
                f"UV_PROJECT_ENVIRONMENT={expected_environment}",
                config.read_text(),
            )
            for line in uv_log.read_text().splitlines():
                self.assertTrue(line.startswith(f"{expected_environment}|"), line)

    def test_faster_toggle_finalizes_in_clipboard_mode(self):
        toggle = (ROOT / "bin/talk2claude-faster-toggle").read_text()
        self.assertIn('signal_validated_records INT "${PROCESS_TREE[@]}"', toggle)
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
        self.assertIn("lst-dictate", installer)
        self.assertIn("lst-agent", installer)
        self.assertIn("lst-asr-corpus", installer)
        self.assertIn("lst-ibus-check", installer)
        self.assertIn("lst-insertion-metrics", installer)
        self.assertIn('rm -f "$INSTALL_DIR/$name"', installer)

    def test_uv_installer_default_is_versioned_and_verified(self):
        installer = (ROOT / "scripts/install/install-with-uv.sh").read_text()
        versioned_url = "https://astral.sh/uv/0.11.32/install.sh"
        rolling_url = "https://astral.sh/uv/install.sh"
        expected_sha256 = (
            "43aff33a967fe40e8c17949d8c85c65bc43f3b5c94742393c957f56ab5ba80f4"
        )
        self.assertIn(
            f'local default_uv_installer_url="{versioned_url}"',
            installer,
        )
        self.assertNotIn(
            f'local default_uv_installer_url="{rolling_url}"',
            installer,
        )
        self.assertIn(
            f'local default_uv_installer_sha256="{expected_sha256}"',
            installer,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            fakebin = Path(tmpdir) / "bin"
            fakebin.mkdir()
            (fakebin / "dirname").symlink_to(shutil.which("dirname"))
            result = subprocess.run(
                [
                    shutil.which("bash"),
                    str(ROOT / "scripts/install/install-with-uv.sh"),
                    "--dry-run",
                    "--no-system-deps",
                    "--no-path-edit",
                ],
                env={**os.environ, "PATH": str(fakebin)},
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Would download {versioned_url}", result.stdout)
        self.assertIn(expected_sha256, result.stdout)
        self.assertNotIn(f"Would download {rolling_url}", result.stdout)

    def test_config_helper_loads_parakeet_values_literally(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_home = Path(tmpdir) / "config"
            config_dir = config_home / "linux-speech-tools"
            config_dir.mkdir(parents=True)
            config_home.chmod(0o700)
            config_dir.chmod(0o700)
            marker = Path(tmpdir) / "must-not-exist"
            expected = [
                "'parakeet'",
                f"nemo-$(touch {marker})-v3",
                r"int8\literal",
                "27.5",
            ]
            config = config_dir / "install.env"
            config.write_text(
                "\n".join(
                    [
                        f"STT_ENGINE={expected[0]}",
                        f"STT_PARAKEET_MODEL={expected[1]}",
                        f"STT_PARAKEET_QUANTIZATION={expected[2]}",
                        f"STT_TRANSCRIBE_TIMEOUT_SECONDS={expected[3]}",
                        "",
                    ]
                )
            )
            config.chmod(0o600)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    "source \"$1\"; printf '%s\\n' \"$STT_ENGINE\" "
                    "\"$STT_PARAKEET_MODEL\" \"$STT_PARAKEET_QUANTIZATION\" "
                    "\"$STT_TRANSCRIBE_TIMEOUT_SECONDS\"",
                    "bash",
                    str(ROOT / "bin/linux-speech-tools-env"),
                ],
                env={**os.environ, "XDG_CONFIG_HOME": str(config_home)},
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), expected)
        self.assertFalse(marker.exists())

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
            self.assertIn(PROMPT_DICTATION_BINDING_PATH, calls)
            self.assertNotIn(LEGACY_DICTATION_BINDING_PATH, calls)
            self.assertIn("Speech Dictation (Toggle)", calls)
            self.assertIn(f"command {install_dir / 'talk2claude-faster-toggle'}", calls)
            self.assertIn("Developer Prompt Dictation (Live)", calls)
            self.assertIn(f"command {install_dir / 'lst-dictate'} toggle", calls)
            self.assertTrue((install_dir / "setup-faster-hotkey.sh").exists())
            self.assertTrue((install_dir / "lst-dictate").exists())

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
            f"'{PROMPT_DICTATION_BINDING_PATH}', '{LEGACY_DICTATION_BINDING_PATH}']"
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
            for path in (
                CANONICAL_DICTATION_BINDING_PATH,
                PROMPT_DICTATION_BINDING_PATH,
                LEGACY_DICTATION_BINDING_PATH,
            ):
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
        # session.py imports numpy at module top; main()'s --engine validation
        # runs before any array work, so a bare stand-in is enough to let the
        # import succeed in the dependency-free release QA-gate environment.
        fake_numpy = types.ModuleType("numpy")
        with mock.patch.dict(
            sys.modules,
            {"faster_whisper": fake_fw, "webrtcvad": fake_vad, "numpy": fake_numpy},
        ):
            sys.modules.pop("src.stt.faster_whisper_clipboard", None)
            sys.modules.pop("src.stt.session", None)
            module = importlib.import_module("src.stt.faster_whisper_clipboard")
            self.__class__._fake_session_module = sys.modules.get("src.stt.session")
            return module

    def import_session_module_with_fakes(self):
        cached = getattr(self.__class__, "_fake_session_module", None)
        if cached is not None and callable(getattr(cached.np, "zeros", None)):
            return cached
        loaded = sys.modules.get("src.stt.session")
        if loaded is not None and callable(getattr(loaded.np, "zeros", None)):
            return loaded
        # The clipboard import helper deliberately uses a bare numpy module for
        # dependency-light engine-validation tests. Reload the session against
        # the real project numpy before running its array/concurrency tests.
        sys.modules.pop("src.stt.session", None)
        fake_vad = types.SimpleNamespace(Vad=mock.Mock())
        with mock.patch.dict(
            sys.modules,
            {"webrtcvad": fake_vad},
        ):
            module = importlib.import_module("src.stt.session")
            self.__class__._fake_session_module = module
            return module

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

    def test_auto_mode_help_and_diagnostics_expose_engine(self):
        help_result = subprocess.run(
            [sys.executable, "-m", "src.stt.faster_whisper_auto", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--engine", help_result.stdout)

        diag = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.stt.faster_whisper_auto",
                "--diagnose",
                "--engine",
                "parakeet",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(diag.returncode, 0, diag.stderr)
        output = diag.stdout + diag.stderr
        self.assertIn("Engine: parakeet", output)
        self.assertIn("Compute type: n/a", output)

    def test_auto_mode_rejects_unknown_engine(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.stt.faster_whisper_auto",
                "--check",
                "--engine",
                "bogus-engine",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown STT engine", result.stdout + result.stderr)

    def test_clipboard_main_rejects_unknown_engine(self):
        # The auto dispatcher forwards --engine to the clipboard/typing sub-module;
        # verify the sub-module's own main() also rejects a bad engine cleanly
        # (exit 2) before constructing any session/model.
        module = self.import_clipboard_module_with_fakes()
        with mock.patch.object(
            sys, "argv", ["faster_whisper_clipboard", "--engine", "bogus-engine"]
        ):
            with self.assertRaises(SystemExit) as ctx:
                module.main()
        self.assertEqual(ctx.exception.code, 2)

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
            with mock.patch.object(
                module, "detect_clipboard_tool", return_value="file"
            ):
                with mock.patch.dict(
                    os.environ, {"XDG_RUNTIME_DIR": tmpdir}, clear=True
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

    def test_lst_dictate_status_plain_json_and_purge_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {**os.environ, "XDG_RUNTIME_DIR": tmpdir}
            plain = subprocess.run(
                [str(ROOT / "bin/lst-dictate"), "status", "--plain"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertEqual(plain.stdout.strip(), "idle")

            status = subprocess.run(
                [str(ROOT / "bin/lst-dictate"), "status", "--json"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            data = json.loads(status.stdout)
            self.assertEqual(data["state"], "idle")
            self.assertEqual(data["mode"], "prompt")
            self.assertIn("log_file", data)
            self.assertIn("status_file", data)
            self.assertIn("exit_file", data)

            state_dir = Path(tmpdir) / "linux-speech-tools"
            state_dir.mkdir(exist_ok=True)
            for name in [
                "lst-dictate.pid",
                "lst-dictate.log",
                "lst-dictate.status.json",
                "lst-dictate.exit",
            ]:
                (state_dir / name).write_text("stale\n")

            purge = subprocess.run(
                [str(ROOT / "bin/lst-dictate"), "purge-state"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(purge.returncode, 0, purge.stderr)
            for name in [
                "lst-dictate.pid",
                "lst-dictate.log",
                "lst-dictate.status.json",
                "lst-dictate.exit",
            ]:
                self.assertFalse((state_dir / name).exists(), name)

    def test_prompt_dictation_target_and_paste_defaults(self):
        from src.stt.prompt_delivery import paste_key_for_target
        from src.stt.target_context import classify_context

        self.assertEqual(paste_key_for_target("claude"), "ctrl-shift-v")
        self.assertEqual(paste_key_for_target("codex"), "ctrl-shift-v")
        self.assertEqual(paste_key_for_target("ide"), "ctrl-v")
        self.assertEqual(paste_key_for_target("terminal", "shift-insert"), "shift-insert")

        claude = classify_context(title="Claude Code", wm_class="Alacritty")
        codex = classify_context(title="codex - ~/repo", wm_class="org.gnome.Terminal")
        ide = classify_context(title="main.py - Visual Studio Code", wm_class="code")
        self.assertEqual(claude.kind, "claude")
        self.assertEqual(codex.kind, "codex")
        self.assertEqual(ide.kind, "ide")

    def test_live_type_finalize_does_not_duplicate_completed_prompt(self):
        from src.stt.insertion_session import InsertionState, result
        from src.stt.prompt_delivery import LiveTypeRenderer

        clipboard = mock.Mock()
        clipboard.read.return_value = "original clipboard"
        clipboard.write.return_value = True
        input_controller = mock.Mock()
        input_controller.send_paste_key_result.return_value = result(
            InsertionState.DISPATCHED_UNCONFIRMED, "test"
        )
        input_controller.send_backspace_result.return_value = result(
            InsertionState.DISPATCHED_UNCONFIRMED, "test"
        )
        renderer = LiveTypeRenderer(
            "ctrl-shift-v",
            clipboard=clipboard,
            input_controller=input_controller,
            focus_guard=lambda: True,
        )

        self.assertTrue(renderer.update("completed prompt"))
        self.assertTrue(renderer.finalize("completed prompt"))

        clipboard.write.assert_called_once_with("completed prompt")
        input_controller.send_paste_key_result.assert_called_once_with(
            "ctrl-shift-v", None
        )
        input_controller.send_backspace_result.assert_not_called()

    def test_live_type_failure_after_preview_removal_locks_to_clipboard(self):
        from src.stt.insertion_session import InsertionState, result
        from src.stt.prompt_delivery import LiveTypeRenderer

        clipboard = mock.Mock()
        clipboard.read.return_value = None
        clipboard.write.side_effect = [True, False, True, True]
        input_controller = mock.Mock()
        input_controller.send_paste_key_result.return_value = result(
            InsertionState.DISPATCHED_UNCONFIRMED, "test"
        )
        input_controller.send_backspace_result.return_value = result(
            InsertionState.DISPATCHED_UNCONFIRMED, "test"
        )
        renderer = LiveTypeRenderer(
            "ctrl-v",
            clipboard=clipboard,
            input_controller=input_controller,
            focus_guard=lambda: True,
        )

        self.assertTrue(renderer.update("old"))
        failed = renderer.update("replacement")
        self.assertEqual(failed.state, InsertionState.AMBIGUOUS_AFTER_DISPATCH)
        self.assertEqual(renderer.rendered_text, "")
        self.assertTrue(renderer.update("replacement"))

        input_controller.send_backspace_result.assert_called_once_with(
            len("old"), None
        )
        self.assertEqual(renderer.mode, "clipboard-fallback")
        self.assertEqual(renderer.rendered_text, "")

    def test_live_type_focus_drift_falls_back_without_destructive_input(self):
        from src.stt.insertion_session import InsertionState, result
        from src.stt.prompt_delivery import LiveTypeRenderer

        clipboard = mock.Mock()
        clipboard.read.return_value = "original clipboard"
        clipboard.write.return_value = True
        input_controller = mock.Mock()
        input_controller.send_paste_key_result.return_value = result(
            InsertionState.DISPATCHED_UNCONFIRMED, "test"
        )
        input_controller.send_backspace_result.return_value = result(
            InsertionState.DISPATCHED_UNCONFIRMED, "test"
        )
        focus_guard = mock.Mock(side_effect=[True, True, False])
        renderer = LiveTypeRenderer(
            "ctrl-v",
            clipboard=clipboard,
            input_controller=input_controller,
            focus_guard=focus_guard,
        )

        self.assertTrue(renderer.update("old preview"))
        self.assertTrue(renderer.update("safe final text"))
        self.assertEqual(renderer.mode, "clipboard-fallback")
        self.assertFalse(renderer.can_submit())
        renderer.close()

        input_controller.send_backspace_result.assert_not_called()
        input_controller.send_paste_key_result.assert_called_once_with(
            "ctrl-v", None
        )
        self.assertEqual(clipboard.write.call_args_list[-1], mock.call("safe final text"))
        self.assertNotIn(mock.call("original clipboard"), clipboard.write.call_args_list)

    def test_live_type_unverifiable_focus_fails_closed_to_clipboard(self):
        from src.stt.prompt_delivery import LiveTypeRenderer

        clipboard = mock.Mock()
        clipboard.read.return_value = None
        clipboard.write.return_value = True
        input_controller = mock.Mock()
        renderer = LiveTypeRenderer(
            "ctrl-shift-v",
            clipboard=clipboard,
            input_controller=input_controller,
        )

        self.assertTrue(renderer.update("preserved prompt"))

        self.assertEqual(renderer.mode, "clipboard-fallback")
        clipboard.write.assert_called_once_with("preserved prompt")
        input_controller.send_backspace.assert_not_called()
        input_controller.send_paste_key.assert_not_called()

    def test_non_inserting_renderers_never_submit(self):
        from src.stt.prompt_delivery import (
            ClipboardRenderer,
            OverlayRenderer,
            StdoutRenderer,
        )
        from src.stt.prompt_dictation import PromptDictation

        input_controller = mock.Mock()
        dictation = PromptDictation.__new__(PromptDictation)
        dictation.input_controller = input_controller

        with mock.patch.object(OverlayRenderer, "_start_overlay", return_value=None):
            renderers = (
                ClipboardRenderer(mock.Mock()),
                OverlayRenderer(mock.Mock()),
                StdoutRenderer(),
            )

        for renderer in renderers:
            for submit, text in (
                ("always", "do the work"),
                ("voice-command", "do the work, submit"),
            ):
                with self.subTest(renderer=renderer.mode, submit=submit):
                    dictation.renderer = renderer
                    dictation.submit = submit
                    dictation.maybe_submit(text)

        input_controller.send_key_combo.assert_not_called()

    def test_lst_dictate_serializes_toggle_but_waits_outside_lock(self):
        script = (ROOT / "bin/lst-dictate").read_text()

        self.assertIn('LOCK_FILE="$STATE_DIR/lst-dictate.lock"', script)
        self.assertIn('exec 9>"$LOCK_FILE"', script)
        self.assertIn('stop_target="$(toggle_action "$@")"', script)
        unlock = script.index("flock -u 9")
        wait = script.index('wait_for_finalize "$stop_target"', unlock)
        self.assertLess(unlock, wait)

    def test_partial_worker_shutdown_cancels_late_callback_and_joins(self):
        session_module = self.import_session_module_with_fakes()
        session = session_module.FasterWhisperSession.__new__(
            session_module.FasterWhisperSession
        )
        started = threading.Event()
        release = threading.Event()
        shutdown_complete = threading.Event()
        callbacks = []

        def transcribe_audio(_audio, *, partial=False):
            self.assertTrue(partial)
            started.set()
            release.wait(timeout=5)
            return "late partial"

        session.partial_handler = callbacks.append
        session.recording = True
        session.speech_frames = 20
        session.partial_min_frames = 1
        session.audio_buffer = [session_module.np.zeros(10)] * 11
        session.partial_interval = 0.2
        session.last_partial_at = 0.0
        session.partial_generation = 0
        session.transcribe_lock = threading.Lock()
        session.partial_shutdown = threading.Event()
        session.partial_threads = set()
        session.partial_threads_lock = threading.Lock()
        session.partial_callback_lock = threading.Lock()
        session.partial_shutdown_timeout = 1.0
        session.transcribe_audio = transcribe_audio

        session.maybe_emit_partial()
        self.assertTrue(started.wait(timeout=2))
        shutdown_thread = threading.Thread(
            target=lambda: (
                session.shutdown_partial_workers(),
                shutdown_complete.set(),
            )
        )
        shutdown_thread.start()
        self.assertFalse(shutdown_complete.wait(timeout=0.05))
        release.set()
        shutdown_thread.join(timeout=2)

        self.assertTrue(shutdown_complete.is_set())
        self.assertEqual(callbacks, [])
        self.assertEqual(session.partial_threads, set())

    def test_wedged_partial_worker_shutdown_is_bounded_and_callback_safe(self):
        session_module = self.import_session_module_with_fakes()
        session = session_module.FasterWhisperSession.__new__(
            session_module.FasterWhisperSession
        )
        started = threading.Event()
        release = threading.Event()
        callbacks = []

        def transcribe_audio(_audio, *, partial=False):
            self.assertTrue(partial)
            started.set()
            release.wait(timeout=5)
            return "too late"

        session.partial_handler = callbacks.append
        session.recording = True
        session.speech_frames = 20
        session.partial_min_frames = 1
        session.audio_buffer = [session_module.np.zeros(10)] * 11
        session.partial_interval = 0.2
        session.last_partial_at = 0.0
        session.partial_generation = 0
        session.transcribe_lock = threading.Lock()
        session.partial_shutdown = threading.Event()
        session.partial_threads = set()
        session.partial_threads_lock = threading.Lock()
        session.partial_callback_lock = threading.Lock()
        session.partial_shutdown_timeout = 0.05
        session.transcribe_audio = transcribe_audio

        session.maybe_emit_partial()
        self.assertTrue(started.wait(timeout=2))
        with session.partial_threads_lock:
            worker = next(iter(session.partial_threads))

        before = time.monotonic()
        session.shutdown_partial_workers()
        elapsed = time.monotonic() - before

        self.assertLess(elapsed, 0.5)
        self.assertTrue(worker.is_alive())
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(callbacks, [])

    def test_finalize_path_times_out_wedged_partial_and_cleans_renderer(self):
        from src.stt import prompt_dictation
        from src.stt.target_context import TargetContext

        session_module = self.import_session_module_with_fakes()
        with mock.patch.object(
            session_module, "create_engine", return_value=mock.Mock()
        ), mock.patch.object(
            session_module.webrtcvad, "Vad", return_value=mock.Mock()
        ), mock.patch.object(session_module.signal, "signal"):
            session = session_module.FasterWhisperSession(
                mode="prompt",
                output_handler=lambda _text: True,
                partial_handler=lambda text: callbacks.append(text),
            )

        started = threading.Event()
        release = threading.Event()
        callbacks = []

        def transcribe_audio(_audio, *, partial=False):
            if partial:
                started.set()
                release.wait(timeout=5)
                return "late partial"
            return "final text"

        session.set_status = mock.Mock()
        session.recording = True
        session.speech_frames = 20
        session.audio_buffer = [session_module.np.zeros(10)] * 11
        session.partial_min_frames = 1
        session.partial_interval = 0.2
        session.last_partial_at = 0.0
        session.partial_shutdown_timeout = 0.05
        session.finalization_lock_timeout = 0.05
        session.transcribe_audio = transcribe_audio
        session.maybe_emit_partial()
        self.assertTrue(started.wait(timeout=2))
        with session.partial_threads_lock:
            worker = next(iter(session.partial_threads))

        def request_finalize():
            session.finalize_requested = True
            session.running = False
            session.audio_queue.put(None)

        session.audio_capture_thread = request_finalize

        renderer = mock.Mock(mode="live-type")
        input_controller = mock.Mock()
        dictation = prompt_dictation.PromptDictation.__new__(
            prompt_dictation.PromptDictation
        )
        dictation.profile = "codex"
        dictation.output = "live-type"
        dictation.submit = "always"
        dictation.target = TargetContext(
            kind="codex", confidence="explicit", source="profile"
        )
        dictation.confirmed_parts = []
        dictation.renderer = renderer
        dictation.input_controller = input_controller
        dictation.session = session

        before = time.monotonic()
        with mock.patch.object(prompt_dictation, "notify"):
            result = dictation.run()
        elapsed = time.monotonic() - before

        self.assertEqual(result, 1)
        self.assertLess(elapsed, 0.5)
        self.assertIn("timed out", session.finalization_error)
        renderer.finalize.assert_not_called()
        renderer.close.assert_called_once_with()
        input_controller.send_key_combo.assert_not_called()
        self.assertTrue(worker.is_alive())

        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(callbacks, [])

    def test_natural_flush_times_out_wedged_partial_without_signal(self):
        from src.stt import prompt_dictation
        from src.stt.target_context import TargetContext

        session_module = self.import_session_module_with_fakes()
        callbacks = []
        with mock.patch.object(
            session_module, "create_engine", return_value=mock.Mock()
        ), mock.patch.object(
            session_module.webrtcvad, "Vad", return_value=mock.Mock()
        ), mock.patch.object(session_module.signal, "signal"):
            session = session_module.FasterWhisperSession(
                mode="prompt",
                output_handler=lambda _text: True,
                partial_handler=callbacks.append,
            )

        started = threading.Event()
        release = threading.Event()

        def transcribe_audio(_audio, *, partial=False):
            if partial:
                started.set()
                release.wait(timeout=5)
                return "late partial"
            return "natural final text"

        session.set_status = mock.Mock()
        session.vad.is_speech.return_value = False
        session.recording = True
        session.speech_frames = 20
        session.silence_frames = session.silence_threshold
        session.audio_buffer = [session_module.np.zeros(10)] * 11
        session.partial_min_frames = 1
        session.partial_interval = 0.2
        session.last_partial_at = 0.0
        session.partial_shutdown_timeout = 0.05
        session.finalization_lock_timeout = 0.05
        session.transcribe_audio = transcribe_audio
        session.maybe_emit_partial()
        self.assertTrue(started.wait(timeout=2))
        with session.partial_threads_lock:
            worker = next(iter(session.partial_threads))

        audio_frame = b"\0" * (session.frame_size * 2)
        session.audio_capture_thread = lambda: session.audio_queue.put(audio_frame)

        renderer = mock.Mock(mode="live-type")
        input_controller = mock.Mock()
        dictation = prompt_dictation.PromptDictation.__new__(
            prompt_dictation.PromptDictation
        )
        dictation.profile = "codex"
        dictation.output = "live-type"
        dictation.submit = "always"
        dictation.target = TargetContext(
            kind="codex", confidence="explicit", source="profile"
        )
        dictation.confirmed_parts = []
        dictation.renderer = renderer
        dictation.input_controller = input_controller
        dictation.session = session

        before = time.monotonic()
        with mock.patch.object(prompt_dictation, "notify"):
            result = dictation.run()
        elapsed = time.monotonic() - before

        self.assertEqual(result, 1)
        self.assertLess(elapsed, 0.5)
        self.assertIn("utterance transcription timed out", session.transcription_error)
        self.assertIsNone(session.finalization_error)
        self.assertFalse(session.finalize_requested)
        renderer.finalize.assert_not_called()
        renderer.close.assert_called_once_with()
        input_controller.send_key_combo.assert_not_called()
        self.assertTrue(worker.is_alive())

        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(callbacks, [])

    def test_successful_natural_flush_allows_later_partial_generation(self):
        session_module = self.import_session_module_with_fakes()
        final_outputs = []
        partial_outputs = []
        partial_called = threading.Event()

        with mock.patch.object(
            session_module, "create_engine", return_value=mock.Mock()
        ), mock.patch.object(
            session_module.webrtcvad, "Vad", return_value=mock.Mock()
        ), mock.patch.object(session_module.signal, "signal"):
            session = session_module.FasterWhisperSession(
                mode="prompt",
                output_handler=lambda text: final_outputs.append(text) or True,
                partial_handler=lambda text: (
                    partial_outputs.append(text),
                    partial_called.set(),
                ),
            )

        def transcribe_audio(_audio, *, partial=False):
            return "later partial" if partial else "completed utterance"

        session.set_status = mock.Mock()
        session.speech_frames = 20
        session.audio_buffer = [session_module.np.zeros(10)] * 11
        session.transcribe_audio = transcribe_audio

        self.assertTrue(session.transcribe_buffer())
        self.assertFalse(session.partial_shutdown.is_set())
        self.assertEqual(final_outputs, ["completed utterance"])

        session.reset_recording_state()
        session.recording = True
        session.speech_frames = 20
        session.audio_buffer = [session_module.np.zeros(10)] * 11
        session.partial_min_frames = 1
        session.partial_interval = 0.2
        session.last_partial_at = 0.0
        session.maybe_emit_partial()

        self.assertTrue(partial_called.wait(timeout=2))
        session.shutdown_partial_workers()
        self.assertEqual(partial_outputs, ["later partial"])

    def test_focus_matches_requires_exact_stable_window_id(self):
        from src.stt import target_context

        current = target_context.TargetContext(window_id="window-42")
        with mock.patch.object(
            target_context, "live_focus_context", return_value=current
        ):
            self.assertTrue(
                target_context.focus_matches(
                    target_context.TargetContext(window_id="window-42")
                )
            )
            self.assertFalse(
                target_context.focus_matches(
                    target_context.TargetContext(window_id="window-99")
                )
            )
            self.assertFalse(target_context.focus_matches(target_context.TargetContext()))

    def test_invalid_prompt_vad_environment_falls_back_to_default(self):
        from src.stt.prompt_dictation import build_parser

        for value in ("invalid", "-1", "4"):
            with self.subTest(value=value):
                with mock.patch.dict(
                    os.environ, {"WHISPER_VAD": value}, clear=True
                ):
                    self.assertEqual(build_parser().parse_args([]).vad, 2)

    def test_invalid_session_timing_and_vad_values_fall_back_safely(self):
        session_module = self.import_session_module_with_fakes()
        vad_levels = []

        with mock.patch.dict(
            os.environ,
            {
                "STT_PARTIAL_INTERVAL_SECONDS": "nan",
                "STT_PARTIAL_MIN_SECONDS": "-inf",
                "STT_TRANSCRIBE_TIMEOUT_SECONDS": "inf",
            },
            clear=True,
        ), mock.patch.object(
            session_module, "create_engine", return_value=mock.Mock()
        ), mock.patch.object(
            session_module.webrtcvad,
            "Vad",
            side_effect=lambda level: vad_levels.append(level) or mock.Mock(),
        ), mock.patch.object(session_module.signal, "signal"):
            session = session_module.FasterWhisperSession(
                mode="prompt",
                output_handler=lambda _text: True,
                vad_aggressiveness=99,
            )

        self.assertEqual(vad_levels, [2])
        self.assertEqual(session.partial_interval, 1.2)
        self.assertEqual(session.partial_min_frames, 26)
        self.assertEqual(session.transcription_timeout, 30.0)

    def test_prompt_dictation_does_not_submit_after_finalize_failure(self):
        from src.stt import prompt_dictation
        from src.stt.target_context import TargetContext

        renderer = mock.Mock(mode="live-type")
        renderer.finalize.return_value = False
        session = mock.Mock()
        input_controller = mock.Mock()
        dictation = prompt_dictation.PromptDictation.__new__(
            prompt_dictation.PromptDictation
        )
        dictation.profile = "codex"
        dictation.output = "live-type"
        dictation.submit = "always"
        dictation.target = TargetContext(
            kind="codex", confidence="explicit", source="profile"
        )
        dictation.confirmed_parts = ["completed prompt"]
        dictation.renderer = renderer
        dictation.input_controller = input_controller
        dictation.session = session

        with mock.patch.object(prompt_dictation, "notify"):
            self.assertEqual(dictation.run(), 1)

        renderer.finalize.assert_called_once_with("completed prompt", 1)
        renderer.submit.assert_not_called()
        input_controller.send_key_combo.assert_not_called()
        renderer.close.assert_called_once_with()

    def test_auto_environment_profile_still_detects_target(self):
        from src.stt.target_context import detect_target

        context = json.dumps(
            {"title": "Claude Code", "wm_class": "Alacritty"}
        )
        with mock.patch.dict(
            os.environ,
            {
                "PROMPT_DICTATION_PROFILE": "auto",
                "LST_TARGET_CONTEXT_JSON": context,
            },
            clear=True,
        ):
            target = detect_target("auto")

        self.assertEqual(target.kind, "claude")
        self.assertEqual(target.confidence, "high")
        self.assertEqual(target.source, "env-json")

    def test_explicit_profile_overrides_auto_environment_default(self):
        from src.stt.prompt_dictation import build_parser
        from src.stt.target_context import detect_target

        with mock.patch.dict(
            os.environ, {"PROMPT_DICTATION_PROFILE": "auto"}, clear=True
        ):
            args = build_parser().parse_args(["--profile", "codex"])
            target = detect_target(args.profile)

        self.assertEqual(target.kind, "codex")
        self.assertEqual(target.confidence, "explicit")
        self.assertEqual(target.source, "profile")

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
            "Live Developer Prompt Dictation",
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


class TestSayReadSSRFGuard(unittest.TestCase):
    """Cover the security-critical SSRF guard in say_read.py without network.

    Only the literal-IP / scheme branches are exercised (no getaddrinfo), so
    these stay hermetic and fast while pinning the tricky logic: link-local
    (incl. the 169.254.169.254 cloud-metadata endpoint), loopback, private,
    ULA, and the IPv4-mapped-IPv6 unwrap must all be rejected.
    """

    def _guard(self):
        import importlib
        import ipaddress
        say_read = importlib.import_module("src.tts.say_read")
        return say_read._is_public_ip, say_read._assert_public_url, ipaddress

    def test_is_public_ip_accepts_public_and_rejects_internal(self):
        is_public, _, ipaddress = self._guard()
        for good in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"):
            self.assertTrue(is_public(ipaddress.ip_address(good)), good)
        for bad in (
            "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1",
            "169.254.169.254",  # cloud metadata endpoint
            "0.0.0.0", "::1", "fc00::1", "fe80::1",
        ):
            self.assertFalse(is_public(ipaddress.ip_address(bad)), bad)

    def test_is_public_ip_unwraps_ipv4_mapped_ipv6(self):
        is_public, _, ipaddress = self._guard()
        self.assertFalse(is_public(ipaddress.ip_address("::ffff:169.254.169.254")))
        self.assertFalse(is_public(ipaddress.ip_address("::ffff:127.0.0.1")))
        self.assertTrue(is_public(ipaddress.ip_address("::ffff:8.8.8.8")))

    def test_assert_public_url_rejects_non_http_scheme(self):
        _, assert_public_url, _ = self._guard()
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://h/"):
            with self.assertRaises(ValueError):
                assert_public_url(url)

    def test_assert_public_url_rejects_literal_internal_ip(self):
        _, assert_public_url, _ = self._guard()
        for url in (
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://10.0.0.5/admin",
            "https://192.168.1.1/",
        ):
            with self.assertRaises(ValueError):
                assert_public_url(url)


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
            ROOT / "src/stt/prompt_delivery.py",
            ROOT / "src/stt/prompt_dictation.py",
            ROOT / "src/stt/prompt_overlay.py",
            ROOT / "src/stt/target_context.py",
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
