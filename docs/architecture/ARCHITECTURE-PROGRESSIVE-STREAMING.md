# 🏗️ Progressive Streaming Technical Architecture
## Detailed Implementation Design

**Version**: 1.0
**Date**: 2024-11-14
**Related**: PRD-PROGRESSIVE-STREAMING.md

---

## 🎯 Architecture Overview

### **Multi-Threaded Pipeline Design**

```
┌─────────────────────────────────────────────────────────────────┐
│                        MAIN PROCESS                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐    ┌─────────────────┐    ┌──────────────┐ │
│  │  THREAD 1       │    │  THREAD 2       │    │  THREAD 3    │ │
│  │  Content        │    │  TTS Audio      │    │  Audio       │ │
│  │  Fetcher        │    │  Generator      │    │  Player      │ │
│  │                 │    │                 │    │              │ │
│  │ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌──────────┐ │ │
│  │ │URL/File     │ │    │ │Content      │ │    │ │Audio     │ │ │
│  │ │Parser       │ │    │ │Queue        │ │    │ │Buffer    │ │ │
│  │ └─────┬───────┘ │    │ └─────┬───────┘ │    │ └─────┬────┘ │ │
│  │       │         │    │       │         │    │       │      │ │
│  │       ▼         │    │       ▼         │    │       ▼      │ │
│  │ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌──────────┐ │ │
│  │ │Content      │ │    │ │TTS Engine   │ │    │ │Seamless  │ │ │
│  │ │Queue        │ │────┼▶│(say_read.py)│ │────┼▶│Playback  │ │ │
│  │ └─────────────┘ │    │ └─────────────┘ │    │ └──────────┘ │ │
│  └─────────────────┘    └─────────────────┘    └──────────────┘ │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                    INTER-PROCESS COMMUNICATION                 │
│                                                                 │
│  ┌─────────────────┐    ┌─────────────────┐    ┌──────────────┐ │
│  │  GNOME D-Bus    │    │  Progress       │    │  Media       │ │
│  │  Service        │    │  Tracking       │    │  Controls    │ │
│  │                 │    │                 │    │              │ │
│  │ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌──────────┐ │ │
│  │ │Notification │ │    │ │Buffer       │ │    │ │Pause/    │ │ │
│  │ │Manager      │ │    │ │Status       │ │    │ │Resume/   │ │ │
│  │ └─────────────┘ │    │ └─────────────┘ │    │ │Stop      │ │ │
│  └─────────────────┘    └─────────────────┘    │ └──────────┘ │ │
│                                                └──────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🧵 Thread Architecture

### **Thread 1: Progressive Content Fetcher**

```python
class ProgressiveContentFetcher(threading.Thread):
    """
    Fetches and parses content progressively, queuing chunks immediately
    """

    def __init__(self, source, content_queue, chunk_size=200, max_chars=None):
        super().__init__(name="ContentFetcher")
        self.source = source
        self.content_queue = content_queue
        self.chunk_size = chunk_size
        self.max_chars = max_chars
        self.stop_event = threading.Event()
        self.stats = {
            'total_chunks': 0,
            'bytes_processed': 0,
            'fetch_time': 0
        }

    def run(self):
        """Main fetcher loop"""
        try:
            if self.source.startswith(('http://', 'https://')):
                self._fetch_url_progressive()
            elif self.source == '-':
                self._fetch_stdin_progressive()
            else:
                self._fetch_file_progressive()
        except Exception as e:
            self.content_queue.put(('error', str(e)))

    def _fetch_url_progressive(self):
        """Fetch URL content with streaming"""
        import requests
        from bs4 import BeautifulSoup

        with requests.get(self.source, stream=True) as response:
            response.raise_for_status()

            # Parse HTML progressively
            content_buffer = ""
            for chunk in response.iter_content(chunk_size=8192, decode_unicode=True):
                if self.stop_event.is_set():
                    break

                content_buffer += chunk

                # Parse when we have enough content
                if len(content_buffer) > 4096:
                    text_chunks = self._extract_text_chunks(content_buffer)
                    for text_chunk in text_chunks:
                        self._queue_text_chunk(text_chunk)
                    content_buffer = ""

            # Process remaining content
            if content_buffer:
                text_chunks = self._extract_text_chunks(content_buffer)
                for text_chunk in text_chunks:
                    self._queue_text_chunk(text_chunk)

    def _extract_text_chunks(self, html_content):
        """Extract readable text and split into chunks"""
        soup = BeautifulSoup(html_content, 'html.parser')

        # Remove unwanted elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer']):
            element.decompose()

        text = soup.get_text()
        text = ' '.join(text.split())  # Normalize whitespace

        # Split into logical chunks
        return self._smart_chunk_split(text)

    def _smart_chunk_split(self, text):
        """Split text into logical chunks (sentences, paragraphs)"""
        chunks = []
        sentences = text.split('. ')

        current_chunk = ""
        for sentence in sentences:
            if len(current_chunk) + len(sentence) > self.chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence + ". "
            else:
                current_chunk += sentence + ". "

        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks

    def _queue_text_chunk(self, text_chunk):
        """Queue text chunk for TTS processing"""
        if text_chunk.strip():
            self.content_queue.put(('text', text_chunk))
            self.stats['total_chunks'] += 1

    def stop(self):
        """Signal thread to stop"""
        self.stop_event.set()
