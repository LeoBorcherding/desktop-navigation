"""Tour steps: parse `name X Y [W H]; ...`, then order gestures between click barriers."""
from __future__ import annotations

import math
from dataclasses import dataclass

BARRIERS = {"click", "rclick"}
GESTURES = {"move", "hover", "underline", "circle"}
DEFAULT_SIZE = {"click": (0, 0), "rclick": (0, 0), "move": (0, 0), "hover": (0, 20), "underline": (80, 20),
                "circle": (40, 40)}


@dataclass(frozen=True)
class Step:
    name: str
    x: int
    y: int
    w: int
    h: int

    @property
    def pt(self) -> tuple[int, int]:
        return self.x, self.y

    def __str__(self) -> str:
        return f"{self.name} {self.x},{self.y}"


def parse_steps(text: str) -> list[Step]:
    steps = []
    for raw in text.split(";"):
        parts = raw.split()
        if not parts:
            continue
        name = parts[0].lower()
        if name not in BARRIERS | GESTURES:
            raise ValueError(f"unknown tour step {parts[0]!r} (click, rclick, move, hover, underline, circle)")
        if len(parts) not in (3, 5):
            raise ValueError(f"tour step {raw.strip()!r} needs `name X Y` or `name X Y W H`")
        try:
            nums = [int(float(p)) for p in parts[1:]]
        except ValueError:
            raise ValueError(f"tour step {raw.strip()!r} has a non-numeric coordinate") from None
        w, h = (nums[2], nums[3]) if len(nums) == 4 else DEFAULT_SIZE[name]
        steps.append(Step(name, nums[0], nums[1], w, h))
    if not steps:
        raise ValueError("tour needs at least one step")
    return steps


def path_len(start, pts) -> float:
    total, prev = 0.0, start
    for p in pts:
        total += math.dist(prev, p)
        prev = p
    return total


def order_run(start, run: list[Step]) -> list[Step]:
    """Nearest neighbour from start, then 2-opt on the open path (no return leg)."""
    left, out, cur = list(run), [], start
    while left:
        nxt = min(left, key=lambda s: math.dist(cur, s.pt))
        left.remove(nxt)
        out.append(nxt)
        cur = nxt.pt
    improved = True
    while improved and len(out) > 2:
        improved = False
        for i in range(len(out) - 1):
            for j in range(i + 1, len(out)):
                cand = out[:i] + out[i:j + 1][::-1] + out[j + 1:]
                if path_len(start, [s.pt for s in cand]) + 1e-9 < path_len(start, [s.pt for s in out]):
                    out, improved = cand, True
    return out


def plan(steps: list[Step], start) -> list[Step]:
    out, run, cur = [], [], start
    for s in steps:
        if s.name in BARRIERS:
            out += order_run(cur, run)
            out.append(s)
            run, cur = [], s.pt
        else:
            run.append(s)
    return out + order_run(cur, run)


def route_line(steps: list[Step]) -> str:
    return "route: " + " -> ".join(str(s) for s in steps)
