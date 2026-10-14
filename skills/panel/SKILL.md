---
name: panel
description: TRIGGER — ONLY when the user explicitly asks for the panel, by typing /panel or saying it in their own words ("ask the panel", "consult the panel", "get a second opinion from the panel", "have the panel ideate on this", "scope this request"). NEVER invoke it on your own judgement, however ambiguous the request or stuck the work — the user chooses when to spend the panel, and an unrequested run costs minutes and several xhigh calls they did not ask for. When invoked it runs a cross-model panel (Codex + a fresh Claude + Grok + Antigravity) on whatever the moment holds, and YOU pick the moment from the state of the session rather than asking. Before work has started the object is the REQUEST — what it leaves ambiguous, before effort is spent on the wrong reading. Once work is underway the object is the live PROBLEM — an impasse, an analysis that will not reconcile, an open question — and the answer is a menu of ways forward. Domain-neutral. It reviews nothing — when what the user is pointing at is finished work, `/assess turbo` is the right one and you route there in a line rather than asking them; the after-work review still runs when the task finishes either way.
---

## What this is

The panel — Codex, a fresh un-primed Claude, Grok, Antigravity (`agy`), optionally pi — run on demand, on
whatever the session is holding right now. `/assess turbo` is the third moment and stays
separate because the hook triggers it when a turn ends; this is the one a person calls by
hand — and it hands off to `/assess turbo` when what they are pointing at is finished work.

It has **two moments, not two commands** — and a third route out, to `/assess turbo`, when
what the user is pointing at is finished work rather than a request or a problem. Before
work starts, the object is the request:
nothing is built, so there is nothing to refute, and asking the models "how should this be
done?" only yields plausible plans with no way to choose — so every reviewer is asked what
it thinks is being **asked**, and all are forbidden from proposing an approach. Once work
is underway there is a live problem to test against, so the question inverts to "what would
you **try**?" and the reviewers propose while you execute.

Running this reviews nothing, so the Stop hook will still ask for `/assess` when the work
is done. That is correct; let it.

## Which moment — you decide, from the session

**Route by the object — what the user is pointing at — never by asking them which panel to
run.** That question is the friction this one command exists to remove.

