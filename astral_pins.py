#!/usr/bin/env python3
"""
Astral 12V-2x6 per-pin power monitor — GTK4 gauge panel.

Reads the ITE IT8915FN monitoring chip on ASUS ROG Astral RTX 50-series
cards directly over the kernel I2C interface (read-only), and renders:

  - six per-pin gauges (volts / amps) with color states
  - a 2-minute rolling history graph of per-pin current
  - GPU temp / power / utilization via nvidia-smi
  - AIO coolant temp via the ROG Ryujin hwmon (if present)
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
import math
import os
import subprocess
import sys
import threading
import time
from collections import deque

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Gdk, Gio  # noqa: E402

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


def coolant_temp():
    """AIO coolant °C from the ROG Ryujin's hwmon, or None.

    Resolved by name on every call: the hwmon index moves across boots (USB
    enumeration) and the device can drop and re-enumerate mid-session. Two
    sysfs reads every SMI_S seconds is free.
    """
    for path in glob.glob("/sys/class/hwmon/hwmon*/name"):
        try:
            if open(path).read().strip() == "rog_ryujin":
                mdeg = open(os.path.join(os.path.dirname(path),
                                         "temp1_input")).read()
                return int(mdeg) / 1000.0
        except (OSError, ValueError):
            continue
    return None


# ---------------------------------------------------------------- GTK app

CSS = """
window { background: #11151b; color: #e6edf5; }
headerbar { background: #171e27; border-bottom: 1px solid #303946; }
label { color: #e6edf5; }
.content { padding: 20px; }
.heading { font-size: 27px; font-weight: 700; }
.subtitle, .muted { color: #aebdce; }
.subtitle { font-size: 14px; }
.panel, .pin-card, .metric { background: #1a222d; border: 1px solid #303d4b; border-radius: 10px; }
.summary { padding: 18px 22px; }
.metric-title { color: #b5c4d6; font-size: 13px; }
.summary-value { font-size: 35px; font-weight: 600; font-feature-settings: "tnum"; }
.pin-card { padding: 15px 12px; border-top-width: 4px; }
.pin-name { color: #c4d0df; font-size: 13px; font-weight: 600; }
.pin-value { font-size: 25px; font-weight: 600; font-feature-settings: "tnum"; }
.voltage { color: #b5c4d6; font-size: 15px; font-feature-settings: "tnum"; }
.pin-status { font-size: 12px; }
.status { padding: 9px 13px; border-radius: 7px; background: #202d32; font-size: 13px; font-weight: 600; }
.ok { color: #8fdda8; }
.warn { color: #ffd078; }
.alarm { color: #ff8d94; }
.pin-card.warning { border-color: #8d723f; }
.pin-card.fault { border-color: #ae5563; }
.banner { padding: 12px 16px; border: 1px solid #ae5563; border-radius: 8px; background: #41242d; color: #ffdce0; }
.chart { padding: 16px; }
.section-title { font-size: 16px; font-weight: 600; }
.legend { font-size: 12px; }
.metric { padding: 14px; }
.metric-value { font-size: 23px; font-weight: 500; font-feature-settings: "tnum"; }
.metric-icon { color: #9bc8fa; }
.footer { color: #aebdce; font-size: 12px; }
flowboxchild { padding: 0; }
levelbar trough { min-height: 7px; background: #303d4b; border-radius: 5px; }
levelbar block { min-height: 7px; border: none; border-radius: 5px; }
levelbar block.empty { background: transparent; }
"""
for _i, (_r, _g, _b) in enumerate(PIN_COLORS):
    _color = f"#{int(_r*255):02x}{int(_g*255):02x}{int(_b*255):02x}"
    CSS += f".pin-{_i} {{ border-top-color: {_color}; }}\n"
    CSS += f".pin-{_i} levelbar block.filled {{ background: {_color}; }}\n"
    CSS += f".series-{_i} {{ color: {_color}; }}\n"


def label(text="", css=None, xalign=0):
    widget = Gtk.Label(label=text, xalign=xalign)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def set_tone(widget, tone):
    for name in ("ok", "warn", "alarm", "muted"):
        widget.remove_css_class(name)
    if tone:
        widget.add_css_class(tone)


def column(spacing=6, css=None):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    if css:
        for name in css.split():
            box.add_css_class(name)
    return box


def flow(maximum, minimum=1):
    box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                      homogeneous=True, column_spacing=10, row_spacing=10,
                      min_children_per_line=minimum,
                      max_children_per_line=maximum)
    return box


class PinCard:
    def __init__(self, idx):
        self.idx = idx
        self.widget = column(9, f"pin-card pin-{idx}")
        self.widget.set_size_request(130, -1)
        self.widget.append(label(f"PIN {idx}", "pin-name", 0.5))
        self.value = label("— A", "pin-value", 0.5)
        self.voltage = label("— V", "voltage", 0.5)
        self.bar = Gtk.LevelBar(min_value=0.0, max_value=10.0)
        self.bar.set_margin_top(5)
        self.bar.set_margin_bottom(5)
        self.status = label("Waiting", "pin-status muted", 0.5)
        for child in (self.value, self.voltage, self.bar, self.status):
            self.widget.append(child)

    def update(self, volts=None, amps=None, state="Waiting"):
        valid = amps is not None
        self.value.set_label(f"{amps:.3f} A" if valid else "— A")
        self.voltage.set_label(f"{volts:.2f} V" if valid else "— V")
        self.bar.set_value(min(amps, 10.0) if valid else 0)
        tone = ("muted" if not valid else "alarm" if state in
                ("Overcurrent", "Possible dropout", "Confirming") else
                "warn" if state == "Elevated" else "ok")
        self.status.set_label(f"{'✓' if tone == 'ok' else '!' if valid else '–'}  {state}")
        set_tone(self.status, tone)
        set_tone(self.value, tone if tone in ("warn", "alarm") else "")
        for name in ("warning", "fault"):
            self.widget.remove_css_class(name)
        if tone in ("warn", "alarm"):
            self.widget.add_css_class("warning" if tone == "warn" else "fault")
        self.widget.set_tooltip_text(
            f"PIN {self.idx}: {amps:.3f} A, {volts:.2f} V. {state}. Gauge: 0–10 A."
            if valid else f"PIN {self.idx}: no current sensor reading")


class MetricIcon(Gtk.DrawingArea):
    """Small Cairo symbols keep telemetry icons consistent across GTK themes."""
    def __init__(self, kind):
        super().__init__(content_width=30, content_height=34)
        self.kind = kind
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self.draw)

    def draw(self, area, cr, width, height):
        cr.translate((width - 30) / 2, (height - 34) / 2)
        cr.set_source_rgb(0.61, 0.78, 0.98)
        cr.set_line_width(2)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        if self.kind == "temperature":
            cr.move_to(11, 21); cr.line_to(11, 7)
            cr.arc(15, 7, 4, math.pi, 2 * math.pi)
            cr.line_to(19, 21)
            cr.arc(15, 25, 6, -0.73, math.pi + 0.73)
            cr.close_path(); cr.stroke()
            cr.move_to(15, 12); cr.line_to(15, 25); cr.stroke()
        elif self.kind == "power":
            for j, point in enumerate(((17, 3), (7, 19), (14, 19), (12, 31), (24, 14), (17, 14))):
                (cr.move_to if j == 0 else cr.line_to)(*point)
            cr.close_path(); cr.stroke()
        elif self.kind == "utilization":
            cr.arc(15, 18, 12, 0, 2 * math.pi); cr.stroke()
            cr.move_to(15, 18); cr.line_to(21, 10); cr.stroke()
            for x, y in ((5, 18), (9, 10), (15, 7), (25, 18)):
                cr.arc(x, y, 1, 0, 2 * math.pi); cr.fill()
        elif self.kind == "memory":
            cr.rectangle(7, 8, 16, 19); cr.stroke()
            for y in (11, 17, 23):
                cr.move_to(3, y); cr.line_to(7, y)
                cr.move_to(23, y); cr.line_to(27, y)
            cr.stroke()
            cr.rectangle(11, 12, 8, 11); cr.stroke()
        else:
            cr.move_to(15, 3)
            cr.curve_to(12, 10, 5, 17, 5, 23)
            cr.curve_to(5, 35, 25, 35, 25, 23)
            cr.curve_to(25, 17, 18, 10, 15, 3)
            cr.close_path(); cr.stroke()


class Metric:
    def __init__(self, title, icon):
        self.widget = Gtk.Box(spacing=10)
        self.widget.add_css_class("metric")
        self.widget.set_size_request(162, -1)
        image = MetricIcon(icon)
        self.widget.append(image)
        text = column(6)
        text.append(label(title, "metric-title"))
        self.value = label("—", "metric-value")
        text.append(self.value)
        self.widget.append(text)

    def update(self, value):
        self.value.set_label(value)


class App(Gtk.Application):
    def __init__(self, bus, fd, demo=False):
        super().__init__(application_id="dev.zac.astralpins",
                         flags=Gio.ApplicationFlags.NON_UNIQUE if demo else Gio.ApplicationFlags.FLAGS_NONE)
        self.bus, self.fd, self.demo = bus, fd, demo
        self.history = [deque() for _ in range(6)]
        self.over_since = [None] * 6
        self.telemetry = (None, None)
        self.alert_msg = None
        self.last_sample = None
        self.stale = True
        self.stop_event = threading.Event()
        self.timers = []
        self.started = time.monotonic()
        self.graph_pointer = None

    def do_activate(self):
        if self.get_active_window():
            self.get_active_window().present()
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = Gtk.ApplicationWindow(application=self, title="Astral Pins")
        win.set_default_size(1120, 940)
        header = Gtk.HeaderBar()
        header.set_title_widget(label("Astral Pins", "section-title"))
        win.set_titlebar(header)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        win.set_child(scroll)
        box = column(14, "content")
        scroll.set_child(box)
        heading = column(4)
        heading.append(label("Astral Pins", "heading"))
        heading.append(label("12V-2x6 power monitor", "subtitle"))
        box.append(heading)

        summary = flow(3)
        summary.add_css_class("panel")
        summary.add_css_class("summary")
        self.power = label("— W", "summary-value")
        self.current = label("— A", "summary-value")
        for title, value in (("Connector power", self.power), ("Total current", self.current)):
            group = column()
            group.append(label(title, "metric-title"))
            group.append(value)
            summary.insert(group, -1)
        self.status = label("Waiting for sensor", "status muted", 0.5)
        self.status.set_valign(Gtk.Align.CENTER)
        self.status.set_wrap(True)
        summary.insert(self.status, -1)
        box.append(summary)
        self.banner = label("", "banner")
        self.banner.set_wrap(True)
        self.banner.set_visible(False)
        box.append(self.banner)

        self.cards_box = flow(6, 2)
        self.rows = [PinCard(i) for i in range(6)]
        for row in self.rows:
            self.cards_box.insert(row.widget, -1)
        box.append(self.cards_box)

        chart = column(12, "panel chart")
        chart_header = Gtk.Box(spacing=14)
        chart_header.append(label("Pin current", "section-title"))
        self.chart_note = label("Last 2 minutes", "muted")
        chart_header.append(self.chart_note)
        chart.append(chart_header)
        legend = flow(6, 3)
        for i in range(6):
            legend.insert(label(f"━  PIN {i}", f"legend series-{i}"), -1)
        chart.append(legend)
        self.graph = Gtk.DrawingArea(content_height=230, hexpand=True, vexpand=True)
        self.graph.set_draw_func(self.draw_graph)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_graph_motion)
        motion.connect("leave", self.on_graph_leave)
        self.graph.add_controller(motion)
        chart.append(self.graph)
        self.hover_note = label("Hover over the graph to compare pin peaks at a current level", "muted")
        self.hover_note.set_wrap(True)
        chart.append(self.hover_note)
        peaks = flow(6, 3)
        self.peak_labels = []
        for i in range(6):
            value = label(f"PIN {i}: —", f"legend series-{i}")
            self.peak_labels.append(value)
            peaks.insert(value, -1)
        chart.append(peaks)
        self.graph.connect("resize", lambda area, width, height: self.refresh_graph_hover())
        chart.set_vexpand(True)
        box.append(chart)

        metrics = flow(5)
        self.metrics = []
        for title, icon in (("GPU temperature", "temperature"),
                            ("GPU board power", "power"),
                            ("Utilization", "utilization"),
                            ("VRAM", "memory"),
                            ("Coolant", "coolant")):
            metric = Metric(title, icon)
            self.metrics.append(metric)
            metrics.insert(metric.widget, -1)
        box.append(metrics)
        footer = Gtk.Box(spacing=12)
        source = "Demo · Simulated readings" if self.demo else f"i2c-{self.bus} · Read-only"
        footer.append(label(source, "footer"))
        self.freshness = label("Waiting for first sample", "footer", 1)
        self.freshness.set_hexpand(True)
        self.freshness.set_wrap(True)
        footer.append(self.freshness)
        box.append(footer)
        if self.demo:
            now = time.monotonic()
            for seconds in range(HISTORY_S, 0, -1):
                for i, (_, amps) in enumerate(self.demo_pins(now - seconds)):
                    self.history[i].append((now - seconds, amps))
        self.poll_pins()
        self.refresh_smi_label()
        self.timers.append(GLib.timeout_add(int(POLL_S * 1000), self.poll_pins))
        if not self.demo:
            threading.Thread(target=self.smi_loop, daemon=True).start()
        self.timers.append(GLib.timeout_add(int(SMI_S * 1000), self.refresh_smi_label))
        win.present()

    def do_shutdown(self):
        self.stop_event.set()
        for timer in self.timers:
            GLib.source_remove(timer)
        Gtk.Application.do_shutdown(self)

    def demo_pins(self, now):
        return [(12.0, base + 0.045 * math.sin((now - self.started) * 0.6 + i))
                for i, base in enumerate((5.10, 5.20, 5.00, 5.30, 5.15, 5.25))]

    def poll_pins(self):
        now = time.monotonic()
        try:
            pins = self.demo_pins(now) if self.demo else decode(read_raw(self.fd))
        except OSError:
            self.show_stale(now)
            return True
        self.update_pins(pins, now)
        return True

    def prune_history(self, now):
        for samples in self.history:
            while samples and samples[0][0] < now - HISTORY_S:
                samples.popleft()

    def show_stale(self, now):
        self.stale = True
        self.over_since = [None] * 6  # A gap cannot establish sustained current.
        self.power.set_label("— W")
        self.current.set_label("— A")
        self.status.set_label("Sensor unavailable")
        set_tone(self.status, "warn")
        age = f"Last sample {int(now - self.last_sample)} seconds ago" if self.last_sample is not None else "No successful sample yet"
        self.freshness.set_label(age)
        self.alert_msg = "Sensor read failed · Retrying automatically"
        self.banner.set_label(self.alert_msg)
        self.banner.set_visible(True)
        self.chart_note.set_label("Historical data · Sensor disconnected")
        for row in self.rows:
            row.update(state="Unavailable")
        self.prune_history(now)
        self.refresh_graph_hover()
        self.graph.queue_draw()

    def update_pins(self, pins, now):
        # A delayed event-loop tick must not count unobserved time as sustained load.
        if self.last_sample is not None and now - self.last_sample > POLL_S * 2.5:
            self.over_since = [None] * 6
        total_a = sum(a for _, a in pins)
        total_w = sum(v * a for v, a in pins)
        alerts = []
        elevated = pending = False
        for i, (v, a) in enumerate(pins):
            state = "OK"
            if a >= ALARM_A:
                if self.over_since[i] is None:
                    self.over_since[i] = now
                duration = now - self.over_since[i]
                if duration >= ALARM_HOLD_S:
                    state = "Overcurrent"
                    alerts.append(f"PIN {i}: {a:.3f} A for {duration:.0f} s (threshold {ALARM_A} A)")
                else:
                    state, pending = "Confirming", True
            else:
                self.over_since[i] = None
                if a >= WARN_A:
                    state, elevated = "Elevated", True
            if total_a > LOADED_TOTAL_A and a < DROPOUT_A:
                state = "Possible dropout"
                alerts.append(f"PIN {i}: {a:.3f} A under load · Possible dropout")
            self.rows[i].update(v, a, state)
            self.history[i].append((now, a))
        self.prune_history(now)
        self.stale = False
        self.last_sample = now
        self.power.set_label(f"{total_w:.1f} W")
        self.current.set_label(f"{total_a:.2f} A")
        self.alert_msg = " · ".join(alerts) or None
        self.banner.set_visible(bool(alerts))
        if alerts:
            self.banner.set_label("!  " + self.alert_msg)
        text, tone = (("!  Connector alert", "alarm") if alerts else
                      ("!  Above 9.2 A · Confirming", "alarm") if pending else
                      ("!  Elevated current", "warn") if elevated else
                      ("✓  Within thresholds", "ok"))
        self.status.set_label(text)
        set_tone(self.status, tone)
        self.freshness.set_label("Simulated · Updated just now" if self.demo else "Updated just now")
        self.chart_note.set_label("Last 2 minutes")
        self.refresh_graph_hover()
        self.graph.queue_draw()

    def smi_loop(self):
        while not self.stop_event.is_set():
            gpu = None
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=temperature.gpu,power.draw,utilization.gpu,memory.used,memory.total",
                     "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, check=True)
                t, p, u, mu, mt = [float(s.strip()) for s in result.stdout.strip().split(",")]
                gpu = (f"{t:.0f} °C", f"{p:.1f} W", f"{u:.0f}%", f"{mu/1024:.1f} / {mt/1024:.0f} GiB")
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self.telemetry = (gpu, coolant_temp())
            self.stop_event.wait(SMI_S)

    def refresh_smi_label(self):
        gpu, coolant = (("62 °C", "401.0 W", "96%", "18.4 / 32 GiB"), 34.0) if self.demo else self.telemetry
        for metric, value in zip(self.metrics[:4], gpu or ("—",) * 4):
            metric.update(value)
            metric.widget.set_tooltip_text(None if gpu else "GPU telemetry unavailable")
        self.metrics[4].update(f"{coolant:.1f} °C" if coolant is not None else "Not detected")
        return True

    def graph_hover_data(self, w, h, now):
        """Return the hovered current and each visible pin peak, without interpolation."""
        left, right, top, bottom = 42, w - 14, 30, h - 28
        visible = [[(t, a) for t, a in samples if now - HISTORY_S <= t <= now]
                   for samples in self.history]
        y_max = max(10, math.ceil(max((a for samples in visible for _, a in samples),
                                    default=0) / 2) * 2)
        level = None
        if self.graph_pointer is not None and right > left and bottom > top:
            x, y = self.graph_pointer
            if left <= x <= right and top <= y <= bottom:
                level = (bottom - y) * y_max / (bottom - top)
        peaks = [max(samples, key=lambda sample: sample[1], default=None) for samples in visible]
        return level, peaks, y_max

    def on_graph_motion(self, controller, x, y):
        self.graph_pointer = (x, y)
        self.refresh_graph_hover()
        self.graph.queue_draw()

    def on_graph_leave(self, controller):
        self.graph_pointer = None
        self.refresh_graph_hover()
        self.graph.queue_draw()

    def refresh_graph_hover(self):
        level, peaks, _ = self.graph_hover_data(
            self.graph.get_width(), self.graph.get_height(), time.monotonic())
        if level is None:
            self.hover_note.set_label("Hover over the graph to compare pin peaks at a current level")
        else:
            prefix = "Historical · " if self.stale else ""
            self.hover_note.set_label(f"{prefix}{level:.3f} A · Peaks at or above the line · Last 2 minutes")
        for i, (widget, peak) in enumerate(zip(self.peak_labels, peaks)):
            value = ("—" if level is None else "No data" if peak is None else
                     f"{peak[1]:.3f} A" if peak[1] >= level else "Below line")
            widget.set_label(f"PIN {i}: {value}")

    def draw_graph(self, area, cr, w, h, _data=None):
        left, right, top, bottom = 42, w - 14, 30, h - 28
        if right <= left or bottom <= top:
            return
        now = time.monotonic()
        level, peaks, y_max = self.graph_hover_data(w, h, now)
        span = bottom - top
        cr.select_font_face("sans-serif")
        cr.set_font_size(12)
        def text(x, y, content, color=(0.68, 0.75, 0.83)):
            cr.set_source_rgb(*color)
            cr.move_to(x, y)
            cr.show_text(content)
        # Expand the axis for over-range samples, never silently clip them.
        for tick in range(6):
            amps = y_max * tick / 5
            y = bottom - span * amps / y_max
            cr.set_source_rgb(0.21, 0.26, 0.32)
            cr.set_line_width(0.6)
            cr.move_to(left, y)
            cr.line_to(right, y)
            cr.stroke()
            text(0, y + 4, f"{amps:g} A")
        for threshold, color in ((WARN_A, (0.72, 0.58, 0.32)), (ALARM_A, (1.0, 0.48, 0.53))):
            y = bottom - span * threshold / y_max
            cr.set_source_rgb(*color)
            cr.set_dash([5, 5])
            cr.set_line_width(1)
            cr.move_to(left, y)
            cr.line_to(right, y)
            cr.stroke()
        cr.set_dash([])
        text(left, 13, "Warning 8.0 A", (0.88, 0.73, 0.46))
        caption = "Alarm 9.2 A · 5 s"
        ext = cr.text_extents(caption)
        text(right - ext.width, 13, caption, (1.0, 0.55, 0.59))
        for seconds in (120, 90, 60, 30, 0):
            x = left + (right - left) * (1 - seconds / HISTORY_S)
            caption = f"-{seconds}s" if seconds else "Now"
            ext = cr.text_extents(caption)
            text(max(0, min(x - ext.width / 2, w - ext.width)), h - 5, caption)
        cr.save()
        cr.rectangle(left, top, right - left, span)
        cr.clip()
        for i, samples in enumerate(self.history):
            cr.set_source_rgba(*PIN_COLORS[i], 0.35 if self.stale else 1.0)
            cr.set_line_width(1.6)
            previous = None
            for timestamp, amps in samples:
                x = right - (right - left) * (now - timestamp) / HISTORY_S
                y = bottom - span * amps / y_max
                if previous is None or timestamp - previous > POLL_S * 2.5:
                    cr.move_to(x, y)
                else:
                    cr.line_to(x, y)
                previous = timestamp
            cr.stroke()
        if level is not None:
            y = bottom - span * level / y_max
            cr.set_source_rgb(0.90, 0.94, 1.0)
            cr.set_line_width(1.2)
            cr.set_dash([2, 4])
            cr.move_to(left, y)
            cr.line_to(right, y)
            cr.stroke()
            cr.set_dash([])
            for i, peak in enumerate(peaks):
                if peak is not None and peak[1] >= level:
                    timestamp, amps = peak
                    x = right - (right - left) * (now - timestamp) / HISTORY_S
                    y = bottom - span * amps / y_max
                    cr.set_source_rgb(*PIN_COLORS[i])
                    cr.arc(x, y, 4, 0, 2 * math.pi)
                    cr.fill()
        cr.restore()
        if not any(peak is not None for peak in peaks):
            text(left + 12, top + span / 2, "Waiting for current history…")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", type=int, default=None,
                    help="I2C bus number (skips auto-scan)")
    ap.add_argument("--demo", action="store_true",
                    help="Preview the interface with simulated readings; no hardware access")
    args, gtk_args = ap.parse_known_args()
    bus, fd = (None, None) if args.demo else find_bus(args.bus)
    try:
        return App(bus, fd, demo=args.demo).run([sys.argv[0]] + gtk_args)
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    main()
