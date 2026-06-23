#!/usr/bin/env python3
"""Shared faster-whisper dictation session core."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import warnings
from collections import deque
from typing import Callable, Dict, List, Optional

import numpy as np

try:
    from .runtime import (
        audio_capture_candidates,
        compute_type_for_device,
        normalize_language,
        write_status,
    )
except ImportError:
    from runtime import (
        audio_capture_candidates,
        compute_type_for_device,
        normalize_language,
        write_status,
    )

try:
    from faster_whisper import WhisperModel
except ImportError:
    print("Error: faster-whisper not installed", file=sys.stderr)
    sys.exit(1)

try:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pkg_resources is deprecated as an API.*",
            category=UserWarning,
        )
        import webrtcvad
except ImportError:
    print("Error: webrtcvad not installed", file=sys.stderr)
    sys.exit(1)


StatusFields = Callable[[], Dict[str, object]]
OutputHandler = Callable[[str], bool]


def _env_float(name: str, default: float) -> float:
    """Parse a float environment variable, falling back to default if unset/invalid."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class FasterWhisperSession:
    """Capture audio, detect speech, transcribe, and emit recognized text."""

    def __init__(
        self,
        *,
        model_size: str = "tiny",
        language: str = "en",
        device: str = "cpu",
        vad_aggressiveness: int = 2,
        mode: str,
        output_handler: OutputHandler,
        status_fields: Optional[StatusFields] = None,
        on_listening: Optional[Callable[[], None]] = None,
        on_recording: Optional[Callable[[], None]] = None,
        on_processing: Optional[Callable[[], None]] = None,
    ):
        self.mode = mode
        self.output_handler = output_handler
        self.status_fields = status_fields or (lambda: {})
        self.on_listening = on_listening
        self.on_recording = on_recording
        self.on_processing = on_processing

        self.compute_type = compute_type_for_device(device)
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=self.compute_type,
        )
        self.language = normalize_language(language)

        self.sample_rate = 16000
        self.frame_duration = 30
        self.frame_size = int(self.sample_rate * self.frame_duration / 1000)
        self.vad = webrtcvad.Vad(vad_aggressiveness)

        self.audio_buffer: List[np.ndarray] = []
        self.audio_preroll = deque(maxlen=10)
        self.voiced_buffer = deque(maxlen=10)
        self.recording = False
        self.silence_frames = 0
        self.speech_frames = 0

        self.speech_threshold = 0.5
        self.silence_threshold = 20
        self.max_utterance_frames = max(
            1,
            int(_env_float("STT_MAX_UTTERANCE_SECONDS", 60) * 1000 / self.frame_duration),
        )
        self.max_buffer_frames = max(
            self.max_utterance_frames,
            int(_env_float("STT_MAX_BUFFER_SECONDS", 75) * 1000 / self.frame_duration),
        )

        # Bound the queue so it cannot grow without limit while a multi-second
        # transcription blocks the consumer. Sized to comfortably hold a full
        # max-length utterance plus headroom; on overflow we drop the oldest
        # frame (see _enqueue_audio) rather than block the capture thread.
        self.audio_queue: queue.Queue = queue.Queue(maxsize=max(self.max_buffer_frames * 2, 200))
        self.running = False
        self.capture_error: Optional[str] = None
        self.finalize_requested = False
        self.capture_process: Optional[subprocess.Popen] = None

        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def status_payload(self, **extra: object) -> Dict[str, object]:
        payload = {"mode": self.mode}
        payload.update(self.status_fields())
        payload.update({key: value for key, value in extra.items() if value is not None})
        return payload

    def set_status(self, state: str, **extra: object) -> None:
        write_status(state, **self.status_payload(**extra))

    def _enqueue_audio(self, audio_bytes: bytes) -> None:
        """Enqueue a captured audio frame, dropping the oldest on overflow.

        Keeps the capture thread non-blocking when transcription stalls the
        consumer; preserving the newest frames keeps the active utterance intact.
        """
        try:
            self.audio_queue.put_nowait(audio_bytes)
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.audio_queue.put_nowait(audio_bytes)
            except queue.Full:
                pass

    def signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals by finalizing active speech first.

        Async-signal-safe: only set flags here. The main loop wakes on its
        0.1s queue-get timeout, emits the "finalizing" status, and finalizes.
        """
        self.finalize_requested = True
        self.running = False

    def reset_recording_state(self) -> None:
        self.recording = False
        self.audio_buffer = []
        self.speech_frames = 0
        self.silence_frames = 0

    def transcribe_buffer(self) -> bool:
        if self.speech_frames <= 0 or len(self.audio_buffer) <= 0:
            return False

        if self.on_processing:
            self.on_processing()
        self.set_status("processing")

        audio = np.concatenate(self.audio_buffer)
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                threshold=0.5,
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
        )

        text = " ".join([segment.text.strip() for segment in segments])
        if text.strip():
            emitted = self.output_handler(text)
            self.set_status("listening")
            return emitted
        self.set_status("listening")
        return False

    def describe_capture_failure(self, last_error: Optional[str]) -> str:
        backend = os.environ.get("STT_AUDIO_BACKEND", "auto")
        device = os.environ.get("STT_AUDIO_DEVICE", "default")
        detail = f": {last_error}" if last_error else ""
        return (
            f"ffmpeg audio capture failed{detail}. "
            f"Backend={backend}, device={device}. "
            "Check microphone permissions, choose another input with "
            "STT_AUDIO_DEVICE, or set STT_AUDIO_BACKEND=pulse/alsa."
        )

    def audio_capture_thread(self) -> None:
        try:
            candidates = audio_capture_candidates(self.sample_rate)
        except ValueError as exc:
            self.capture_error = (
                f"{exc}. Set STT_AUDIO_BACKEND to auto, pulse, pipewire, or alsa."
            )
            self.set_status("error", error=self.capture_error)
            self.running = False
            self.audio_queue.put(None)
            return

        last_error = None
        frame_size_bytes = self.frame_size * 2
        for cmd in candidates:
            if not self.running:
                break

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError:
                self.capture_error = "ffmpeg not found. Install ffmpeg to use dictation."
                self.set_status("error", error=self.capture_error)
                self.running = False
                self.audio_queue.put(None)
                return

            self.capture_process = process
            got_audio = False
            assert process.stdout is not None
            pending = b""
            while self.running:
                chunk = process.stdout.read(frame_size_bytes - len(pending))
                if not chunk:
                    # Truly empty read: end of stream.
                    break
                pending += chunk
                if len(pending) < frame_size_bytes:
                    # Short read: accumulate until we have a full VAD frame.
                    continue
                got_audio = True
                self._enqueue_audio(pending)
                pending = b""

            return_code = process.poll()
            if return_code is None:
                process.terminate()
                try:
                    return_code = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    return_code = process.wait()

            if got_audio or not self.running:
                break

            stderr = (
                process.stderr.read().decode(errors="replace").strip()
                if process.stderr else ""
            )
            last_error = stderr.splitlines()[-1] if stderr else f"exit code {return_code}"

        if self.running:
            self.capture_error = self.describe_capture_failure(last_error)
            self.set_status("error", error=self.capture_error)
            self.running = False
            self.audio_queue.put(None)

    def _ingest_frame(self, audio_bytes: bytes) -> None:
        """Append a captured frame to the active utterance buffer when recording."""
        if not self.recording:
            return
        audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self.audio_buffer.append(audio_data)
        if self.vad.is_speech(audio_bytes, self.sample_rate):
            self.speech_frames += 1
        else:
            self.silence_frames += 1

    def _drain_tail_into_buffer(self) -> None:
        """Pull any queued real frames into the active utterance before finalizing."""
        while True:
            try:
                audio_bytes = self.audio_queue.get_nowait()
            except queue.Empty:
                break
            if audio_bytes is None:
                continue
            self._ingest_frame(audio_bytes)

    def _finalize(self) -> None:
        """Drain queued tail frames and transcribe the active utterance."""
        self.set_status("finalizing")
        self._drain_tail_into_buffer()
        self.transcribe_buffer()

    def process_audio(self) -> None:
        self.set_status("listening")
        if self.on_listening:
            self.on_listening()

        while self.running or self.finalize_requested:
            try:
                audio_bytes = self.audio_queue.get(timeout=0.1)
                if audio_bytes is None:
                    if self.capture_error:
                        print(f"Error: {self.capture_error}", file=sys.stderr)
                    if self.finalize_requested:
                        self._finalize()
                    break

                is_speech = self.vad.is_speech(audio_bytes, self.sample_rate)
                self.voiced_buffer.append(is_speech)
                audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                self.audio_preroll.append(audio_data)

                if not self.recording:
                    if len(self.voiced_buffer) == self.voiced_buffer.maxlen:
                        speech_ratio = sum(self.voiced_buffer) / len(self.voiced_buffer)
                        if speech_ratio >= self.speech_threshold:
                            self.recording = True
                            self.silence_frames = 0
                            self.speech_frames = sum(self.voiced_buffer)
                            self.audio_buffer = list(self.audio_preroll)
                            self.set_status("recording")
                            if self.on_recording:
                                self.on_recording()
                else:
                    self.audio_buffer.append(audio_data)

                    if is_speech:
                        self.speech_frames += 1
                        self.silence_frames = 0
                    else:
                        self.silence_frames += 1

                    if (
                        self.silence_frames > self.silence_threshold
                        or len(self.audio_buffer) >= self.max_buffer_frames
                        or (self.speech_frames + self.silence_frames) >= self.max_utterance_frames
                    ):
                        self.recording = False
                        if self.speech_frames > 10 and len(self.audio_buffer) > 10:
                            self.transcribe_buffer()
                        self.reset_recording_state()
                        if self.on_listening:
                            self.on_listening()

            except queue.Empty:
                # A shutdown signal sets finalize_requested + running=False but
                # (being async-signal-safe) does no work; pick it up here.
                if self.finalize_requested and not self.running:
                    if self.capture_error:
                        print(f"Error: {self.capture_error}", file=sys.stderr)
                    self._finalize()
                    break
                continue
            except Exception as exc:
                # One bad frame (e.g. wrong-length VAD input, decode error)
                # must not spin the loop nor leave recording stuck on: report
                # once and reset back to a clean listening state. Kept broad so
                # an unexpected backend error cannot crash the capture loop.
                self.set_status("error", error=str(exc))
                print(f"Error: {exc}", file=sys.stderr)
                self.reset_recording_state()
                if self.on_listening:
                    self.on_listening()

    def _stop_capture_process(self) -> None:
        """Terminate the ffmpeg capture process so it cannot be orphaned."""
        process = self.capture_process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def run(self) -> None:
        self.running = True
        capture_thread = threading.Thread(target=self.audio_capture_thread, daemon=True)
        capture_thread.start()
        try:
            self.process_audio()
        finally:
            # process_audio may exit (e.g. on Ctrl-C finalize) while the capture
            # thread is still blocked reading ffmpeg; stop it so it can't orphan.
            self.running = False
            self._stop_capture_process()
            capture_thread.join(timeout=2)
            self._stop_capture_process()
        if not self.capture_error:
            self.set_status("idle")
