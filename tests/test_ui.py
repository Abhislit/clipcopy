#!/usr/bin/env python3
"""UI tests for the history window. Needs a Wayland session, runs headless-ish."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE)))

os.environ.setdefault("GDK_BACKEND", "wayland")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

import clipcopy  # noqa: E402
from history import History  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        fails.append(name)


def row_texts(win):
    out = []
    child = win.listbox.get_first_child()
    while child is not None:
        out.append(child.get_child().get_first_child().get_label())
        child = child.get_next_sibling()
    return out


def main():
    app = Gtk.Application(application_id="io.github.clipcopy.uitest",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", run)
    return app.run([])


def run(app):
    Gtk.init()
    hist = History()
    for t in ["first entry alpha", "second entry beta", "multi\nline\ngamma",
              "fourth with unique-token", "fifth é世界\U0001f389"]:
        hist.add(t)
    by_text = lambda t: [i for i in hist if i.text == t][0]  # noqa: E731
    by_text("multi\nline\ngamma").pinned = True
    clipcopy._state["history"] = hist

    win = clipcopy.ClipWindow(app)
    clipcopy._state["win"] = win
    win.refresh()

    check("window title", win.get_title() == "ClipCopy")
    check("rows built", len(win.rows) == 5, len(win.rows))
    check("status line", win.status.get_text() == "5 of 5 entries",
          win.status.get_text())

    win.search_text = "beta"
    win.rebuild()
    check("search filters", len(win.rows) == 1, len(win.rows))

    win.search_text = "GAMMA"
    win.rebuild()
    check("search case-insensitive", len(win.rows) == 1, len(win.rows))

    win.search_text = "zzzz-nothing"
    win.rebuild()
    check("no matches", len(win.rows) == 0)

    win.search_text = ""
    win.rebuild()
    check("pinned sorts first", row_texts(win)[0].startswith("multi"),
          row_texts(win)[:1])

    # select the 'fourth' row and pin it
    child = win.listbox.get_first_child()
    while child is not None:
        if child.item.text.startswith("fourth"):
            win.listbox.select_row(child)
            break
        child = child.get_next_sibling()
    sel = win.selected_item()
    check("selection works", sel is not None and sel.text.startswith("fourth"),
          sel)
    win.on_pin(None)
    check("pin toggles", by_text("fourth with unique-token").pinned is True)
    check("pin count now 2", sum(1 for i in hist if i.pinned) == 2)

    n0 = len(hist)
    # Select an unpinned row ("second entry beta") and delete that, so the
    # two pinned entries survive for the clear test below.
    child = win.listbox.get_first_child()
    while child is not None:
        if child.item.text.startswith("second"):
            win.listbox.select_row(child)
            break
        child = child.get_next_sibling()
    check("re-selected unpinned row",
          win.selected_item().text.startswith("second"),
          win.selected_item().text)
    win.on_delete(None)
    check("delete removes one", len(hist) == n0 - 1, len(hist))
    check("deleted item is gone",
          not any(i.text.startswith("second") for i in hist))

    win.on_clear(None)
    check("clear keeps both pinned", len(hist) == 2, len(hist))
    check("only pinned remain", all(i.pinned for i in hist),
          [i.text[:10] for i in hist])

    # window visibility toggle
    check("starts hidden", not win.get_visible())
    win.toggle()
    check("toggle shows", win.get_visible() and win.get_mapped())
    win.toggle()
    check("toggle hides", not win.get_visible())
    win.toggle()
    check("toggle shows again", win.get_visible())

    print()
    print("RESULT: %s (%d failures)" % ("PASS" if not fails else "FAIL", len(fails)))
    Gtk.Application.quit(app)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
