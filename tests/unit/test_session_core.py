"""Behavioral tests for the FasterWhisperSession state machine (J6).

``src/stt/session.py`` is the shared dictation core: it reads raw int16 audio
frames, runs WebRTC VAD over them, buffers voiced audio, transcribes the buffer
with faster-whisper, and emits recognized text through an ``output_handler``.
Previously this was only covered by string-grep assertions. Here we drive the
real ``process_audio`` loop with synthetic audio and *faked* ``faster_whisper`` /
``webrtcvad`` modules to assert the VAD -> buffer -> transcribe -> output state
machine, including the SIGINT finalize path.

Hermetic by construction: no real model, audio device, or network is touched.
``faster_whisper`` and ``webrtcvad`` are replaced with in-memory fakes; numpy is
the only real dependency (it is a core install dep, always present in CI).
"""

import importlib
import sys
import threading
import types
from unittest import mock

import pytest

numpy = pytest.importorskip("numpy")


SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)  # 480 samples / 960 bytes
VAD_AMPLITUDE_THRESHOLD = 4000  # int16 level above which the fake VAD says "speech"


def _frame(amplitude):
    """Build one 30ms int16 PCM frame at a constant amplitude (as raw bytes)."""
    samples = numpy.full(FRAME_SAMPLES, amplitude, dtype=numpy.int16)
    return samples.tobytes()


SPEECH_FRAME = _frame(8000)   # above threshold -> voiced
SILENCE_FRAME = _frame(0)     # below threshold -> unvoiced


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeWhisperModel:
    """Records construction + transcribe calls; returns canned segments."""

    instances = []

    def __init__(self, model_size, device, compute_type):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.transcribe_calls = []
        self.result_text = "hello world"
        FakeWhisperModel.instances.append(self)

    def transcribe(self, audio, **kwargs):
        # ``audio`` is the np.concatenate of buffered float32 frames.
        self.transcribe_calls.append((audio, kwargs))
        return [FakeSegment(self.result_text)], types.SimpleNamespace()


class FakeParakeetModel:
    """Records recognize() calls; returns canned text (onnx-asr adapter API)."""

    def __init__(self):
        self.recognize_calls = []

    def recognize(self, audio, sample_rate):
        self.recognize_calls.append((audio, sample_rate))
        return "Hola mundo."


class FakeOnnxAsr:
    """Fake onnx_asr module: load_model(name, quantization=, providers=) -> model."""

    def __init__(self):
        self.model = FakeParakeetModel()

    def load_model(self, name, quantization=None, providers=None):
        return self.model


class FakeVad:
    """VAD that classifies a frame as speech by inspecting its amplitude.

    This keeps the test deterministic and tied to the actual frame bytes rather
    than to call ordering, so speech vs silence is driven by the data we enqueue.
    """

    def __init__(self, aggressiveness=2):
        self.aggressiveness = aggressiveness

    def is_speech(self, frame_bytes, sample_rate):
        samples = numpy.frombuffer(frame_bytes, dtype=numpy.int16)
        peak = int(numpy.abs(samples.astype(numpy.int32)).max()) if samples.size else 0
        return peak >= VAD_AMPLITUDE_THRESHOLD


@pytest.fixture
def session_module():
    """Import src.stt.session with faster_whisper + webrtcvad faked out."""
    fake_fw = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
    fake_vad = types.SimpleNamespace(Vad=FakeVad)
    FakeWhisperModel.instances = []
    with mock.patch.dict(sys.modules, {"faster_whisper": fake_fw, "webrtcvad": fake_vad}):
        sys.modules.pop("src.stt.session", None)
        module = importlib.import_module("src.stt.session")
        try:
            yield module
        finally:
            sys.modules.pop("src.stt.session", None)


