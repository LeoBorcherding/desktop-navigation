import json
import os
import sys

import pytest
from conftest import FakeBackend, FakeMac

import drive
import platforms
from core import capture, procs
from core.exits import ARGS, CAPTURE, FOCUS, GUARD, OK, PERMS, Unsupported
from core.guard import read_target, write_target


def run(argv, b):
    return drive.main(argv, backend=b)


def test_parser_pointer_flags():
    a = drive.parser().parse_args(["pointer", "drag", "1", "2", "--to-x", "3", "--to-y", "4", "--back",
                                   "--no-guard", "--title", "App"])
    assert (a.x, a.y, a.to_x, a.to_y, a.back, a.no_guard, a.title) == (1, 2, 3, 4, True, True, "App")
    with pytest.raises(SystemExit) as e:
        drive.parser().parse_args(["pointer", "teleport"])
    assert e.value.code == 2


def test_pos_and_front_are_read_only(capsys):
    b = FakeBackend(pos=(12, 34))
    assert run(["pointer", "pos"], b) == OK
    assert run(["pointer", "front"], b) == OK
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "12 34" and json.loads(out[1])["title"] == "Target App"
    assert b.events == [] and b.moves == []


@pytest.mark.parametrize("argv", [["pointer", "click"], ["pointer", "drag", "1", "2"],
                                  ["pointer", "tour", "--steps", "warp 1 2"], ["pointer", "tour"],
                                  ["pointer", "keys", "--keys", "hyper+q", "--no-guard"]])
def test_bad_arguments_exit_2_and_send_nothing(argv, no_sleep):
    b = FakeBackend()
    assert run(argv, b) == ARGS
    assert b.events == [] and b.moves == []


def test_focus_writes_target_then_guard_passes(no_sleep, capsys):
    b = FakeBackend()
    assert run(["pointer", "focus", "--title", "target"], b) == OK
    assert read_target()["handle"] == 42
    assert "front: Target App" in capsys.readouterr().out
    assert run(["pointer", "click", "10", "10"], b) == OK
    assert b.events == [("left", True), ("left", False)]
    assert run(["pointer", "focus", "--title", "nothing"], b) == FOCUS


def test_input_without_target_exits_6(no_sleep, capsys):
    b = FakeBackend()
    assert run(["pointer", "click", "10", "10"], b) == GUARD
    assert b.events == [] and b.moves == []
    assert "pointer focus" in capsys.readouterr().err


def test_wrong_foreground_exits_6_with_message(no_sleep, capsys):
    write_target({"handle": 42, "title": "Target App"})
    b = FakeBackend(fg={"handle": 7, "title": "Chat", "process": "chat.exe"})
    assert run(["pointer", "move", "10", "10"], b) == GUARD
    assert b.moves == []
    assert "foreground changed: expected 'Target App' (42), found 'Chat'" in capsys.readouterr().err


def test_no_guard_and_title_guard(no_sleep):
    b = FakeBackend(fg={"handle": 7, "title": "Taskbar", "process": "shell"})
    assert run(["pointer", "click", "5", "5", "--no-guard"], b) == OK
    assert run(["pointer", "click", "5", "5", "--title", "task"], b) == OK
    assert run(["pointer", "click", "5", "5", "--title", "other"], b) == GUARD


def test_keys_check_guard_per_chord(no_sleep):
    b = FakeBackend()
    assert run(["pointer", "keys", "--keys", "cmd+a esc", "--title", "target"], b) == OK
    assert b.events == [("chord", ("cmd",), 0), ("chord", (), 53)]
    b2 = FakeBackend(fg={"handle": 1, "title": "Elsewhere"})
    assert run(["pointer", "keys", "--keys", "cmd+a", "--title", "target"], b2) == GUARD
    assert b2.events == []


def test_type_instant_and_paced(no_sleep):
    b = FakeBackend()
    assert run(["type", "--text", "hi", "--no-guard"], b) == OK
    assert b.events == [("text", "hi"), "enter"]
    b2 = FakeBackend()
    assert run(["type", "--text", "ok", "--speed", "fast", "--no-enter", "--no-guard"], b2) == OK
    assert b2.events == [("char", "o"), ("char", "k")]


def test_type_keycodes_rejects_before_typing(no_sleep):
    b = FakeMac()
    assert run(["type", "--keycodes", "--text", "naïve", "--no-guard"], b) == ARGS
    assert b.events == []


