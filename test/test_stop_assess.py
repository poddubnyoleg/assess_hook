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


def tool_result(text, tool_use_id=None):
    block = {"type": "tool_result", "content": text}
    if tool_use_id:
        block["tool_use_id"] = tool_use_id
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def task_notification(tool_use_id, status="completed"):
    # Delivered completion notification — a PLAIN user record (no isMeta),
    # the CLI 2.1.199 shape observed in the field.
    return user(f"<task-notification>\n<task-id>x1</task-id>\n"
                f"<tool-use-id>{tool_use_id}</tool-use-id>\n"
                f"<status>{status}</status>\n</task-notification>")


def a_text(text):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def a_tool(name, inp, id=None):
    block = {"type": "tool_use", "name": name, "input": inp}
    if id:
        block["id"] = id
    return {"type": "assistant", "message": {"role": "assistant", "content": [block]}}


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
        # The prompt the classifier received, for tests that assert on the
        # hook's injected [NOTE: …] hints rather than on the decision alone.
        self.classifier_prompt = tmp / "classifier_prompt"
        fake = fakebin / "claude"
        fake.write_text(f'#!/bin/sh\ntouch "{called}"\n'
                        f'cat >"{self.classifier_prompt}"\necho {level}\nexit {rc}\n')
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

    def test_panel_skill_run_does_not_satisfy_the_review_gate(self):
        # The on-demand `panel` skill reviews nothing, so running it must NOT count
        # as "/assess already ran" — otherwise work done afterwards reaches Stop with
        # the flag spent and ships with no review and no notice.
        #
        # Feeds the REAL SKILL.md, not a fixture: this skill's body is injected as an
        # isMeta turn, and both detectors that could misread it (is_assess_launch's
        # skill-body branch, is_our_prompt) work by matching text. They are anchored
        # now — see the two tests below — but this is the end-to-end proof for the
        # actual file that ships, whatever it grows to say.
        root = Path(__file__).resolve().parent.parent
        panel = root / "skills" / "panel" / "panel.sh"
        self.assertTrue(panel.resolve().is_file(),
                        f"{panel} must resolve — SKILL.md mandates this path, so a "
                        f"dangling link leaves no documented way to run /panel")
        skill = (root / "skills" / "panel" / "SKILL.md").read_text()
        records = [
            user("research whether we can add a third reviewer"),
            a_tool("Skill", {"skill": "panel", "args": ""}),
            tool_result("Launching skill: panel"),
            meta("Base directory for this skill: /Users/x/.claude/skills/panel\n" + skill),
            a_text("Readings converged; assumptions agreed. Building it."),
            a_tool("Write", {"file_path": "/x/third.py", "content": "x = 1\n" * 60}),
            a_text("Added the third reviewer. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="TURBO")
        self.assertEqual(decision["decision"], "block")
        self.assertIn("turbo", decision["reason"].lower())
        self.assertTrue(called)

    def _panel_run_then_work(self, body):
        return [
            user("research whether we can add a third reviewer"),
            a_tool("Skill", {"skill": "panel", "args": ""}),
            tool_result("Launching skill: panel"),
            meta(body),
            a_tool("Write", {"file_path": "/x/third.py", "content": "x = 1\n" * 60}),
            a_text("Added the third reviewer. " + LONG_PROSE),
        ]

    def test_skill_body_merely_citing_the_assess_path_is_not_a_review(self):
        # is_assess_launch used to scan the WHOLE injected body, so any skill whose
        # instructions mentioned a path under skills/assess read as a completed review.
        # The header line names the skill; the body doesn't. This exact shape — the
        # panel skill documenting the panel.sh both skills share — disarmed the gate.
        records = self._panel_run_then_work(
            "Base directory for this skill: /Users/x/.claude/skills/panel\n"
            "Run the shared panel: ~/.claude/skills/assess/panel.sh --mode scope\n")
        decision, called = self.run_hook(records, level="TURBO")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)

    def test_skill_body_describing_the_handoff_is_not_our_prompt(self):
        # Same class, second channel: is_our_prompt matched a bare "run /assess" in any
        # isMeta turn, so a skill body explaining what the hook will later ask for read
        # as "already prompted this cycle" and approved the turn. Our own reasons say
        # "Run /assess ... before finishing"; prose describing them does not.
        records = self._panel_run_then_work(
            "Base directory for this skill: /Users/x/.claude/skills/panel\n"
            "When the work is done the Stop hook will tell you to run /assess turbo.\n")
        decision, called = self.run_hook(records, level="TURBO")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)

    def test_midtask_panel_run_does_not_satisfy_the_review_gate(self):
        # Same skill, its other moment: a mid-task panel run gathers outside
        # suggestions so work can CONTINUE, reviews nothing, and so must not
        # count as "/assess already ran" — the work it unblocked would otherwise
        # reach Stop with the flag spent and ship with no review and no notice.
        root = Path(__file__).resolve().parent.parent
        panel = root / "skills" / "panel" / "panel.sh"
        self.assertTrue(panel.resolve().is_file(),
                        f"{panel} must resolve — SKILL.md mandates this path, so a "
                        f"dangling link leaves no documented way to run /panel")
        skill = (root / "skills" / "panel" / "SKILL.md").read_text()
        records = [
            user("ask the panel about this flaky test"),
            a_tool("Skill", {"skill": "panel", "args": ""}),
            tool_result("Launching skill: panel"),
            meta("Base directory for this skill: /Users/x/.claude/skills/panel\n" + skill),
            a_text("The panel's first moves converge on the fixture; testing that."),
            a_tool("Write", {"file_path": "/x/fix.py", "content": "x = 1\n" * 60}),
            a_text("Fixture was stale; rebuilt it and the test passes. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="TURBO")
        self.assertEqual(decision["decision"], "block")
        self.assertIn("turbo", decision["reason"].lower())
        self.assertTrue(called)

    def test_panel_in_cycle_biases_the_classifier_up_not_down(self):
        # A panel turn's tail is reconciliation prose plus applied fixes,
        # which surface-matches the classifier's "reviews work done EARLIER ->
        # NONE" escape hatch — so the turn where a hard problem finally cracked
        # would be the one most likely to be classified down, silently. The
        # hook counters with an injected note; pin that it reaches the
        # classifier, and only on cycles where a panel run actually happened.
        panel_records = [
            user("ask the panel about this flaky test"),
            a_tool("Skill", {"skill": "panel", "args": ""}),
            tool_result("Launching skill: panel"),
            a_tool("Write", {"file_path": "/x/fix.py", "content": "x = 1\n" * 60}),
            a_text("Reconciled the panel's suggestions and applied the fix. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(panel_records, level="NORMAL")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)
        self.assertIn("panel run is NOT a review",
                      self.classifier_prompt.read_text())

        decision, called = self.run_hook(WORK, level="NORMAL")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)
        self.assertNotIn("panel run is NOT a review",
                         self.classifier_prompt.read_text())

    def test_panel_note_needs_edits_in_the_cycle(self):
        # A panel turn that only ran the reviewers and reported back produced
        # no artifact. The note claims the turn holds NEW work; injected here it
        # would push a review-of-nothing toward NORMAL — the exact inverse of
        # the downgrade it exists to counter. Long prose + a Bash tool call
        # reach the classifier; the note must not ride along.
        records = [
            user("ask the panel about this flaky test"),
            a_tool("Skill", {"skill": "panel", "args": ""}),
            tool_result("Launching skill: panel"),
            a_tool("Bash", {"command": "bash ~/.claude/skills/panel/panel.sh "
                                       "--mode consult /tmp/ev.txt 'flaky test' > out.log"}),
            a_text("The panel's menu, reconciled: three first moves converge on "
                   "the fixture; reporting before touching anything. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NONE")
        self.assertEqual(decision["decision"], "approve")
        self.assertTrue(called)
        self.assertNotIn("panel run is NOT a review",
                         self.classifier_prompt.read_text())

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

    def test_backstop_recognises_the_reason_the_hook_actually_emits(self):
        # is_our_prompt matches the SHAPE of our reason ("run /assess ... before
        # finishing") rather than any mention of assess, so rewording block_normal or
        # block_turbo could silently break the backstop above — and the fixture there is
        # a hand-copied string that has already drifted from the real text, so it would
        # stay green while production looped. Feed the hook its OWN emitted reason to
        # couple the two by test instead of by memory.
        for level in ("NORMAL", "TURBO"):
            with self.subTest(level=level):
                first, _ = self.run_hook(WORK, level=level)
                self.assertEqual(first["decision"], "block")
                records = WORK + [
                    meta("Stop hook feedback:\n" + first["reason"]),
                    a_text("Reviewed inline, nothing critical. " + LONG_PROSE),
                ]
                second, called = self.run_hook(records, level=level)
                self.assertEqual(
                    second["decision"], "approve",
                    "the emitted block reason no longer matches is_our_prompt — the "
                    "prompted-cycle backstop is broken, which is the infinite-loop guard")
                self.assertFalse(called)

    # --- background work in flight ---

    BG_LAUNCH = [
        user("research the store APIs"),
        a_tool("Agent", {"description": "Apple research", "prompt": "..."}, id="toolu_apple"),
        tool_result("Async agent launched successfully. (This tool result is "
                    "internal metadata — never quote it.)", "toolu_apple"),
        a_tool("Agent", {"description": "Google research", "prompt": "..."}, id="toolu_google"),
        tool_result("Async agent launched successfully. (This tool result is "
                    "internal metadata — never quote it.)", "toolu_google"),
        a_text("Two research agents are running; I'll synthesize when they "
               "return. Meanwhile I confirmed the repo has no store-API "
               "integration. " + LONG_PROSE),
    ]

    def test_bg_launch_in_flight_defers_without_classifying(self):
        # The observed failure: Stop fires right after background agents
        # launch — nothing to review yet, and blocking burns the cycle's one
        # prompt so the eventual deliverable ships unreviewed.
        decision, called = self.run_hook(self.BG_LAUNCH, level="NORMAL")
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)

    def test_bg_partial_completion_still_defers(self):
        # The mid-flight turn USES TOOLS (reads the finished agent's output),
        # so if the notification wrongly advanced the cycle boundary, the
        # launches would leave the slice, pending would read False, and this
        # turn would reach the classifier and block. Deferral here pins BOTH
        # behaviors: notifications don't start a cycle, and one unanswered
        # launch is enough to defer.
        records = self.BG_LAUNCH + [
            task_notification("toolu_google"),
            a_tool("Read", {"file_path": "/tmp/tasks/google.output"}),
            tool_result("## Google Play API findings\n" + LONG_PROSE),
            a_text("Google results are in; still waiting on Apple. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)

    def test_bg_bash_without_edits_defers(self):
        # A background command with no shipped work yet: pure wait — defer.
        records = [
            user("run the long benchmark"),
            a_tool("Bash", {"command": "bench.sh", "run_in_background": True},
                   id="toolu_bench"),
            tool_result("Command running in background with ID: abc123. Output "
                        "is being written to: /tmp/tasks/abc123.output.", "toolu_bench"),
            a_text("Benchmark started; I'll analyze when it completes. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)

    def test_bg_bash_with_edits_still_classifies(self):
        # A dev-server-style command never exits by design; it must not
        # suppress review of a real module shipped in the same cycle.
        records = [
            user("start the dev server and build the widget"),
            a_tool("Bash", {"command": "npm run dev", "run_in_background": True},
                   id="toolu_server"),
            tool_result("Command running in background with ID: srv1. Output "
                        "is being written to: /tmp/tasks/srv1.output.", "toolu_server"),
            a_tool("Write", {"file_path": "/x/widget.py", "content": "class Widget: pass\n" * 60}),
            a_text("Server running; widget module written. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)

    def test_ack_text_from_foreground_tool_is_not_a_launch(self):
        # A foreground `cat` of a file whose first line IS an ack must not
        # read as in-flight work: the ack's id must belong to a tool that can
        # launch background tasks (Agent, or Bash with run_in_background).
        records = [
            user("show me that task output file"),
            a_tool("Bash", {"command": "cat /tmp/notes.txt"}, id="toolu_cat"),
            tool_result("Command running in background with ID: fake. (quoted "
                        "file content, not a real ack)", "toolu_cat"),
            a_tool("Edit", {"file_path": "/x/hook.py", "old_string": "a", "new_string": "b"}),
            a_text("Cleaned up the notes handling. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)

    def test_errored_workflow_launch_is_not_in_flight(self):
        # A Workflow whose call itself errors (bad script) starts nothing and
        # will never notify — it must not suppress review for the cycle.
        records = [
            user("run the audit workflow"),
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_wf_bad", "name": "Workflow",
                 "input": {"script": "syntax error"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_wf_bad",
                 "is_error": True, "content": "SyntaxError: unexpected token"}]}},
            a_tool("Write", {"file_path": "/x/audit.md", "content": "manual audit " * 100}),
            a_text("Workflow script was broken; did the audit inline instead. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)

    def test_bg_all_complete_classifies_the_synthesis(self):
        # Once every launch has a delivered notification, the synthesis turn
        # is the real deliverable and must reach the classifier. This also
        # pins that notification turns (plain user records) do NOT advance the
        # cycle boundary — if they did, the launches would leave the slice.
        records = self.BG_LAUNCH + [
            task_notification("toolu_google"),
            task_notification("toolu_apple", status="failed"),
            a_tool("Write", {"file_path": "/x/report.md", "content": "# Store APIs\n" * 80}),
            a_text("Full research report synthesized and written. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="TURBO")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)

    def test_quoted_launch_ack_is_not_a_launch(self):
        # grep/Read output that merely QUOTES an ack mid-content must not
        # read as in-flight work and suppress the review.
        records = [
            user("fix the hook"),
            tool_result('24 user :: TOOL_RESULT:Async agent launched successfully.\n'
                        '{"content": "Async agent launched successfully."}'),
            a_tool("Edit", {"file_path": "/x/hook.py", "old_string": "a", "new_string": "b"}),
            a_text("Adjusted the in-flight guard. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "block")
        self.assertTrue(called)

    def test_workflow_launch_defers_until_notified(self):
        records = [
            user("run the audit workflow"),
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_wf", "name": "Workflow",
                 "input": {"script": "..."}}]}},
            tool_result("Workflow started. runId: wf_abc123", "toolu_wf"),
            a_text("Audit workflow launched; results will land shortly. " + LONG_PROSE),
        ]
        decision, called = self.run_hook(records, level="NORMAL")
        self.assertEqual(decision["decision"], "approve")
        self.assertFalse(called)
        decision, called = self.run_hook(
            records + [task_notification("toolu_wf"),
                       a_tool("Write", {"file_path": "/x/audit.md", "content": "findings " * 100}),
                       a_text("Audit synthesized. " + LONG_PROSE)],
            level="NORMAL")
        self.assertEqual(decision["decision"], "block")

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
