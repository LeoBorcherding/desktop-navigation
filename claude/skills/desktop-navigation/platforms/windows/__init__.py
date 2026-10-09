"""Windows backend: user32/gdi32 over ctypes. Pillow is used for screenshots when installed, else GDI."""
from __future__ import annotations

import ctypes
import os
import shutil
import struct
import subprocess
import time
import zlib
from ctypes import wintypes
from pathlib import Path

from core import procs
from core.exits import ARGS, Unsupported

if os.name != "nt":
    raise ImportError("the Windows backend only loads on Windows")

NAME = "windows"
KEYS_SYNTAX = "sendkeys"
WHEEL_UNIT = 1
HERE = Path(__file__).resolve().parent

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")
winmm = ctypes.WinDLL("winmm")

BUTTON_FLAGS = {("left", True): 0x2, ("left", False): 0x4, ("right", True): 0x8, ("right", False): 0x10,
                ("middle", True): 0x20, ("middle", False): 0x40}
WHEEL = 0x0800
MOVE_ABS = 0x0001 | 0x8000 | 0x4000  # MOVE | ABSOLUTE | VIRTUALDESK
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
VK_SHIFT, VK_RETURN = 0x10, 0x0D
MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)
ULONG_PTR = ctypes.c_size_t

user32.GetForegroundWindow.restype = wintypes.HWND
for _f in ("ShowWindow", "BringWindowToTop", "SetForegroundWindow", "IsWindowVisible", "GetWindowTextLengthW",
           "IsWindow"):
    getattr(user32, _f).argtypes = [wintypes.HWND] + ([ctypes.c_int] if _f == "ShowWindow" else [])
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ULONG_PTR]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.BitBlt.argtypes = [wintypes.HDC] + [ctypes.c_int] * 4 + [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def init():
    # per-monitor aware so screenshot pixels equal click coordinates on every display
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def input_ok() -> bool:
    return True


def displays() -> list[tuple[int, int, int, int]]:
    """Display rects (x, y, w, h), primary first."""
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                               wintypes.LPARAM)

    def cb(hmon, hdc, rect, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        found.append((bool(mi.dwFlags & 1), (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True
    user32.EnumDisplayMonitors(None, None, proto(cb), 0)
    found.sort(key=lambda f: not f[0])
    return [r for _, r in found]


def virtual_screen():
    g = user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def png_bytes(w: int, h: int, bgra: bytes) -> bytes:
    rows = []
    for y in range(h):
        row = bgra[y * w * 4:(y + 1) * w * 4]
        rgb = bytearray(w * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
        rows.append(b"\x00" + bytes(rgb))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))


def screenshot(rect, out: str, index: int = 0):
    x, y, w, h = rect
    try:
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(out)
        return
    except ImportError:
        pass
    sdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, sdc, x, y, 0x00CC0020 | 0x40000000)  # SRCCOPY | CAPTUREBLT
    bi = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0), 44)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, bi, 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(None, sdc)
    Path(out).write_bytes(png_bytes(w, h, buf.raw))


def shot(out: str, index: int = 0):
    ds = displays()
    if index >= len(ds):
        raise Unsupported(f"display {index} not found; {len(ds)} connected", ARGS)
    screenshot(ds[index], out, index)
    return ds[index]


