#!/usr/bin/env python3
"""Drive a native window with screenshots and real OS input: Windows, macOS, Linux (X11).

    python drive.py shot [--out PNG] [--display N]
    python drive.py pointer focus --title "Unsloth"
    python drive.py pointer click 640 400
    python drive.py pointer tour --steps "circle 300 200 120 30; click 640 400"
    python drive.py type --text "hello" --no-enter
    python drive.py glow start|stop
    python drive.py capture start [--video] [--trail] [--name N] | stop | clear

Exit codes: 0 ok, 1 capture state error, 2 bad arguments / unsupported OS, 3 a person took the mouse
(nothing clicked), 4 focus failed, 5 permission or display missing, 6 foreground is not the target.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import platforms  # noqa: E402
from core import capture, keymap  # noqa: E402
from core import motion as M  # noqa: E402
from core.driver import Driver, sleep_ms  # noqa: E402
from core.exits import (ARGS, FOCUS, GUARD, OK, PERMS, TAKEOVER, GuardError, Takeover,  # noqa: E402
                        Unsupported)
from core.guard import Guard, NoGuard, write_target  # noqa: E402
from core.tour import parse_steps  # noqa: E402

GESTURES = ("hover", "underline", "circle", "highlight")
INPUT_ACTIONS = ("click", "rclick", "mclick", "move", "scroll", "drag", "tour", "keys") + GESTURES
POINTER_ACTIONS = INPUT_ACTIONS + ("focus", "window", "pos", "front", "perms")


def err(msg: str, code: int = ARGS) -> int:
    print(msg, file=sys.stderr)
    return code


def no_input_msg(b) -> str:
    if b.NAME == "macos":
        return "Accessibility is not granted to the host app; events would be dropped. Run `drive.py perms --ask`."
    return "no input available: log into an X11 session (not Wayland) with DISPLAY set and libX11/libXtst installed"


def make_guard(a, b):
    return NoGuard() if a.no_guard else Guard(b, title=a.title)


def default_shot() -> str:
    return str(Path(tempfile.gettempdir()) / "desktop-navigation" / "screen.png")


def cmd_shot(a, b) -> int:
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rect = b.shot(str(out), a.display)
    if rect is None:
        print(f"{out} origin 0,0")
    else:
        print(f"{out} {rect[2]}x{rect[3]} origin {rect[0]},{rect[1]}")
    return OK


def cmd_focus(a, b) -> int:
    if not a.title:
        return err("focus needs --title")
    ok, info = b.focus(a.title, a.prefer)
    if not ok:
        print(f"could not focus '{a.title}': {info}")
        return FOCUS
    fg = b.foreground()
    write_target(fg)
    print(f"front: {fg.get('title') or info}")
    return OK


def send_chords(b, spec: str, guard) -> None:
    if b.KEYS_SYNTAX == "sendkeys":
        guard.check()
        b.send_keys(spec)
        return
    for mods, code in keymap.parse_chords(spec):
        guard.check()
        b.key_chord(mods, code)
        sleep_ms(30)


def cmd_pointer(a, b) -> int:
    act = a.action
    if act == "pos":
        x, y = b.get_pos()
        print(f"{x} {y}")
        return OK
    if act == "front":
        print(json.dumps(b.foreground()))
        return OK
    if act == "perms":
        return cmd_perms(a, b)
    if act == "focus":
        return cmd_focus(a, b)
    if act == "window":
        if not a.title or not a.state:
            return err("window needs --title and --state")
        hit = b.set_window_state(a.title, a.state)
        for name in hit:
            print(f"{a.state}: {name}")
        return OK if hit else FOCUS
    if not b.input_ok():
        return err(no_input_msg(b), PERMS)
    guard = make_guard(a, b)
    if act == "keys":
        if not a.keys:
            return err("keys needs --keys")
        try:
            send_chords(b, a.keys, guard)
        except ValueError as e:
            return err(str(e))
        except GuardError as e:
            return err(str(e), GUARD)
        return OK
    if act == "tour":
        if not a.steps:
            return err("tour needs --steps \"name X Y [W H]; ...\"")
        try:
            steps = parse_steps(a.steps)
        except ValueError as e:
            return err(str(e))
    elif a.x is None or a.y is None:
        return err(f"{act} needs X Y")
    if act == "drag" and (a.to_x is None or a.to_y is None):
        return err("drag needs --to-x and --to-y")
    try:
        opts = M.resolve_opts(a.speed, a.natural, a.record, a.instant, a.cursor, a.hold, a.side)
    except ValueError as e:
        return err(str(e))
    d = Driver(b, opts, guard=guard, heading_path=M.heading_file())
    x, y = a.x, a.y
    w = a.w if a.w is not None else (2 * a.r if a.r else 0)
    h = a.h if a.h is not None else (2 * a.r if a.r else 0)
    try:
        if act == "move":
            d.move(x, y)
        elif act in ("click", "rclick", "mclick"):
            d.click(x, y, {"click": "left", "rclick": "right", "mclick": "middle"}[act])
        elif act == "scroll":
            d.scroll(x, y, a.delta)
        elif act == "drag":
            d.drag(x, y, a.to_x, a.to_y, a.back)
        elif act == "hover":
            d.hover(x, y, w, h or 20)
        elif act == "underline":
            d.underline(x, y, w or 80, h or 20)
        elif act == "circle":
            d.circle(x, y, w or 40, h or 40)
        elif act == "highlight":
            d.highlight(x, y, w or 80, h or 0, a.h is not None)
        elif act == "tour":
            d.tour(steps, a.keep_order)
    except Takeover:
        d.release_all()
        return err("the mouse moved under us: a person is driving, nothing more was sent", TAKEOVER)
    except GuardError as e:
        d.release_all()
        return err(str(e), GUARD)
    return OK


def cmd_type_keycodes(a, b, guard) -> int:
    if not hasattr(b, "key_tap"):
        raise Unsupported("--keycodes is macOS only (on Linux, xdotool types Unicode directly)")
    try:
        plan = keymap.keycode_plan(a.text or "")
        taps = [(code, "shift" in mods) for mods, code in keymap.parse_chords(a.keys)] if a.keys else []
    except ValueError as e:
        return err(str(e))
    if a.expect:
        front = b.foreground()
        name = f"{front.get('process', '')} {front.get('title', '')}"
        if a.expect.lower() not in name.lower():
            return err(f"foreground changed: expected '{a.expect}', found '{front.get('title') or '(none)'}'", GUARD)
    speed = a.speed or "fast"
    gap = 0 if speed == "instant" else M.TYPE_MS[speed]
    b.release_mods()
    try:
        for i, (code, shift) in enumerate(plan):
            if i % 50 == 0:
                guard.check()
            b.key_tap(code, shift)
            sleep_ms(gap or 8)
        for code, shift in taps:
            guard.check()
            b.key_tap(code, shift)
            sleep_ms(30)
        if a.text and not a.no_enter:
            sleep_ms(300)
            guard.check()
            b.key_tap(36, False)
    except GuardError as e:
        return err(str(e), GUARD)
    finally:
        b.release_mods()
    return OK


def cmd_type(a, b) -> int:
    if a.release_mods and not a.text and not a.keys:
        b.release_mods()
        return OK
    if not b.input_ok():
        return err(no_input_msg(b), PERMS)
    guard = make_guard(a, b)
    if a.keycodes:
        return cmd_type_keycodes(a, b, guard)
    if a.keys:
        return err("--keys goes with --keycodes; for chords use `pointer keys`")
    if a.text is None:
        return err("type needs --text")
    pre = M.env_prefix()
    speed = a.speed or os.environ.get(pre + "TYPE_SPEED") or (
        "medium" if M.env_flag(os.environ.get(pre + "RECORD", "")) else "instant")
    if speed not in M.TYPE_MS and speed != "instant":
        return err(f"type speed must be instant, slow, medium or fast, not {speed!r}")
    try:
        guard.check()
        if speed == "instant":
            b.type_text(a.text)
        else:
            for i, (ch, delay) in enumerate(M.type_schedule(a.text, speed, random.Random())):
                sleep_ms(delay)
                if i and i % 50 == 0:
                    guard.check()
                b.type_char(ch)
        if not a.no_enter:
            sleep_ms(random.uniform(250, 500))
            guard.check()
            b.press_enter()
    except GuardError as e:
        return err(str(e), GUARD)
    return OK


def cmd_perms(a, b) -> int:
    ask = getattr(a, "ask", False)
    ax, sc = b.accessibility_ok(ask), b.screen_capture_ok(ask)
    if b.NAME == "linux":
        print(f"X display {'reachable' if ax else 'NOT reachable'} (input and screenshots)")
    elif b.NAME == "windows":
        print("no permissions needed on Windows")
    else:
        print(f"accessibility {'granted' if ax else 'MISSING'}; screen recording {'granted' if sc else 'MISSING'}")
    if b.NAME == "macos" and not (ax and sc):
        print("Grant both to the host app (it may be a versioned helper bundle of the agent app), then restart it.")
    return OK if ax else PERMS


def cmd_glow(a, b) -> int:
    if os.environ.get(M.env_prefix() + "GLOW", "1").strip() == "0":
        print("glow disabled")
        return OK
    b.glow(a.kind)
    print(f"glow {a.kind}")
    return OK


def cmd_capture(a, b) -> int:
    if a.op == "start":
        return capture.start(b, a.video, a.trail, a.name)
    if a.op == "stop":
        return capture.stop(b)
    return capture.clear()


def add_guard_args(p):
    p.add_argument("--title", help="focus/window: window to act on; input: foreground title must contain this")
    p.add_argument("--no-guard", action="store_true", help="skip the foreground check (desktop-wide targets)")


def parser():
    ap = argparse.ArgumentParser(description="Screenshots plus real mouse and keyboard input for native windows.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("shot", help="capture a display to PNG")
    s.add_argument("--out", default=default_shot())
    s.add_argument("--display", type=int, default=0, help="0 = primary")

    p = sub.add_parser("pointer", help="move, click, scroll, drag, gestures, tour, focus, keys")
    p.add_argument("action", choices=POINTER_ACTIONS)
    p.add_argument("x", type=int, nargs="?")
    p.add_argument("y", type=int, nargs="?")
    p.add_argument("--delta", type=int, default=-360, help="wheel units, 120 per notch, negative scrolls down")
    p.add_argument("--to-x", type=int, dest="to_x")
    p.add_argument("--to-y", type=int, dest="to_y")
    p.add_argument("--back", action="store_true", help="drag: glide back to the start before releasing")
    p.add_argument("--steps", help='tour: "name X Y [W H]; ..." with click rclick move hover underline circle')
    p.add_argument("--keep-order", action="store_true", help="tour: run the steps as written")
    p.add_argument("--state", choices=["min", "restore", "max"], help="window: what to do with every match")
    p.add_argument("--prefer", default="unsloth-studio", help="process name preferred when several windows match")
    p.add_argument("--keys", help="Windows: SendKeys ({ESC} ^a {TAB}); macOS and Linux: chords (cmd+a esc)")
    p.add_argument("--w", type=int)
    p.add_argument("--h", type=int)
    p.add_argument("--r", type=int)
    p.add_argument("--instant", action="store_true")
    p.add_argument("--speed", choices=list(M.SPEED_FACTOR))
    p.add_argument("--natural", action="store_true")
    p.add_argument("--record", action="store_true")
    p.add_argument("--hold", type=int, help="ms to hold after the action")
    p.add_argument("--side", choices=["right", "below"])
    p.add_argument("--cursor", type=int, help="recorded cursor height in px, for gesture geometry")
    p.add_argument("--ask", action="store_true", help="perms: open the macOS settings pane")
    add_guard_args(p)

    t = sub.add_parser("type", help="type literal text")
    t.add_argument("--text")
    t.add_argument("--speed", choices=["instant", "slow", "medium", "fast"])
    t.add_argument("--no-enter", action="store_true")
    t.add_argument("--keycodes", action="store_true", help="macOS: one physical US key per char (remote desktops)")
    t.add_argument("--expect", help="--keycodes: the frontmost app must contain this")
    t.add_argument("--keys", help="--keycodes: named keys to tap after the text (tab enter esc)")
    t.add_argument("--release-mods", action="store_true", help="release every modifier key and exit")
    add_guard_args(t)

    q = sub.add_parser("perms", help="permission / display status")
    q.add_argument("--ask", action="store_true")
    g = sub.add_parser("glow", help="edge glow when taking or handing back control")
    g.add_argument("kind", choices=["start", "stop"])
    c = sub.add_parser("capture", help="cursor trail, screen video and keep-awake")
    c.add_argument("op", choices=["start", "stop", "clear"])
    c.add_argument("--video", action="store_true")
    c.add_argument("--trail", action="store_true")
    c.add_argument("--name")
    return ap


HANDLERS = {"shot": cmd_shot, "pointer": cmd_pointer, "type": cmd_type, "perms": cmd_perms, "glow": cmd_glow,
            "capture": cmd_capture}


def main(argv=None, backend=None) -> int:
    a = parser().parse_args(argv)
    try:
        b = backend or platforms.load()
        return HANDLERS[a.cmd](a, b)
    except Unsupported as e:
        return err(str(e), e.code)


if __name__ == "__main__":
    sys.exit(main())
