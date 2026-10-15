---
name: assess
description: Critically double-check your work — code, research, or plans — AFTER doing it. Usually triggered by the Stop hook when a turn ends; `/assess turbo` adds a cross-model panel on top of the self-review. The mirror of this, run on demand before the work starts or mid-task rather than on the result, is the separate `panel` skill.
---

## Levels

The Stop hook decides the level and tells you which to run:

- **Normal** (`/assess`) — critical self-review in this session (the criteria below).
- **Turbo** (`/assess turbo`) — **everything Normal does, PLUS** the cross-model panel.
  Substantial work was done, so a deeper check is warranted.

Both run AFTER the work. The panel's other two moments live in the **separate `panel`
skill**, which shares only `panel.sh` — before the work it runs on the request, during it
on an impasse, and the user invokes it explicitly. It reviews nothing, so running it does
not satisfy the Stop hook, and this skill still applies when the work is done.

## Turbo: cross-model panel

When invoked with `turbo` (or when `$ARGUMENTS` contains "turbo"):

1. **Do the full Normal self-review first** — every relevant criterion in the
   "Double-Check" section below. Turbo never skips it; the panel is added on top.
   **Write your findings down as a list NOW, before the panel returns** — step 6 logs
   which findings were yours (`self`), and labelling them after you have read the
   panel's output invites hindsight in both directions: "I'd have caught that too"
   deflates the panel's beyond-self metric exactly as much as forgetting to log
   yourself inflates it. The list you carry into step 6 must be this one, verbatim.
