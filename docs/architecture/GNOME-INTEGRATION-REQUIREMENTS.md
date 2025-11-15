# 🎮 GNOME Media Controls Integration Requirements
## Detailed Specification for Native Desktop Experience

**Version**: 1.0
**Date**: 2024-11-14
**Related**: PRD-PROGRESSIVE-STREAMING.md, ARCHITECTURE-PROGRESSIVE-STREAMING.md

---

## 🎯 Integration Overview

Transform Linux Speech Tools into a **native GNOME application** that provides the same level of integration and user experience as built-in media players like Rhythmbox, GNOME Music, or Video Player.

### **Core Integration Goals**
- 🎵 **Native Media Experience**: Indistinguishable from GNOME's built-in media players
- 📱 **Notification Integration**: Rich, interactive notifications with progress and controls
- 🔌 **D-Bus Integration**: Proper service registration and inter-process communication
- 🎮 **Media Key Support**: Global keyboard shortcuts and media keys
- 📊 **Progress Visualization**: Real-time progress with buffer status indicators

---

## 🔌 D-Bus Service Specification

### **Service Registration**
```xml
<!-- D-Bus Service Definition -->
<interface name="org.gnome.SpeechTools.MediaPlayer">
  <method name="Play"/>
  <method name="Pause"/>
  <method name="Stop"/>
  <method name="Next"/>
  <method name="Previous"/>
  <method name="Seek">
    <arg direction="in" type="x" name="Position"/>
  </method>

  <property name="PlaybackStatus" type="s" access="read"/>
  <property name="Position" type="x" access="read"/>
  <property name="Duration" type="x" access="read"/>
  <property name="Metadata" type="a{sv}" access="read"/>
  <property name="BufferStatus" type="a{sv}" access="read"/>

  <signal name="PlaybackStatusChanged">
    <arg type="s" name="Status"/>
  </signal>
  <signal name="PositionChanged">
    <arg type="x" name="Position"/>
  </signal>
</interface>
```

### **D-Bus Service Implementation**
```python
class GNOMESpeechToolsMediaService:
    """
    D-Bus service for GNOME media integration
    """

    # D-Bus service details
    BUS_NAME = "org.gnome.SpeechTools.MediaPlayer"
    OBJECT_PATH = "/org/gnome/SpeechTools/MediaPlayer"
    INTERFACE_NAME = "org.gnome.SpeechTools.MediaPlayer"

    def __init__(self, progressive_streaming_manager):
        self.streaming_manager = progressive_streaming_manager
        self.session_bus = dbus.SessionBus()
        self.dbus_service = dbus.service.Object(
            dbus.service.BusName(self.BUS_NAME, self.session_bus),
            self.OBJECT_PATH
        )

    @dbus.service.method(INTERFACE_NAME)
    def Play(self):
        """Resume or start playback"""
        self.streaming_manager.resume()
        self._emit_playback_status_changed("Playing")

    @dbus.service.method(INTERFACE_NAME)
    def Pause(self):
        """Pause current playback"""
        self.streaming_manager.pause()
        self._emit_playback_status_changed("Paused")

    @dbus.service.method(INTERFACE_NAME)
    def Stop(self):
        """Stop current playback"""
        self.streaming_manager.stop()
        self._emit_playback_status_changed("Stopped")

    @dbus.service.method(INTERFACE_NAME, in_signature='x')
    def Seek(self, position):
        """Seek to specific position (in microseconds)"""
        self.streaming_manager.seek(position)

    @dbus.service.property(INTERFACE_NAME, signature='s')
    def PlaybackStatus(self):
        """Current playback status"""
        return self.streaming_manager.get_playback_status()

    @dbus.service.property(INTERFACE_NAME, signature='x')
    def Position(self):
        """Current playback position in microseconds"""
        return self.streaming_manager.get_position()

    @dbus.service.property(INTERFACE_NAME, signature='x')
    def Duration(self):
        """Total duration in microseconds"""
        return self.streaming_manager.get_duration()

    @dbus.service.property(INTERFACE_NAME, signature='a{sv}')
    def Metadata(self):
        """Current media metadata"""
        return {
            'mpris:artUrl': dbus.String('file:///usr/share/icons/hicolor/symbolic/apps/audio-volume-medium-symbolic.svg'),
            'xesam:title': dbus.String(self.streaming_manager.get_title()),
            'xesam:artist': dbus.Array(['Linux Speech Tools'], signature='s'),
            'xesam:album': dbus.String('Text-to-Speech'),
            'mpris:length': dbus.Int64(self.streaming_manager.get_duration())
        }

    @dbus.service.property(INTERFACE_NAME, signature='a{sv}')
    def BufferStatus(self):
        """Buffer status information"""
        buffer_info = self.streaming_manager.get_buffer_status()
        return {
            'bufferSize': dbus.Int32(buffer_info['size']),
            'maxBuffer': dbus.Int32(buffer_info['max_size']),
            'bufferHealth': dbus.String(buffer_info['health']),
            'chunksAhead': dbus.Int32(buffer_info['chunks_ahead'])
        }
```

