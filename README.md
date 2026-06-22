# assess-panel

**A self-review step for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that, when you've done substantial work, brings in a *different* AI model and a *fresh, un-primed* instance to catch the mistakes the working agent can't see in its own output.**

## The problem

When a coding agent finishes a task, the obvious quality check is to have it review its own work. That catches typos, off-by-ones, and forgotten edge cases — real but shallow mistakes. It does **not** catch two deeper failure modes:

- **Model-systematic blind spots.** Every run of the same model shares the same priors. If the model misjudges a class of problem, re-reading its own output misjudges it the same way — you can't out-think your own blind spot using the same brain.
- **Anchoring / "solved the wrong problem."** By the time it reviews, the session has already convinced itself what the task means and that it's done. Reviewing inside that same context inherits the misunderstanding.

## The idea

Add reviewers that **fail differently** from the working agent, so their mistakes don't line up with its blind spots:

- a **different model** — OpenAI's Codex — whose different training catches model-systematic errors; and
- a **fresh instance of the same model** with a clean context, which, never having seen the session's reasoning, catches anchoring and wrong-problem errors.

The point is not "a smarter reviewer." It's **decorrelation**: two checkers whose errors are independent of the author's. The chance all three miss the same bug is far lower than any one alone. Notably this holds *even if the reviewers are no more capable* than the working agent — independence, not capability, is what buys the catch.

Independent review costs tokens and time, so it's **gated**: a cheap classifier decides per turn whether the work is substantial enough to be worth it.

## What it is

Two pieces that plug into Claude Code:

- **An `/assess` skill** — a structured self-review (for code: correctness, edge cases, security, consistency; for research: sourcing and internal consistency; for plans: simplicity and completeness). You can run it by hand (`/assess`), or let the hook trigger it.
- **A Stop hook** — a script Claude Code runs every time the agent finishes a turn. It can block the turn and tell the agent to do something first. Here it classifies the turn into one of three review levels.

![How assess-panel works](docs/how-it-works.svg)

## Three levels (the Stop hook picks one automatically)

- **none** — trivial turn (a reply, a tiny edit). No review.
- **normal** — real but bounded work (a function, a bugfix). The agent reviews its own work in-session.
- **turbo** — a substantial artifact was produced (a new module, a large refactor, a full report). On top of the self-review, it runs the cross-model panel below and reconciles everything.

A cheap classifier (Claude Sonnet, low effort) decides the level on every Stop. You can also force the deepest level yourself with `/assess turbo`.

## The turbo panel

`panel.sh` runs two independent skeptical reviewers **in parallel** (wall-clock ≈ the slower one, not the sum) over the same artifact:

- **Codex** — `gpt-5.5` at `xhigh` effort; a different model lineage.
- **Fresh Claude** — a clean `claude -p` context, read-only tools (`Read`/`Grep`/`Glob`), no MCP.

Both are told to **find problems, not to praise**, and to judge independently. Neither sees the other's output or any prior review, so their errors stay decorrelated. The reviewer prompt is fixed inside the script (it's a guard — not something the orchestrator rewrites per call), and the *task* handed to them is the original request verbatim, never the agent's summary of what it did (a paraphrase would re-anchor them).

## How findings are combined

Not by majority vote — two of the three voices are the same model lineage and correlate. Reconcile **by axis** instead:

- a **verifiable** finding (a real bug, a failing case, a spec mismatch) → check it yourself, fix if real;
- a finding **one reviewer raised and the other missed** → investigate it; cross-lineage disagreement is signal, not a minority to overrule;
- **both agree it's fine** → high confidence, move on.

## Requirements

- **Claude Code** (`claude` CLI), authenticated.
- **Codex** (`codex` CLI), authenticated. Model and effort are pinned to `gpt-5.5` / `xhigh` (override with `ASSESS_CODEX_MODEL` / `ASSESS_CODEX_EFFORT`).
- macOS or Linux (the hook and `panel.sh` are bash + standard tools).

## Files

- `hooks/stop_assess.py` — the 3-level gate (the Sonnet classifier).
- `skills/assess/SKILL.md` — the review instructions, normal + turbo branches.
- `skills/assess/panel.sh` — runs the two reviewers in parallel on an artifact.
- `install.sh` — symlinks the hook + skill into `~/.claude/` (backs up anything already there).

## Install

Review the files, then:

```bash
bash install.sh
```

It backs up any existing `~/.claude/hooks/stop_assess.py` and `~/.claude/skills/assess`, then symlinks this repo in their place (so edits here go live immediately). You must also register the Stop hook once in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 ~/.claude/hooks/stop_assess.py" } ] }
    ]
  }
}
```

## Notes

- Both reviewers run **read-only** — they cannot edit your files.
- Per-reviewer timeout defaults to 300s (`ASSESS_PANEL_TIMEOUT` to override).
- The panel reviewers root in the project directory (`$PWD`, or `ASSESS_PANEL_ROOT`) so they can read the surrounding code, not just the artifact text.
