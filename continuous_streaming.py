#!/usr/bin/env python3
"""
Lightweight Continuous Audio Streaming for say_read.py
Eliminates gaps between audio chunks using the existing infrastructure.
"""

import subprocess
import tempfile
import time
import os
import sys
import shutil
from typing import List, Iterator

class LightweightAudioStreamer:
    """Lightweight continuous audio streaming using existing tools."""

    def __init__(self, sample_rate: int = 24000, debug: bool = False):
        self.sample_rate = sample_rate
        self.debug = debug
        self.temp_files = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def cleanup(self):
        """Clean up temporary files."""
        for temp_file in self.temp_files:
            try:
                os.remove(temp_file)
            except:
                pass
        self.temp_files.clear()

    def detect_player(self) -> str:
        """Detect the best available audio player."""
        players = ['ffplay', 'mpv', 'paplay', 'aplay']
        for player in players:
            if shutil.which(player):
                return player
        return None

    def stream_audio_chunks_continuous(self, audio_chunks: List[str], player: str = None):
        """
        Stream audio files continuously by concatenating them.

        Args:
            audio_chunks: List of WAV file paths
            player: Audio player to use
        """
        if not audio_chunks:
            return False

        if not player:
            player = self.detect_player()

        if not player:
            print("No suitable audio player found", file=sys.stderr)
            return False

        try:
            # Create concatenated audio file
            concat_file = self._concatenate_audio_files(audio_chunks)
            if not concat_file:
                return False

            # Play the concatenated file
            self._play_file(concat_file, player)
            return True

        except Exception as e:
            if self.debug:
                print(f"Error in continuous streaming: {e}", file=sys.stderr)
            return False

    def _concatenate_audio_files(self, audio_files: List[str]) -> str:
        """Concatenate multiple WAV files into one continuous file."""
        if len(audio_files) == 1:
            return audio_files[0]

        # Create output file
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            output_file = f.name

        self.temp_files.append(output_file)

        try:
            # Use ffmpeg to concatenate if available
            if shutil.which('ffmpeg'):
                return self._concatenate_with_ffmpeg(audio_files, output_file)
            # Use sox as fallback if available
            elif shutil.which('sox'):
                return self._concatenate_with_sox(audio_files, output_file)
            else:
                # Simple byte concatenation for WAV files (risky but might work)
                return self._simple_wav_concatenate(audio_files, output_file)

        except Exception as e:
            if self.debug:
                print(f"Concatenation failed: {e}", file=sys.stderr)
            return None

    def _concatenate_with_ffmpeg(self, audio_files: List[str], output_file: str) -> str:
        """Concatenate using ffmpeg."""
        # Create input file list
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            list_file = f.name
            for audio_file in audio_files:
                f.write(f"file '{audio_file}'\n")

        self.temp_files.append(list_file)

        cmd = [
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', list_file, '-c', 'copy', output_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            if self.debug:
                print(f"ffmpeg error: {result.stderr}", file=sys.stderr)
            return None

        return output_file

    def _concatenate_with_sox(self, audio_files: List[str], output_file: str) -> str:
        """Concatenate using sox."""
        cmd = ['sox'] + audio_files + [output_file]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            if self.debug:
                print(f"sox error: {result.stderr}", file=sys.stderr)
            return None

        return output_file

    def _simple_wav_concatenate(self, audio_files: List[str], output_file: str) -> str:
        """
        Simple WAV concatenation by copying audio data.
        WARNING: This assumes all files have the same format.
        """
        try:
            with open(output_file, 'wb') as outf:
                for i, audio_file in enumerate(audio_files):
                    with open(audio_file, 'rb') as inf:
                        data = inf.read()
                        if i == 0:
                            # Write entire first file (including header)
                            outf.write(data)
                        else:
                            # Skip WAV header (44 bytes) for subsequent files
                            outf.write(data[44:])

            return output_file

        except Exception as e:
            if self.debug:
                print(f"Simple concatenation failed: {e}", file=sys.stderr)
            return None

    def _play_file(self, audio_file: str, player: str):
        """Play a single audio file."""
        if player == 'ffplay':
            cmd = ['ffplay', '-hide_banner', '-loglevel', 'error',
                   '-nodisp', '-autoexit', audio_file]
        elif player == 'mpv':
            cmd = ['mpv', '--no-video', '--really-quiet', audio_file]
        elif player == 'paplay':
            cmd = ['paplay', audio_file]
        elif player == 'aplay':
            cmd = ['aplay', audio_file]
        else:
            print(f"Unsupported player: {player}", file=sys.stderr)
            return

        if self.debug:
            print(f"Playing with {player}: {audio_file}", file=sys.stderr)

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def enhance_say_read_streaming():
    """
    Function that can be used to patch the original say_read.py
    for continuous streaming.
    """
    import sys
    from pathlib import Path

    # Check if we can import the original say_read
    try:
        script_dir = Path(__file__).parent
        sys.path.insert(0, str(script_dir))
        import say_read

        # Monkey patch the streaming function
        original_stream_function = getattr(say_read, 'stream_fast', None)

        def continuous_stream_wrapper(k, pieces, voice, lang, debug):
            """Enhanced streaming function with continuous playback."""

            with LightweightAudioStreamer(debug=debug) as streamer:
                temp_audio_files = []

                try:
                    # Generate all audio chunks
                    for i, piece in enumerate(pieces, 1):
                        audio, sr, did_split, dt = say_read.synth_retry(k, piece, voice, lang, debug)

                        # Save to temporary file
                        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                            temp_file = f.name

                        # Use soundfile if available, otherwise fallback
                        try:
                            import soundfile as sf
                            sf.write(temp_file, audio, sr)
                        except ImportError:
                            # Fallback to basic WAV writing
                            import wave
                            with wave.open(temp_file, 'wb') as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(sr)
                                wf.writeframes((audio * 32767).astype('int16').tobytes())

                        temp_audio_files.append(temp_file)
                        streamer.temp_files.append(temp_file)

                        if debug:
                            print(f"[continuous] [{i}/{len(pieces)}] Generated audio chunk",
                                  file=sys.stderr)

                    # Stream all chunks continuously
                    success = streamer.stream_audio_chunks_continuous(temp_audio_files)
                    return success

                except Exception as e:
                    if debug:
                        print(f"[continuous] Error: {e}", file=sys.stderr)
                    return False

        # Replace the function
        if hasattr(say_read, 'stream_fast'):
            say_read.stream_fast_original = say_read.stream_fast
            say_read.stream_fast = continuous_stream_wrapper

        return True

    except ImportError as e:
        print(f"Could not enhance say_read: {e}", file=sys.stderr)
        return False

# Test function
def test_streaming():
    """Test the streaming functionality."""
    print("Testing lightweight continuous streaming...")

    # Create some test audio files (dummy)
    test_files = []
    for i in range(3):
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            test_files.append(f.name)
            # Write minimal WAV header + silence
            f.write(b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00')

    try:
        with LightweightAudioStreamer(debug=True) as streamer:
            player = streamer.detect_player()
            print(f"Using player: {player}")

            if player:
                print("Testing concatenation and playback...")
                success = streamer.stream_audio_chunks_continuous(test_files, player)
                print(f"Test {'passed' if success else 'failed'}")
            else:
                print("No audio player available for testing")

    finally:
        # Cleanup test files
        for f in test_files:
            try:
                os.remove(f)
            except:
                pass

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_streaming()
    elif len(sys.argv) > 1 and sys.argv[1] == "enhance":
        enhance_say_read_streaming()
        print("Enhanced say_read streaming functionality")
    else:
        print("Usage: python3 continuous_streaming.py [test|enhance]")