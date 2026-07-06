"""Stop hook: a cheap classifier picks one of three assess levels.

Levels:
- NONE   -> approve (trivial turn: a reply, an explanation, a tiny edit)
- NORMAL -> block "Run /assess" (real but bounded work: self-review in-session)
- TURBO  -> block "Run /assess turbo" (substantial artifact: also run the
            cross-model panel — Codex + a fresh Claude)

Design:
- stop_hook_active=True -> approve. The harness sets it on every Stop that
  follows a stop-hook block in the same chain, so this enforces "prompt once
  per cycle" STRUCTURALLY: however the agent handled the prompt (ran the
  skill, reviewed inline, applied fixes, declined), its response is final.
  This closes every assess-of-assess loop variant — including the one where
  an inline review applies a fix and a re-classification flags the review
  prose + fix edits as fresh work. (Field verified against the installed CLI
  bundle, 2.1.199 — not yet in the public docs; read with .get() so a CLI
  that stops sending it degrades to the guards below, not to a loop.)
- Scopes "recent" to lines after the last user message in the transcript
- If /assess already ran this cycle -> approve. This still matters after the
  guard above: a user-typed /assess ends its OWN chain (stop_hook_active is
  False there). Detection is STRUCTURAL — a Skill tool_use with skill=assess,
  the injected skill body, or a tool_result that IS the launch marker — never
  a substring over the dumped slice: file contents that merely QUOTE the
  marker (e.g. this very file being Read) must not count as a run.
- A cycle with no tool calls at all is conversation, not shipped work ->
  approve without classifying. (A long recap/summary getting classified
  NORMAL was the observed loop trigger.)
- Background work still in flight -> approve. Launching background agents
  (or bg Bash / Workflow) ENDS the turn, so Stop fires mid-task; blocking
  there demands a review of work that doesn't exist yet AND burns the
  cycle's single prompt, so the real deliverable synthesized after the
  completion notifications ships unreviewed (has_assess is already true
  for the cycle). Launches are matched to delivered <task-notification>
  turns by tool_use_id; unmatched launch -> defer to a later Stop. Agent/
  Workflow launches defer unconditionally (they reliably notify, so the
  review is postponed, never skipped); a bg Bash launch defers only while
  the cycle has no file edits, because dev-server-style commands never
  exit by design and must not suppress review of shipped work beside
  them. The notification turns themselves are plain type=user records (no isMeta,
  CLI 2.1.199) but are machine turns: they must not advance the cycle
  boundary, or the launches they answer leave the "recent" slice and
  this detection goes blind mid-flight.
- A cheap Sonnet call (low effort, no tools) classifies the level
- Classifier unavailable -> approve (fail-open, with a stderr note): a missed
  review costs less than a broken classifier blocking every Stop
- Conservative defaults: unsure NONE/NORMAL -> NONE; unsure NORMAL/TURBO -> NORMAL
"""
import sys
import json
import os
import re
import subprocess


def approve():
    print(json.dumps({"decision": "approve"}))
    sys.exit(0)


def block_normal():
    print(json.dumps({
        "decision": "block",
        "reason": "Run /assess before finishing. If issues found: fix minor ones "
                  "directly, but ASK THE USER before significant changes.",
    }))
    sys.exit(0)


def block_turbo():
    print(json.dumps({
        "decision": "block",
        "reason": "Run /assess turbo before finishing — substantial work was done. "
                  "Turbo does the self-review AND a cross-model panel (Codex + a fresh "
                  "Claude), then reconciles by axis. Fix minor issues directly, but ASK "
                  "THE USER before significant changes.",
    }))
    sys.exit(0)


