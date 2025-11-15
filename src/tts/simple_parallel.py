#!/usr/bin/env uv run --with linux-speech-tools[tts]
# /// script
# dependencies = []  # Uses shared dependencies from pyproject.toml
# requires-python = ">=3.8"
# ///
"""
Simple Parallel TTS Processing for Progressive Streaming MVP
Basic parallelization to validate performance assumptions
"""

import threading
import queue
import subprocess
import os
import time
import tempfile
import logging
from typing import List, Tuple, Optional

class SimpleParallelTTS:
    """Basic parallel TTS processing - no complex audio pipeline"""

    def __init__(self, max_workers=2, tts_timeout=30):
        """
        Initialize parallel TTS processor

        Args:
            max_workers: Number of concurrent TTS processes (conservative start)
            tts_timeout: Timeout for individual TTS operations
        """
        self.max_workers = max_workers
        self.tts_timeout = tts_timeout
        self.tts_python = os.path.expanduser("~/.venvs/tts/bin/python")
        self.say_read_script = os.path.join(os.path.dirname(__file__), "say_read.py")

        # Thread-safe structures
        self.text_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.error_count = 0
        self.processing_stats = {
            'total_chunks': 0,
            'completed_chunks': 0,
            'failed_chunks': 0,
            'total_time': 0,
            'average_time_per_chunk': 0
        }

    def process_chunks_parallel(self, text_chunks: List[str]) -> List[Tuple[int, str]]:
        """
        Process multiple text chunks in parallel

        Args:
            text_chunks: List of text strings to convert to audio

        Returns:
            List of (index, audio_file_path) tuples, sorted by original order
        """
        print(f"🔄 Starting parallel TTS processing for {len(text_chunks)} chunks")
        start_time = time.time()

        self.processing_stats['total_chunks'] = len(text_chunks)
        audio_files = []

        # Determine optimal worker count
        actual_workers = min(self.max_workers, len(text_chunks))

        # Start worker threads
        workers = []
        for i in range(actual_workers):
            worker = threading.Thread(
                target=self._tts_worker,
                name=f"TTSWorker-{i+1}"
            )
            worker.start()
            workers.append(worker)

        # Queue all text chunks with their indices
        for i, chunk_text in enumerate(text_chunks):
            self.text_queue.put((i, chunk_text))

        # Signal completion to workers
        for _ in range(actual_workers):
            self.text_queue.put(None)

        # Collect results
        results_collected = 0
        while results_collected < len(text_chunks):
            try:
                result = self.result_queue.get(timeout=self.tts_timeout + 5)
                if result is not None:
                    audio_files.append(result)
                    results_collected += 1
                    print(f"  ✅ Completed chunk {results_collected}/{len(text_chunks)}")
            except queue.Empty:
                logging.error("Timeout waiting for TTS results")
                break

        # Wait for all workers to complete
        for worker in workers:
            worker.join(timeout=5)

        # Sort results by original order
        audio_files.sort(key=lambda x: x[0])

        # Update statistics
        total_time = time.time() - start_time
        self.processing_stats['total_time'] = total_time
        self.processing_stats['completed_chunks'] = len(audio_files)
        self.processing_stats['failed_chunks'] = len(text_chunks) - len(audio_files)

        if len(audio_files) > 0:
            self.processing_stats['average_time_per_chunk'] = total_time / len(audio_files)

        print(f"⚡ Parallel processing complete: {len(audio_files)}/{len(text_chunks)} chunks in {total_time:.1f}s")

        return audio_files

    def _tts_worker(self):
        """Worker thread for TTS processing"""
        worker_name = threading.current_thread().name

        while True:
            try:
                item = self.text_queue.get(timeout=1)

                if item is None:  # Shutdown signal
                    break

                chunk_index, text = item
                print(f"  🎤 {worker_name}: Processing chunk {chunk_index+1}")

                # Generate audio file
                audio_file = self._generate_audio_file(chunk_index, text, worker_name)

                if audio_file:
                    self.result_queue.put((chunk_index, audio_file))
                else:
                    self.error_count += 1
                    logging.error(f"{worker_name}: Failed to generate audio for chunk {chunk_index}")
                    self.result_queue.put(None)  # Signal failed chunk

                self.text_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"{worker_name}: Worker error: {e}")
                self.result_queue.put(None)

        print(f"  👋 {worker_name}: Finished")

    def _generate_audio_file(self, chunk_index: int, text: str, worker_name: str) -> Optional[str]:
        """
        Generate audio file for a single text chunk

        Args:
            chunk_index: Index of the chunk for ordering
            text: Text to convert to speech
            worker_name: Name of worker thread (for logging)

        Returns:
            Path to generated audio file, or None if failed
        """
        # Create unique temporary file
        temp_dir = tempfile.gettempdir()
        audio_file = os.path.join(
            temp_dir,
            f"mvp_chunk_{chunk_index}_{os.getpid()}_{int(time.time())}.wav"
        )

        # Prepare TTS command
        cmd = [
            self.tts_python,
            self.say_read_script,
            "-o", audio_file,
            "-"  # Read from stdin
        ]

        try:
            start_time = time.time()

            # Execute TTS command
            result = subprocess.run(
                cmd,
                input=text,
                text=True,
                capture_output=True,
                timeout=self.tts_timeout
            )

            generation_time = time.time() - start_time

            if result.returncode == 0 and os.path.exists(audio_file):
                file_size = os.path.getsize(audio_file)
                print(f"    ✅ {worker_name}: Generated {os.path.basename(audio_file)} "
                      f"({file_size} bytes) in {generation_time:.1f}s")
                return audio_file
            else:
                print(f"    ❌ {worker_name}: TTS failed for chunk {chunk_index}")
                if result.stderr:
                    print(f"       Error: {result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            print(f"    ⏰ {worker_name}: TTS timeout for chunk {chunk_index}")
            return None
        except Exception as e:
            print(f"    💥 {worker_name}: TTS exception for chunk {chunk_index}: {e}")
            return None

    def get_processing_stats(self) -> dict:
        """Get processing statistics"""
        return self.processing_stats.copy()

class SequentialTTSProcessor:
    """Sequential TTS processor for comparison"""

    def __init__(self, tts_timeout=30):
        self.tts_timeout = tts_timeout
        self.tts_python = os.path.expanduser("~/.venvs/tts/bin/python")
        self.say_read_script = os.path.join(os.path.dirname(__file__), "say_read.py")

    def process_chunks_sequential(self, text_chunks: List[str]) -> List[Tuple[int, str]]:
        """Process chunks sequentially for comparison"""
        print(f"🔄 Starting sequential TTS processing for {len(text_chunks)} chunks")
        start_time = time.time()

        audio_files = []

        for i, text in enumerate(text_chunks):
            print(f"  🎤 Processing chunk {i+1}/{len(text_chunks)}")

            temp_dir = tempfile.gettempdir()
            audio_file = os.path.join(
                temp_dir,
                f"seq_chunk_{i}_{os.getpid()}_{int(time.time())}.wav"
            )

            cmd = [
                self.tts_python,
                self.say_read_script,
                "-o", audio_file,
                "-"
            ]

            try:
                result = subprocess.run(
                    cmd,
                    input=text,
                    text=True,
                    capture_output=True,
                    timeout=self.tts_timeout
                )

                if result.returncode == 0 and os.path.exists(audio_file):
                    audio_files.append((i, audio_file))
                    print(f"    ✅ Generated {os.path.basename(audio_file)}")
                else:
                    print(f"    ❌ Failed to generate audio for chunk {i}")

            except subprocess.TimeoutExpired:
                print(f"    ⏰ Timeout for chunk {i}")
            except Exception as e:
                print(f"    💥 Error for chunk {i}: {e}")

        total_time = time.time() - start_time
        print(f"⚡ Sequential processing complete: {len(audio_files)}/{len(text_chunks)} chunks in {total_time:.1f}s")

        return audio_files

def benchmark_parallel_vs_sequential():
    """Benchmark parallel vs sequential TTS processing"""
    print("📊 Benchmarking Parallel vs Sequential TTS")
    print("=" * 60)

    # Create test chunks
    test_chunks = [
        "This is the first test chunk for TTS processing. It's a reasonable length for speech synthesis.",
        "Here's the second chunk that we want to convert to audio. It should take a similar amount of time.",
        "The third chunk continues our testing. We want to see how parallel processing improves performance.",
        "Fourth chunk adds more content to process. Multiple chunks help us see the benefits of parallelization.",
        "Finally, the fifth chunk completes our test set. This gives us enough data to measure improvements."
    ]

    print(f"Testing with {len(test_chunks)} chunks")
    print(f"Average chunk length: {sum(len(chunk) for chunk in test_chunks) // len(test_chunks)} characters")

    # Test sequential processing
    print("\n🔄 Testing Sequential Processing:")
    sequential_processor = SequentialTTSProcessor()
    start_time = time.time()
    sequential_results = sequential_processor.process_chunks_sequential(test_chunks)
    sequential_time = time.time() - start_time

    # Test parallel processing
    print("\n🔄 Testing Parallel Processing:")
    parallel_processor = SimpleParallelTTS(max_workers=3)
    start_time = time.time()
    parallel_results = parallel_processor.process_chunks_parallel(test_chunks)
    parallel_time = time.time() - start_time

    # Calculate improvement
    if sequential_time > 0:
        improvement_percent = ((sequential_time - parallel_time) / sequential_time) * 100
    else:
        improvement_percent = 0

    # Results
    print("\n📊 Performance Comparison:")
    print(f"  Sequential: {len(sequential_results)} chunks in {sequential_time:.1f}s")
    print(f"  Parallel:   {len(parallel_results)} chunks in {parallel_time:.1f}s")
    print(f"  Improvement: {improvement_percent:.1f}% faster")

    # Cleanup
    print("\n🧹 Cleaning up test files...")
    for _, audio_file in sequential_results + parallel_results:
        try:
            if os.path.exists(audio_file):
                os.remove(audio_file)
        except:
            pass

    return {
        'sequential_time': sequential_time,
        'parallel_time': parallel_time,
        'improvement_percent': improvement_percent,
        'chunks_processed': len(test_chunks)
    }

if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO)

    # Run benchmark
    results = benchmark_parallel_vs_sequential()

    print(f"\n🎯 Benchmark Results:")
    print(f"  Improvement: {results['improvement_percent']:.1f}% faster with parallel processing")
    print(f"  Speedup factor: {results['sequential_time'] / max(results['parallel_time'], 0.001):.1f}x")

    if results['improvement_percent'] > 30:
        print("  ✅ Significant improvement - parallel processing validated!")
    elif results['improvement_percent'] > 10:
        print("  ⚠️ Moderate improvement - parallel processing shows promise")
    else:
        print("  ❌ Minimal improvement - parallel processing may not be worth complexity")