---

## 📱 Advanced Notification System

### **Notification Layout Specification**

```
┌─────────────────────────────────────────────────────────────┐
│ 🎵 Linux Speech Tools                    [X] [🔽] [🎮]      │
├─────────────────────────────────────────────────────────────┤
│ ▶️ The Attention Economy is Inverting                        │
│ 📄 sundaylettersfromsam.substack.com                         │
├─────────────────────────────────────────────────────────────┤
│ ████████████████▒▒▒▒▒▒▒▒▒▒  67% Complete                    │
│ 📊 Chunk 21/32 • ⚡ 4 buffered • ⏱️ 2:34 remaining          │
├─────────────────────────────────────────────────────────────┤
│ [⏸️ Pause] [⏭️ Next] [⏮️ Prev] [⚡ Speed] [⏹️ Stop]          │
└─────────────────────────────────────────────────────────────┘
```

### **Notification Implementation**
```python
class AdvancedNotificationManager:
    """
    Rich GNOME notification management
    """

    def __init__(self, dbus_service, progress_tracker):
        self.dbus_service = dbus_service
        self.progress_tracker = progress_tracker
        self.notification_id = None
        self.update_frequency = 2.0  # seconds

    def create_rich_notification(self, progress_data):
        """Create rich notification with all media information"""

        # Build progress bar
        progress_bar = self._create_progress_bar(progress_data['percentage'])

        # Build status line
        status_line = self._create_status_line(progress_data)

        # Build notification body
        body = f"{progress_data['status_icon']} {progress_data['title']}\n"
        body += f"📄 {progress_data['source']}\n"
        body += f"{progress_bar} {progress_data['percentage']:.0f}% Complete\n"
        body += f"{status_line}"

        # Create notification with actions
        notification_cmd = [
            'notify-send',
            '--app-name=Linux Speech Tools',
            '--icon=audio-volume-medium-symbolic',
            f'--replace-id={self.notification_id or 0}',
            '--category=media',
            '--urgency=low',
            '--hint=int:transient:0',  # Keep notification persistent
            '🎵 Linux Speech Tools',
            body,
            # Media control actions
            '--action=pause=⏸️ Pause',
            '--action=next=⏭️ Next',
            '--action=previous=⏮️ Previous',
            '--action=speed=⚡ Speed',
            '--action=stop=⏹️ Stop'
        ]

        try:
            result = subprocess.run(
                notification_cmd,
                capture_output=True,
                text=True,
                timeout=3
            )

            # Store notification ID for replacement
            if result.returncode == 0 and result.stdout:
                self.notification_id = result.stdout.strip()

        except Exception as e:
            logging.error(f"Notification failed: {e}")

    def _create_progress_bar(self, percentage):
        """Create visual progress bar"""
        bar_length = 20
        filled = int(bar_length * percentage / 100)
        empty = bar_length - filled
        return '█' * filled + '▒' * empty

    def _create_status_line(self, progress_data):
        """Create detailed status information line"""
        status_parts = []

        # Chunk progress
        if progress_data.get('current_chunk') and progress_data.get('total_chunks'):
            status_parts.append(f"📊 Chunk {progress_data['current_chunk']}/{progress_data['total_chunks']}")

        # Buffer status
        if progress_data.get('buffer_chunks', 0) > 0:
            status_parts.append(f"⚡ {progress_data['buffer_chunks']} buffered")

        # Time remaining
        if progress_data.get('time_remaining', 0) > 0:
            mins, secs = divmod(int(progress_data['time_remaining']), 60)
            status_parts.append(f"⏱️ {mins}:{secs:02d} remaining")

        return ' • '.join(status_parts)

    def handle_notification_action(self, action):
        """Handle user actions from notification buttons"""
        action_handlers = {
            'pause': self._handle_pause_action,
            'next': self._handle_next_action,
            'previous': self._handle_previous_action,
            'speed': self._handle_speed_action,
            'stop': self._handle_stop_action
        }

        handler = action_handlers.get(action)
        if handler:
            handler()
        else:
            logging.warning(f"Unknown notification action: {action}")

    def _handle_pause_action(self):
        """Handle pause/resume toggle"""
        current_status = self.dbus_service.PlaybackStatus()
        if current_status == "Playing":
            self.dbus_service.Pause()
        else:
            self.dbus_service.Play()

    def _handle_next_action(self):
        """Skip to next logical section/chunk"""
        self.dbus_service.Next()

    def _handle_previous_action(self):
        """Skip to previous logical section/chunk"""
        self.dbus_service.Previous()

    def _handle_speed_action(self):
        """Cycle through playback speeds"""
        current_speed = self.progress_tracker.get_playback_speed()
        speed_cycle = [0.75, 1.0, 1.25, 1.5, 2.0]

        try:
            current_index = speed_cycle.index(current_speed)
            next_index = (current_index + 1) % len(speed_cycle)
            new_speed = speed_cycle[next_index]
        except ValueError:
            new_speed = 1.0

        self.dbus_service.SetPlaybackSpeed(new_speed)

    def _handle_stop_action(self):
        """Stop playback completely"""
        self.dbus_service.Stop()
```

