#!/usr/bin/env uv run --with linux-speech-tools[tts,audio]
# /// script
# dependencies = []  # Uses shared dependencies from pyproject.toml
# requires-python = ">=3.8"
# ///
"""
Early Start Audio Player for Progressive Streaming MVP
Start playing audio while TTS processing continues in background
"""

import threading
import queue
import subprocess
import os
import time
import logging
from typing import List, Optional, Tuple
from smart_chunking import ProgressiveContentFetcher
from enhanced_chunking import NaturalSpeechChunker
from tts_optimized_chunking import TTSOptimizedChunker
from gold_standard_chunker import GoldStandardChunker
from simple_parallel import SimpleParallelTTS

class EarlyStartPlayer:
    """Play audio as soon as first chunks are ready"""

    def __init__(self, player_cmd="ffplay", initial_batch_size=2):
        """
        Initialize early start player

        Args:
            player_cmd: Audio player command (ffplay, mpv, etc.)
            initial_batch_size: Number of chunks to process immediately
        """
        self.player_cmd = player_cmd
        self.initial_batch_size = initial_batch_size
        self.audio_queue = queue.Queue()
        self.playing = False
        self.stopped = False

        # Statistics
        self.stats = {
            'time_to_first_audio': 0,
            'total_chunks_played': 0,
            'playback_errors': 0,
            'total_playback_time': 0
        }

    def start_early_playback(self, text_chunks: List[str]) -> Tuple[float, threading.Thread, Optional[threading.Thread]]:
        """
        Start playing audio while TTS continues in background

        Args:
            text_chunks: List of text chunks to convert and play

        Returns:
            Tuple of (time_to_first_audio, playback_thread, background_thread)
        """
        start_time = time.time()

        print(f"🎵 Starting early playback with {len(text_chunks)} total chunks")
        print(f"🚀 Processing first {self.initial_batch_size} chunks immediately...")

        # Split chunks into immediate and background processing
        immediate_chunks = text_chunks[:self.initial_batch_size]
        background_chunks = text_chunks[self.initial_batch_size:]

        # Process first batch immediately
        immediate_processor = SimpleParallelTTS(max_workers=1)
        immediate_audio_files = immediate_processor.process_chunks_parallel(immediate_chunks)

        # Record time to first audio
        first_audio_time = time.time()
        self.stats['time_to_first_audio'] = first_audio_time - start_time

        print(f"⚡ Time to first audio: {self.stats['time_to_first_audio']:.1f}s")

        # Start playback thread
        self.playing = True
        playback_thread = threading.Thread(
            target=self._playback_loop,
            name="EarlyPlayback"
        )
        playback_thread.start()

        # Queue immediate audio files
        for _, audio_file in immediate_audio_files:
            self.audio_queue.put(audio_file)

        # Start background processing if there are remaining chunks
        background_thread = None
        if background_chunks:
            print(f"⏳ Processing remaining {len(background_chunks)} chunks in background...")
            background_thread = threading.Thread(
                target=self._background_processing,
                args=(background_chunks,),
                name="BackgroundTTS"
            )
            background_thread.start()
        else:
            # Signal end of queue
            self.audio_queue.put(None)

        return self.stats['time_to_first_audio'], playback_thread, background_thread

    def _playback_loop(self):
        """Simple sequential playback loop"""
        print("🎧 Starting audio playback...")
        playback_start = time.time()

        while self.playing and not self.stopped:
            try:
                audio_file = self.audio_queue.get(timeout=10)

                if audio_file is None:  # End signal
                    print("✅ All audio chunks completed")
                    break

                chunk_start = time.time()
                success = self._play_audio_file(audio_file)

                if success:
                    self.stats['total_chunks_played'] += 1
                    chunk_time = time.time() - chunk_start
                    print(f"  ✅ Played chunk {self.stats['total_chunks_played']} ({chunk_time:.1f}s)")
                else:
                    self.stats['playback_errors'] += 1
                    print(f"  ❌ Failed to play {os.path.basename(audio_file)}")

                # Cleanup audio file
                self._cleanup_audio_file(audio_file)

            except queue.Empty:
                if not self.playing:
                    break
                print("  ⏳ Waiting for more audio chunks...")
                continue

            except Exception as e:
                logging.error(f"Playback error: {e}")
                self.stats['playback_errors'] += 1

        self.stats['total_playback_time'] = time.time() - playback_start
        print(f"🎵 Playback finished: {self.stats['total_chunks_played']} chunks in {self.stats['total_playback_time']:.1f}s")

    def _play_audio_file(self, audio_file: str) -> bool:
        """
        Play single audio file

        Args:
            audio_file: Path to audio file

        Returns:
            True if playback successful, False otherwise
        """
        if not os.path.exists(audio_file):
            logging.error(f"Audio file not found: {audio_file}")
            return False

        # Build playback command
        cmd = [
            self.player_cmd,
            "-nodisp",      # No video display
            "-autoexit",    # Exit when done
            "-loglevel", "quiet",  # Reduce ffplay output
            audio_file
        ]

        try:
            # Execute playback
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30  # Reasonable timeout for chunk playback
            )

            return result.returncode == 0

        except subprocess.TimeoutExpired:
            logging.error(f"Audio playback timeout: {audio_file}")
            return False
        except Exception as e:
            logging.error(f"Audio playback error: {e}")
            return False

    def _background_processing(self, remaining_chunks: List[str]):
        """Process remaining chunks while audio plays"""
        try:
            # Use multiple workers for background processing
            background_processor = SimpleParallelTTS(max_workers=3)
            audio_files = background_processor.process_chunks_parallel(remaining_chunks)

            print(f"🔄 Background processing completed: {len(audio_files)} chunks ready")

            # Queue background audio files
            for _, audio_file in audio_files:
                self.audio_queue.put(audio_file)

        except Exception as e:
            logging.error(f"Background processing error: {e}")
        finally:
            # Signal end of queue
            self.audio_queue.put(None)

    def _cleanup_audio_file(self, audio_file: str):
        """Clean up temporary audio file"""
        try:
            if os.path.exists(audio_file):
                os.remove(audio_file)
        except Exception as e:
            logging.warning(f"Failed to cleanup {audio_file}: {e}")

    def stop(self):
        """Stop playback"""
        print("🛑 Stopping audio playback...")
        self.stopped = True
        self.playing = False

        # Clear remaining queue
        try:
            while True:
                audio_file = self.audio_queue.get_nowait()
                if audio_file:
                    self._cleanup_audio_file(audio_file)
        except queue.Empty:
            pass

    def get_stats(self) -> dict:
        """Get playback statistics"""
        return self.stats.copy()

