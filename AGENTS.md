## Linux dependency maintenance

- Keep distro dependencies in `scripts/linux-deps.sh`; setup and CI must use the same list.
- Check current distro package names and required GTK/PyGObject/Cairo bindings when changing installation support. Fedora needs the full `python3-gobject` package for the GI/Cairo bridge, not just `python3-gobject-base`.
- Run the Linux installation matrix after dependency changes, including the minimum supported Python release. The weekly scheduled workflow opens an issue on compatibility failures; investigate and fix the failing distro before closing it.
- Keep setup repeatable, preserve the checkout as the application source, and keep user launchers unprivileged. Test setup without changing the developer machine's drivers or I2C configuration.
- Dependabot tracks GitHub Actions updates; distro libraries are maintained through package repositories and the scheduled installation matrix, not pip pins.
