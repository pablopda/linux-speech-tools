#!/usr/bin/env python3
"""Shared dictation session core (engine-agnostic via ``asr_engine``)."""

from __future__ import annotations

import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import warnings
from collections import deque
from typing import Callable, Dict, List, Optional

import numpy as np

try:
    from .runtime import (
        audio_capture_candidates,
        normalize_language,
        normalize_vad_aggressiveness,
        write_status,
    )
    from .asr_engine import create_engine
except ImportError:
    from runtime import (
        audio_capture_candidates,
        normalize_language,
        normalize_vad_aggressiveness,
        write_status,
    )
    from asr_engine import create_engine

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
PartialHandler = Callable[[str], None]


def _finite_env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value >= 0 else default


def _env_float(name: str, default: float) -> float:
    """Parse a finite nonnegative float, falling back on unsafe values."""
    return _finite_env_float(name, default)


class FasterWhisperSession:
    """Capture audio, detect speech, transcribe, and emit recognized text."""

    def __init__(
        self,
        *,
        model_size: str = "tiny",
        language: str = "en",
        device: str = "cpu",
        engine: str = "faster-whisper",
        vad_aggressiveness: int = 2,
        mode: str,
        output_handler: OutputHandler,
        partial_handler: Optional[PartialHandler] = None,
        status_fields: Optional[StatusFields] = None,
        on_listening: Optional[Callable[[], None]] = None,
        on_recording: Optional[Callable[[], None]] = None,
        on_processing: Optional[Callable[[], None]] = None,
        initial_prompt: Optional[str] = None,
        hotwords: Optional[str] = None,
    ):
        self.mode = mode
        self.output_handler = output_handler
        self.partial_handler = partial_handler
        self.status_fields = status_fields or (lambda: {})
        self.on_listening = on_listening
        self.on_recording = on_recording
        self.on_processing = on_processing
        self.initial_prompt = initial_prompt
        self.hotwords = hotwords

        self.engine = create_engine(engine, model_size=model_size, device=device)
        # Backward-compatible aliases: expose the underlying faster-whisper model
        # (and its compute type) when the active backend has one, so existing
        # white-box callers/tests keep working after the abstraction.
        self.model = getattr(self.engine, "model", None)
        self.compute_type = getattr(self.engine, "compute_type", None)
        self.language = normalize_language(language)

        self.sample_rate = 16000
        self.frame_duration = 30
        self.frame_size = int(self.sample_rate * self.frame_duration / 1000)
        self.vad = webrtcvad.Vad(normalize_vad_aggressiveness(vad_aggressiveness))

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
        self.transcription_error: Optional[str] = None
        self.finalization_error: Optional[str] = None
        self.finalize_requested = False
        self.capture_process: Optional[subprocess.Popen] = None
        self.partial_interval = max(
            0.2,
            _finite_env_float("STT_PARTIAL_INTERVAL_SECONDS", 1.2),
        )
        self.partial_min_frames = max(
            1,
            int(_finite_env_float("STT_PARTIAL_MIN_SECONDS", 0.8) * 1000 / self.frame_duration),
        )
        self.last_partial_at = 0.0
        self.partial_generation = 0
        self.transcribe_lock = threading.Lock()
        self.partial_shutdown = threading.Event()
        self.partial_threads = set()
        self.partial_threads_lock = threading.Lock()
        self.partial_callback_lock = threading.Lock()
        self.partial_shutdown_timeout = min(
            30.0,
            _finite_env_float("STT_PARTIAL_SHUTDOWN_SECONDS", 1.0),
        )
        self.finalization_lock_timeout = min(
            60.0,
            _finite_env_float("STT_FINALIZE_LOCK_SECONDS", 10.0),
        )
        self.transcription_timeout = min(
            300.0,
            max(0.1, _finite_env_float("STT_TRANSCRIBE_TIMEOUT_SECONDS", 30.0)),
        )

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
        self.partial_generation += 1

    def transcribe_audio(self, audio: np.ndarray, *, partial: bool = False) -> str:
        return self.engine.transcribe(
            audio,
            self.language,
            partial=partial,
            initial_prompt=self.initial_prompt,
            hotwords=self.hotwords,
        )

    def maybe_emit_partial(self) -> None:
        if self.partial_shutdown.is_set() or not self.partial_handler or not self.recording:
            return
        if self.speech_frames < self.partial_min_frames or len(self.audio_buffer) <= 10:
            return

        now = time.monotonic()
        if now - self.last_partial_at < self.partial_interval:
            return
        self.last_partial_at = now

        generation = self.partial_generation
        audio = np.concatenate(list(self.audio_buffer))
        thread = threading.Thread(
            target=self._partial_transcribe_thread,
            args=(audio, generation),
            daemon=True,
        )
        with self.partial_threads_lock:
            if self.partial_shutdown.is_set():
                return
            self.partial_threads.add(thread)
            thread.start()

    def _partial_transcribe_thread(self, audio: np.ndarray, generation: int) -> None:
        acquired = self.transcribe_lock.acquire(blocking=False)
        try:
            if not acquired:
                return
            if self.partial_shutdown.is_set() or generation != self.partial_generation:
                return
            text = self.transcribe_audio(audio, partial=True)
            if text:
                with self.partial_callback_lock:
                    if (
                        not self.partial_shutdown.is_set()
                        and generation == self.partial_generation
                        and self.partial_handler
                    ):
                        self.partial_handler(text)
        except Exception as exc:
            print(f"Partial transcription error: {exc}", file=sys.stderr)
        finally:
            if acquired:
                self.transcribe_lock.release()
            with self.partial_threads_lock:
                self.partial_threads.discard(threading.current_thread())

    def cancel_partial_callbacks(self, *, permanent: bool = False) -> None:
        """Invalidate partial results and synchronize with active callbacks."""
        if permanent:
            self.partial_shutdown.set()
        self.partial_generation += 1
        # A callback that started before cancellation must finish before this
        # method returns. Workers finishing later acquire this same lock, see
        # partial_shutdown, and cannot call a closed renderer.
        with self.partial_callback_lock:
            pass

    def shutdown_partial_workers(self) -> None:
        """Cancel callbacks and wait briefly for partial inference workers."""
        self.cancel_partial_callbacks(permanent=True)

        with self.partial_threads_lock:
            threads = list(self.partial_threads)
        deadline = time.monotonic() + self.partial_shutdown_timeout
        for thread in threads:
            if thread is not threading.current_thread():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                thread.join(timeout=remaining)

    def _bounded_final_transcription(self, audio: np.ndarray) -> tuple:
        """Run final-quality inference without letting it wedge the session.

        The daemon worker only publishes its result into a private dictionary.
        Output delivery remains on the session thread after a timely completion,
        so a worker that finishes after the budget can never emit stale text.
        """
        completed = threading.Event()
        result = {}

        def transcribe() -> None:
            try:
                result["text"] = self.transcribe_audio(audio, partial=False)
            except Exception as exc:
                result["error"] = exc
            finally:
                completed.set()

        worker = threading.Thread(target=transcribe, daemon=True)
        worker.start()
        if not completed.wait(timeout=self.transcription_timeout):
            return False, None, None
        return True, result.get("text", ""), result.get("error")

    def transcribe_buffer(self, *, finalizing: bool = False) -> bool:
        if self.speech_frames <= 0 or len(self.audio_buffer) <= 0:
            return False

        if self.on_processing:
            self.on_processing()
        self.set_status("processing")

        audio = np.concatenate(self.audio_buffer)
        self.cancel_partial_callbacks(permanent=finalizing)
        acquired = self.transcribe_lock.acquire(
            timeout=self.finalization_lock_timeout
        )
        if not acquired:
            phase = "final transcription" if finalizing else "utterance transcription"
            self.transcription_error = (
                f"{phase} timed out waiting for partial inference"
            )
            if finalizing:
                self.finalization_error = self.transcription_error
            self.running = False
            self.set_status("error", error=self.transcription_error)
            print(f"Error: {self.transcription_error}", file=sys.stderr)
            return False
        try:
            completed, text, error = self._bounded_final_transcription(audio)
            if not completed:
                phase = "final transcription" if finalizing else "utterance transcription"
                self.transcription_error = (
                    f"{phase} timed out after {self.transcription_timeout:g} seconds"
                )
                if finalizing:
                    self.finalization_error = self.transcription_error
                self.running = False
                # This is now a terminal session: prevent any new partial work
                # while the daemon inference is allowed to wind down privately.
                self.cancel_partial_callbacks(permanent=True)
                self.set_status("error", error=self.transcription_error)
                print(f"Error: {self.transcription_error}", file=sys.stderr)
                return False
            if error is not None:
                phase = "final transcription" if finalizing else "utterance transcription"
                self.transcription_error = f"{phase} failed: {error}"
                if finalizing:
                    self.finalization_error = self.transcription_error
                self.running = False
                self.set_status("error", error=self.transcription_error)
                print(f"Error: {self.transcription_error}", file=sys.stderr)
                return False
        finally:
            self.transcribe_lock.release()
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
        self.transcribe_buffer(finalizing=True)

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
                            if self.transcription_error:
                                break
                        self.reset_recording_state()
                        if self.on_listening:
                            self.on_listening()
                    else:
                        self.maybe_emit_partial()

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

    def run(self) -> bool:
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
            self.shutdown_partial_workers()
        if not self.capture_error and not self.transcription_error:
            self.set_status("idle")
        return not self.capture_error and not self.transcription_error


# Forward-looking neutral name: the session is now engine-agnostic. Kept as an
# alias so existing call sites/tests using ``FasterWhisperSession`` are unaffected
# (the rename can be completed later without churn).
DictationSession = FasterWhisperSession