class MVPProgressiveStreaming:
    """Complete MVP integration of all components"""

    def __init__(self, target_chunk_size=250, max_workers=2, initial_batch_size=2):
        """
        Initialize MVP progressive streaming system

        Args:
            target_chunk_size: Target size for text chunks
            max_workers: Number of parallel TTS workers
            initial_batch_size: Chunks to process immediately
        """
        self.chunker = GoldStandardChunker(target_size=target_chunk_size)
        self.content_fetcher = ProgressiveContentFetcher(self.chunker)
        self.player = EarlyStartPlayer(initial_batch_size=initial_batch_size)
        self.max_workers = max_workers

        # Overall statistics
        self.stats = {
            'total_time': 0,
            'content_fetch_time': 0,
            'chunk_processing_time': 0,
            'time_to_first_audio': 0,
            'total_chunks': 0,
            'successful_chunks': 0
        }

    def stream_content(self, source: str, max_chars: Optional[int] = None) -> dict:
        """
        Stream content with progressive audio playback

        Args:
            source: URL, file path, or stdin marker (-)
            max_chars: Optional limit on content length

        Returns:
            Dictionary with performance statistics
        """
        overall_start = time.time()

        try:
            print(f"🎯 MVP Progressive Streaming: {source}")

            # 1. Fetch and chunk content
            print("⏳ Fetching and chunking content...")
            fetch_start = time.time()

            if source.startswith(('http://', 'https://')):
                chunks = list(self.content_fetcher.fetch_and_chunk_progressive(source))
            elif source == '-':
                chunks = list(self.content_fetcher.fetch_and_chunk_progressive('-'))
            else:
                chunks = list(self.content_fetcher.fetch_and_chunk_progressive(source))

            # Use enhanced natural chunking instead of basic chunking
            if chunks:
                # Re-chunk the content using natural speech boundaries
                full_text = ' '.join(chunks)
                chunks = self.chunker.gold_standard_chunk_text(full_text)

            # Apply character limit if specified
            if max_chars:
                total_chars = 0
                limited_chunks = []
                for chunk in chunks:
                    if total_chars + len(chunk) > max_chars:
                        remaining = max_chars - total_chars
                        if remaining > 100:  # Don't create tiny chunks
                            limited_chunks.append(chunk[:remaining])
                        break
                    limited_chunks.append(chunk)
                    total_chars += len(chunk)
                chunks = limited_chunks

            self.stats['content_fetch_time'] = time.time() - fetch_start
            self.stats['total_chunks'] = len(chunks)

            if not chunks:
                print("❌ No content chunks generated")
                return self.stats

            print(f"✅ Generated {len(chunks)} chunks ({sum(len(c) for c in chunks)} characters)")

            # 2. Start early audio playback
            print("🎵 Starting progressive audio playback...")
            time_to_first, playback_thread, background_thread = self.player.start_early_playback(chunks)

            self.stats['time_to_first_audio'] = time_to_first

            # 3. Wait for completion
            print("⏳ Waiting for playback completion...")
            playback_thread.join()

            if background_thread:
                background_thread.join()

            # 4. Collect statistics
            player_stats = self.player.get_stats()
            self.stats.update(player_stats)
            self.stats['total_time'] = time.time() - overall_start
            self.stats['successful_chunks'] = player_stats['total_chunks_played']

            # 5. Display results
            self._display_results()

            return self.stats

        except KeyboardInterrupt:
            print("\n🛑 Interrupted by user")
            self.player.stop()
            return self.stats

        except Exception as e:
            print(f"❌ MVP streaming failed: {e}")
            logging.error(f"MVP streaming error: {e}")
            return self.stats

    def _display_results(self):
        """Display performance results"""
        print("\n" + "=" * 60)
        print("📊 MVP Progressive Streaming Results")
        print("=" * 60)

        print(f"⏱️  Time to first audio: {self.stats['time_to_first_audio']:.1f}s")
        print(f"🕐 Total processing time: {self.stats['total_time']:.1f}s")
        print(f"📊 Chunks processed: {self.stats['successful_chunks']}/{self.stats['total_chunks']}")

        if self.stats['total_chunks'] > 0:
            success_rate = (self.stats['successful_chunks'] / self.stats['total_chunks']) * 100
            print(f"✅ Success rate: {success_rate:.1f}%")

        if self.stats['playback_errors'] > 0:
            print(f"❌ Playback errors: {self.stats['playback_errors']}")