1. **A finished artifact** ("check this", "review what you built", "a second opinion on
   this report") → neither panel below. That is `/assess turbo`, which runs the same
   reviewers in review mode against the work, with findings that die if they don't hold up.
   Say which one fits in a line and run it; routing is not bouncing.
2. **The request itself, with nothing produced yet** → the **before** panel. The object is
   the user's words, verbatim, and the question is what they leave ambiguous.
3. **A live problem, or an open question** → the **during** panel. A failing check that has
   survived attempts, numbers that will not reconcile, a dead end — or an open problem the
   user wants ideas on, with nothing failed yet.

The work state is what usually separates 2 from 3, and where it and the wording disagree,
the wording is evidence about the object rather than a routing question you are putting to
the user: at t=0 someone asking for **ideas** ("what would the panel try", "have the panel
ideate on this") wants a menu, which is the during panel, and they have said so. What the
work state does bind is the reverse direction — the two findings only the before panel can
produce (that the question cannot be answered with what is available, **tractability**, and
that the answerable question is adjacent to the one asked, **wrong question**) expire the
moment work begins. So an interpretive ask arriving mid-task is not the before panel run
late; it is a during-panel problem — "what we are building may have drifted from what was
asked" — and it goes in as one.

**Two budgets, not one.** At most one before-work run per line of work: when it ends by
putting a choice to the user, their answer is a follow-up to that run, not a new request to
panel. And at most one during-work run per stuck episode: when the menu is exhausted and
the problem still stands, escalate to the user with the evidence rather than re-running the
same facts for the same menu. Spending one does **not** spend the other — a line of work
that was scoped at t=0 and then hits a real impasse has the strongest claim on the panel
there is.

## How

### 1. Build the input

Build `$ART` with a **shell redirect or heredoc, not the Write tool**: a Write marks the
turn as having edited files, and a before-panel run that ends by putting a question to the
user should reach Stop having changed nothing.

**Before panel — the request plus context.** Gather context into one file, `$ART`, in your
session scratchpad if you have one, else `$TMPDIR` (bare `/tmp` is typically not writable
under the Bash sandbox, and a fixed name collides across concurrent sessions). Include what
the reviewers cannot see and would need: the relevant slice of this conversation, the source
documents, prior decisions, a file inventory. Do **NOT** include your own reading of the
request — that is the thing being tested. If there is genuinely no context, an **empty file
is fine** (`: > "$ART"`); the reviewers can still Read/Grep the working directory.

**During panel — the evidence packet.** `$ART` is the load-bearing input here, and it has
rules, because this is the one moment with no un-authored source: review has the artifact,
the before panel has the user's words, and this has only what you choose to include — your
selection is itself a theory. What goes in depends on the shape; a mixed case takes the
union:

- **A failing check** (a test, a build, a validation): **re-run it NOW and capture its raw
  output fresh.** Never paste from memory or scrollback — and if it passes on the re-run,
  stop: report that instead of consulting about a problem that no longer reproduces.
- **An analysis that will not reconcile** (contradictory numbers, an implausible result, a
  dataset resisting the question): **re-execute the queries or steps NOW and include their
  actual output** — the real numbers, row counts, and the exact query or code that produced
  each, never figures recalled from earlier in the session. Include both sides of the
  contradiction and the definitions of the metrics involved.
- **An open problem / ideation** (nothing failed — the user wants outside ideas): the
  evidence is the problem's current state — the framing, the constraints, the relevant prior
  discussion verbatim. Here, unlike the shapes above, your current thinking MAY go in —
  **labelled as the state of thinking to build beyond**, so the panel extends it instead of
  re-deriving it.
- **Attempts as facts** (the stuck shapes). Each attempt in order: the exact change or
  action and the observed result. Include abandoned attempts — they are negative evidence.
- **Over-include what you never suspected.** The output you believe matters plus the config
  you never doubted, the environment's *shape*, versions, the check's or metric's own
  definition. The packet must let a consultant disagree with you about where the problem
  lives.
- **Exclude your diagnosis entirely** (the stuck shapes). No theories, no "likely cause",
  no "I think". The consultants form their own reading; yours is the thing that has failed,
  and smuggled in it re-anchors them to the frame the panel exists to break. (Ideation is
  the exception above — there the frame has not failed, and the thinking is context, not
  contamination.)

**Both panels: no secrets, ever.** The packet travels verbatim to four external providers.
No `env` dumps, no `.env` or credential files, no auth headers or connection strings; name
the variable and redact the value (`DB_URL=[redacted]`), and scan captured output for
token-shaped runs before including it. Everywhere else this repo isolates provider keys per
reviewer; one pasted environment undoes that in a single step. (Data itself — including
personal or medical records the user is analyzing — is not restricted: the user has chosen
to trust the panel's providers with the problem, and the data is the problem. That trust is
**uniform across seats** — Codex, the fresh Claude, Grok, and Antigravity all get the same packet; a
privacy rule that would exclude one of them excludes the whole panel, so never drop a
single seat on privacy grounds.)

### 2. Run the panel

Pass the text through a **quoted heredoc**, never pasted inline into the command — a
request or problem statement containing `$VAR`, backticks or `$(...)` would otherwise be
expanded or executed by the shell, silently altering the exact wording that is the point of
passing it.

```
REQ="$(cat <<'VERBATIM'
<before: the user's request, exactly as written; multiple turns in order if it spans several>
<during: the user's words where they name the problem, plus the bare facts — what fails or
 will not reconcile, with what output, after how many attempts. Facts, never diagnosis.>
VERBATIM
)"
bash ~/.claude/skills/panel/panel.sh --mode scope   "$ART" "$REQ"   # before the work
bash ~/.claude/skills/panel/panel.sh --mode consult "$ART" "$REQ"   # during the work
```

`--mode` must come **before** the artifact path; after it, the flag is task text. The flag
names are the script's own (`scope` / `consult`) and predate this skill — one script, three
entry points, `review` being `/assess turbo`'s.

- **Check the delimiter against the text.** If what you are passing contains a line that
  is exactly `VERBATIM`, the heredoc ends there and the remainder is executed as shell —
  change the delimiter on both lines to something the text does not contain.
- The request you pass to the **before** panel MUST be the user's own words, verbatim. A
  paraphrase is your interpretation, which is exactly what that panel exists to test — pass
  it and you guarantee agreement, which makes the run worthless.
- **Run it OUTSIDE your Bash sandbox** (in Claude Code: `dangerouslyDisableSandbox`). Every
  reviewer is a network client, and a sandbox that routes egress through a host-allowlisted
  proxy refuses the tunnel and kills them slowly, with logs that read like flaky providers.
  `panel.sh` detects and names it, but only the caller can avoid it.
- **Long Bash timeout**: `600000` ms. Mid-task you may instead run it **backgrounded** with
  `ASSESS_PANEL_TIMEOUT=1500` and keep testing your own next hypothesis while it runs —
  wall-clock is the scarcest resource there. The before panel gets no such option: starting
  work destroys the two findings it exists to produce.
- Run it **from the project directory** (or set `ASSESS_PANEL_ROOT`) so the reviewers can
  Read/Grep the surrounding files.
- A reviewer that prints `>>> TIMED OUT` / `>>> FAILED` / `>>> SKIPPED` produced nothing.
  That is an **incomplete panel** — never "no ambiguity found" or "the panel's whole menu".
  Say so. (Partial output printed before a kill is still readable and still credits that
  reviewer.)
- Do NOT rewrite the reviewer instructions. The lenses are fixed inside `panel.sh` on
  purpose; you supply the input, never the question they are asked.

The lenses differ by moment, and differ from each other on purpose — asked the same
question, each extra reviewer would cost another call for no additional voice:

| | **before** | **during** |
|---|---|---|
| Codex | a six-slot interpretation schema — deliverable, underlying question, assumptions, out of scope, done condition, most likely failure | experiment cards — distinct hypotheses, each with a disconfirmer and the cheapest discriminating probe, ranked by information per cost |
| fresh Claude | assumptions, then a pre-mortem — wrong question, already done, tractability, hidden cost, misreading risk | the shared assumption of the failed attempts, attacked; on an open problem, the framing instead |
| Grok *(default on; `ASSESS_PANEL_GROK=0` disables)* | the request's extent — minimal and maximal reading, where a worker's default lands, the stopping rule, the spillover | lateral moves from outside the current approach, grounded in what the directory contains |
| Antigravity (`agy`) *(default on; `ASSESS_PANEL_AGY=0` disables)* | invariants & coupling — invariants that must hold, blast radius, implicit interface, edge & degradation cases, verification gap | boundary & inversion probes — nearest contrast case, exact fault line where the invariant breaks, minimal reducer |
| pi *(opt-in, `ASSESS_PANEL_PI=1`)* | the competing readings, and the single question that discriminates between them | the mundane causes a clever search skips — stale state, wrong branch or dataset, a filter that never took effect, join fan-out, units |
| **shared slot** | **ASSUMPTIONS**, answered first — the only cross-lineage comparison available | **FIRST MOVE**, answered last — a first move is a conclusion, worthless un-grounded |

### 3. Reconcile

**Before → by UNION.** There is no artifact to check a finding against and no evidence to
kill one with, so nothing is filtered for lacking proof:

- **Diff the ASSUMPTIONS lists first** — the only slot every reviewer answers.
- **Assumptions that diverge are the deliverable.** Independent readers filled the same
  blank differently, so the request is ambiguous there. Put it to the user as an explicit
  choice. Do NOT pick one silently and do NOT average them into a compromise reading —
  resolving it yourself throws away the only thing the panel produced.
- **Every other slot is single-source by design.** "The others didn't raise it" is not
  evidence against it; they were never asked. Check each against your own reading, which is
  the last voice.
- **WRONG QUESTION / TRACTABILITY / ALREADY DONE hits outrank everything** — they can make
  the task unnecessary or impossible, and they expire the moment you start.

**During → as a MENU.** Nothing is being refuted and nothing needs the user's decision yet:

- **Diff the FIRST MOVE slots first.** Different lineages independently proposing the same
  move is the strongest signal the run produces — do that move.
- **Order the rest by cost-to-test, cheapest first.** The run succeeds if ANY suggestion
  unblocks; divergent suggestions are breadth, not disagreement.
- **Run at least one proposed probe before your next speculative fix.** Binding: reading
  the menu, nodding, and resuming your own loop spends the panel on nothing.
- **A SHARED ASSUMPTION finding outranks the individual cards** — it names the frame
  everything else was searched inside. Test it even when a cheaper card looks promising.

### 4. Close the telemetry loop

If the footer printed `Panel run id: <id>`, it also printed the exact
`panel_stats.py --log … verdict <id>` command — use it verbatim, findings JSON on stdin
(`verdict --schema` prints the shape). **No run id → telemetry was off; skip this, and never
substitute another run's id.** Verdicts answer the moment's own question:

- **before**: `confirmed` = it changed your reading or became a question to the user;
  `refuted` = noise. (There is no artifact to check it against.)
- **during**: `confirmed` = you acted on it and it unblocked the work or changed your
  approach; `refuted` = tried it, dead end; `unverifiable` = untested. Log the dead ends —
  they are the precision signal.

`sources` names who raised it (`codex`, `claude`, `grok`, `agy`, `pi`, or `self` for your own).
Optional fields take **only** their literal values — `severity`: `HIGH`/`MED`/`LOW`;
`action`: `fixed`/`deferred`/`dismissed`/`none` — a synonym fails the whole call; omit the
key when none fits. One call with everything; a second call for the same run replaces the
first.

### 5. Report

The substance, never the raw panel. Then:

- **Before, readings converged** → state the shared assumptions and the done condition in a
  line or two, then get on with the work in the same turn.
- **Before, readings diverged, or a wrong-question / tractability / already-done hit** →
  put the choice to the user and **stop there**. Starting would pick a branch by default,
  which is the failure the run was spent to prevent.
- **During** → what was stuck or asked, what the panel proposed, what you tested, where
  things stand. Then continue the work.
- **Routed out to `/assess turbo`** (the object was finished work) → nothing to report from
  here; that skill's own reconciliation and summary take over.

Either way the Stop hook's review request, when the work finishes, is the after-work check
this deliberately is not.

## Detailed user input:

$ARGUMENTS
