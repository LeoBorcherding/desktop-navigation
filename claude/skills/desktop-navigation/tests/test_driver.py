import random

import pytest
from conftest import FakeBackend

from core import motion as M
from core.driver import Driver
from core.exits import GuardError, Takeover
from core.guard import Guard, write_target
from core.tour import parse_steps


def drv(b, heading=None, guard=None, **opts):
    logs = []
    d = Driver(b, M.Opts(**opts), random.Random(0), guard=guard, heading_path=heading, sleep=lambda ms: None,
               log=logs.append)
    d.logs = logs
    return d


def test_click_lands_and_clicks():
    b = FakeBackend()
    drv(b, natural=True).click(700, 300)
    assert b.pos == (700, 300) and b.events == [("left", True), ("left", False)]


def test_mclick_uses_middle_button():
    b = FakeBackend()
    drv(b).click(10, 10, "middle")
    assert b.events == [("middle", True), ("middle", False)]


def test_takeover_aborts_without_click():
    b = FakeBackend(drift_after=3)
    with pytest.raises(Takeover):
        drv(b).click(900, 600)
    assert b.events == []


def test_cursor_lagging_one_step_is_not_a_takeover():
    b = FakeBackend(lag=True)
    drv(b, natural=True).click(900, 600)
    assert b.events == [("left", True), ("left", False)]


def test_guard_failure_sends_nothing():
    b = FakeBackend(fg={"handle": 1, "title": "Other"})
    write_target({"handle": 42, "title": "Target App"})
    with pytest.raises(GuardError):
        drv(b, guard=Guard(b)).click(500, 500)
    assert b.events == [] and b.moves == []


def test_guard_rechecked_right_before_the_press():
    b = FakeBackend()
    calls = []

    class FlipGuard:
        def check(self):
            calls.append(1)
            if len(calls) > 1:
                raise GuardError("foreground changed")
    with pytest.raises(GuardError):
        drv(b, guard=FlipGuard()).click(400, 400)
    assert b.moves and b.events == []


def test_drag_holds_while_moving_and_releases_on_takeover():
    b = FakeBackend()
    d = drv(b)
    d.drag(100, 100, 400, 100, back=True)
    assert b.events == [("left", True), ("left", False)]
    assert b.pos == (100, 100) and max(x for x, _ in b.moves) == 400
    b2 = FakeBackend(drift_after=40)
    with pytest.raises(Takeover):
        drv(b2).drag(100, 100, 900, 100)
    assert b2.events[-1] == ("left", False)


def test_drag_path_is_straight():
    b = FakeBackend(pos=(100, 100))
    drv(b, natural=True).drag(100, 100, 600, 100)
    assert all(y == 100 for _, y in b.moves)


def test_scroll_emits_eased_stream_summing_to_delta():
    b = FakeBackend()
    drv(b).scroll(300, 300, -360)
    assert sum(b.wheels) == -360 and len(b.wheels) > 8
    b2 = FakeBackend()
    drv(b2, instant=True).scroll(300, 300, -360)
    assert b2.wheels == [-360]


def test_scroll_converts_to_line_units():
    b = FakeBackend()
    b.WHEEL_UNIT = 40
    drv(b).scroll(300, 300, 360)
    assert sum(b.wheels) == 360 and all(w % 40 == 0 for w in b.wheels)


def test_hover_ends_on_park_point():
    b = FakeBackend()
    drv(b, natural=True).hover(500, 500, 100, 20)
    x, y, _ = M.hover_point(500, 500, 100, 20, 32, (0, 0, 1920, 1080))
    assert b.pos == (x, y) and b.events == []


def test_circle_falls_back_when_off_screen():
    b = FakeBackend()
    d = drv(b, cursor=80)
    d.circle(10, 10, 60, 30)
    assert any("underlining and hovering" in m for m in d.logs)


def test_highlight_selects_with_left_held():
    b = FakeBackend()
    drv(b).highlight(500, 300, 200, 20, has_h=False)
    assert b.events == [("left", True), ("left", False)] and b.pos == (600, 300)


def test_heading_carried_between_commands(tmp_path):
    b = FakeBackend()
    h = tmp_path / "heading.json"
    drv(b, heading=h, natural=True).move(800, 400)
    assert M.load_heading(h, b.pos) is not None


def test_tour_prints_route_and_runs_every_step():
    b = FakeBackend()
    d = drv(b)
    d.tour(parse_steps("hover 900 100; hover 100 100; click 500 500 ; underline 800 600 100 20"))
    assert d.logs[0] == "route: hover 100,100 -> hover 900,100 -> click 500,500 -> underline 800,600"
    assert b.events == [("left", True), ("left", False)]


def test_tour_keep_order():
    b = FakeBackend()
    d = drv(b)
    d.tour(parse_steps("move 900 100; move 100 100"), keep_order=True)
    assert d.logs[0] == "route: move 900,100 -> move 100,100" and b.pos == (100, 100)
