import sys
import tempfile
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

SCREEN = (0, 0, 1920, 1080)


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path, monkeypatch):
    # heading.json, target.json and capture state all live under tempfile.gettempdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    for k in ("DNAV_ENV_PREFIX", "DNAV_RECORD", "DNAV_NATURAL", "DNAV_SPEED", "DNAV_GLIDE", "DNAV_CURSOR",
              "DNAV_TYPE_SPEED", "DNAV_GLOW"):
        monkeypatch.delenv(k, raising=False)


class FakeBackend:
    """Records every primitive; no OS input is ever sent."""

    NAME = "fake"
    KEYS_SYNTAX = "chords"
    WHEEL_UNIT = 1

    def __init__(self, pos=(0, 0), drift_after=None, lag=False, fg=None, alive=True):
        self.pos = self.prev = pos
        self.events, self.moves, self.wheels = [], [], []
        self.n = 0
        self.drift_after, self.lag = drift_after, lag
        self.fg = fg or {"handle": 42, "title": "Target App", "process": "target.exe"}
        self.alive = alive

    def get_pos(self):
        self.n += 1
        if self.drift_after is not None and self.n > self.drift_after:
            return (self.pos[0] + 50, self.pos[1])
        return self.prev if self.lag else self.pos

    def set_pos(self, x, y):
        self.prev, self.pos = self.pos, (x, y)
        self.moves.append((x, y))

    def button(self, which, down):
        self.events.append((which, down))

    def wheel(self, amount):
        self.wheels.append(amount)

    def timer(self, on):
        pass

    def virtual_screen(self):
        return SCREEN

    def input_ok(self):
        return True

    def foreground(self):
        return dict(self.fg)

    def handle_alive(self, handle):
        return self.alive

    def release_mods(self):
        self.events.append("release_mods")

    def key_chord(self, mods, code):
        self.events.append(("chord", mods, code))

    def type_text(self, text):
        self.events.append(("text", text))

    def type_char(self, ch):
        self.events.append(("char", ch))

    def press_enter(self):
        self.events.append("enter")

    def focus(self, title, prefer):
        return title.lower() in self.fg["title"].lower(), self.fg["title"]

    def glow(self, kind):
        self.events.append(("glow", kind))


class FakeMac(FakeBackend):
    NAME = "macos"

    def key_tap(self, code, shift):
        self.events.append(("tap", code, shift))


@pytest.fixture
def fake():
    return FakeBackend()


@pytest.fixture
def no_sleep(monkeypatch):
    import drive
    from core import driver
    monkeypatch.setattr(driver, "sleep_ms", lambda ms: None)
    monkeypatch.setattr(drive, "sleep_ms", lambda ms: None)
