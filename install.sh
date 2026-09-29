#!/bin/bash
#
# ClipCopy installer.
#
# Installs to ~/.local/share/clipcopy, registers a systemd user service, a
# global hotkey and a .desktop entry. Does not require root: the wl-clipboard
# helper binaries are unpacked into ClipCopy's own directory rather than
# installed system-wide.
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${CLIPCOPY_PREFIX:-$HOME/.local/share/clipcopy}"
DATA_DIR="$HOME/.local/share/clipcopy-data"
BIN_DIR="$PREFIX/bin"
UNIT_DIR="$HOME/.config/systemd/user"
KEY_DIR="$HOME/.config/gnome/keybindings"
APP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
KEYBIND_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
# GNOME ships no schema key for user-defined shortcuts, so we attach to a
# media-key slot that is unbound on most laptops. Override with CLIPCOPY_KEY
# and CLIPCOPY_KEYS if you would rather use a different one.
KEY="${CLIPCOPY_KEY:-email}"
KEYS="${CLIPCOPY_KEYS:-<Super><Shift>v}"

say() { printf '  %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- checks

command -v python3 >/dev/null || die "python3 is required"
python3 - <<'PY' || die "PyGObject with GTK 4 is required (sudo apt install python3-gi gir1.2-gtk-4.0)"
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: F401
PY

if [ "${XDG_SESSION_TYPE:-}" != "wayland" ]; then
    say "warning: this tool is built for Wayland; session type is '${XDG_SESSION_TYPE:-unknown}'"
fi

# ------------------------------------------------------- wl-clipboard deps

have_helpers() { [ -x "$BIN_DIR/wl-copy" ] && [ -x "$BIN_DIR/wl-paste" ]; }

# A system wl-copy is only safe to copy if it lives in a normal system
# directory. Skipping snap/flatpak paths avoids vendoring a binary that
# disappears when its package is removed, or one that carries its own
# library assumptions.
system_wl_copy() {
    local candidate
    for candidate in /usr/bin/wl-copy /usr/local/bin/wl-copy /bin/wl-copy; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

fetch_helpers() {
    local sys_copy
    if sys_copy="$(system_wl_copy)"; then
        say "using system wl-clipboard ($sys_copy)"
        cp "$sys_copy" "$BIN_DIR/wl-copy"
        cp "${sys_copy%/wl-copy}/wl-paste" "$BIN_DIR/wl-paste"
        chmod +x "$BIN_DIR"/wl-*
        have_helpers && return
        say "system wl-paste missing alongside wl-copy; continuing"
    fi

    if command -v apt-get >/dev/null; then
        say "fetching wl-clipboard package..."
        tmp="$(mktemp -d)"
        ( cd "$tmp" && apt-get download wl-clipboard >/dev/null 2>&1 ) || true
        deb="$(find "$tmp" -name 'wl-clipboard*.deb' | head -1)"
        if [ -n "$deb" ] && command -v dpkg-deb >/dev/null; then
            dpkg-deb -x "$deb" "$tmp/x"
            cp "$tmp/x/usr/bin/wl-copy" "$tmp/x/usr/bin/wl-paste" "$BIN_DIR/" 2>/dev/null || true
            chmod +x "$BIN_DIR"/wl-* 2>/dev/null || true
            rm -rf "$tmp"
            have_helpers && { say "unpacked wl-clipboard locally"; return; }
            say "could not unpack; falling back to a source build"
        else
            rm -rf "$tmp"
        fi
    fi

    say "building wl-clipboard from source (needs gcc, make, libwayland-dev)..."
    tmp="$(mktemp -d)"
    if git clone --depth 1 https://github.com/bugaevc/wl-clipboard "$tmp/wlc" \
        && make -C "$tmp/wlc" >/dev/null 2>&1; then
        cp "$tmp/wlc/wl-copy" "$tmp/wlc/wl-paste" "$BIN_DIR/"
        chmod +x "$BIN_DIR"/wl-*
        rm -rf "$tmp"
        have_helpers && { say "built wl-clipboard"; return; }
    fi
    rm -rf "$tmp"
    die "could not obtain wl-copy/wl-paste. Install them with: sudo apt install wl-clipboard, then re-run this script."
}

# ---------------------------------------------------------------- install

echo "Installing ClipCopy to $PREFIX"
mkdir -p "$BIN_DIR" "$DATA_DIR" "$UNIT_DIR" "$KEY_DIR" "$APP_DIR"

cp "$SRC_DIR/clipcopy.py" "$SRC_DIR/history.py" "$PREFIX/"
cp "$SRC_DIR/bin/clipcopy-toggle" "$BIN_DIR/"
chmod +x "$BIN_DIR/clipcopy-toggle"

if have_helpers; then
    say "wl-copy/wl-paste already present"
else
    fetch_helpers
fi

sed "s|\$(cd \"\$(dirname \"\$0\")/\.\.\" && pwd)|$PREFIX|" \
    "$SRC_DIR/bin/clipcopy-toggle" > "$BIN_DIR/clipcopy-toggle"
chmod +x "$BIN_DIR/clipcopy-toggle"

cat > "$UNIT_DIR/clipcopy.service" <<EOF
[Unit]
Description=ClipCopy clipboard history
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/.local/share/clipcopy/clipcopy.py
Restart=on-failure
RestartSec=5
# GDK negotiates Wayland clipboard access over the session bus; without these
# the clipboard always reads back as empty.
Environment=GDK_BACKEND=wayland
Environment=XDG_RUNTIME_DIR=%t
Environment=WAYLAND_DISPLAY=$WAYLAND_DISPLAY
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus
Nice=5

[Install]
WantedBy=default.target
EOF

cat > "$KEY_DIR/clipcopy.keybinding" <<EOF
[Desktop Action clipcopy]
Name=Open ClipCopy
Exec=$BIN_DIR/clipcopy-toggle
EOF

cat > "$APP_DIR/clipcopy.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ClipCopy
GenericName=Clipboard Manager
Comment=Search and reuse your clipboard history
Exec=$BIN_DIR/clipcopy-toggle
Icon=edit-paste
Terminal=false
Categories=GTK;Utility;
Keywords=clipboard;copy;paste;history;manager;
StartupNotify=false
EOF

cat > "$HOME/.local/bin/clipcopy" <<EOF
#!/bin/bash
# Control the ClipCopy clipboard history daemon.
PREFIX="$PREFIX"
case "\${1:-toggle}" in
  on|start)   systemctl --user start clipcopy.service; echo "clipcopy: ON (watching clipboard)" ;;
  off|stop)   systemctl --user stop clipcopy.service;  echo "clipcopy: OFF" ;;
  toggle)
    if systemctl --user is-active --quiet clipcopy.service; then
      systemctl --user stop clipcopy.service; echo "clipcopy: OFF"
    else
      systemctl --user start clipcopy.service; echo "clipcopy: ON (watching clipboard)"
    fi ;;
  show|window) "$BIN_DIR/clipcopy-toggle" && echo "clipcopy: window toggled (hotkey: $KEYS)" ;;
  log)        journalctl --user -u clipcopy.service -f ;;
  status)     systemctl --user status clipcopy.service --no-pager ;;
  history)
    python3 -c "
