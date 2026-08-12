#!/usr/bin/env python3
"""
Astral 12V-2x6 per-pin power monitor — GTK4 gauge panel.

Reads the ITE IT8915FN monitoring chip on ASUS ROG Astral RTX 50-series
cards directly over the kernel I2C interface (read-only), and renders:

  - six per-pin gauges (volts / amps) with color states
  - a 2-minute rolling history graph of per-pin current
  - GPU temp / power / utilization via nvidia-smi
  - a red alert state if any pin sits above 9.2 A (ASUS's own
    warning threshold) for 5+ seconds, or a pin reads ~0 A while
    the connector is under load (dropout signature)

Usage:
    ./astral_pins.py            # auto-scans NVIDIA I2C buses
    ./astral_pins.py --bus 7    # skip the scan, use i2c-7

Requires: python3-gi, gir1.2-gtk-4.0 (stock on Ubuntu GNOME),
membership in the i2c group (or run with sudo, but don't).
"""

import argparse
import ctypes
import fcntl
import glob
import os
import subprocess
import sys
import threading
import time
from collections import deque

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Gdk  # noqa: E402

# ---------------------------------------------------------------- I2C layer

I2C_SLAVE = 0x0703
I2C_SMBUS = 0x0720
I2C_SMBUS_READ = 1
I2C_SMBUS_BYTE_DATA = 2
CHIP_ADDR = 0x2B
REG_BASE = 0x80
PIN_BYTES = 24  # 6 pins x (2B mV + 2B mA), big-endian, reverse pin order

WARN_A = 8.0        # amber
ALARM_A = 9.2       # red (ASUS Power Detector+ threshold)
ALARM_HOLD_S = 5.0  # sustained seconds before alert state
DROPOUT_A = 0.2     # a pin below this while connector is loaded => dropout
LOADED_TOTAL_A = 10.0

HISTORY_S = 120     # seconds of graph history
POLL_S = 1.0        # pin poll interval
SMI_S = 2.0         # nvidia-smi poll interval

PIN_COLORS = [
    (0.36, 0.72, 1.00),  # pin 0
    (0.42, 0.90, 0.55),  # pin 1
    (1.00, 0.78, 0.35),  # pin 2
    (0.95, 0.55, 0.85),  # pin 3
    (0.60, 0.62, 1.00),  # pin 4
    (0.55, 0.90, 0.90),  # pin 5
]


class SMBusData(ctypes.Union):
    _fields_ = [
        ("byte", ctypes.c_uint8),
        ("word", ctypes.c_uint16),
        ("block", ctypes.c_uint8 * 34),
    ]


class SMBusIoctl(ctypes.Structure):
    _fields_ = [
        ("read_write", ctypes.c_uint8),
        ("command", ctypes.c_uint8),
        ("size", ctypes.c_uint32),
        ("data", ctypes.POINTER(SMBusData)),
    ]


def read_raw(fd):
    """24 SMBus read-byte-data ops — byte-for-byte what `i2cget ... b` does.

    Empirically on the Astral: raw I2C_RDWR transfers (block OR byte-wise)
    return zeros/junk from the IT8915FN via NVIDIA's adapter, while the
    kernel SMBus ioctl path returns real data. So we use exactly that path.
    """
    fcntl.ioctl(fd, I2C_SLAVE, CHIP_ADDR)
    out = bytearray()
    data = SMBusData()
    for off in range(REG_BASE, REG_BASE + PIN_BYTES):
        args = SMBusIoctl(I2C_SMBUS_READ, off, I2C_SMBUS_BYTE_DATA,
                          ctypes.pointer(data))
        fcntl.ioctl(fd, I2C_SMBUS, args)
        out.append(data.byte)
    return bytes(out)


def decode(raw):
    """-> list of (volts, amps) indexed pin 0..5."""
    pins = [None] * 6
    for i in range(6):
        o = i * 4
        mv = (raw[o] << 8) | raw[o + 1]
        ma = (raw[o + 2] << 8) | raw[o + 3]
        pins[5 - i] = (mv / 1000.0, ma / 1000.0)  # hardware order is reversed
    return pins


