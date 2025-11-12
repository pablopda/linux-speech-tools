#!/usr/bin/env python3
"""
Continuous Audio Streaming for say_read.py
Solves the chunking gap problem with multiple streaming strategies.
"""

import subprocess
import tempfile
import threading
import queue
import numpy as np
import soundfile as sf
import time
import os
import sys
from pathlib import Path
from typing import Optional, List, Tuple, Iterator

class AudioBuffer:
    """Smart audio buffer that can stream audio chunks continuously."""

    def __init__(self, sample_rate: int = 24000, buffer_duration: float = 0.5):
        self.sample_rate = sample_rate
        self.buffer_duration = buffer_duration
        self.buffer_size = int(sample_rate * buffer_duration)
        self.queue = queue.Queue(maxsize=10)  # Buffer up to 10 chunks
        self.playing = False
        self.finished_generating = False

    def add_chunk(self, audio_chunk: np.ndarray):
        """Add an audio chunk to the buffer."""
        if not self.finished_generating:
            self.queue.put(audio_chunk, timeout=30)

    def mark_finished(self):
        """Mark that no more chunks will be added."""
        self.finished_generating = True
        self.queue.put(None)  # Sentinel value

    def get_chunks(self) -> Iterator[np.ndarray]:
        """Generator that yields audio chunks for playback."""
        while True:
            try:
                chunk = self.queue.get(timeout=1)
                if chunk is None:  # Sentinel - no more chunks
                    break
                yield chunk
                self.queue.task_done()
            except queue.Empty:
                if self.finished_generating:
                    break
                continue

