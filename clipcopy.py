#!/usr/bin/env python3
"""ClipCopy - clipboard history for GNOME/Wayland.

Runs as a tray-less background app:
  * watches the clipboard and records history to disk
  * opens a searchable window on a global hotkey (Super+Shift+V)
  * click an entry to put it back on the clipboard

Monitoring is done in-process through GDK. A subprocess is spawned only when
the clipboard content actually changes, so the app is nearly free when idle.

Known limitation: Mutter does not implement the wlroots data-control protocol,
so there is no way to observe clipboard changes other than polling, and a
clipboard that changes twice within one poll interval is recorded once. The
interval is kept short (200 ms) to make that rare in practice.
"""
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("GDK_BACKEND", "wayland")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from history import History  # noqa: E402

APP_ID = "io.github.clipcopy"
BIN = os.path.join(BASE, "bin")
WL_COPY = os.path.join(BIN, "wl-copy")
DATA_DIR = os.path.join(GLib.get_user_data_dir(), "clipcopy-data")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")

POLL_MS = 200
STALL_LIMIT = 8
SAVE_DEBOUNCE_S = 2.0

_state = {
    "history": None,
    "clipboard": None,
    "primary": None,
    "last_clip": None,
    "last_primary": None,
    "reading": False,
    "stalled": 0,
    "owner": None,
    "save_timer": 0,
    "win": None,
    "mon": None,
    "starting": True,
}


def log(msg):
    sys.stderr.write("clipcopy: %s\n" % msg)
    sys.stderr.flush()


# ---------------------------------------------------------------- clipboard

