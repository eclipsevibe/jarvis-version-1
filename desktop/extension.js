import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Cairo from 'cairo';
import Clutter from 'gi://Clutter';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Background from 'resource:///org/gnome/shell/ui/background.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const TAU = Math.PI * 2;

export default class JarvisDesktop extends Extension {
    enable() {
        this._phase = 0;
        this._outerPhase = 0;
        this._tickPhase = 0;
        this._level = 0;
        this._state = {};
        this._reading = false;
        this._runtime = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'jarvis-desktop']);
        GLib.mkdir_with_parents(this._runtime, 0o700);
        this._stateFile = Gio.File.new_for_path(`${this._runtime}/state.json`);
        this._readyFile = Gio.File.new_for_path(`${this._runtime}/ready.json`);
        this._enabled = true;
        this._rebuild();
        this._clones = new Set();
        this._injections = new InjectionManager();
        const extension = this;
        this._injections.overrideMethod(Background.BackgroundManager.prototype,
            '_createBackgroundActor', original => function (...args) {
                const actor = original.apply(this, args);
                extension._decorateBackground(this, actor);
                return actor;
            });
        // Overview actors may already exist when the extension is re-enabled.
        const visit = actor => {
            const manager = actor._bgManager;
            if (manager?.backgroundActor)
                this._decorateBackground(manager, manager.backgroundActor);
            for (const child of actor.get_children()) visit(child);
        };
        visit(Main.overview._overview);
        this._map = global.window_manager.connect('map', (_wm, actor) => {
            this._positionInput(actor.meta_window);
        });
        this._monitors = Main.layoutManager.connect('monitors-changed', () => this._rebuild());
        this._background = Main.layoutManager._backgroundGroup.connect('child-added', () => {
            if (this._area?.get_parent())
                Main.layoutManager._backgroundGroup.set_child_above_sibling(this._area, null);
        });
        this._monitor = this._stateFile.monitor_file(Gio.FileMonitorFlags.NONE, null);
        this._monitor.connect('changed', () => this._read());
        this._read();
        this._heartbeat();
        this._heartbeatTimer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
            this._heartbeat();
            return GLib.SOURCE_CONTINUE;
        });
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 40, () => {
            if (!this._area || Main.layoutManager.primaryIndex < 0)
                return GLib.SOURCE_CONTINUE;
            // Overview clones need a live source, including fullscreen previews.
            const visible = !Main.sessionMode.isLocked && (Main.overview.visible ||
                !global.display.get_monitor_in_fullscreen(Main.layoutManager.primaryIndex));
            this._area.visible = visible;
            if (visible) {
                const fresh = Date.now() / 1000 - (this._state.time || 0) < 3;
                const target = fresh ? Math.max(0, Math.min(1, Number(this._state.level) || 0)) : 0;
                this._level += (target - this._level) * (target > this._level ? 0.75 : 0.35);
                const step = fresh && this._state.mode === 'thinking' ? 0.045 : 0.009;
                // Each layer wraps only after its own complete turn. Multiplying
                // an already wrapped master phase makes slower layers jump.
                this._phase = (this._phase + step) % TAU;
                this._outerPhase = (this._outerPhase - step * 0.7 + TAU) % TAU;
                this._tickPhase = (this._tickPhase + step * 0.12) % TAU;
                this._area.queue_repaint();
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    _decorateBackground(manager, actor) {
        if (manager._monitorIndex !== Main.layoutManager.primaryIndex ||
            manager._settingsSchema !== 'org.gnome.desktop.background' ||
            manager._container === Main.layoutManager._backgroundGroup ||
            [...this._clones].some(clone => clone.get_parent() === actor))
            return;
        const clone = new Clutter.Clone({source: this._area, reactive: false});
        clone.add_constraint(new Clutter.BindConstraint({source: actor,
            coordinate: Clutter.BindCoordinate.ALL}));
        actor.add_child(clone);
        this._clones.add(clone);
        clone.connect('destroy', () => this._clones.delete(clone));
    }

    _positionInput(window) {
        // Qt cannot position top-level windows on Wayland. Place once on map,
        // then let the user drag freely until the next hide/show.
        if (window?.get_title() !== 'J.A.R.V.I.S. — Command Bar')
            return;
        const m = Main.layoutManager.primaryMonitor;
        if (!m) return;
        const work = Main.layoutManager.getWorkAreaForMonitor(m.index);
        const rect = window.get_frame_rect();
        const scale = Math.min(m.width / 1440, m.height / 900);
        const x = m.x + (m.width - rect.width) / 2;
        const y = m.y + m.height * 0.47 + 280 * scale;
        window.move_frame(false,
            Math.round(Math.max(work.x, Math.min(x, work.x + work.width - rect.width))),
            Math.round(Math.max(work.y, Math.min(y, work.y + work.height - rect.height))));
    }

    _heartbeat() {
        // Tiny heartbeat lets the input bar fall back if the extension stops.
        try {
            this._readyFile.replace_contents(JSON.stringify({version: 1, time: Date.now() / 1000}),
                null, false, Gio.FileCreateFlags.PRIVATE, null);
        } catch (e) { console.error(`Jarvis heartbeat: ${e.message}`); }
    }

    _read() {
        if (this._reading || !this._enabled)
            return;
        this._reading = true;
        this._stateFile.load_contents_async(null, (file, result) => {
            this._reading = false;
            if (!this._enabled)
                return;
            try {
                const [, bytes] = file.load_contents_finish(result);
                if (bytes.length < 4096) {
                    const state = JSON.parse(new TextDecoder().decode(bytes));
                    if (state.version === 1)
                        this._state = state;
                }
            } catch (_) { /* Atomic replacements and daemon restarts are expected. */ }
        });
    }

    _rebuild() {
        this._area?.destroy();
        this._limitsButton?.destroy();
        const m = Main.layoutManager.primaryMonitor;
        if (!m)
            return;
        // A nonreactive background child cannot intercept desktop clicks or focus.
        this._area = new St.DrawingArea({reactive: false, can_focus: false,
            x: m.x, y: m.y, width: m.width, height: m.height});
        this._area.connect('repaint', area => this._paint(area));
        Main.layoutManager._backgroundGroup.add_child(this._area);
        this._limitsButton = new St.Button({reactive: true, can_focus: false,
            label: 'CPU / RAM limits · Fan', x: m.x + Math.max(0, m.width / 2 - 125),
            y: m.y + m.height - 125, width: 250, height: 32,
            style: 'color:#ffb438;background-color:rgba(15,10,4,0.8);border:1px solid #80501b;border-radius:8px;font-size:12px;'});
        this._limitsButton.connect('clicked', () => {
            const root = GLib.build_filenamev([GLib.get_home_dir(), 'jarvis']);
            try { Gio.Subprocess.new([`${root}/.venv/bin/python`, `${root}/ui.py`, '--resources'], Gio.SubprocessFlags.NONE); }
            catch (error) { console.error(`Jarvis resource panel: ${error.message}`); }
        });
        Main.layoutManager._backgroundGroup.add_child(this._limitsButton);
        for (const clone of this._clones ?? []) clone.set_source(this._area);
    }

    _paint(area) {
        const [w, h] = area.get_surface_size();
        if (!w || !h)
            return;
        const cr = area.get_context();
        try {
            cr.setSourceRGB(0.016, 0.021, 0.028);
            cr.paint();
            const scale = Math.min(w / 1440, h / 900);
            const cx = w / 2, cy = h * 0.47;
            const level = this._level;
            const color = (a, gold = false) => cr.setSourceRGBA(1, gold ? 0.70 : 0.55, 0, a);
            const text = (value, x, y, size, alpha = 0.7) => {
                cr.selectFontFace('DejaVu Sans Mono', Cairo.FontSlant.NORMAL, Cairo.FontWeight.NORMAL);
                cr.setFontSize(size * scale);
                color(alpha);
                const ext = cr.textExtents(value);
                cr.moveTo(x - ext.width / 2, y);
                cr.showText(value);
            };
            cr.save();
            cr.translate(cx, cy);
            cr.scale(scale, scale);
            const glow = new Cairo.RadialGradient(0, 0, 30, 0, 0, 330);
            glow.addColorStopRGBA(0, 1, 0.38, 0, 0.04 + level * 0.10);
            glow.addColorStopRGBA(1, 1, 0.38, 0, 0);
            cr.setSource(glow); cr.arc(0, 0, 330, 0, TAU); cr.fill();
            cr.scale(1 + level * 0.065, 1 + level * 0.065);
            for (const [r, alpha] of [[158, 0.35], [181, 0.65], [207, 0.28], [231, 0.45]]) {
                color(Math.min(1, alpha + level * 0.25)); cr.setLineWidth(1.2);
                cr.arc(0, 0, r, 0, TAU); cr.stroke();
            }
            for (let i = 0; i < 96; i++) {
                const a = i * TAU / 96 + this._tickPhase;
                const outer = i % 4 === 0 ? 248 + level * 10 : 240;
                color(i % 4 === 0 ? 0.78 : 0.3); cr.setLineWidth(i % 4 === 0 ? 2 : 1);
                cr.moveTo(Math.sin(a) * 234, Math.cos(a) * 234);
                cr.lineTo(Math.sin(a) * outer, Math.cos(a) * outer); cr.stroke();
            }
            for (const [r, rotation, length] of [[181, this._phase, 1.9], [207, this._outerPhase, 1.1]]) {
                cr.save(); cr.rotate(rotation); color(0.85 + level * 0.15, true);
                cr.setLineWidth(3 + level * 2);
                cr.arc(0, 0, r, 0.2, 0.2 + length); cr.stroke();
                cr.arc(0, 0, r, Math.PI + 0.2, Math.PI + 0.2 + length); cr.stroke();
                cr.restore();
            }
            cr.restore();
            text('J.A.R.V.I.S.', cx, cy + 5 * scale, 24, 0.95);
            const fresh = Date.now() / 1000 - (this._state.time || 0) < 3;
            const status = !fresh ? 'ASSISTANT OFFLINE' : level > 0.035 ? 'SPEAKING' :
                this._state.mode === 'thinking' ? 'PROCESSING' : 'AWAITING YOUR REQUEST';
            text(status, cx, cy + 33 * scale, 10, 0.6);
            text('// DESKTOP INTERFACE', cx, cy - 290 * scale, 11, 0.5);
            if (fresh && typeof this._state.telemetry === 'string')
                text(this._state.telemetry, cx, h - 65 * scale, 11, 0.6);
        } finally { cr.$dispose(); }
    }

    disable() {
        this._enabled = false;
        this._injections?.clear();
        if (this._map) global.window_manager.disconnect(this._map);
        this._map = 0;
        for (const clone of this._clones ?? []) clone.destroy();
        this._clones?.clear();
        if (this._timer) GLib.source_remove(this._timer);
        if (this._heartbeatTimer) GLib.source_remove(this._heartbeatTimer);
        if (this._monitors) Main.layoutManager.disconnect(this._monitors);
        if (this._background) Main.layoutManager._backgroundGroup.disconnect(this._background);
        this._monitor?.cancel(); this._monitor = null;
        this._area?.destroy(); this._area = null;
        this._limitsButton?.destroy(); this._limitsButton = null;
        try { this._readyFile?.delete(null); } catch (_) { }
        this._timer = this._heartbeatTimer = this._monitors = this._background = 0;
        this._state = {};
    }
}