class ContinuousAudioPlayer:
    """Plays audio chunks continuously without gaps."""

    def __init__(self, sample_rate: int = 24000, player: str = 'ffplay'):
        self.sample_rate = sample_rate
        self.player = player
        self.process = None

    def __enter__(self):
        self._start_player()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_player()

    def _start_player(self):
        """Start the audio player process."""
        try:
            if self.player == 'ffplay':
                self.process = subprocess.Popen([
                    'ffplay',
                    '-hide_banner',
                    '-loglevel', 'error',
                    '-nodisp',
                    '-autoexit',
                    '-f', 's16le',
                    '-ar', str(self.sample_rate),
                    '-ac', '1',
                    '-i', '-'
                ], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            elif self.player == 'mpv':
                self.process = subprocess.Popen([
                    'mpv',
                    '--no-video',
                    '--really-quiet',
                    '--demuxer-rawvideo-format=s16le',
                    '--demuxer-rawvideo-rate', str(self.sample_rate),
                    '--demuxer-rawvideo-channels=1',
                    '-'
                ], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            else:
                raise ValueError(f"Unsupported player: {self.player}")

        except Exception as e:
            raise RuntimeError(f"Failed to start {self.player}: {e}")

    def _stop_player(self):
        """Stop the audio player process."""
        if self.process:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                pass
            finally:
                if self.process.poll() is None:
                    self.process.kill()
                self.process = None

    def write_audio(self, audio_chunk: np.ndarray):
        """Write an audio chunk to the player."""
        if not self.process or self.process.poll() is not None:
            raise RuntimeError("Player process is not running")

        # Convert to 16-bit PCM
        audio_int16 = (audio_chunk * 32767).astype(np.int16)

        try:
            self.process.stdin.write(audio_int16.tobytes())
            self.process.stdin.flush()
        except BrokenPipeError:
            raise RuntimeError("Player closed unexpectedly")

class SmartAudioStreamer:
    """Enhanced audio streaming with multiple strategies."""

    def __init__(self, sample_rate: int = 24000, debug: bool = False):
        self.sample_rate = sample_rate
        self.debug = debug

    def stream_continuous(self, audio_generator, player: str = 'ffplay'):
        """
        Stream audio continuously without gaps.

        Args:
            audio_generator: Generator that yields (audio_array, sample_rate) tuples
            player: Audio player to use ('ffplay' or 'mpv')
        """
        try:
            with ContinuousAudioPlayer(self.sample_rate, player) as audio_player:
                chunk_count = 0
                start_time = time.time()

                for audio_chunk, sr in audio_generator:
                    if sr != self.sample_rate:
                        # Resample if needed (basic resampling)
                        from scipy import signal
                        audio_chunk = signal.resample(audio_chunk,
                                                    int(len(audio_chunk) * self.sample_rate / sr))

                    audio_player.write_audio(audio_chunk)
                    chunk_count += 1

                    if self.debug:
                        elapsed = time.time() - start_time
                        print(f"[continuous] Streamed chunk {chunk_count}, "
                              f"duration: {len(audio_chunk)/self.sample_rate:.2f}s, "
                              f"total_time: {elapsed:.2f}s", file=sys.stderr)

                if self.debug:
                    total_time = time.time() - start_time
                    print(f"[continuous] Finished streaming {chunk_count} chunks "
                          f"in {total_time:.2f}s", file=sys.stderr)

        except Exception as e:
            print(f"[continuous] Error during streaming: {e}", file=sys.stderr)
            return False

        return True

    def stream_buffered(self, audio_generator, player: str = 'ffplay', buffer_size: int = 3):
        """
        Stream with intelligent buffering to prevent gaps.

        Args:
            audio_generator: Generator yielding (audio_array, sample_rate) tuples
            player: Audio player to use
            buffer_size: Number of chunks to buffer ahead
        """
        buffer = AudioBuffer(self.sample_rate)

        def generate_audio():
            """Background thread to generate audio chunks."""
            try:
                for audio_chunk, sr in audio_generator:
                    if sr != self.sample_rate:
                        from scipy import signal
                        audio_chunk = signal.resample(audio_chunk,
                                                    int(len(audio_chunk) * self.sample_rate / sr))
                    buffer.add_chunk(audio_chunk)

                    if self.debug:
                        print(f"[buffered] Generated chunk, queue size: {buffer.queue.qsize()}",
                              file=sys.stderr)
            except Exception as e:
                print(f"[buffered] Generation error: {e}", file=sys.stderr)
            finally:
                buffer.mark_finished()

        # Start background generation
        generator_thread = threading.Thread(target=generate_audio, daemon=True)
        generator_thread.start()

        # Stream buffered audio
        try:
            with ContinuousAudioPlayer(self.sample_rate, player) as audio_player:
                chunk_count = 0

                for audio_chunk in buffer.get_chunks():
                    audio_player.write_audio(audio_chunk)
                    chunk_count += 1

                    if self.debug:
                        print(f"[buffered] Played chunk {chunk_count}, "
                              f"queue size: {buffer.queue.qsize()}", file=sys.stderr)

                if self.debug:
                    print(f"[buffered] Finished playing {chunk_count} chunks", file=sys.stderr)

        except Exception as e:
            print(f"[buffered] Playback error: {e}", file=sys.stderr)
            return False

        # Wait for generation to complete
        generator_thread.join(timeout=30)
        return True

def detect_best_player() -> str:
    """Detect the best available audio player."""
    import shutil

    players = ['ffplay', 'mpv', 'paplay', 'aplay']
    for player in players:
        if shutil.which(player):
            return player
    return 'ffplay'  # Default fallback

# Test function for the module
def test_continuous_streaming():
    """Test the continuous streaming functionality."""

    def dummy_audio_generator():
        """Generate some test audio chunks."""
        sample_rate = 24000
        duration = 0.5  # 0.5 second chunks
        frequency = 440  # A4 note

        for i in range(5):  # 5 chunks
            t = np.linspace(0, duration, int(sample_rate * duration))
            # Generate a sine wave with different frequencies
            audio = np.sin(2 * np.pi * (frequency + i * 50) * t) * 0.1
            yield audio, sample_rate
            time.sleep(0.1)  # Simulate synthesis time

    print("Testing continuous audio streaming...")

    streamer = SmartAudioStreamer(debug=True)
    player = detect_best_player()

    print(f"Using player: {player}")

    # Test continuous streaming
    print("Testing continuous streaming...")
    success = streamer.stream_continuous(dummy_audio_generator(), player)

    if success:
        print("✅ Continuous streaming test passed!")
    else:
        print("❌ Continuous streaming test failed!")

    return success

if __name__ == "__main__":
    test_continuous_streaming()