"""Chord grammar shared by macOS and Linux, US-ANSI keycodes, and the macOS keycode -> X keysym table.

Chords are space separated, modifiers joined with `+`: `cmd+a esc shift+tab`.
"""
from __future__ import annotations

NAMED = {"enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "delete": 51, "esc": 53,
         "escape": 53, "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119,
         "pageup": 116, "pagedown": 121, "forwarddelete": 117}

# US-ANSI virtual keycodes: unshifted char -> keycode, and the shifted char on the same key.
_ROWS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
    ".": 47, "`": 50,
}
_SHIFTED = dict(zip("!@#$%^&*()_+{}|:\"<>?~", "1234567890-=[]\\;',./`"))

US_KEYS: dict[str, tuple[int, bool]] = {c: (k, False) for c, k in _ROWS.items()}
US_KEYS.update({c.upper(): (k, True) for c, k in _ROWS.items() if c.isalpha()})
US_KEYS.update({s: (_ROWS[base], True) for s, base in _SHIFTED.items()})
US_KEYS[" "] = (49, False)
US_KEYS["\t"] = (48, False)

MODS = {"cmd": "cmd", "command": "cmd", "ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt",
        "opt": "alt", "option": "alt"}
MAC_MOD_KEYCODE = {"cmd": 55, "shift": 56, "alt": 58, "ctrl": 59}
MAC_MOD_FLAG = {"cmd": 1 << 20, "shift": 1 << 17, "alt": 1 << 19, "ctrl": 1 << 18}
# Both sides of every modifier plus Fn, released before and after remote-desktop typing.
MAC_ALL_MOD_KEYCODES = (55, 54, 56, 60, 59, 62, 58, 61, 63)
SHIFT_KEYCODE = 56

_X_NAMED = {36: "Return", 48: "Tab", 49: "space", 51: "BackSpace", 53: "Escape", 117: "Delete", 123: "Left",
            124: "Right", 125: "Down", 126: "Up", 115: "Home", 119: "End", 116: "Prior", 121: "Next"}
_X_PUNCT = {"=": "equal", "-": "minus", "]": "bracketright", "[": "bracketleft", "'": "apostrophe",
            ";": "semicolon", "\\": "backslash", ",": "comma", "/": "slash", ".": "period", "`": "grave"}
MAC_TO_X = dict(_X_NAMED)
MAC_TO_X.update({k: _X_PUNCT.get(c, c) for c, k in _ROWS.items()})
# On Linux cmd and ctrl are both Control: shared scripts that send cmd+c must copy, not press Super.
X_MODS = {"cmd": "Control_L", "ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L"}

X_PUNCT_CHARS = {" ": "space", "\t": "Tab", "!": "exclam", '"': "quotedbl", "#": "numbersign",
                 "$": "dollar", "%": "percent", "&": "ampersand", "'": "apostrophe", "(": "parenleft",
                 ")": "parenright", "*": "asterisk", "+": "plus", ",": "comma", "-": "minus", ".": "period",
                 "/": "slash", ":": "colon", ";": "semicolon", "<": "less", "=": "equal", ">": "greater",
                 "?": "question", "@": "at", "[": "bracketleft", "\\": "backslash", "]": "bracketright",
                 "^": "asciicircum", "_": "underscore", "`": "grave", "{": "braceleft", "|": "bar",
                 "}": "braceright", "~": "asciitilde"}


def key_code(name: str) -> int:
    n = name.lower()
    if n in NAMED:
        return NAMED[n]
    if len(name) == 1 and name.lower() in _ROWS:
        return _ROWS[name.lower()]
    raise ValueError(f"unknown key {name!r}")


def parse_chords(spec: str) -> list[tuple[tuple[str, ...], int]]:
    """`cmd+a esc` -> [(("cmd",), 0), ((), 53)]. ValueError on an unknown key or modifier."""
    out = []
    for chord in spec.split():
        parts = chord.split("+")
        if chord.endswith("++"):
            parts = parts[:-2] + ["+"]
        *mods, key = parts
        bad = [m for m in mods if m.lower() not in MODS]
        if bad or not key:
            raise ValueError(f"unknown modifier or empty key in {chord!r}")
        out.append((tuple(MODS[m.lower()] for m in mods), key_code(key)))
    if not out:
        raise ValueError("no keys given")
    return out


def keycode_plan(text: str) -> list[tuple[int, bool]]:
    """Physical keycodes for remote-desktop typing. ValueError listing every char with no US key."""
    bad = sorted({c for c in text if c not in US_KEYS and c != "\n"})
    if bad:
        raise ValueError("no US key for: " + " ".join(repr(c) for c in bad))
    return [(36, True) if c == "\n" else US_KEYS[c] for c in text]


def x_keysym_name(mac_code: int) -> str:
    try:
        return MAC_TO_X[mac_code]
    except KeyError:
        raise ValueError(f"no X keysym for keycode {mac_code}") from None


def x_char_keysym(ch: str) -> str | None:
    if ch.isascii() and ch.isalnum():
        return ch
    return X_PUNCT_CHARS.get(ch)
