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
# project, not just the artifact text. Per-reviewer timeout: ASSESS_PANEL_TIMEOUT (540s).
# Both reviewers run at xhigh effort and may Read/Grep a large repo, so this is generous —
# but it stays UNDER the caller's 10-min foreground Bash-tool cap so the panel always
# finishes and prints (with a clear TIMED OUT notice) instead of the whole command being
# killed with no output. For longer budgets, run the panel backgrounded and raise this var.
# A reviewer that times out or crashes says so EXPLICITLY (never a bare "(no output)"), so a
# dead reviewer is never mistaken for "all clear". Exits 0 even if one reviewer fails.

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

TIMEOUT="${ASSESS_PANEL_TIMEOUT:-540}"

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
( cd "$root" && printf '%s' "$PROMPT" | claude -p --model opus --effort xhigh \
    --strict-mcp-config --no-session-persistence --permission-mode default \
    --add-dir "$root" --allowedTools Read Grep Glob \
    >"$claude_out" 2>"$tmp/claude.log" ) &
claude_pid=$!

# Watchdog: kill either reviewer that overruns (macOS has no `timeout` by default).
# It drops a sentinel BEFORE killing so the reporting below can tell a timeout-kill
# (rc 143 + sentinel) apart from a clean empty exit. For each reviewer we kill the actual
# child (`pkill -P` → the codex/claude process inside the wrapper) AND the wrapper subshell:
# without the `pkill`, killing only the subshell orphans the reviewer, which keeps running
# and burning xhigh API budget for output we already discarded. Killing the subshell too
# keeps `wait` returning 143 (a plain bash dies on SIGTERM), which the report() relies on.
# disown so the shell doesn't print a "Terminated" job message when we kill it.
( sleep "$TIMEOUT"; : >"$tmp/timedout"
  for p in "$codex_pid" "$claude_pid"; do
    pkill -TERM -P "$p" 2>/dev/null   # the reviewer process (codex/claude) inside the wrapper
    kill  -TERM    "$p" 2>/dev/null   # the wrapper subshell -> `wait` still yields rc 143
  done ) &
watchdog_pid=$!
disown "$watchdog_pid" 2>/dev/null || true

wait "$codex_pid" 2>/dev/null; codex_rc=$?
wait "$claude_pid" 2>/dev/null; claude_rc=$?
kill "$watchdog_pid" 2>/dev/null

# Print one reviewer. An EMPTY result is never silent: it states WHY (timed out /
# crashed / genuinely found nothing) so the caller cannot mistake a dead reviewer for
# "all clear" — the exact bug that quietly halved a past panel to one lineage.
# Branch on EXIT STATUS first, output last — a reviewer killed mid-run can have flushed
# partial text, and printing that as if complete (the old output-first order) is the exact
# silent-degradation this change exists to kill. Our watchdog sends SIGTERM (rc 143), so a
# timeout is rc==143 + sentinel; any other signal is a real crash, not a timeout.
report() { # report <label> <outfile> <logfile> <rc>
  local label="$1" out="$2" log="$3" rc="$4"
  echo "================ $label ================"
  if [ "$rc" -eq 143 ] && [ -f "$tmp/timedout" ]; then
    echo ">>> TIMED OUT after ${TIMEOUT}s — watchdog killed this reviewer mid-run; any output below is PARTIAL, not a finished review."
    echo ">>> The panel is INCOMPLETE: do NOT read this as 'clean'. To allow longer, run the panel BACKGROUNDED (to clear the 10-min foreground Bash cap) with e.g. ASSESS_PANEL_TIMEOUT=1500."
    [ -s "$out" ] && { echo "--- partial output before kill ---"; cat "$out"; }
    tail -n 5 "$log" 2>/dev/null
  elif [ "$rc" -ge 128 ]; then
    echo ">>> KILLED by signal $((rc-128)) (not the watchdog) — reviewer died mid-run; any output below is PARTIAL. Last log lines:"
    [ -s "$out" ] && { echo "--- partial output before kill ---"; cat "$out"; }
    tail -n 15 "$log" 2>/dev/null
  elif [ "$rc" -ne 0 ]; then
    echo ">>> FAILED (exit $rc) — reviewer errored. Output (if any) below; last log lines after:"
    [ -s "$out" ] && cat "$out"
    tail -n 15 "$log" 2>/dev/null
  elif [ -s "$out" ]; then
    cat "$out"
  else
    echo ">>> No findings — reviewer exited cleanly (exit 0) with empty output."
    tail -n 5 "$log" 2>/dev/null
  fi
}

report "CODEX (gpt-5.5 — different lineage)" "$codex_out" "$tmp/codex.log" "$codex_rc"
echo
report "FRESH CLAUDE (clean context — no anchoring)" "$claude_out" "$tmp/claude.log" "$claude_rc"
echo
echo "================ END PANEL ================"
echo "Reconcile by axis (do NOT majority-vote):"
echo " - verifiable finding -> check it yourself, fix if real"
echo " - raised by one reviewer only -> investigate; disagreement is signal"
echo " - both say fine -> high confidence"
echo " - a reviewer TIMED OUT / FAILED -> panel is INCOMPLETE; say so and re-run, do NOT treat as 'all clear'"
