/* Isolated GNOME Shell runtime smoke test for the focus provider.
 *
 * Run only through gnome-shell-test-tool in a private dbus-run-session and
 * temporary XDG environment. It never enables or disables the user's live
 * extension installation.
 */

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {ExtensionState} from 'resource:///org/gnome/shell/misc/extensionUtils.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export var METRICS = {};

const UUID = 'speech-to-clipboard@linux-speech-tools';
const BUS_NAME = 'org.linux_speech_tools.Focus';
const OBJECT_PATH = '/org/linux_speech_tools/Focus';

function callOn(
    connection,
    busName,
    objectPath,
    interfaceName,
    methodName,
    parameters = null
) {
    return new Promise((resolve, reject) => {
        connection.call(
            busName,
            objectPath,
            interfaceName,
            methodName,
            parameters,
            null,
            Gio.DBusCallFlags.NONE,
            3000,
            null,
            (connection, result) => {
                try {
                    resolve(connection.call_finish(result).deepUnpack());
                } catch (error) {
                    reject(error);
                }
            }
        );
    });
}

function call(busName, objectPath, interfaceName, methodName, parameters = null) {
    return callOn(
        Gio.DBus.session,
        busName,
        objectPath,
        interfaceName,
        methodName,
        parameters
    );
}

function privateSessionConnection() {
    return new Promise((resolve, reject) => {
        Gio.DBusConnection.new_for_address(
            GLib.getenv('DBUS_SESSION_BUS_ADDRESS'),
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            null,
            null,
            (_source, result) => {
                try {
                    resolve(Gio.DBusConnection.new_for_address_finish(result));
                } catch (error) {
                    reject(error);
                }
            }
        );
    });
}

function closeConnection(connection) {
    return new Promise((resolve, reject) => {
        connection.close(null, (source, result) => {
            try {
                source.close_finish(result);
                resolve();
            } catch (error) {
                reject(error);
            }
        });
    });
}

async function waitForExtensionState(predicate, description) {
    for (let attempt = 0; attempt < 50; attempt++) {
        const extension = Main.extensionManager.lookup(UUID);
        if (extension && predicate(extension))
            return extension;
        await Scripting.sleep(100);
    }
    throw new Error(`Timed out waiting for extension to be ${description}`);
}

async function focusPayload() {
    const [json] = await call(
        BUS_NAME,
        OBJECT_PATH,
        BUS_NAME,
        'GetFocus'
    );
    return JSON.parse(json);
}

async function nameOwner(name) {
    const [owner] = await call(
        'org.freedesktop.DBus',
        '/org/freedesktop/DBus',
        'org.freedesktop.DBus',
        'GetNameOwner',
        new GLib.Variant('(s)', [name])
    );
    return owner;
}

async function assertFocusNameReleased() {
    const [names] = await call(
        'org.freedesktop.DBus',
        '/org/freedesktop/DBus',
        'org.freedesktop.DBus',
        'ListNames'
    );
    if (names.includes(BUS_NAME))
        throw new Error('Focus provider retained its D-Bus name after disable');
}

/** @returns {Promise<void>} */
export async function run() {
    const extension = await waitForExtensionState(
        candidate => candidate.state === ExtensionState.ACTIVE,
        'active'
    );
    if (extension.error)
        throw new Error(`Extension load error: ${extension.error}`);

    const payload = await focusPayload();
    if (payload.schema_version !== 1)
        throw new Error('Focus provider returned the wrong schema version');
    if (typeof payload.shell_session_id !== 'string' ||
        payload.shell_session_id.length === 0)
        throw new Error('Focus provider omitted its shell-session identity');
    if (!Number.isSafeInteger(payload.focus_generation) ||
        payload.focus_generation < 0)
        throw new Error('Focus provider returned an invalid generation');
    if (!Number.isSafeInteger(payload.window_sequence) ||
        payload.window_sequence < 0)
        throw new Error('Focus provider returned an invalid window sequence');
    if (typeof payload.locked !== 'boolean')
        throw new Error('Focus provider returned an invalid lock state');
    if (await nameOwner(BUS_NAME) !== await nameOwner('org.gnome.Shell'))
        throw new Error('Focus provider is not owned by the nested GNOME Shell');

    // No application is launched in this isolated smoke run. If Mutter has no
    // focused window, the provider must not disclose stale metadata.
    if (payload.window_sequence === 0 &&
        (payload.window_id || payload.app_id || payload.wm_class ||
         payload.title || payload.pid !== null))
        throw new Error('No-focus payload disclosed window metadata');

    if (!Main.extensionManager.disableExtension(UUID))
        throw new Error('Could not request extension disable');
    await waitForExtensionState(
        candidate => candidate.state === ExtensionState.INACTIVE,
        'inactive'
    );
    await Scripting.sleep(100);
    await assertFocusNameReleased();

    // Hold the focus name from a separate bus connection. The extension's
    // DO_NOT_QUEUE request must fail without becoming a latent owner.
    const rival = await privateSessionConnection();
    const [requestReply] = await callOn(
        rival,
        'org.freedesktop.DBus',
        '/org/freedesktop/DBus',
        'org.freedesktop.DBus',
        'RequestName',
        new GLib.Variant(
            '(su)', [BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE])
    );
    if (requestReply !== 1)
        throw new Error('Could not establish the competing focus-name owner');

    if (!Main.extensionManager.enableExtension(UUID))
        throw new Error('Could not enable extension during name conflict');
    await waitForExtensionState(
        candidate => candidate.state === ExtensionState.ACTIVE,
        'active during name conflict'
    );
    await Scripting.sleep(100);
    if (await nameOwner(BUS_NAME) !== rival.get_unique_name())
        throw new Error('Focus provider displaced the competing name owner');

    if (!Main.extensionManager.disableExtension(UUID))
        throw new Error('Could not disable extension during name conflict');
    await waitForExtensionState(
        candidate => candidate.state === ExtensionState.INACTIVE,
        'inactive after name conflict'
    );
    await closeConnection(rival);
    await Scripting.sleep(100);
    await assertFocusNameReleased();

    // If the failed ownership request survived destroy(), it would acquire the
    // name when the rival disconnects. A clean re-enable must instead create a
    // new provider owned by GNOME Shell.
    if (!Main.extensionManager.enableExtension(UUID))
        throw new Error('Could not request clean extension re-enable');
    await waitForExtensionState(
        candidate => candidate.state === ExtensionState.ACTIVE,
        'active after clean re-enable'
    );
    const nextPayload = await focusPayload();
    if (nextPayload.shell_session_id === payload.shell_session_id)
        throw new Error('Extension re-enable reused the old shell-session identity');
    if (await nameOwner(BUS_NAME) !== await nameOwner('org.gnome.Shell'))
        throw new Error('Cleanly re-enabled provider is not owned by GNOME Shell');

    if (!Main.extensionManager.disableExtension(UUID))
        throw new Error('Could not request final extension disable');
    await waitForExtensionState(
        candidate => candidate.state === ExtensionState.INACTIVE,
        'inactive after final disable'
    );
    await Scripting.sleep(100);
    await assertFocusNameReleased();
    console.log('GNOME focus provider smoke passed');
}

export function finish() {}
