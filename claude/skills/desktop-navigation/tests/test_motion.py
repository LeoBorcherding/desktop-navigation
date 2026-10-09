import math
import random

import pytest
from conftest import SCREEN

from core import motion as M


def test_resolve_opts_record_implies_natural_slow_big_cursor():
    o = M.resolve_opts(record=True, env={})
    assert o.natural and o.speed == "slow" and o.cursor == 80 and o.factor == 4
    assert M.resolve_opts(speed="fast", record=True, env={}).speed == "fast"
    assert M.resolve_opts(env={}).cursor == 32


def test_resolve_opts_env_prefix_and_glide_off():
    o = M.resolve_opts(env={"X_NATURAL": "1", "X_GLIDE": "0", "X_SPEED": "medium", "DNAV_ENV_PREFIX": "X_"})
    assert o.natural and o.instant and o.speed == "medium"
    with pytest.raises(ValueError):
        M.resolve_opts(speed="warp", env={})


def test_approach_duration_formula_and_clamp():
    rng = random.Random(0)
    o = M.Opts()
    assert M.approach_ms(0, o, rng) == 60
    assert M.approach_ms(10 ** 6, o, rng) == 300
    assert M.approach_ms(16, o, rng) == pytest.approx(60 + 45)
    assert M.approach_ms(10 ** 6, M.Opts(speed="slow"), rng) == 1200
    nat = [M.approach_ms(10 ** 6, M.Opts(natural=True), random.Random(s)) for s in range(30)]
    assert all(255 <= v <= 345 for v in nat) and len(set(nat)) > 1


def plan_for(p0, p1, opts, seed=0, **kw):
    rng = random.Random(seed)
    return M.timeline([M.approach(p0, p1, opts, rng, **kw)], rng, SCREEN)


def test_timeline_ends_exactly_and_matches_budget():
    for seed in range(20):
        o = M.Opts(natural=True)
        rng = random.Random(seed)
        seg = M.approach((10, 10), (900, 500), o, rng)
        plan = M.timeline([seg], rng, SCREEN)
        assert plan.samples[-1][:2] == (900, 500)
        assert plan.total_ms == pytest.approx(seg.ms, abs=1)
        assert all(d <= M.STEP_MS + 1e-6 for _, _, d in plan.samples)


def test_straight_path_stays_on_the_line():
    plan = plan_for((0, 100), (800, 100), M.Opts(natural=True), straight=True)
    assert all(y == 100 for _, y, _ in plan.samples)