---

## 🎮 Media Key Integration

### **Global Media Key Support**
```python
class MediaKeyHandler:
    """
    Handle global media keys (Play/Pause, Next, Previous, etc.)
    """

    def __init__(self, dbus_service):
        self.dbus_service = dbus_service
        self.session_bus = dbus.SessionBus()

    def register_media_keys(self):
        """Register for global media key events"""
        try:
            # Connect to GNOME Settings Daemon media keys
            media_keys_proxy = self.session_bus.get_object(
                'org.gnome.SettingsDaemon.MediaKeys',
                '/org/gnome/SettingsDaemon/MediaKeys'
            )

            # Register application for media key events
            media_keys_proxy.GrabMediaPlayerKeys(
                'org.gnome.SpeechTools.MediaPlayer',
                0,  # Current time
                dbus_interface='org.gnome.SettingsDaemon.MediaKeys'
            )

            # Connect signal handler
            media_keys_proxy.connect_to_signal(
                'MediaPlayerKeyPressed',
                self._handle_media_key,
                dbus_interface='org.gnome.SettingsDaemon.MediaKeys'
            )

        except Exception as e:
            logging.error(f"Failed to register media keys: {e}")

    def _handle_media_key(self, application, key):
        """Handle media key press events"""
        if application != 'org.gnome.SpeechTools.MediaPlayer':
            return

        key_handlers = {
            'Play': self.dbus_service.Play,
            'Pause': self.dbus_service.Pause,
            'Stop': self.dbus_service.Stop,
            'Next': self.dbus_service.Next,
            'Previous': self.dbus_service.Previous
        }

        handler = key_handlers.get(key)
        if handler:
            handler()
            logging.info(f"Handled media key: {key}")
```

---

## 📊 Progress Visualization System

