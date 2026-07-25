# assess-panel

**A review step for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that brings in a *different* AI model and a *fresh, un-primed* instance to catch the mistakes the working agent can't see by itself — after substantial work (did it do this right?) or, on demand, before it starts (does it even know what you asked?).**

## The problem

When a coding agent finishes a task, the obvious quality check is to have it review its own work. That catches typos, off-by-ones, and forgotten edge cases — real but shallow mistakes. It does **not** catch two deeper failure modes:

- **Model-systematic blind spots.** Every run of the same model shares the same priors. If the model misjudges a class of problem, re-reading its own output misjudges it the same way — you can't out-think your own blind spot using the same brain.
- **Anchoring / "solved the wrong problem."** By the time it reviews, the session has already convinced itself what the task means and that it's done. Reviewing inside that same context inherits the misunderstanding.

## The idea

Add reviewers that **fail differently** from the working agent, so their mistakes don't line up with its blind spots:

- a **different model** — OpenAI's Codex — whose different training catches model-systematic errors; and
- a **fresh instance of the same model** with a clean context, which, never having seen the session's reasoning, catches anchoring and wrong-problem errors.
- optionally a **third lineage** — Moonshot's Kimi via the [`pi`](https://pi.dev) agent — off unless you turn it on, because it needs a binary and a provider key this repo can't assume you have.

The point is not "a smarter reviewer." It's **decorrelation**: checkers whose errors are independent of the author's. The chance all of them miss the same bug is far lower than any one alone. Notably this holds *even if the reviewers are no more capable* than the working agent — independence, not capability, is what buys the catch.

Independent review costs tokens and time, so it's **gated**: a cheap classifier decides per turn whether the work is substantial enough to be worth it.

## What it is

Three pieces that plug into Claude Code:

