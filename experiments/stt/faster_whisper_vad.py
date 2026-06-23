#!/usr/bin/env python3
"""
Real-time dictation using faster-whisper with WebRTC VAD
Uses ffmpeg for audio capture - no PyAudio needed!
"""
import sys
import subprocess
import signal
import threading
import queue
import numpy as np
import time
from collections import deque
import os

try:
    from .runtime import audio_capture_candidates
except ImportError:
    from runtime import audio_capture_candidates

# Check dependencies
try:
    from faster_whisper import WhisperModel
except ImportError:
    print("Error: faster-whisper not installed")
    print("Install with: pip install faster-whisper")
    sys.exit(1)

try:
    import webrtcvad
except ImportError:
    print("Error: webrtcvad not installed")
    print("Install with: pip install webrtcvad")
    sys.exit(1)

class FasterWhisperVAD:
    """Real-time speech-to-text using faster-whisper and WebRTC VAD"""

    def __init__(self, model_size="tiny", language="en", device="cpu", vad_aggressiveness=2):
        """
        Initialize the dictation system

        Args:
            model_size: Whisper model size (tiny, base, small, medium, large)
            language: Language code (en, es, fr, etc.)
            device: Device to use (cpu or cuda)
            vad_aggressiveness: VAD aggressiveness (0-3, higher = more aggressive)
        """
        print(f"Loading Whisper model: {model_size}...")
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type="int8" if device == "cpu" else "float16",
        )
        self.language = language

        # Audio settings (WebRTC VAD needs specific settings)
        self.sample_rate = 16000  # WebRTC VAD only supports 8, 16, 32, 48 kHz
        self.frame_duration = 30  # WebRTC VAD frame duration in ms (10, 20, or 30)
        self.frame_size = int(self.sample_rate * self.frame_duration / 1000)

        # VAD setup
        self.vad = webrtcvad.Vad(vad_aggressiveness)

        # Buffers and state
        self.audio_buffer = []
        self.audio_preroll = deque(maxlen=10)
        self.voiced_buffer = deque(maxlen=10)  # Store last 10 VAD decisions
        self.recording = False
        self.silence_frames = 0
        self.speech_frames = 0

        # Thresholds
        self.speech_threshold = 0.5  # 50% of frames need to have speech to start recording
        self.silence_threshold = 20  # Number of silent frames to stop recording
        self.max_utterance_frames = max(
            1,
            int(float(os.environ.get("STT_MAX_UTTERANCE_SECONDS", "60")) * 1000 / self.frame_duration),
        )
        self.max_buffer_frames = max(
            self.max_utterance_frames,
            int(float(os.environ.get("STT_MAX_BUFFER_SECONDS", "75")) * 1000 / self.frame_duration),
        )

        # Threading
        self.audio_queue = queue.Queue()
        self.running = False
        self.capture_error = None
        self.finalize_requested = False

        # Signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        print(f"✅ Initialized with model={model_size}, language={language}")

    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print("\n\n👋 Finishing dictation...")
        self.finalize_requested = True
        self.running = False
        self.audio_queue.put(None)

    def transcribe_buffer(self) -> bool:
        """Transcribe the active speech buffer, if one is present."""
        if self.speech_frames <= 0 or len(self.audio_buffer) <= 0:
            return False

        audio = np.concatenate(self.audio_buffer)
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                threshold=0.5,
                min_silence_duration_ms=500,
                speech_pad_ms=200
            )
        )

        text = " ".join([s.text.strip() for s in segments])
        if text.strip():
            print(" " * 60, end='\r')
            print(f"📝 {text}")
            return True
        return False

    def reset_recording_state(self):
        self.recording = False
        self.audio_buffer = []
        self.speech_frames = 0
        self.silence_frames = 0

    def audio_capture_thread(self):
        """Capture audio from microphone using ffmpeg"""
        try:
            candidates = audio_capture_candidates(self.sample_rate)
        except ValueError as exc:
            self.capture_error = str(exc)
            self.running = False
            self.audio_queue.put(None)
            return

        last_error = None

        frame_size_bytes = self.frame_size * 2  # 2 bytes per sample (16-bit)
        for cmd in candidates:
            if not self.running:
                break

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            except FileNotFoundError:
                self.capture_error = "ffmpeg not found. Install ffmpeg to use dictation."
                self.running = False
                self.audio_queue.put(None)
                return

            got_audio = False
            while self.running:
                # Read exactly one frame
                audio_bytes = process.stdout.read(frame_size_bytes)
                if not audio_bytes or len(audio_bytes) < frame_size_bytes:
                    break

                got_audio = True
                self.audio_queue.put(audio_bytes)

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

            stderr = process.stderr.read().decode(errors='replace').strip() if process.stderr else ""
            last_error = stderr.splitlines()[-1] if stderr else f"exit code {return_code}"

        if self.running:
            self.capture_error = (
                f"ffmpeg audio capture failed: {last_error}"
                if last_error else
                "audio capture ended before a full frame was read"
            )
            self.running = False
            self.audio_queue.put(None)

    def process_audio(self):
        """Process audio with VAD and trigger transcription"""
        print("🎤 Listening... (speak to start dictation)")

        while self.running or self.finalize_requested:
            try:
                # Get audio frame with timeout
                audio_bytes = self.audio_queue.get(timeout=0.1)
                if audio_bytes is None:
                    if self.capture_error:
                        print(f"\n❌ {self.capture_error}")
                    if self.finalize_requested:
                        self.transcribe_buffer()
                    break

                # Check if frame contains speech
                is_speech = self.vad.is_speech(audio_bytes, self.sample_rate)

                # Update rolling buffer
                self.voiced_buffer.append(is_speech)

                # Convert to float32 for Whisper
                audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                self.audio_preroll.append(audio_data)

                if not self.recording:
                    # Check if we should start recording
                    if len(self.voiced_buffer) == self.voiced_buffer.maxlen:
                        speech_ratio = sum(self.voiced_buffer) / len(self.voiced_buffer)
                        if speech_ratio >= self.speech_threshold:
                            self.recording = True
                            self.silence_frames = 0
                            self.speech_frames = sum(self.voiced_buffer)
                            self.audio_buffer = list(self.audio_preroll)
                            print("🔴 Recording...", end='\r')

                else:
                    # We're recording
                    self.audio_buffer.append(audio_data)

                    if is_speech:
                        self.speech_frames += 1
                        self.silence_frames = 0
                    else:
                        self.silence_frames += 1

                    # Check if we should stop recording
                    if (
                        self.silence_frames > self.silence_threshold
                        or len(self.audio_buffer) >= self.max_buffer_frames
                        or (self.speech_frames + self.silence_frames) >= self.max_utterance_frames
                    ):
                        self.recording = False
                        print("⏸  Processing...        ", end='\r')

                        # Only transcribe if we have enough speech
                        if self.speech_frames > 10 and len(self.audio_buffer) > 10:
                            self.transcribe_buffer()

                        # Clear buffer
                        self.reset_recording_state()
                        print("🎤 Listening...         ", end='\r')

            except queue.Empty:
                continue
            except Exception as e:
                print(f"\n❌ Error: {e}")

    def run(self):
        """Start the dictation system"""
        self.running = True

        # Start audio capture in background thread
        capture_thread = threading.Thread(target=self.audio_capture_thread, daemon=True)
        capture_thread.start()

        # Process audio in main thread
        self.process_audio()
        capture_thread.join(timeout=1)

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Real-time dictation using faster-whisper with WebRTC VAD"
    )
    parser.add_argument(
        "--model", "-m",
        default="tiny",
        choices=["tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "large", "large-v2", "large-v3"],
        help="Whisper model size (default: tiny)"
    )
    parser.add_argument(
        "--language", "-l",
        default="en",
        help="Language code (default: en)"
    )
    parser.add_argument(
        "--device", "-d",
        default="cpu",
        choices=["cpu", "cuda"],
        help="Device to use (default: cpu)"
    )
    parser.add_argument(
        "--vad", "-v",
        type=int,
        default=2,
        choices=[0, 1, 2, 3],
        help="VAD aggressiveness 0-3 (default: 2)"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test dependencies and audio"
    )

    args = parser.parse_args()

    if args.test:
        print("🔍 Testing dependencies...")
        print("✅ faster-whisper installed")
        print("✅ webrtcvad installed")

        # Test ffmpeg audio capture
        print("\n🎤 Testing audio capture...")
        try:
            last_error = ""
            for cmd in audio_capture_candidates(16000):
                test_cmd = [*cmd[:-3], "-t", "1", "-f", "null", "-"]
                result = subprocess.run(
                    test_cmd,
                    capture_output=True,
                    timeout=2
                )
                if result.returncode == 0:
                    print("✅ Audio capture works")
                    break
                last_error = result.stderr.decode(errors="replace")[:200]
            else:
                print("❌ Audio capture failed - check microphone")
                print("Error:", last_error)
        except ValueError as exc:
            print(f"❌ {exc}")
        except subprocess.TimeoutExpired:
            print("❌ Audio capture timeout - microphone may be in use")
        except FileNotFoundError:
            print("❌ ffmpeg not found - install with: sudo apt install ffmpeg")

        return

    print("🚀 Faster-Whisper Dictation with WebRTC VAD")
    print("=" * 50)
    print(f"Model: {args.model}")
    print(f"Language: {args.language}")
    print(f"Device: {args.device}")
    print(f"VAD Level: {args.vad}")
    print("=" * 50)
    print("Press Ctrl+C to stop\n")

    # Create and run dictation
    dictation = FasterWhisperVAD(
        model_size=args.model,
        language=args.language,
        device=args.device,
        vad_aggressiveness=args.vad
    )

    try:
        dictation.run()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")

if __name__ == "__main__":
    main()
