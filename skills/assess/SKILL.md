---
name: assess
description: Critically double-check your work — code, research, or plans
---

## Levels

The Stop hook decides the level and tells you which to run:

- **Normal** (`/assess`) — critical self-review in this session (the criteria below).
- **Turbo** (`/assess turbo`) — **everything Normal does, PLUS** the cross-model panel.
  Substantial work was done, so a deeper check is warranted.

## Turbo: cross-model panel

When invoked with `turbo` (or when `$ARGUMENTS` contains "turbo"):

1. **Do the full Normal self-review first** — every relevant criterion in the
   "Double-Check" section below. Turbo never skips it; the panel is added on top.
2. Gather what to review into one file — **include NEW files, not just tracked edits**:
   - git repo → `{ git diff HEAD; for f in $(git ls-files --others --exclude-standard); do echo "=== NEW: $f ==="; cat "$f"; done; } > /tmp/assess_artifact.txt`
     (`git diff` alone omits untracked files, so a brand-new module would be invisible to the panel.)
   - non-git → write the changed/new file(s) or the report into `/tmp/assess_artifact.txt`
3. Run the panel:
   ```
   bash ~/.claude/skills/assess/panel.sh /tmp/assess_artifact.txt "<the original task>"
   ```
   This runs Codex (a different model) and a fresh Claude (clean context, no anchoring),
   in parallel, each told to refute — not approve. Each is read-only.

   - **Give the Bash call a long timeout** — set the Bash tool `timeout` to the max
     (`600000` ms). Both reviewers run at **xhigh** effort and may Read/Grep the repo, so
     they can take several minutes; the default 2-min Bash timeout would kill the panel
     before they finish. The panel's own per-reviewer watchdog (540s) stays under that cap
     and degrades gracefully. For an even longer budget, run the panel **backgrounded**
     (no foreground cap) with `ASSESS_PANEL_TIMEOUT=1500` or higher.
   - **A reviewer can TIME OUT or FAIL.** If the panel prints `>>> TIMED OUT` / `>>> FAILED`
     for a reviewer, that lineage produced nothing — the panel is **incomplete**, not
     clean. Say so explicitly when reconciling and, if it matters, re-run per the note
     above; never read a missing reviewer as "all clear."
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
4. Reconcile by axis — **do NOT majority-vote** (two of the three voices are the same
   lineage and correlate):
   - **verifiable finding** (a real bug, a failing case, a spec mismatch) → check it
     yourself, fix if real;
   - **raised by one reviewer, missed by the other** → investigate it; cross-lineage
     disagreement is signal, not a minority to overrule;
   - **both agree it's fine** → high confidence, move on.
5. Merge your self-review findings and the panel's findings into **one** list.
6. **Close with the full-work summary** (see "Finish with a self-contained summary"
   below) — not just the panel delta.

Why two extra reviewers, and why they are not redundant:
- **Codex** is a different model lineage → catches blind spots Claude shares across its
  own runs.
- **Fresh Claude** is the same model with no priming → catches anchoring and
  "solved the wrong problem," which the working session cannot see by itself.

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
