"""macOS backend: CoreGraphics and the ObjC runtime over ctypes; screencapture, sips, osascript. Stdlib only."""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from core import keymap, procs
from core.exits import ARGS, PERMS, Unsupported

if sys.platform != "darwin":
    raise ImportError("the macOS backend only loads on macOS")

NAME = "macos"
KEYS_SYNTAX = "chords"
WHEEL_UNIT = 40  # 120 per notch -> 3 lines
HERE = Path(__file__).resolve().parent


def _lib(name: str, path: str):
    return ctypes.CDLL(ctypes.util.find_library(name) or path)


cg = _lib("CoreGraphics", "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
ax = _lib("ApplicationServices", "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
cf = _lib("CoreFoundation", "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


vp, u32 = ctypes.c_void_p, ctypes.c_uint32
cg.CGEventCreate.restype = vp
cg.CGEventCreate.argtypes = [vp]
cg.CGEventGetLocation.restype = CGPoint
cg.CGEventGetLocation.argtypes = [vp]
cg.CGEventCreateMouseEvent.restype = vp
cg.CGEventCreateMouseEvent.argtypes = [vp, u32, CGPoint, u32]
cg.CGEventCreateScrollWheelEvent2.restype = vp
cg.CGEventCreateScrollWheelEvent2.argtypes = [vp, u32, u32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
cg.CGEventCreateKeyboardEvent.restype = vp
cg.CGEventCreateKeyboardEvent.argtypes = [vp, ctypes.c_uint16, ctypes.c_bool]
cg.CGEventSetFlags.argtypes = [vp, ctypes.c_uint64]
cg.CGEventKeyboardSetUnicodeString.argtypes = [vp, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16)]
cg.CGEventPost.argtypes = [u32, vp]
cg.CGGetActiveDisplayList.argtypes = [u32, ctypes.POINTER(u32), ctypes.POINTER(u32)]
cg.CGDisplayBounds.restype = CGRect
cg.CGDisplayBounds.argtypes = [u32]
cg.CGMainDisplayID.restype = u32
cg.CGWindowListCopyWindowInfo.restype = vp
cg.CGWindowListCopyWindowInfo.argtypes = [u32, u32]
cf.CFRelease.argtypes = [vp]
cf.CFArrayGetCount.restype = ctypes.c_long
cf.CFArrayGetCount.argtypes = [vp]
cf.CFArrayGetValueAtIndex.restype = vp
cf.CFArrayGetValueAtIndex.argtypes = [vp, ctypes.c_long]
cf.CFDictionaryGetValue.restype = vp
cf.CFDictionaryGetValue.argtypes = [vp, vp]
cf.CFStringCreateWithCString.restype = vp
cf.CFStringCreateWithCString.argtypes = [vp, ctypes.c_char_p, u32]
cf.CFStringGetCString.restype = ctypes.c_bool
cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, u32]
cf.CFNumberGetValue.restype = ctypes.c_bool
cf.CFNumberGetValue.argtypes = [vp, ctypes.c_int, vp]
ax.AXIsProcessTrusted.restype = ctypes.c_bool

UTF8 = 0x08000100
MOVED, LDOWN, LUP, RDOWN, RUP, LDRAG, RDRAG, ODOWN, OUP, ODRAG = 5, 1, 2, 3, 4, 6, 7, 25, 26, 27
BTN = {"left": (LDOWN, LUP, 0), "right": (RDOWN, RUP, 1), "middle": (ODOWN, OUP, 2)}
_held: dict[str, bool] = {"left": False, "right": False, "middle": False}


def init():
    pass


def input_ok() -> bool:
    return accessibility_ok()


def _display_ids():
    ids = (u32 * 16)()
    n = u32()
    cg.CGGetActiveDisplayList(16, ids, ctypes.byref(n))
    main = cg.CGMainDisplayID()
    return sorted(ids[: n.value], key=lambda d: d != main)


def displays():
    out = []
    for d in _display_ids():
        r = cg.CGDisplayBounds(d)
        out.append((int(r.origin.x), int(r.origin.y), int(r.size.width), int(r.size.height)))
    return out


def virtual_screen():
    ds = displays()
    x0, y0 = min(d[0] for d in ds), min(d[1] for d in ds)
    x1, y1 = max(d[0] + d[2] for d in ds), max(d[1] + d[3] for d in ds)
    return x0, y0, x1 - x0, y1 - y0


def screenshot(rect, out: str, index: int = 0):
    if os.path.exists(out):
        os.remove(out)
    cmd = ["screencapture", "-x", "-t", "png"] + (["-D", str(index + 1)] if index else []) + [out]
    subprocess.run(cmd, check=False)
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise Unsupported("screencapture wrote nothing: grant Screen Recording to the host app "
                           "(System Settings > Privacy & Security > Screen Recording), then restart it", PERMS)
    # Retina captures at 2x; scale to points so pixels equal click coordinates.
    subprocess.run(["sips", "-z", str(rect[3]), str(rect[2]), out], check=True, capture_output=True)


def shot(out: str, index: int = 0):
    ds = displays()
    if index >= len(ds):
        raise Unsupported(f"display {index} not found; {len(ds)} connected", ARGS)
    screenshot(ds[index], out, index)
    return ds[index]


def get_pos():
    ev = cg.CGEventCreate(None)
    p = cg.CGEventGetLocation(ev)
    cf.CFRelease(ev)
    return int(round(p.x)), int(round(p.y))


def _post_mouse(kind, x, y, btn=0):
    ev = cg.CGEventCreateMouseEvent(None, kind, CGPoint(x, y), btn)
    cg.CGEventPost(0, ev)
    cf.CFRelease(ev)


def set_pos(x, y):
    if _held["left"]:
        _post_mouse(LDRAG, x, y, 0)
    elif _held["right"]:
        _post_mouse(RDRAG, x, y, 1)
    elif _held["middle"]:
        _post_mouse(ODRAG, x, y, 2)
    else:
        _post_mouse(MOVED, x, y)


def button(which: str, down: bool):
    x, y = get_pos()
    d, u, num = BTN[which]
    _held[which] = down
    _post_mouse(d if down else u, x, y, num)


def wheel(units: int):
    lines = int(round(units / WHEEL_UNIT))
    step = 1 if lines > 0 else -1
    for _ in range(abs(lines)):
        ev = cg.CGEventCreateScrollWheelEvent2(None, 1, 1, step, 0, 0)  # kCGScrollEventUnitLine
        cg.CGEventPost(0, ev)
        cf.CFRelease(ev)


def timer(on: bool):
    pass


def key_event(code: int, down: bool, flags: int = 0):
    ev = cg.CGEventCreateKeyboardEvent(None, code, down)
    cg.CGEventSetFlags(ev, flags)
    cg.CGEventPost(0, ev)
    cf.CFRelease(ev)


def key_chord(mods: tuple[str, ...], code: int):
    flags = 0
    for m in mods:
        flags |= keymap.MAC_MOD_FLAG[m]
        key_event(keymap.MAC_MOD_KEYCODE[m], True, flags)
    key_event(code, True, flags)
    key_event(code, False, flags)
    for m in reversed(mods):
        flags &= ~keymap.MAC_MOD_FLAG[m]
        key_event(keymap.MAC_MOD_KEYCODE[m], False, flags)


def key_tap(code: int, shift: bool):
    """One physical key, Shift as its own key event (remote-desktop clients drop chord flags)."""
    flags = keymap.MAC_MOD_FLAG["shift"] if shift else 0
    if shift:
        key_event(keymap.SHIFT_KEYCODE, True, flags)
    key_event(code, True, flags)
    key_event(code, False, flags)
    if shift:
        key_event(keymap.SHIFT_KEYCODE, False, 0)


def release_mods():
    for code in keymap.MAC_ALL_MOD_KEYCODES:
        key_event(code, False, 0)


def type_char(ch: str):
    if ch == "\n":
        key_chord(("shift",), 36)
        return
    if ch == "\r":
        return
    data = ch.encode("utf-16-le")
    units = (ctypes.c_uint16 * (len(data) // 2)).from_buffer_copy(data)
    for down in (True, False):
        ev = cg.CGEventCreateKeyboardEvent(None, 0, down)
        cg.CGEventKeyboardSetUnicodeString(ev, len(units), units)
        cg.CGEventPost(0, ev)
        cf.CFRelease(ev)


def type_text(text: str):
    for ch in text:
        type_char(ch)
        time.sleep(0.004)


def press_enter():
    key_chord((), 36)


def _osa(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip()


def set_window_state(title: str, state: str) -> list[str]:
    """min / restore / max for windows whose name contains `title`, across foreground apps."""
    t = title.replace('"', '\\"')
    prop, value = {"min": ("miniaturized", "true"), "restore": ("miniaturized", "false"),
                   "max": ("zoomed", "true")}[state]
    script = "\n".join([
        "set hit to {}",
        'tell application "System Events" to set procs to name of every process whose background only is false',
        "repeat with pn in procs",
        "  try",
        f'    tell application pn to set ws to (every window whose name contains "{t}")',
        "    repeat with w in ws",
        f"      tell application pn to set {prop} of w to {value}",
        "      set end of hit to (name of w)",
        "    end repeat",
        "  end try",
        "end repeat",
        "set AppleScript's text item delimiters to linefeed",
        "hit as text",
    ])
    return [line for line in _osa(script).splitlines() if line]


def focus(title: str, prefer: str) -> tuple[bool, str]:
    t, pr = title.replace('"', '\\"'), prefer.replace('"', '\\"')
    _osa("\n".join([
        'tell application "System Events"',
        f'set ps to (every process whose name contains "{pr}")',
        f'if ps is {{}} then set ps to (every process whose name contains "{t}")',
        "if ps is {} then",
        "  repeat with p in (every process whose background only is false)",
        "    try",
        f'      if (exists (first window of p whose name contains "{t}")) then set ps to {{p}}',
        "    end try",
        "    if ps is not {} then exit repeat",
        "  end repeat",
        "end if",
        "if ps is not {} then set frontmost of (item 1 of ps) to true",
        "end tell"]))
    time.sleep(0.15)
    front = _osa('tell application "System Events" to get name of first process whose frontmost is true').lower()
    fg = foreground().get("title", "")
    if front and (title.lower() in front or prefer.lower() in front or title.lower() in fg.lower()):
        return True, fg or front
    return False, f"'{front or 'unknown'}' is still frontmost"


# Foreground: frontmost app PID plus its top on-screen window -------------------------------------

def _cfstr(s: str):
    return cf.CFStringCreateWithCString(None, s.encode(), UTF8)


_KEYS = {k: _cfstr(k) for k in ("kCGWindowOwnerPID", "kCGWindowNumber", "kCGWindowLayer", "kCGWindowName",
                                "kCGWindowOwnerName")}


def _num(d, key) -> int:
    v = cf.CFDictionaryGetValue(d, _KEYS[key])
    out = ctypes.c_int64()
    return int(out.value) if v and cf.CFNumberGetValue(v, 4, ctypes.byref(out)) else 0


def _str(d, key) -> str:
    v = cf.CFDictionaryGetValue(d, _KEYS[key])
    if not v:
        return ""
    buf = ctypes.create_string_buffer(1024)
    return buf.value.decode("utf-8", "replace") if cf.CFStringGetCString(v, buf, 1024, UTF8) else ""


def onscreen_windows() -> list[dict]:
    arr = cg.CGWindowListCopyWindowInfo(1 | 16, 0)  # OnScreenOnly | ExcludeDesktopElements, front to back
    if not arr:
        return []
    try:
        out = []
        for i in range(cf.CFArrayGetCount(arr)):
            d = cf.CFArrayGetValueAtIndex(arr, i)
            out.append({"pid": _num(d, "kCGWindowOwnerPID"), "number": _num(d, "kCGWindowNumber"),
                        "layer": _num(d, "kCGWindowLayer"), "name": _str(d, "kCGWindowName"),
                        "owner": _str(d, "kCGWindowOwnerName")})
        return out
    finally:
        cf.CFRelease(arr)


def _frontmost_pid() -> int:
    try:
        objc = _lib("objc", "/usr/lib/libobjc.A.dylib")
        _lib("AppKit", "/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype = vp
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = vp
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = ctypes.CFUNCTYPE(vp, vp, vp)(ctypes.cast(objc.objc_msgSend, vp).value)
        send_int = ctypes.CFUNCTYPE(ctypes.c_int, vp, vp)(ctypes.cast(objc.objc_msgSend, vp).value)
        ws = send(objc.objc_getClass(b"NSWorkspace"), objc.sel_registerName(b"sharedWorkspace"))
        app = send(ws, objc.sel_registerName(b"frontmostApplication"))
        if app:
            return int(send_int(app, objc.sel_registerName(b"processIdentifier")))
    except (OSError, AttributeError):
        pass
    out = _osa('tell application "System Events" to get unix id of first process whose frontmost is true')
    return int(out) if out.isdigit() else 0


def foreground() -> dict:
    pid = _frontmost_pid()
    top = next((w for w in onscreen_windows() if w["pid"] == pid and w["layer"] == 0), None)
    if top is None:
        return {"handle": {"pid": pid, "window": 0}, "title": "", "process": ""}
    return {"handle": {"pid": pid, "window": top["number"]}, "title": top["name"] or top["owner"],
            "process": top["owner"]}


def handle_alive(handle) -> bool:
    try:
        return procs.alive(int(handle["pid"]))
    except (TypeError, KeyError, ValueError):
        return False


PRIVACY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


def accessibility_ok(ask=False) -> bool:
    if ask:
        subprocess.run(["open", PRIVACY_PANE])
    return bool(ax.AXIsProcessTrusted())


def screen_capture_ok(ask=False) -> bool:
    try:
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        if ask:
            cg.CGRequestScreenCaptureAccess()
        return bool(cg.CGPreflightScreenCaptureAccess())
    except AttributeError:
        return True


# Overlays and capture -------------------------------------------------------------------------

def _jxa(script: str, *args, log=None) -> int:
    return procs.spawn(["osascript", "-l", "JavaScript", str(HERE / script), *args], log)


def glow(kind: str) -> int:
    return _jxa("glow.js", kind)


def spawn_trail(ctl: str) -> int:
    return _jxa("trail.js", ctl, log=Path(ctl).with_name("trail.log"))


def spawn_awake() -> int:
    if not shutil.which("caffeinate"):
        raise Unsupported("caffeinate not found")
    return procs.spawn(["caffeinate", "-d", "-i", "-u", "-t", "7200"])


def spawn_video(base: str) -> tuple[int, str]:
    out = base + ".mov"
    return procs.spawn(["screencapture", "-v", "-C", "-x", out], Path(base + ".screencapture.log")), out


def stop_video(pid: int):
    # SIGINT lets screencapture finalize the .mov
    procs.kill(pid, graceful=True)
