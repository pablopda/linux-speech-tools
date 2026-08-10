"""Hermetic tests for private LATAM ASR corpus acquisition."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import stat
import subprocess
import threading
import time
import wave
from pathlib import Path

import pytest

from src.utils import asr_benchmark
from src.utils import asr_corpus_acquisition as corpus


ROOT = Path(__file__).resolve().parents[2]


def _private_reference(workspace, text="referencia revisada"):
    path = workspace / "reference.txt"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def _write_wav(path, *, sample=0, frames=1600):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(int(sample).to_bytes(2, "little", signed=True) * frames)


def _fake_recorder(sample=0):
    def record(_source, output_path, _seconds, _executable):
        _write_wav(output_path, sample=sample)

    return record


def _load_private_manifest(workspace):
    return json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))


def _complete_workspace(workspace):
    clips = _load_private_manifest(workspace)
    for index, clip in enumerate(clips):
        audio = Path(clip["audio"])
        _write_wav(audio, sample=index + 1)
        audio.chmod(0o600)
        inspected = asr_benchmark._inspect_audio_file(audio)
        clip.update(
            {
                "reference": "" if clip["category"] == "silence" else "texto revisado",
                "reference_reviewed": True,
                "speaker_id": f"speaker-{clip['accent'].lower()}-01",
                "accent_authenticity": "confirmed",
                "consent": "recorded",
                "privacy_review": "passed",
                "sha256": inspected["sha256"],
                "sample_rate": inspected["sample_rate"],
                "channels": inspected["channels"],
                "duration_s": inspected["duration_s"],
                "capture_device": "device-01",
                "recording_environment": "private-local",
            }
        )
    corpus._atomic_write_manifest(workspace / "manifest.json", clips)
    return clips


def _record(workspace, clip_id="ar-001", speaker_id="speaker-ar-01", **kwargs):
    reference = kwargs.pop("reference_file", None)
    if reference is None:
        reference = _private_reference(workspace)
    return corpus.record_clip(
        workspace,
        clip_id=clip_id,
        source="default",
        reference_file=reference,
        consent_confirmed=True,
        reference_reviewed=True,
        authentic_accent_confirmed=True,
        speaker_id=speaker_id,
        recorder=kwargs.pop("recorder", _fake_recorder()),
        **kwargs,
    )


def test_init_creates_exact_private_external_plan_and_resumes(tmp_path):
    workspace = tmp_path / "latam-private"

    status = corpus.initialize_workspace(workspace)
    clips = _load_private_manifest(workspace)

    assert status["clips"] == 36
    assert status["accent_plan"] == {"AR": 24, "MX": 4, "CO": 4, "CL": 4}
    assert [clip["accent"] for clip in clips].count("AR") == 24
    assert [clip["accent"] for clip in clips].count("MX") == 4
    assert [clip["accent"] for clip in clips].count("CO") == 4
    assert [clip["accent"] for clip in clips].count("CL") == 4
    assert {clip["category"] for clip in clips} == set(corpus._CATEGORY_PLAN)
    assert sum(bool(clip["code_switch"]) for clip in clips) == 1
    assert all(clip["speaker_id"] == "pending" for clip in clips)
    assert all(clip["accent_authenticity"] == "pending" for clip in clips)
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    assert stat.S_IMODE((workspace / "audio").stat().st_mode) == 0o700
    assert stat.S_IMODE((workspace / "manifest.json").stat().st_mode) == 0o600
    lock = workspace / corpus.LOCK_NAME
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert lock.stat().st_nlink == 1
    assert corpus.initialize_workspace(workspace)["states"] == {"not-recorded": 36}


def test_init_failure_after_managed_directories_is_retryable(monkeypatch, tmp_path):
    workspace = tmp_path / "latam-private"
    original_writer = corpus._atomic_write_manifest
    calls = 0

    def fail_once(path, clips):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(f"private init failure at {workspace}")
        return original_writer(path, clips)

    monkeypatch.setattr(corpus, "_atomic_write_manifest", fail_once)
    with pytest.raises(OSError):
        corpus.initialize_workspace(workspace)

    assert (workspace / corpus.LOCK_NAME).is_file()
    assert not (workspace / corpus.MANIFEST_NAME).exists()
    assert not (workspace / corpus.AUDIO_DIRECTORY_NAME).exists()

    status = corpus.initialize_workspace(workspace)
    assert status["states"] == {"not-recorded": 36}
    assert calls == 2


@pytest.mark.parametrize("kind", ["relative", "repo", "symlink", "fifo", "public"])
def test_init_refuses_repo_symlink_fifo_and_unsafe_paths(tmp_path, kind):
    if kind == "relative":
        path = Path("private-corpus")
    elif kind == "repo":
        path = ROOT / "private-corpus-must-not-exist"
    elif kind == "symlink":
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        path = tmp_path / "linked"
        path.symlink_to(target, target_is_directory=True)
    elif kind == "fifo":
        path = tmp_path / "fifo"
        os.mkfifo(path)
    else:
        path = tmp_path / "public"
        path.mkdir(mode=0o777)
        path.chmod(0o777)

    with pytest.raises(corpus.CorpusAcquisitionError):
        corpus.initialize_workspace(path)


def test_record_requires_each_explicit_human_gate(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    reference = _private_reference(workspace)
    base = {
        "directory": workspace,
        "clip_id": "ar-001",
        "source": "default",
        "reference_file": reference,
        "speaker_id": "speaker-ar-01",
        "recorder": _fake_recorder(),
    }

    with pytest.raises(corpus.CorpusAcquisitionError, match="consent"):
        corpus.record_clip(
            **base,
            consent_confirmed=False,
            reference_reviewed=True,
            authentic_accent_confirmed=True,
        )
    with pytest.raises(corpus.CorpusAcquisitionError, match="reference-reviewed"):
        corpus.record_clip(
            **base,
            consent_confirmed=True,
            reference_reviewed=False,
            authentic_accent_confirmed=True,
        )
    with pytest.raises(corpus.CorpusAcquisitionError, match="authentic-accent"):
        corpus.record_clip(
            **base,
            consent_confirmed=True,
            reference_reviewed=True,
            authentic_accent_confirmed=False,
        )
    with pytest.raises(corpus.CorpusAcquisitionError, match="speaker id"):
        corpus.record_clip(
            **dict(base, speaker_id=None),
            consent_confirmed=True,
            reference_reviewed=True,
            authentic_accent_confirmed=True,
        )


def test_record_hashes_metadata_and_status_redacts_private_content(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    private_reference = "texto privado que no debe aparecer"
    reference = _private_reference(workspace, private_reference)

    result = _record(
        workspace,
        reference_file=reference,
        recorder=_fake_recorder(sample=12),
    )
    clips = _load_private_manifest(workspace)
    clip = clips[0]
    audio = Path(clip["audio"])
    status = corpus.workspace_status(workspace)

    assert result == {"id": "ar-001", "state": "privacy-review-pending"}
    assert clip["reference"] == private_reference
    assert clip["reference_reviewed"] is True
    assert clip["consent"] == "recorded"
    assert clip["accent_authenticity"] == "confirmed"
    assert clip["privacy_review"] == "pending"
    assert clip["sample_rate"] == 16000
    assert clip["channels"] == 1
    assert clip["duration_s"] == pytest.approx(0.1)
    assert len(clip["sha256"]) == 64
    assert stat.S_IMODE(audio.stat().st_mode) == 0o600
    assert status["states"]["privacy-review-pending"] == 1
    rendered = json.dumps(status)
    assert private_reference not in rendered
    assert str(workspace) not in rendered
    assert "speaker-ar-01" not in rendered
    assert "default" not in rendered


def test_post_capture_privacy_review_is_separate_and_explicit(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    _record(workspace)

    with pytest.raises(corpus.CorpusAcquisitionError, match="post-capture"):
        corpus.confirm_privacy_review(
            workspace, clip_id="ar-001", privacy_reviewed=False
        )
    assert corpus.confirm_privacy_review(
        workspace, clip_id="ar-001", privacy_reviewed=True
    ) == {"id": "ar-001", "state": "ready"}
    assert corpus.workspace_status(workspace)["states"]["ready"] == 1


def test_speaker_token_cannot_cross_authentic_accents(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    _record(workspace, clip_id="ar-001", speaker_id="speaker-ar-01")
    called = []

    with pytest.raises(corpus.CorpusAcquisitionError, match="match the clip accent"):
        _record(
            workspace,
            clip_id="mx-001",
            speaker_id="speaker-ar-01",
            recorder=lambda *args: called.append(args),
        )
    assert called == []


def test_rerecord_is_explicit_transactional_and_resets_privacy_review(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    _record(workspace, recorder=_fake_recorder(sample=1))
    corpus.confirm_privacy_review(workspace, clip_id="ar-001", privacy_reviewed=True)
    original_audio = Path(_load_private_manifest(workspace)[0]["audio"])
    original_bytes = original_audio.read_bytes()
    original_manifest = (workspace / "manifest.json").read_bytes()

    with pytest.raises(corpus.CorpusAcquisitionError, match="--replace"):
        _record(workspace, recorder=_fake_recorder(sample=2))

    def failed_capture(*_args):
        raise corpus.CorpusAcquisitionError("fake capture failure")

    with pytest.raises(corpus.CorpusAcquisitionError, match="fake capture"):
        _record(workspace, replace=True, recorder=failed_capture)
    assert original_audio.read_bytes() == original_bytes
    assert (workspace / "manifest.json").read_bytes() == original_manifest

    _record(workspace, replace=True, recorder=_fake_recorder(sample=2))
    updated = _load_private_manifest(workspace)[0]
    assert original_audio.read_bytes() != original_bytes
    assert updated["privacy_review"] == "pending"


def test_rerecord_rolls_back_audio_when_manifest_commit_fails(monkeypatch, tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    _record(workspace, recorder=_fake_recorder(sample=1))
    manifest = workspace / "manifest.json"
    original_manifest = manifest.read_bytes()
    audio = Path(_load_private_manifest(workspace)[0]["audio"])
    original_audio = audio.read_bytes()

    def fail_manifest_commit(_path, _clips):
        raise OSError("simulated manifest commit failure")

    monkeypatch.setattr(corpus, "_atomic_write_manifest", fail_manifest_commit)
    with pytest.raises(OSError, match="simulated manifest"):
        _record(workspace, replace=True, recorder=_fake_recorder(sample=2))

    assert audio.read_bytes() == original_audio
    assert manifest.read_bytes() == original_manifest


def test_new_capture_stays_consistent_after_manifest_directory_fsync_failure(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    original_fsync_directory = corpus._fsync_directory

    def fail_manifest_directory(path):
        if Path(path) == workspace:
            raise OSError(f"private path must stay redacted: {workspace}")
        original_fsync_directory(path)

    monkeypatch.setattr(corpus, "_fsync_directory", fail_manifest_directory)
    with pytest.raises(corpus.CorpusAcquisitionError, match="committed.*durability"):
        _record(workspace, recorder=_fake_recorder(sample=31))

    clip = _load_private_manifest(workspace)[0]
    audio = Path(clip["audio"])
    assert audio.is_file()
    assert asr_benchmark._inspect_audio_file(audio)["sha256"] == clip["sha256"]
    assert corpus.workspace_status(workspace)["states"]["privacy-review-pending"] == 1
    assert list((workspace / "audio").glob("*.bak")) == []


def test_replacement_stays_consistent_after_manifest_directory_fsync_failure(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    _record(workspace, recorder=_fake_recorder(sample=1))
    original_audio = Path(_load_private_manifest(workspace)[0]["audio"])
    original_bytes = original_audio.read_bytes()
    original_fsync_directory = corpus._fsync_directory

    def fail_manifest_directory(path):
        if Path(path) == workspace:
            raise OSError("simulated post-replace directory fsync failure")
        original_fsync_directory(path)

    monkeypatch.setattr(corpus, "_fsync_directory", fail_manifest_directory)
    with pytest.raises(corpus.CorpusAcquisitionError, match="committed.*durability"):
        _record(workspace, replace=True, recorder=_fake_recorder(sample=41))

    clip = _load_private_manifest(workspace)[0]
    assert original_audio.read_bytes() != original_bytes
    assert asr_benchmark._inspect_audio_file(original_audio)["sha256"] == clip["sha256"]
    assert clip["privacy_review"] == "pending"
    assert list((workspace / "audio").glob("*.bak")) == []


def test_concurrent_recordings_are_serialized_without_lost_manifest_updates(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    first_reference = workspace / "reference-one.txt"
    second_reference = workspace / "reference-two.txt"
    for path in (first_reference, second_reference):
        path.write_text("referencia revisada", encoding="utf-8")
        path.chmod(0o600)

    first_capture_started = threading.Event()
    allow_first_capture = threading.Event()
    second_operation_started = threading.Event()
    second_capture_started = threading.Event()

    def first_recorder(_source, output_path, _seconds, _executable):
        first_capture_started.set()
        assert allow_first_capture.wait(timeout=5)
        _write_wav(output_path, sample=51)

    def second_recorder(_source, output_path, _seconds, _executable):
        second_capture_started.set()
        _write_wav(output_path, sample=61)

    def run_second_recording():
        second_operation_started.set()
        return _record(
            workspace,
            clip_id="ar-002",
            reference_file=second_reference,
            recorder=second_recorder,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            _record,
            workspace,
            clip_id="ar-001",
            reference_file=first_reference,
            recorder=first_recorder,
        )
        assert first_capture_started.wait(timeout=5)
        second = executor.submit(run_second_recording)
        assert second_operation_started.wait(timeout=5)
        assert not second_capture_started.wait(timeout=0.25)
        allow_first_capture.set()
        assert first.result(timeout=5)["id"] == "ar-001"
        assert second.result(timeout=5)["id"] == "ar-002"

    clips = _load_private_manifest(workspace)
    assert clips[0]["sha256"] != "pending"
    assert clips[1]["sha256"] != "pending"
    assert clips[0]["sha256"] != clips[1]["sha256"]


def test_fake_ffmpeg_capture_receives_safe_pulse_wav_contract(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    fake_ffmpeg = tmp_path / "fake-ffmpeg"
    fake_ffmpeg.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, wave\n"
        "with wave.open(sys.argv[-1], 'wb') as output:\n"
        "    output.setnchannels(1)\n"
        "    output.setsampwidth(2)\n"
        "    output.setframerate(16000)\n"
        "    output.writeframes(b'\\x00\\x00' * 1600)\n",
        encoding="utf-8",
    )
    fake_ffmpeg.chmod(0o700)

    result = corpus.record_clip(
        workspace,
        clip_id="ar-001",
        source="fake-pulse-source",
        reference_file=_private_reference(workspace),
        consent_confirmed=True,
        reference_reviewed=True,
        authentic_accent_confirmed=True,
        speaker_id="speaker-ar-01",
        max_seconds=1,
        ffmpeg=str(fake_ffmpeg),
    )

    assert result["state"] == "privacy-review-pending"
    assert _load_private_manifest(workspace)[0]["capture_device"] == "fake-pulse-source"


def test_capture_deadline_terminates_process_group(monkeypatch, tmp_path):
    events = []

    class TimedOutProcess:
        pid = 4321

        def __init__(self):
            self.waits = 0

        def wait(self, timeout):
            events.append(("wait", timeout))
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("fake", timeout)
            return -15

    monkeypatch.setattr(
        corpus.subprocess, "Popen", lambda *args, **kwargs: TimedOutProcess()
    )
    monkeypatch.setattr(corpus.os, "killpg", lambda pid, sig: events.append((pid, sig)))

    with pytest.raises(corpus.CorpusAcquisitionError, match="hard deadline"):
        corpus._capture_with_ffmpeg("default", tmp_path / "out.wav", 1, "/fake/ffmpeg")
    assert (4321, corpus.signal.SIGTERM) in events
    assert (4321, corpus.signal.SIGKILL) in events


def test_workspace_rejects_manifest_symlink_and_forbidden_name_field(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    manifest = workspace / "manifest.json"
    external = tmp_path / "external.json"
    external.write_bytes(manifest.read_bytes())
    external.chmod(0o600)
    manifest.unlink()
    manifest.symlink_to(external)
    with pytest.raises(corpus.CorpusAcquisitionError, match="symlink|regular"):
        corpus.workspace_status(workspace)

    manifest.unlink()
    manifest.write_bytes(external.read_bytes())
    manifest.chmod(0o600)
    clips = _load_private_manifest(workspace)
    clips[0]["speaker_name"] = "must never be retained"
    corpus._atomic_write_manifest(manifest, clips)
    with pytest.raises(corpus.CorpusAcquisitionError, match="speaker names"):
        corpus.workspace_status(workspace)


def test_workspace_rejects_nonprivate_or_hardlinked_lock(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    lock = workspace / corpus.LOCK_NAME

    lock.chmod(0o644)
    with pytest.raises(corpus.CorpusAcquisitionError, match="lock.*0600"):
        corpus.workspace_status(workspace)

    lock.chmod(0o600)
    linked = tmp_path / "linked-corpus-lock"
    os.link(lock, linked)
    with pytest.raises(corpus.CorpusAcquisitionError, match="one hard link"):
        corpus.workspace_status(workspace)


def test_workspace_lock_wait_is_bounded_and_redacted(monkeypatch, tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    paths = corpus._workspace_paths(workspace)
    monkeypatch.setattr(corpus, "_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(corpus, "_LOCK_POLL_SECONDS", 0.005)

    started = time.monotonic()
    with corpus._workspace_lock(paths, exclusive=True):
        with pytest.raises(corpus.CorpusAcquisitionError, match="busy; retry") as error:
            corpus.workspace_status(workspace)
    elapsed = time.monotonic() - started

    assert str(workspace) not in str(error.value)
    assert 0.04 <= elapsed < 1.0


def test_record_refuses_reference_and_audio_special_files(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    reference_target = _private_reference(workspace)
    linked_reference = workspace / "reference-link.txt"
    linked_reference.symlink_to(reference_target)
    with pytest.raises(corpus.CorpusAcquisitionError, match="symlink"):
        _record(workspace, reference_file=linked_reference)

    fifo_reference = workspace / "reference.fifo"
    os.mkfifo(fifo_reference)
    with pytest.raises(corpus.CorpusAcquisitionError, match="regular file"):
        _record(workspace, reference_file=fifo_reference)

    audio_path = Path(_load_private_manifest(workspace)[0]["audio"])
    outside_audio = tmp_path / "outside.wav"
    _write_wav(outside_audio)
    audio_path.symlink_to(outside_audio)
    with pytest.raises(corpus.CorpusAcquisitionError, match="symlink|regular file"):
        _record(workspace, reference_file=reference_target, replace=True)


def test_record_rejects_oversized_fake_capture(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)

    def oversized(_source, output_path, _seconds, _executable):
        with output_path.open("wb") as output:
            output.truncate(corpus._MAX_CAPTURE_BYTES + 1)

    with pytest.raises(corpus.CorpusAcquisitionError, match="safe regular file"):
        _record(workspace, recorder=oversized)


def test_validate_handoff_accepts_only_complete_exact_corpus(tmp_path):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    _complete_workspace(workspace)

    summary = corpus.validate_workspace(workspace)

    assert summary["accepted"] is True
    assert summary["clips"] == 36
    assert summary["accents"] == {"AR": 24, "MX": 4, "CO": 4, "CL": 4}
    rendered = json.dumps(summary)
    assert str(workspace) not in rendered
    assert "texto revisado" not in rendered
    assert "speaker-ar-01" not in rendered


@pytest.mark.parametrize("unsafe_kind", ["mode", "hardlink"])
def test_validate_rejects_any_nonprivate_audio_before_acceptance(tmp_path, unsafe_kind):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    clips = _complete_workspace(workspace)
    audio = Path(clips[17]["audio"])

    if unsafe_kind == "mode":
        audio.chmod(0o644)
    else:
        os.link(audio, tmp_path / "linked-private-audio.wav")

    with pytest.raises(
        ValueError,
        match="audio validation failed",
    ):
        corpus.validate_workspace(workspace)


@pytest.mark.parametrize("mutation", ["chmod", "hardlink"])
def test_validate_rejects_transient_inode_mutation_during_pinned_inspection(
    monkeypatch, tmp_path, mutation
):
    workspace = tmp_path / "latam-private"
    corpus.initialize_workspace(workspace)
    clips = _complete_workspace(workspace)
    audio = Path(clips[0]["audio"])
    transient_link = tmp_path / "transient-audio-link.wav"
    original_inspector = asr_benchmark._inspect_audio_fd
    mutated = False

    def inspect_while_mutating(fd):
        nonlocal mutated
        if mutated:
            return original_inspector(fd)
        mutated = True
        if mutation == "chmod":
            audio.chmod(0o644)
        else:
            os.link(audio, transient_link)
        try:
            return original_inspector(fd)
        finally:
            if mutation == "chmod":
                audio.chmod(0o600)
            else:
                transient_link.unlink()

    monkeypatch.setattr(asr_benchmark, "_inspect_audio_fd", inspect_while_mutating)
    with pytest.raises(ValueError, match="audio validation failed"):
        corpus.validate_workspace(workspace)

    assert mutated is True
    assert stat.S_IMODE(audio.stat().st_mode) == 0o600
    assert audio.stat().st_nlink == 1


def test_cli_redacts_unexpected_oserror_details(monkeypatch, capsys, tmp_path):
    private_detail = f"sensitive transcript path {tmp_path}/reference.txt"

    def fail_initialize(_directory):
        raise OSError(private_detail)

    monkeypatch.setattr(corpus, "initialize_workspace", fail_initialize)
    with pytest.raises(SystemExit):
        corpus.main(["init", "--directory", str(tmp_path / "private-corpus")])

    stderr = capsys.readouterr().err
    assert private_detail not in stderr
    assert str(tmp_path) not in stderr
    assert "invalid or unsafe request" in stderr


def test_argparse_never_echoes_unexpected_private_arguments(capsys, tmp_path):
    private_argument = f"--unexpected={tmp_path}/spoken private transcript"

    with pytest.raises(SystemExit):
        corpus.main(
            [
                "status",
                "--directory",
                str(tmp_path / "private-corpus"),
                private_argument,
            ]
        )

    stderr = capsys.readouterr().err
    assert private_argument not in stderr
    assert str(tmp_path) not in stderr
    assert "spoken private transcript" not in stderr
    assert stderr == "lst-asr-corpus: error: invalid or unsafe request\n"


def test_launcher_and_installer_include_corpus_command():
    launcher = ROOT / "bin" / "lst-asr-corpus"
    assert launcher.exists()
    assert os.access(launcher, os.X_OK)
    launcher_text = launcher.read_text(encoding="utf-8")
    assert "/usr/share/linux-speech-tools" in launcher_text
    assert 'uv --project "$PROJECT_ROOT" run --locked' in launcher_text
    assert launcher_text.index("linux-speech-tools-env") < launcher_text.index(
        'uv --project "$PROJECT_ROOT"'
    )
    assert "lst-asr-corpus" in (ROOT / "scripts/install/install-with-uv.sh").read_text(
        encoding="utf-8"
    )


def test_packaged_launcher_honors_persisted_uv_project_environment(tmp_path):
    package = tmp_path / "package"
    package_bin = package / "bin"
    runtime = package / "src" / "utils"
    fake_bin = tmp_path / "fake-bin"
    config_home = tmp_path / "config"
    config_dir = config_home / "linux-speech-tools"
    uv_log = tmp_path / "uv.log"
    package_bin.mkdir(parents=True)
    runtime.mkdir(parents=True)
    fake_bin.mkdir()
    config_dir.mkdir(parents=True, mode=0o700)
    config_home.chmod(0o700)
    config_dir.chmod(0o700)
    (runtime / "asr_corpus_acquisition.py").write_text("# test runtime\n")
    for name in ("lst-asr-corpus", "linux-speech-tools-env"):
        source = ROOT / "bin" / name
        target = package_bin / name
        target.write_bytes(source.read_bytes())
        target.chmod(0o700)

    uv_environment = tmp_path / "user-data" / "uv-runtime"
    config = config_dir / "install.env"
    config.write_text(
        f"LST_PROJECT_ROOT={package}\nUV_PROJECT_ENVIRONMENT={uv_environment}\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n%s\\n" "$UV_PROJECT_ENVIRONMENT" "$*" > "$UV_LOG"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o700)

    completed = subprocess.run(
        [str(package_bin / "lst-asr-corpus"), "status", "--directory", "/private"],
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(config_home),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "UV_LOG": str(uv_log),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr
    lines = uv_log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == str(uv_environment)
    assert lines[1].startswith(f"--project {package} run --locked python -m ")
