#!/usr/bin/env bash
# install.sh — symlink the 3-level assess hook + both skills into ~/.claude/.
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
# Separate skill on purpose, and the name must not contain "assess": the Stop hook's
# is_assess_launch() keys on that substring, so a scope run under an assess-ish name would
# spend the cycle's review flag and let the work that follows ship unreviewed.
link "$here/skills/scope"         "$CLAUDE/skills/scope"

echo
echo "Done."
echo "If not already registered, add the Stop hook once to ~/.claude/settings.json:"
echo '  {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/stop_assess.py"}]}]}}'
echo "Codex model/effort are pinned in panel.sh (gpt-5.6-sol / xhigh; needs codex-cli >= 0.144)."
echo "Optional third reviewer (off by default): ASSESS_PANEL_PI=1 adds pi/Kimi K3 as a third"
echo "lineage. Needs \`pi\` on PATH plus a provider key — see 'The third reviewer' in README.md."
