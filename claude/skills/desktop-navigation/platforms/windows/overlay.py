"""Detached Windows overlays: `glow start|stop`, `trail <control file>`, `awake`.

Started by the backend with pythonw; never imported by drive.py. Click-through layered windows that never
activate and have no taskbar entry.
"""
from __future__ import annotations

import colorsys
import ctypes
import math
import os
import sys
import time
from array import array
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")

WS_POPUP = 0x80000000
EX_STYLE = 0x00080000 | 0x00000020 | 0x00000008 | 0x00000080 | 0x08000000  # LAYERED TRANSPARENT TOPMOST TOOLWINDOW NOACTIVATE
SW_SHOWNOACTIVATE, ULW_ALPHA, PM_REMOVE = 4, 2, 1
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD] + \
    [ctypes.c_int] * 4 + [wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p,
                                       wintypes.HDC, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD]
user32.GetAsyncKeyState.restype = ctypes.c_short
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.SetThreadExecutionState.restype = wintypes.DWORD
kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class ULWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hdcDst", wintypes.HDC), ("pptDst", ctypes.c_void_p),
                ("psize", ctypes.c_void_p), ("hdcSrc", wintypes.HDC), ("pptSrc", ctypes.c_void_p),
                ("crKey", wintypes.COLORREF), ("pblend", ctypes.c_void_p), ("dwFlags", wintypes.DWORD),
                ("prcDirty", ctypes.c_void_p)]


def dpi_aware():
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    user32.SetProcessDPIAware()


_wndproc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))  # noqa: E741
_class_name = None


def _register() -> str:
    global _class_name
    if _class_name is None:
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = _wndproc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "DesktopNavigationOverlay"
        user32.RegisterClassExW(ctypes.byref(wc))
        _class_name = wc.lpszClassName
    return _class_name


def pump():
    msg = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


