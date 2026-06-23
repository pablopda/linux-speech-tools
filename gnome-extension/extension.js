/* extension.js
 * Speech to Clipboard GNOME Shell Extension
 * Integrates linux-speech-tools for system-wide voice dictation
 */

const { GObject, St, Gio, GLib } = imports.gi;
const Main = imports.ui.main;
const PanelMenu = imports.ui.panelMenu;
const PopupMenu = imports.ui.popupMenu;
const MessageTray = imports.ui.messageTray;

// Path to speech tools (update this to match your installation)
const SPEECH_TOOLS_PATH = GLib.get_home_dir() + '/.local/bin';
const GNOME_DICTATION_CMD = SPEECH_TOOLS_PATH + '/gnome-dictation';

// State directory matches bin/talk2claude-faster-toggle and
// bin/linux-speech-tools-dictation-hotkey: prefer XDG_RUNTIME_DIR, then
// XDG_STATE_HOME, then ~/.local/state.
function getStateDir() {
    let runtimeDir = GLib.getenv('XDG_RUNTIME_DIR');
    if (runtimeDir && runtimeDir.length > 0) {
        return runtimeDir + '/linux-speech-tools';
    }
    let stateHome = GLib.getenv('XDG_STATE_HOME');
    if (stateHome && stateHome.length > 0) {
        return stateHome + '/linux-speech-tools';
    }
    return GLib.get_home_dir() + '/.local/state/linux-speech-tools';
}

// Status JSON written by talk2claude-faster / talk2claude-faster-toggle.
const STATUS_FILE = getStateDir() + '/talk2claude-faster.status.json';

var SpeechToClipboardIndicator = GObject.registerClass(
class SpeechToClipboardIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'Speech to Clipboard');

        // Panel icon
        this._icon = new St.Icon({
            icon_name: 'audio-input-microphone-symbolic',
            style_class: 'system-status-icon'
        });
        this.add_child(this._icon);

        // Recording state
        this._isRecording = false;
        this._statusProc = null;

        // Create menu items
        this._createMenu();

        // Check initial status
        this._updateStatus();

        // Update status every 2 seconds
        this._statusTimeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 2000, () => {
            this._updateStatus();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _createMenu() {
        // Toggle recording item
        this._toggleItem = new PopupMenu.PopupMenuItem('Start Recording');
        this._toggleItem.connect('activate', () => this._toggleRecording());
        this.menu.addMenuItem(this._toggleItem);

        // Quick dictation submenu
        let quickMenu = new PopupMenu.PopupSubMenuMenuItem('Quick Dictation');

        [3, 5, 10, 15].forEach(seconds => {
            let item = new PopupMenu.PopupMenuItem(`${seconds} seconds`);
            item.connect('activate', () => this._quickDictation(seconds));
            quickMenu.menu.addMenuItem(item);
        });

        this.menu.addMenuItem(quickMenu);

        // Separator
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Status item
        this._statusItem = new PopupMenu.PopupMenuItem('Status: Checking...');
        this._statusItem.reactive = false;
        this.menu.addMenuItem(this._statusItem);
    }

    _updateStatus() {
        // Read the status JSON file directly instead of spawning a subprocess
        // on the compositor thread every 2s (GLib.spawn_sync would block GNOME
        // Shell). The file is written by talk2claude-faster / -toggle and uses
        // the "listening"/"idle" state vocabulary.
        let state = 'idle';
        try {
            let file = Gio.File.new_for_path(STATUS_FILE);
            let [ok, contents] = file.load_contents(null);
            if (ok) {
                let text = new TextDecoder().decode(contents);
                let data = JSON.parse(text);
                if (data && typeof data.state === 'string') {
                    state = data.state;
                }
            }
        } catch (e) {
            // Missing file (never started) or unreadable -> treat as idle.
            // Only surface genuinely unexpected errors to the log.
            if (!(e instanceof GLib.Error) || !e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND)) {
                log('Speech Extension status read: ' + e.message);
            }
        }

        // "listening" means actively capturing; other non-idle states
        // (processing/finalizing) also count as an in-progress session.
        this._isRecording = (state !== 'idle' && state !== 'error');

        if (this._isRecording) {
            this._icon.icon_name = 'audio-input-microphone-high-symbolic';
            this._icon.add_style_class_name('recording');
            this._toggleItem.label.text = 'Stop & Transcribe';
            this._statusItem.label.text = 'Status: Recording...';
        } else {
            this._icon.icon_name = 'audio-input-microphone-symbolic';
            this._icon.remove_style_class_name('recording');
            this._toggleItem.label.text = 'Start Recording';
            this._statusItem.label.text = 'Status: Ready';
        }
    }

    _toggleRecording() {
        try {
            GLib.spawn_async(
                null,
                [GNOME_DICTATION_CMD, 'toggle'],
                null,
                GLib.SpawnFlags.SEARCH_PATH,
                null
            );

            // Update status after a short delay
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
                this._updateStatus();
                return GLib.SOURCE_REMOVE;
            });
        } catch (e) {
            this._showNotification('Error', 'Failed to toggle recording: ' + e.message);
        }
    }

    _quickDictation(seconds) {
        try {
            GLib.spawn_async(
                null,
                [GNOME_DICTATION_CMD, 'quick', seconds.toString()],
                null,
                GLib.SpawnFlags.SEARCH_PATH,
                null
            );
        } catch (e) {
            this._showNotification('Error', 'Failed to start quick dictation: ' + e.message);
        }
    }

    _showNotification(title, message) {
        // Legacy (GNOME 42-44) MessageTray API. The Source/Notification
        // constructor signatures changed in GNOME 46 (Source now takes a params
        // object; Notification likewise), so this code is only valid on the
        // 42-44 shell versions advertised in metadata.json. An ESM/46+ port
        // would need to update these calls.
        let source = new MessageTray.Source('Speech to Clipboard', 'audio-input-microphone-symbolic');
        Main.messageTray.add(source);

        let notification = new MessageTray.Notification(source, title, message);
        notification.setTransient(true);
        source.showNotification(notification);
    }

    destroy() {
        if (this._statusTimeout) {
            GLib.Source.remove(this._statusTimeout);
            this._statusTimeout = null;
        }
        super.destroy();
    }
});

class Extension {
    constructor() {
        this._indicator = null;
    }

    enable() {
        log('Enabling Speech to Clipboard extension');
        this._indicator = new SpeechToClipboardIndicator();
        Main.panel.addToStatusArea('speech-to-clipboard', this._indicator);

        // NOTE: This extension intentionally does NOT register its own global
        // keybinding. Main.wm.addKeybinding requires a real Gio.Settings backed
        // by a compiled GSettings schema, and no schema ships with this
        // extension. The system-wide dictation hotkey (Ctrl+Alt+V) is installed
        // separately by `gnome-dictation setup` as a GNOME custom keybinding
        // that runs talk2claude-faster-toggle. Use that hotkey, or the panel
        // menu items above, to toggle recording.
    }

    disable() {
        log('Disabling Speech to Clipboard extension');

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}