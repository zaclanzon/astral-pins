# astral-pins

Per-pin 12V-2x6 power monitoring for ASUS ROG Astral RTX 50-series cards on
Linux — a GTK4 panel that reads the card's onboard ITE **IT8915FN** monitoring
chip directly over I2C.

ASUS markets this hardware as *Power Detector+* and surfaces it in GPU Tweak
on Windows. This tool talks to the same chip from Linux, read-only, with no
driver and no vendor software.

![screenshot](screenshots/panel.png)

## What it shows

- Six per-pin gauges (volts / amps) with OK / warn / alarm color states
- A 2-minute rolling graph of per-pin current
- Total connector watts and amps
- GPU temperature, board power, utilization, and VRAM via `nvidia-smi`
- A red alert banner if any pin sustains more than 9.2 A (ASUS's own warning
  threshold) for 5+ seconds, or if a pin reads ~0 A while the connector is
  under load — the classic melted-connector dropout signature

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

## Requirements

- ASUS ROG Astral RTX 50-series card (tested: RTX 5090 Astral)
- NVIDIA proprietary driver (exposes the I2C buses; also used for `nvidia-smi`)
- Python 3.10+, GTK4, and PyGObject — stock on Ubuntu GNOME:

  ```
  sudo apt install python3-gi gir1.2-gtk-4.0
  ```

## Setup (one time)

The kernel I2C device interface must be loaded, and your user needs access
to `/dev/i2c-*`:

```
sudo modprobe i2c-dev
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf

sudo groupadd -f i2c
sudo cp contrib/60-i2c-group.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG i2c "$USER"   # then log out and back in
```

Don't run the panel with sudo; the group route is safer and works fine.

## Run

```
./astral_pins.py            # auto-scans NVIDIA I2C buses for the chip
./astral_pins.py --bus 7    # skip the scan if you know the bus
```

Or install it as a command:

```
pipx install --system-site-packages .
astral-pins
```

(`--system-site-packages` lets the venv see the distro's PyGObject/GTK
bindings instead of trying to build them from source.)

A desktop entry is included in `contrib/` if you want it in your launcher:

```
cp contrib/astral-pins.desktop ~/.local/share/applications/
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
