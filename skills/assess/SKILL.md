---
name: assess
description: Critically double-check work after doing it (code, research, plans). With `scope`, checks what a request actually asks BEFORE any work starts — invoke scope unprompted before substantial research, an open-ended investigation, or any request that describes an outcome rather than a change.
---

## Which mode

Two of these run AFTER the work and are picked by the Stop hook. One runs BEFORE it and is
never hook-triggered — either the user asks for it, or **you invoke it yourself** without
being asked (see the rule in that section; it is the main way it fires).

- **Normal** (`/assess`) — critical self-review in this session (the criteria below).
- **Turbo** (`/assess turbo`) — **everything Normal does, PLUS** the cross-model panel.
  Substantial work was done, so a deeper check is warranted.
- **Scope** (`/assess scope`) — **before** starting: a cross-model check of what the
  request actually asks. Nothing has been built, so there is nothing to review; the
  panel surfaces ambiguity instead.

**Routing: if the FIRST word of `$ARGUMENTS` is "scope", do the "Scope" section below and
NOTHING else** — skip Double-Check, skip the closing full-work summary, since both assess
finished work and there is none yet. Anything else, including a bare invocation, is Normal
or Turbo. Match the first word, never a substring: "scope" is an ordinary word in this
domain ("assess the scope of the refactor", "scope creep"), and a stray mention must not
turn a post-work review into an interpretation panel that reviews nothing.

## Scope: before-work panel

The Stop hook never triggers this one — it fires when a turn ends, by which point the moment
has passed. So it runs exactly two ways: the user asks, or you invoke it yourself.

**Run it unprompted, before starting, whenever the request calls for substantial research**
— an open question, a multi-source investigation, an analysis whose shape you would have to
invent. Do not wait to be asked. Research is where the two findings that only this panel can
produce are both most likely and most expensive: that the question cannot be answered with
what is actually available (**tractability**), and that the answerable question is adjacent
to the one asked (**wrong question**). Both expire the moment you start — once there are
findings in hand, sunk cost makes them nearly impossible to see.

Otherwise use judgement: worth it when the request describes an outcome rather than a change
("figure out whether X", "make the dashboard useful"), when it is expensive or hard to
reverse, or when you notice yourself filling in a blank the user never specified. Not worth
it when the request already names the files, the function, or the exact question — there is
no ambiguity to find, and the panel costs minutes.

**Run it at most once per line of work.** Nothing gates this one — Normal and Turbo have the
Stop hook's classifier and its recursion guards, scope has only your judgement, and each run
costs two xhigh calls and minutes. In particular, when a scope run ends by putting a choice
to the user, their answer is a *follow-up to a scope run*: the ambiguity has just been
resolved by hand, so do not run a second panel on it. Same for a request the user has
already spelled out in detail after you asked. Re-run only if the task genuinely changes
shape, not because the follow-up is still phrased as an outcome.

1. Gather **context** into one file — `$ART` below, in your session scratchpad if you have
   one, else `$TMPDIR` (bare `/tmp` is typically not writable under the Bash sandbox, and a
   fixed name collides across concurrent sessions). Include what the reviewers cannot see
   for themselves and would need: the relevant slice of this conversation, the source
   documents, prior decisions, a file inventory. Do NOT include your own reading of the
   request — that is the thing being tested. If there is genuinely no context, an **empty
   file is fine** (`: > "$ART"`); the reviewers can still Read/Grep the working directory.
