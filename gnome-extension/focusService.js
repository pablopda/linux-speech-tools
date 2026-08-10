/* SPDX-License-Identifier: MIT
 *
 * A small, read-only focus provider for linux-speech-tools. This module was
 * independently authored for this project and does not contain code copied
 * from another dictation project.
 */

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS_NAME = 'org.linux_speech_tools.Focus';
const OBJECT_PATH = '/org/linux_speech_tools/Focus';
const SCHEMA_VERSION = 1;

const FOCUS_INTERFACE = `
<node>
  <interface name="${BUS_NAME}">
    <method name="GetFocus">
      <arg name="focus_json" type="s" direction="out"/>
    </method>
  </interface>
</node>`;

function boundedString(value, limit) {
    if (typeof value !== 'string')
        return '';
    return value.slice(0, limit);
}

export class FocusService {
    constructor() {
        this._shellSessionId = GLib.uuid_string_random();
        this._focusGeneration = 0;
        this._lastLocked = Boolean(Main.sessionMode.isLocked);
        this._focusSignalId = 0;
        this._sessionSignalId = 0;
        this._nameOwnerId = 0;
        this._nameAcquired = false;
        this._destroyed = false;
        this._dbusObject = null;

        try {
            this._dbusObject = Gio.DBusExportedObject.wrapJSObject(
                FOCUS_INTERFACE, this);
            this._dbusObject.export(Gio.DBus.session, OBJECT_PATH);
            this._nameOwnerId = Gio.DBus.session.own_name(
                BUS_NAME,
                Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                () => this._onNameAcquired(),
                () => this._onNameLost()
            );
            this._focusSignalId = global.display.connect(
                'notify::focus-window', () => this._onFocusChanged());
            this._sessionSignalId = Main.sessionMode.connect(
                'updated', () => this._onSessionUpdated());
        } catch (error) {
            this.destroy();
            throw error;
        }
    }

    _onFocusChanged() {
        this._focusGeneration += 1;
    }

    _onSessionUpdated() {
        const locked = Boolean(Main.sessionMode.isLocked);
        if (locked !== this._lastLocked) {
            // A lock/unlock boundary invalidates a captured target even if
            // Mutter later reports the same window as focused.
            this._focusGeneration += 1;
            this._lastLocked = locked;
        }
    }

    _onNameAcquired() {
        if (this._destroyed)
            return;
        this._nameAcquired = true;
    }

    _onNameLost() {
        if (this._destroyed)
            return;

        // Keep _nameOwnerId until destroy(): it is the subscription handle,
        // not an "is acquired" flag. Clearing it here would prevent
        // unown_name() from cancelling a failed/lost ownership request.
        this._nameAcquired = false;
        console.error(`Speech focus provider lost D-Bus name ${BUS_NAME}`);
    }

    GetFocus() {
        const locked = Boolean(Main.sessionMode.isLocked);
        const payload = {
            schema_version: SCHEMA_VERSION,
            shell_session_id: this._shellSessionId,
            window_sequence: 0,
            focus_generation: this._focusGeneration,
            locked,
            app_id: '',
            wm_class: '',
            title: '',
            pid: null,
            window_id: '',
        };

        // Do not disclose active-window metadata while the session is locked.
        const window = locked ? null : global.display.focus_window;
        if (!window)
            return JSON.stringify(payload);

        const sequence = Number(window.get_stable_sequence());
        if (!Number.isSafeInteger(sequence) || sequence <= 0)
            return JSON.stringify(payload);

        const tracker = Shell.WindowTracker.get_default();
        const app = tracker.get_window_app(window);
        payload.window_sequence = sequence;
        payload.window_id = `gnome:${this._shellSessionId}:${sequence}`;
        payload.app_id = boundedString(app?.get_id() ?? '', 512);
        payload.wm_class = boundedString(window.get_wm_class() ?? '', 512);
        payload.title = boundedString(window.get_title() ?? '', 4096);

        const pid = Number(window.get_pid());
        if (Number.isSafeInteger(pid) && pid > 0)
            payload.pid = pid;

        return JSON.stringify(payload);
    }

    destroy() {
        if (this._destroyed)
            return;
        this._destroyed = true;

        if (this._focusSignalId) {
            global.display.disconnect(this._focusSignalId);
            this._focusSignalId = 0;
        }
        if (this._sessionSignalId) {
            Main.sessionMode.disconnect(this._sessionSignalId);
            this._sessionSignalId = 0;
        }
        if (this._nameOwnerId) {
            Gio.DBus.session.unown_name(this._nameOwnerId);
            this._nameOwnerId = 0;
        }
        this._nameAcquired = false;
        if (this._dbusObject) {
            this._dbusObject.unexport();
            this._dbusObject.run_dispose();
            this._dbusObject = null;
        }
    }
}
