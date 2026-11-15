import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';

const XML = '<node><interface name="org.jarvis.DesktopControl"><method name="Call"><arg type="s" direction="in"/><arg type="s" direction="out"/></method></interface></node>';

export default class DesktopControls extends Extension {
    enable() {
        this._jobs = new Map();
        this._keyboard = null;
        this._notificationIds = new WeakMap();
        this._export = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._export.export(Gio.DBus.session, '/org/jarvis/DesktopControl');
        this._owner = Gio.bus_own_name_on_connection(Gio.DBus.session,
            'org.jarvis.DesktopControl', Gio.BusNameOwnerFlags.NONE, null, null);
    }

    _windows() {
        return global.get_window_actors().map(actor => actor.meta_window)
            .filter(w => !w.is_override_redirect() && !w.is_skip_taskbar());
    }

    _info(w) {
        const r = w.get_frame_rect();
        return {id: w.get_stable_sequence(), title: w.get_title() || '',
            app: w.get_gtk_application_id() || w.get_wm_class() || '',
            workspace: (w.get_workspace()?.index() ?? -1) + 1,
            monitor: w.get_monitor() + 1, x: r.x, y: r.y,
            width: r.width, height: r.height, minimized: w.minimized};
    }

    _number(value, low, high) {
        if (!Number.isInteger(value) || value < low || value > high)
            throw new Error(`Value must be an integer from ${low} to ${high}.`);
        return value;
    }

    Call(raw) {
        try {
            if (raw.length > 20000) throw new Error('Request too large.');
            if (Main.sessionMode.isLocked ||
                (Main.sessionMode.currentMode !== 'user' && Main.sessionMode.parentMode !== 'user'))
                throw new Error('Desktop controls are unavailable while locked or outside the user session.');
            const args = JSON.parse(raw);
            return JSON.stringify({ok: true, ...this._call(args)});
        } catch (error) {
            return JSON.stringify({ok: false, error: error.message});
        }
    }

    _call(args) {
        if (args.action === 'notifications' || args.action === 'dismiss_notifications') {
            const available=[];
            for (const source of Main.messageTray.getSources()) {
                for (const notification of source.notifications) {
                    if (!this._notificationIds.has(notification)) this._notificationIds.set(notification, GLib.uuid_string_random());
                    available.push({notification, info:{id:this._notificationIds.get(notification),
                        app:source.title || '',title:notification.title || '',body:notification.body || '',
                        visible:Main.messageTray._notification===notification}});
                }
            }
            if (args.action==='notifications') return {notifications:available.map(n=>n.info)};
            if (!Array.isArray(args.ids) || args.ids.length>200) throw new Error('Choose up to 200 notifications.');
            const selected=available.filter(n=>args.ids.includes(n.info.id));
            for (const item of selected) item.notification.destroy(MessageTray.NotificationDestroyedReason.DISMISSED);
            return {dismissed:selected.length};
        }
        if (args.action === 'list') return {windows: this._windows().map(w => this._info(w))};
        if (args.action === 'workspaces') return {count: global.workspace_manager.n_workspaces,
            current: global.workspace_manager.get_active_workspace_index() + 1};
        if (args.action === 'monitors') return {monitors: Main.layoutManager.monitors.map((m, i) =>
            ({id: i + 1, x: m.x, y: m.y, width: m.width, height: m.height}))};
        if (args.action === 'arrange') {
            const ids = args.windows || [];
            if (!Array.isArray(ids) || ids.length > 16) throw new Error('Arrange up to 16 windows.');
            const active = global.workspace_manager.get_active_workspace_index();
            const available = this._windows();
            const windows = ids.length ? ids.map(id => {
                const selected = available.find(w => w.get_stable_sequence() === Number(id));
                if (!selected) throw new Error('A selected window has closed.');
                return selected;
            }) : available.filter(w => w.get_workspace()?.index() === active && !w.minimized && !/J\.A\.R\.V\.I\.S\./i.test(w.get_title() || ''));
            if (!windows.length) throw new Error('No windows to arrange on the current workspace.');
            if (windows.length > 16 || new Set(windows).size !== windows.length) throw new Error('Choose up to 16 different windows.');
            if (!['grid', 'columns', 'rows'].includes(args.layout || 'grid')) throw new Error('Unknown layout.');
            const area = Main.layoutManager.getWorkAreaForMonitor(windows[0].get_monitor());
            const columns = args.layout === 'rows' ? 1 : args.layout === 'columns' ? windows.length : Math.ceil(Math.sqrt(windows.length));
            const rows = Math.ceil(windows.length / columns);
            if (area.width / columns < 100 || area.height / rows < 80) throw new Error('Too many windows for this screen layout.');
            windows.forEach((w, index) => {
                const column = index % columns, row = Math.floor(index / columns);
                const x = area.x + Math.floor(column * area.width / columns);
                const y = area.y + Math.floor(row * area.height / rows);
                const right = area.x + Math.floor((column + 1) * area.width / columns);
                const bottom = area.y + Math.floor((row + 1) * area.height / rows);
                w.unmaximize(Meta.MaximizeFlags.BOTH); w.unminimize();
                w.change_workspace_by_index(active, false);
                if (w.get_monitor() !== windows[0].get_monitor()) w.move_to_monitor(windows[0].get_monitor());
                w.move_resize_frame(true, x, y, right - x, bottom - y);
            });
            return {windows: windows.map(w => this._info(w)), note: 'Layout requested on the current workspace; app size constraints may apply.'};
        }
        if (args.action === 'typing_status' || args.action === 'cancel_typing') {
            const job = this._jobs.get(args.job);
            if (!job) throw new Error('Unknown typing operation.');
            if (args.action === 'cancel_typing' && job.state === 'typing') {
                GLib.source_remove(job.source); job.source = 0;
                job.state = 'cancelled'; job.message = 'Typing cancelled; some text may already have been entered.';
            }
            return {state: job.state, message: job.message};
        }
        const w = this._windows().find(window => window.get_stable_sequence() === Number(args.window));
        if (!w) throw new Error('The selected window has closed.');
        const area = Main.layoutManager.getWorkAreaForMonitor(w.get_monitor());
        const r = w.get_frame_rect();
        switch (args.action) {
        case 'focus': w.unminimize(); w.activate(global.get_current_time()); break;
        case 'minimize': w.minimize(); break;
        case 'maximize': w.maximize(Meta.MaximizeFlags.BOTH); break;
        case 'restore': w.unmaximize(Meta.MaximizeFlags.BOTH); w.unminimize(); break;
        case 'close': w.delete(global.get_current_time()); return {requested: 'close'};
        case 'workspace':
            w.change_workspace_by_index(this._number(args.workspace, 1, global.workspace_manager.n_workspaces) - 1, false);
            break;
        case 'monitor':
            w.move_to_monitor(this._number(args.monitor, 1, Main.layoutManager.monitors.length) - 1);
            break;
        case 'left': case 'right': {
            w.unmaximize(Meta.MaximizeFlags.BOTH);
            const width = Math.floor(area.width / 2);
            w.move_resize_frame(true, area.x + (args.action === 'right' ? width : 0), area.y, width, area.height);
            break;
        }
        case 'top': case 'bottom': case 'center':
        case 'top-left': case 'top-right': case 'bottom-left': case 'bottom-right': {
            w.unmaximize(Meta.MaximizeFlags.BOTH); w.unminimize();
            const corner = args.action.includes('-');
            const width = corner ? Math.floor(area.width / 2) : args.action === 'center' ? Math.min(r.width, area.width) : area.width;
            const height = args.action === 'center' ? Math.min(r.height, area.height) : Math.floor(area.height / 2);
            const x = args.action === 'center' ? area.x + Math.floor((area.width - width) / 2) : area.x + (args.action.endsWith('right') ? area.width - width : 0);
            const y = args.action === 'center' ? area.y + Math.floor((area.height - height) / 2) : area.y + (args.action.startsWith('bottom') ? area.height - height : 0);
            w.move_resize_frame(true, x, y, width, height);
            break;
        }
        case 'move': case 'resize': case 'geometry': {
            const width = args.action === 'move' ? r.width : this._number(args.width, 100, area.width);
            const height = args.action === 'move' ? r.height : this._number(args.height, 80, area.height);
            const x = args.action === 'resize' ? r.x : this._number(args.x, area.x, area.x + area.width - 50);
            const y = args.action === 'resize' ? r.y : this._number(args.y, area.y, area.y + area.height - 40);
            w.unmaximize(Meta.MaximizeFlags.BOTH);
            w.move_resize_frame(true, Math.min(x, area.x + Math.max(0, area.width - width)),
                Math.min(y, area.y + Math.max(0, area.height - height)), width, height);
            break;
        }
        case 'type': return this._type(w, args.text);
        default: throw new Error('Unsupported desktop operation.');
        }
        return {window: this._info(w), note: 'Window manager accepted the request; app size constraints may apply.'};
    }

