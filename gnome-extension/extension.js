/* extension.js
 * Speech to Clipboard GNOME Shell Extension
 * Integrates linux-speech-tools for system-wide voice dictation.
 *
 * Ported to the modern ESM extension API (GNOME Shell 45+). Targets
 * GNOME 45-48 and 50. The MessageTray Source/Notification
 * constructors changed in GNOME 46 (positional args -> params object,
 * showNotification -> addNotification); this is feature-detected at
 * runtime so the same file works on every supported shell.
 */

import GObject from 'gi://GObject';
import St from 'gi://St';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';

import {FocusService} from './focusService.js';

// Command launched to toggle dictation. Resolved from PATH at call time via
// GLib.find_program_in_path so it works whether installed to ~/.local/bin or
// a system location. The companion `gnome-dictation` helper (also on PATH)
// handles the timed "quick" dictation actions.
const TOGGLE_CMD = 'talk2claude-faster-toggle';
const GNOME_DICTATION_CMD = 'gnome-dictation';

// How often (seconds) to poll the status file from the main loop.
const STATUS_POLL_SECONDS = 2;

// State directory matches bin/talk2claude-faster-toggle and
// bin/linux-speech-tools-dictation-hotkey: prefer XDG_RUNTIME_DIR, then
// XDG_STATE_HOME, then ~/.local/state.
function getStateDir() {
    const runtimeDir = GLib.getenv('XDG_RUNTIME_DIR');
    if (runtimeDir && runtimeDir.length > 0)
        return GLib.build_filenamev([runtimeDir, 'linux-speech-tools']);

    const stateHome = GLib.getenv('XDG_STATE_HOME');
    if (stateHome && stateHome.length > 0)
        return GLib.build_filenamev([stateHome, 'linux-speech-tools']);

    return GLib.build_filenamev([GLib.get_home_dir(), '.local', 'state', 'linux-speech-tools']);
}

// Status JSON written by talk2claude-faster / talk2claude-faster-toggle.
const STATUS_FILE = GLib.build_filenamev([getStateDir(), 'talk2claude-faster.status.json']);

