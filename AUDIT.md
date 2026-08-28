# Code audit & fix plan

Audit of `astral_pins.py` (plus packaging files) as of `d62985e`. Findings are
ordered by priority; each has a proposed fix. Nothing here changes the
read-only I2C posture of the tool.

## P1 — Correctness bugs

### 1. LevelBar color zones are inverted (warn color shown at normal current)

`PinRow.__init__` registers offsets:

```python
self.bar.add_offset_value("warnzone", ALARM_A)   # 9.2
self.bar.add_offset_value("alarmzone", 10.0)
```

GTK's `GtkLevelBar` offset semantics are "the interval *topped by* the offset
value gets that offset's style class". So a value of, say, 3 A falls in the
interval [0, 9.2] and the filled block is styled `.warnzone` (amber), and
anything between 9.2 and 10 gets `.alarmzone`. Consequences:

- The bar is amber at perfectly normal currents and only ever amber/red.
- The default blue `levelbar block.filled` CSS never applies.
- `WARN_A` (8.0) plays no part in the bar at all (only in the text label
  class), so the bar and the numeric label disagree between 8.0–9.2 A.

**Fix:** register three offsets matching the label logic —
`("ok", WARN_A)`, `("warn", ALARM_A)`, `("alarm", 10.0)` — and change the CSS
to color `.filled.ok` blue, `.filled.warn` amber, `.filled.alarm` red.

### 2. Launching a second instance corrupts the first

`Gtk.Application` with a fixed `application_id` is unique per session: running
the script while a panel is already open delivers `activate` to the *primary*
instance, and `do_activate` unconditionally builds a second window, a second
`poll_pins` timeout, a second SMI thread, and a second label-refresh timeout on
it. The result is doubled polling (with two timers appending to the same
history deques, so the graph timeline runs 2×) and duplicate windows.

**Fix:** guard `do_activate`:

```python
if self.props.active_window:
    self.props.active_window.present()
    return
```

### 3. Leftover CLI args crash the app; multi-GPU breaks the SMI readout

- `main()` uses `parse_known_args()` and forwards unknown args to
  `app.run()`. GTK4 no longer consumes X11/GTK command-line options, and
  `GApplication` (flags `NONE`) errors out on any unrecognized option — so a
  typo like `--buss 7` silently skips argparse and then kills the app with a
  GLib "Unknown option" error after the bus scan already ran.
  **Fix:** use `ap.parse_args()` and call `app.run([sys.argv[0]])`.
- `smi_loop` parses `nvidia-smi` output with a single `out.split(",")`. With
  more than one GPU the query prints one line per GPU, the 5-way unpack raises,
  and the label permanently reads "nvidia-smi unavailable" on exactly the
  multi-GPU machines most likely to run this.
  **Fix:** parse `out.splitlines()[0]` (or select the GPU with `-i`/by PCI bus
  id, ideally the one whose I2C bus we're reading).

## P2 — Robustness

### 4. I2C polling runs on the GTK main thread

`poll_pins` does 24 blocking SMBus ioctls per tick inside a `GLib.timeout_add`
callback. SMBus byte transactions through the NVIDIA adapter can be slow
(milliseconds each, worse under bus contention), so worst case the UI stutters
every second. **Fix:** move `read_raw`+`decode` into a worker thread (like
`smi_loop`) and marshal results to the UI with `GLib.idle_add`; keep all widget
access on the main thread.

### 5. No recovery from a persistently failing bus, and history distorts on errors

- On `OSError` the poll shows "retrying…" forever; if the driver reloads or
  the device node goes away, the fd never becomes valid again. **Fix:** after N
  consecutive failures, close the fd and re-run `find_bus` (without the
  `sys.exit` paths — see below).
- Failed polls append nothing to the history deques, so the graph's implicit
  time axis compresses across gaps. **Fix:** append `None`/NaN on failure and
  break the line in `draw_graph`, so gaps are visible instead of hidden.
- `e.errno` can be `None` for some OSErrors → header shows "read error
  (None)". Use `e.strerror or e` in the message.

### 6. No plausibility filtering of raw readings

A wedged bus that answers `0xFF` (or torn 16-bit values, since each register is
read in a separate transaction) yields momentary readings like 65.5 A, which
can trip the alarm banner and spike the graph. **Fix:** discard/clamp samples
outside a plausible envelope (e.g. 0–16 V, 0–15 A) and require two consecutive
dropout-signature samples before raising the dropout alert (the sustained-9.2 A
alert already has a 5 s hold; the dropout alert fires on a single sample).

### 7. `find_bus` mixes probing with process exit, and small hygiene issues

- `sys.exit` inside `find_bus` makes it untestable and produces a misleading
  message ("not found on any NVIDIA I2C bus") when the user passed an explicit
  `--bus`. **Fix:** raise a dedicated exception, handle it in `main()`, and
  tailor the message for the explicit-bus case.
- `open(path).read()` leaks the file object (ResourceWarning under `-W`); use a
  `with` block.

## P3 — Polish / maintainability

### 8. Deprecated GTK APIs

`CssProvider.load_from_data` is deprecated since GTK 4.12 (use
`load_from_string`, with a fallback for older GTK). Worth doing now so the app
stays quiet on current distros.

### 9. Alert banner has no hysteresis

`alert_msg` is recomputed every poll, so a pin oscillating around a threshold
flickers the banner on/off at 1 Hz. **Fix:** hold the banner for a minimum
time (e.g. 10 s) after the last triggering sample. Optionally emit a desktop
notification (`Gio.Notification`) on first trigger — this is a safety tool and
the window may not be visible when it matters.

### 10. Tests and CI (currently none)

`decode()` and the alert logic are pure and easy to test — the register layout
is exactly the kind of thing a refactor silently breaks. **Fix:** extract the
alert-state computation into a pure function, add a small `pytest` module
(decode round-trip with reversed pin order, warn/alarm/dropout transitions),
and a GitHub Actions workflow running `ruff check` + `pytest` (no GTK needed if
imports are kept lazy or tests target the pure functions).

### 11. Packaging nits

- `pyproject.toml`: move to SPDX `license = "MIT"` and drop the license
  classifier (setuptools ≥ 77 deprecates the table+classifier combo).
- `contrib/astral-pins.desktop`: add `StartupNotify=true`; consider shipping a
  proper icon instead of `utilities-system-monitor`.

## Suggested order of work

1. **Fix pass 1 (small, user-visible):** items 1, 2, 3 — three focused commits,
   no structural change.
2. **Fix pass 2 (structure):** items 4, 5, 7 together, since moving polling to
   a thread touches the same code paths as reconnection and error display.
3. **Fix pass 3 (hardening + polish):** items 6, 8, 9.
4. **Tooling:** item 10 (tests + CI), then 11.
