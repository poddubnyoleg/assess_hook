"""Stop hook: a cheap classifier picks one of three assess levels.

Levels:
- NONE   -> approve (trivial turn: a reply, an explanation, a tiny edit)
- NORMAL -> block "Run /assess" (real but bounded work: self-review in-session)
- TURBO  -> block "Run /assess turbo" (substantial artifact: also run the
            cross-model panel — Codex + a fresh Claude)

Design:
- Scopes "recent" to lines after the last user message in the transcript
- If /assess already ran this turn -> approve (no recursion)
- A cheap Sonnet call (low effort, no tools) classifies the level
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


def analyze_transcript(path):
    """Parse transcript; return (has_tools, has_assess, recent_assistant_text)."""
    if not path:
        return False, False, ""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 500_000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except Exception:
        return False, False, ""

    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            continue

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

    # Detect a prior /assess run anywhere in the recent slice. The marker
    # "Launching skill: assess" lives in tool_result records (type=user), NOT in
    # assistant text — so scan the whole slice, not just assistant text blocks.
    has_assess = "launching skill: assess" in json.dumps(recent).lower()

    edit_tools = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
    has_tools = False
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
                name = block.get("name", "")
                inp = block.get("input") or {}
                path = inp.get("file_path") or inp.get("notebook_path") or ""
                if name in edit_tools:
                    has_tools = True
                    if name == "Edit":
                        parts.append(
                            f"[Edit {path}] {snippet(inp.get('old_string'))} → "
                            f"{snippet(inp.get('new_string'))}"
                        )
                    elif name == "Write":
                        body = inp.get("content", "") or ""
                        parts.append(f"[Write {path}] ({len(body)} chars) {snippet(body)}")
                    elif name == "MultiEdit":
                        edits = inp.get("edits") or []
                        parts.append(f"[MultiEdit {path}] {len(edits)} edit(s)")
                    elif name == "NotebookEdit":
                        parts.append(f"[NotebookEdit {path}] {snippet(inp.get('new_source'))}")
                elif name == "Bash":
                    cmd = inp.get("command", "") or ""
                    if any(tok in cmd for tok in ("rm ", "mv ", "cp ", ">", ">>", "mkdir", "touch")):
                        parts.append(f"[Bash] {snippet(cmd, 300)}")

    recent_text = "\n\n".join(parts).strip()
    return has_tools, has_assess, recent_text


def classify_with_llm(msg, tool_context):
    """Return one of 'NONE', 'NORMAL', 'TURBO', or None on error."""
    prompt = (
        "Classify the work the assistant just did this turn. Output ONE word.\n\n"
        "NONE   — trivial: a reply, an explanation, a tiny edit. No review needed.\n"
        "NORMAL — real but bounded work: a function, a bugfix, a moderate edit. "
        "A quick self-review is warranted.\n"
        "TURBO  — a substantial artifact: a new module or file, a large or multi-file "
        "refactor, a full research report. A deep cross-model review is warranted.\n\n"
        "Be conservative: if unsure between NONE and NORMAL pick NONE; if unsure between "
        "NORMAL and TURBO pick NORMAL.\n\n"
        "Assistant's recent work this turn:\n---\n" + msg + tool_context +
        "\n---\n\nONE WORD: NONE, NORMAL, or TURBO"
    )
    # Strip ANTHROPIC_API_KEY so claude CLI uses keychain OAuth instead
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
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
            timeout=90,
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

# 0. Skip for headless agent runs (monitor, dashboard-analyzer)
HEADLESS_AGENTS = {"monitor", "dashboard-analyzer"}
if transcript_path:
    try:
        with open(transcript_path, "rb") as f:
            tail = f.read()[-5000:].decode("utf-8", errors="replace")
        for agent_name in HEADLESS_AGENTS:
            if f'"agentSetting": "{agent_name}"' in tail or f'"agentSetting":"{agent_name}"' in tail:
                approve()
    except Exception:
        pass

# 1. Launching /assess right now -> approve (covers both normal and turbo)
if "launching skill: assess" in msg.lower():
    approve()

# 2. Analyze transcript since last user message
has_tools, has_assess, recent_text = analyze_transcript(transcript_path)

# Prefer the full recent assistant message queue over just the last message
classify_msg = recent_text or msg

# 3. /assess already ran this turn -> approve (prevents recursion)
if has_assess:
    approve()

# 4. Short text-only message -> approve (text-only replies aren't shipped work)
if len(classify_msg) < 250 and not has_tools:
    approve()

# 5. Classify via claude CLI
tool_context = "\n[NOTE: This turn included file Edit/Write tool calls.]" if has_tools else ""
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
# None = classifier error, fall through to heuristic

# 6. Fallback heuristic (conservative: never auto-escalates to turbo)
if has_tools or len(classify_msg) > 500:
    block_normal()
else:
    approve()