class Layer:
    """One layered window backed by a top-down 32-bit premultiplied DIB."""

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, max(1, w), max(1, h)
        self.hwnd = user32.CreateWindowExW(EX_STYLE, _register(), None, WS_POPUP, x, y, self.w, self.h,
                                           None, None, kernel32.GetModuleHandleW(None), None)
        self.screen = user32.GetDC(None)
        self.mdc = gdi32.CreateCompatibleDC(self.screen)
        hdr = (ctypes.c_byte * 44)()
        ctypes.memmove(hdr, (ctypes.c_int32 * 3)(40, self.w, -self.h), 12)
        ctypes.memmove(ctypes.byref(hdr, 12), (ctypes.c_uint16 * 2)(1, 32), 4)
        self.bits = ctypes.c_void_p()
        self.bmp = gdi32.CreateDIBSection(self.screen, hdr, 0, ctypes.byref(self.bits), None, 0)
        self.old = gdi32.SelectObject(self.mdc, self.bmp)
        self.shown = False

    def blit(self, data: bytes):
        ctypes.memmove(self.bits, data, min(len(data), self.w * self.h * 4))

    def update(self, alpha=255, dirty=None):
        blend = BLENDFUNCTION(0, 0, max(0, min(255, int(alpha))), 1)
        pt, size, src = wintypes.POINT(self.x, self.y), wintypes.SIZE(self.w, self.h), wintypes.POINT(0, 0)
        if dirty and self.shown and hasattr(user32, "UpdateLayeredWindowIndirect"):
            rc = wintypes.RECT(*dirty)
            info = ULWINFO(ctypes.sizeof(ULWINFO), self.screen, ctypes.addressof(pt), ctypes.addressof(size),
                           self.mdc, ctypes.addressof(src), 0, ctypes.addressof(blend), ULW_ALPHA,
                           ctypes.addressof(rc))
            user32.UpdateLayeredWindowIndirect(self.hwnd, ctypes.byref(info))
        else:
            user32.UpdateLayeredWindow(self.hwnd, self.screen, ctypes.byref(pt), ctypes.byref(size), self.mdc,
                                       ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        if not self.shown:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.shown = True

    def close(self):
        user32.DestroyWindow(self.hwnd)
        gdi32.SelectObject(self.mdc, self.old)
        gdi32.DeleteObject(self.bmp)
        gdi32.DeleteDC(self.mdc)
        user32.ReleaseDC(None, self.screen)


def monitors() -> list[tuple[tuple[int, int, int, int], int]]:
    """[((x, y, w, h), dpi)] for every monitor."""
    out = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                               wintypes.LPARAM)
    try:
        shcore = ctypes.WinDLL("shcore")
    except OSError:
        shcore = None

    def cb(hmon, hdc, rect, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        dpi = 96
        if shcore is not None:
            dx, dy = wintypes.UINT(), wintypes.UINT()
            if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                dpi = dx.value
        out.append(((r.left, r.top, r.right - r.left, r.bottom - r.top), dpi))
        return True
    user32.EnumDisplayMonitors(None, None, proto(cb), 0)
    return out


# glow --------------------------------------------------------------------------------------------

GLOW_MS, BINS = 1400, 360


def smoothstep(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def depth_alpha(f: float) -> float:
    """0.55 at the screen edge, 0.20 halfway in, 0 at the inner edge."""
    if f >= 1:
        return 0.0
    return 0.55 + (0.20 - 0.55) * f / 0.5 if f < 0.5 else 0.20 * (1 - (f - 0.5) / 0.5)


def glow_frame(kind: str, p: float, depth: int) -> tuple[float, float, float]:
    """(thickness px, window alpha 0..1, slide fraction of the perimeter) at progress p."""
    if kind == "start":
        thick = depth * smoothstep(p / 0.35)
        alpha = p / 0.12 if p < 0.12 else (1.0 if p < 0.6 else max(0.0, (1 - p) / 0.4))
    else:
        q = max(0.0, (p - 0.5) / 0.5)
        thick = depth * (1 - smoothstep(q))
        alpha = min(1.0, p / 0.12) * (1 - q * q)
    return thick, alpha, 0.3 * p


class Rainbow:
    """Premultiplied BGRA rows for one monitor's perimeter, cached by alpha byte."""

    def __init__(self, perim: int):
        self.perim = perim
        rgb = [colorsys.hsv_to_rgb(b / BINS, 0.72, 1.0) for b in range(BINS)]
        self.rgb = [(int(r * 255), int(g * 255), int(b * 255)) for r, g, b in rgb]
        self.bounds = [round(b * perim / BINS) for b in range(BINS + 1)]
        self.fwd: dict[int, bytes] = {}
        self.rev: dict[int, bytes] = {}
        self.pix: dict[tuple[int, int], bytes] = {}
        self.cols: dict[tuple[int, int, bool], bytes] = {}

    def px(self, b: int, a: int) -> bytes:
        key = (b, a)
        if key not in self.pix:
            r, g, bl = self.rgb[b]
            self.pix[key] = bytes((bl * a // 255, g * a // 255, r * a // 255, a))
        return self.pix[key]

    def strip(self, a: int, reverse=False) -> bytes:
        if a not in self.fwd:
            one = b"".join(self.px(b, a) * (self.bounds[b + 1] - self.bounds[b]) for b in range(BINS))
            self.fwd[a] = one * 2
            arr = array("I", self.fwd[a])
            arr.reverse()
            self.rev[a] = arr.tobytes()
        return self.rev[a] if reverse else self.fwd[a]

    def column(self, b: int, thick: int, alphas: list[int], edge_right: bool) -> bytes:
        key = (b, thick, edge_right)
        if key not in self.cols:
            seq = reversed(alphas) if edge_right else alphas
            self.cols[key] = b"".join(self.px(b, a) for a in seq)
        return self.cols[key]

    def bin_at(self, s: float) -> int:
        return int((s % self.perim) / self.perim * BINS) % BINS


def glow(kind: str):
    dpi_aware()
    bands = []
    for (x, y, w, h), dpi in monitors():
        d = max(8, round(72 * dpi / 96))
        mid = max(1, h - 2 * d)
        rb = Rainbow(2 * w + 2 * h)
        bands.append((x, y, w, h, d, rb, {
            "top": Layer(x, y, w, d), "right": Layer(x + w - d, y + d, d, mid),
            "bottom": Layer(x, y + h - d, w, d), "left": Layer(x, y + d, d, mid)}))
    zero_row_cache: dict[int, bytes] = {}
    t0 = time.perf_counter()
    try:
        while True:
            p = min(1.0, (time.perf_counter() - t0) * 1000 / GLOW_MS)
            for x, y, w, h, d, rb, layers in bands:
                thick, alpha, slide_f = glow_frame(kind, p, d)
                perim, slide = rb.perim, slide_f * rb.perim
                ti = int(thick)
                alphas = [int(255 * depth_alpha(i / ti)) if ti >= 1 else 0 for i in range(d)]
                zero = zero_row_cache.setdefault(w, bytes(4 * w))
                o = int((0 - slide) % perim)
                layers["top"].blit(b"".join(rb.strip(a)[o * 4:(o + w) * 4] if a else zero for a in alphas))
                base = int((2 * w + h - 1 - slide) % perim) + perim
                k0 = 2 * perim - 1 - base
                layers["bottom"].blit(b"".join(rb.strip(a, True)[k0 * 4:(k0 + w) * 4] if a else zero
                                               for a in reversed(alphas)))
                mid = layers["right"].h
                rows_r, rows_l = [], []
                for r in range(mid):
                    br = rb.bin_at(w + d + r - slide)
                    bl = rb.bin_at(2 * w + h + (h - 1 - (d + r)) - slide)
                    rows_r.append(rb.column(br, ti, alphas, True))
                    rows_l.append(rb.column(bl, ti, alphas, False))
                layers["right"].blit(b"".join(rows_r))
                layers["left"].blit(b"".join(rows_l))
                for layer in layers.values():
                    layer.update(255 * alpha)
            pump()
            if p >= 1:
                break
            time.sleep(1 / 60)
    finally:
        for *_, layers in bands:
            for layer in layers.values():
                layer.close()


# trail ---------------------------------------------------------------------------------------------

class GdiPlus:
    def __init__(self):
        self.g = ctypes.WinDLL("gdiplus")

        class StartupInput(ctypes.Structure):
            _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                        ("SuppressBackgroundThread", wintypes.BOOL), ("SuppressExternalCodecs", wintypes.BOOL)]
        self.token = ctypes.c_size_t()
        self.g.GdiplusStartup(ctypes.byref(self.token), ctypes.byref(StartupInput(1, None, False, False)), None)
        f = ctypes.c_float
        for name, args in {"GdipDrawLine": [ctypes.c_void_p, ctypes.c_void_p, f, f, f, f],
                           "GdipFillEllipse": [ctypes.c_void_p, ctypes.c_void_p, f, f, f, f],
                           "GdipDrawEllipse": [ctypes.c_void_p, ctypes.c_void_p, f, f, f, f],
                           "GdipCreatePen1": [ctypes.c_uint32, f, ctypes.c_int, ctypes.c_void_p],
                           "GdipSetPenColor": [ctypes.c_void_p, ctypes.c_uint32],
                           "GdipSetPenWidth": [ctypes.c_void_p, f],
                           "GdipCreateSolidFill": [ctypes.c_uint32, ctypes.c_void_p],
                           "GdipGraphicsClear": [ctypes.c_void_p, ctypes.c_uint32]}.items():
            getattr(self.g, name).argtypes = args


PNG_CLSID = (0x557CF406, 0x1A04, 0x11D3, (0x9A, 0x73, 0x00, 0x00, 0xF8, 0x1E, 0xF3, 0x2E))


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]


def argb(a: float, r: float, g: float, b: float) -> int:
    return (int(a * 255) << 24) | (int(r * 255) << 16) | (int(g * 255) << 8) | int(b * 255)


def trail(ctl_path: str):
    dpi_aware()
    kernel32.SetThreadExecutionState(0x80000000 | 0x1 | 0x2)  # CONTINUOUS | SYSTEM | DISPLAY
    gm = user32.GetSystemMetrics
    vx, vy, vw, vh = gm(76), gm(77), gm(78), gm(79)
    layer = Layer(vx, vy, vw, vh)
    gp = GdiPlus()
    g = gp.g
    bitmap, gfx = ctypes.c_void_p(), ctypes.c_void_p()
    g.GdipCreateBitmapFromScan0(vw, vh, vw * 4, 0xE200B, layer.bits, ctypes.byref(bitmap))  # 32bppPARGB
    g.GdipGetImageGraphicsContext(bitmap, ctypes.byref(gfx))
    g.GdipSetSmoothingMode(gfx, 4)  # AntiAlias
    pen, bead, white = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    g.GdipCreatePen1(argb(0.9, 1, 0, 0), 3.0, 2, ctypes.byref(pen))
    g.GdipSetPenLineCap197819(pen, 2, 2, 2)  # round caps
    g.GdipCreatePen1(argb(1, 1, 1, 1), 1.2, 2, ctypes.byref(white))
    g.GdipCreateSolidFill(argb(0.45, 0, 0, 0), ctypes.byref(bead))
    ctl = Path(ctl_path)
    layer.update(255)
    t0 = time.perf_counter()
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    last = (pt.x - vx, pt.y - vy)
    last_bead = last_paint = last_poll = 0.0
    was_down = False
    dirty: list[float] | None = None
    deadline = t0 + 4 * 3600

    def grow(x0, y0, x1, y1, pad=12):
        nonlocal dirty
        box = [min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad]
        dirty = box if dirty is None else [min(dirty[0], box[0]), min(dirty[1], box[1]),
                                           max(dirty[2], box[2]), max(dirty[3], box[3])]
    try:
        while time.perf_counter() < deadline:
            now = time.perf_counter()
            r, gg, b = colorsys.hsv_to_rgb(((now - t0) / 12.0) % 1.0, 0.9, 1.0)
            hue = argb(0.9, r, gg, b)
            user32.GetCursorPos(ctypes.byref(pt))
            cur = (pt.x - vx, pt.y - vy)
            if math.dist(cur, last) >= 0.3:
                g.GdipSetPenColor(pen, hue)
                g.GdipSetPenWidth(pen, 3.0)
                g.GdipDrawLine(gfx, pen, last[0], last[1], cur[0], cur[1])
                if now - last_bead >= 0.1:
                    g.GdipFillEllipse(gfx, bead, cur[0] - 2, cur[1] - 2, 4, 4)
                    last_bead = now
                grow(*last, *cur)
                last = cur
            down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
            if down and not was_down:
                g.GdipSetPenColor(pen, hue)
                g.GdipSetPenWidth(pen, 2.0)
                g.GdipDrawEllipse(gfx, pen, cur[0] - 9, cur[1] - 9, 18, 18)
                g.GdipDrawEllipse(gfx, white, cur[0] - 6, cur[1] - 6, 12, 12)
                grow(*cur, *cur, pad=14)
            was_down = down
            if dirty and now - last_paint >= 1 / 30:
                d = [max(0, int(dirty[0])), max(0, int(dirty[1])), min(vw, int(dirty[2]) + 1), min(vh, int(dirty[3]) + 1)]
                g.GdipFlush(gfx, 1)
                layer.update(255, d)
                dirty, last_paint = None, now
            if now - last_poll >= 0.25:
                last_poll = now
                pump()
                if ctl.exists():
                    try:
                        cmd = ctl.read_text().strip()
                        ctl.unlink()
                    except OSError:
                        cmd = ""
                    if cmd == "stop":
                        break
                    if cmd == "clear":
                        g.GdipGraphicsClear(gfx, 0)
                        g.GdipFlush(gfx, 1)
                        layer.update(255)
                    elif cmd.startswith("dump "):
                        g.GdipFlush(gfx, 1)
                        a, b2, c, dd = PNG_CLSID
                        clsid = GUID(a, b2, c, (ctypes.c_ubyte * 8)(*dd))
                        g.GdipSaveImageToFile(bitmap, ctypes.c_wchar_p(cmd[5:].strip()), ctypes.byref(clsid), None)
            time.sleep(1 / 120)
    finally:
        g.GdipDeleteGraphics(gfx)
        g.GdipDisposeImage(bitmap)
        layer.close()
        kernel32.SetThreadExecutionState(0x80000000)


def awake():
    kernel32.SetThreadExecutionState(0x80000000 | 0x1 | 0x2)
    time.sleep(4 * 3600)


def main(argv):
    if os.name != "nt" or not argv:
        return 2
    if argv[0] == "glow" and len(argv) > 1 and argv[1] in ("start", "stop"):
        glow(argv[1])
    elif argv[0] == "trail" and len(argv) > 1:
        trail(argv[1])
    elif argv[0] == "awake":
        awake()
    else:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
