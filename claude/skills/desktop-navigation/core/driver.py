"""Runs motion plans and gestures against a backend's primitives, with takeover and foreground checks."""
from __future__ import annotations

import math
import random
import time
from collections import deque

from core import motion as M
from core.exits import Takeover
from core.guard import NoGuard
from core.tour import plan as plan_tour
from core.tour import route_line


def sleep_ms(ms: float):
    if ms > 0:
        time.sleep(ms / 1000)


class Driver:
    def __init__(self, b, opts: M.Opts, rng=None, guard=None, heading_path=None, sleep=None, log=print):
        self.b, self.o = b, opts
        self.rng = rng or random.Random()
        self.guard = guard or NoGuard()
        self.heading_path = heading_path
        self.sleep = sleep or sleep_ms
        self.log = log
        self.recent: deque = deque(maxlen=2)
        self.held: list[str] = []

    # primitives ------------------------------------------------------------------------------

    def _check(self):
        if not self.recent or M.near_any(self.recent, self.b.get_pos()):
            return
        # macOS applies posted moves asynchronously; give a lagging cursor one more look
        self.sleep(12)
        if not M.near_any(self.recent, self.b.get_pos()):
            raise Takeover()

    def _step(self, x, y):
        self._check()
        self.b.set_pos(x, y)
        self.recent.append((x, y))

    def run(self, plan: M.Plan, save_heading=True):
        if not plan.samples:
            return
        if not self.recent:
            self.recent.append(self.b.get_pos())
        self.b.timer(True)
        try:
            for x, y, d in plan.samples:
                self._step(x, y)
                self.sleep(d)
        finally:
            self.b.timer(False)
        if save_heading and self.heading_path:
            M.save_heading(self.heading_path, plan.samples[-1][:2], plan.end_dir)

    def glide(self, x, y, arrive=None, straight=False, extra=(), dur_scale=1.0):
        p0 = self.b.get_pos()
        self.recent.clear()
        self.recent.append(p0)
        screen = self.b.virtual_screen()
        if self.o.instant:
            self.run(M.jump(x, y))
            if extra:
                self.run(M.timeline(list(extra), self.rng, screen))
            return
        segs = []
        if math.dist(p0, (x, y)) >= 3:
            heading = None
            if self.o.natural and not straight and self.heading_path:
                heading = M.load_heading(self.heading_path, p0)
            segs.append(M.approach(p0, (x, y), self.o, self.rng, heading, arrive, straight, dur_scale))
        segs += list(extra)
        self.run(M.timeline(segs, self.rng, screen) if segs else M.jump(x, y))

    def settle(self):
        ms = 40 * self.o.factor * (self.rng.uniform(0.7, 1.3) if self.o.natural else 1)
        self.sleep(ms + (250 if self.o.record else 0))

    def press(self, which="left"):
        self._check()
        self.guard.check()
        self.b.button(which, True)
        self.held.append(which)

    def release(self, which="left"):
        if which in self.held:
            self.held.remove(which)
        self.b.button(which, False)

    def release_all(self):
        for which in list(self.held):
            try:
                self.release(which)
            except Exception:  # noqa: BLE001 - best effort on the way out
                pass

    def hold(self, default_ms):
        self.sleep(self.o.hold_ms if self.o.hold_ms is not None else default_ms)

    # actions ---------------------------------------------------------------------------------

    def move(self, x, y):
        self.guard.check()
        self.glide(x, y)

    def click(self, x, y, which="left", hold_ms=None):
        self.guard.check()
        self.glide(x, y)
        self.settle()
        self.press(which)
        self.sleep(self.rng.uniform(40, 90) if self.o.natural else 20)
        self.release(which)
        if hold_ms is None:
            hold_ms = self.o.hold_ms if self.o.hold_ms is not None else (700 if self.o.record else 0)
        self.sleep(hold_ms)

    def scroll(self, x, y, delta):
        self.guard.check()
        self.glide(x, y)
        self.settle()
        self._check()
        self.guard.check()
        if self.o.instant:
            self.b.wheel(delta)
        else:
            for amount in M.eased_scroll(delta, self.o.factor, getattr(self.b, "WHEEL_UNIT", 1)):
                self._check()
                if amount:
                    self.b.wheel(amount)
                self.sleep(10)
        if self.o.record:
            self.sleep(600)

    def hover(self, cx, cy, w, h, nxt=None):
        self.guard.check()
        x, y, side = M.hover_point(cx, cy, w, h, self.o.cursor, self.b.virtual_screen(), self.o.side)
        self.glide(x, y)
        hold = self.o.hold_ms if self.o.hold_ms is not None else 300 * self.o.factor
        self.run(M.Plan(M.hover_jiggle(x, y, side, hold, self.rng)), save_heading=False)

    def underline(self, cx, cy, w, h, nxt=None):
        self.guard.check()
        start, seg, arrive = M.underline_plan(cx, cy, w, h, self.b.get_pos(), self.o, self.rng, nxt)
        self.glide(*start, arrive=arrive, extra=[seg])
        self.hold(300 * self.o.factor)

    def circle(self, cx, cy, w, h, nxt=None):
        self.guard.check()
        e = M.ellipse_for(cx, cy, w, h, self.o.cursor, self.rng)
        if not M.ellipse_fits(e, self.b.virtual_screen()):
            self.log("notice: a circle would leave the screen; underlining and hovering instead")
            self.underline(cx, cy, w, h, nxt)
            self.hover(cx, cy, w, h)
            return
        th0, sign = M.circle_entry(e, self.b.get_pos(), self.rng)
        turns = M.circle_turns(e, th0, sign, self.rng, nxt)
        seg = M.circle_segment(e, th0, sign, turns, self.o, self.rng)
        self.glide(*seg.pts[0], arrive=e.tangent(th0, sign), extra=[seg])
        self.hold(300 * self.o.factor)

    def highlight(self, cx, cy, w, h, has_h):
        self.guard.check()
        (x0, y0), (x1, y1) = M.highlight_ends(cx, cy, w, h, has_h)
        self.glide(x0, y0)
        self.settle()
        self.press("left")
        try:
            self.glide(x1, y1, straight=True)
        finally:
            self.release_all()
        self.sleep(max(1200, self.o.hold_ms or 0))

    def drag(self, x, y, x2, y2, back=False):
        self.guard.check()
        self.glide(x, y)
        self.settle()
        self.press("left")
        try:
            self.sleep(60 * self.o.factor)
            self.glide(x2, y2, straight=True, dur_scale=1.5)
            if back:
                self.sleep(120 * self.o.factor)
                self.glide(x, y, straight=True, dur_scale=1.5)
            self.sleep(60)
        finally:
            self.release_all()
            # a following press this soon would read as a double-click
            self.sleep(550)

    def tour(self, steps, keep_order=False):
        order = list(steps) if keep_order else plan_tour(steps, self.b.get_pos())
        self.log(route_line(order))
        for i, s in enumerate(order):
            self.guard.check()
            nxt = order[i + 1].pt if i + 1 < len(order) else None
            if s.name in ("click", "rclick"):
                self.click(s.x, s.y, "left" if s.name == "click" else "right", 700 if self.o.record else 150)
            elif s.name == "move":
                self.move(s.x, s.y)
            elif s.name == "hover":
                self.hover(s.x, s.y, s.w, s.h or 20, nxt)
            elif s.name == "underline":
                self.underline(s.x, s.y, s.w or 80, s.h or 20, nxt)
            elif s.name == "circle":
                self.circle(s.x, s.y, s.w or 40, s.h or 40, nxt)