const SpeechToClipboardIndicator = GObject.registerClass(
class SpeechToClipboardIndicator extends PanelMenu.Button {
    _init(notify) {
        super._init(0.0, 'Speech to Clipboard');

        // Callback (title, body) => void used to raise notifications. Supplied
        // by the owning Extension, which holds the version-aware MessageTray
        // logic. Cleared in destroy().
        this._notify = notify;

        // Panel icon.
        this._icon = new St.Icon({
            icon_name: 'audio-input-microphone-symbolic',
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);

        // Recording state.
        this._isRecording = false;

        // Cancellable shared by in-flight async file reads so disable() can
        // abort any pending I/O.
        this._cancellable = new Gio.Cancellable();

        // Polling source id; removed in destroy().
        this._statusTimeout = 0;

        this._createMenu();

        // Read initial status, then poll on the main loop. The handler
        // returns SOURCE_CONTINUE to keep the timeout alive.
        this._updateStatus();
        this._statusTimeout = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            STATUS_POLL_SECONDS,
            () => {
                this._updateStatus();
                return GLib.SOURCE_CONTINUE;
            }
        );
    }

    // A primary (left) click on the indicator toggles dictation and does NOT
    // open the menu. This must be done in vfunc_event (the class's default
    // 'event' handler) rather than a connected 'button-press-event' handler:
    // PanelMenu.Button opens the menu from its own vfunc_event, which runs as
    // part of the generic 'event' emission before any specific signal, so a
    // connected handler returning EVENT_STOP cannot suppress it. Returning
    // EVENT_STOP here stops emission before super.vfunc_event() runs the
    // default menu toggle. Secondary/middle presses fall through to super and
    // open the menu, exposing the quick-dictation options. The menu's first
    // item also toggles, satisfying the "click the indicator or a menu item"
    // behavior.
    vfunc_event(event) {
        if (this.menu &&
            event.type() === Clutter.EventType.BUTTON_PRESS &&
            event.get_button() === Clutter.BUTTON_PRIMARY) {
            this._toggleRecording();
            return Clutter.EVENT_STOP;
        }
        return super.vfunc_event(event);
    }

    _createMenu() {
        // Toggle recording item.
        this._toggleItem = new PopupMenu.PopupMenuItem('Start Recording');
        this._toggleItem.connect('activate', () => this._toggleRecording());
        this.menu.addMenuItem(this._toggleItem);

        // Quick dictation submenu.
        const quickMenu = new PopupMenu.PopupSubMenuMenuItem('Quick Dictation');
        for (const seconds of [3, 5, 10, 15]) {
            const item = new PopupMenu.PopupMenuItem(`${seconds} seconds`);
            item.connect('activate', () => this._quickDictation(seconds));
            quickMenu.menu.addMenuItem(item);
        }
        this.menu.addMenuItem(quickMenu);

        // Separator.
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Status item (non-interactive).
        this._statusItem = new PopupMenu.PopupMenuItem('Status: Checking...');
        this._statusItem.reactive = false;
        this.menu.addMenuItem(this._statusItem);
    }

    // Read the status JSON file asynchronously. Doing this on the compositor
    // thread with a blocking read (or, worse, spawn_sync) would jank the
    // shell, so we use Gio.File.load_contents_async and update the UI in the
    // callback. The file is written by talk2claude-faster / -toggle and uses
    // the "listening"/"idle" state vocabulary. A missing or unreadable file is
    // treated as idle.
    _updateStatus() {
        const file = Gio.File.new_for_path(STATUS_FILE);
        file.load_contents_async(this._cancellable, (source, result) => {
            let state = 'idle';
            try {
                const [ok, contents] = source.load_contents_finish(result);
                if (ok && contents && contents.length > 0) {
                    const text = new TextDecoder().decode(contents);
                    const data = JSON.parse(text);
                    if (data && typeof data.state === 'string')
                        state = data.state;
                }
            } catch (e) {
                // Operation cancelled during disable() -> indicator is gone,
                // bail without touching any UI.
                if (e instanceof GLib.Error &&
                    e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                    return;

                // Missing file (never started) is expected and silent.
                const notFound = e instanceof GLib.Error &&
                    e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND);
                if (!notFound)
                    console.warn(`Speech Extension status read: ${e.message}`);
                // Fall through with state === 'idle'.
            }

            this._applyState(state);
        });
    }

    _applyState(state) {
        // Guard against late callbacks after destroy().
        if (this._icon === null)
            return;

        // "listening"/"recording" means actively capturing; other non-idle,
        // non-error states (processing/finalizing) also count as in-progress.
        this._isRecording = state !== 'idle' && state !== 'error';

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
        this._spawn([TOGGLE_CMD], 'Failed to toggle recording');
    }

    _quickDictation(seconds) {
        this._spawn(
            [GNOME_DICTATION_CMD, 'quick', String(seconds)],
            'Failed to start quick dictation'
        );
    }

    // Launch a command asynchronously via Gio.Subprocess (never spawn_sync).
    // The first argv element is resolved against PATH; if it is not found we
    // surface a notification instead of silently failing.
    _spawn(argv, errorPrefix) {
        const program = GLib.find_program_in_path(argv[0]);
        if (program === null) {
            this._notify?.(
                'Speech tools not found',
                `Could not find "${argv[0]}" on your PATH. ` +
                'Install linux-speech-tools or add it to PATH.'
            );
            return;
        }

        const resolved = [program, ...argv.slice(1)];
        try {
            const proc = new Gio.Subprocess({
                argv: resolved,
                flags: Gio.SubprocessFlags.NONE,
            });
            proc.init(this._cancellable);

            // Reap the child asynchronously so it is not left as a zombie and
            // so failures are reported. We do not block on the result.
            proc.wait_check_async(this._cancellable, (subprocess, result) => {
                try {
                    subprocess.wait_check_finish(result);
                } catch (e) {
                    if (e instanceof GLib.Error &&
                        e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        return;
                    this._notify?.(errorPrefix, e.message);
                }
            });
        } catch (e) {
            this._notify?.(errorPrefix, e.message);
        }
    }

    destroy() {
        if (this._statusTimeout) {
            GLib.Source.remove(this._statusTimeout);
            this._statusTimeout = 0;
        }

        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }

        // Null references so any in-flight async callback that slips through
        // (already queued before cancel) becomes a no-op.
        this._icon = null;
        this._toggleItem = null;
        this._statusItem = null;
        this._notify = null;

        super.destroy();
    }
});

