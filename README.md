# ClipCopy

A clipboard history manager for GNOME on Wayland.

Select or copy text and it is recorded to history. Press <kbd>Super</kbd>+<kbd>Shift</kbd>+<kbd>V</kbd> to open a searchable list, click an entry, and paste it — without hunting for what you copied five minutes ago.

```
Super+Shift+V   open history
type            filter
Enter / click   copy entry to clipboard, close
Esc             close
```

## Install

```bash
git clone https://github.com/Abhislit/clipcopy.git
cd clipcopy
./install.sh
```

No `sudo` required. The installer copies everything to `~/.local/share/clipcopy`, registers a systemd user service, a hotkey and a `.desktop` entry. `wl-copy`/`wl-paste` are obtained from your system, or unpacked from the distro package into ClipCopy's own directory, or built from source — whichever works.

Requires GNOME on Wayland, Python 3, and PyGObject with GTK 4:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0
```

## Commands

| Command | Does |
|---|---|
| `clipcopy show` | open the history window (same as the hotkey) |
| `clipcopy history` | print history to the terminal |
| `clipcopy on` / `off` / `toggle` | control the background service |
| `clipcopy log` | follow the service log |
| `clipcopy status` | service status |

## How it works

Selecting text sets the *primary* selection; <kbd>Ctrl</kbd>+<kbd>C</kbd> sets the *clipboard*. ClipCopy watches both and records whatever is new.

Reading happens **in-process** through GDK, every 200 ms. Nothing is spawned while idle — a helper process is created only at the moment content actually changes. Idle cost is roughly 0.2–0.4% of one core and ~50 MB RSS.

History is capped at 300 entries, deduplicated by content, and stored as JSON at `~/.local/share/clipcopy-data/history.json`. Re-copying something already stored moves it to the top rather than adding a duplicate. Pinned entries are exempt from the cap and survive "clear".

## Known limitations

These are consequences of the Wayland/Mutter clipboard model, not oversights:

- **A 1×1 helper window stays mapped.** GDK only reports clipboard content while a window is mapped. It may appear in the app grid or <kbd>Alt</kbd>+<kbd>Tab</kbd> as `clipcopy`, and it cannot be hidden — GTK 4 removed the window-hint API, and making it transparent breaks clipboard reading entirely. It never takes keyboard focus.
- **Very fast successive copies can be missed.** Mutter does not implement the `wlroots` `data-control` protocol, so `wl-paste --watch` is unavailable and polling is the only option. Two copies within one 200 ms window are recorded as one. The only true fix is a GNOME Shell extension listening to Mutter's internal `Meta.Selection` signal.
- **Re-copying the exact text already on the clipboard is ignored** — that is the deduplication behaviour described above.
- **Text only.** Images and rich content are not recorded.
- **Wayland only.** Under X11 the helper window is unnecessary, but the app is only tested on Wayland.

## Uninstall

```bash
systemctl --user disable --now clipcopy.service
rm -rf ~/.local/share/clipcopy ~/.local/share/clipcopy-data \
       ~/.config/systemd/user/clipcopy.service \
       ~/.config/gnome/keybindings/clipcopy.keybinding \
       ~/.local/share/applications/clipcopy.desktop \
       ~/.local/bin/clipcopy
gsettings reset org.gnome.settings-daemon.plugins.media-keys email
```

Your history is only ever stored locally in `~/.local/share/clipcopy-data/`. Nothing is uploaded anywhere.

## Tests

```bash
python3 tests/test_history.py   # store logic, no GTK needed
python3 tests/test_ui.py        # window behaviour, needs a Wayland session
```

## License

MIT