### **Real-Time Progress Updates**
```python
class ProgressVisualizationSystem:
    """
    Advanced progress tracking and visualization
    """

    def __init__(self, notification_manager, dbus_service):
        self.notification_manager = notification_manager
        self.dbus_service = dbus_service
        self.progress_data = {
            'percentage': 0,
            'current_chunk': 0,
            'total_chunks': 0,
            'buffer_chunks': 0,
            'time_remaining': 0,
            'status_icon': '⏸️',
            'status_text': 'Initializing',
            'title': 'Loading...',
            'source': '',
            'playback_speed': 1.0
        }

    def update_progress(self, **kwargs):
        """Update progress data and refresh notifications"""
        self.progress_data.update(kwargs)

        # Update status icon based on current state
        self._update_status_icon()

        # Emit D-Bus signals
        self._emit_dbus_updates()

        # Update notification
        self.notification_manager.create_rich_notification(self.progress_data)

    def _update_status_icon(self):
        """Update status icon based on current playback state"""
        playback_status = self.dbus_service.PlaybackStatus()

        status_icons = {
            'Playing': '▶️',
            'Paused': '⏸️',
            'Stopped': '⏹️',
            'Loading': '⏳',
            'Buffering': '⚡',
            'Error': '❌'
        }

        self.progress_data['status_icon'] = status_icons.get(playback_status, '❓')

    def _emit_dbus_updates(self):
        """Emit D-Bus property change signals"""
        # Emit position change
        self.dbus_service.PositionChanged(self.dbus_service.Position())

        # Emit playback status if changed
        current_status = self.dbus_service.PlaybackStatus()
        if current_status != self.progress_data.get('last_status'):
            self.dbus_service.PlaybackStatusChanged(current_status)
            self.progress_data['last_status'] = current_status

    def start_periodic_updates(self, update_interval=2.0):
        """Start periodic progress updates"""
        def update_loop():
            while self.dbus_service.is_active():
                # Gather current progress data
                progress_info = self._gather_progress_info()

                # Update progress
                self.update_progress(**progress_info)

                # Wait for next update
                time.sleep(update_interval)

        update_thread = threading.Thread(target=update_loop, daemon=True)
        update_thread.start()

    def _gather_progress_info(self):
        """Gather current progress information from all sources"""
        # This would gather data from the streaming manager,
        # audio buffer, content fetcher, etc.
        return {
            'percentage': self._calculate_percentage(),
            'current_chunk': self._get_current_chunk(),
            'total_chunks': self._get_total_chunks(),
            'buffer_chunks': self._get_buffer_chunks(),
            'time_remaining': self._estimate_time_remaining()
        }
```

---

## 🔧 Configuration & Settings Integration

### **GNOME Settings Schema**
```xml
<!-- GNOME Settings Schema -->
<schemalist>
  <schema path="/org/gnome/speech-tools/" id="org.gnome.speech-tools">
    <key name="enable-media-keys" type="b">
      <default>true</default>
      <summary>Enable global media key support</summary>
    </key>

    <key name="notification-detail-level" type="s">
      <choices>
        <choice value="minimal"/>
        <choice value="standard"/>
        <choice value="detailed"/>
      </choices>
      <default>'standard'</default>
      <summary>Level of detail in notifications</summary>
    </key>

    <key name="auto-pause-on-notification" type="b">
      <default>false</default>
      <summary>Auto-pause when important notifications appear</summary>
    </key>

    <key name="buffer-size-chunks" type="i">
      <range min="2" max="10"/>
      <default>5</default>
      <summary>Audio buffer size in chunks</summary>
    </key>
  </schema>
</schemalist>
```

### **Settings Integration**
```python
class GNOMESettingsIntegration:
    """
    Integration with GNOME settings system
    """

    def __init__(self):
        self.settings = Gio.Settings.new('org.gnome.speech-tools')
        self.settings.connect('changed', self._on_setting_changed)

    def _on_setting_changed(self, settings, key):
        """Handle setting changes"""
        setting_handlers = {
            'enable-media-keys': self._handle_media_keys_setting,
            'notification-detail-level': self._handle_notification_detail_setting,
            'auto-pause-on-notification': self._handle_auto_pause_setting,
            'buffer-size-chunks': self._handle_buffer_size_setting
        }

        handler = setting_handlers.get(key)
        if handler:
            handler(settings.get_value(key))

    def get_setting(self, key):
        """Get current setting value"""
        return self.settings.get_value(key).unpack()

    def set_setting(self, key, value):
        """Set setting value"""
        self.settings.set_value(key, GLib.Variant.new_string(value))
```

