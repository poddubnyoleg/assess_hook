---
name: scope
description: TRIGGER — run BEFORE starting work, never after, whenever the request calls for substantial research, an open-ended investigation, a multi-source analysis, or describes an outcome rather than a named change ("figure out whether X", "research the possibility of Y", "make the dashboard useful"). Do NOT skip because the request "seems clear enough" or because you can see where to start — that judgement is precisely what this checks, and it becomes worthless the moment work begins. Runs a cross-model panel (Codex + a fresh Claude) on the REQUEST ITSELF to surface what it leaves ambiguous, before effort is spent on the wrong reading of it. SKIP when the request already names the files, the function, or the exact question; when it is the user answering a previous scope run; or when work on this task has already started.
---

## What this is

A cross-model panel run on the **request**, before anything is built. It is the mirror of
`/assess turbo`, which runs the same two reviewers on a **finished artifact** — separate
skill, opposite end of the task. Running this one does not review anything, so the Stop
hook will still ask for `/assess turbo` when the work is done. That is correct; let it.

The question has to differ from turbo's. After the work there is an artifact, so "is this
wrong?" is decidable against evidence. Before it there is nothing to refute, and asking two
models "how should this be done?" just yields two plausible plans and no way to choose. So
both reviewers are asked what they think is being **asked**, and both are forbidden from
proposing an approach.

## When

**The trigger is in the description above — follow it rather than re-deriving it.** The two
findings only this panel can produce are also the two that expire fastest: that the question
cannot be answered with what is actually available (**tractability**), and that the
answerable question is adjacent to the one asked (**wrong question**). Once there are
findings in hand, sunk cost makes both nearly impossible to see, which is why "I'll start and
check later" does not work.

**At most once per line of work.** Nothing gates this — turbo has the Stop hook's classifier
and its recursion guards, this has only your judgement, and each run costs two xhigh calls
and minutes. In particular, when a run ends by putting a choice to the user, their answer is
a *follow-up to a scope run*: the ambiguity was just resolved by hand, so do not panel it
again. Same for a request the user has already spelled out in detail after you asked. Re-run
only if the task genuinely changes shape.

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

   `panel.sh` here is a symlink to the copy in the sibling review skill — one script, two
   entry points, so each skill can cite its own directory.
   Codex answers a six-slot interpretation schema; a fresh Claude answers assumptions and
   then runs a pre-mortem. Each is read-only.

   - The request you pass MUST be **the user's own words, verbatim**. A paraphrase is your
     interpretation, which is exactly what this panel exists to test against — passing it
     guarantees agreement and makes the whole run worthless.
   - **Long Bash timeout**: set the Bash tool `timeout` to `600000` ms. Both reviewers run at
     xhigh effort. A reviewer that prints `>>> TIMED OUT` / `>>> FAILED` produced nothing —
     that is an **incomplete panel**, never "no ambiguity found."
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
   - **WRONG QUESTION / TRACTABILITY / ALREADY DONE** hits outrank everything: they can make
     the task unnecessary or impossible, and they expire the moment you start.
4. Report the substance — never paste the raw panel — then:
   - **Readings converged** → state the shared assumptions and the done condition in a line
     or two, then get on with the work in the same turn. No need to ask.
   - **Readings diverged, or a wrong-question / tractability / already-done hit** → put the
     choice to the user as an explicit question and **stop there**. Starting would pick a
     branch by default, which is the failure this was run to prevent.

## Detailed user input:

$ARGUMENTS
