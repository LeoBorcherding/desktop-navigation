"""Pick the backend for this OS. Every backend exposes the same primitive surface (see SKILL.md)."""
from __future__ import annotations

import sys

from core.exits import ARGS, PERMS, Unsupported


def load(platform: str | None = None):
    platform = platform or sys.platform
    try:
        if platform == "win32":
            from platforms import windows as b
        elif platform == "darwin":
            from platforms import macos as b
        elif platform.startswith("linux"):
            from platforms import linux as b
        else:
            raise Unsupported(f"desktop-navigation runs on Windows, macOS and Linux (X11); this is {platform}. "
                              "For anything served on localhost use a browser tool instead.", ARGS)
    except (ImportError, OSError) as e:
        raise Unsupported(f"the {platform} backend could not load its system libraries: {e}", PERMS) from e
    b.init()
    return b
