# 🎵 Continuous Audio Streaming for Linux Speech Tools

Eliminates gaps between audio chunks for smooth, professional TTS playback.

## 🎯 Problem Solved

### **Before: Chunked Audio with Gaps**
The original `say-read` streaming mode played each text chunk separately:
- ✋ **Noticeable gaps** between audio segments
- ⚠️ **Choppy playback** that interrupts listening flow
- 🔧 **Process overhead** from starting/stopping audio player for each chunk
- 📱 **Unprofessional quality** for long-form content

### **After: Continuous Streaming**
Our enhanced streaming technology provides:
- ✅ **Seamless audio flow** with no gaps between chunks
- 🎵 **Professional quality** comparable to commercial TTS
- ⚡ **Improved performance** with one long-lived player process
- 🎧 **Better listening experience** for URLs, PDFs, and documents

## 🚀 Features

### **🔄 Streaming Modes**

1. **Continuous Streaming** (Default)
   - Streams raw PCM to one `ffplay` process when available
   - Eliminates all gaps between segments

2. **Original Mode** (Fallback)
   - Traditional chunk-by-chunk playback
   - Available for compatibility

### **🛠️ Smart Technology**

- **Single Player Process**: Avoids per-chunk player startup gaps
- **Format Handling**: Streams 24 kHz PCM to `ffplay` when possible
- **Player Detection**: Auto-selects best available audio player
- **Error Handling**: Graceful fallbacks to original mode
- **Memory Efficient**: Streaming without excessive buffering

## 📦 Components

### **Core Scripts**
- `say-read` - Primary reader; uses `--stream-fast` through the installed wrapper
- `say-read-continuous` - Compatibility wrapper that delegates to `say-read`
- `examples/demos/demo-audio-streaming.sh` - Interactive demonstration

### **Advanced Tools**
- Historical architecture notes describe older buffered prototypes, but the
  maintained implementation is now `src/tts/say_read.py` plus the `say-read`
  wrapper.

## 🎮 Usage

### **🎯 Quick Start**

```bash
# Enhanced streaming through the primary reader
say-read https://example.com/article

# Compatibility wrapper
say-read-continuous https://www.bbc.com/news/technology
```

### **📋 Command Options**

```bash
# Basic usage
say-read <URL|FILE>

# Streaming modes
say-read --stream-fast <URL>      # Single ffplay process for low latency
say-read --stream <URL>           # Piece-by-piece streaming fallback

# Save to file (no streaming)
say-read -o output.mp3 <URL>

# Language and voice options
say-read -l es -v ef_dora <URL>  # Spanish with Dora voice
```

### **🔧 Integration**

```bash
# Test the difference between old and new
./examples/demos/demo-audio-streaming.sh

# Check system compatibility
linux-speech-tools-setup --check --kokoro

# Fall back to original if needed
say-read --stream <URL>
```

## 🧪 Technical Details

### **Audio Processing Pipeline**

1. **Text Extraction**: URLs, PDFs, documents processed as before
2. **Text Chunking**: Split into optimal synthesis chunks (320 chars default)
3. **Audio Generation**: Each chunk synthesized with Kokoro TTS
4. **Continuous Playback**: Audio is written to one `ffplay` stdin stream
5. **Fallback Playback**: If fast streaming is unavailable, use regular
   player auto-detection

### **Streaming Strategies**

#### **Continuous Mode**
```python
# Start one ffplay process
player = start_ffplay_stdin()

# Synthesize and write each chunk to that process
for piece in text_pieces:
    audio = synthesize(piece)
    player.stdin.write(to_pcm(audio))
```

### **Performance Optimizations**

- **Memory Management**: Temporary files cleaned automatically
- **Process Efficiency**: Single audio player process
- **Format Optimization**: Native WAV handling for speed
- **Fallbacks**: Regular stream and file output remain available

## 🔧 Dependencies

### **Required**
- `python3` - Core runtime
- `ffplay` or `mpv` - Audio playback
- Existing speech-tools setup (Kokoro TTS, etc.)

### **Optional**
- `mpv`, `paplay`, or `aplay` - Playback fallback when `ffplay` is unavailable

### **Installation Check**
```bash
# Verify all dependencies
linux-speech-tools-setup --check --kokoro

# Test basic functionality
say-read --help
```

## 🎨 Use Cases

### **📚 Long-Form Reading**
- **Articles & Blogs**: Smooth narration without interruptions
- **Documentation**: Professional presentation of technical content
- **Books & Papers**: Natural reading flow for extended listening

### **🎓 Educational Content**
- **Online Courses**: Seamless audio for learning materials
- **Research Papers**: Professional narration of academic content
- **News Articles**: Broadcast-quality news reading

### **💼 Professional Applications**
- **Content Creation**: High-quality audio for videos/podcasts
- **Accessibility**: Smooth screen reading for vision-impaired users
- **Presentations**: Professional TTS for demonstrations

## 🚦 Migration Guide

### **From Original say-read**

```bash
# Before (chunky audio)
say-read --stream https://example.com

# After (smooth audio)
say-read https://example.com
```

### **Backward Compatibility**

- **All original options** preserved and supported
- **Drop-in replacement** for existing scripts
- **Fallback mode** if continuous streaming fails
- **Same output formats** and file handling

## 🎯 Performance Comparison

| Mode | Audio Quality | Processing Speed | Memory Usage | User Experience |
|------|---------------|------------------|--------------|-----------------|
| **Original Streaming** | ⚠️ Choppy with gaps | Fast | Low | Poor for long content |
| **Continuous Mode** | ✅ Professional smooth | Medium | Medium | Excellent |
| **Buffered Mode** | ✅ Maximum smoothness | Medium-Slow | Higher | Best for complex content |

## 🛠️ Troubleshooting

### **Common Issues**

**No audio player found:**
```bash
# Install ffmpeg
sudo apt install ffmpeg

# Or install mpv
sudo apt install mpv
```

**Concatenation fails:**
```bash
# Install audio tools
sudo apt install ffmpeg sox

# Test functionality
linux-speech-tools-setup --check --kokoro
say-read --help
```

**Memory issues with large content:**
```bash
# Use smaller chunks
say-read -c 200 <URL>

# Or limit content size
say-read --max-chars 5000 <URL>
```

### **Debug Mode**

```bash
# Enable verbose logging
say-read --debug <URL>

# Test individual components
say-read --help
./examples/demos/demo-audio-streaming.sh
```

## 🚀 Future Enhancements

- **Real-time streaming** with live audio generation
- **Multiple language mixing** in single stream
- **Voice cloning integration** for personalized narration
- **Streaming server mode** for web applications
- **Mobile app integration** via audio streaming API

---

**Transform your Linux speech tools into a professional TTS system with broadcast-quality continuous audio!** 🎤✨
