# 🎮 GNOME Reader Controls Beta

Adds GNOME notification controls for document reading. This is a beta D-Bus and
`notify-send` integration, not full MPRIS/native media-player support.

## 🎯 Overview

This feature adds notification-based controls to Linux Speech Tools, providing:

- **📱 Notification-based controls** - Play, pause, and stop from your desktop
- **📊 Real-time progress tracking** - See reading progress as it happens
- **🎵 Reader integration** - Works with the maintained `say-read` path
- **📖 Document information** - Display source title and reading status
- **🎮 Beta desktop controls** - Depends on notification action support

## 🚀 What's New

### **Before: Terminal-only Control**
- ❌ No way to control reading once started
- ❌ Must return to terminal to stop
- ❌ No progress visibility
- ❌ Poor experience for long documents

### **After: Notification Control Experience**
- ✅ **Desktop media controls** in notification panel
- ✅ **Play/pause/stop** buttons always accessible
- ✅ **Progress tracking** with document info
- ✅ **GNOME notification integration** for extended reading sessions

## 📦 Components

### **Core Components**
- `say-read-gnome` - Enhanced reader with GNOME media controls
- `gnome-reader-control.py` - D-Bus service for media control
- `gnome-notification-handler.sh` - Notification action handler
- `demo-gnome-media-integration.sh` - Interactive demo and setup

### **Integration Points**
- **D-Bus Service**: `org.gnome.SpeechTools.Reader`
- **GNOME Notifications**: Action buttons for media control
- **Desktop Integration**: Native notification panel experience
- **State Management**: Persistent reading session tracking

## 🎮 Usage

### **🚀 Quick Start**

```bash
# Enhanced reading with GNOME media controls
./say-read-gnome https://example.com/article

# Notification controls for long content
./say-read-gnome --max-chars 5000 https://en.wikipedia.org/wiki/Linux

# Setup GNOME integration (first time)
./say-read-gnome --setup
```

### **🎵 Media Controls**

While reading, use the **notification panel**:

| Control | Action | Description |
|---------|--------|-------------|
| **⏸️ Pause** | Click in notification | Pause audio playback |
| **▶️ Resume** | Click in notification | Resume paused reading |
| **⏹️ Stop** | Click in notification | Stop reading completely |

Notification buttons require a desktop notification server that supports
`notify-send --wait --action`. The command-line controls below use the same
D-Bus methods and are the fallback when a shell does not expose action buttons.

### **📋 Command Line Options**

```bash
# Basic usage
./say-read-gnome <URL|FILE>

# Language and voice options
./say-read-gnome --lang es --voice ef_dora document.pdf

# Content control
./say-read-gnome --max-chars 3000 --chunk 200 article.txt

# Integration management
./say-read-gnome --setup           # Setup GNOME integration
./say-read-gnome --check-gnome     # Check integration status
```

### **🔧 Manual Controls**

```bash
# Command line media control (while reading)
./gnome-notification-handler.sh pause    # Pause reading
./gnome-notification-handler.sh resume   # Resume reading
./gnome-notification-handler.sh stop     # Stop reading
./gnome-notification-handler.sh status   # Check status
```

## 🧪 Demo & Testing

### **🎬 Interactive Demo**

```bash
# Run complete demo with examples
./demo-gnome-media-integration.sh

# Quick setup only
./demo-gnome-media-integration.sh setup

# Check GNOME environment
./demo-gnome-media-integration.sh check
```

### **🔍 Status Check**

```bash
# Comprehensive status check
./say-read-gnome --check-gnome

# Output example:
# ✅ GNOME desktop environment detected
# ✅ D-Bus available
# ✅ Notification system available
# ⚠️ GNOME Reader Control service not running (will start when needed)
# 🎉 GNOME integration is ready!
```

## 🛠️ Technical Details

### **Architecture**

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   say-read-     │───▶│ gnome-reader-    │───▶│ GNOME           │
│   gnome         │    │ control.py       │    │ Notifications   │
│                 │    │ (D-Bus Service)  │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
        │                        │                        │
        │                        ▼                        │
        │               ┌──────────────────┐              │
        └──────────────▶│ Continuous       │              │
                        │ Streaming        │              │
                        │ (say-read-       │              │
                        │  continuous)     │              │
                        └──────────────────┘              │
                                                          │