- **An `/assess` skill** — a structured self-review (for code: correctness, edge cases, security, consistency; for research: sourcing and internal consistency; for plans: simplicity and completeness). You can run it by hand (`/assess`), or let the hook trigger it.
- **A Stop hook** — a script Claude Code runs every time the agent finishes a turn. It can block the turn and tell the agent to do something first. Here it classifies the turn into one of three review levels.
- **A `/scope` skill** — the same panel run on the *request*, before work starts. Deliberately a separate skill rather than a mode of `/assess`; [see below](#scope-the-same-trick-before-the-work) for why the split is load-bearing.

![How assess-panel works](docs/how-it-works.svg)

## Three levels (the Stop hook picks one automatically)

- **none** — trivial turn (a reply, a tiny edit). No review.
- **normal** — real but bounded work (a function, a bugfix). The agent reviews its own work in-session.
- **turbo** — a substantial artifact was produced (a new module, a large refactor, a full report). On top of the self-review, it runs the cross-model panel below and reconciles everything.

A cheap classifier (Claude Sonnet, low effort) decides the level on every Stop. You can also force the deepest level yourself with `/assess turbo`.

## The turbo panel

`panel.sh` runs independent skeptical reviewers **in parallel** (wall-clock ≈ the slowest one, not the sum) over the same artifact:

- **Codex** — `gpt-5.6-sol` at `xhigh` effort; a different model lineage.
- **Fresh Claude** — a clean `claude -p` context, read-only tools (`Read`/`Grep`/`Glob`), no MCP.
- **Pi** *(optional, `ASSESS_PANEL_PI=1`)* — `moonshotai/kimi-k3` at `high` via OpenRouter; a third lineage. See [the third reviewer](#the-third-reviewer-optional).

All are told to **find problems, not to praise**, and to judge independently. None sees the others' output or any prior review, so their errors stay decorrelated. The reviewer prompt is fixed inside the script (it's a guard — not something the orchestrator rewrites per call), and the *task* handed to them is the original request verbatim, never the agent's summary of what it did (a paraphrase would re-anchor them).

## How findings are combined (turbo)

Not by majority vote — the working agent and the fresh Claude are the same model lineage and correlate. Reconcile **by axis** instead:

- a **verifiable** finding (a real bug, a failing case, a spec mismatch) → check it yourself, fix if real;
- a finding **one reviewer raised and the others missed** → investigate it; cross-lineage disagreement is signal, not a minority to overrule;
- **all agree it's fine** → high confidence, move on.

With the third reviewer on, a 2-1 split is especially not a vote: the working agent and the fresh Claude agreeing against Codex or Pi is *one* lineage, not two.

## Scope: the same trick, before the work

`/scope` runs the same two reviewers at the *other* end of a task — on the request,
before anything is built. Decorrelation is worth more here: a misread request at t=0 wastes
the entire task, while a bug at t=1 wastes a fix.

But the question has to change. After the work there is an artifact, so "is this wrong?" is
decidable against evidence. Before it there is nothing to refute, and asking two models
"how should this be done?" just yields two plausible plans and no way to choose. So the
reviewers are asked what they think is being **asked**, never how to do it:

- **Codex** answers a fixed six-slot schema — deliverable, underlying question, assumptions,
  out of scope, done condition, most likely failure.
- **Fresh Claude** answers **assumptions** first, then runs a **pre-mortem** — wrong
  question, already done, tractability, hidden cost, misreading risk.
- **Pi** *(when enabled)* answers **assumptions** first, then enumerates the **competing
  readings** — every reading that would lead to different work, the single question that
  would discriminate between them, how far apart they are, and what evidence favours one.

Different lenses on purpose. After the work, a fresh Claude earns its slot by being
un-anchored from a session that has convinced itself; at t=0 there is no such anchor, so an
identical prompt would make it a second copy of the working agent's own reading. The same
argument applies to a third reviewer: asked the same question, it would cost a third call
for no additional voice.

**Assumptions is the load-bearing slot, and the only one both reviewers answer.** When the
request is clear, everything else agrees; assumptions is where two independent readers reveal
that it wasn't — so it is the one place a genuine cross-lineage comparison exists, and it is
shared for exactly that reason. Every other slot is single-source by design, which does not
make it weak: the other reviewer was never asked, so its silence is not a dissent.

Aggregation **inverts**: union, not survival. Nothing is built, so there is no evidence to
kill a weak finding with, and the costs are lopsided — a false alarm takes one sentence to
dismiss, a missed misreading costs the whole task. Divergent assumptions are not noise to
resolve; they *are* the output, and they go back to the user as an explicit choice.

This also makes the panel domain-neutral. The slots hold for code, research, analysis,
writing, or a decision — only their contents change, so nothing has to classify the task
first. Two failure modes that barely exist in coding dominate elsewhere and get their own
slots: **tractability** ("the available data cannot support this claim") and the
**wrong-question** check ("the answerable version is adjacent to what you asked").

**When to run it.** Always, before **substantial research** — that is where wrong-question
and tractability findings are most common and most expensive, and where they expire fastest
once work begins. Otherwise: when a request describes an outcome rather than a change, when
it is expensive or hard to reverse, or when the agent notices itself filling in a blank the
user never specified. Skip it when the request already names the files or the exact question
— there is no ambiguity to find and the panel costs minutes.

Unlike the other levels this is **not** hook-triggered: the Stop hook fires when a turn
ends, by which point the moment for it has passed. The agent decides to run it, from the
skill's own description — so nothing extra needs registering, but it is judgement rather
than a gate, and a description has to win against the strongest default there is, which is
to start working. That is why the trigger is written the way it is: timing first, the
obvious rationalization named and refused ("don't skip because it seems clear enough"), and
an explicit skip list so the boundary is crisp.

**Why a separate skill and not `/assess scope`.** The Stop hook's `is_assess_launch()` keys
on the literal string "assess" — in the Skill tool's argument, in the harness's launch
marker, in the injected skill body's path, and in a typed slash command. It never reads the
arguments, so a `/assess scope` run spent the cycle's review flag: the agent could run the
before-work panel, do substantial work in the same turn, and reach Stop with the hook
already satisfied, shipping it reviewed by nobody. The before-work panel would silently
cancel the after-work one. A skill named `scope` matches none of those four paths, so the
two are independent again — run scope, work, and the Stop hook still asks for turbo at the
end, which is the correct flow.

Getting there took two false starts, both worth recording because they were the same
mistake. Two of those checks were substring scans over a whole injected turn rather than
checks anchored to the thing they were about, and an injected *skill body* is such a turn.
So a skill whose instructions merely mentioned the review skill's path counted as a
completed review — which the scope skill did, in the obvious way, by documenting the
`panel.sh` the two share. The first fix failed identically: the comment written to warn
about the string contained the string. A third channel had the same shape, `is_our_prompt()`
matching a bare `"run /assess"` in any isMeta turn, so describing the hand-off in prose read
as "we already prompted this cycle" and approved the turn outright.

Both checks are now anchored to what they actually mean — the base-directory line that names
the skill, and the hook's own injected reason (`"Run /assess … before finishing"`) — so
ordinary prose about assess is just prose. Tests pin both, and fail against the old logic.

What remains load-bearing is only the skill *name*: the other detectors key on it, which is
correct rather than fragile, so a future rename must keep "assess" out of it. `skills/scope/panel.sh`
stays as a symlink — no longer required, just one entry point per skill.

## The third reviewer (optional)

Codex and a fresh Claude are two lineages. A third — [`pi`](https://pi.dev) driving
`moonshotai/kimi-k3` over OpenRouter — adds another, in both modes. It is **off by default
and opt-in**, because unlike the other two it needs a binary, a provider key, and (for a
custom model) a machine-local model definition that this repo cannot assume you have:

```bash
ASSESS_PANEL_PI=1 bash skills/assess/panel.sh "$ART" "$TASK"
```

| Variable | Default | |
|---|---|---|
| `ASSESS_PANEL_PI` | *(off)* | `1` turns the third reviewer on. |
| `ASSESS_PI_MODEL` | `moonshotai/kimi-k3` | Must be a model `pi --list-models` reports on this machine. |
| `ASSESS_PI_PROVIDER` | `openrouter` | |
| `ASSESS_PI_EFFORT` | `high` | `xhigh` asks for the model's top tier. pi clamps to what the model actually supports (see below). |
| `ASSESS_PI_KEY_FILE` | — | A `.env`-style file to read the key from, for when it lives in a project rather than your shell. **Only the pi reviewer sees it**; codex and claude never do. |
| `ASSESS_PI_KEY_VAR` | derived from provider | e.g. `openrouter` → `OPENROUTER_API_KEY`. |

**It degrades loudly, never silently.** A panel's whole value is decorrelation, so a
reviewer that quietly doesn't run is worse than one that errors — you'd read two sections
and believe three lineages agreed. Before spending a slot, `panel.sh` checks that `pi` is on
PATH, that a credential is resolvable (environment → key file → a provider entry in pi's own
`auth.json`), and that the model actually resolves; each failure prints an explicit
`>>> SKIPPED` naming the cause, and the panel continues with the other two. The model check
matters more than it sounds: pi does *not* fail fast on an unknown id — it warns and then
dies at the provider with a `401`, which reads like an auth problem.

**Read-only and hermetic, like the others.** `--tools read,grep,find,ls` is an allowlist over
pi's built-in read-only set (its others are `bash`/`edit`/`write`), and every source of
ambient state is switched off: no session, no extensions or installed packages, no skills, no
prompt templates, and **no `AGENTS.md`/`CLAUDE.md` discovery** — a reviewer that inherits your
own global instructions is not an independent voice. It runs against a throwaway copy of
`~/.pi/agent` rather than the real one, which also keeps it working under Claude Code's Bash
sandbox (pi writes a settings lock and a session dir at startup, which would otherwise EPERM
and kill it before the first token).

**Asking for the top tier needs a one-line config change to be real.** The default effort is
`high`. If you raise it with `ASSESS_PI_EFFORT=xhigh`, note that Kimi K3's reasoning tiers are
`low`/`high`/`max` and pi only offers `xhigh` for a model whose `thinkingLevelMap` defines it.
Without one, `--thinking xhigh` silently clamps *back down* to `high`, so you pay the latency
of asking and get the default anyway. Where the map goes depends on the model:

```jsonc
// ~/.pi/agent/models.json
{"providers": {"openrouter": {
  // a CUSTOM model (not in pi's bundled catalog, e.g. kimi-k3): on its models[] entry
  "models": [{"id": "moonshotai/kimi-k3", "reasoning": true, /* … */
              "thinkingLevelMap": {"off": null, "minimal": null, "low": "low",
                                   "medium": null, "high": "high", "xhigh": "max"}}],
  // a BUILT-IN model: under modelOverrides, so the catalog's context window,
  // max-output and pricing are kept rather than reset to defaults
  "modelOverrides": {"moonshotai/kimi-k2.6": {"thinkingLevelMap": {"xhigh": "max"}}}
}}}
```

Then `xhigh` reaches OpenRouter as `reasoning: {effort: "max"}`. The panel reads both forms:
when it can prove the level clamps it says so, and when the model is configured in neither
place it says the effort could **not** be verified rather than let the header imply otherwise.

**Budget for it being the slowest reviewer.** Kimi K3 lists at $3/$15 per MTok. At `max`
effort on a ~1,100-line diff it overran the default 540s watchdog in our runs while Codex and
Claude finished comfortably — which is why the default here is `high` rather than the top
tier. For a large artifact, still prefer running the panel **backgrounded** with
`ASSESS_PANEL_TIMEOUT=1500` (the 540s default exists to stay under the caller's 10-minute
foreground Bash cap, which backgrounding removes).

## Requirements

- **Claude Code** (`claude` CLI), authenticated.
- **Codex** (`codex` CLI ≥ 0.144), authenticated. Model and effort are pinned to `gpt-5.6-sol` / `xhigh` (override with `ASSESS_CODEX_MODEL` / `ASSESS_CODEX_EFFORT`).
- macOS or Linux (the hook and `panel.sh` are bash + standard tools).
- *Optional, only for the third reviewer:* **pi** (`npm i -g @mariozechner/pi-coding-agent`), a provider key, and `ripgrep` on PATH (without it the reviewer keeps `read`/`ls` but loses `grep`/`find`, and says so).

## Files

- `hooks/stop_assess.py` — the 3-level gate (the Sonnet classifier).
- `skills/assess/SKILL.md` — the after-work instructions: normal + turbo branches.
- `skills/scope/SKILL.md` — the before-work instructions. Separate skill (see above for why).
- `skills/assess/panel.sh` — runs the reviewers in parallel. `--mode review` (default) on a finished artifact; `--mode scope` on a request that hasn't been started.
- `test/test_panel_pi.py` — pins the third reviewer's gating: off by default, loud when enabled-but-unavailable, and never rendered as agreement when it dies.
- `skills/scope/panel.sh` — a symlink to the script above, not a second copy, so each skill cites its own directory. Deleting it breaks `/scope`.
- `install.sh` — symlinks the hook + skill into `~/.claude/` (backs up anything already there).

## Install

Review the files, then:

```bash
bash install.sh
```

It backs up any existing `~/.claude/hooks/stop_assess.py` and `~/.claude/skills/assess` into `~/.claude/backups/` (outside the skills dir, so backups don't show up as live skills), then symlinks this repo in their place (so edits here go live immediately). You must also register the Stop hook once in `~/.claude/settings.json`:

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

- All reviewers run **read-only** — they cannot edit your files.
- `panel.sh` defaults to `--mode review`, so existing callers are unaffected; `--mode scope` (or `ASSESS_PANEL_MODE=scope`) selects the before-work panel. In scope mode the file argument is the *context* bundle rather than an artifact, and may be empty; the request itself is the second argument and is required.
- Scope is triggered by the agent's own judgement from the skill description, not by a hook — nothing to register beyond the Stop hook below.
- Per-reviewer timeout defaults to 540s (`ASSESS_PANEL_TIMEOUT` to override).
- The panel reviewers root in the project directory (`$PWD`, or `ASSESS_PANEL_ROOT`) so they can read the surrounding code, not just the artifact text.
- The hook prompts **at most once per stop-chain**: on any Stop that follows its own block (`stop_hook_active`), it approves — however the agent handled the prompt. This is what makes "assess the assessment" loops structurally impossible.
- The hook **fails open**: if the classifier CLI errors or times out, the turn is approved with a stderr note — an infra hiccup never manufactures a review demand.
- Turns with **no tool calls at all** (a reply, a recap, an explanation) are approved without classifying — there is no artifact to review.
