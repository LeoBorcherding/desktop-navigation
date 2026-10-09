"""Linux backend: X11 + XTest over ctypes; xdotool / wmctrl when present; screenshot tools on PATH.

X11 sessions only. Under Wayland the display may open through XWayland, but only XWayland windows react.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

from core import keymap, procs
from core.exits import ARGS, PERMS, Unsupported

if not sys.platform.startswith("linux"):
    raise ImportError("the Linux backend only loads on Linux")

NAME = "linux"
KEYS_SYNTAX = "chords"
WHEEL_UNIT = 40
NO_X = "needs an X11 session: log into an X11 session (not Wayland) with DISPLAY set and libX11/libXtst installed"
SHOT_TOOLS = ("scrot", "maim", "import", "gnome-screenshot", "grim")

X = Xtst = None
display = None
root = 0


def _load(name: str, soname: str):
    return ctypes.CDLL(ctypes.util.find_library(name) or soname)


def init():
    global X, Xtst, display, root
    try:
        X = _load("X11", "libX11.so.6")
        Xtst = _load("Xtst", "libXtst.so.6")
    except OSError:
        return
    ul, vp, i = ctypes.c_ulong, ctypes.c_void_p, ctypes.c_int
    X.XOpenDisplay.restype = vp
    X.XOpenDisplay.argtypes = [ctypes.c_char_p]
    X.XDefaultRootWindow.restype = ul
    X.XDefaultRootWindow.argtypes = [vp]
    X.XDefaultScreen.argtypes = [vp]
    X.XDisplayWidth.argtypes = [vp, i]
    X.XDisplayHeight.argtypes = [vp, i]
    X.XQueryPointer.argtypes = [vp, ul, ctypes.POINTER(ul), ctypes.POINTER(ul)] + [ctypes.POINTER(i)] * 4 + \
        [ctypes.POINTER(ctypes.c_uint)]
    X.XFlush.argtypes = [vp]
    X.XStringToKeysym.restype = ul
    X.XStringToKeysym.argtypes = [ctypes.c_char_p]
    X.XKeysymToKeycode.restype = ctypes.c_ubyte
    X.XKeysymToKeycode.argtypes = [vp, ul]
    X.XkbKeycodeToKeysym.restype = ul
    X.XkbKeycodeToKeysym.argtypes = [vp, ctypes.c_ubyte, i, i]
    X.XInternAtom.restype = ul
    X.XInternAtom.argtypes = [vp, ctypes.c_char_p, i]
    X.XGetWindowProperty.argtypes = [vp, ul, ul, ctypes.c_long, ctypes.c_long, i, ul, ctypes.POINTER(ul),
                                     ctypes.POINTER(i), ctypes.POINTER(ul), ctypes.POINTER(ul),
                                     ctypes.POINTER(ctypes.c_void_p)]
    X.XFree.argtypes = [vp]
    Xtst.XTestFakeMotionEvent.argtypes = [vp, i, i, i, ul]
    Xtst.XTestFakeButtonEvent.argtypes = [vp, ctypes.c_uint, i, ul]
    Xtst.XTestFakeKeyEvent.argtypes = [vp, ctypes.c_uint, i, ul]
    display = X.XOpenDisplay(None)
    if display:
        root = X.XDefaultRootWindow(display)


def input_ok() -> bool:
    return bool(display)


def _need_x():
    if not display:
        raise Unsupported(NO_X, PERMS)


def _size() -> tuple[int, int]:
    scr = X.XDefaultScreen(display)
    return X.XDisplayWidth(display, scr), X.XDisplayHeight(display, scr)


def displays():
    _need_x()
    w, h = _size()
    return [(0, 0, w, h)]


def virtual_screen():
    return displays()[0]


def png_size(path: str) -> tuple[int, int] | None:
    try:
        head = Path(path).read_bytes()[:24]
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def shot_command(tool: str, out: str) -> list[str]:
    return {"scrot": ["scrot", "-o", out], "maim": ["maim", out], "import": ["import", "-window", "root", out],
            "gnome-screenshot": ["gnome-screenshot", "-f", out], "grim": ["grim", out]}[tool]


def shot(out: str, index: int = 0):
    if index:
        raise Unsupported("no per-monitor screenshots on Linux: the X root window spans every monitor", ARGS)
    if os.path.exists(out):
        os.remove(out)  # so a tool that silently writes nothing cannot leave an old image behind
    tool = next((t for t in SHOT_TOOLS if shutil.which(t)), None)
    if tool is None:
        raise Unsupported("no screenshot tool found: install one of scrot, maim, ImageMagick (import), "
                          "gnome-screenshot or grim", ARGS)
    subprocess.run(shot_command(tool, out), capture_output=True)
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise Unsupported(f"{tool} wrote nothing (no display, or Wayland blocked it)", ARGS)
    size = png_size(out)
    return (0, 0, size[0], size[1]) if size else None


def get_pos():
    _need_x()
    r, c = ctypes.c_ulong(), ctypes.c_ulong()
    rx, ry, wx, wy, m = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_uint()
    X.XQueryPointer(display, root, ctypes.byref(r), ctypes.byref(c), ctypes.byref(rx), ctypes.byref(ry),
                    ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(m))
    return rx.value, ry.value


def set_pos(x, y):
    Xtst.XTestFakeMotionEvent(display, -1, int(x), int(y), 0)
    X.XFlush(display)


BUTTONS = {"left": 1, "middle": 2, "right": 3}


def button(which: str, down: bool):
    Xtst.XTestFakeButtonEvent(display, BUTTONS[which], int(down), 0)
    X.XFlush(display)


def wheel(units: int):
    lines = int(round(units / WHEEL_UNIT))
    btn = 4 if lines > 0 else 5  # positive is up, as on macOS
    for _ in range(abs(lines)):
        Xtst.XTestFakeButtonEvent(display, btn, 1, 0)
        Xtst.XTestFakeButtonEvent(display, btn, 0, 0)
    X.XFlush(display)


def timer(on: bool):
    pass


def _keycode(keysym_name: str) -> int:
    ks = X.XStringToKeysym(keysym_name.encode())
    return X.XKeysymToKeycode(display, ks) if ks else 0


def _key(name: str, down: bool):
    kc = _keycode(name)
    if kc:
        Xtst.XTestFakeKeyEvent(display, kc, int(down), 0)


def key_chord(mods: tuple[str, ...], code: int):
    _need_x()
    name = keymap.x_keysym_name(code)
    for m in mods:
        _key(keymap.X_MODS[m], True)
    _key(name, True)
    _key(name, False)
    for m in reversed(mods):
        _key(keymap.X_MODS[m], False)
    X.XFlush(display)


def release_mods():
    if not display:
        return
    for name in ("Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R"):
        _key(name, False)
    X.XFlush(display)


def _type_keymap(ch: str) -> bool:
    name = keymap.x_char_keysym(ch)
    if not name:
        return False
    ks = X.XStringToKeysym(name.encode())
    kc = X.XKeysymToKeycode(display, ks) if ks else 0
    if not kc:
        return False
    shift = X.XkbKeycodeToKeysym(display, kc, 0, 0) != ks
    if shift:
        _key("Shift_L", True)
    Xtst.XTestFakeKeyEvent(display, kc, 1, 0)
    Xtst.XTestFakeKeyEvent(display, kc, 0, 0)
    if shift:
        _key("Shift_L", False)
    X.XFlush(display)
    return True


def type_char(ch: str):
    _need_x()
    if ch == "\n":
        key_chord(("shift",), 36)
    elif ch != "\r" and not _type_keymap(ch) and shutil.which("xdotool"):
        subprocess.run(["xdotool", "type", "--delay", "0", "--", ch], check=False)


def type_text(text: str):
    _need_x()
    if shutil.which("xdotool") and "\n" not in text:
        subprocess.run(["xdotool", "type", "--delay", "0", "--", text], check=False)
        return
    for ch in text:
        type_char(ch)


def press_enter():
    key_chord((), 36)


# Windows: focus, state, foreground --------------------------------------------------------------

def _prop(win: int, name: str, max_items=1024) -> tuple[int, int, bytes]:
    """(format, item count, raw bytes) of a window property; format 32 items are C longs."""
    atom = X.XInternAtom(display, name.encode(), 1)
    if not atom:
        return 0, 0, b""
    at, fmt, n, after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
    data = ctypes.c_void_p()
    if X.XGetWindowProperty(display, win, atom, 0, max_items, 0, 0, ctypes.byref(at), ctypes.byref(fmt),
                            ctypes.byref(n), ctypes.byref(after), ctypes.byref(data)) != 0 or not data.value:
        return 0, 0, b""
    size = {8: 1, 16: ctypes.sizeof(ctypes.c_short), 32: ctypes.sizeof(ctypes.c_long)}.get(fmt.value, 1)
    raw = ctypes.string_at(data.value, n.value * size)
    X.XFree(data)
    return fmt.value, n.value, raw


def _longs(win: int, name: str) -> list[int]:
    fmt, n, raw = _prop(win, name)
    if fmt != 32:
        return []
    return list(struct.unpack(f"{n}L", raw))


def window_title(win: int) -> str:
    for name in ("_NET_WM_NAME", "WM_NAME"):
        fmt, _, raw = _prop(win, name)
        if fmt == 8 and raw:
            return raw.decode("utf-8", "replace")
    return ""


def _proc_of(win: int) -> str:
    pid = _longs(win, "_NET_WM_PID")
    try:
        return Path(f"/proc/{pid[0]}/comm").read_text().strip() if pid else ""
    except OSError:
        return ""


def foreground() -> dict:
    _need_x()
    active = _longs(root, "_NET_ACTIVE_WINDOW")
    win = active[0] if active else 0
    if not win:
        return {"handle": 0, "title": "", "process": ""}
    return {"handle": int(win), "title": window_title(win), "process": _proc_of(win)}


def handle_alive(handle) -> bool:
    try:
        return int(handle) in _longs(root, "_NET_CLIENT_LIST")
    except (TypeError, ValueError):
        return False


def _xdo(*args, timeout=5) -> subprocess.CompletedProcess:
    return subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=timeout)


def _search(title: str) -> list[int]:
    r = _xdo("search", "--onlyvisible", "--name", re.escape(title.strip('"')))
    return [int(w) for w in r.stdout.split() if w.isdigit()]


def focus(title: str, prefer: str) -> tuple[bool, str]:
    title = title.replace('"', "")
    if shutil.which("xdotool"):
        ids = _search(title)
        if not ids:
            return False, "no visible window matches"
        if display and prefer:
            ids.sort(key=lambda w: prefer.lower() not in _proc_of(w).lower())
        wid = ids[0]
        try:
            _xdo("windowactivate", "--sync", str(wid))
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.3)
        active = _xdo("getactivewindow").stdout.strip()
        if active.isdigit() and int(active) == wid:
            return True, _xdo("getwindowname", str(wid)).stdout.strip()
        return False, "the window manager did not bring it to the front"
    if shutil.which("wmctrl"):
        # wmctrl cannot confirm activation; xdotool is what makes exit 4 meaningful
        if subprocess.run(["wmctrl", "-a", title], capture_output=True).returncode == 0:
            return True, f"{title} (unverified, wmctrl)"
        return False, "wmctrl found no matching window"
    return False, "install xdotool (or wmctrl)"


def set_window_state(title: str, state: str) -> list[str]:
    if not shutil.which("xdotool") or (state != "min" and not shutil.which("wmctrl")):
        raise Unsupported(f"window --state {state} on Linux needs xdotool" + ("" if state == "min" else " and wmctrl"))
    hit = []
    for wid in _search(title):
        if state == "min":
            _xdo("windowminimize", str(wid))
        else:
            op = "add" if state == "max" else "remove"
            subprocess.run(["wmctrl", "-i", "-r", hex(wid), "-b", f"{op},maximized_vert,maximized_horz"],
                           capture_output=True)
            if state == "restore":
                _xdo("windowactivate", str(wid))
        hit.append(_xdo("getwindowname", str(wid)).stdout.strip() or hex(wid))
    return hit


def accessibility_ok(ask=False) -> bool:
    return bool(display)


def screen_capture_ok(ask=False) -> bool:
    return bool(display)


# Overlays and capture --------------------------------------------------------------------------

def glow(kind: str) -> int:
    raise Unsupported("glow is not supported on Linux")


def spawn_trail(ctl: str) -> int:
    raise Unsupported("the trail overlay is not supported on Linux")


def spawn_awake() -> int:
    if not shutil.which("systemd-inhibit"):
        raise Unsupported("systemd-inhibit not found; the display may sleep during capture")
    return procs.spawn(["systemd-inhibit", "--what=idle", "--who=desktop-navigation", "--why=capture",
                        "sleep", "7200"])


def spawn_video(base: str) -> tuple[int, str]:
    ff = shutil.which("ffmpeg")
    if not ff or not os.environ.get("DISPLAY"):
        raise Unsupported("ffmpeg (x11grab) and an X11 DISPLAY are needed for video")
    _need_x()
    w, h = _size()
    out = base + ".mp4"
    cmd = [ff, "-y", "-loglevel", "error", "-f", "x11grab", "-framerate", "30", "-draw_mouse", "1",
           "-video_size", f"{w}x{h}", "-i", os.environ["DISPLAY"], "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "frag_keyframe+empty_moov",
           out]
    return procs.spawn(cmd, Path(base + ".ffmpeg.log")), out


def stop_video(pid: int):
    procs.kill(pid, graceful=True)
