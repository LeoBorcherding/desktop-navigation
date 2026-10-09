"""Exit codes and the exceptions that map onto them."""
from __future__ import annotations

OK, CAPTURE, ARGS, TAKEOVER, FOCUS, PERMS, GUARD = 0, 1, 2, 3, 4, 5, 6


class Takeover(Exception):
    """The real cursor left the path we set: a person has the mouse."""


class GuardError(Exception):
    """The foreground window is not the one we were told to drive."""


class Unsupported(Exception):
    """This OS or session cannot do the requested thing."""

    def __init__(self, msg: str, code: int = ARGS):
        super().__init__(msg)
        self.code = code
