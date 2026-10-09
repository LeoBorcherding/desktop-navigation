# Claude Code skills

One folder per skill: a `SKILL.md` that Claude loads only when a task matches its description, plus the
helper scripts it calls and their tests. Unlike `claude/workflows/`, nothing here is inlined into CLAUDE.md.
Helpers are standalone (stdlib plus optional platform tools) and run from `~/.claude/skills/<name>/`.

## Install

```bash
bash claude/skills/install.sh                          # every skill, existing copies kept
bash claude/skills/install.sh --force desktop-navigation   # one skill, replacing the installed copy
```

Only copies into `~/.claude/skills`; `settings.json` is left alone. Tests run from the repo: `python -m pytest claude/skills/<name>/tests -q`.

## Skills

| Skill | Use it for | Entry point |
|---|---|---|
| `desktop-navigation` | driving a native window (Unsloth Desktop, any app) with screenshots and real input on Windows, macOS and Linux (X11), with gestures, tours, a cursor trail and screen video for recordings | `drive.py` |
