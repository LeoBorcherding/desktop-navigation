"""The Linux backend against a fake libX11/libXtst: keysym translation, Shift handling, wheel buttons, focus."""
import importlib.util
import subprocess
import sys

import pytest
from conftest import SKILL

from core.exits import Unsupported

# keysym -> (keycode, unshifted keysym on that key), US layout subset
KEYMAP = {"a": (38, "a"), "A": (38, "a"), "Return": (36, "Return"), "Control_L": (37, "Control_L"),
          "Shift_L": (50, "Shift_L"), "Alt_L": (64, "Alt_L"), "Escape": (9, "Escape"), "exclam": (10, "1"),
          "1": (10, "1"), "comma": (59, "comma")}
SYMS = {name: i + 1 for i, name in enumerate(sorted({*KEYMAP, *(v[1] for v in KEYMAP.values())}))}


class FakeX:
    def __init__(self):
        self.flushes = 0

    def XStringToKeysym(self, name):
        return SYMS.get(name.decode(), 0)

    def XKeysymToKeycode(self, display, ks):
        name = next((n for n, v in SYMS.items() if v == ks), None)
        return KEYMAP[name][0] if name in KEYMAP else 0

    def XkbKeycodeToKeysym(self, display, kc, group, level):
        return next(SYMS[v[1]] for v in KEYMAP.values() if v[0] == kc)

    def XFlush(self, display):
        self.flushes += 1


class FakeXtst:
    def __init__(self):
        self.events = []

    def XTestFakeKeyEvent(self, display, kc, down, delay):
        self.events.append(("key", kc, down))

    def XTestFakeButtonEvent(self, display, btn, down, delay):
        self.events.append(("btn", btn, down))

    def XTestFakeMotionEvent(self, display, screen, x, y, delay):
        self.events.append(("move", x, y))


@pytest.fixture
def lx(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    spec = importlib.util.spec_from_file_location("dnav_linux_under_test", SKILL / "platforms" / "linux" / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.X, mod.Xtst, mod.display, mod.root = FakeX(), FakeXtst(), 1, 1
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    return mod


def test_cmd_chord_presses_control_not_super(lx):
    lx.key_chord(("cmd",), 0)
    assert lx.Xtst.events == [("key", 37, 1), ("key", 38, 1), ("key", 38, 0), ("key", 37, 0)]


def test_named_key_translation(lx):
    lx.key_chord(("shift", "alt"), 53)
    assert lx.Xtst.events == [("key", 50, 1), ("key", 64, 1), ("key", 9, 1), ("key", 9, 0), ("key", 64, 0),
                              ("key", 50, 0)]


def test_keymap_typing_wraps_shift_when_needed(lx):
    lx.type_text("A!a")
    ev = lx.Xtst.events
    assert ev[:4] == [("key", 50, 1), ("key", 38, 1), ("key", 38, 0), ("key", 50, 0)]
    assert ev[4:8] == [("key", 50, 1), ("key", 10, 1), ("key", 10, 0), ("key", 50, 0)]
    assert ev[8:] == [("key", 38, 1), ("key", 38, 0)]


def test_unmappable_chars_are_skipped_without_xdotool(lx):
    lx.type_text("é")
    assert lx.Xtst.events == []


def test_wheel_lines_use_buttons_4_and_5(lx):
    lx.wheel(120)
    lx.wheel(-80)
    assert lx.Xtst.events == [("btn", 4, 1), ("btn", 4, 0)] * 3 + [("btn", 5, 1), ("btn", 5, 0)] * 2


def test_buttons(lx):
    lx.button("middle", True)
    lx.button("right", False)
    assert lx.Xtst.events == [("btn", 2, 1), ("btn", 3, 0)]


def test_no_display_is_exit_5(lx):
    lx.display = None
    with pytest.raises(Unsupported) as e:
        lx.get_pos()
    assert e.value.code == 5 and not lx.input_ok()


def test_focus_needs_a_tool(lx):
    assert lx.focus("App", "") == (False, "install xdotool (or wmctrl)")


def test_focus_with_xdotool_verifies_active_window(lx, monkeypatch):
    monkeypatch.setattr(lx.shutil, "which", lambda name: "/usr/bin/" + name if name == "xdotool" else None)
    monkeypatch.setattr(lx, "_proc_of", lambda w: "")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = {"search": "111\n222\n", "getactivewindow": "111\n", "getwindowname": "My App\n"}.get(cmd[1], "")
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(lx.subprocess, "run", fake_run)
    monkeypatch.setattr(lx.time, "sleep", lambda s: None)
    assert lx.focus('"My.App"', "") == (True, "My App")
    assert calls[0][-1] == r"My\.App"


def test_screenshot_without_tools_is_exit_2(lx, tmp_path):
    with pytest.raises(Unsupported) as e:
        lx.shot(str(tmp_path / "s.png"))
    assert e.value.code == 2 and "scrot" in str(e.value)


def test_png_size(lx, tmp_path):
    import struct
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 640, 480))
    assert lx.png_size(str(p)) == (640, 480)
    assert lx.png_size(str(tmp_path / "missing.png")) is None


def test_overlays_unsupported(lx):
    for fn, arg in ((lx.glow, "start"), (lx.spawn_trail, "ctl")):
        with pytest.raises(Unsupported):
            fn(arg)
