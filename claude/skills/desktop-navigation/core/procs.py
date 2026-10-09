"""Detached helper processes: spawn, liveness, stop. Stdlib only."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

DETACHED_PROCESS, NEW_GROUP, NO_WINDOW = 0x8, 0x200, 0x08000000


def gui_python() -> str:
    """pythonw on Windows so overlay helpers never open a console."""
    if os.name == "nt":
        w = Path(sys.executable).with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return sys.executable


def spawn(cmd: list[str], log: Path | None = None, console=False) -> int:
    out = open(log, "ab") if log else subprocess.DEVNULL
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = NEW_GROUP | (NO_WINDOW if console else DETACHED_PROCESS)
    else:
        kw["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kw).pid
    finally:
        if log:
            out.close()


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        h = _k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = wintypes.DWORD()
        ok = _k32.GetExitCodeProcess(h, ctypes.byref(code))
        _k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill(pid: int | None, graceful=False):
    if not alive(pid):
        return
    if os.name == "nt":
        h = _k32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if h:
            _k32.TerminateProcess(h, 1)
            _k32.CloseHandle(h)
        return
    try:
        os.kill(int(pid), signal.SIGINT if graceful else signal.SIGKILL)
    except OSError:
        pass


def wait_gone(pid: int | None, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return not alive(pid)