2. Gather what to review into one file — **include NEW files, not just tracked edits**.
   Put it in your session scratchpad directory if you have one, else under `$TMPDIR`
   (bare `/tmp` is typically NOT writable under the Bash sandbox, and a fixed name
   would collide across concurrent sessions). Below, `$ART` stands for that absolute
   path, e.g. `ART="$TMPDIR/assess_artifact.txt"` — reuse the SAME absolute path in
   step 3 (each Bash call is a fresh shell, so don't rely on the variable surviving):
   - git repo → `{ git diff HEAD; git ls-files --others --exclude-standard -z | while IFS= read -r -d '' f; do echo "=== NEW: $f ==="; cat "$f"; done; } > "$ART"`
     (`git diff` alone omits untracked files, so a brand-new module would be invisible to the panel.)
   - non-git → write the changed/new file(s) or the report into `$ART`
3. Run the panel. Pass the task through a **quoted heredoc**, never pasted inline into the
   command — a task containing `$VAR`, backticks or `$(...)` would otherwise be expanded or
   executed by the shell, silently altering the exact wording that is the point of passing
   it verbatim:
   ```
   TASK="$(cat <<'VERBATIM'
   <the original request, pasted exactly as written>
   VERBATIM
   )"
   ASSESS_PANEL_TIMEOUT=1500 bash ~/.claude/skills/assess/panel.sh "$ART" "$TASK" > "$TMPDIR/panel_out.txt" 2>&1
   ```
   **Run that Bash call in the background** (`run_in_background`), with the sandbox
   disabled, and read `$TMPDIR/panel_out.txt` when the completion notice arrives. The
   exec seats run a reproduction and a few tests, which the 540s foreground default
   does not reliably cover; backgrounding removes the 10-minute foreground cap that the
   default exists for. Only if backgrounding is unavailable, run it in the foreground
   without `ASSESS_PANEL_TIMEOUT` and with the Bash `timeout` at its maximum.
   This runs Codex (a different model), a fresh Claude (clean context, no anchoring),
   Grok (xAI's grok-4.7 — a third lineage, **on by default**), and Antigravity (`agy`,
   Google's `gemini-4-argon` — a fourth lineage, **on by default**), in parallel, each told
   to refute — not approve. **Codex and the fresh Claude can EXECUTE**: each works in its
   own throwaway scratch copy of the project (a clone with your uncommitted change
   applied and untracked files copied in, no remote), fenced by its CLI's sandbox,
   network closed, and is told to build one reproduction from the task's own sentence,
   try a fix, and run the project's tests only when cheap. Grok, Antigravity, and pi stay
   read-only by design. **Every finding, from every seat,
   must carry `CHECKED` (what was run or read) and `FOUND` (what it showed, quoted), or be
   tagged `[UNTESTED]` instead of a severity.**

   - **Exec is on by default** in a git repo (`ASSESS_PANEL_EXEC=0` turns it off). It costs
     a ~15s preflight: the fresh Claude's seat is granted only after a cheap canary proves
     its sandbox refuses a write outside the copy (that sandbox fails OPEN, so an unproven
     fence is no fence). A seat that cannot be given exec — no git, no commits, a canary
     that did not prove the fence — prints a `>>> NOTE` in its section and runs read-only;
     read its findings as argued, not shown. **For a code artifact, prefer running the
     panel BACKGROUNDED with `ASSESS_PANEL_TIMEOUT=1500`**: a reviewer that runs the suite
     and a reproduction needs more than the 540s foreground default.

   - **Grok** needs the `grok` CLI authenticated (`grok login`); when it can't run, the
     panel prints `>>> SKIPPED` with the reason and continues with the rest. It is trusted
     with the artifact **exactly as Codex, the fresh Claude, and Antigravity are** — all four are
     external providers, and a privacy or data-sensitivity rule either excludes the whole
     panel or none of it; never disable one seat on privacy grounds the others don't face.
     Disable it (a missing CLI, an explicit user choice — not a privacy inference) with
     `ASSESS_PANEL_GROK=0`; override with `ASSESS_GROK_MODEL` / `ASSESS_GROK_EFFORT`
     (default `grok-4.7` at `xhigh`, its top tier — the CLI rejects a level the model
     doesn't support rather than clamping, so a bad value fails fast and loud).

   - **Antigravity (`agy`)** needs the `agy` CLI signed in (run `agy` once and log in
     interactively; or `GEMINI_API_KEY` / `GOOGLE_API_KEY`); when it can't run, the panel prints `>>> SKIPPED`
     with the reason and continues with the rest. Disable it with `ASSESS_PANEL_AGY=0`;
     override with `ASSESS_AGY_MODEL` / `ASSESS_AGY_EFFORT` (default `gemini-4-argon` at
     `high`).

   - An optional **fifth lineage** (pi driving Kimi K3) is available with
     `ASSESS_PANEL_PI=1` prefixed to the command. It is off by default because it needs a
     binary and a provider key that may not exist on this machine; when it can't run it
     prints `>>> SKIPPED` with the reason and the panel continues without it. Turn it on when
     the artifact is important enough to be worth the extra lineage and the extra cost.
     **On a large artifact, run the panel BACKGROUNDED with `ASSESS_PANEL_TIMEOUT=1500`** —
     it is the slowest reviewer on the panel, and at max effort it overran the 540s default
     on a ~1,100-line diff, handing back a TIMED OUT reviewer after nine minutes. It runs
     at `high` by default for that reason; `ASSESS_PI_EFFORT=xhigh` buys the top tier at
     that cost.

   - **Run it OUTSIDE your Bash sandbox** (in Claude Code: `dangerouslyDisableSandbox`).
     Every reviewer here is a network client, and a sandbox that routes egress through a
     host-allowlisted proxy refuses the tunnel to whichever provider is not on the list.
     The reviewer then dies *slowly* — codex spends five WebSocket reconnects and a
     transport fallback before exiting 1 — and the log reads like a flaky provider, so the
     natural response is to re-run it and pay again. `panel.sh` probes for this and turns
     it into an immediate `>>> SKIPPED` naming the host, but it cannot fix it from inside:
     only the caller can choose not to sandbox the call.
   - **In the foreground, give the Bash call a long timeout** — set the Bash tool `timeout`
     to the max (`600000` ms). Codex and Claude run at **xhigh** effort and may Read/Grep
     the repo, so they can take several minutes; the default 2-min Bash timeout would kill
     the panel before they finish. The panel's own per-reviewer watchdog (540s) stays under
     that cap and degrades gracefully — and prints a note when exec seats are on under it.
   - **A reviewer can TIME OUT, FAIL, or be SKIPPED.** If the panel prints `>>> TIMED OUT` /
     `>>> FAILED` / `>>> SKIPPED` for a reviewer, that lineage produced nothing — the panel
     is **incomplete**, not clean. Say so explicitly when reconciling and, if it matters,
     re-run per the note above; never read a missing reviewer as "all clear."
   - Run it **from the project directory** (the reviewers root in the current working
     directory so they can Read/Grep the surrounding code; the artifact file may live
     anywhere, e.g. /tmp). Override the root with `ASSESS_PANEL_ROOT=<dir>` if needed.
   - The `<task>` you pass MUST be the **original request — the user's words or the
     spec — verbatim**, NOT your summary of what you did. A paraphrase re-anchors the
     reviewers to your own understanding and kills the main reason to run a fresh
     Claude: catching "solved the wrong problem."
   - Do NOT rewrite the reviewer instructions. The skeptical stance ("find problems,
     judge independently") is fixed inside `panel.sh` on purpose — it is a guard, and
     keeping it constant is what keeps the reviewers independent of you. You only supply
     the task; you never author their lens.
4. Reconcile by axis — **do NOT majority-vote** (you and the fresh Claude are the same
   lineage and correlate, so a split that looks 2-1 may be one voice against one):
   - **every finding carries `CHECKED`/`FOUND`** → repeat the CHECKED step yourself in the
     real tree before you log a verdict (the exec seats' commands assume their scratch cwd,
     which is gone — run them from your project root). A FOUND that does not actually show
     the problem is a claim, not evidence: treat it as unconfirmed until your own check
     settles it. An **obligation finding** ("the task asked for X and the artifact never
     addresses it") is checked against the task's words, not the diff.
   - **`[UNTESTED]` items are leads, not findings** → no severity, no vote. Test them
     yourself, then log them `confirmed`/`refuted` like any other.
   - **verifiable finding** (a real bug, a failing case, a spec mismatch) → check it
     yourself, fix if real;
   - **raised by one reviewer, missed by the others** → investigate it; cross-lineage
     disagreement is signal, not a minority to overrule;
   - **all agree it's fine** → high confidence, move on.
5. Merge your self-review findings and the panel's findings into **one** list.
6. **Log the verdicts.** If the panel footer printed `Panel run id: <id>`, it also
   printed the exact `panel_stats.py --log … verdict <id>` command — use **that command
   verbatim** (it pins the log file this run actually landed in; reconstructing the
   path can attach your verdicts to the wrong log). **No run id in the footer means
   telemetry was off or unavailable for this run — skip this step entirely; never
   substitute another run's id.** Submit **everything in ONE call** — findings from the
   panel, from your own step-1 self-review, and the refuted ones; a second call for the
   same run *replaces* the first, it does not add. Feed the JSON on stdin:
   ```
   <the footer's command> <<'JSON'
   {"findings": [
     {"summary": "length check off by one in parse()",
      "sources": ["codex"], "severity": "HIGH", "verdict": "confirmed", "action": "fixed"},
     {"summary": "claimed a race on cache init",
      "sources": ["claude", "self"], "verdict": "refuted", "action": "dismissed"}
   ]}
   JSON
   ```
   - `sources` = every voice that **independently** raised it: `codex`, `claude`, `grok`,
     `agy`, `pi`, and `self` for your own step-1 self-review (a finding only you raised is
     `["self"]`). A reviewer is creditable if its review text existed in this run —
     including partial output printed before a timeout or crash.
   - `verdict`: `confirmed` (checked against the work, real), `refuted` (checked, wrong),
     `unverifiable` (could not be settled).
   - `severity` (optional): `HIGH`, `MED`, or `LOW`. `action` (optional): what you did —
     `fixed`, `deferred`, `dismissed`, or `none`. **Those literal values only** — a synonym
     (`applied`, `skipped`, `n/a`, …) fails the whole call and nothing gets logged; when
     none fits, omit the key.
   - Log the **refuted findings too** — they are the precision signal; logging only what
     was confirmed flatters every reviewer equally and measures nothing.
   - Log your **own self-review findings too**, with `self` in `sources` — the list you
     wrote in step 1, verbatim. If that self-review completed and found **nothing**, say
     so explicitly with `"self_clean": true` next to `"findings"`: without it, a clean
     self-review is indistinguishable from a forgotten one, and the panel's beyond-self
     numbers inflate. Nothing to reconcile from anyone → `{"findings": [],
     "self_clean": true}`: "panel ran clean" is also data.
7. **Close with the full-work summary** (see "Finish with a self-contained summary"
   below) — not just the panel delta.

Why the extra reviewers, and why they are not redundant:
- **Codex** is a different model lineage → catches blind spots Claude shares across its
  own runs.
- **Fresh Claude** is the same model with no priming → catches anchoring and
  "solved the wrong problem," which the working session cannot see by itself.
- **Exec** (Codex and the fresh Claude) → findings that report what the environment
  SHOWED when the code was run, not what the diff suggests; that evidence does not depend
  on any model's priors, which is the one thing a change of lineage cannot buy.
- **Grok** is a third lineage, on by default → the same argument as Codex, again, against
  a model trained by neither Anthropic nor OpenAI.
- **Antigravity (`agy`)** is a fourth lineage (Google Gemini), on by default → another
  independent training lineage, running read-only: plan mode, a headless print mode
  that cannot approve writes, and agy's own sandbox.
- **Pi/Kimi** (optional) is a fifth lineage → the argument once more, at extra cost,
  for artifacts worth it.

## Double-Check

Determine what to assess based on context:
- **Code changes** (triggered by Stop hook or after implementation) → run `git diff` and review the changes
- **Research/analysis** (after producing findings or a report) → verify the output quality
- **Plan** (file highlighted in IDE or mentioned in conversation) → assess the approach

Then critically assess using the relevant criteria:

### For code:
- **Correctness**: Logic errors, edge cases, missing error handling
- **Simplicity**: Over-engineering, unnecessary abstractions
- **Security**: Injection, secrets exposure, unsafe operations
- **Consistency**: Does it match existing codebase patterns?

### For research:
- **Every claim cites a source** (query result, dashboard, URL, experiment ID)
- **Numbers are internally consistent** — no contradictions between sections
- **Conclusions follow from the data** — not extrapolated or hallucinated
- **Gaps are flagged** — missing data or unanswered questions are called out, not silently skipped
- **Recommendations are grounded** — tied to evidence, not generic advice

### For plans:
- **Simplification**: Can the approach be simpler?
- **Integration points**: Dependencies correct? Edge cases covered?
- **Completeness**: Anything important missed?

**Important constraints**:

- Do NOT bring up already discussed or deferred topics
- Do NOT mention things that work fine but aren't ideal, unless otherwise stated
- Do NOT just mention issues, do research before you report it
- ONLY raise critical issues or genuine uncertainties
- If you can't find critical issues, do NOT come up with questions for the sake of asking
- Write one ungrouped list of questions including all your findings

## Finish with a self-contained summary

Assess is the LAST thing that runs before you hand back to the user, so your final
message is what they read at the bottom of the screen. Make it stand on its own — the
user should NOT have to scroll up to recover what was actually produced.

After you have applied fixes and reconciled findings, write ONE closing summary that
recaps the **whole work cycle**, not just the assessment:

1. **Lead with the deliverable** — the actual results, analysis, numbers, or what the
   code now does. This is the part that was scrolled off-screen by the assess output;
   restate it (compressed, but complete enough to stand alone), don't assume it's still
   visible.
2. **Then the assessment outcome** — what was checked, what you fixed, and what (if
   anything) the panel/self-review flagged.
3. **Then anything still open** — unresolved findings, caveats, or offered next steps.

Keep it dense, not long: a faithful compression of the work, not a re-paste. The test
is simple — if reading only this final message leaves the user reaching for the scroll
bar to understand the result, it's incomplete.

## Detailed user input:

$ARGUMENTS