2. Run the panel. Pass the request through a **quoted heredoc**, never pasted inline into
   the command — a request containing `$VAR`, backticks or `$(...)` would otherwise be
   expanded or executed by the shell, silently altering the exact wording that is the whole
   point of passing it. (Same applies to the `<the original task>` argument in Turbo below.)
   ```
   REQ="$(cat <<'VERBATIM'
   <the user's request, pasted exactly as written; multiple turns in order if it spans several>
   VERBATIM
   )"
   bash ~/.claude/skills/assess/panel.sh --mode scope "$ART" "$REQ"
   ```
   `--mode scope` must come **before** the artifact path; after it, the flag is task text.
   Codex answers a six-slot interpretation schema; a fresh Claude runs a pre-mortem. Both
   are forbidden from proposing an approach — you want their reading of the request, not
   their plan for it. Each is read-only.

   - The request you pass MUST be **the user's own words, verbatim**. A paraphrase is your
     interpretation, which is exactly what this panel exists to test against — passing it
     guarantees agreement and makes the whole run worthless.
   - Same **long Bash timeout** as turbo: set the Bash tool `timeout` to `600000` ms. Both
     reviewers run at xhigh effort. A reviewer that prints `>>> TIMED OUT` / `>>> FAILED`
     produced nothing — that is an **incomplete panel**, never "no ambiguity found."
   - Run it **from the project directory** (or set `ASSESS_PANEL_ROOT`).
   - Do NOT rewrite the reviewer instructions; the lenses are fixed inside `panel.sh` on
     purpose. You supply the request and the context, never the question they are asked.
3. Reconcile by **UNION** — the opposite of turbo. There is no artifact to check a finding
   against and no evidence to kill one with, so nothing gets filtered for lacking proof:
   - **ASSUMPTIONS is the only slot both reviewers answer**, so it is the only cross-lineage
     comparison available. Diff those two lists first.
   - **Assumptions that diverge are the deliverable.** Two independent readers filled the
     same blank differently, so the request is ambiguous there. Put it to the user as an
     explicit choice. Do NOT pick one silently and do NOT average them into a compromise
     reading — resolving it yourself throws away the only thing the panel produced.
   - **Every other slot is single-source by design** (Codex: deliverable, underlying
     question, done condition; Claude: wrong question, already done, tractability, hidden
     cost, misreading risk). Single-source is not weak here, and "the other reviewer didn't
     raise it" is not evidence against it — they were never asked. Check each against your
     own reading, which is the third voice.
   - **WRONG QUESTION / TRACTABILITY / ALREADY DONE** hits outrank everything: they can
     make the task unnecessary or impossible, and they expire the moment you start.
4. Report and **end the turn**. In both outcomes, do not begin the work yet:
   - **Readings converged** → state the shared assumptions and the done condition in a line
     or two, say you are ready to start, and hand back.
   - **Readings diverged, or a wrong-question / tractability / already-done hit** → put the
     choice to the user as an explicit question and hand back. Starting would pick a branch
     by default, which is the failure this was run to prevent.
   - Either way report the substance, not the transcript — never paste the raw panel.

   Handing back on the converged branch too is not politeness — it is what keeps the
   after-work review alive. The Stop hook counts **any** assess launch as "a review already
   ran" (`is_assess_launch` in `hooks/stop_assess.py` never looks at the arguments), so work
   produced in the same turn as a scope run reaches Stop with that flag already spent: the
   hook approves, and a substantial artifact ships reviewed by nobody. The before-work panel
   would have silently cancelled the after-work one. Ending the turn puts the work in the
   next turn, where the classifier sees it normally.

## Turbo: cross-model panel

When invoked with `turbo` (or when `$ARGUMENTS` contains "turbo"):

1. **Do the full Normal self-review first** — every relevant criterion in the
   "Double-Check" section below. Turbo never skips it; the panel is added on top.
2. Gather what to review into one file — **include NEW files, not just tracked edits**.
   Put it in your session scratchpad directory if you have one, else under `$TMPDIR`
   (bare `/tmp` is typically NOT writable under the Bash sandbox, and a fixed name
   would collide across concurrent sessions). Below, `$ART` stands for that absolute
   path, e.g. `ART="$TMPDIR/assess_artifact.txt"` — reuse the SAME absolute path in
   step 3 (each Bash call is a fresh shell, so don't rely on the variable surviving):
   - git repo → `{ git diff HEAD; git ls-files --others --exclude-standard -z | while IFS= read -r -d '' f; do echo "=== NEW: $f ==="; cat "$f"; done; } > "$ART"`
     (`git diff` alone omits untracked files, so a brand-new module would be invisible to the panel.)
   - non-git → write the changed/new file(s) or the report into `$ART`
3. Run the panel:
   ```
   bash ~/.claude/skills/assess/panel.sh "$ART" "<the original task>"
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