import json, os, time
p = os.path.join('$DATA_DIR', 'history.json')
try: data = json.load(open(p))
except OSError as e: print('no history yet (%s)' % e); raise SystemExit(0)
for i, e in enumerate(data, 1):
    t = time.strftime('%Y-%m-%d %H:%M', time.localtime(e.get('added_at', 0)))
    print('%3d  %s  %s' % (i, t, ' '.join(e['text'].split())[:70]))
print('\n%d entries' % len(data))
" ;;
  *) echo "usage: clipcopy [on|off|toggle|show|log|status|history]"; exit 1 ;;
esac
EOF
chmod +x "$HOME/.local/bin/clipcopy"

systemctl --user daemon-reload
systemctl --user enable --now clipcopy.service

# Hotkey: only bind a slot that GNOME's schema already knows about.
# gsettings can transiently fail while the session bus is settling just after
# a service start, so retry briefly rather than silently skipping the binding.
hotkey_bound=0
for _ in 1 2 3 4 5; do
    if keys="$(gsettings list-keys "$KEYBIND_SCHEMA" 2>/dev/null)" && \
       printf '%s\n' "$keys" | grep -qx "$KEY"; then
        gsettings set "$KEYBIND_SCHEMA" "$KEY" "['$KEYS']" && hotkey_bound=1
        break
    fi
    sleep 0.4
done

if [ "$hotkey_bound" -eq 1 ]; then
    say "hotkey $KEYS bound to '$KEY'"
else
    say "could not bind a hotkey: no '$KEY' key in $KEYBIND_SCHEMA"
    say "run '$BIN_DIR/clipcopy-toggle' directly, or bind a key in Settings > Keyboard"
fi

echo
echo "Installed."
echo "  open history : $KEYS   (or: clipcopy show)"
echo "  control      : clipcopy [on|off|toggle|show|log|status|history]"
echo "  history file : $DATA_DIR/history.json"
echo
echo "Note: ClipCopy keeps a 1x1 helper window mapped, because GDK cannot read"
echo "the Wayland clipboard without one. It may appear in the app grid or"
echo "Alt+Tab as 'clipcopy'. It never takes keyboard focus."
