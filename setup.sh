#!/bin/sh
# Install distro dependencies, hardware access, and user launchers in one step.
set -eu

setup_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$setup_dir/scripts/linux-deps.sh"

fail() { printf 'Setup failed: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sh setup.sh [--demo | --dependencies-only] [--dry-run]
  Default: install dependencies, configure I2C access, and install user launchers.
  --demo               Skip hardware setup; install the simulated preview.
  --dependencies-only  Install/check distro packages only (also usable as root).
  --dry-run            Print the plan without changing anything or using sudo.
Run as your normal desktop user. System changes prompt for sudo when necessary.
Rerun this command to refresh dependencies from your distro's repositories.
EOF
}
mode=live
dry_run=no
for arg in "$@"; do
    case "$arg" in
        --demo) [ "$mode" = live ] || fail 'Choose only one setup mode.'; mode=demo ;;
        --dependencies-only) [ "$mode" = live ] || fail 'Choose only one setup mode.'; mode=dependencies ;;
        --dry-run) dry_run=yes ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "Unknown option: $arg" ;;
    esac
done
[ "$(uname -s)" = Linux ] || fail 'Linux is required.'
[ -r /etc/os-release ] || fail 'Cannot detect the distro: /etc/os-release is missing.'
# os-release is the host OS's trusted system configuration.
. /etc/os-release
family=$(linux_family "${ID:-}" "${ID_LIKE:-}") ||
    fail "Unsupported distro ${ID:-unknown}. Install Python 3.10+, GTK4, PyGObject and Cairo with your system tools, then run python3 install.py."
[ ! -e /run/ostree-booted ] && [ ! -d /ostree/repo ] ||
    fail 'Immutable/OSTree host detected. Install dependencies through your host configuration, then run python3 install.py on the host.'
packages=$(linux_packages "$family")
printf 'Distro: %s (%s family)\nPackages: %s\n' "${PRETTY_NAME:-$ID}" "$family" "$packages"
if [ "$family" = arch ]; then
    printf 'Arch setup includes a full system upgrade to avoid unsupported partial upgrades.\n'
fi
if [ "$mode" = live ]; then
    printf 'Live setup: load i2c-dev, install the I2C group rule, and add your user to i2c.\n'
fi
if [ "$dry_run" = yes ]; then
    printf 'Plan only. No changes made. Mode: %s\n' "$mode"
    exit 0
fi
if [ "$(id -u)" = 0 ] && [ "$mode" != dependencies ]; then
    fail 'Run sh setup.sh as your desktop user, without sudo. The script elevates only system steps.'
fi
as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else
        command -v sudo >/dev/null 2>&1 || fail 'sudo is required for system package and I2C setup.'
        sudo "$@"
    fi
}
# Tumbleweed mirrors are briefly inconsistent while they sync, so a single
# refresh can fail on a missing repodata file. Retry, then let install refresh.
zypper_refresh() {
    zr_attempt=1
    while [ "$zr_attempt" -le 3 ]; do
        as_root zypper --non-interactive --gpg-auto-import-keys refresh --force && return 0
        echo "zypper refresh failed (attempt $zr_attempt/3); retrying in 15s." >&2
        zr_attempt=$((zr_attempt + 1))
        sleep 15
    done
    echo 'zypper refresh kept failing; continuing so install can refresh itself.' >&2
    return 0
}
# Intentional word splitting: package lists are fixed identifiers in linux-deps.sh.
case "$family" in
    debian)
        as_root apt-get update
        as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y $packages ;;
    fedora) as_root dnf --refresh install -y $packages ;;
    arch) as_root pacman -Syu --noconfirm $packages ;;
    suse) zypper_refresh
        as_root zypper --non-interactive install $packages ;;
esac
setup_python=/usr/bin/python3
[ -x "$setup_python" ] || setup_python=$(command -v python3)
"$setup_python" "$setup_dir/install.py" --check
[ "$mode" != dependencies ] || exit 0

if [ "$mode" = live ]; then
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 ||
        fail 'Install and activate the NVIDIA driver using your distro tools, then rerun setup. Use --demo for a hardware-free preview.'
    as_root modprobe i2c-dev || fail 'Cannot load i2c-dev. Check that your kernel provides it.'
    as_root groupadd -f i2c
    as_root install -m 644 "$setup_dir/contrib/60-i2c-group.rules" /etc/udev/rules.d/60-i2c-group.rules
    as_root usermod -aG i2c "$(id -un)"
    as_root udevadm control --reload-rules
    as_root udevadm trigger --subsystem-match=i2c-dev
    if [ -d /run/systemd/system ]; then
        as_root install -d -m 755 /etc/modules-load.d
        printf '%s\n' i2c-dev | as_root tee /etc/modules-load.d/astral-pins.conf >/dev/null
    else
        printf 'Configure your init system to load i2c-dev at boot; automatic persistence supports systemd.\n'
    fi
fi
"$setup_python" "$setup_dir/install.py"
if [ "$mode" = live ]; then
    printf '\nSetup complete. Log out and back in for I2C group access, then open Astral Pins.\n'
else
    printf '\nSetup complete. Preview: ~/.local/bin/astral-pins --demo\n'
fi
printf 'Libraries receive updates through your distro package manager. Weekly CI checks track compatibility.\n'
