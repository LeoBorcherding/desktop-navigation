#!/bin/bash
# Usage: claude/skills/install.sh [--force] [name ...]
#   Copies claude/skills/<name>/ into ~/.claude/skills. Installed skills are kept unless --force.
#   Touches nothing else in ~/.claude (settings.json stays as it is).
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.claude/skills"
FORCE=0; NAMES=()
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -h|--help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) NAMES+=("$arg") ;;
    esac
done
if [[ ${#NAMES[@]} -eq 0 ]]; then
    for dir in "$SRC"/*/; do NAMES+=("$(basename "$dir")"); done
fi
mkdir -p "$DEST"
for name in "${NAMES[@]}"; do
    [[ -f "$SRC/$name/SKILL.md" ]] || { echo "no skill named $name" >&2; exit 2; }
    if [[ -e "$DEST/$name" && "$FORCE" != 1 ]]; then
        echo "Kept existing $DEST/$name (use --force to replace)"
        continue
    fi
    rm -rf "${DEST:?}/$name"
    cp -r "$SRC/$name" "$DEST/$name"
    find "$DEST/$name" \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +
    echo "Installed $name"
done
