#!/usr/bin/env bash
# install.sh — symlink the 3-level assess hook + the two skills into ~/.claude/.
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
# A separate skill on purpose, and its name must not contain "assess": the Stop hook's
# is_assess_launch() keys on that substring, so an on-demand panel run under an assess-ish
# name would spend the cycle's review flag and let the work that follows ship unreviewed.
link "$here/skills/panel"         "$CLAUDE/skills/panel"

# `panel` folded in the former `scope` and `consult` skills. Drop their links, or the
# retired names keep showing up as live skills and each one points at a directory that no
# longer exists. Only links into THIS repo are removed; anything else of yours is left.
for old in scope consult; do
  d="$CLAUDE/skills/$old"
  if [ -L "$d" ]; then
    tgt="$(readlink "$d")"
    case "$tgt" in
      "$here"/*) rm "$d"; echo "removed:  $d (folded into panel)" ;;
      *)
        # A link made from a different clone path (repo moved, second checkout) fails the
        # match above, but its target went away with the rename too — a dangling skill
        # either way. Only a link that still resolves is someone else's and stays.
        if [ ! -e "$d" ]; then
          rm "$d"; echo "removed:  $d (dangling link to a retired skill)"
        else
          echo "kept:     $d -> $tgt (still resolves, not ours)"
        fi ;;
    esac
  elif [ -d "$d" ]; then
    echo "WARNING:  $d is a real directory, not a link. The panel skill replaces it, and"
    echo "          Claude Code will keep listing it as a live skill until you remove it."
  fi
done

echo
echo "Done."
echo "If not already registered, add the Stop hook once to ~/.claude/settings.json:"
echo '  {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/stop_assess.py"}]}]}}'
echo "Codex model/effort are pinned in panel.sh (gpt-5.6-sol / xhigh; needs codex-cli >= 0.144)."
echo "Grok (grok-4.6 / xhigh) is a DEFAULT third reviewer: it needs the grok CLI"
echo "(curl -fsSL https://x.ai/cli/install.sh | bash) plus a one-time \`grok login\`."
echo "Without them every panel prints a SKIPPED notice naming the cause; set"
echo "ASSESS_PANEL_GROK=0 to drop the reviewer instead — see 'The Grok reviewer' in README.md."
echo "Optional fourth reviewer (off by default): ASSESS_PANEL_PI=1 adds pi/Kimi K3 as another"
echo "lineage. Needs \`pi\` on PATH plus a provider key — see 'The fourth reviewer' in README.md."
