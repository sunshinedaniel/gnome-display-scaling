import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';

import {buildConfiguration, describeState, percent, sameScale} from './display-config.mjs';

const BUS_NAME = 'org.gnome.Mutter.DisplayConfig';
const OBJECT_PATH = '/org/gnome/Mutter/DisplayConfig';
const ICON = 'video-display-symbolic';

export default class ScaleToggleExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._cancellable = new Gio.Cancellable();
        this._busy = false;
        this._state = null;
        this._refreshId = 0;
        this._indicator = new QuickSettings.SystemIndicator();
        this._toggle = new QuickSettings.QuickMenuToggle({
            title: 'Display Scaling',
            subtitle: 'Reading display...',
            iconName: ICON,
            toggleMode: false,
            menuButtonAccessibleName: 'Choose display scale',
        });
        this._section = new PopupMenu.PopupMenuSection();
        this._toggle.menu.addMenuItem(this._section);
        this._toggle.menu.setHeader(ICON, 'Display Scaling', 'Off: 100%');
        this._toggle.connect('clicked', () => {
            if (!this._state || this._busy)
                return;
            const scale = this._toggle.checked
                ? 1 : this._settings.get_double('preferred-scale');
            this._applyScale(scale);
        });
        this._indicator.quickSettingsItems.push(this._toggle);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);

        this._subscription = Gio.DBus.session.signal_subscribe(
            BUS_NAME, BUS_NAME, 'MonitorsChanged', OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE, () => {
                if (!this._busy)
                    this._refresh();
            });
        this._refresh();
    }

    disable() {
        this._cancellable?.cancel();
        this._cancellable = null;
        if (this._subscription) {
            Gio.DBus.session.signal_unsubscribe(this._subscription);
            this._subscription = 0;
        }
        this._toggle?.menu.destroy();
        this._toggle?.destroy();
        this._indicator?.destroy();
        this._toggle = null;
        this._indicator = null;
        this._section = null;
        this._settings = null;
        this._state = null;
    }

    _active(cancellable) {
        return this._cancellable === cancellable && !cancellable.is_cancelled();
    }

    _call(method, parameters, cancellable) {
        return new Promise((resolve, reject) => {
            Gio.DBus.session.call(
                BUS_NAME, OBJECT_PATH, BUS_NAME, method, parameters, null,
                Gio.DBusCallFlags.NONE, 10000, cancellable,
                (connection, result) => {
                    try {
                        resolve(connection.call_finish(result));
                    } catch (error) {
                        reject(error);
                    }
                });
        });
    }

    async _readState(cancellable) {
        const result = await this._call('GetCurrentState', null, cancellable);
        return describeState(result.recursiveUnpack());
    }

    async _refresh() {
        const cancellable = this._cancellable;
        const refreshId = ++this._refreshId;
        try {
            const state = await this._readState(cancellable);
            if (!this._active(cancellable) || refreshId !== this._refreshId || this._busy)
                return;
            this._state = state;
            if (!sameScale(state.scale, 1))
                this._settings.set_double('preferred-scale', state.scale);
            this._render();
        } catch (error) {
            if (!this._active(cancellable) || refreshId !== this._refreshId)
                return;
            this._state = null;
            this._toggle.subtitle = 'Display unavailable';
            this._section.removeAll();
            const item = new PopupMenu.PopupMenuItem(error.message, {reactive: false});
            this._section.addMenuItem(item);
            console.error(`[${this.uuid}] Cannot read display configuration: ${error.message}`);
            Main.notifyError('Display Scaling', error.message);
        }
    }

    _render() {
        const {scale, scales} = this._state;
        const preferred = this._settings.get_double('preferred-scale');
        this._toggle.checked = !sameScale(scale, 1);
        this._toggle.subtitle = this._busy ? 'Applying...' : percent(scale);
        this._toggle.menu.setHeader(
            ICON, 'Display Scaling', `Off: 100%  |  On: ${percent(preferred)}`);
        this._section.removeAll();
        for (const candidate of scales.filter(value => !sameScale(value, 1))) {
            const item = new PopupMenu.PopupMenuItem(percent(candidate));
            item.setOrnament(sameScale(candidate, preferred)
                ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            item.setSensitive(!this._busy && scales.some(value => sameScale(value, 1)));
            item.connect('activate', () => {
                if (this._busy)
                    return;
                if (this._toggle.checked) {
                    this._applyScale(candidate);
                } else {
                    this._settings.set_double('preferred-scale', candidate);
                    this._render();
                }
            });
            this._section.addMenuItem(item);
        }
    }

    async _applyScale(scale) {
        if (this._busy || !this._cancellable)
            return;
        const cancellable = this._cancellable;
        this._busy = true;
        ++this._refreshId;
        this._render();
        try {
            const state = await this._readState(cancellable);
            if (!this._active(cancellable))
                return;
            const configuration = buildConfiguration(state, scale);
            if (!state.scales.some(candidate => sameScale(candidate, 1)))
                throw new Error('This display mode does not support the required 100% off state.');

            const logicalMonitors = configuration.logicalMonitors.map(logical => [
                ...logical.slice(0, 5),
                logical[5].map(([connector, mode, properties]) => {
                    const options = {};
                    if ('underscanning' in properties)
                        options.underscanning = new GLib.Variant('b', properties.underscanning);
                    if ('color-mode' in properties)
                        options['color-mode'] = new GLib.Variant('u', properties['color-mode']);
                    return [connector, mode, options];
                }),
            ]);
            const properties = {};
            if ('layout-mode' in configuration.properties)
                properties['layout-mode'] = new GLib.Variant('u', configuration.properties['layout-mode']);
            if ('monitors-for-lease' in configuration.properties)
                properties['monitors-for-lease'] = new GLib.Variant('a(ssss)', configuration.properties['monitors-for-lease']);

            // Temporary application avoids GNOME's confirmation dialog on every click.
            await this._call('ApplyMonitorsConfig',
                new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [
                    configuration.serial, 1, logicalMonitors, properties,
                ]), cancellable);
            if (!this._active(cancellable))
                return;
            const applied = await this._readState(cancellable);
            if (!this._active(cancellable))
                return;
            if (!sameScale(applied.scale, scale))
                throw new Error(`The display reports ${percent(applied.scale)} instead of ${percent(scale)}.`);
            this._state = applied;
            if (!sameScale(scale, 1))
                this._settings.set_double('preferred-scale', scale);
        } catch (error) {
            if (!this._active(cancellable))
                return;
            console.error(`[${this.uuid}] Cannot apply display scale: ${error.message}`);
            Main.notifyError('Could not change display scaling', error.message);
        } finally {
            if (this._active(cancellable)) {
                this._busy = false;
                this._render();
                this._refresh();
            }
        }
    }
}
