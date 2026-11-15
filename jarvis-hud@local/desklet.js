const Clutter = imports.gi.Clutter;
const Cairo = imports.cairo;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const St = imports.gi.St;
const Main = imports.ui.main;
const Desklet = imports.ui.desklet;
const Mainloop = imports.mainloop;

const ORANGE = [1.0, 0.55, 0.0];

function readText(path) {
    try {
        let [ok, bytes] = Gio.File.new_for_path(path).load_contents(null);
        if (ok) return imports.byteArray.toString(bytes).trim();
    } catch (e) {}
    return "";
}

function source(cr, color, alpha) {
    cr.setSourceRGBA(color[0], color[1], color[2], alpha);
}

class JarvisDesklet extends Desklet.Desklet {
    constructor(metadata, id) {
        super(metadata, id);
        this.setHeader("");
        this._header.hide();
        this.actor.set_style("padding: 0; spacing: 0; background-color: transparent;");
        this.content.set_style("padding: 0; margin: 0; background-color: transparent;");
        this.area = new St.DrawingArea({reactive: false, track_hover: false});
        this.area.connect("repaint", area => this._paint(area));
        this.setContent(this.area);
        this.phase = 0;
        this.cpuPrev = null;
        this.stats = {cpu: 0, ram: 0, bat: "--", fan: "--", temp: "--", online: false};
        this._sizeToMonitor();
        this._sample();
        this.animationTimer = Mainloop.timeout_add(50, () => {
            this.phase = (this.phase + 0.025) % (Math.PI * 2);
            this.area.queue_repaint();
            return true;
        });
        this.statsTimer = Mainloop.timeout_add(1500, () => {
            this._sample();
            return true;
        });
    }

    _sizeToMonitor() {
        let monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        this.area.set_size(monitor.width, monitor.height);
        this.content.set_size(monitor.width, monitor.height);
        this.actor.set_size(monitor.width, monitor.height);
    }

    _sample() {
        let stat = readText("/proc/stat").split("\n")[0].trim().split(/\s+/).slice(1).map(Number);
        if (stat.length >= 4) {
            let idle = stat[3] + (stat[4] || 0);
            let total = stat.reduce((a, b) => a + b, 0);
            if (this.cpuPrev) {
                let deltaTotal = total - this.cpuPrev.total;
                let deltaIdle = idle - this.cpuPrev.idle;
                this.stats.cpu = deltaTotal > 0 ? Math.round(100 * (deltaTotal - deltaIdle) / deltaTotal) : 0;
            }
            this.cpuPrev = {total, idle};
        }
        let mem = {};
        for (let line of readText("/proc/meminfo").split("\n")) {
            let m = line.match(/^(MemTotal|MemAvailable):\s+(\d+)/);
            if (m) mem[m[1]] = Number(m[2]);
        }
        if (mem.MemTotal && mem.MemAvailable)
            this.stats.ram = Math.round(100 * (mem.MemTotal - mem.MemAvailable) / mem.MemTotal);

        this.stats.bat = "--";
        try {
            let dir = Gio.File.new_for_path("/sys/class/power_supply").enumerate_children("standard::name", Gio.FileQueryInfoFlags.NONE, null);
            let item;
            while ((item = dir.next_file(null)) !== null) {
                if (item.get_name().startsWith("BAT")) {
                    let value = readText("/sys/class/power_supply/" + item.get_name() + "/capacity");
                    if (value) { this.stats.bat = value; break; }
                }
            }
            dir.close(null);
        } catch (e) {}

        this.stats.fan = "--";
        this.stats.temp = "--";
        let maxFan = 0;
        let automaticFan = false;
        try {
            let hwmons = Gio.File.new_for_path("/sys/class/hwmon").enumerate_children("standard::name", Gio.FileQueryInfoFlags.NONE, null);
            let hwmon;
            while ((hwmon = hwmons.next_file(null)) !== null) {
                let base = "/sys/class/hwmon/" + hwmon.get_name();
                let chip = readText(base + "/name");
                if (chip === "k10temp") {
                    let files = Gio.File.new_for_path(base).enumerate_children("standard::name", Gio.FileQueryInfoFlags.NONE, null);
                    let sensor;
                    while ((sensor = files.next_file(null)) !== null) {
                        if (/^temp\d+_input$/.test(sensor.get_name())) {
                            let reading = Number(readText(base + "/" + sensor.get_name()));
                            if (reading > 0) { this.stats.temp = String(Math.round(reading / 1000)); break; }
                        }
                    }
                    files.close(null);
                }
                let files = Gio.File.new_for_path(base).enumerate_children("standard::name", Gio.FileQueryInfoFlags.NONE, null);
                let file;
                while ((file = files.next_file(null)) !== null) {
                    if (/^fan\d+_input$/.test(file.get_name()))
                        maxFan = Math.max(maxFan, Number(readText(base + "/" + file.get_name())) || 0);
                    if (/^pwm\d+_enable$/.test(file.get_name()) && Number(readText(base + "/" + file.get_name())) >= 2)
                        automaticFan = true;
                }
                files.close(null);
            }
            hwmons.close(null);
        } catch (e) {}
        if (maxFan) this.stats.fan = String(maxFan);
        else if (automaticFan) this.stats.fan = "AUTO";

        this.stats.online = false;
        try {
            let nets = Gio.File.new_for_path("/sys/class/net").enumerate_children("standard::name", Gio.FileQueryInfoFlags.NONE, null);
            let net;
            while ((net = nets.next_file(null)) !== null) {
                let name = net.get_name();
                if (name !== "lo" && readText("/sys/class/net/" + name + "/operstate") === "up")
                    this.stats.online = true;
            }
            nets.close(null);
        } catch (e) {}
        this.area.queue_repaint();
    }

