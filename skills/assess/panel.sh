#!/usr/bin/env bash
# panel.sh — cross-lineage review panel for `/assess turbo`.
#
# Runs two INDEPENDENT skeptical reviewers in parallel on the same artifact:
#   1. Codex   — gpt-5.5 / xhigh from ~/.codex/config.toml; a different model lineage.
#   2. Fresh Claude — clean context, read-only; same lineage, but no anchoring.
# Both are told to REFUTE, not to approve. Neither sees the other's output or any
# prior review, so their errors stay independent. Reconciliation is the caller's job.
#
# Usage:  panel.sh <artifact-file> [task description...]
#   <artifact-file>  path to the diff or file(s) to review (may live anywhere, e.g. /tmp)
#   [task ...]       the ORIGINAL task, verbatim — not a summary of what was done
#
# Reviewers root in $PWD (override: ASSESS_PANEL_ROOT) so they can Read/Grep the
# project, not just the artifact text. Per-reviewer timeout: ASSESS_PANEL_TIMEOUT (300s).
# Prints both reviews, labeled. Exits 0 even if one reviewer fails (the other prints).

set -u

artifact="${1:-}"
shift || true
task="$*"

if [ -z "$artifact" ] || [ ! -f "$artifact" ]; then
  echo "panel.sh: need an existing artifact file as \$1" >&2
  exit 2
fi
[ -n "$task" ] || task="(no task description given — infer intent from the artifact)"

# Reviewers root in the PROJECT (so they can Read/Grep surrounding code), not in the
# artifact's folder — the diff often lives in /tmp. Defaults to the caller's cwd (the
# project Claude is working in); override with ASSESS_PANEL_ROOT.
root="${ASSESS_PANEL_ROOT:-$PWD}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

SYSTEM="You are a skeptical senior reviewer. You are given a TASK and an ARTIFACT (a diff or file) produced by another engineer. Find REAL problems: correctness bugs, unhandled edge cases, false assumptions, security issues, or places where the work solved the wrong problem. Be concrete — name the line or construct. If you genuinely find nothing serious, say so plainly; do NOT invent nits to look thorough. Judge independently; assume no previous review was correct. Output a short list, each item prefixed with a severity tag [HIGH], [MED], or [LOW]."

PROMPT="$SYSTEM

# TASK
$task

# ARTIFACT
$(cat "$artifact")"

TIMEOUT="${ASSESS_PANEL_TIMEOUT:-300}"

codex_out="$tmp/codex.txt"
claude_out="$tmp/claude.txt"

# Codex — different lineage. Reads prompt from stdin, writes final message to -o.
( printf '%s' "$PROMPT" | codex exec \
    -m "${ASSESS_CODEX_MODEL:-gpt-5.5}" \
    -c "model_reasoning_effort=\"${ASSESS_CODEX_EFFORT:-xhigh}\"" \
    --skip-git-repo-check --sandbox read-only --ephemeral --color never \
    -C "$root" -o "$codex_out" >/dev/null 2>"$tmp/codex.log" ) &
codex_pid=$!

# Fresh Claude — clean context, read-only tools, no MCP.
# Prompt goes via stdin: --add-dir/--allowedTools are variadic and would otherwise
# swallow a trailing positional prompt as an extra directory/tool.
( cd "$root" && printf '%s' "$PROMPT" | claude -p --model opus --effort high \
    --strict-mcp-config --no-session-persistence --permission-mode default \
    --add-dir "$root" --allowedTools Read Grep Glob \
    >"$claude_out" 2>"$tmp/claude.log" ) &
claude_pid=$!

# Watchdog: kill either reviewer that overruns (macOS has no `timeout` by default).
# disown so the shell doesn't print a "Terminated" job message when we kill it.
( sleep "$TIMEOUT"; kill "$codex_pid" "$claude_pid" 2>/dev/null ) &
watchdog_pid=$!
disown "$watchdog_pid" 2>/dev/null || true

wait "$codex_pid" 2>/dev/null
wait "$claude_pid" 2>/dev/null
kill "$watchdog_pid" 2>/dev/null

echo "================ CODEX (gpt-5.5 — different lineage) ================"
if [ -s "$codex_out" ]; then
  cat "$codex_out"
else
  echo "(no output)"; tail -n 5 "$tmp/codex.log" 2>/dev/null
fi
echo
echo "================ FRESH CLAUDE (clean context — no anchoring) ================"
if [ -s "$claude_out" ]; then
  cat "$claude_out"
else
  echo "(no output)"; tail -n 5 "$tmp/claude.log" 2>/dev/null
fi
echo
echo "================ END PANEL ================"
echo "Reconcile by axis (do NOT majority-vote):"
echo " - verifiable finding -> check it yourself, fix if real"
echo " - raised by one reviewer only -> investigate; disagreement is signal"
echo " - both say fine -> high confidence"
