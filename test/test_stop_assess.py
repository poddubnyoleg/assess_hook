"""End-to-end tests for hooks/stop_assess.py.

Each test runs the real hook as a subprocess with a synthetic transcript and a
fake `claude` CLI on PATH (so no tokens are spent and the classifier's verdict
is controlled per test). Every loop bug so far was a transcript-shape
regression — these fixtures pin the shapes that mattered:

  - stop_hook_active caps the hook at one prompt per stop-chain
  - a zero-tool cycle (recap/summary prose) never reaches the classifier
  - assess-run detection is structural, so file contents QUOTING the launch
    marker (e.g. this hook being Read) don't count as a run
  - isMeta harness injections don't advance the last-user-message boundary
  - classifier failure fails open

Run:  python3 -m unittest discover -s test
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "stop_assess.py"

LONG_PROSE = ("The summary of the issue, in a nutshell, is that the reviewer "
              "pipeline degraded silently under a stale key. " * 20)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def meta(text):
    return {"type": "user", "isMeta": True, "message": {"role": "user", "content": text}}


def tool_result(text):
    return {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "content": text}]}}


def a_text(text):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def a_tool(name, inp):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": inp}]}}


WORK = [
    user("build the widget module"),
    a_tool("Write", {"file_path": "/x/widget.py", "content": "class Widget:\n    pass\n" * 60}),
    a_text("Built the widget module with full validation. " + LONG_PROSE),
]


class HookTest(unittest.TestCase):

    def run_hook(self, records, *, level="NONE", rc=0, stop_hook_active=False, last_msg=""):
        """Returns (decision_dict, classifier_was_called)."""
        tmp = Path(tempfile.mkdtemp())
        transcript = tmp / "transcript.jsonl"
        transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n")

        fakebin = tmp / "bin"
        fakebin.mkdir()
        called = tmp / "classifier_called"
        fake = fakebin / "claude"
        fake.write_text(f'#!/bin/sh\ntouch "{called}"\ncat >/dev/null\necho {level}\nexit {rc}\n')
        fake.chmod(0o755)

        env = dict(os.environ, PATH=f"{fakebin}:{os.environ['PATH']}")
        env.pop("ASSESS_SKIP_HOOK", None)

        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({
                "hook_event_name": "Stop",
                "transcript_path": str(transcript),
                "stop_hook_active": stop_hook_active,
                "last_assistant_message": last_msg,
            }),
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout), called.exists()

    # --- structural loop guards ---

    def test_stop_hook_active_approves_without_classifying(self):
        # Second Stop in a chain we already blocked: approve, whatever happened.
        decision, called = self.run_hook(WORK, level="TURBO", stop_hook_active=True)
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)

    def test_zero_tool_cycle_approves_without_classifying(self):
        # A long technical recap with NO tool calls is conversation, not work —
        # the exact turn shape that used to classify NORMAL and start the loop.
        records = [user("give me issue summary in a nutshell"), a_text(LONG_PROSE)]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)

    def test_skill_tool_use_counts_as_assess_run(self):
        records = WORK + [
            meta("Stop hook feedback:\nRun /assess before finishing."),
            a_tool("Skill", {"skill": "assess", "args": "turbo"}),
            tool_result("Launching skill: assess"),
            a_text("Review done; two nits fixed."),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)

    def test_launch_marker_tool_result_counts_as_assess_run(self):
        records = WORK + [tool_result("Launching skill: assess"), a_text("done")]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "approve")

    def test_marker_quoted_in_file_content_is_not_a_run(self):
        # Reading/grepping the hook source injects the marker string into a
        # tool_result mid-content. That must NOT read as "assess already ran".
        hook_source = '1\thas_assess = "launching skill: assess" in json.dumps(recent).lower()\n'
        records = [
            user("fix the hook"),
            tool_result(hook_source),
            a_tool("Edit", {"file_path": "/x/hook.py", "old_string": "a", "new_string": "b"}),
            a_text("Adjusted the guard. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)

    def test_ismeta_injection_does_not_advance_boundary(self):
        # The injected block reason (isMeta) must not become the "last user
        # message" — otherwise the assess run before it goes out of scope and
        # the hook re-blocks forever (the original infinite loop).
        records = WORK + [
            a_tool("Skill", {"skill": "assess"}),
            tool_result("Launching skill: assess"),
            meta("Base directory for this skill: /Users/x/.claude/skills/assess\n## Levels ..."),
            a_text("Self-review complete, no critical issues."),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)

    def test_prompted_cycle_backstop_without_stop_hook_active(self):
        # If the CLI ever stops sending stop_hook_active, the injected prompt
        # itself must end the loop — even when the agent answered with an
        # inline review that applied a fix, the shape that beat the old
        # edit_after_prompt guard.
        records = WORK + [
            meta("Stop hook feedback:\nRun /assess before finishing. If issues "
                 "found: fix minor ones directly."),
            a_tool("Edit", {"file_path": "/x/widget.py",
                            "old_string": "pass", "new_string": "return 1"}),
            a_text("Inline review: found an off-by-one, fixed it. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NORMAL", stop_hook_active=False)
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)

    # --- classification plumbing ---

    def test_substantial_work_blocks_turbo(self):
        decision, called = self.run_hook(WORK, level="TURBO")
        self.assertEqual(decision["decision"], "block")
        self.assertIn("turbo", decision["reason"].lower())
        self.assertTrue(called)

    def test_bounded_work_blocks_normal(self):
        decision, called = self.run_hook(WORK, level="NORMAL")
        self.assertEqual(decision["decision"], "block")
        self.assertNotIn("turbo", decision["reason"].lower())

    def test_none_verdict_approves(self):
        decision, called = self.run_hook(WORK, level="NONE")
        self.assertEqual(decision["decision"], "approve")
        self.assertTrue(called)

    def test_classifier_failure_fails_open(self):
        decision, called = self.run_hook(WORK, rc=1)
        self.assertEqual(decision["decision"], "approve")
        self.assertTrue(called)

    def test_short_reply_approves(self):
        decision, called = self.run_hook([user("hi"), a_text("hello!")])
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)

    def test_skip_hook_env_approves_immediately(self):
        tmp = Path(tempfile.mkdtemp())
        transcript = tmp / "t.jsonl"
        transcript.write_text(json.dumps(WORK[0]) + "\n")
        env = dict(os.environ, ASSESS_SKIP_HOOK="1")
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"transcript_path": str(transcript)}),
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(json.loads(proc.stdout)["decision"], "approve")


if __name__ == "__main__":
    unittest.main()
