/* extension.js
 * Speech to Clipboard GNOME Shell Extension
 * Integrates linux-speech-tools for system-wide voice dictation
 */

const { GObject, St, Clutter, Gio, GLib, Meta, Shell } = imports.gi;
const Main = imports.ui.main;
const PanelMenu = imports.ui.panelMenu;
const PopupMenu = imports.ui.popupMenu;
const MessageTray = imports.ui.messageTray;

// Path to speech tools (update this to match your installation)
const SPEECH_TOOLS_PATH = GLib.get_home_dir() + '/.local/bin';
const TALK2CLAUDE_CMD = SPEECH_TOOLS_PATH + '/talk2claude';
const GNOME_DICTATION_CMD = SPEECH_TOOLS_PATH + '/gnome-dictation';

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
        try {
            let [success, output] = GLib.spawn_sync(
                null,
                [TALK2CLAUDE_CMD, 'status'],
                null,
                GLib.SpawnFlags.SEARCH_PATH,
                null
            );

            if (success) {
                let statusText = new TextDecoder().decode(output).trim();
                this._isRecording = statusText.includes('recording');

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
        } catch (e) {
            this._statusItem.label.text = 'Status: Error - Check installation';
            log('Speech Extension: ' + e.message);
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

        // Add global keybinding
        Main.wm.addKeybinding(
            'toggle-speech-recording',
            this._getSettings(),
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            () => {
                if (this._indicator) {
                    this._indicator._toggleRecording();
                }
            }
        );
    }

    disable() {
        log('Disabling Speech to Clipboard extension');

        // Remove keybinding
        Main.wm.removeKeybinding('toggle-speech-recording');

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }

    _getSettings() {
        // Return dummy settings for simplicity
        // In a real extension, you'd use Gio.Settings
        return {
            get_strv: () => ['<Super><Shift>space']
        };
    }
}