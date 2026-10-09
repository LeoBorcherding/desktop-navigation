---
name: desktop-navigation
description: Operate a native desktop window that has no DOM to query (Unsloth Desktop's Tauri webview, or any app) by reading screenshots and sending real OS mouse and keyboard input, with human-looking motion, pointing gestures, a cursor trail and screen video for recordings. Windows, macOS and Linux (X11). Use to check a UI change by eye, click through a flow, capture before/after stills, or perform on camera. For anything served on localhost, prefer a browser tool that can read the DOM.
---

# desktop-navigation

One entry point, `drive.py` (installed at `~/.claude/skills/desktop-navigation/`). Stdlib only; optional
tools per OS are listed below. Coordinates are screen pixels on Windows and Linux, points on macOS, and a
screenshot pixel equals a click coordinate (add the printed origin on a non-primary display).

| exit | meaning |
|---|---|
| 0 | ok |
| 1 | capture state error (already running, nothing running) |
| 2 | bad arguments, unsupported OS or feature, no screenshot tool |
| 3 | a person moved the mouse mid-motion; nothing more was sent. Stop and report, never retry |
| 4 | focus failed |
| 5 | permission or display missing (macOS Accessibility / Screen Recording, no X11 display) |
| 6 | the foreground window is not the target; nothing was sent |

## Operating loop
1. If a screen-claim mechanism exists, claim the screen first and release it after. If someone is using
   the machine, don't drive.
2. `python drive.py pointer focus --title "Unsloth"` brings the window forward (the `unsloth-studio` process
   wins over a browser tab with the same title; `--prefer` changes that) and remembers it in
   `<temp>/desktop-navigation/target.json`. Exit 4 means stop.
3. `python drive.py glow start` when taking control (a 1.4 s rainbow edge flash), `glow stop` when handing
   back. Never mid-run. `DNAV_GLOW=0` turns it off.
4. `python drive.py shot --out <png>`, read the image, act, shoot again to confirm. Chain predictable steps
   with short sleeps but always end on a screenshot. Copy proof shots to named files.

## Foreground guard
Every input action checks, right before its first button, key or wheel event, that the focused target is
still in front (pure moves and non-pressing gestures check once before moving). Tours re-check between
steps, `keys` before each chord, typing every ~50 characters. A mismatch sends nothing more, releases any
held button and exits 6 with `foreground changed: expected <target>, found <title>`.
- Default: the window from the last `pointer focus`, if under 30 minutes old and still open.
- `--title TEXT`: instead require the foreground title to contain TEXT.
- `--no-guard`: per call only, for desktop-wide targets such as the taskbar. There is no env default.

## Commands
```bash
python drive.py shot [--out PNG] [--display N]          # prints "<path> WxH origin X,Y"
python drive.py pointer pos | front | perms              # read-only: cursor, foreground window, permissions
python drive.py pointer click|rclick|mclick|move X Y
python drive.py pointer scroll X Y --delta -360          # 120 per notch, negative scrolls down, eased stream
python drive.py pointer drag X Y --to-x X2 --to-y Y2 [--back]
python drive.py pointer hover|underline|circle|highlight X Y --w W --h H   # or --r R
python drive.py pointer tour --steps "circle 300 200 120 30; hover 600 220; click 640 400" [--keep-order]
python drive.py pointer keys --keys "^a{ESC}"            # Windows: SendKeys syntax
python drive.py pointer keys --keys "cmd+a esc shift+tab" # macOS / Linux: chords (cmd = Ctrl on Linux)
python drive.py pointer window --title "Discord" --state min|restore|max
python drive.py type --text "a prompt" [--speed slow|medium|fast] [--no-enter]
python drive.py type --keycodes --text "abc" [--expect "Remote"] [--keys "tab"]   # macOS remote desktops
python drive.py type --release-mods
python drive.py glow start|stop
python drive.py capture start [--video] [--trail] [--name N] | stop | clear
```
- Motion: `--instant`, `--speed slow|medium|fast`, `--natural`, `--record`, `--hold MS`, `--side right|below`,
  `--cursor PX`. Env overrides use the `DNAV_` prefix (`DNAV_ENV_PREFIX` changes it): `SPEED`, `NATURAL`,
  `RECORD`, `CURSOR`, `GLIDE=0` (= instant), `TYPE_SPEED`, `GLOW=0`.
- `--record` turns on natural motion, defaults to slow, pauses around clicks and assumes an 80 px cursor.
- Gestures place the tip so the recorded cursor body sits beside the label. `circle` falls back to
  underline plus hover when it would leave the screen. `highlight` is a real drag selection.
- `tour` keeps clicks in place and reorders the gestures between them for the shortest route, printed
  before moving. Each gesture aims its exit at the next step.
- `drag` moves with the button held (real drag events) and always releases, even on exit 3.
- Typing sends a line break as Shift+Enter so a chat box does not submit early, and Enter at the end
  unless `--no-enter`. `--keycodes` sends one physical US key per character for remote-desktop clients;
  a character with no US key is exit 2 before anything is typed.
- `capture start` runs a cursor trail overlay (line, speed beads, click rings), screen video and a
  keep-awake helper, detached. `capture stop` saves the trail PNG and video under
  `<temp>/desktop-navigation/capture/` and prints each path. `capture clear` wipes the trail.

## Per-OS support

| feature | Windows | macOS | Linux (X11) |
|---|---|---|---|
| input, gestures, tour, drag, eased scroll, exit 3 | user32 | CoreGraphics | XTest |
| screenshots | Pillow or GDI | `screencapture` + `sips` | scrot, maim, import, gnome-screenshot or grim (first found) |
| focus by title, exit 4 | yes | System Events | xdotool (verified) or wmctrl (unverified) |
| window min / restore / max | yes | System Events | xdotool (min), wmctrl (max, restore) |
| foreground guard, exit 6 | HWND | PID + window number | `_NET_ACTIVE_WINDOW` id |
| keys syntax | SendKeys | chords | chords, cmd and ctrl both Control |
| Unicode typing | yes | yes | xdotool; without it ASCII only |
| `--keycodes` typing | no | yes | no (xdotool types Unicode) |
| glow, trail overlay | layered windows | osascript JXA | not supported (exit 2) |
| capture video | ffmpeg `gdigrab` if on PATH | `screencapture -v` | ffmpeg `x11grab` if on PATH |
| keep-awake | SetThreadExecutionState | caffeinate | systemd-inhibit if present |
| per-monitor screenshot | `--display N` | `--display N` | no, the root window spans every monitor |

Requirements: Windows nothing extra (Pillow, ffmpeg optional). macOS: grant Accessibility and Screen
Recording to the process that posts events (`drive.py perms --ask`); without Accessibility events are
dropped silently, hence exit 5. Linux: an X11 session with libX11 and libXtst; Wayland-native windows
cannot be driven.

## Pitfalls
- A background process asking Windows for the foreground is often ignored while someone types elsewhere,
  and input then lands in the wrong app. That is what the guard is for; still confirm with a screenshot.
- Clicking into a console window starts selection mode and freezes its output until Esc. Menus and
  popovers close when focus moves, so open and use them without switching windows.
- Two quick presses on a window's top edge maximize it vertically: space clicks apart.
- In a Tauri window the edge resize zone overlaps the outer pixels of a right-edge scrollbar: grab the
  thumb near its inner side. `drag --back` undoes a drag that would leave something under a title bar.
- Screenshots taken while the trail is up include it: `capture clear` first.
- On camera: never jump (`--instant` is for setup only); point with underline or circle rather than a still
  hover; keep clicks at least 1.5 s apart; hover a beat before clicking after a circle; scroll content into
  view before pointing. Auto-zoom recorders zoom on clicks and sometimes on pauses.
- Measure the recorded cursor height once and pass `--cursor` if gestures sit on top of labels.
- macOS: the process needing the grants may be a versioned helper inside the agent app, and it loses them
  on update. Re-grant, then restart the host.
- Linux: under Wayland the display can open through XWayland and input "succeeds" while only XWayland
  windows react. Log into an X11 session instead.
- Never type credentials or click through a sign-in or a security confirmation; hand those to the user.