    _type(w, text) {
        if (typeof text !== 'string' || !text.length || text.length > 2000 || /[\x00-\x1f\x7f]/.test(text))
            throw new Error('Only printable literal text is accepted. No control keys or automatic submission.');
        if ([...this._jobs.values()].some(job => job.state === 'typing'))
            throw new Error('Another typing operation is active.');
        if (!this._keyboard)
            this._keyboard = Clutter.get_default_backend().get_default_seat()
                .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        // Finished jobs contain no text; keep the status table bounded.
        for (const [id, job] of this._jobs) if (job.state !== 'typing') this._jobs.delete(id);
        const id = GLib.uuid_string_random();
        const job = {state: 'typing', source: 0, message: ''};
        this._jobs.set(id, job);
        w.unminimize(); w.activate(global.get_current_time());
        const chars = [...text];
        let index = 0;
        const start = GLib.get_monotonic_time();
        job.source = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 10, () => {
            if (GLib.get_monotonic_time() - start < 250000) return GLib.SOURCE_CONTINUE;
            if (Main.sessionMode.isLocked || global.display.focus_window !== w || !this._windows().includes(w)) {
                job.state = 'cancelled'; job.source = 0;
                job.message = 'Typing stopped because the screen locked, the window closed, or focus changed.';
                return GLib.SOURCE_REMOVE;
            }
            try {
                const keyval = Clutter.unicode_to_keysym(chars[index++].codePointAt(0));
                const now = Clutter.get_current_event_time() * 1000;
                this._keyboard.notify_keyval(now, keyval, Clutter.KeyState.PRESSED);
                this._keyboard.notify_keyval(now, keyval, Clutter.KeyState.RELEASED);
            } catch (_) {
                job.state = 'cancelled'; job.source = 0; job.message = 'The desktop could not deliver this character.';
                return GLib.SOURCE_REMOVE;
            }
            if (index === chars.length) {
                job.state = 'completed'; job.source = 0;
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
        return {job: id};
    }

    disable() {
        for (const job of this._jobs?.values() ?? []) if (job.source) GLib.source_remove(job.source);
        this._jobs?.clear();
        this._keyboard = null;
        this._export?.unexport(); this._export = null;
        if (this._owner) Gio.bus_unown_name(this._owner);
        this._owner = 0;
    }
}
