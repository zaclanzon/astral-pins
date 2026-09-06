# astral-pins

Per-pin 12V-2x6 power monitoring for ASUS ROG Astral RTX 50-series cards on
Linux — a GTK4 panel that reads the card's onboard ITE **IT8915FN** monitoring
chip directly over I2C.

ASUS markets this hardware as *Power Detector+* and surfaces it in GPU Tweak
on Windows. This tool talks to the same chip from Linux, read-only, with no
driver and no vendor software.

![Astral Pins redesigned interface with simulated readings](screenshots/2.png)

## What it shows

- Six color-coded pin cards with volts, amps, gauges, and explicit status labels
- A 2-minute rolling graph with a six-pin legend, warning/alarm guides, and timestamps
- Hover over the plot for a horizontal dotted current guide and each pin's highest
  visible reading at or above that level. Matching peaks are marked on the graph;
  the summary identifies pins below the line or without history.
- Total connector watts and amps
- GPU temperature, board power, utilization, and VRAM via `nvidia-smi`
- AIO coolant temperature via a `rog_ryujin` hwmon, when one is present
- A red alert banner if any pin sustains more than 9.2 A (ASUS's own warning
  threshold) for 5+ seconds, or if a pin reads ~0 A while the connector is
  under load — the classic melted-connector dropout signature

The slate-and-blue interface adapts to narrower windows by wrapping pin cards
and telemetry tiles, with vertical scrolling when needed. Sensor read failures
replace live readings with dashes and label the remaining plot as historical.
GPU telemetry remains independent of connector connectivity.

## How it works (the interesting part)

The IT8915FN sits at address `0x2b` on one of the NVIDIA I2C buses the GPU
driver exposes. Per-pin data lives at registers `0x80`–`0x97`: six pins ×
(2 bytes millivolts + 2 bytes milliamps), big-endian, in reverse pin order.

One empirical quirk worth knowing if you're doing something similar: the chip
does **not** answer raw `I2C_RDWR` transfers through NVIDIA's adapter — block
reads and byte-wise raw reads both return junk. It only responds to the
kernel's **SMBus read-byte-data ioctl** path (what `i2cget -y BUS 0x2b REG b`
does). So that is exactly what this tool does, 24 register reads per poll,
via `ioctl(fd, I2C_SMBUS, ...)` with no external dependencies.

## Install on Linux

Use a Linux desktop with **Python 3.10+, GTK4, PyGObject, and Cairo bindings**.
The installer is independent of the package manager and CPU architecture; your
distro must provide those dependencies. A graphical Wayland or X11 session is
needed to open the app. GNOME itself is not required.

### 1. Install dependencies for your distro family

Choose one group below. Commands target maintained releases that offer Python
3.10+ and GTK4; older releases may need an OS upgrade.

**Debian / Ubuntu family** — Debian, Ubuntu, Linux Mint, Pop!_OS, elementary OS

```sh
sudo apt update
sudo apt install python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0
```

**Fedora / RHEL family** — Fedora; RHEL, Rocky Linux, AlmaLinux, and CentOS Stream
where these packages are available in the enabled repositories

```sh
sudo dnf install python3 python3-gobject gtk4
```

**Arch family** — Arch Linux, EndeavourOS, Manjaro

```sh
sudo pacman -Syu python python-gobject python-cairo gtk4
```

**openSUSE family** — Tumbleweed and Leap releases with a suitable Python version

```sh
sudo zypper install python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-4_0 libgtk-4-1
```

