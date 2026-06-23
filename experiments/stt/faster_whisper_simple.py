#!/usr/bin/env python3
"""
Simple real-time dictation using faster-whisper directly
No PyAudio needed - uses sounddevice or subprocess for audio capture
"""
import sys
import os
import time
import signal
import threading
import queue
import numpy as np
from pathlib import Path
from typing import Optional

# Check what's available for audio
AUDIO_BACKEND = None
try:
    import sounddevice as sd
    AUDIO_BACKEND = "sounddevice"
except (ImportError, OSError):
    # Fallback to subprocess/ffmpeg
    import subprocess
    AUDIO_BACKEND = "ffmpeg"

# Import faster-whisper
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

# Simple VAD using energy threshold
class SimpleVAD:
    """Simple Voice Activity Detection using energy threshold"""

    def __init__(self, threshold=0.01, silence_duration=1.0, sample_rate=16000):
        self.threshold = threshold
        self.silence_duration = silence_duration
        self.sample_rate = sample_rate
        self.silence_samples = int(silence_duration * sample_rate)
        self.is_speaking = False
        self.silence_counter = 0

    def is_speech(self, audio_chunk):
        """Check if audio chunk contains speech"""
        # Calculate RMS energy
        energy = np.sqrt(np.mean(audio_chunk**2))

        if energy > self.threshold:
            self.is_speaking = True
            self.silence_counter = 0
            return True
        else:
            if self.is_speaking:
                self.silence_counter += len(audio_chunk)
                if self.silence_counter > self.silence_samples:
                    self.is_speaking = False
                    return False
                return True  # Still speaking (in pause)
            return False

class FasterWhisperDictation:
    """Simple dictation using faster-whisper"""

    def __init__(self, model_size="tiny", language="en", device="cpu"):
        if not WHISPER_AVAILABLE:
            print("Error: faster-whisper not installed")
            print("Install with: pip install faster-whisper")
            sys.exit(1)

        print(f"Loading Whisper model: {model_size}...")
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type="int8" if device == "cpu" else "float16",
            cpu_threads=os.cpu_count() // 2,
            num_workers=1
        )
        self.language = language

        # Audio settings
        self.sample_rate = 16000
        self.chunk_duration = 0.1  # 100ms chunks
        self.chunk_size = int(self.sample_rate * self.chunk_duration)

        # VAD
        self.vad = SimpleVAD(threshold=0.01, silence_duration=0.8)

        # Audio buffer
        self.audio_buffer = []
        self.audio_queue = queue.Queue()
        self.running = False

        # Setup signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print("\n\nShutting down...")
        self.running = False
        sys.exit(0)

    def audio_callback_sounddevice(self, indata, frames, time_info, status):
        """Sounddevice audio callback"""
        if status:
            print(f"Audio error: {status}")
        # Convert to float32 and put in queue
        audio_data = indata[:, 0].astype(np.float32)
        self.audio_queue.put(audio_data)

    def audio_capture_ffmpeg(self):
        """Capture audio using ffmpeg subprocess"""
        cmd = [
            'ffmpeg',
            '-f', 'alsa',  # Linux audio input
            '-i', 'default',  # Default mic
            '-acodec', 'pcm_f32le',  # 32-bit float PCM
            '-ar', str(self.sample_rate),  # Sample rate
            '-ac', '1',  # Mono
            '-f', 'f32le',  # Raw float output
            '-'  # Pipe to stdout
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self.chunk_size * 4  # 4 bytes per float32
        )

        while self.running:
            # Read chunk
            data = process.stdout.read(self.chunk_size * 4)
            if not data:
                break

            # Convert to numpy array
            audio_data = np.frombuffer(data, dtype=np.float32)
            self.audio_queue.put(audio_data)

        process.terminate()

    def process_audio(self):
        """Process audio chunks and transcribe when speech ends"""
        print("🎤 Listening... (speak to transcribe)")

        while self.running:
            try:
                # Get audio chunk with timeout
                audio_chunk = self.audio_queue.get(timeout=0.1)

                # Check for speech
                if self.vad.is_speech(audio_chunk):
                    self.audio_buffer.append(audio_chunk)

                    # Show recording indicator
                    print("🔴 Recording...", end='\r')

                elif self.audio_buffer:
                    # Speech ended, transcribe
                    print("⏸  Processing...", end='\r')

                    # Combine buffer
                    audio_data = np.concatenate(self.audio_buffer)

                    # Transcribe
                    segments, _ = self.model.transcribe(
                        audio_data,
                        language=self.language,
                        beam_size=5,
                        vad_filter=True,
                        vad_parameters=dict(
                            threshold=0.5,
                            min_silence_duration_ms=500,
                            speech_pad_ms=200
                        )
                    )

                    # Get text
                    text = " ".join([s.text for s in segments]).strip()

                    if text:
                        # Clear line and print result
                        print(" " * 50, end='\r')
                        print(f"📝 {text}")

                    # Clear buffer
                    self.audio_buffer = []

            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error: {e}")

    def run(self):
        """Start dictation"""
        print(f"Starting Faster-Whisper Dictation")
        print(f"Backend: {AUDIO_BACKEND}")
        print(f"Model: {self.model.model_size_or_path}")
        print(f"Language: {self.language}")
        print("Press Ctrl+C to stop\n")

        self.running = True

        # Start audio capture
        if AUDIO_BACKEND == "sounddevice":
            # Use sounddevice
            import sounddevice as sd
            stream = sd.InputStream(
                callback=self.audio_callback_sounddevice,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.chunk_size,
                dtype='float32'
            )

            # Start processing thread
            process_thread = threading.Thread(target=self.process_audio)
            process_thread.start()

            # Start audio stream
            with stream:
                while self.running:
                    time.sleep(0.1)

        else:
            # Use ffmpeg in thread
            capture_thread = threading.Thread(target=self.audio_capture_ffmpeg)
            capture_thread.start()

            # Process audio in main thread
            self.process_audio()

            capture_thread.join()

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Simple real-time dictation using faster-whisper"
    )
    parser.add_argument(
        "--model", "-m",
        default="tiny",
        choices=["tiny", "base", "small", "medium", "large"],
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
        "--test",
        action="store_true",
        help="Test audio capture"
    )

    args = parser.parse_args()

    if args.test:
        # Test audio capture
        print("Testing audio capture...")
        print(f"Audio backend: {AUDIO_BACKEND}")

        if AUDIO_BACKEND == "sounddevice":
            import sounddevice as sd
            print("Sounddevice available")
            print("Input devices:")
            print(sd.query_devices())
        else:
            print("Using ffmpeg for audio capture")
            # Test ffmpeg
            try:
                result = subprocess.run(
                    ["ffmpeg", "-f", "alsa", "-i", "default", "-t", "0.1", "-f", "null", "-"],
                    capture_output=True,
                    timeout=2
                )
                if result.returncode == 0:
                    print("✓ FFmpeg audio capture works")
                else:
                    print("✗ FFmpeg audio capture failed")
            except Exception as e:
                print(f"✗ FFmpeg test failed: {e}")

        sys.exit(0)

    # Run dictation
    dictation = FasterWhisperDictation(
        model_size=args.model,
        language=args.language,
        device=args.device
    )

    try:
        dictation.run()
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()