def _text_of(content):
    """Flatten a message content field (str or block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b["text"] for b in content
                        if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def is_assess_launch(r):
    """True if this record IS an /assess run — not text that merely mentions one."""
    msg = r.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None

    if r.get("type") == "assistant" and isinstance(content, list):
        # The model invoked the Skill tool with skill=assess.
        for b in content:
            if (isinstance(b, dict) and b.get("type") == "tool_use"
                    and b.get("name") == "Skill"
                    and "assess" in str((b.get("input") or {}).get("skill", "")).lower()):
                return True

    if r.get("type") == "user":
        if isinstance(content, list):
            # The harness ack for a Skill launch: a tool_result whose content
            # STARTS with the marker. startswith, not `in` — Read/Grep output
            # quoting the marker mid-file must not match.
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                cc = b.get("content")
                if isinstance(cc, str):
                    texts = [cc]
                elif isinstance(cc, list):
                    texts = [t.get("text", "") for t in cc if isinstance(t, dict)]
                else:
                    texts = []
                for t in texts:
                    if t.strip().lower().startswith("launching skill: assess"):
                        return True
        text = _text_of(content).lower()
        # The injected skill body (an isMeta turn) — how a user-typed /assess
        # shows up inside the cycle.
        if r.get("isMeta") is True and "base directory for this skill:" in text \
                and "skills/assess" in text:
            return True
        # A user-typed slash-command record.
        if "<command-name>assess" in text or "<command-name>/assess" in text:
            return True
    return False


def analyze_transcript(path):
    """Parse transcript tail.

    Returns (ok, has_edits, any_tools, has_assess, prior_assess, prompted,
    pending_bg, recent_text):
    - ok           whether the transcript was readable and non-empty
    - has_edits    file Edit/Write tools ran after the last real user message
    - any_tools    ANY tool ran after the last real user message
    - has_assess   /assess ran after the last real user message
    - prior_assess /assess ran BEFORE it (earlier in the session/window)
    - prompted     OUR "Run /assess" block reason already sits in this cycle
    - pending_bg   background tasks launched this cycle with no completion
                   notification yet — the turn ended as a handoff, not done
    - recent_text  assistant text + edit summaries since then, chronological
    """
    empty = (False, False, False, False, False, False, False, "")
    if not path:
        return empty
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            # 2MB, not less: launch acks sit at the START of a cycle, and a
            # heavy multi-agent cycle can accumulate hundreds of KB of results
            # before a mid-flight Stop. Truncating the acks away would read as
            # "nothing in flight" and reintroduce the premature block under
            # exactly the load the in-flight guard exists for.
            f.seek(max(0, size - 2_000_000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except Exception:
        return empty

    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    if not records:
        return empty

    # Scope to records after the last *real* user message.
    # Tool results also have type=user; ignore those.
    def is_real_user_msg(r):
        if r.get("type") != "user":
            return False
        # Harness-injected turns (our own "Run /assess" block reason, local-command
        # caveats, etc.) are also type=user but carry isMeta=True. They are NOT real
        # user turns. Counting them would advance the boundary past an /assess run that
        # already happened this turn, so has_assess goes False and the hook re-blocks
        # forever — the infinite loop this guard exists to prevent.
        if r.get("isMeta") is True:
            return False
        msg = r.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        # Background-task completion notifications are delivered as PLAIN user
        # records (no isMeta — CLI 2.1.199), but they are machine turns: they
        # must not start a new cycle, or the launches they answer fall out of
        # the "recent" slice and in-flight detection below goes blind.
        if _text_of(content).strip().lower().startswith("<task-notification>"):
            return False
        if isinstance(content, str):
            return True
        if isinstance(content, list):
            return any(isinstance(b, dict) and b.get("type") != "tool_result" for b in content)
        return False

    last_user_idx = -1
    for i, r in enumerate(records):
        if is_real_user_msg(r):
            last_user_idx = i
    recent = records[last_user_idx + 1:] if last_user_idx >= 0 else records
    prior = records[:last_user_idx + 1] if last_user_idx >= 0 else []

    has_assess = any(is_assess_launch(r) for r in recent)
    # Classifier hint only: an assess earlier in the session means the bulk of
    # `git diff HEAD` may already be reviewed and only newer work matters.
    prior_assess = any(is_assess_launch(r) for r in prior)

    # Our own injected block reason ("Run /assess ...") comes back as an isMeta
    # user turn. Its presence in THIS cycle's slice means we already prompted.
    def is_our_prompt(r):
        if r.get("type") != "user" or r.get("isMeta") is not True:
            return False
        m = r.get("message") or {}
        return "run /assess" in _text_of(m.get("content") if isinstance(m, dict) else None).lower()

    prompted = any(is_our_prompt(r) for r in recent)

    # Background work in flight this cycle. Launches are detected from ground
    # truth: the launch-ack tool_result for Agent ("Async agent launched
    # successfully...") / Bash ("Command running in background..."), matched
    # by startswith so grep/Read output QUOTING an ack mid-content doesn't
    # count — the same quoting rule as the assess marker. The ack text alone
    # is still forgeable (a foreground `cat` of a file STARTING with an ack),
    # so the id must also belong to a tool_use that can launch background
    # work. (Workflow is the exception: background by contract, its tool_use
    # block itself is the launch — notification-on-tool_use_id field-verified,
    # 4/4 across three sessions.) Completions are DELIVERED
    # <task-notification> turns carrying the launch's tool_use_id — a plain
    # user record starting with the tag (CLI 2.1.199) or an attachment with
    # commandMode=task-notification (2.1.150). queue-operation enqueues are
    # deliberately NOT counted: an enqueued-but-undelivered notification means
    # the model hasn't seen the result yet, and observed enqueue+remove pairs
    # show the queue can drop entries without delivering. Any delivered status
    # counts (completed, failed, killed): no longer in flight either way.
    #
    # Launches are split by how reliably they notify (field-measured):
    # - Agent / Workflow notify essentially always -> defer unconditionally;
    #   the completion-triggered Stop reviews the whole cycle, so deferral is
    #   a postponement, not a skip.
    # - bg Bash orphans ~8% of the time, and long-lived processes (dev
    #   server, watcher, tail -f) never exit BY DESIGN -> defer only while
    #   the cycle has no file edits, so a forever-running server can't
    #   suppress review of real shipped work sitting next to it.
    launch_prefixes = ("async agent launched successfully",
                       "command running in background")
    notif_id_re = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")
    reliable, bash_only, notified = set(), set(), set()
    use_info = {}  # tool_use_id -> (tool name, run_in_background input)
    for r in recent:
        rtype = r.get("type")
        msg_ = r.get("message") or {}
        content = msg_.get("content") if isinstance(msg_, dict) else None
        if rtype == "user":
            text = _text_of(content)
            if text.strip().lower().startswith("<task-notification>"):
                notified.update(notif_id_re.findall(text))
            if isinstance(content, list):
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    # An errored call never started background work (matters
                    # for Workflow, whose tool_use alone marks a launch): a
                    # bad script would otherwise read as in-flight forever.
                    if b.get("is_error"):
                        notified.add(b.get("tool_use_id"))
                        continue
                    cc = b.get("content")
                    if isinstance(cc, str):
                        texts = [cc]
                    elif isinstance(cc, list):
                        texts = [t.get("text", "") for t in cc if isinstance(t, dict)]
                    else:
                        texts = []
                    if not any(t.strip().lower().startswith(launch_prefixes) for t in texts):
                        continue
                    name, bg = use_info.get(b.get("tool_use_id"), (None, None))
                    if name == "Agent":
                        reliable.add(b.get("tool_use_id"))
                    elif name == "Bash" and bg:
                        bash_only.add(b.get("tool_use_id"))
                    elif name is None:
                        # The tool_use fell outside the tail window; the ack
                        # is the only evidence left -> count it. (A forged ack
                        # always has its tool_use in-window, right before it.)
                        reliable.add(b.get("tool_use_id"))
        elif rtype == "assistant" and isinstance(content, list):
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                inp = b.get("input") or {}
                use_info[b.get("id")] = (b.get("name"), bool(inp.get("run_in_background")))
                if b.get("name") == "Workflow":
                    reliable.add(b.get("id"))
        elif rtype == "attachment":
            att = r.get("attachment") or {}
            if isinstance(att, dict) and att.get("commandMode") == "task-notification":
                notified.update(notif_id_re.findall(att.get("prompt") or ""))

    edit_tools = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
    has_edits = False
    any_tools = False
    parts = []  # interleaved assistant text + edit summaries in chronological order

    def snippet(s, n=200):
        s = (s or "").replace("\n", " ")
        return s[:n] + ("…" if len(s) > n else "")

    for r in recent:
        if r.get("type") != "assistant":
            continue
        msg = r.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "")
                if text.strip():
                    parts.append(text)
            elif btype == "tool_use":
                any_tools = True
                name = block.get("name", "")
                inp = block.get("input") or {}
                path_ = inp.get("file_path") or inp.get("notebook_path") or ""
                if name in edit_tools:
                    has_edits = True
                    if name == "Edit":
                        parts.append(
                            f"[Edit {path_}] {snippet(inp.get('old_string'))} → "
                            f"{snippet(inp.get('new_string'))}"
                        )
                    elif name == "Write":
                        body = inp.get("content", "") or ""
                        parts.append(f"[Write {path_}] ({len(body)} chars) {snippet(body)}")
                    elif name == "MultiEdit":
                        edits = inp.get("edits") or []
                        parts.append(f"[MultiEdit {path_}] {len(edits)} edit(s)")
                    elif name == "NotebookEdit":
                        parts.append(f"[NotebookEdit {path_}] {snippet(inp.get('new_source'))}")
                elif name == "Bash":
                    cmd = inp.get("command", "") or ""
                    if any(tok in cmd for tok in ("rm ", "mv ", "cp ", ">", ">>", "mkdir", "touch")):
                        parts.append(f"[Bash] {snippet(cmd, 300)}")

    # Decided here because the bg-Bash arm is gated on has_edits (above).
    pending_bg = bool(reliable - notified) or \
        (bool(bash_only - notified) and not has_edits)

    recent_text = "\n\n".join(parts).strip()
    return (True, has_edits, any_tools, has_assess, prior_assess, prompted,
            pending_bg, recent_text)


def classify_with_llm(msg, tool_context):
    """Return one of 'NONE', 'NORMAL', 'TURBO', or None on error."""
    prompt = (
        "Classify the work the assistant just did this turn. Output ONE word.\n\n"
        "NONE   — trivial: a reply, an explanation, a tiny edit. Also NONE: the turn "
        "only summarizes, recaps, explains, or reviews work done EARLIER (including "
        "applying fixes from a just-finished review) without producing a new artifact. "
        "No review needed.\n"
        "NORMAL — real but bounded NEW work: a function, a bugfix, a moderate edit. "
        "A quick self-review is warranted.\n"
        "TURBO  — a substantial NEW artifact: a new module or file, a large or multi-file "
        "refactor, a full research report. A deep cross-model review is warranted.\n\n"
        "Be conservative: if unsure between NONE and NORMAL pick NONE; if unsure between "
        "NORMAL and TURBO pick NORMAL.\n\n"
        "Assistant's recent work this turn:\n---\n" + msg + tool_context +
        "\n---\n\nONE WORD: NONE, NORMAL, or TURBO"
    )
    # Strip ANTHROPIC_API_KEY so claude CLI uses keychain OAuth instead.
    # Mark this nested `claude -p` so its own Stop hook short-circuits (see the
    # recursion guard in main) instead of spawning yet another classifier.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["ASSESS_SKIP_HOOK"] = "1"
    try:
        result = subprocess.run(
            [
                "claude", "-p",
                "--model", "sonnet",
                "--effort", "low",
                "--tools", "",
                "--disable-slash-commands",
                "--strict-mcp-config",
                "--no-session-persistence",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            # Claude Code kills a hook command at 60s by default; this budget must
            # stay UNDER that so a slow classifier ends as our fail-open, not as
            # the harness killing the hook mid-decision.
            timeout=50,
            env=env,
        )
        if result.returncode != 0:
            sys.stderr.write(f"stop_assess: claude CLI exit {result.returncode}: {result.stderr[:200]}\n")
            return None
        out = result.stdout.strip().upper()
        # Compliant case: the reply is exactly the verdict word.
        if out in ("NONE", "NORMAL", "TURBO"):
            return out
        # Non-compliant (a sentence): take the LAST standalone level word as the
        # verdict, rather than a fixed TURBO-first priority that would bias a
        # multi-word reply toward the expensive level.
        words = re.findall(r"\b(NONE|NORMAL|TURBO)\b", out)
        return words[-1] if words else None
    except Exception as e:
        sys.stderr.write(f"stop_assess: {e}\n")
        return None


# --- Main ---
d = json.load(sys.stdin)
msg = d.get("last_assistant_message", "")
transcript_path = d.get("transcript_path", "")

# 0a. Recursion guard. The turbo panel (panel.sh) and this hook's own classifier
# spawn nested `claude -p` subprocesses. Those nested instances inherit the user's
# global Stop hook (this file), so when one finishes the hook fires on IT too. A
# panel reviewer has only Read/Grep/Glob — it cannot run /assess — so a block tells
# it to "Run /assess turbo," which it can't, and it confabulates a "panel couldn't
# run, here's my self-review" message that REPLACES its real review in stdout. The
# panel then captures that garbage and silently degrades to one lineage. Any process
# launched by panel.sh / the classifier carries ASSESS_SKIP_HOOK=1, so detect it and
# get out of the way immediately — never block (or re-classify) a nested assess run.
if os.environ.get("ASSESS_SKIP_HOOK") == "1":
    approve()

# 0b. We already blocked once in this stop-chain. stop_hook_active is True on
# every Stop that follows a stop-hook block, so approving here caps the hook at
# ONE prompt per cycle no matter how the agent responded (skill run, inline
# review, fixes, or a refusal) — the structural fix for "assess the assessment".
if d.get("stop_hook_active"):
    approve()

# 0c. Skip for headless agent runs (monitor, dashboard-analyzer)
HEADLESS_AGENTS = {"monitor", "dashboard-analyzer"}
if transcript_path:
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 5000))
            tail = f.read().decode("utf-8", errors="replace")
        for agent_name in HEADLESS_AGENTS:
            if f'"agentSetting": "{agent_name}"' in tail or f'"agentSetting":"{agent_name}"' in tail:
                approve()
    except Exception:
        pass

# 1. Analyze transcript since last user message
ok, has_tools, any_tools, has_assess, prior_assess, prompted, pending_bg, recent_text = \
    analyze_transcript(transcript_path)

# Prefer the full recent assistant message queue over just the last message
classify_msg = recent_text or msg

# 2. /assess already ran this cycle -> approve. Needed on top of 0b: a
# user-typed /assess ends its own fresh chain, where stop_hook_active is False.
if has_assess:
    approve()

# 2b. Backstop for a CLI that stops sending stop_hook_active: if OUR "Run
# /assess" prompt already sits in this cycle's slice, we prompted once —
# approve no matter how the agent responded. Deliberately NO edit-after-prompt
# condition: fixes applied in response to the prompt are part of the response,
# not new work to re-review — the old guard's edit condition is exactly what
# let inline-review-plus-fix loop forever. Redundant while 0b works; harmless.
if prompted:
    approve()

# 2c. Background tasks launched this cycle are still running -> approve. The
# turn ended because the agent HANDED OFF to background work, not because the
# work is done; the completion notification re-invokes the agent, and that
# later Stop sees launches and notifications paired up and classifies the real
# deliverable. Blocking here reviews nothing AND spends has_assess for the
# whole cycle, so the eventual deliverable would ship unreviewed (observed:
# /assess demanded mid-research with three agents still in flight). Residual
# risk: an Agent that dies without ever notifying suppresses review of
# EVERYTHING in the cycle — including unrelated edits — until the next user
# message; accepted because agents notified 100% of the time in field data
# and the bg-Bash arm (where orphans are real) is already edit-gated.
if pending_bg:
    approve()

# 3. No tool calls at all this cycle -> conversation, not shipped work. There is
# no artifact to review, however long or technical the prose reads — a recap of
# finished work classified as NORMAL was the observed loop trigger. Research
# reports still reach the classifier: producing one always involves tool calls.
if ok and not any_tools:
    approve()

# 4. Short text without file edits -> approve
if len(classify_msg) < 250 and not has_tools:
    approve()

# 5. Classify via claude CLI
tool_context = ""
if has_tools:
    tool_context += "\n[NOTE: This turn included file Edit/Write tool calls.]"
if prior_assess:
    tool_context += ("\n[NOTE: An /assess review already ran earlier in this session; "
                     "only work NEWER than that review warrants another one.]")
# Keep head + tail when long: early [Write]/[Edit] summaries (the substantial-artifact
# signal) sit at the head and would be lost by a tail-only cut, biasing toward NORMAL.
if len(classify_msg) > 8000:
    classify_input = classify_msg[:4000] + "\n…\n" + classify_msg[-4000:]
else:
    classify_input = classify_msg
level = classify_with_llm(classify_input, tool_context)
if level == "TURBO":
    block_turbo()
elif level == "NORMAL":
    block_normal()
elif level == "NONE":
    approve()

# 6. Classifier unavailable (CLI error, timeout, or non-compliant output): fail
# OPEN. A missed review costs less than a broken classifier blocking every Stop —
# an infra hiccup must never manufacture a review demand.
sys.stderr.write("stop_assess: classifier unavailable; approving (fail-open)\n")
approve()