def find_bus(explicit=None):
    """Return (bus_number, fd) for the bus hosting the IT8915FN."""
    if explicit is not None:
        candidates = [explicit]
    else:
        candidates = []
        for path in sorted(glob.glob("/sys/bus/i2c/devices/i2c-*/name")):
            try:
                name = open(path).read()
            except OSError:
                continue
            if "nvidia" in name.lower():
                candidates.append(int(os.path.basename(os.path.dirname(path)).split("-")[1]))
    for bus in candidates:
        dev = f"/dev/i2c-{bus}"
        try:
            fd = os.open(dev, os.O_RDWR)
        except PermissionError:
            sys.exit(
                f"Permission denied on {dev}.\n"
                "Add yourself to the i2c group (see udev setup) and re-log."
            )
        except OSError as e:
            print(f"skip i2c-{bus}: open failed: {e}", file=sys.stderr)
            continue
        try:
            pins = decode(read_raw(fd))
            # sanity: at least one pin near 12 V
            if any(6.0 < v < 14.0 for v, _ in pins):
                return bus, fd
            print(f"skip i2c-{bus}: device at 0x2b but no ~12 V pin "
                  f"(pin5={pins[5][0]:.2f} V)", file=sys.stderr)
        except OSError as e:
            print(f"skip i2c-{bus}: probe failed: {e}", file=sys.stderr)
        os.close(fd)
    sys.exit(
        "IT8915FN not found at 0x2b on any NVIDIA I2C bus.\n"
        "Is i2c-dev loaded?  (sudo modprobe i2c-dev)"
    )


# ---------------------------------------------------------------- GTK app

CSS = b"""
window { background-color: #14161a; }
label { color: #d8dce2; font-family: monospace; }
.title { font-size: 15px; font-weight: bold; color: #ffb454; }
.pinname { font-size: 13px; font-weight: bold; }
.value { font-size: 13px; }
.amps-ok { color: #7ce38b; }
.amps-warn { color: #ffcf5e; }
.amps-alarm { color: #ff6b6b; font-weight: bold; }
.gpu { font-size: 12px; color: #9aa4b2; }
.banner { font-size: 14px; font-weight: bold; color: #ffffff;
          background-color: #b3261e; padding: 6px; }
levelbar block.filled { background-color: #4d8fd1; }
levelbar block.filled.warnzone { background-color: #d1a24d; }
levelbar block.filled.alarmzone { background-color: #d14d4d; }
"""


class PinRow:
    def __init__(self, idx, grid, row):
        self.name = Gtk.Label(label=f"PIN {idx}", xalign=0)
        self.name.add_css_class("pinname")
        r, g, b = PIN_COLORS[idx]
        self.name.set_markup(
            f'<span foreground="#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}">PIN {idx}</span>'
        )
        self.bar = Gtk.LevelBar(min_value=0.0, max_value=10.0)
        self.bar.set_hexpand(True)
        self.bar.add_offset_value("warnzone", ALARM_A)
        self.bar.add_offset_value("alarmzone", 10.0)
        self.value = Gtk.Label(label="--.-- V   -.--- A", xalign=1)
        self.value.add_css_class("value")
        grid.attach(self.name, 0, row, 1, 1)
        grid.attach(self.bar, 1, row, 1, 1)
        grid.attach(self.value, 2, row, 1, 1)

    def update(self, volts, amps):
        self.bar.set_value(min(amps, 10.0))
        self.value.set_label(f"{volts:5.2f} V  {amps:6.3f} A")
        for c in ("amps-ok", "amps-warn", "amps-alarm"):
            self.value.remove_css_class(c)
        cls = "amps-ok" if amps < WARN_A else ("amps-warn" if amps < ALARM_A else "amps-alarm")
        self.value.add_css_class(cls)