def _publish(text, kind):
    """Write text to the clipboard, taking ownership until the next change."""
    proc = subprocess.Popen(
        [WL_COPY, "--type", "text/plain;charset=utf-8"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.communicate(text.encode("utf-8"))
    old = _state["owner"]
    _state["owner"] = proc
    if old is not None and old.poll() is None:
        try:
            old.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            old.kill()
            old.wait()


def schedule_save():
    if _state["save_timer"]:
        return
    _state["save_timer"] = GLib.timeout_add_seconds(
        int(SAVE_DEBOUNCE_S), _do_save)
    if not _state["save_timer"]:
        log("warning: could not schedule history save")


def _do_save():
    _state["save_timer"] = 0
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _state["history"].save(HISTORY_PATH)
    except OSError as e:
        log("save failed: %s" % e)
    return GLib.SOURCE_REMOVE


def _record(text, kind):
    hist = _state["history"]
    before = hist.items[0] if len(hist) else None
    item = hist.add(text)
    if item is None:
        return
    if _state["starting"]:
        # Do not log whatever was already on the clipboard at startup as if
        # the user had just copied it.
        return
    # A repeated copy of something already stored still counts: add() has
    # just promoted it to the top with a fresh timestamp, and that new
    # ordering has to be written to disk.
    promoted = before is not item
    if item.logged and not promoted:
        return
    item.logged = True
    schedule_save()
    preview = text.strip().replace("\n", " ")[:50]
    log("%s: %d chars %r" % (kind, len(text), preview))
    if _state["win"] is not None and _state["win"].get_visible():
        _state["win"].refresh()


def on_clip_read(clip, result, _kind=None):
    _state["reading"] = False
    _state["stalled"] = 0
    try:
        text = clip.read_text_finish(result)
    except GLib.Error:
        return None
    if not text or not text.strip():
        return None
    if _kind == "clip":
        if text == _state["last_clip"]:
            return None
        _state["last_clip"] = text
        _record(text, "clip")
    else:
        if text == _state["last_primary"]:
            return None
        _state["last_primary"] = text
        _record(text, "select")
    return None


def poll():
    if _state["reading"]:
        _state["stalled"] += 1
        if _state["stalled"] >= STALL_LIMIT:
            log("clipboard read stalled; resetting")
            _state["reading"] = False
            _state["stalled"] = 0
        return True
    _state["reading"] = True
    _state["stalled"] = 0
    # Primary selection first: selecting text is the common case, and it is
    # what the old auto-copy behaviour keyed off.
    _state["primary"].read_text_async(
        None, lambda c, r: on_clip_read(c, r, "primary"))
    _state["clipboard"].read_text_async(
        None, lambda c, r: on_clip_read(c, r, "clip"))
    return True


def copy_entry(item):
    """Put a history entry back on the clipboard, primary and clipboard."""
    _publish(item.text, "clipboard")
    _state["last_clip"] = item.text
    # Also set the primary selection so a middle-click paste works.
    try:
        p = subprocess.Popen(
            [WL_COPY, "--primary", "--foreground", "--type",
             "text/plain;charset=utf-8"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        p.stdin.write(item.text.encode("utf-8"))
        try:
            p.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        _state["primary_owner"] = p
    except OSError as e:
        log("primary set failed: %s" % e)
    log("restored %d chars" % len(item.text))


# ---------------------------------------------------------------- window UI

class ClipWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(
            application=app, title="ClipCopy",
            modal=False, resizable=False,
        )
        self.set_default_size(560, 460)
        self.search_text = ""
        self.rows = []

        self.search = Gtk.SearchEntry()
        self.search.set_hexpand(True)
        self.search.set_placeholder_text("Search clipboard history")
        self.search.connect("search-changed", self.on_search)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.set_margin_top(10)
        header.set_margin_start(10)
        header.set_margin_end(10)
        header.append(self.search)

        clear_btn = Gtk.Button(icon_name="edit-clear-all-symbolic")
        clear_btn.set_tooltip_text("Clear unpinned history")
        clear_btn.connect("clicked", self.on_clear)
        header.append(clear_btn)

        pin_btn = Gtk.Button(icon_name="view-pin-symbolic")
        pin_btn.set_tooltip_text("Pin / unpin the selected entry")
        pin_btn.connect("clicked", self.on_pin)
        header.append(pin_btn)

        del_btn = Gtk.Button(icon_name="user-trash-symbolic")
        del_btn.set_tooltip_text("Delete the selected entry")
        del_btn.connect("clicked", self.on_delete)
        header.append(del_btn)

        self.store = Gtk.StringList()
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self.on_activate)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self.listbox)

        self.status = Gtk.Label(label="")
        self.status.add_css_class("dim-label")
        self.status.set_margin_top(6)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_margin_top(6)
        footer.set_margin_start(10)
        footer.set_margin_end(10)
        footer.set_margin_bottom(10)
        footer.append(self.status)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        footer.append(spacer)
        hint = Gtk.Label(label="Enter = copy    Esc = close")
        hint.add_css_class("dim-label")
        footer.append(hint)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(scroller)
        box.append(footer)
        self.set_child(box)

        self.connect("close-request", self.on_close)

    # -- helpers ------------------------------------------------------

    def preview(self, text):
        one = text.replace("\n", " ").replace("\t", " ").strip()
        one = " ".join(one.split())
        return one[:110] + ("..." if len(one) > 110 else "")

    def refresh(self):
        self.rebuild()

    def rebuild(self):
        hist = _state["history"]
        items = hist.search(self.search_text)
        self.rows = items

        child = self.listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.listbox.remove(child)
            child = nxt

        first_row = None
        for item in items:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(10)
            box.set_margin_end(10)

            label = Gtk.Label(label=self.preview(item.text))
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_hexpand(True)
            box.append(label)

            if item.pinned:
                pin = Gtk.Image.new_from_icon_name("view-pin-symbolic")
                box.append(pin)

            if "\n" in item.text:
                lines = item.text.count("\n") + 1
                multi = Gtk.Label(label="%d lines" % lines)
                multi.add_css_class("dim-label")
                box.append(multi)

            row.set_child(box)
            self.listbox.append(row)
            row.item = item
            if first_row is None:
                first_row = row

        self.status.set_text(
            "%d of %d entries" % (len(items), len(hist)))
        if first_row is not None:
            self.listbox.select_row(first_row)

    # -- actions ------------------------------------------------------

    def selected_item(self):
        row = self.listbox.get_selected_row()
        if row is None:
            return None
        return getattr(row, "item", None)

    def on_activate(self, _listbox, row):
        item = getattr(row, "item", None)
        if item is not None:
            copy_entry(item)
            self.close()

    def on_search(self, entry):
        self.search_text = entry.get_text()
        self.rebuild()

    def on_clear(self, _btn):
        _state["history"].clear(keep_pinned=True)
        schedule_save()
        self.rebuild()

    def on_delete(self, _btn):
        item = self.selected_item()
        if item is None:
            return
        _state["history"].remove(item)
        schedule_save()
        self.rebuild()

    def on_pin(self, _btn):
        item = self.selected_item()
        if item is None:
            return
        item.pinned = not item.pinned
        schedule_save()
        self.rebuild()

    def on_close(self, *_args):
        self.set_visible(False)
        return True

    def toggle(self):
        if self.get_visible():
            self.set_visible(False)
        else:
            self.search_text = ""
            self.search.set_text("")
            self.rebuild()
            self.present()
            self.search.grab_focus()


# ---------------------------------------------------------------- app

class ClipCopyApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.connect("activate", self.on_activate)

    def on_activate(self, _app):
        self.show_window()

    def show_window(self):
        if _state["win"] is None:
            _state["win"] = ClipWindow(self)
            _state["win"].refresh()
        _state["win"].toggle()

    def do_startup(self):
        Gtk.Application.do_startup(self)

        # Expose a D-Bus action so a global keybinding can raise the window
        # without a second process owning the application id.
        toggle = Gio.SimpleAction.new("toggle", None)
        toggle.connect("activate", lambda _a, _p: self.show_window())
        self.add_action(toggle)

        os.makedirs(DATA_DIR, exist_ok=True)
        hist = History()
        try:
            hist.load(HISTORY_PATH)
        except Exception as e:      # noqa: BLE001
            log("history load failed: %s" % e)
        _state["history"] = hist
        log("loaded %d history entries" % len(hist))

        # GDK only reports clipboard content while a window is mapped, so a
        # 1x1 helper window is kept. It must stay opaque: making it
        # transparent stops GDK from reading the clipboard at all.
        helper = Gtk.Window()
        helper.set_decorated(False)
        helper.set_resizable(False)
        helper.set_default_size(1, 1)
        helper.set_focusable(False)
        helper.set_title("clipcopy")
        helper.present()
        _state["mon"] = helper

        disp = Gdk.Display.get_default()
        _state["clipboard"] = disp.get_clipboard()
        _state["primary"] = disp.get_primary_clipboard()

        # Take a baseline so existing clipboard content is not recorded as a
        # fresh copy on startup.
        _state["last_clip"] = None
        _state["last_primary"] = None

        GLib.timeout_add(POLL_MS, poll)
        GLib.timeout_add(4000, mark_startup_done)
        log("running (poll %dms, in-process)" % POLL_MS)

    def do_shutdown(self):
        if _state["save_timer"]:
            GLib.source_remove(_state["save_timer"])
            _state["save_timer"] = 0
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            _state["history"].save(HISTORY_PATH)
        except (OSError, AttributeError):
            pass
        Gtk.Application.do_shutdown(self)


def mark_startup_done():
    _state["starting"] = False
    # Seed the baselines with whatever is currently selected/copied so it is
    # only recorded if the user copies it again.
    clip = _state["clipboard"]
    prim = _state["primary"]

    def seed(c, r, which):
        try:
            t = c.read_text_finish(r)
        except GLib.Error:
            t = None
        if which == "clip":
            _state["last_clip"] = t
        else:
            _state["last_primary"] = t
        return None

    clip.read_text_async(None, lambda c, r: seed(c, r, "clip"))
    prim.read_text_async(None, lambda c, r: seed(c, r, "prim"))
    return False


def main():
    app = ClipCopyApp()
    # Single instance: a second launch just raises the window.
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