```

### **Thread 2: TTS Audio Generator**

```python
class TTSAudioGenerator(threading.Thread):
    """
    Processes text chunks into audio files using TTS engine
    """

    def __init__(self, content_queue, audio_buffer, tts_config):
        super().__init__(name="TTSGenerator")
        self.content_queue = content_queue
        self.audio_buffer = audio_buffer
        self.tts_config = tts_config
        self.stop_event = threading.Event()
        self.temp_dir = tempfile.mkdtemp(prefix='progressive_tts_')
        self.stats = {
            'chunks_processed': 0,
            'audio_generated': 0,
            'processing_time': 0
        }

    def run(self):
        """Main TTS processing loop"""
        while not self.stop_event.is_set():
            try:
                # Get next text chunk (with timeout)
                item = self.content_queue.get(timeout=1.0)

                if item[0] == 'text':
                    audio_file = self._generate_audio(item[1])
                    if audio_file:
                        self.audio_buffer.add_chunk(audio_file)
                        self.stats['chunks_processed'] += 1
                elif item[0] == 'error':
                    self.audio_buffer.add_chunk(('error', item[1]))

                self.content_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"TTS generation error: {e}")
                self.audio_buffer.add_chunk(('error', str(e)))

    def _generate_audio(self, text):
        """Generate audio file from text chunk"""
        start_time = time.time()

        # Create temporary output file
        audio_file = os.path.join(self.temp_dir, f'chunk_{self.stats["chunks_processed"]}.wav')

        # Call TTS engine
        cmd = [
            self.tts_config['python_path'],
            self.tts_config['say_read_path'],
            '-o', audio_file,
            '--lang', self.tts_config.get('lang', 'en-us'),
            '--voice', self.tts_config.get('voice', 'af_heart'),
            '-'  # stdin
        ]

        try:
            result = subprocess.run(
                cmd,
                input=text,
                text=True,
                capture_output=True,
                timeout=30
            )

            if result.returncode == 0 and os.path.exists(audio_file):
                self.stats['processing_time'] += time.time() - start_time
                return audio_file
            else:
                logging.error(f"TTS failed: {result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            logging.error("TTS generation timeout")
            return None
        except Exception as e:
            logging.error(f"TTS subprocess error: {e}")
            return None

    def cleanup(self):
        """Clean up temporary files"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def stop(self):
        """Signal thread to stop"""
        self.stop_event.set()
```

### **Thread 3: Seamless Audio Player**

```python
class SeamlessAudioPlayer(threading.Thread):
    """
    Plays audio chunks with seamless transitions and gap elimination
    """

    def __init__(self, audio_buffer, gnome_controls, player_config):
        super().__init__(name="AudioPlayer")
        self.audio_buffer = audio_buffer
        self.gnome_controls = gnome_controls
        self.player_config = player_config
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.current_process = None
        self.stats = {
            'chunks_played': 0,
            'gaps_eliminated': 0,
            'playback_time': 0
        }

    def run(self):
        """Main playback loop"""
        while not self.stop_event.is_set():
            try:
                # Get next audio chunk
                audio_chunk = self.audio_buffer.get_next_chunk(timeout=1.0)

                if audio_chunk:
                    if audio_chunk[0] == 'error':
                        self._handle_playback_error(audio_chunk[1])
                    else:
                        self._play_chunk_seamless(audio_chunk)
                        self.stats['chunks_played'] += 1

                # Handle pause/resume
                if self.pause_event.is_set():
                    self._handle_pause()

            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"Playback error: {e}")

    def _play_chunk_seamless(self, audio_file):
        """Play audio chunk with seamless transition"""
        if self.player_config['method'] == 'crossfade':
            self._play_with_crossfade(audio_file)
        else:
            self._play_with_concat(audio_file)

    def _play_with_crossfade(self, audio_file):
        """Play with crossfade transition to eliminate gaps"""
        # This requires advanced audio processing
        # For now, implement concatenation method
        self._play_with_concat(audio_file)

    def _play_with_concat(self, audio_file):
        """Play using ffplay with minimized gaps"""
        cmd = [
            'ffplay',
            '-nodisp',           # No video display
            '-autoexit',         # Exit when done
            '-fast',             # Fast decode
            '-sync', 'audio',    # Audio sync
            audio_file
        ]

        try:
            # Start playback
            self.current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # Wait for completion or stop signal
            while self.current_process.poll() is None:
                if self.stop_event.is_set():
                    self.current_process.terminate()
                    break

                if self.pause_event.is_set():
                    self._handle_pause()

                time.sleep(0.1)

            self.current_process = None

        except Exception as e:
            logging.error(f"Audio playback error: {e}")

    def _handle_pause(self):
        """Handle pause/resume functionality"""
        if self.current_process:
            # Pause current playback
            self.current_process.send_signal(signal.SIGSTOP)

            # Wait for resume signal
            self.pause_event.wait()

            # Resume playback
            self.current_process.send_signal(signal.SIGCONT)

    def _handle_playback_error(self, error_msg):
        """Handle playback errors gracefully"""
        logging.error(f"Playback error: {error_msg}")
        self.gnome_controls.show_error_notification(error_msg)

    def pause(self):
        """Pause playback"""
        self.pause_event.set()

    def resume(self):
        """Resume playback"""
        self.pause_event.clear()

    def stop(self):
        """Stop playback"""
        self.stop_event.set()
        if self.current_process:
            self.current_process.terminate()
```

---

## 🔄 Shared Data Structures

### **Audio Buffer Manager**

```python
class AudioBufferManager:
    """
    Thread-safe audio buffer with intelligent management
    """

    def __init__(self, buffer_size=5, low_water_mark=2):
        self.buffer_size = buffer_size
        self.low_water_mark = low_water_mark
        self.audio_queue = queue.Queue(maxsize=buffer_size)
        self.buffer_lock = threading.Lock()
        self.stats = {
            'chunks_buffered': 0,
            'buffer_underruns': 0,
            'buffer_overruns': 0
        }

    def add_chunk(self, audio_file):
        """Add audio chunk to buffer (thread-safe)"""
        try:
            self.audio_queue.put(audio_file, block=False)
            self.stats['chunks_buffered'] += 1
        except queue.Full:
            # Buffer overflow - this shouldn't happen with proper flow control
            self.stats['buffer_overruns'] += 1
            logging.warning("Audio buffer overflow")

    def get_next_chunk(self, timeout=1.0):
        """Get next audio chunk (blocking with timeout)"""
        try:
            chunk = self.audio_queue.get(timeout=timeout)

            # Check for buffer underrun
            if self.audio_queue.qsize() < self.low_water_mark:
                self.stats['buffer_underruns'] += 1
                logging.info(f"Buffer low: {self.audio_queue.qsize()} chunks")

            return chunk
        except queue.Empty:
            return None

    def get_buffer_status(self):
        """Get current buffer status for UI updates"""
        with self.buffer_lock:
            return {
                'size': self.audio_queue.qsize(),
                'max_size': self.buffer_size,
                'percentage': (self.audio_queue.qsize() / self.buffer_size) * 100,
                'low_water': self.audio_queue.qsize() <= self.low_water_mark
            }

    def clear(self):
        """Clear buffer (for stop/reset)"""
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
```

### **Progress Tracking System**

```python
class ProgressTracker:
    """
    Tracks progress across all pipeline stages
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.progress = {
            'content_fetched': 0,      # Bytes/chunks fetched
            'content_total': 0,        # Estimated total
            'audio_generated': 0,      # Chunks converted to audio
            'audio_played': 0,         # Chunks played
            'buffer_status': {},       # Current buffer state
            'estimated_duration': 0,   # Total estimated time
            'elapsed_time': 0,         # Time since start
            'remaining_time': 0        # Estimated remaining time
        }

    def update_fetch_progress(self, chunks_fetched, total_estimate=None):
        """Update content fetching progress"""
        with self.lock:
            self.progress['content_fetched'] = chunks_fetched
            if total_estimate:
                self.progress['content_total'] = total_estimate

    def update_generation_progress(self, chunks_generated):
        """Update audio generation progress"""
        with self.lock:
            self.progress['audio_generated'] = chunks_generated

    def update_playback_progress(self, chunks_played):
        """Update playback progress"""
        with self.lock:
            self.progress['audio_played'] = chunks_played

            # Calculate time estimates
            if chunks_played > 0 and self.progress['content_total'] > 0:
                completion_ratio = chunks_played / self.progress['content_total']
                self.progress['remaining_time'] = (
                    self.progress['elapsed_time'] / completion_ratio
                ) - self.progress['elapsed_time']

    def get_progress_summary(self):
        """Get progress summary for UI updates"""
        with self.lock:
            total = max(self.progress['content_total'], 1)
            played = self.progress['audio_played']

            return {
                'percentage': min((played / total) * 100, 100),
                'current_chunk': played,
                'total_chunks': self.progress['content_total'],
                'buffer_chunks': self.progress.get('buffer_status', {}).get('size', 0),
                'time_remaining': self.progress['remaining_time'],
                'status': self._get_status_text()
            }

    def _get_status_text(self):
        """Generate human-readable status"""
        if self.progress['audio_played'] == 0:
            return "Starting..."
        elif self.progress['audio_played'] >= self.progress['content_total']:
            return "Completed"
        else:
            return "Playing"
```

---

## 🎮 GNOME Integration Layer

### **Enhanced GNOME Controls**

```python
class GNOMEMediaControls:
    """
    Enhanced GNOME notification and D-Bus integration
    """

    def __init__(self, progress_tracker, audio_player):
        self.progress_tracker = progress_tracker
        self.audio_player = audio_player
        self.notification_id = None
        self.update_interval = 2  # seconds

    def start_notification_updates(self):
        """Start periodic notification updates"""
        self.update_thread = threading.Thread(
            target=self._notification_update_loop,
            name="GNOMEUpdater"
        )
        self.update_thread.daemon = True
        self.update_thread.start()

    def _notification_update_loop(self):
        """Periodic notification update loop"""
        while self.audio_player.is_alive():
            progress = self.progress_tracker.get_progress_summary()
            buffer_status = self.progress_tracker.progress.get('buffer_status', {})

            self._update_notification(progress, buffer_status)
            time.sleep(self.update_interval)

    def _update_notification(self, progress, buffer_status):
        """Update GNOME notification with current progress"""
        title = "🎵 Linux Speech Tools"

        # Build progress message
        percentage = progress['percentage']
        current = progress['current_chunk']
        total = progress['total_chunks']
        buffered = progress['buffer_chunks']

        body = f"▶️ {progress['status']}\n"
        body += f"📊 {percentage:.0f}% • {current}/{total} chunks"

        if buffered > 0:
            body += f" • {buffered} buffered"

        if progress['time_remaining'] > 0:
            mins, secs = divmod(int(progress['time_remaining']), 60)
            body += f"\n⏱️ {mins}:{secs:02d} remaining"

        # Show notification with actions
        cmd = [
            'notify-send',
            '--app-name=Linux Speech Tools',
            '--icon=audio-volume-medium-symbolic',
            '--replace-id=123',  # Replace previous notification
            title,
            body,
            '--action=pause=⏸️ Pause',
            '--action=skip=⏭️ Skip',
            '--action=stop=⏹️ Stop'
        ]

        try:
            subprocess.run(cmd, capture_output=True, timeout=2)
        except Exception as e:
            logging.error(f"Notification update failed: {e}")

    def handle_notification_action(self, action):
        """Handle user actions from notifications"""
        if action == 'pause':
            self.audio_player.pause()
        elif action == 'skip':
            self.audio_player.skip_current()
        elif action == 'stop':
            self.audio_player.stop()
```

---

## 🔧 Configuration System

### **Progressive Streaming Configuration**

```python
class ProgressiveStreamingConfig:
    """
    Configuration manager for progressive streaming
    """

    def __init__(self):
        self.config = {
            # Content fetching
            'chunk_size': 200,
            'max_chars': None,
            'fetch_timeout': 30,
            'parallel_fetch': True,

            # Audio generation
            'buffer_size': 5,
            'low_water_mark': 2,
            'generation_timeout': 30,
            'tts_quality': 'medium',

            # Audio playback
            'crossfade_ms': 50,
            'gap_elimination': True,
            'playback_method': 'concat',  # or 'crossfade'
            'player_command': 'ffplay',

            # GNOME integration
            'notification_updates': True,
            'update_interval': 2,
            'progress_detail': 'full',  # or 'simple'

            # Performance
            'max_memory_mb': 100,
            'cpu_limit_percent': 50,
            'cleanup_interval': 60
        }

    def load_from_file(self, config_file):
        """Load configuration from file"""
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                user_config = json.load(f)
                self.config.update(user_config)

    def save_to_file(self, config_file):
        """Save configuration to file"""
        with open(config_file, 'w') as f:
            json.dump(self.config, f, indent=2)

    def get(self, key, default=None):
        """Get configuration value"""
        return self.config.get(key, default)

    def set(self, key, value):
        """Set configuration value"""
        self.config[key] = value
```

---

## 🚀 Main Orchestrator

### **Progressive Streaming Manager**

```python
class ProgressiveStreamingManager:
    """
    Main orchestrator for progressive streaming pipeline
    """

    def __init__(self, config_file=None):
        self.config = ProgressiveStreamingConfig()
        if config_file:
            self.config.load_from_file(config_file)

        # Initialize components
        self.content_queue = queue.Queue(maxsize=20)
        self.audio_buffer = AudioBufferManager(
            buffer_size=self.config.get('buffer_size'),
            low_water_mark=self.config.get('low_water_mark')
        )
        self.progress_tracker = ProgressTracker()

        # Initialize threads
        self.content_fetcher = None
        self.tts_generator = None
        self.audio_player = None
        self.gnome_controls = None

    def start_streaming(self, source, **kwargs):
        """Start progressive streaming pipeline"""
        try:
            # Initialize TTS configuration
            tts_config = {
                'python_path': os.path.expanduser('~/.venvs/tts/bin/python'),
                'say_read_path': os.path.join(os.path.dirname(__file__), 'say_read.py'),
                'lang': kwargs.get('lang', 'en-us'),
                'voice': kwargs.get('voice', 'af_heart')
            }

            # Start content fetcher
            self.content_fetcher = ProgressiveContentFetcher(
                source=source,
                content_queue=self.content_queue,
                chunk_size=self.config.get('chunk_size'),
                max_chars=kwargs.get('max_chars')
            )

            # Start TTS generator
            self.tts_generator = TTSAudioGenerator(
                content_queue=self.content_queue,
                audio_buffer=self.audio_buffer,
                tts_config=tts_config
            )

            # Start audio player
            player_config = {
                'method': self.config.get('playback_method'),
                'player': kwargs.get('player', 'ffplay')
            }
            self.audio_player = SeamlessAudioPlayer(
                audio_buffer=self.audio_buffer,
                gnome_controls=self.gnome_controls,
                player_config=player_config
            )

            # Start GNOME controls
            self.gnome_controls = GNOMEMediaControls(
                progress_tracker=self.progress_tracker,
                audio_player=self.audio_player
            )

            # Start all threads
            self.content_fetcher.start()
            self.tts_generator.start()
            self.audio_player.start()
            self.gnome_controls.start_notification_updates()

            print("🎵 Progressive streaming started!")
            print("⏳ First audio will begin within 3-5 seconds...")

            # Wait for completion or user interrupt
            self._wait_for_completion()

        except Exception as e:
            logging.error(f"Streaming failed: {e}")
            self.stop_streaming()
            raise

    def _wait_for_completion(self):
        """Wait for streaming to complete"""
        try:
            # Wait for content fetching to complete
            self.content_fetcher.join()

            # Wait for TTS generation to complete
            self.tts_generator.join()

            # Wait for audio playback to complete
            self.audio_player.join()

            print("✅ Progressive streaming completed successfully!")

        except KeyboardInterrupt:
            print("\n🛑 Streaming interrupted by user")
            self.stop_streaming()

    def stop_streaming(self):
        """Stop all streaming components"""
        print("🛑 Stopping progressive streaming...")

        if self.content_fetcher:
            self.content_fetcher.stop()
        if self.tts_generator:
            self.tts_generator.stop()
        if self.audio_player:
            self.audio_player.stop()

        # Clean up
        if self.tts_generator:
            self.tts_generator.cleanup()
        if self.audio_buffer:
            self.audio_buffer.clear()

    def get_stats(self):
        """Get comprehensive pipeline statistics"""
        stats = {}

        if self.content_fetcher:
            stats['content_fetcher'] = self.content_fetcher.stats
        if self.tts_generator:
            stats['tts_generator'] = self.tts_generator.stats
        if self.audio_player:
            stats['audio_player'] = self.audio_player.stats
        if self.audio_buffer:
            stats['audio_buffer'] = self.audio_buffer.stats

        return stats
```

---

## 📋 Integration Points

### **Modified say-read-gnome Script**

```bash
#!/usr/bin/env bash
# Enhanced say-read-gnome with Progressive Streaming

# Use progressive streaming manager
exec ~/.venvs/tts/bin/python -c "
import sys
sys.path.insert(0, '$(dirname "$0")')
from progressive_streaming_manager import ProgressiveStreamingManager

manager = ProgressiveStreamingManager()
manager.start_streaming('$1', $(printf "%q " "${@:2}"))
"
```

### **Installation Requirements**

```bash
# Additional dependencies for progressive streaming
pip install threading queue tempfile logging json
```

---

*This technical architecture provides the foundation for implementing the progressive streaming solution outlined in the PRD, with detailed specifications for each component and their interactions.*