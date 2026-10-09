"""capture start|stop|clear: trail overlay, screen video and keep-awake as detached processes.

The backend spawns the parts (`spawn_trail`, `spawn_video`, `spawn_awake`, `stop_video`); this module owns
the state file and the stop protocol.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from core import procs
from core.exits import CAPTURE, OK, Unsupported


def capture_dir() -> Path:
    return Path(tempfile.gettempdir()) / "desktop-navigation" / "capture"


def state_path() -> Path:
    return capture_dir() / "state.json"


def control_path() -> Path:
    return capture_dir() / "trail.ctl"


def load_state() -> dict | None:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return None


def _write_ctl(cmd: str):
    control_path().write_text(cmd + "\n")


def _wait(pred, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


def start(b, video: bool, trail: bool, name: str | None, log=print) -> int:
    st = load_state()
    if st and any(procs.alive(p) for p in st.get("pids", {}).values()):
        log(f"a capture is already running ({st.get('name')}); run `capture stop` first")
        return CAPTURE
    if not video and not trail:
        video = trail = True
    d = capture_dir()
    d.mkdir(parents=True, exist_ok=True)
    control_path().unlink(missing_ok=True)
    name = name or time.strftime("%Y%m%d-%H%M%S")
    state = {"name": name, "started": time.time(), "pids": {}, "paths": {}}
    if trail:
        try:
            state["pids"]["trail"] = b.spawn_trail(str(control_path()))
            state["paths"]["trail"] = str(d / f"{name}-trail.png")
            log("trail overlay started")
        except Unsupported as e:
            log(f"trail: {e}")
    if video:
        try:
            pid, path = b.spawn_video(str(d / name))
            state["pids"]["video"], state["paths"]["video"] = pid, path
            log(f"video recording to {path}")
        except Unsupported as e:
            log(f"video: {e}")
    try:
        pid = b.spawn_awake()
        if pid:
            state["pids"]["awake"] = pid
            log("keeping the display awake")
    except Unsupported as e:
        log(f"keep-awake: {e}")
    if not state["pids"]:
        log("nothing started")
        return CAPTURE
    state_path().write_text(json.dumps(state, indent=1))
    return OK


def stop(b, log=print) -> int:
    st = load_state()
    if not st:
        log("no capture is running")
        return CAPTURE
    pids, paths = st.get("pids", {}), st.get("paths", {})
    secs = time.time() - float(st.get("started", time.time()))
    if pids.get("trail"):
        png = Path(paths["trail"])
        png.unlink(missing_ok=True)
        if procs.alive(pids["trail"]):
            _write_ctl(f"dump {png}")
            _wait(lambda: not control_path().exists() and png.exists(), 3)
            _write_ctl("stop")
            if not procs.wait_gone(pids["trail"], 2):
                procs.kill(pids["trail"])
    if pids.get("video"):
        b.stop_video(pids["video"])
        if not procs.wait_gone(pids["video"], 10):
            procs.kill(pids["video"])
    if pids.get("awake"):
        procs.kill(pids["awake"])
    control_path().unlink(missing_ok=True)
    state_path().unlink(missing_ok=True)
    for kind, p in paths.items():
        f = Path(p)
        if f.exists() and f.stat().st_size > 0:
            extra = f", {secs:.0f} s" if kind == "video" else ""
            log(f"{kind}: {f} ({f.stat().st_size // 1024} KB{extra})")
        else:
            log(f"{kind}: {f} (not written)")
    return OK


def clear(log=print) -> int:
    st = load_state()
    if not st or not procs.alive(st.get("pids", {}).get("trail")):
        log("no trail overlay is running")
        return CAPTURE
    _write_ctl("clear")
    log("trail cleared")
    return OK