export default class SpeechToClipboardExtension extends Extension {
    enable() {
        this._notificationSource = null;
        this._focusService = null;
        this._indicator = null;

        // Expose a versioned snapshot of the focused window to the dictation
        // client. The per-enable session nonce prevents stable sequences from
        // being confused across GNOME Shell restarts.
        try {
            this._focusService = new FocusService();
        } catch (error) {
            // Dictation remains available through the existing panel controls,
            // while target-guard clients fail closed without the D-Bus service.
            console.error(`Could not start speech focus provider: ${error.message}`);
        }

        try {
            // Give the indicator a bound notifier callback; the Extension
            // keeps the version-aware MessageTray logic.
            this._indicator = new SpeechToClipboardIndicator(
                (title, body) => this._showNotification(title, body)
            );
            Main.panel.addToStatusArea(this.uuid, this._indicator);
        } catch (error) {
            // GNOME does not guarantee disable() after an enable exception.
            // Release the D-Bus name, exported object, signal handlers and any
            // partially-created UI before surfacing the load failure.
            this.disable();
            throw error;
        }

        // NOTE: This extension intentionally does NOT register its own global
        // keybinding. Main.wm.addKeybinding requires a real Gio.Settings backed
        // by a compiled GSettings schema, and no schema ships with this
        // extension. The system-wide dictation hotkey (Ctrl+Alt+V) is installed
        // separately by `gnome-dictation setup` as a GNOME custom keybinding
        // that runs talk2claude-faster-toggle. Use that hotkey, the panel
        // indicator click, or the menu items to toggle recording.
    }

    disable() {
        if (this._focusService) {
            this._focusService.destroy();
            this._focusService = null;
        }

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }

        // Tear down the notification source so it is not leaked across a
        // disable/enable cycle (required for EGO review). Destroying the
        // source removes it from the message tray.
        if (this._notificationSource) {
            this._notificationSource.destroy();
            this._notificationSource = null;
        }
    }

    // Create (and cache) a MessageTray source, then show a transient
    // notification. The Source/Notification constructors changed in GNOME 46:
    //   - 45:   new Source(title, iconName);        source.showNotification(n)
    //           new Notification(source, title, body)
    //   - 46+:  new Source({title, iconName});      source.addNotification(n)
    //           new Notification({source, title, body, isTransient})
    // We feature-detect via MessageTray.getSystemSource, which only exists on
    // 46+, rather than parsing the shell version string.
    _showNotification(title, body) {
        const modern = typeof MessageTray.getSystemSource === 'function';
        const iconName = 'audio-input-microphone-symbolic';

        if (!this._notificationSource) {
            if (modern) {
                this._notificationSource = new MessageTray.Source({
                    title: 'Speech to Clipboard',
                    iconName,
                });
            } else {
                this._notificationSource = new MessageTray.Source(
                    'Speech to Clipboard', iconName);
            }

            // Drop our cached reference if the shell destroys the source
            // (e.g. user clears notifications) so we recreate it next time.
            this._notificationSource.connect('destroy', () => {
                this._notificationSource = null;
            });

            Main.messageTray.add(this._notificationSource);
        }

        let notification;
        if (modern) {
            notification = new MessageTray.Notification({
                source: this._notificationSource,
                title,
                body,
                isTransient: true,
            });
            this._notificationSource.addNotification(notification);
        } else {
            notification = new MessageTray.Notification(
                this._notificationSource, title, body);
            notification.setTransient(true);
            this._notificationSource.showNotification(notification);
        }
    }
}