def test_non_natural_path_is_straight_and_eases_in_and_out():
    plan = plan_for((0, 300), (1200, 300), M.Opts())
    xs = [x for x, _, _ in plan.samples]
    assert xs == sorted(xs)
    steps = [b - a for a, b in zip(xs, xs[1:])]
    mid = steps[len(steps) // 2]
    assert steps[0] < mid and steps[-2] < mid


def test_samples_clamped_to_screen():
    o = M.Opts(natural=True)
    for seed in range(40):
        plan = plan_for((5, 5), (1915, 1075), o, seed)
        assert all(0 <= x < 1920 and 0 <= y < 1080 for x, y, _ in plan.samples)


def test_wobble_is_zero_at_segment_ends():
    rng = random.Random(3)
    seg = M.Segment([(0.0, 0.0), (400.0, 0.0)], 300, wobble=6)
    plan = M.timeline([seg], rng)
    assert plan.samples[-1][:2] == (400, 0)
    assert max(abs(y) for _, y, _ in plan.samples) > 0.5
    assert abs(plan.samples[0][1]) <= 1


def test_multi_segment_drops_duplicate_join_and_keeps_order():
    a = M.Segment([(0.0, 0.0), (100.0, 0.0)], 100)
    b = M.Segment([(100.0, 0.0), (100.0, 100.0)], 100)
    plan = M.timeline([a, b], random.Random(0))
    assert plan.samples[-1][:2] == (100, 100)
    assert plan.total_ms == pytest.approx(200, abs=1)
    assert plan.end_dir == pytest.approx((0.0, 1.0))


def test_hook_arrival_curls_in_on_some_long_natural_moves():
    hooks = 0
    for seed in range(100):
        plan = plan_for((100, 500), (1100, 500), M.Opts(natural=True), seed)
        assert plan.samples[-1][:2] == (1100, 500)
        hooks += plan.end_dir[0] < 0  # arriving heading back toward the start
    assert 20 <= hooks <= 60
    assert all(plan_for((100, 500), (300, 500), M.Opts(natural=True), s).end_dir[0] > 0 for s in range(30))
    assert all(plan_for((100, 500), (1100, 500), M.Opts(), s).end_dir[0] > 0 for s in range(10))


def test_forced_arrival_caps_control_and_sets_end_direction():
    rng = random.Random(1)
    seg = M.approach((0, 0), (600, 0), M.Opts(natural=True), rng, arrive=(0.0, 1.0))
    plan = M.timeline([seg], rng)
    assert plan.end_dir[1] > 0.9
    assert all(p[0] <= 601 for p in seg.pts)


def test_heading_roundtrip_age_and_distance(tmp_path):
    path = tmp_path / "h.json"
    M.save_heading(path, (100, 100), (3, 4), now=1000)
    assert M.load_heading(path, (101, 102), now=1010) == pytest.approx((0.6, 0.8))
    assert M.load_heading(path, (110, 100), now=1010) is None
    assert M.load_heading(path, (100, 100), now=1021) is None
    assert M.load_heading(tmp_path / "missing.json", (0, 0)) is None


def test_backward_heading_makes_short_hairpin_departure():
    rng = random.Random(0)
    seg = M.approach((500, 500), (900, 500), M.Opts(natural=True), rng, heading=(-1.0, 0.0))
    second = seg.pts[1]
    assert second[0] < 500  # leaves along the carried heading
    assert min(p[0] for p in seg.pts) > 500 - 48


def test_hover_point_sides():
    x, y, side = M.hover_point(500, 500, 100, 20, 32, SCREEN)
    assert side == "right" and x > 550
    x, y, side = M.hover_point(500, 500, 200, 100, 32, SCREEN)
    assert side == "below" and y > 550
    assert M.hover_point(500, 1040, 200, 100, 80, SCREEN)[2] == "right"


@pytest.mark.parametrize("side,sign", [("right", (1, -1)), ("below", (-1, 1))])
def test_hover_jiggle_leans_away_and_returns_to_rest(side, sign):
    pts = M.hover_jiggle(300, 300, side, 600, random.Random(2))
    assert pts[-1][:2] == (300, 300)
    assert sum(d for _, _, d in pts) == pytest.approx(600)
    along = [((x - 300) * 0.74 * sign[0] + (y - 300) * 0.67 * sign[1]) for x, y, _ in pts]
    assert max(along) >= 2 and min(along) >= -2
    assert all(math.dist((x, y), (300, 300)) <= 8 for x, y, _ in pts)


def test_underline_picks_cheaper_direction():
    o, rng = M.Opts(), random.Random(0)
    start, seg, arrive = M.underline_plan(500, 500, 200, 20, (1000, 520), o, rng)
    assert start == (600, 522) and arrive == (-1.0, 0.0) and seg.pts[-1][0] == 400
    start, _, arrive = M.underline_plan(500, 500, 200, 20, (0, 520), o, rng)
    assert start == (400, 522) and arrive == (1.0, 0.0)
    # in a tour the finish end should face the next target
    start, _, _ = M.underline_plan(500, 500, 200, 20, (500, 600), o, rng, nxt=(1500, 520))
    assert start[0] == 400


def test_underline_wave_and_duration():
    o = M.Opts()
    _, seg, _ = M.underline_plan(500, 500, 400, 20, (0, 0), o, random.Random(4))
    assert all(abs(y - 522) <= 3.0001 for _, y in seg.pts)
    assert seg.pts[0][1] == pytest.approx(522) and seg.pts[-1][1] == pytest.approx(522)
    assert 0.9 * 360 <= seg.ms <= 1.1 * 360


def test_ellipse_geometry_and_fit():
    e = M.ellipse_for(800, 500, 100, 20, 40, random.Random(0))
    pad = 30 + 0.2 * 28
    assert e.cx == pytest.approx(800 - 0.2 * 28) and e.cy == pytest.approx(500 - 10)
    assert 0.94 * (50 + pad) <= e.a <= 1.08 * (50 + pad)
    assert M.ellipse_fits(e, SCREEN)
    assert not M.ellipse_fits(M.ellipse_for(10, 10, 60, 30, 80, random.Random(0)), SCREEN)


def test_circle_entry_tangent_parallel_to_approach():
    e = M.Ellipse(500, 500, 100, 60)
    th, sign = M.circle_entry(e, (100, 500), random.Random(0))
    t = e.tangent(th, sign)
    v = M.unit(500 - 100, 0)
    assert t[0] * v[0] + t[1] * v[1] == pytest.approx(1, abs=1e-6)
    p = e.point(th)
    assert math.dist(p, (100, 500)) <= math.dist(e.point(th + math.pi), (100, 500))


def test_circle_entry_from_inside_uses_nearest_angle():
    e = M.Ellipse(500, 500, 100, 60)
    th, sign = M.circle_entry(e, (520, 500), random.Random(0))
    assert th == pytest.approx(0) and sign in (-1, 1)


def test_circle_turns_aim_exit_at_next_target():
    e = M.Ellipse(500, 500, 100, 60)
    rng = random.Random(0)
    turns = M.circle_turns(e, 0.0, 1, rng, nxt=(500, 1000))
    th = math.tau * turns
    t, p = e.tangent(th, 1), e.point(th)
    to = M.unit(500 - p[0], 1000 - p[1])
    assert 1.0 <= turns <= 1.35 and t[0] * to[0] + t[1] * to[1] > 0.8
    assert 1.05 <= M.circle_turns(e, 0.0, 1, rng) <= 1.30


def test_circle_segment_stays_round_the_target_and_timing():
    o = M.Opts(speed="medium")
    cx, cy, w, h, cur = 800, 500, 120, 30, 32
    rng = random.Random(3)
    e = M.ellipse_for(cx, cy, w, h, cur, rng)
    seg = M.circle_segment(e, 0.0, 1, 1.2, o, rng)
    left, top, right, bottom = M.box(cx, cy, w, h)
    assert not any(left + 4 < x < right - 4 and top + 4 < y < bottom - 4 for x, y in seg.pts)
    assert seg.ms == pytest.approx(2 * min(600, 240 + 0.9 * (e.a + e.b)) * 1.2)


def test_highlight_ends():
    assert M.highlight_ends(100, 50, 40, 10, False) == ((80, 50), (120, 50))
    assert M.highlight_ends(100, 50, 40, 10, True) == ((80, 45), (120, 55))


@pytest.mark.parametrize("delta,unit", [(-360, 1), (600, 1), (-360, 40), (130, 40)])
def test_eased_scroll_preserves_total_with_smooth_velocity(delta, unit):
    steps = M.eased_scroll(delta, 1, unit)
    assert sum(steps) == delta
    assert len(steps) >= 8
    assert all(s % unit == 0 for s in steps[:-1])
    if unit == 1:
        mid = abs(steps[len(steps) // 2])
        assert abs(steps[0]) < mid and abs(steps[-1]) <= mid


def test_scroll_duration_formula():
    assert M.scroll_ms(-360, 1) == pytest.approx(220 + 324)
    assert M.scroll_ms(-360, 4) == pytest.approx((220 + 324) * 2)


def test_takeover_tolerance_accepts_either_recent_target():
    assert M.near_any([(100, 100), (120, 100)], (104, 103))
    assert M.near_any([(100, 100), (120, 100)], (118, 100))
    assert not M.near_any([(100, 100), (120, 100)], (150, 100))


def test_sendkeys_escape():
    assert M.sendkeys_escape("a+b^(c)%~{x}[y]") == "a{+}b{^}{(}c{)}{%}{~}{{}x{}}{[}y{]}"
    assert M.sendkeys_escape("l1\r\nl2") == "l1+{ENTER}l2"


def test_type_schedule_word_start_slowest_and_punctuation_pause():
    sched = M.type_schedule("hello, world", "medium", random.Random(5))
    assert "".join(c for c, _ in sched) == "hello, world"
    d = [t for _, t in sched]
    assert d[0] > d[3]
    assert d[6] > M.TYPE_MS["medium"] * 2
