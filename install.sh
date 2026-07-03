#!/usr/bin/env bash
# install.sh — symlink the 3-level assess hook + skill into ~/.claude/.
# Backs up anything real that is already there. Nothing in your live setup
# changes until you run this. Re-running is safe (idempotent).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
CLAUDE="$HOME/.claude"
BACKUPS="$CLAUDE/backups"
ts="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$CLAUDE/hooks" "$CLAUDE/skills"
chmod +x "$here/skills/assess/panel.sh"

link() { # link <src> <dst>
  local src="$1" dst="$2"
  if [ -L "$dst" ]; then
    rm "$dst"
  elif [ -e "$dst" ]; then
    # Back up OUTSIDE ~/.claude/skills: a directory left in there (e.g.
    # skills/assess.bak.<ts>) is picked up by Claude Code as a live,
    # selectable skill and pollutes the skill list in every session.
    mkdir -p "$BACKUPS"
    mv "$dst" "$BACKUPS/$(basename "$dst").bak.$ts"
    echo "backed up: $dst -> $BACKUPS/$(basename "$dst").bak.$ts"
  fi
  ln -s "$src" "$dst"
  echo "linked:   $dst -> $src"
}

link "$here/hooks/stop_assess.py" "$CLAUDE/hooks/stop_assess.py"
link "$here/skills/assess"        "$CLAUDE/skills/assess"

echo
echo "Done."
echo "If not already registered, add the Stop hook once to ~/.claude/settings.json:"
echo '  {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/stop_assess.py"}]}]}}'
echo "Codex uses model/effort from ~/.codex/config.toml (currently gpt-5.5 / xhigh)."
