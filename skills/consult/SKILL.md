---
name: consult
description: TRIGGER — ONLY when the user explicitly asks for it, by typing /consult or saying in their own words to consult the panel ("consult the panel", "ask the panel", "get a second opinion from the panel", "have the panel ideate on this"). NEVER invoke it on your own judgement, however stuck the work feels — the user chooses when to spend the panel, and an unrequested run costs minutes and several xhigh calls they did not ask for. When invoked, it runs the cross-model panel (Codex + a fresh Claude + Grok) on an IMPASSE — a problem the session cannot get past mid-work — or on an open problem the user wants outside ideas on, and returns a menu of ways forward. Domain-neutral: a bug, a data analysis that will not reconcile, a research dead end, open-ended ideation. It reviews nothing; the after-work review still runs when the task finishes.
---

## What this is

The panel's third moment. Review (`/assess turbo`) runs AFTER the work, on the artifact;
scope (`/scope`) runs BEFORE it, on the request; consult runs DURING it, on an
impasse — a failing check that has survived several attempted fixes, an analysis whose
numbers will not reconcile, a dataset that resists the question, a dead end — or on an
open problem the user wants outside ideas on, with nothing failed yet. **Domain-neutral
like its siblings**: code is one case, not the definition — data analysis (medical or
otherwise), research, writing, a decision, and open-ended ideation are equally its
territory, and nothing needs to classify the task first. The argument for spending the
panel here is the strongest of the three: review and scope hedge against the
*possibility* of a blind spot, while an impasse is a *demonstrated* one — this model's
priors have visibly failed on this specific problem, which is exactly when a foreign
lineage's priors are worth the most per call.

The question inverts a third way: not "is this wrong?" (judicial) or "what is being
asked?" (interpretive) but "what would you try?" — generative. The reviewers propose;
you execute. Running this reviews nothing, so the Stop hook will still ask for
`/assess` when the work is done. That is correct; let it.

## When

**Only on the user's explicit request — the trigger is in the description above.** You
do not decide the session is stuck enough; the user does, by invoking `/consult`. (The
roadmap in the README has a hook-side trigger as v2; until it exists, the choice stays
with the person who pays for it — same discipline as `/scope`, learned the same way.)

Its moment is **a live problem**. A stuck consult belongs mid-task, on evidence; an
ideation consult fits whenever the user asks for outside ideas — the panel reacting to
a question is the deliverable itself, so it has no timing rule. What consult is *not*:
interpreting a request about to be worked on (that is `/scope`) or checking a finished
artifact (that is `/assess turbo`). Asked to consult in either of those positions, say
which skill fits and offer it instead.

**At most once per stuck episode.** If the menu is exhausted and the problem still
stands, escalate to the user with the evidence — do not re-run the panel over the same
facts; a second run on unchanged evidence buys the same menu again.

## How

1. **Build the EVIDENCE file** — `$ART`, in your session scratchpad if you have one,
   else `$TMPDIR`. This is the load-bearing step, and it has rules; every one exists
   because the packet is the one thing here with no un-authored source (review has the
   artifact, scope has the user's words — consult has only what you choose to include,
   and your selection is itself a theory). What goes in depends on the shape of the
   impasse — the rules below name three; a mixed case takes the union:
   - **A failing check** (a test, a build, a validation): **re-run it NOW and capture
     its raw output fresh.** Never paste from memory or scrollback — and if it passes
     on the re-run, stop: report that to the user instead of consulting about a
     problem that no longer reproduces.
   - **An analysis that will not reconcile** (contradictory numbers, an implausible
     result, a dataset resisting the question): **re-execute the queries or steps NOW
     and include their actual output** — the real numbers, row counts, and the exact
     query or code that produced each, never figures recalled from earlier in the
     session. Include both sides of any contradiction, and the definitions of the
     metrics involved.
   - **An open problem / ideation** (nothing failed — the user wants outside ideas):
     the evidence is the problem's current state — the framing, the constraints, the
     relevant prior discussion verbatim. Here, unlike the stuck shapes, your current
     thinking MAY go in — **labelled as the state of thinking to build beyond**, so
     the panel extends it instead of re-deriving it.
   - **Attempts as facts** (stuck shapes). For each attempt so far, in order: the
     exact change or action and the observed result. Include abandoned attempts —
     they are negative evidence the consultants need.
   - **Over-include what you never suspected — but never secrets.**
     The output you believe matters plus the config you never doubted, the
     environment's *shape*, versions, the check's or metric's own definition. The
     packet must let a consultant disagree with you about where the problem lives.
     The counter-rule arrives with this one, because the packet goes verbatim to
     three external model providers: **no secrets** — no `env` dumps, no
     `.env`/credential files, no auth headers or connection strings; name the
     variable and redact the value (`DB_URL=[redacted]`), and scan captured output
     for token-shaped runs before including it. Everywhere else this repo isolates
     provider keys per reviewer; a consult packet that pastes the environment would
     undo that in one step. (Data itself — including personal or medical records the
     user is analyzing — is not restricted: the user has chosen to trust the panel's
     providers with the problem, and the data is the problem.)
   - **Exclude your diagnosis entirely** (stuck shapes). No theories, no "likely
     cause", no "I think". The consultants form their own reading; yours is the thing
     that has failed, and smuggled in it re-anchors them to the frame the panel
     exists to break. (The ideation shape is the one exception, above — there the
     frame has not failed, and the state of thinking is context, not contamination.)
