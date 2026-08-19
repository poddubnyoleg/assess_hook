---
name: scope
description: TRIGGER — ONLY when the user explicitly asks for it, by typing /scope or saying in their own words to scope the request ("scope this", "run scope on this"). NEVER invoke it on your own judgement, however open-ended or ambiguous the request looks — the user has chosen explicit invocation over proactive triggering, and an unrequested run spends minutes and three xhigh calls they did not ask for. When invoked, run it BEFORE starting work, never after: it runs a cross-model panel (Codex + a fresh Claude + Grok) on the REQUEST ITSELF to surface what it leaves ambiguous, before effort is spent on the wrong reading of it. If asked to scope a task that is already underway, say the moment has passed and offer to proceed instead.
---

## What this is

A cross-model panel run on the **request**, before anything is built. It is the mirror of
`/assess turbo`, which runs the same reviewers on a **finished artifact** — separate
skill, opposite end of the task. Running this one does not review anything, so the Stop
hook will still ask for `/assess turbo` when the work is done. That is correct; let it.

The question has to differ from turbo's. After the work there is an artifact, so "is this
wrong?" is decidable against evidence. Before it there is nothing to refute, and asking the
models "how should this be done?" just yields plausible plans and no way to choose. So
every reviewer is asked what it thinks is being **asked**, and all are forbidden from
proposing an approach.

## When

**Only on the user's explicit request — the trigger is in the description above.** You do
not decide that a request deserves scoping; the user does, by invoking `/scope`. What stays
yours is the timing discipline: the two findings only this panel can produce — that the
question cannot be answered with what is available (**tractability**), and that the
answerable question is adjacent to the one asked (**wrong question**) — expire the moment
work begins, so an invocation must run before anything else is attempted, and a scope
request arriving after work has started should be declined as too late rather than run
for show.

**At most once per line of work.** When a run ends by putting a choice to the user, their
answer is a *follow-up to a scope run*: the ambiguity was just resolved by hand, so do not
panel it again unless they explicitly ask again. Re-run only if the task genuinely changes
shape — and even then, only on request.

## How

1. Gather **context** into one file — `$ART` below, in your session scratchpad if you have
   one, else `$TMPDIR` (bare `/tmp` is typically not writable under the Bash sandbox, and a
   fixed name collides across concurrent sessions). Include what the reviewers cannot see
   for themselves and would need: the relevant slice of this conversation, the source
   documents, prior decisions, a file inventory. Do NOT include your own reading of the
   request — that is the thing being tested. If there is genuinely no context, an **empty
   file is fine** (`: > "$ART"`); the reviewers can still Read/Grep the working directory.
2. Run the panel. Pass the request through a **quoted heredoc**, never pasted inline into
   the command — a request containing `$VAR`, backticks or `$(...)` would otherwise be
   expanded or executed by the shell, silently altering the exact wording that is the entire
   point of passing it.
   ```
   REQ="$(cat <<'VERBATIM'
   <the user's request, pasted exactly as written; multiple turns in order if it spans several>
   VERBATIM
   )"
   bash ~/.claude/skills/scope/panel.sh --mode scope "$ART" "$REQ"
   ```
   `--mode scope` must come **before** the artifact path; after it, the flag is task text.

   `panel.sh` here is a symlink to the copy in the sibling review skill — one script, three
   entry points (review, scope, consult), so each skill can cite its own directory.
   Codex answers a six-slot interpretation schema; a fresh Claude answers assumptions and
   then runs a pre-mortem; Grok (on by default; `ASSESS_PANEL_GROK=0` disables) bounds the
   request's **extent** — the minimal and maximal readings, where a worker's default lands
   between them, the stopping rule, and the spillover. Each is read-only. When Grok's CLI
   or login is missing it prints `>>> SKIPPED` with the reason and the panel continues.

   - An optional **fourth lineage** (pi driving Kimi K3) is available by prefixing
     `ASSESS_PANEL_PI=1`. Its lens is the **competing readings**: every reading that would
     lead to different work, and the single question that would discriminate between them —
     which is already the shape of what step 4 has to put to the user. Off by default
     because it needs a binary and a provider key that may not exist here; when it can't
     run it prints `>>> SKIPPED` with the reason and the panel continues without it.

   - The request you pass MUST be **the user's own words, verbatim**. A paraphrase is your
     interpretation, which is exactly what this panel exists to test against — passing it
     guarantees agreement and makes the whole run worthless.
   - **Run it OUTSIDE your Bash sandbox** (in Claude Code: `dangerouslyDisableSandbox`).
     A sandbox that routes egress through a host-allowlisted proxy refuses the tunnel to
     whichever provider is not on the list, and the reviewer dies slowly with a log that
     reads like a flaky provider. `panel.sh` detects it and says so, but only the caller
     can avoid it.
   - **Long Bash timeout**: set the Bash tool `timeout` to `600000` ms. Codex, Claude and
     Grok run at xhigh effort. A reviewer that prints `>>> TIMED OUT` / `>>> FAILED` / `>>> SKIPPED`
     produced nothing — that is an **incomplete panel**, never "no ambiguity found."
   - Run it **from the project directory** (or set `ASSESS_PANEL_ROOT`).
   - Do NOT rewrite the reviewer instructions; the lenses are fixed inside `panel.sh` on
     purpose. You supply the request and the context, never the question they are asked.
3. Reconcile by **UNION** — the opposite of turbo. There is no artifact to check a finding
   against and no evidence to kill one with, so nothing gets filtered for lacking proof:
   - **ASSUMPTIONS is the only slot every reviewer answers**, so it is the only
     cross-lineage comparison available. Diff those lists first.
   - **Assumptions that diverge are the deliverable.** Independent readers filled the same
     blank differently, so the request is ambiguous there. Put it to the user as an
     explicit choice. Do NOT pick one silently and do NOT average them into a compromise
     reading — resolving it yourself throws away the only thing the panel produced.
   - **Every other slot is single-source by design** (Codex: deliverable, underlying
     question, done condition; Claude: wrong question, already done, tractability, hidden
     cost, misreading risk; Grok: minimal/maximal reading, default trap, stopping rule,
     spillover; Pi, when on: competing readings, discriminating question, distance,
     evidence, unstated constraint). Single-source is not weak here, and "the other
     reviewers didn't raise it" is not evidence against it — they were never asked.
     Check each against your own reading, which is the last voice.
   - **WRONG QUESTION / TRACTABILITY / ALREADY DONE** hits outrank everything: they can make
     the task unnecessary or impossible, and they expire the moment you start.
4. Report the substance — never paste the raw panel — then:
   - **Readings converged** → state the shared assumptions and the done condition in a line
     or two, then get on with the work in the same turn. No need to ask.
   - **Readings diverged, or a wrong-question / tractability / already-done hit** → put the
     choice to the user as an explicit question and **stop there**. Starting would pick a
     branch by default, which is the failure this was run to prevent.

   Either way, close the telemetry loop: if the panel footer printed `Panel run id:
   <id>`, it also printed the exact `panel_stats.py --log … verdict <id>` command — use
   it verbatim, with the findings JSON on stdin (`verdict --schema` prints the shape).
   **No run id in the footer → telemetry was off; skip this, and never substitute
   another run's id.** Log which findings mattered — in scope mode `confirmed` means it
   changed your reading or became a question to the user (there is no artifact to check
   it against), `refuted` means noise; `sources` names who raised it (`codex`, `claude`,
   `grok`, `pi`, or `self` for your own reading). One call with everything: a second call
   for the same run replaces the first.

## Detailed user input:

$ARGUMENTS