class App(Gtk.Application):
    def __init__(self, bus, fd):
        super().__init__(application_id="dev.zac.astralpins")
        self.bus, self.fd = bus, fd
        self.history = [deque(maxlen=int(HISTORY_S / POLL_S)) for _ in range(6)]
        self.over_since = [None] * 6
        self.smi_text = "nvidia-smi: …"
        self.alert_msg = None

    # ---- UI construction
    def do_activate(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = Gtk.ApplicationWindow(application=self, title="Astral 12V-2x6")
        win.set_default_size(560, 620)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=10, margin_bottom=10,
                      margin_start=12, margin_end=12)
        win.set_child(box)

        self.banner = Gtk.Label(label="", visible=False)
        self.banner.add_css_class("banner")
        box.append(self.banner)

        self.header = Gtk.Label(xalign=0)
        self.header.add_css_class("title")
        box.append(self.header)

        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        box.append(grid)
        self.rows = [PinRow(i, grid, i) for i in range(6)]

        self.graph = Gtk.DrawingArea(content_height=200, hexpand=True, vexpand=True)
        self.graph.set_draw_func(self.draw_graph)
        box.append(self.graph)

        self.gpu_label = Gtk.Label(xalign=0)
        self.gpu_label.add_css_class("gpu")
        box.append(self.gpu_label)

        win.present()

        GLib.timeout_add(int(POLL_S * 1000), self.poll_pins)
        threading.Thread(target=self.smi_loop, daemon=True).start()
        GLib.timeout_add(int(SMI_S * 1000), self.refresh_smi_label)
        self.poll_pins()

    # ---- data plumbing
    def poll_pins(self):
        try:
            pins = decode(read_raw(self.fd))
        except OSError as e:
            self.header.set_label(f"i2c-{self.bus}  —  read error ({e.errno}), retrying…")
            return True

        now = time.monotonic()
        total_a = sum(a for _, a in pins)
        total_w = sum(v * a for v, a in pins)
        alerts = []

        for i, (v, a) in enumerate(pins):
            self.rows[i].update(v, a)
            self.history[i].append(a)
            if a >= ALARM_A:
                self.over_since[i] = self.over_since[i] or now
                if now - self.over_since[i] >= ALARM_HOLD_S:
                    alerts.append(f"PIN {i} sustained {a:.2f} A > {ALARM_A} A")
            else:
                self.over_since[i] = None
            if total_a > LOADED_TOTAL_A and a < DROPOUT_A:
                alerts.append(f"PIN {i} at {a:.2f} A under load — possible dropout")

        self.header.set_label(
            f"i2c-{self.bus}   connector: {total_w:6.1f} W   {total_a:6.2f} A")
        self.alert_msg = " · ".join(alerts) if alerts else None
        self.banner.set_visible(bool(self.alert_msg))
        if self.alert_msg:
            self.banner.set_label("⚠  " + self.alert_msg)
        self.graph.queue_draw()
        return True

    def smi_loop(self):
        while True:
            try:
                out = subprocess.run(
                    ["nvidia-smi",
                     "--query-gpu=temperature.gpu,power.draw,utilization.gpu,memory.used,memory.total",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5).stdout.strip()
                t, p, u, mu, mt = [s.strip() for s in out.split(",")]
                self.smi_text = (f"GPU {t} °C   {p} W board   {u}% util   "
                                 f"{float(mu)/1024:.1f}/{float(mt)/1024:.0f} GiB VRAM")
            except Exception:
                self.smi_text = "nvidia-smi unavailable"
            time.sleep(SMI_S)

    def refresh_smi_label(self):
        self.gpu_label.set_label(self.smi_text)
        return True

    # ---- history graph
    def draw_graph(self, area, cr, w, h, _data=None):
        cr.set_source_rgb(0.055, 0.06, 0.075)
        cr.paint()
        top, bottom, left = 8, h - 18, 34
        span = bottom - top
        y_max = 10.0

        cr.set_font_size(10)
        cr.set_source_rgb(0.45, 0.48, 0.55)
        for amps in (0, 2, 4, 6, 8, 10):
            y = bottom - span * amps / y_max
            cr.move_to(left, y); cr.line_to(w - 4, y)
            cr.set_line_width(0.5); cr.stroke()
            cr.move_to(2, y + 3); cr.show_text(f"{amps:2d}A")

        # ASUS alarm threshold
        y = bottom - span * ALARM_A / y_max
        cr.set_source_rgb(0.85, 0.30, 0.30)
        cr.set_dash([4, 3]); cr.set_line_width(1)
        cr.move_to(left, y); cr.line_to(w - 4, y); cr.stroke()
        cr.set_dash([])

        n = self.history[0].maxlen
        for i in range(6):
            pts = list(self.history[i])
            if len(pts) < 2:
                continue
            r, g, b = PIN_COLORS[i]
            cr.set_source_rgb(r, g, b)
            cr.set_line_width(1.4)
            for j, a in enumerate(pts):
                x = left + (w - 4 - left) * (n - len(pts) + j) / (n - 1)
                yv = bottom - span * min(a, y_max) / y_max
                (cr.move_to if j == 0 else cr.line_to)(x, yv)
            cr.stroke()

        cr.set_source_rgb(0.45, 0.48, 0.55)
        cr.move_to(left, h - 5)
        cr.show_text(f"per-pin current, last {HISTORY_S//60} min")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", type=int, default=None,
                    help="I2C bus number (skips auto-scan)")
    args, gtk_args = ap.parse_known_args()
    bus, fd = find_bus(args.bus)
    app = App(bus, fd)
    app.run([sys.argv[0]] + gtk_args)


if __name__ == "__main__":
    main()