def make_session(session_module, tmp_path, monkeypatch, outputs, **overrides):
    """Construct a FasterWhisperSession wired to capture emitted text.

    Status writes are redirected to a temp XDG_RUNTIME_DIR so the test never
    touches the user's runtime state. signal.signal is neutralized because the
    real handlers are irrelevant here (and would only work on the main thread).
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    # Neutralize signal registration done in __init__.
    monkeypatch.setattr(session_module.signal, "signal", lambda *a, **k: None)

    def output_handler(text):
        outputs.append(text)
        return True

    kwargs = dict(
        model_size="tiny",
        language="en",
        device="cpu",
        mode="test",
        output_handler=output_handler,
    )
    kwargs.update(overrides)
    return session_module.FasterWhisperSession(**kwargs)


def drain(session, frames, *, finalize=False):
    """Pre-load the audio queue and run one process_audio pass to completion.

    A trailing ``None`` sentinel ends the loop the same way the capture thread
    signals end-of-stream / shutdown.
    """
    for frame in frames:
        session.audio_queue.put(frame)
    if finalize:
        session.finalize_requested = True
    session.audio_queue.put(None)
    session.running = True
    session.process_audio()


def test_construction_uses_device_compute_type(session_module, tmp_path, monkeypatch):
    outputs = []
    session = make_session(session_module, tmp_path, monkeypatch, outputs)
    assert isinstance(session.model, FakeWhisperModel)
    # cpu -> int8 per compute_type_for_device
    assert session.model.compute_type == "int8"
    assert session.model.model_size == "tiny"
    assert session.frame_size == FRAME_SAMPLES


def test_speech_then_silence_triggers_transcription(session_module, tmp_path, monkeypatch):
    outputs = []
    session = make_session(
        session_module,
        tmp_path,
        monkeypatch,
        outputs,
        initial_prompt="Codex CLI",
        hotwords="pytest",
    )

    # Enough voiced frames to cross the speech ratio + speech_frames>10 gate,
    # then a long run of silence to exceed silence_threshold (20) and flush.
    frames = [SPEECH_FRAME] * 25 + [SILENCE_FRAME] * 25
    drain(session, frames)

    assert outputs == ["hello world"], "transcription was not emitted on silence flush"
    assert session.model.transcribe_calls, "model.transcribe was never called"
    _, kwargs = session.model.transcribe_calls[0]
    assert kwargs["beam_size"] == 5
    assert kwargs["vad_filter"] is True
    assert kwargs["initial_prompt"] == "Codex CLI"
    assert kwargs["hotwords"] == "pytest"
    # Recording state must be reset after a completed utterance.
    assert session.recording is False
    assert session.audio_buffer == []
    assert session.speech_frames == 0


def test_finalize_path_transcribes_buffered_speech(session_module, tmp_path, monkeypatch):
    outputs = []
    session = make_session(session_module, tmp_path, monkeypatch, outputs)

    # Speech with NO trailing silence: only the finalize (SIGINT-style) path can
    # flush this buffer. The None sentinel arrives with finalize_requested=True.
    frames = [SPEECH_FRAME] * 25
    drain(session, frames, finalize=True)

    assert outputs == ["hello world"], "finalize did not transcribe buffered speech"
    assert session.model.transcribe_calls


def test_session_routes_partial_and_hints_to_backend(session_module, tmp_path, monkeypatch):
    outputs = []
    partials = []
    callback = threading.Event()

    def partial_handler(text):
        partials.append(text)
        callback.set()

    session = make_session(
        session_module,
        tmp_path,
        monkeypatch,
        outputs,
        partial_handler=partial_handler,
        initial_prompt="Codex CLI",
        hotwords="pytest",
    )
    session.recording = True
    session.speech_frames = session.partial_min_frames
    session.audio_buffer = [numpy.zeros(FRAME_SAMPLES, dtype=numpy.float32)] * 11
    session.last_partial_at = 0

    session.maybe_emit_partial()
    assert callback.wait(timeout=2), "partial callback was not emitted"
    session.shutdown_partial_workers()

    assert partials == ["hello world"]
    _, kwargs = session.model.transcribe_calls[0]
    assert kwargs["beam_size"] == 1
    assert kwargs["vad_filter"] is False
    assert kwargs["initial_prompt"] == "Codex CLI"
    assert kwargs["hotwords"] == "pytest"


def test_natural_transcribe_lock_timeout_stops_session(
    session_module, tmp_path, monkeypatch
):
    outputs = []
    session = make_session(session_module, tmp_path, monkeypatch, outputs)
    session.finalization_lock_timeout = 0.01
    session.running = True
    session.speech_frames = 11
    session.audio_buffer = [numpy.zeros(FRAME_SAMPLES, dtype=numpy.float32)] * 11
    session.transcribe_lock.acquire()
    try:
        assert session.transcribe_buffer() is False
    finally:
        session.transcribe_lock.release()

    assert session.running is False
    assert "utterance transcription timed out" in session.transcription_error
    assert session.partial_shutdown.is_set() is False
    assert outputs == []


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "not-a-number"])
def test_invalid_max_duration_env_values_use_safe_defaults(
    session_module, tmp_path, monkeypatch, value
):
    monkeypatch.setenv("STT_MAX_UTTERANCE_SECONDS", value)
    monkeypatch.setenv("STT_MAX_BUFFER_SECONDS", value)

    session = make_session(session_module, tmp_path, monkeypatch, [])

    assert session.max_utterance_frames == 2000
    assert session.max_buffer_frames == 2500


def test_backend_exception_is_reported_as_finalization_failure(
    session_module, tmp_path, monkeypatch
):
    outputs = []
    session = make_session(session_module, tmp_path, monkeypatch, outputs)
    session.speech_frames = 11
    session.audio_buffer = [numpy.zeros(FRAME_SAMPLES, dtype=numpy.float32)] * 11
    session.engine.transcribe = mock.Mock(side_effect=RuntimeError("model crashed"))

    assert session.transcribe_buffer(finalizing=True) is False
    assert session.running is False
    assert "final transcription failed: model crashed" == session.transcription_error
    assert session.finalization_error == session.transcription_error
    assert outputs == []


def test_natural_boundary_backend_execution_timeout_is_bounded_and_output_safe(
    session_module, tmp_path, monkeypatch
):
    outputs = []
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    session = make_session(session_module, tmp_path, monkeypatch, outputs)
    session.transcription_timeout = 0.02
    session.set_status = mock.Mock()
    session.recording = True
    session.speech_frames = 20
    session.silence_frames = session.silence_threshold
    session.audio_buffer = [numpy.zeros(FRAME_SAMPLES, dtype=numpy.float32)] * 11

    def wedged_transcription(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2)
        finished.set()
        return "late natural result"

    session.engine.transcribe = wedged_transcription
    session.audio_queue.put(SILENCE_FRAME)
    session.running = True

    session.process_audio()

    assert started.is_set()
    assert session.running is False
    assert "utterance transcription timed out" in session.transcription_error
    assert session.partial_shutdown.is_set()
    assert outputs == []
    release.set()
    assert finished.wait(timeout=2)
    assert outputs == [], "a timed-out backend emitted stale natural-boundary text"


def test_finalize_backend_execution_timeout_is_bounded_and_output_safe(
    session_module, tmp_path, monkeypatch
):
    outputs = []
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    session = make_session(session_module, tmp_path, monkeypatch, outputs)
    session.transcription_timeout = 0.02
    session.set_status = mock.Mock()
    session.recording = True
    session.speech_frames = 20
    session.audio_buffer = [numpy.zeros(FRAME_SAMPLES, dtype=numpy.float32)] * 11

    def wedged_transcription(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2)
        finished.set()
        return "late finalize result"

    session.engine.transcribe = wedged_transcription
    session.finalize_requested = True
    session.audio_queue.put(None)
    session.running = True

    session.process_audio()

    assert started.is_set()
    assert session.running is False
    assert "final transcription timed out" in session.finalization_error
    assert session.partial_shutdown.is_set()
    assert outputs == []
    release.set()
    assert finished.wait(timeout=2)
    assert outputs == [], "a timed-out backend emitted stale finalized text"


def test_prompt_dictation_parser_and_main_pass_resolved_engine(monkeypatch):
    from src.stt import prompt_dictation

    captured = {}

    class FakeDictation:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return 0

    monkeypatch.setattr(prompt_dictation, "PromptDictation", FakeDictation)
    assert prompt_dictation.main(["--engine", "onnx-asr"]) == 0
    assert captured["engine"] == "parakeet"


def test_prompt_dictation_check_reports_resolved_engine(monkeypatch, capsys):
    from src.stt import prompt_dictation

    target = types.SimpleNamespace(kind="terminal", confidence="high", source="test")
    caps = types.SimpleNamespace(
        can_type=False,
        method="none",
        has_ydotool=False,
        has_xdotool=False,
        has_uinput_group=False,
        can_access_uinput=False,
    )
    monkeypatch.setattr(prompt_dictation, "detect_target", lambda _profile: target)
    monkeypatch.setattr(prompt_dictation, "check_typing_capability", lambda: caps)
    monkeypatch.setattr(prompt_dictation, "describe_audio_candidates", lambda: "pulse:default")

    args = prompt_dictation.build_parser().parse_args(
        ["--check", "--engine", "onnx-asr"]
    )
    assert prompt_dictation.check(args) == 0
    assert "Engine: parakeet" in capsys.readouterr().out


def test_silence_only_never_transcribes(session_module, tmp_path, monkeypatch):
    outputs = []
    session = make_session(session_module, tmp_path, monkeypatch, outputs)

    drain(session, [SILENCE_FRAME] * 40)

    assert outputs == [], "transcription emitted for silence-only input"
    assert session.model.transcribe_calls == []
    assert session.recording is False


def test_transcribe_buffer_guard_rejects_empty(session_module, tmp_path, monkeypatch):
    outputs = []
    session = make_session(session_module, tmp_path, monkeypatch, outputs)
    # No speech frames buffered -> transcribe_buffer must short-circuit to False.
    assert session.transcribe_buffer() is False
    assert session.model.transcribe_calls == []


def test_signal_handler_requests_finalize(session_module, tmp_path, monkeypatch):
    outputs = []
    session = make_session(session_module, tmp_path, monkeypatch, outputs)
    session.running = True
    session.signal_handler(2, None)  # SIGINT
    # The handler is async-signal-safe: it only sets flags. The processing loop
    # is responsible for noticing and finalizing (see test below).
    assert session.finalize_requested is True
    assert session.running is False


def test_signal_finalize_flushes_via_empty_queue(session_module, tmp_path, monkeypatch):
    """SIGINT during buffered speech finalizes through the queue.Empty branch.

    This mirrors the real shutdown sequence: frames are consumed, the signal sets
    flags only, and process_audio finalizes the active utterance on the next
    timed-out queue read (no None sentinel involved).
    """
    outputs = []
    session = make_session(session_module, tmp_path, monkeypatch, outputs)

    # Buffer speech but no trailing silence, then request finalize WITHOUT a
    # sentinel so termination must come from the queue.Empty + finalize branch.
    for frame in [SPEECH_FRAME] * 25:
        session.audio_queue.put(frame)
    session.running = True
    session.finalize_requested = True
    session.running = False  # signal_handler effect: flags only

    session.process_audio()

    assert outputs == ["hello world"], "finalize via empty queue did not transcribe"
    assert session.model.transcribe_calls


def test_status_payload_merges_mode_and_extra_fields(session_module, tmp_path, monkeypatch):
    outputs = []
    session = make_session(
        session_module,
        tmp_path,
        monkeypatch,
        outputs,
        status_fields=lambda: {"model": "tiny"},
    )
    payload = session.status_payload(error="boom", skip_me=None)
    assert payload["mode"] == "test"
    assert payload["model"] == "tiny"
    assert payload["error"] == "boom"
    # None-valued extras are dropped.
    assert "skip_me" not in payload


def test_lifecycle_callbacks_fire(session_module, tmp_path, monkeypatch):
    events = []
    outputs = []
    session = make_session(
        session_module,
        tmp_path,
        monkeypatch,
        outputs,
        on_listening=lambda: events.append("listening"),
        on_recording=lambda: events.append("recording"),
        on_processing=lambda: events.append("processing"),
    )

    frames = [SPEECH_FRAME] * 25 + [SILENCE_FRAME] * 25
    drain(session, frames)

    assert "listening" in events  # emitted at startup
    assert "recording" in events  # emitted when VAD opened the buffer
    assert "processing" in events  # emitted inside transcribe_buffer
    assert outputs == ["hello world"]


def test_session_routes_to_parakeet_engine(session_module, tmp_path, monkeypatch):
    """End-to-end: a session built with engine='parakeet' transcribes via onnx-asr.

    The fixture already fakes faster_whisper + webrtcvad; here we additionally fake
    onnx_asr so the Parakeet backend constructs without the optional dependency.
    This proves the refactor is genuinely engine-agnostic at the session layer,
    not just for faster-whisper.
    """
    fake_onnx = FakeOnnxAsr()
    monkeypatch.setitem(sys.modules, "onnx_asr", fake_onnx)
    monkeypatch.delenv("STT_PARAKEET_MODEL", raising=False)
    monkeypatch.delenv("STT_PARAKEET_QUANTIZATION", raising=False)

    outputs = []
    session = make_session(
        session_module, tmp_path, monkeypatch, outputs, engine="parakeet"
    )

    frames = [SPEECH_FRAME] * 25 + [SILENCE_FRAME] * 25
    drain(session, frames)

    assert outputs == ["Hola mundo."], "parakeet transcription was not emitted"
    assert fake_onnx.model.recognize_calls, "parakeet recognize() was never called"
    audio_arg, sample_rate = fake_onnx.model.recognize_calls[0]
    assert sample_rate == 16000
    assert audio_arg.dtype == numpy.float32