2. **State the problem** — the user's words verbatim where they name it (their original
   request, and their consult ask if it says what is stuck or what to ideate on), plus
   the bare facts in neutral terms: what fails or will not reconcile, with what output,
   after how many attempts — or, for an open problem, the question as posed. Facts,
   never diagnosis. Pass it through a **quoted heredoc**, never inline — a problem
   statement containing `$VAR`, backticks or `$(...)` would otherwise be expanded:
   ```
   REQ="$(cat <<'VERBATIM'
   <the user's request + the bare facts, exactly as described above>
   VERBATIM
   )"
   bash ~/.claude/skills/consult/panel.sh --mode consult "$ART" "$REQ"
   ```
   `--mode consult` must come **before** the artifact path; after it, the flag is task
   text. `panel.sh` here is a symlink to the copy in the sibling review skill — one
   script, three entry points, so each skill can cite its own directory.

   Codex proposes experiment cards (distinct hypotheses, each with the cheapest
   discriminating probe — a command, a query, a source to check); a fresh Claude infers
   the **shared assumption** of the failed attempts and attacks it (on an open problem
   with no attempts, it attacks the framing instead); Grok proposes lateral moves from
   outside the current approach; pi, when on (`ASSESS_PANEL_PI=1`), lists the mundane
   causes a clever search skips — from stale caches to silent join fan-outs. All four
   end with **FIRST MOVE** — the one slot every reviewer answers, so the one place a
   cross-lineage comparison exists.

   - **Run it OUTSIDE your Bash sandbox** (in Claude Code: `dangerouslyDisableSandbox`)
     — every reviewer is a network client, and a proxying sandbox kills them slowly
     with logs that read like flaky providers. `panel.sh` detects and names it, but
     only the caller can avoid it.
   - **Long Bash timeout** (`600000` ms), or run it **backgrounded** with
     `ASSESS_PANEL_TIMEOUT=1500` and keep testing your own next hypothesis while it
     runs — mid-task, wall-clock is the scarcest resource on the table.
   - Run it **from the project directory** (or set `ASSESS_PANEL_ROOT`) so the
     reviewers can Read/Grep the files around the evidence — code, data extracts,
     notebooks, notes.
   - Do NOT rewrite the reviewer instructions; the lenses are fixed inside `panel.sh`
     on purpose. You supply the problem and the evidence, never the question they
     are asked.
3. **Reconcile as a MENU** — not survival (nothing is being refuted), not union-to-user
   (nothing needs their decision yet). There is a live problem to test suggestions
   against, so:
   - **Diff the FIRST MOVE slots first.** Different lineages independently proposing
     the same move is the strongest signal the run produces — do that move.
   - **Order the rest by cost-to-test, cheapest first.** The run succeeds if ANY
     suggestion unblocks; divergent suggestions are breadth, not disagreement.
   - **Run at least one proposed probe before your next speculative fix.** This is the
     one binding rule: reading the menu, nodding, and resuming your own loop spends
     the panel on nothing — if it was worth calling, it is worth testing its cheapest
     hypothesis first.
   - **A SHARED ASSUMPTION finding outranks the individual cards.** It names the frame
     everything else was searched inside; test it even when a cheaper card looks
     promising.
   - **A reviewer that TIMED OUT / FAILED / SKIPPED delivered no finished review** —
     the menu is narrower than designed. Say so; never read the remaining voices as
     the whole panel. (Partial output printed before a kill is still readable, and a
     suggestion taken from it still credits that reviewer in the verdicts.)
4. **Act, then close the telemetry loop.** If the panel footer printed `Panel run id:
   <id>`, use the `panel_stats.py --log … verdict <id>` command it printed, verbatim,
   findings JSON on stdin. **No run id in the footer → telemetry was off; skip this,
   never substitute another run's id.** In consult mode the verdicts mean: `confirmed`
   = you acted on it and it unblocked the work or changed your approach; `refuted` =
   tried it, dead end; `unverifiable` = untested (ran out of budget, or the episode
   ended another way). Log the dead ends too — they are the precision signal — and
   `sources` names who proposed it (`codex`, `claude`, `grok`, `pi`, or `self` if you
   arrived at it independently before reading the menu). One call with everything; a
   second call for the same run replaces the first.
5. **Report to the user**: what was stuck or asked, what the panel proposed (the
   substance, never the raw dump), what you tested, and where things stand now. Then
   continue the work — and when it finishes, the Stop hook's review request is the
   after-work check this consult deliberately is not.

## Detailed user input:

$ARGUMENTS