def get_pos():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def set_pos(x, y):
    # SetCursorPos sends no move event, so an app tracking a drag sees only press and release. Inject an
    # absolute move, then wait for it to land so the takeover check reads where we put the pointer.
    x, y = int(x), int(y)
    vx, vy, vw, vh = virtual_screen()
    nx = ((x - vx) * 65535 + (vw - 1) // 2) // max(vw - 1, 1)
    ny = ((y - vy) * 65535 + (vh - 1) // 2) // max(vh - 1, 1)
    user32.mouse_event(MOVE_ABS, nx, ny, 0, 0)
    deadline = time.monotonic() + 0.05
    while time.monotonic() < deadline:
        px, py = get_pos()
        if abs(px - x) <= 1 and abs(py - y) <= 1:
            break
        time.sleep(0.001)
    if get_pos() != (x, y):
        user32.SetCursorPos(x, y)


def button(which: str, down: bool):
    user32.mouse_event(BUTTON_FLAGS[(which, down)], 0, 0, 0, 0)


def wheel(amount: int):
    user32.mouse_event(WHEEL, 0, 0, ctypes.c_uint32(int(amount) & 0xFFFFFFFF).value, 0)


def timer(on: bool):
    (winmm.timeBeginPeriod if on else winmm.timeEndPeriod)(1)


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _key(vk=0, scan=0, flags=0):
    return INPUT(1, _U(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))


def type_char(ch: str):
    if ch == "\n":
        _send(_key(VK_SHIFT), _key(VK_RETURN), _key(VK_RETURN, flags=KEYEVENTF_KEYUP),
              _key(VK_SHIFT, flags=KEYEVENTF_KEYUP))
        return
    if ch == "\r":
        return
    data = ch.encode("utf-16-le")
    for unit in struct.unpack(f"<{len(data) // 2}H", data):
        _send(_key(scan=unit, flags=KEYEVENTF_UNICODE), _key(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))


def type_text(text: str):
    from core.motion import sendkeys_escape
    send_keys(sendkeys_escape(text))


def press_enter():
    _send(_key(VK_RETURN), _key(VK_RETURN, flags=KEYEVENTF_KEYUP))


def send_keys(spec: str):
    """SendKeys syntax ({ESC}, ^a, {TAB}), routed through .NET so the syntax is the real one."""
    lit = spec.replace("'", "''")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    f"[System.Windows.Forms.SendKeys]::SendWait('{lit}')"], check=True)


def release_mods():
    ups = [_key(vk, flags=KEYEVENTF_KEYUP) for vk in MODIFIER_VKS if user32.GetAsyncKeyState(vk) & 0x8000]
    if ups:
        _send(*ups)


def _proc_name(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def windows_matching(title: str) -> list[tuple[int, str, str]]:
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lp):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER: skip owned popups
            return True
        name = _title(hwnd)
        if title.lower() in name.lower():
            found.append((hwnd, name, _proc_name(_pid(hwnd))))
        return True
    user32.EnumWindows(proto(cb), 0)
    return found


def focus(title: str, prefer: str) -> tuple[bool, str]:
    wins = windows_matching(title)
    if not wins:
        return False, "no visible window title contains it"
    wins.sort(key=lambda w: prefer.lower() not in w[2])
    hwnd = wins[0][0]
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    # Windows ignores SetForegroundWindow from a background process unless input queues are attached.
    attached = fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
    try:
        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    time.sleep(0.15)
    if user32.GetForegroundWindow() == hwnd:
        return True, _title(hwnd)
    return False, f"Windows kept '{_title(user32.GetForegroundWindow())}' in front"


SHOW_CMD = {"min": 6, "restore": 9, "max": 3}


def set_window_state(title: str, state: str) -> list[str]:
    hit = []
    for hwnd, name, _ in windows_matching(title):
        user32.ShowWindow(hwnd, SHOW_CMD[state])
        hit.append(name)
    return hit


def foreground() -> dict:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"handle": 0, "title": "", "process": ""}
    return {"handle": int(hwnd), "title": _title(hwnd), "process": _proc_name(_pid(hwnd))}


def handle_alive(handle) -> bool:
    return bool(handle) and bool(user32.IsWindow(wintypes.HWND(int(handle))))


def accessibility_ok(ask=False) -> bool:
    return True


def screen_capture_ok(ask=False) -> bool:
    return True


def _overlay(*args, log=None) -> int:
    return procs.spawn([procs.gui_python(), str(HERE / "overlay.py"), *args], log)


def glow(kind: str) -> int:
    return _overlay("glow", kind)


def spawn_trail(ctl: str) -> int:
    return _overlay("trail", ctl, log=Path(ctl).with_name("trail.log"))


def spawn_awake() -> int:
    return _overlay("awake")


def spawn_video(base: str) -> tuple[int, str]:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise Unsupported("ffmpeg is not on PATH; carrying on with the trail only")
    out = base + ".mp4"
    cmd = [ff, "-y", "-loglevel", "error", "-f", "gdigrab", "-framerate", "30", "-draw_mouse", "1", "-i", "desktop",
           "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
           "yuv420p", "-movflags", "frag_keyframe+empty_moov", out]
    return procs.spawn(cmd, Path(base + ".ffmpeg.log"), console=True), out


def stop_video(pid: int):
    # fragmented MP4: a terminated ffmpeg still leaves a playable file
    procs.kill(pid)