---

## 🎨 Visual Design Requirements

### **Icon Design**
```
Application Icon: audio-volume-medium-symbolic (GNOME standard)
Notification Icon: Same as application icon
Progress Icon: Dynamic based on state
  - ⏳ Loading/Initializing
  - ▶️ Playing
  - ⏸️ Paused
  - ⏹️ Stopped
  - ⚡ Buffering
  - ❌ Error state
```

### **Color Scheme**
```css
/* GNOME Adwaita Color Palette */
Primary: #3584e4    /* GNOME Blue */
Success: #26a269    /* GNOME Green */
Warning: #f5c211    /* GNOME Yellow */
Error: #e01b24      /* GNOME Red */
Text: #2e3436       /* GNOME Dark Gray */
Background: #ffffff /* GNOME Light */
```

### **Typography**
```
Primary Font: Cantarell (GNOME default)
Title: Cantarell Bold 11pt
Body: Cantarell Regular 9pt
Status: Cantarell Regular 8pt
```

---

## 🔍 Testing Requirements

### **Integration Test Suite**
```python
class GNOMEIntegrationTestSuite:
    """
    Comprehensive GNOME integration testing
    """

    def test_dbus_service_registration(self):
        """Test D-Bus service registers correctly"""

    def test_notification_display(self):
        """Test notification creation and display"""

    def test_media_key_handling(self):
        """Test global media key response"""

    def test_progress_updates(self):
        """Test real-time progress tracking"""

    def test_user_interaction(self):
        """Test notification button responses"""

    def test_settings_integration(self):
        """Test GNOME settings integration"""

    def test_cross_desktop_compatibility(self):
        """Test behavior on non-GNOME desktops"""
```

### **User Acceptance Testing Scenarios**
1. **Startup Experience**: User launches reading, sees immediate feedback
2. **Control Responsiveness**: User clicks pause, audio stops within 100ms
3. **Progress Accuracy**: Progress bar matches actual reading position ±2%
4. **Error Recovery**: Network failure doesn't crash the application
5. **Professional Feel**: Experience matches GNOME media applications

---

## 📋 Compliance & Standards

### **FreeDesktop.org Standards**
- ✅ **Desktop Entry**: Proper .desktop file with correct metadata
- ✅ **Icon Theme**: Uses standard icon theme structure
- ✅ **D-Bus**: Follows D-Bus service naming conventions
- ✅ **Notifications**: Compliant with notification specification

### **GNOME HIG (Human Interface Guidelines)**
- ✅ **Visual Design**: Follows GNOME visual design principles
- ✅ **Interaction**: Standard GNOME interaction patterns
- ✅ **Accessibility**: Supports screen readers and keyboard navigation
- ✅ **Internationalization**: Supports translation and localization

### **Accessibility Requirements**
- ✅ **Screen Reader**: Compatible with Orca screen reader
- ✅ **Keyboard Navigation**: Full keyboard control support
- ✅ **High Contrast**: Works with high contrast themes
- ✅ **Large Text**: Scales with system font size settings

---

## 🚀 Deployment Requirements

### **Package Integration**
```bash
# Required package files
/usr/bin/say-read-gnome-progressive
/usr/share/applications/org.gnome.SpeechTools.desktop
/usr/share/icons/hicolor/scalable/apps/org.gnome.SpeechTools.svg
/usr/share/glib-2.0/schemas/org.gnome.speech-tools.gschema.xml
/usr/share/dbus-1/services/org.gnome.SpeechTools.MediaPlayer.service
```

### **Installation Scripts**
```bash
#!/bin/bash
# Post-installation setup
glib-compile-schemas /usr/share/glib-2.0/schemas/
gtk-update-icon-cache -f -t /usr/share/icons/hicolor/
update-desktop-database /usr/share/applications/
```

---

*This specification provides comprehensive requirements for creating a native GNOME media experience that rivals commercial media applications while maintaining the open-source accessibility that makes Linux Speech Tools unique.*