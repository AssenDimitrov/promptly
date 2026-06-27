# Traffic Light 🚦

A tiny, **movable, always-on-top** status light for **macOS & Linux**. Three lamps
(red / yellow / green) with a glossy UI and a soft glow on the active lamp. It's
driven by a small **local HTTP server**, so *anything* that can run a shell command
or make an HTTP request can control it — Claude Code, shell scripts, CI, cron, etc.

Default meaning:

| Lamp | Meaning |
|------|---------|
| 🔴 red (solid) | busy / working |
| 🟡 yellow (flashing) | needs your input / prompting |
| 🟢 green (solid) | ready / idle |

…but the colors are just states — wire them to whatever you like.

---

## 1. Run it

```bash
pip install PySide6
python traffic_light.py                 # traffic light (default), parks top-right
python traffic_light.py --skin orb      # pick a skin (see "Skins" below)
python traffic_light.py --vertical      # classic vertical stoplight
python traffic_light.py --state red --port 7654
python traffic_light.py --scale 1.4 --attach-top   # large, locked to the top edge
```

### Skins

Same three states, different look. Switch any time via **right-click → Skin**, or
start with `--skin <name>`:

| Skin | `--skin` | Look |
|------|----------|------|
| Traffic light | `traffic` *(default)* | classic three lamps |
| Orb | `orb` | a single breathing orb |
| Spinner | `spinner` | indeterminate spinner while busy, a ✓ when ready |
| Breathing gradient | `gradient` | an ambient bar that breathes; nice "attached to top" |
| Tamagotchi | `tamagotchi` | a creature that works, calls you, and rests |

A skin is purely a *render strategy* over the state — the HTTP server, the `tl`
CLI, and your Claude Code hooks are identical for every skin.

- **Left-click** → jumps to the window where Claude is running (see §3).
- **Drag** (press and move) → repositions the light.
- **Right-click** → everything is configurable here:
  - **Orientation** → Horizontal / Vertical
  - **Size** → Small / Medium / Large / Extra large
  - **Opacity** → 100% / 85% / 70% / 50%
  - **Attach to top bar** → snaps flush to the top edge and locks it there
    (while attached, dragging only slides it left/right along the top)
  - **Always on top** → toggle floating above other windows
  - **Claude window** → *Focus now*, *Set target…*, and the current target
  - **Set lamp** / **Flash current lamp** (handy for testing), and **Quit**
- It floats above other windows and stays out of the dock/taskbar.

A click and a drag are told apart by movement, so a quick click jumps to Claude
while press-and-move still moves the widget.

> Linux note: needs a Qt platform plugin. On a normal desktop session it works out
> of the box; on Wayland you may want `QT_QPA_PLATFORM=wayland` (or `xcb` to force X11).

---

## 2. Control it (the extension point)

Any tool talks to it over `http://127.0.0.1:7654`. Simple path endpoints:

```bash
curl -s localhost:7654/red
curl -s 'localhost:7654/yellow?flash=1&label=Needs+your+input'
curl -s localhost:7654/green
curl -s localhost:7654/off
curl -s localhost:7654/state                       # read current state as JSON
curl -s 'localhost:7654/focus?app=iTerm'           # macOS: set click target
curl -s 'localhost:7654/focus?title=claude-code'   # Linux/X11: set click target
```

Or richer JSON via POST:

```bash
curl -s localhost:7654/state \
  -d '{"state":"yellow","flash":true,"label":"Running tests"}'
```

There's also a one-liner helper, `tl`:

```bash
chmod +x tl && cp tl /usr/local/bin/   # install once
tl red
tl yellow --flash --label "Waiting on you"
tl green
```

That HTTP endpoint is the whole extensibility story: **GitHub Actions, a deploy
script, a long-running job, a Slack bot — any of them can flip the light** with one
line. Red while it works, yellow when it needs you, green when it's done.

---

## 3. Connect it to Claude Code

Claude Code fires lifecycle **hooks** you can map straight onto the light. Open
`claude-code-settings.json`, copy its `hooks` block into either:

- `~/.claude/settings.json` — applies to every project, or
- `<project>/.claude/settings.json` — just this repo.

The mapping:

| Claude Code event | Fires when… | Light |
|---|---|---|
| `SessionStart` | a session begins | 🟢 green |
| `UserPromptSubmit` | you send a prompt | 🔴 red (working) |
| `PreToolUse` | it's about to run a tool | 🔴 red (working) |
| `Notification` | it needs input / a permission | 🟡 yellow flashing |
| `Stop` | it finishes the turn | 🟢 green (ready) |
| `SessionEnd` | the session ends | ⚫ off |

Each hook is just `curl -s -m 1 localhost:7654/<color> || true`, run with
`async: true` — so if the widget isn't running, Claude Code is never blocked or
slowed. Start the widget, start Claude Code, and the light tracks it live.

### Left-click → jump to where Claude is running

Left-clicking the light raises the window Claude Code lives in. The `SessionStart`
hook in `claude-code-settings.json` runs **`register-claude-window.sh`**, which
tells the light what to focus:

- **macOS** — focuses the terminal app by name (`$TERM_PROGRAM` → Terminal, iTerm,
  Visual Studio Code, Ghostty, …) via AppleScript. Works out of the box.
- **Linux/X11** — focuses a window by matching its title with `wmctrl`/`xdotool`
  (`apt install wmctrl` or `xdotool`). If your terminal has no useful title, set
  one in your shell rc with `printf '\033]0;claude-code\007'` and
  `export CLAUDE_WINDOW_TITLE=claude-code`. **Wayland** generally blocks one app
  from focusing another's window — there it may not work.

Edit `/path/to/trafficlight` in the SessionStart hook to point at this folder.
You can also set the target by hand any time: **right-click → Claude window → Set
target…** (enter an app name on macOS, a title on Linux, or prefix with `!` for a
raw shell command). Test it with **Claude window → Focus now**.

---

## 4. How it's built (so you can extend it)

```
        any tool (Claude Code hook, script, CI…)
                       │  HTTP GET/POST  →  /red /yellow /green /off /state
                       ▼
   ┌──────────────────────────────────────────────┐
   │  traffic_light.py                             │
   │  ┌────────────┐   Qt signal   ┌────────────┐  │
   │  │ HTTP server│ ────────────► │  widget    │  │
   │  │ (thread)   │  (thread-safe)│  (GUI)     │  │
   │  └────────────┘               └────────────┘  │
   └──────────────────────────────────────────────┘
```

- The HTTP server runs on a background thread and hands updates to the GUI thread
  through a Qt signal (`Bridge.apply`) — the safe way to touch the UI from another
  thread.
- Adding a new trigger = make something call the endpoint. No app changes needed.
- Adding a new *look* = add a `Skin` subclass (`size` + `paint`, optionally
  `animating`) and list it in `SKIN_LIST`. The control layer is untouched.
- Adding a new *state* (e.g. a 4th lamp) = extend `VALID_STATES`, then handle it
  in the skins you care about.

> macOS note: the panel reaches the underlying `NSWindow` (via `ctypes`/`libobjc`,
> no extra dependency) so **Always on top** keeps it above other apps, visible
> across every Space/desktop, and over fullscreen apps — none of which Qt's
> `Qt.Tool` window does on its own.

---

## Other stacks (if you'd rather)

This is built in **PySide6 (Qt)** because it's one pip-install, one file, no build
step, and looks good on both OSes. If you specifically want a **menu-bar / tray
icon** that changes color, or web-tech (HTML/CSS) styling, the same HTTP-server
design ports cleanly to:

- **Electron** — nicest CSS animations, `Tray` API for a real menubar icon (heavier ~150 MB).
- **Tauri** (Rust + webview) — small bundle, system-tray support, web-tech UI.
- **rumps** — dead simple macOS menu-bar apps, but macOS-only.

Keep the local-HTTP control layer and your Claude Code hooks won't change.
# promptly
