"""Foreground guard: input only goes out while the window we focused (or a title match) is in front.

The backend supplies `foreground() -> {"handle", "title", "process"}` and `handle_alive(handle)`.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from core.exits import GuardError

MAX_AGE_S = 30 * 60


def target_file() -> Path:
    return Path(tempfile.gettempdir()) / "desktop-navigation" / "target.json"


def write_target(fg: dict, path: Path | None = None, now: float | None = None) -> Path:
    path = path or target_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"handle": fg.get("handle"), "process": fg.get("process", ""), "title": fg.get("title", ""),
           "ts": time.time() if now is None else now}
    path.write_text(json.dumps(rec))
    return path


def read_target(path: Path | None = None, now: float | None = None) -> dict | None:
    path = path or target_file()
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    now = time.time() if now is None else now
    if now - float(rec.get("ts", 0)) > MAX_AGE_S or rec.get("handle") is None:
        return None
    return rec


def _norm(h):
    # JSON turns a macOS (pid, window) record into a dict with string-safe ints; compare by value.
    return json.loads(json.dumps(h))


class Guard:
    def __init__(self, backend, title: str | None = None, path: Path | None = None, now: float | None = None):
        self.b = backend
        self.title = title.strip('"') if title else None
        self.target = None if self.title else read_target(path, now)

    def check(self):
        fg = self.b.foreground() or {}
        found = fg.get("title") or "(no title)"
        if self.title:
            if self.title.lower() not in (fg.get("title") or "").lower():
                raise GuardError(f"foreground changed: expected '{self.title}', found '{found}'")
            return
        t = self.target
        if t is None:
            raise GuardError("foreground guard has no target: run `pointer focus --title ...` first "
                             "(or pass --title, or --no-guard for desktop-wide targets)")
        if not self.b.handle_alive(t["handle"]):
            raise GuardError(f"foreground guard target '{t.get('title')}' is gone: run `pointer focus` again")
        if _norm(fg.get("handle")) != _norm(t["handle"]):
            raise GuardError(f"foreground changed: expected '{t.get('title')}' ({t['handle']}), found '{found}'")


class NoGuard:
    def check(self):
        pass