Package names follow the [PyGObject installation guide](https://pygobject.gnome.org/getting_started.html).
If your distro packages multiple Python versions, install the matching bindings
and use that interpreter for every command below.

**Other distributions and immutable systems** — install the same dependencies
through your system's package manager or declarative configuration, then use the
shared installer below. NixOS, Alpine, Gentoo, and immutable Fedora variants do
not share the commands above. Kernel modules, NVIDIA drivers, and device access
must be configured on the host. Installation inside a container alone does not
provide access to the GPU's I2C device. These systems have not been tested.

### 2. Install the app

Download or clone this repository and open a terminal in its directory:

```sh
python3 install.py --check
python3 install.py
~/.local/bin/astral-pins --demo
```

Run the installer as your normal user. It checks the selected Python's GTK4 and
Cairo bindings, adds `~/.local/bin/astral-pins`, and creates an application-menu
entry. No pip, virtual environment, or administrator access is needed for this
step. The demo uses simulated readings and needs no GPU hardware.

The launcher runs **this checkout**, so keep it in its permanent location.
Pulling updates here updates the installed app; restart it to load the changes.
If you move the checkout or change Python environments, rerun `install.py`.
The desktop entry respects `XDG_DATA_HOME`; `--prefix /path` instead installs into
`/path/bin` and `/path/share/applications`. Add `~/.local/bin` to your `PATH` if
you want to type just `astral-pins`.

### 3. Enable live monitoring

Live readings require an **ASUS ROG Astral RTX 50-series card** (tested with the
RTX 5090 Astral), the NVIDIA proprietary driver exposing its I2C buses, and
permission to read `/dev/i2c-*`. Other GPUs can only use the demo. GPU telemetry
uses `nvidia-smi`; coolant temperature is optional and needs a `rog_ryujin` hwmon.

On systems using udev and the standard group-management tools:

```sh
sudo modprobe i2c-dev
sudo groupadd -f i2c
sudo install -m 644 contrib/60-i2c-group.rules /etc/udev/rules.d/60-i2c-group.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=i2c-dev
sudo usermod -aG i2c "$USER"
```

Log out and back in after changing group membership. On systems using
`systemd-modules-load`, persist the module across boots:

```sh
printf '%s\n' i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf
```

For another init system or declarative OS, configure `i2c-dev` loading and I2C
group access using that system's mechanism. Don't run the app with sudo.

```sh
~/.local/bin/astral-pins            # scan NVIDIA I2C buses
~/.local/bin/astral-pins --bus 7    # use a known bus
```

### Other ways to run

Run directly from the checkout with `python3 astral_pins.py --demo`, or install
an independent copy through pipx:

```sh
pipx install --system-site-packages --python /usr/bin/python3 .
astral-pins --demo
```

Choose the interpreter that has the distro bindings installed. The pipx copy
must be reinstalled to pick up source changes; the shared installer above keeps
the checkout as the source of truth.

### Uninstall

For the default installer paths:

```sh
rm ~/.local/bin/astral-pins
rm "${XDG_DATA_HOME:-$HOME/.local/share}/applications/astral-pins.desktop"
```

For a custom prefix, remove its `bin/astral-pins` and
`share/applications/astral-pins.desktop`. This leaves the checkout and hardware
configuration intact. If you installed with pipx, use `pipx uninstall astral-pins`.

## Development checks

The Linux installation workflow checks Ubuntu 22.04, Debian stable, Fedora,
Arch Linux, and openSUSE Tumbleweed. It also runs the GTK tests and builds a
Python wheel on Ubuntu. These checks do not exercise physical GPU hardware.

With GTK4 and a display available:

```
python3 -m unittest discover -s tests -v
python3 -m py_compile astral_pins.py
```

The integration checks use simulated samples for threshold boundaries, alarm
duration, dropout detection, stale-data recovery, history, and missing telemetry.
They skip when no GTK display is available. A headless GTK Broadway display can
be used instead:

```
gtk4-broadwayd -a 127.0.0.1 -p 8097 :7
# In another terminal:
GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 python3 -m unittest discover -s tests -v
```

## Safety

All chip access is read-only — the tool never writes a register. The alarm
thresholds mirror what ASUS uses (9.2 A per pin warning); they're constants
at the top of the script if you want different ones.

## Disclaimer

Not affiliated with or endorsed by ASUS, ITE, or NVIDIA. Register layout was
determined empirically on one card; other Astral models may differ. Use at
your own risk.

## License

[MIT](LICENSE)
