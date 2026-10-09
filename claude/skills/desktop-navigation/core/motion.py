"""Pure geometry and timing: options, the segment path engine, gestures, eased scroll, typing cadence.

No OS calls. Every function that needs randomness takes an rng so tests can pin it.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

SPEED_FACTOR = {"fast": 1, "medium": 2, "slow": 4}
TYPE_MS = {"slow": 110, "medium": 60, "fast": 32}
STEP_MS = 8
TAKEOVER_PX = 6
HEADING_MAX_AGE_S = 20
HEADING_NEAR_PX = 3
SENDKEYS_META = set("+^%~(){}[]")
DEFAULT_PREFIX = "DNAV_"


@dataclass
class Opts:
    speed: str = "fast"
    natural: bool = False
    record: bool = False
    instant: bool = False
    cursor: int = 32
    hold_ms: int | None = None
    side: str | None = None

    @property
    def factor(self) -> int:
        return SPEED_FACTOR[self.speed]


def env_flag(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def env_prefix(env=None) -> str:
    env = os.environ if env is None else env
    return env.get("DNAV_ENV_PREFIX") or DEFAULT_PREFIX


def resolve_opts(speed=None, natural=False, record=False, instant=False, cursor=None, hold_ms=None, side=None,
                 env=None) -> Opts:
    """CLI wins over env; record implies natural and defaults to slow with an 80 px cursor."""
    env = os.environ if env is None else env
    pre = env_prefix(env)
    record = record or env_flag(env.get(pre + "RECORD", ""))
    natural = natural or record or env_flag(env.get(pre + "NATURAL", ""))
    instant = instant or env.get(pre + "GLIDE", "1").strip() == "0"
    speed = speed or env.get(pre + "SPEED") or ("slow" if record else "fast")
    if speed not in SPEED_FACTOR:
        raise ValueError(f"speed must be one of {sorted(SPEED_FACTOR)}")
    cur = cursor or int(env.get(pre + "CURSOR") or (80 if record else 32))
    return Opts(speed, natural, record, instant, cur, hold_ms, side)


def smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def unit(dx: float, dy: float) -> tuple[float, float]:
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else (1.0, 0.0)


def rotate(v, a: float) -> tuple[float, float]:
    c, s = math.cos(a), math.sin(a)
    return v[0] * c - v[1] * s, v[0] * s + v[1] * c


def clamp_pt(p, screen):
    sx, sy, sw, sh = screen
    return min(max(p[0], sx), sx + sw - 1), min(max(p[1], sy), sy + sh - 1)


def moved_away(expected, actual, tol=TAKEOVER_PX) -> bool:
    return math.hypot(actual[0] - expected[0], actual[1] - expected[1]) > tol


def near_any(targets, actual, tol=TAKEOVER_PX) -> bool:
    return any(not moved_away(t, actual, tol) for t in targets)


# Path engine ---------------------------------------------------------------------------------

@dataclass
class Segment:
    pts: list[tuple[float, float]]
    ms: float
    wobble: float = 0.0
    freq: float = 1.0
    phases: tuple[float, float] = (0.0, 0.0)

    @property
    def length(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.pts, self.pts[1:]))


@dataclass
class Plan:
    samples: list[tuple[int, int, float]] = field(default_factory=list)
    end_dir: tuple[float, float] = (1.0, 0.0)

    @property
    def total_ms(self) -> float:
        return sum(d for _, _, d in self.samples)


def bezier(p0, p1, p2, p3, n: int) -> list[tuple[float, float]]:
    out = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def approach_ms(dist: float, opts: Opts, rng: random.Random) -> float:
    ms = opts.factor * min(300.0, max(60.0, 60 + 45 * math.log2(dist / 16 + 1)))
    return ms * rng.uniform(0.85, 1.15) if opts.natural else ms


def approach(p0, p1, opts: Opts, rng: random.Random, heading=None, arrive=None, straight=False,
             dur_scale=1.0) -> Segment:
    """Cubic Bezier leg from p0 to p1: bow, carried heading, hook arrival or a forced arrival tangent."""
    d = math.dist(p0, p1)
    u = unit(p1[0] - p0[0], p1[1] - p0[1])
    ms = approach_ms(d, opts, rng) * dur_scale
    nat = opts.natural and not straight
    c1 = c2 = d * (rng.uniform(0.28, 0.40) if nat else 1 / 3)
    dep, arr = u, u
    if nat:
        theta = rng.choice((-1, 1)) * min(rng.uniform(0.08, 0.25), 80 / (0.25 * max(d, 1)))
        dep, arr = rotate(u, theta), rotate(u, -theta)
        if heading is not None:
            dep = heading
            dot = heading[0] * u[0] + heading[1] * u[1]
            if dot < 0.3:
                # pointing away from the target: a short departure control bends into a hairpin
                back = min(1.0, (0.3 - dot) / 1.3)
                c1 = min(c1, 48 - 34 * back)
    if arrive is not None and not straight:
        arr = arrive
        c2 = min(c2, 45, 0.35 * d)
    elif nat and d > 250 and rng.random() < 0.4:
        arr = rotate((-u[0], -u[1]), rng.choice((-1, 1)) * rng.uniform(0.6, 1.2))
        c2 = min(16, 0.03 * d) * rng.uniform(1.2, 2.0)
    P1 = (p0[0] + dep[0] * c1, p0[1] + dep[1] * c1)
    P2 = (p1[0] - arr[0] * c2, p1[1] - arr[1] * c2)
    n = max(8, int(d / 3))
    seg = Segment(bezier(p0, P1, P2, p1, n), ms)
    if nat:
        seg.wobble = min(6.0, 0.012 * d)
    return seg


def _ramp(x: float) -> float:
    # cube root of smoothstep: speed grows like s^(2/3) (min-jerk-like) so the time integral stays finite
    return smoothstep(x) ** (1 / 3)


def timeline(segs: list[Segment], rng: random.Random, screen=None, step_ms: float = STEP_MS) -> Plan:
    """Run every segment under one speed profile; samples every step_ms, ending exactly on the last point."""
    segs = [s for s in segs if len(s.pts) >= 2]
    if not segs:
        return Plan()
    for s in segs:
        if s.wobble:
            s.freq = rng.uniform(0.8, 1.4)
            s.phases = (rng.uniform(0, math.tau), rng.uniform(0, math.tau))
    pts, seg_of, us, joins, lens = [], [], [], [], []
    for i, s in enumerate(segs):
        sp = s.pts
        cum = [0.0]
        for a, b in zip(sp, sp[1:]):
            cum.append(cum[-1] + math.dist(a, b))
        L = cum[-1] or 1e-9
        lens.append(cum[-1])
        start = 1 if pts and math.dist(pts[-1], sp[0]) < 1e-6 else 0
        if i:
            joins.append(len(pts) - 1)
        for j in range(start, len(sp)):
            pts.append(sp[j])
            seg_of.append(i)
            us.append(cum[j] / L)
    arc = [0.0]
    for a, b in zip(pts, pts[1:]):
        arc.append(arc[-1] + math.dist(a, b))
    total_len = arc[-1]
    total_ms = sum(s.ms for s in segs)
    if total_len < 1e-6:
        x, y = pts[-1]
        return Plan([(round(x), round(y), total_ms)])
    speeds = [(lens[i] or 1e-9) / max(s.ms, 1e-6) for i, s in enumerate(segs)]
    join_s = [arc[j] for j in joins]
    r_in = max(1e-6, min(0.5 * lens[0], 300.0))
    r_out = max(1e-6, min(0.42 * lens[-1], 240.0))

    def speed_at(s: float, si: int) -> float:
        v = speeds[si]
        for k, js in enumerate(join_s):
            h = min(100.0, lens[k] / 2, lens[k + 1] / 2)
            if h > 0 and js - h <= s <= js + h:
                v = speeds[k] + (speeds[k + 1] - speeds[k]) * smoothstep((s - (js - h)) / (2 * h))
                break
        return v * max(0.03, _ramp(s / r_in) * _ramp((total_len - s) / r_out))

    times = [0.0]
    for j in range(len(pts) - 1):
        ds = arc[j + 1] - arc[j]
        times.append(times[-1] + ds / speed_at((arc[j] + arc[j + 1]) / 2, seg_of[j + 1]))
    scale = total_ms / times[-1] if times[-1] > 0 else 0
    times = [t * scale for t in times]

    def pos_at(t: float):
        j = min(len(times) - 2, max(0, bisect.bisect_right(times, t) - 1))
        span = times[j + 1] - times[j]
        f = 0.0 if span <= 0 else min(1.0, (t - times[j]) / span)
        (x0, y0), (x1, y1) = pts[j], pts[j + 1]
        x, y = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
        si = seg_of[j + 1]
        s = segs[si]
        if s.wobble:
            u = us[j] + (us[j + 1] - us[j]) * f if seg_of[j] == si else us[j + 1] * f
            p1, p2 = s.phases
            off = s.wobble * math.sin(math.pi * u) * (
                math.sin(math.tau * s.freq * u + p1) + 0.35 * math.sin(math.tau * 2.7 * s.freq * u + p2))
            tx, ty = unit(x1 - x0, y1 - y0)
            x, y = x - ty * off, y + tx * off
        return x, y

    out = []
    n = max(1, int(total_ms // step_ms))
    for k in range(1, n + 1):
        x, y = pos_at(k * step_ms)
        if screen:
            x, y = clamp_pt((x, y), screen)
        out.append((round(x), round(y), float(step_ms)))
    ex, ey = pts[-1]
    if screen:
        ex, ey = clamp_pt((ex, ey), screen)
    rest = total_ms - n * step_ms
    if out and rest < 1:
        out[-1] = (round(ex), round(ey), out[-1][2])
    else:
        out.append((round(ex), round(ey), max(rest, 0.0)))
    tail = next(((pts[-1][0] - p[0], pts[-1][1] - p[1]) for p in reversed(pts[:-1]) if math.dist(p, pts[-1]) > 0.5),
                (1.0, 0.0))
    return Plan(out, unit(*tail))


def jump(x, y) -> Plan:
    return Plan([(round(x), round(y), 0.0)])


# Carried heading -----------------------------------------------------------------------------

def heading_file() -> Path:
    return Path(tempfile.gettempdir()) / "desktop-navigation" / "heading.json"


def save_heading(path: Path, pos, direction, now: float | None = None):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"x": pos[0], "y": pos[1], "dx": direction[0], "dy": direction[1],
                                    "t": time.time() if now is None else now}))
    except OSError:
        pass


def load_heading(path: Path, pos, now: float | None = None):
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    now = time.time() if now is None else now
    if now - rec.get("t", 0) > HEADING_MAX_AGE_S or math.dist(pos, (rec["x"], rec["y"])) > HEADING_NEAR_PX:
        return None
    return unit(rec["dx"], rec["dy"])


# Gestures ------------------------------------------------------------------------------------

def box(cx, cy, w, h):
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def hover_point(cx, cy, w, h, cursor, screen, side=None) -> tuple[int, int, str]:
    """Tip position so the cursor body (hanging down-right) sits beside the target, not on it."""
    left, top, right, bottom = box(cx, cy, w, h)
    sx, sy, sw, sh = screen
    if side is None:
        side = "right" if h < 48 else "below"
    if side == "below" and bottom + 6 + cursor > sy + sh:
        side = "right"
    if side == "right":
        return int(right + 6), int(top + min(h, 48) / 2), "right"
    return int(cx), int(bottom + 6), "below"


def hover_jiggle(x, y, side, hold_ms, rng: random.Random, step_ms=16) -> list[tuple[int, int, float]]:
    """Finger-like strokes along a diagonal leaning away from the target, starting and ending at rest."""
    ax = (0.74, -0.67) if side == "right" else (-0.74, 0.67)
    cross = (-ax[1], ax[0])
    hold_s = max(hold_ms, step_ms) / 1000
    strokes = 1 + hold_s * rng.uniform(1.5, 2.5)
    amp, sway, ph = rng.uniform(3.5, 6.0), rng.uniform(0.8, 1.6), rng.uniform(0, math.tau)
    n = max(2, int(hold_ms / step_ms))
    out = []
    for k in range(1, n + 1):
        t = k / n
        env = math.sin(math.pi * t)
        a = amp * (1 - math.cos(math.tau * strokes * t)) / 2 * env
        c = sway * math.sin(math.pi * strokes * t + ph) * env
        out.append((round(x + ax[0] * a + cross[0] * c), round(y + ax[1] * a + cross[1] * c), hold_ms / n))
    out[-1] = (int(x), int(y), out[-1][2])
    return out


def underline_plan(cx, cy, w, h, cursor_pos, opts: Opts, rng: random.Random, nxt=None):
    """(start, sweep Segment, arrival tangent): the cheaper sweep direction given the cursor and the next target."""
    left, _, right, bottom = box(cx, cy, w, h)
    y = bottom + 12
    a, b = (left, y), (right, y)

    def cost(s, e):
        return math.dist(cursor_pos, s) + (math.dist(e, nxt) if nxt is not None else 0)
    start, end = (a, b) if cost(a, b) <= cost(b, a) else (b, a)
    span = end[0] - start[0]
    amp = min(3.0, max(1.0, 0.015 * abs(w)))
    freq, ph = rng.uniform(1.2, 1.8), rng.uniform(0, math.tau)
    n = max(12, int(abs(span) / 4))
    pts = []
    for k in range(n + 1):
        u = k / n
        pts.append((start[0] + span * u, y + amp * math.sin(math.pi * u) * math.sin(math.tau * freq * u + ph)))
    ms = opts.factor * (120 + 0.6 * abs(w)) * rng.uniform(0.9, 1.1)
    return start, Segment(pts, ms), (1.0 if span >= 0 else -1.0, 0.0)


@dataclass
class Ellipse:
    cx: float
    cy: float
    a: float
    b: float

    def point(self, th: float, scale=1.0):
        return self.cx + self.a * scale * math.cos(th), self.cy + self.b * scale * math.sin(th)

    def tangent(self, th: float, sign: int):
        return unit(-self.a * math.sin(th) * sign, self.b * math.cos(th) * sign)


def ellipse_for(cx, cy, w, h, cursor, rng: random.Random) -> Ellipse:
    cw, ch = 0.7 * cursor, float(cursor)
    pad = min(28.0, max(12.0, 0.3 * max(w, h))) + 0.2 * cw
    return Ellipse(cx - 0.2 * cw, cy - 0.25 * ch, (w / 2 + pad) * rng.uniform(0.94, 1.08),
                   (h / 2 + pad) * rng.uniform(0.94, 1.10))


def ellipse_fits(e: Ellipse, screen) -> bool:
    sx, sy, sw, sh = screen
    a, b = e.a * 1.04, e.b * 1.04
    return e.cx - a >= sx and e.cy - b >= sy and e.cx + a <= sx + sw and e.cy + b <= sy + sh


def circle_entry(e: Ellipse, cursor_pos, rng: random.Random) -> tuple[float, int]:
    """(start angle, direction +1/-1)."""
    dx, dy = cursor_pos[0] - e.cx, cursor_pos[1] - e.cy
    if math.hypot(dx, dy) < 0.7 * min(e.a, e.b):
        return math.atan2(dy / e.b, dx / e.a), rng.choice((-1, 1))
    v = unit(-dx, -dy)
    base = math.atan2(-e.b * v[0], e.a * v[1])
    cands = [base, base + math.pi]
    th = min(cands, key=lambda t: math.dist(e.point(t), cursor_pos))
    t = e.tangent(th, 1)
    sign = 1 if t[0] * v[0] + t[1] * v[1] >= 0 else -1
    return th, sign


def circle_turns(e: Ellipse, th0: float, sign: int, rng: random.Random, nxt=None) -> float:
    if nxt is None:
        return rng.uniform(1.05, 1.30)
    best, best_dot = 1.0, -2.0
    for i in range(36):
        turns = 1.0 + 0.35 * i / 35
        th = th0 + sign * math.tau * turns
        p, t = e.point(th), e.tangent(th, sign)
        to = unit(nxt[0] - p[0], nxt[1] - p[1])
        dot = t[0] * to[0] + t[1] * to[1]
        if dot > best_dot:
            best, best_dot = turns, dot
    return best


def circle_segment(e: Ellipse, th0: float, sign: int, turns: float, opts: Opts, rng: random.Random) -> Segment:
    drift = rng.uniform(-0.07, 0.03)
    tilt = rng.uniform(-0.12, 0.12)
    f1, f2 = rng.uniform(1.5, 2.5), rng.uniform(3.5, 5.0)
    p1, p2 = rng.uniform(0, math.tau), rng.uniform(0, math.tau)
    n = max(48, int(64 * turns))
    pts = []
    for k in range(n + 1):
        u = k / n
        env = math.sin(math.pi * u)
        r = 1 + drift * u + env * (0.06 * math.sin(math.tau * f1 * u + p1) + 0.025 * math.sin(math.tau * f2 * u + p2))
        th = th0 + sign * math.tau * turns * u
        x, y = e.a * r * math.cos(th), e.b * r * math.sin(th)
        rot = tilt * smoothstep(min(1.0, 2 * u))
        x, y = rotate((x, y), rot)
        pts.append((e.cx + x, e.cy + y))
    ms = opts.factor * min(600.0, 240 + 0.9 * (e.a + e.b)) * turns
    return Segment(pts, ms)


def highlight_ends(cx, cy, w, h, has_h: bool):
    left, top, right, bottom = box(cx, cy, w, h)
    if has_h:
        return (int(left), int(top)), (int(right), int(bottom))
    return (int(left), int(cy)), (int(right), int(cy))


# Scroll and typing ---------------------------------------------------------------------------

def scroll_ms(delta: int, factor: int) -> float:
    return (220 + 0.9 * abs(delta)) * math.sqrt(factor)


def eased_scroll(delta: int, factor: int, unit_size: int = 1) -> list[int]:
    """Per-step wheel amounts (multiples of unit_size) whose cumulative curve has a sin^2 velocity."""
    n = max(8, int(scroll_ms(delta, factor) / 10))
    out, sent = [], 0
    for i in range(1, n + 1):
        t = i / n
        target = delta * (t - math.sin(math.tau * t) / math.tau)
        q = int(round(target / unit_size)) * unit_size
        out.append(q - sent)
        sent = q
    if sent != delta:
        out[-1] += delta - sent
    return out


def sendkeys_escape(text: str) -> str:
    out = []
    for ch in text:
        if ch in SENDKEYS_META:
            out.append("{" + ch + "}")
        elif ch == "\n":
            out.append("+{ENTER}")
        elif ch != "\r":
            out.append(ch)
    return "".join(out)


def type_schedule(text: str, speed: str, rng: random.Random) -> list[tuple[str, float]]:
    """[(char, delay_ms before it)]: first key of a word slowest, pauses at spaces, punctuation, sometimes to think."""
    base = TYPE_MS[speed]
    out, prev, pos_in_word = [], "", 0
    for ch in text:
        if ch.isspace():
            d, pos_in_word = base * rng.uniform(1.2, 1.8), 0
        else:
            d = base * (1.6 if pos_in_word == 0 else max(0.6, 1.0 - 0.08 * pos_in_word)) * rng.uniform(0.8, 1.2)
            pos_in_word += 1
        if prev in ".,;:!?":
            d += base * 2
        if rng.random() < 0.03:
            d += rng.uniform(400, 900)
        out.append((ch, d))
        prev = ch
    return out
