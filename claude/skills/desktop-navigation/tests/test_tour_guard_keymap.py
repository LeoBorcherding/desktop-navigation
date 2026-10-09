import itertools
import json

import pytest
from conftest import FakeBackend

from core import keymap, tour
from core.exits import GuardError
from core.guard import Guard, read_target, target_file, write_target


def test_parse_steps_defaults_and_sizes():
    steps = tour.parse_steps("hover 10 20; circle 100 100 50 30 ;click 5 5;")
    assert [s.name for s in steps] == ["hover", "circle", "click"]
    assert (steps[0].w, steps[0].h) == (0, 20) and (steps[1].w, steps[1].h) == (50, 30)


@pytest.mark.parametrize("bad", ["jump 1 2", "click 1", "hover 1 2 3", "click a b", ""])
def test_parse_steps_rejects(bad):
    with pytest.raises(ValueError):
        tour.parse_steps(bad)


def test_plan_keeps_clicks_as_barriers():
    steps = tour.parse_steps("hover 900 0; hover 100 0; click 500 500; underline 1000 500; underline 100 500")
    out = tour.plan(steps, (0, 0))
    assert [str(s) for s in out] == ["hover 100,0", "hover 900,0", "click 500,500", "underline 100,500",
                                     "underline 1000,500"]


def test_order_run_is_optimal_on_small_open_paths():
    pts = [(0, 300), (400, 0), (50, 50), (700, 650), (300, 300), (650, 100)]
    run = [tour.Step("hover", x, y, 0, 20) for x, y in pts]
    got = tour.path_len((0, 0), [s.pt for s in tour.order_run((0, 0), run)])
    best = min(tour.path_len((0, 0), p) for p in itertools.permutations(pts))
    assert got == pytest.approx(best, rel=0.05)


def test_route_line():
    assert tour.route_line(tour.parse_steps("click 1 2; hover 3 4")) == "route: click 1,2 -> hover 3,4"


def test_target_roundtrip_and_age():
    write_target({"handle": 7, "title": "T", "process": "p"}, now=1000)
    assert json.loads(target_file().read_text())["handle"] == 7
    assert read_target(now=1000 + 60)["title"] == "T"
    assert read_target(now=1000 + 31 * 60) is None


def test_guard_title_mode():
    b = FakeBackend(fg={"handle": 1, "title": "Unsloth Desktop", "process": "x"})
    Guard(b, title='"unsloth"').check()
    with pytest.raises(GuardError, match="expected 'Notepad', found 'Unsloth Desktop'"):
        Guard(b, title="Notepad").check()


def test_guard_target_mode():
    b = FakeBackend(fg={"handle": 42, "title": "App", "process": "x"})
    with pytest.raises(GuardError, match="pointer focus"):
        Guard(b).check()
    write_target({"handle": 42, "title": "App"})
    Guard(b).check()
    b.fg = {"handle": 43, "title": "Other", "process": "y"}
    with pytest.raises(GuardError, match="foreground changed: expected 'App' \\(42\\), found 'Other'"):
        Guard(b).check()
    b.alive = False
    with pytest.raises(GuardError, match="gone"):
        Guard(b).check()


def test_guard_compares_mac_style_handles_by_value():
    b = FakeBackend(fg={"handle": {"pid": 10, "window": 99}, "title": "App"})
    write_target(b.foreground())
    Guard(b).check()
    b.fg = {"handle": {"pid": 10, "window": 100}, "title": "App"}
    with pytest.raises(GuardError):
        Guard(b).check()


def test_parse_chords():
    assert keymap.parse_chords("cmd+a esc shift+tab") == [(("cmd",), 0), ((), 53), (("shift",), 48)]
    assert keymap.parse_chords("ctrl+shift+Z") == [(("ctrl", "shift"), 6)]
    for bad in ("hyper+a", "f13", "", "cmd+"):
        with pytest.raises(ValueError):
            keymap.parse_chords(bad)


def test_keycode_plan_validates_whole_string_first():
    assert keymap.keycode_plan("aA !") == [(0, False), (0, True), (49, False), (18, True)]
    assert keymap.keycode_plan("x\ny")[1] == (36, True)
    with pytest.raises(ValueError, match="'é'"):
        keymap.keycode_plan("café")


def test_mac_to_x_translation():
    assert keymap.x_keysym_name(36) == "Return" and keymap.x_keysym_name(0) == "a"
    assert keymap.x_keysym_name(116) == "Prior" and keymap.x_keysym_name(43) == "comma"
    assert keymap.X_MODS["cmd"] == keymap.X_MODS["ctrl"] == "Control_L"
    with pytest.raises(ValueError):
        keymap.x_keysym_name(999)
    assert keymap.x_char_keysym("!") == "exclam" and keymap.x_char_keysym("q") == "q"
    assert keymap.x_char_keysym("é") is None