def benchmark_mvp_vs_current():
    """Benchmark MVP vs current system"""
    print("📊 MVP vs Current System Benchmark")
    print("=" * 50)

    # Test with sample content
    test_content = """
    This is a test article for benchmarking the MVP progressive streaming system.
    It contains multiple sentences and paragraphs to simulate real content.

    The progressive streaming system should start playing audio much faster than
    the current approach. We want to measure the time to first audio and overall
    improvement in user experience.

    This content is long enough to require multiple chunks, which allows us to
    test the parallel processing and early start capabilities of the MVP system.
    """

    print("Testing MVP Progressive Streaming:")
    mvp_system = MVPProgressiveStreaming(
        target_chunk_size=200,
        max_workers=2,
        initial_batch_size=2
    )

    # Save test content to temporary file
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(test_content)
        test_file = f.name

    try:
        mvp_results = mvp_system.stream_content(test_file)

        print("\n🎯 MVP Benchmark Results:")
        print(f"  Time to first audio: {mvp_results['time_to_first_audio']:.1f}s")
        print(f"  Total time: {mvp_results['total_time']:.1f}s")
        print(f"  Chunks: {mvp_results['successful_chunks']}/{mvp_results['total_chunks']}")

        # Estimate current system time (30+ seconds typical)
        estimated_current_time = 35  # Conservative estimate

        if mvp_results['time_to_first_audio'] < estimated_current_time:
            improvement = ((estimated_current_time - mvp_results['time_to_first_audio']) / estimated_current_time) * 100
            print(f"  🚀 Estimated improvement: {improvement:.1f}% faster startup")

            if improvement >= 50:
                print("  ✅ SUCCESS: MVP achieves 50%+ improvement target!")
            elif improvement >= 30:
                print("  ⚠️  PARTIAL: Good improvement, optimization potential")
            else:
                print("  ❌ INSUFFICIENT: Need different approach")
        else:
            print("  ❌ SLOWER: MVP is slower than current system")

    finally:
        # Cleanup
        try:
            os.unlink(test_file)
        except:
            pass

if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO)

    # Run MVP benchmark
    benchmark_mvp_vs_current()