    _text(cr, text, x, y, size, alpha, align) {
        cr.selectFontFace("DejaVu Sans Mono", 0, 0);
        cr.setFontSize(size);
        source(cr, ORANGE, alpha);
        let ext = cr.textExtents(text);
        cr.moveTo(align === "center" ? x - ext.width / 2 : x, y);
        cr.showText(text);
    }

    _dial(cr, x, y, label, value, pct, width) {
        cr.setLineWidth(3);
        source(cr, ORANGE, 0.22); cr.arc(x, y, 48, 0, Math.PI * 2); cr.stroke();
        source(cr, [1.0, 0.70, 0.0], 0.78);
        cr.arc(x, y, 48, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.max(0, Math.min(1, pct / 100))); cr.stroke();
        this._text(cr, label, x, y + 2, 12, 0.86, "center");
        this._text(cr, value, x, y + 20, width || 11, 0.68, "center");
    }

    _paint(area) {
        let [w, h] = area.get_surface_size();
        if (!w || !h) return;
        let cr = area.get_context();
        let cx = w / 2, cy = h / 2;
        let pulse = 1 + 0.012 * Math.sin(this.phase * 2);
        let glow = new Cairo.RadialGradient(cx, cy, 5, cx, cy, 360);
        glow.addColorStopRGBA(0, 1, 0.41, 0, 0.07);
        glow.addColorStopRGBA(0.55, 1, 0.35, 0, 0.025);
        glow.addColorStopRGBA(1, 0.02, 0.02, 0.03, 0);
        cr.setSource(glow); cr.arc(cx, cy, 360, 0, Math.PI * 2); cr.fill();
        cr.save(); cr.translate(cx, cy); cr.scale(pulse, pulse);
        cr.setLineWidth(1.4); source(cr, ORANGE, 0.35);
        for (let r of [190, 215, 245, 270]) { cr.arc(0, 0, r, 0, Math.PI * 2); cr.stroke(); }
        cr.save(); cr.rotate(this.phase);
        source(cr, [1, 0.70, 0], 0.75); cr.setLineWidth(2);
        cr.arc(0, 0, 245, 0.2, 2.2); cr.stroke(); cr.arc(0, 0, 245, 3.5, 5.2); cr.stroke(); cr.restore();
        for (let i = 0; i < 120; i++) {
            cr.save(); cr.rotate(i * Math.PI / 60);
            source(cr, ORANGE, i % 5 === 0 ? 0.65 : 0.26); cr.setLineWidth(i % 5 === 0 ? 2 : 1);
            cr.moveTo(0, -255); cr.lineTo(0, -(i % 5 === 0 ? 270 : 262)); cr.stroke(); cr.restore();
        }
        this._text(cr, "J.A.R.V.I.S.", 0, 6, 18, 0.95, "center");
        cr.restore();

        let fan = this.stats.fan === "--" ? "N/A" : this.stats.fan === "AUTO" ? "AUTO" : this.stats.fan + " RPM";
        let fanPercent = Number.isFinite(Number(this.stats.fan)) ? Number(this.stats.fan) / 50 : 0;
        this._dial(cr, w * 0.19, h * 0.5 - 50, "CPU", this.stats.cpu + "%", this.stats.cpu);
        this._dial(cr, w * 0.19, h * 0.5 + 50, "RAM", this.stats.ram + "%", this.stats.ram);
        this._dial(cr, w * 0.81, h * 0.5 - 50, "BAT", this.stats.bat + "%", Number(this.stats.bat) || 0);
        this._dial(cr, w * 0.81, h * 0.5 + 50, "FAN", fan, fanPercent, 10);
        this._text(cr, `CPU ${this.stats.cpu}%   TEMP ${this.stats.temp === "--" ? "N/A" : this.stats.temp + "°C"}   RAM ${this.stats.ram}%   BAT ${this.stats.bat}%   FAN ${fan}`, 50, 62, 12, 0.72);
        this._text(cr, `NETWORK  ${this.stats.online ? "ONLINE" : "OFFLINE"}   •   ${new Date().toLocaleTimeString()}`, w - 390, 62, 12, 0.72);
        let cpuTemp = Number(this.stats.temp);
        let thermalState = cpuTemp >= 98 ? "THERMAL ALERT  //  FIRMWARE COOLING ACTIVE" :
                           cpuTemp >= 90 ? "THERMAL HIGH  //  FIRMWARE COOLING ACTIVE" :
                           "SYSTEMS NOMINAL  //  DESKTOP INTERFACE";
        this._text(cr, thermalState, 50, h - 45, 12, 0.72);
        this._text(cr, "LOCAL AI  •  AUDIO READY", w - 300, h - 45, 12, 0.72);
        cr.$dispose();
    }

    on_desklet_clicked(event) {
        let [x, y] = event.get_coords();
        let [ax, ay] = this.actor.get_transformed_position();
        let dx = x - ax - this.actor.width / 2;
        let dy = y - ay - this.actor.height / 2;
        if (dx * dx + dy * dy < 180 * 180) {
            try { Gio.Subprocess.new([GLib.get_home_dir() + "/.local/bin/jarvis"], Gio.SubprocessFlags.NONE); }
            catch (e) { global.logError("Jarvis HUD: could not open command bar: " + e); }
        }
    }

    on_desklet_removed() {
        if (this.animationTimer) Mainloop.source_remove(this.animationTimer);
        if (this.statsTimer) Mainloop.source_remove(this.statsTimer);
    }
}

function main(metadata, id) {
    return new JarvisDesklet(metadata, id);
}
