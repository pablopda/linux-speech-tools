#!/usr/bin/env python3
"""
Enhanced say_read.py with Continuous Audio Streaming
Eliminates gaps between audio chunks for smooth, professional playback.
"""

import sys
import os
import argparse
from pathlib import Path

# Import original say_read functionality
sys.path.insert(0, str(Path(__file__).parent))

# Import the main components from the original say_read.py
# We'll patch it to use our continuous streaming
import say_read
from continuous_audio import SmartAudioStreamer, detect_best_player

class ContinuousSayRead:
    """Enhanced say_read with continuous audio streaming."""

    def __init__(self, args):
        self.args = args
        self.debug = args.debug
        self.streamer = SmartAudioStreamer(sample_rate=24000, debug=args.debug)

    def setup_kokoro(self):
        """Initialize Kokoro TTS engine."""
        try:
            from kokoro_onnx import Kokoro
            k = Kokoro(self.args.model, self.args.voices)
            return k
        except Exception as e:
            say_read.dbg(f"[say-read-continuous] failed to load kokoro: {e}", True)
            return None

    def audio_generator(self, k, pieces, voice, lang):
        """Generator that yields audio chunks from text pieces."""
        total_t = 0.0

        for i, piece in enumerate(pieces, 1):
            try:
                # Use the original synth_retry function
                audio, sr, did_split, dt = say_read.synth_retry(k, piece, voice, lang, self.debug)
                total_t += dt

                if self.debug:
                    say_read.dbg(f"[continuous] [{i}/{len(pieces)}] "
                                f"len={len(piece)} split={did_split} "
                                f"synth={dt:.2f}s total={total_t:.2f}s", True)

                yield audio, sr

            except Exception as e:
                say_read.dbg(f"[continuous] Error synthesizing piece {i}: {e}", True)
                continue

    def process_url_continuous(self):
        """Main processing function with continuous streaming."""

        # Extract text (reuse original extraction logic)
        input_source = self.args.url_or_file_or_dash

        if input_source == "-":
            text = sys.stdin.read()
            if self.debug:
                say_read.dbg(f"[say-read-continuous] read {len(text)} chars from stdin", True)
        else:
            if input_source.startswith(('http://', 'https://')):
                text = say_read.fetch_url(input_source, self.args.render, self.debug)
            elif input_source.lower().endswith('.pdf'):
                text = say_read.extract_pdf(input_source, self.debug)
            elif input_source.lower().endswith(('.epub', '.mobi')):
                text = say_read.extract_epub(input_source, self.debug)
            else:
                with open(input_source, 'r', encoding='utf-8', errors='replace') as f:
                    text = f.read()

            if self.debug:
                say_read.dbg(f"[say-read-continuous] extracted {len(text)} chars", True)

        if not text.strip():
            print("No text found to read.", file=sys.stderr)
            return 1

        # Apply max-chars limit
        if self.args.max_chars > 0:
            text = text[:self.args.max_chars]

        # Clean and split text into pieces
        text = say_read.clean_text(text)
        pieces = say_read.split_sentences(text, self.args.chunk)

        if self.debug:
            say_read.dbg(f"[say-read-continuous] pieces: {len(pieces)}", True)

        # Initialize Kokoro
        k = self.setup_kokoro()
        if not k:
            return 1

        # Determine voice
        voice = self.args.voice or ('ef_dora' if self.args.lang.lower().startswith('es') else 'af_heart')

        # Detect best player
        player = self.args.player or detect_best_player()

        if self.debug:
            say_read.dbg(f"[say-read-continuous] using player: {player}", True)

        # Handle output file (non-streaming)
        if self.args.out:
            return self._generate_file_output(k, pieces, voice)

        # Choose streaming strategy
        if self.args.continuous_buffered:
            success = self.streamer.stream_buffered(
                self.audio_generator(k, pieces, voice, self.args.lang),
                player
            )
        else:  # Default: continuous streaming
            success = self.streamer.stream_continuous(
                self.audio_generator(k, pieces, voice, self.args.lang),
                player
            )

        return 0 if success else 1

    def _generate_file_output(self, k, pieces, voice):
        """Generate audio file output (concatenated, no streaming)."""
        audio_list = []
        sr = None
        total_t = 0.0

        for i, p in enumerate(pieces, 1):
            a, sr, did_split, dt = say_read.synth_retry(k, p, voice, self.args.lang, self.debug)
            audio_list.append(a)
            total_t += dt
            if self.debug:
                say_read.dbg(f"[say-read-continuous] [{i}/{len(pieces)}] "
                            f"len={len(p)} split={did_split} synth={dt:.2f}s total={total_t:.2f}s", True)

        # Concatenate all audio
        import numpy as np
        wav = np.concatenate(audio_list)

        # Write output file
        if self.args.trim_silence:
            # Use ffmpeg for trimming
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                tmp = f.name

            import soundfile as sf
            sf.write(tmp, wav, sr)

            try:
                import subprocess
                subprocess.call([
                    'ffmpeg', '-y', '-i', tmp,
                    '-af', 'silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:stop_periods=1:stop_duration=0.05:stop_threshold=-40dB',
                    self.args.out
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"Wrote {self.args.out} (with silence trimming)")
            except Exception as e:
                say_read.dbg(f"ffmpeg trim failed: {e}", True)
                say_read.write_audio(wav, sr, self.args.out)
                print(f"Wrote {self.args.out}")
            finally:
                try:
                    os.remove(tmp)
                except:
                    pass
        else:
            say_read.write_audio(wav, sr, self.args.out)
            print(f"Wrote {self.args.out}")

        return 0

def main():
    """Enhanced main function with continuous streaming options."""

    ap = argparse.ArgumentParser(
        description='Enhanced say_read with continuous audio streaming',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Streaming Modes:
  Default: Continuous streaming (eliminates gaps between chunks)
  --continuous-buffered: Buffered streaming (best for slow synthesis)

Examples:
  # Continuous streaming (default)
  python say_read_continuous.py https://example.com/article

  # Buffered streaming for complex content
  python say_read_continuous.py --continuous-buffered https://example.com/long-article

  # Generate file output
  python say_read_continuous.py -o article.mp3 https://example.com/article
        """
    )

    # All original arguments
    ap.add_argument('url_or_file_or_dash', help='URL, file path, or "-" for stdin')
    ap.add_argument('-l','--lang', default=os.environ.get('KOKORO_LANG','en-us'), help='language code')
    ap.add_argument('-v','--voice', default=os.environ.get('KOKORO_VOICE',''), help='voice id')
    ap.add_argument('-c','--chunk', type=int, default=320, help='target characters per piece')
    ap.add_argument('-o','--out', help='write to WAV/MP3 instead of streaming')
    ap.add_argument('--model', default=os.environ.get('KOKORO_MODEL', str(Path.home()/'models/kokoro/kokoro-v1.0.onnx')), help='kokoro model path')
    ap.add_argument('--voices', default=os.environ.get('KOKORO_VOICES', str(Path.home()/'models/kokoro/voices-v1.0.bin')), help='voices pack path')
    ap.add_argument('--render', action='store_true', help='use Playwright to render JS pages')
    ap.add_argument('--player', default=os.environ.get('SAYREAD_PLAYER',''), help='ffplay|mpv|paplay|aplay')
    ap.add_argument('--max-chars', type=int, default=int(os.environ.get('SAYREAD_MAXCHARS','0')), help='truncate text')
    ap.add_argument('--trim-silence', action='store_true', help='remove leading/trailing silence')
    ap.add_argument('-d','--debug', action='store_true', help='debug output')

    # New continuous streaming options
    ap.add_argument('--continuous-buffered', action='store_true',
                   help='use buffered continuous streaming (best for slow synthesis)')
    ap.add_argument('--fallback-original', action='store_true',
                   help='fallback to original say_read.py if continuous streaming fails')

    args = ap.parse_args()

    # Create and run the continuous reader
    reader = ContinuousSayRead(args)

    try:
        return reader.process_url_continuous()
    except Exception as e:
        if args.debug:
            import traceback
            traceback.print_exc()

        if args.fallback_original:
            print(f"[say-read-continuous] Falling back to original implementation due to: {e}", file=sys.stderr)
            # Fallback to original say_read
            return say_read.main() if hasattr(say_read, 'main') else 1
        else:
            print(f"[say-read-continuous] Error: {e}", file=sys.stderr)
            return 1

if __name__ == "__main__":
    sys.exit(main())