def test_type_keycodes_shift_enter_and_mods(no_sleep):
    b = FakeMac()
    assert run(["type", "--keycodes", "--text", "Hi", "--no-guard", "--keys", "tab"], b) == OK
    assert b.events == ["release_mods", ("tap", 4, True), ("tap", 34, False), ("tap", 48, False),
                        ("tap", 36, False), "release_mods"]
    assert run(["type", "--keycodes", "--text", "x", "--expect", "Remote", "--no-guard"], b) == GUARD
    b2 = FakeMac()
    assert run(["type", "--release-mods"], b2) == OK and b2.events == ["release_mods"]


def test_type_keycodes_not_on_windows_or_linux():
    assert run(["type", "--keycodes", "--text", "x", "--no-guard"], FakeBackend()) == ARGS


def test_glow_env_switch(monkeypatch, capsys):
    b = FakeBackend()
    assert run(["glow", "start"], b) == OK and b.events == [("glow", "start")]
    monkeypatch.setenv("DNAV_GLOW", "0")
    assert run(["glow", "stop"], b) == OK and len(b.events) == 1
    assert "glow disabled" in capsys.readouterr().out


def test_input_without_permission_exits_5():
    class NoAx(FakeMac):
        def input_ok(self):
            return False
    assert run(["pointer", "click", "1", "1"], NoAx()) == PERMS
    assert run(["type", "--text", "x"], NoAx()) == PERMS


def test_backend_selection():
    with pytest.raises(Unsupported) as e:
        platforms.load("sunos5")
    assert e.value.code == ARGS
    if sys.platform == "win32":
        assert platforms.load().NAME == "windows"


def test_backend_missing_libraries_is_exit_5(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "platforms" and fromlist and fromlist[0] in ("linux", "macos", "windows"):
            raise OSError("libX11.so.6: cannot open shared object file")
        return real(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(Unsupported) as e:
        platforms.load("linux")
    assert e.value.code == PERMS


class CaptureBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.stopped = []

    def spawn_trail(self, ctl):
        return 999999

    def spawn_video(self, base):
        raise Unsupported("ffmpeg is not on PATH; carrying on with the trail only")

    def spawn_awake(self):
        return None

    def stop_video(self, pid):
        self.stopped.append(pid)


def test_capture_start_stop_clear(monkeypatch, capsys):
    b = CaptureBackend()
    alive = {"v": True}
    monkeypatch.setattr(procs, "alive", lambda pid: bool(pid) and alive["v"])
    monkeypatch.setattr(procs, "wait_gone", lambda pid, t: True)
    monkeypatch.setattr(capture, "_wait", lambda pred, t: pred())
    assert run(["capture", "start", "--name", "demo"], b) == OK
    st = capture.load_state()
    assert st["name"] == "demo" and st["pids"] == {"trail": 999999}
    assert run(["capture", "start"], b) == CAPTURE
    assert run(["capture", "clear"], b) == OK
    assert capture.control_path().read_text().strip() == "clear"
    assert run(["capture", "stop"], b) == OK
    out = capsys.readouterr().out
    assert "ffmpeg is not on PATH" in out and "(not written)" in out
    assert not capture.state_path().exists()
    assert run(["capture", "stop"], b) == CAPTURE


@pytest.mark.skipif(os.name != "nt", reason="the overlay module loads user32")
def test_windows_glow_frame_math():
    sys.path.insert(0, str(drive.HERE / "platforms" / "windows"))
    import overlay
    assert overlay.depth_alpha(0) == pytest.approx(0.55)
    assert overlay.depth_alpha(0.5) == pytest.approx(0.20)
    assert overlay.depth_alpha(1) == 0
    thick, alpha, slide = overlay.glow_frame("start", 0.35, 72)
    assert thick == pytest.approx(72) and alpha == 1 and slide == pytest.approx(0.105)
    assert overlay.glow_frame("start", 1.0, 72)[1] == 0
    assert overlay.glow_frame("stop", 0.25, 72)[0] == 72
    assert overlay.glow_frame("stop", 1.0, 72)[:2] == (0, 0)
    rb = overlay.Rainbow(1000)
    assert len(rb.strip(100)) == 2 * 1000 * 4
