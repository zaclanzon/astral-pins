# Shared by setup.sh and CI. Keep distro package names here, not in workflows.
# Reviewed against distro packages on 2026-09-06; scheduled CI checks weekly.
linux_family() {
    for candidate in "$1" $2; do
        case "$candidate" in
            debian|ubuntu|linuxmint|pop|elementary) printf '%s\n' debian; return ;;
            fedora|rhel|centos|rocky|almalinux) printf '%s\n' fedora; return ;;
            arch|manjaro|endeavouros) printf '%s\n' arch; return ;;
            opensuse*|suse|sles) printf '%s\n' suse; return ;;
        esac
    done
    return 1
}

linux_packages() {
    case "$1" in
        debian) printf '%s\n' 'python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 kmod udev passwd' ;;
        # The full gobject package provides the GI/Cairo bridge; base alone is
        # insufficient for DrawingArea callbacks. Keep Cairo explicit too.
        fedora) printf '%s\n' 'python3 python3-gobject python3-cairo gtk4 kmod systemd-udev shadow-utils' ;;
        arch) printf '%s\n' 'python python-gobject python-cairo gtk4 kmod systemd shadow' ;;
        suse) printf '%s\n' 'python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-4_0 libgtk-4-1 kmod udev shadow' ;;
        *) return 1 ;;
    esac
}