┌─────────────────┐    ┌──────────────────┐              │
│ User clicks     │◀───│ gnome-           │◀─────────────┘
│ notification    │    │ notification-    │
│ button          │    │ handler.sh       │
└─────────────────┘    └──────────────────┘
```

### **D-Bus Interface**

**Service**: `org.gnome.SpeechTools.Reader`
**Object Path**: `/org/gnome/SpeechTools/Reader`

**Methods**:
- `start_reading(source, title, total_chunks)` - Start reading session
- `pause_reading()` - Pause current session
- `resume_reading()` - Resume paused session
- `stop_reading()` - Stop current session
- `update_progress(current_chunk)` - Update reading progress

### **Notification Features**

- **Persistent notifications** during reading sessions
- **Action buttons** for media control
- **Progress display** with percentage and chunk info
- **Document information** (title, URL, or filename)
- **Status indicators** (Playing ▶️, Paused ⏸️)

## 📊 Progress Tracking

### **Information Displayed**

```
▶️ Wikipedia: Linux
Playing • 45% complete (23/51)
[⏸️ Pause] [⏹️ Stop]
```

**Components**:
- **Status emoji**: ▶️ Playing, ⏸️ Paused
- **Document title**: Extracted from URL/filename
- **Progress percentage**: Current completion
- **Chunk progress**: Current chunk / total chunks
- **Action buttons**: Context-appropriate controls

### **Automatic Updates**

- Progress updates **every 5 chunks** to avoid notification spam
- **Real-time status** changes when pausing/resuming
- **Completion notification** when reading finishes
- **Error handling** with appropriate user feedback

## 🎨 Use Cases

### **📚 Extended Reading Sessions**
```bash
# Read long Wikipedia articles with full control
./say-read-gnome https://en.wikipedia.org/wiki/Artificial_intelligence

# While reading, you can:
# - Pause to take notes (⏸️ button)
# - Resume when ready (▶️ button)
# - Stop if interrupted (⏹️ button)
```

### **📖 Document Reading**
```bash
# Read PDFs with progress tracking
./say-read-gnome --max-chars 8000 research-paper.pdf

# Notification shows:
# - Document filename
# - Reading progress
# - Media controls
```

### **🌐 News & Articles**
```bash
# Read news articles with professional controls
./say-read-gnome https://www.bbc.com/news/technology

# Perfect for:
# - Morning news routines
# - Background reading while working
# - Accessibility reading sessions
```

### **🎓 Educational Content**
```bash
# Educational material with study controls
./say-read-gnome --lang en-us learning-material.txt

# Great for:
# - Study sessions (pause/resume for notes)
# - Language learning
# - Accessibility support
```

## ⚙️ Setup & Configuration

### **🔧 First-Time Setup**

```bash
# 1. Setup GNOME integration
./say-read-gnome --setup

# 2. Verify integration
./say-read-gnome --check-gnome

# 3. Test with sample content
./demo-gnome-media-integration.sh
```

### **📋 Requirements**

**System Requirements**:
- GNOME desktop environment
- D-Bus support
- `notify-send` (libnotify-bin)
- `dbus-send` (dbus-x11)
- `python3-dbus` and `python3-gi`

**Speech Tools Requirements**:
- Continuous streaming feature
- say-read-continuous
- Python 3.8+ with GI/DBus support

### **🔍 Troubleshooting**

**Service not starting**:
```bash
# Check Python dependencies
python3 -c "import dbus, gi.repository.GLib"

# Start service manually
python3 gnome-reader-control.py --daemon
```

**Notifications not showing**:
```bash
# Test notification system
notify-send "Test" "Notification system check"

# Check GNOME environment
echo $XDG_CURRENT_DESKTOP
```

**D-Bus connection issues**:
```bash
# Check D-Bus session
dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames
```

## 🚀 Integration with Existing Commands

### **Backward Compatibility**

All existing functionality is **fully preserved**:

```bash
# Original commands still work
say-read-continuous https://example.com
say-read document.pdf

# Enhanced with GNOME controls
say-read-gnome https://example.com     # Same functionality + media controls
```

### **Migration Guide**

```bash
# Before: Basic continuous reading
./say-read-continuous --max-chars 5000 article.txt

# After: Same functionality + GNOME media controls
./say-read-gnome --max-chars 5000 article.txt

# Result: Identical audio experience + desktop integration
```

## 🎯 Benefits

### **👤 User Experience**
- **Professional media controls** like native music players
- **No need to return to terminal** for control
- **Visual progress tracking** for long content
- **Seamless desktop integration**

### **🎵 Audio Experience**
- **Same high-quality** continuous streaming
- **Enhanced control** over playback
- **Better workflow** for extended reading
- **Professional presentation** for demonstrations

### **💻 Productivity**
- **Multitasking support** - control reading while working
- **Accessibility enhancement** - better support for screen readers
- **Professional presentations** - clean, native appearance
- **Study workflows** - pause for notes, resume when ready

## 🔄 Future Enhancements

### **Planned Features**
- **Global keyboard shortcuts** for media control
- **MPRIS integration** for media keys support
- **Reading speed control** via notifications
- **Bookmark/chapter navigation** for long documents
- **Reading history** and resume functionality

### **Advanced Integration**
- **GNOME Shell extension** for system tray control
- **Files integration** for right-click reading
- **Web browser extension** for seamless article reading
- **Workspace awareness** for automatic pause/resume

---

**Transform your Linux Speech Tools into a professional desktop media experience!** 🎵📱✨

Experience reading like never before with native GNOME controls that make long-form content consumption effortless and enjoyable.
