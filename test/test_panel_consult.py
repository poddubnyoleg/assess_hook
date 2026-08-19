"""End-to-end tests for the consult mode in skills/assess/panel.sh.

Each test runs the real panel.sh as a subprocess against stub `codex`, `claude`
and `grok` binaries on a scratch PATH, so no tokens are spent and every
reviewer's exit status and output are controlled per test.

Consult is the third mode — mid-task, generative — and what needs pinning is
what makes it one:

  - the problem statement is REQUIRED, like scope's request: a consult about
    "(no task description given)" would return confident nonsense
  - the lenses are DISTINCT per reviewer (cards / shared assumption / lateral),
    while FIRST MOVE is the one slot all of them share — the design's only
    cross-lineage comparison, so losing either side of that silently would
    gut the mode
  - the footer reconciles as a MENU, not by survival (review) or union (scope),
    and says out loud that a consult reviews nothing
  - a run in which no foreign lineage delivered still refuses to present
    itself as a panel — the shared banner must fire in this mode too
  - telemetry records mode=consult, with no review-mode severity counts

Run:  python3 -m unittest discover -s test
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent / "skills" / "assess" / "panel.sh"

# Each stub copies the prompt it received to a per-reviewer file named by the
# test env, so lens assertions read what the panel actually sent — codex and
# claude take the prompt on stdin, grok via --prompt-file.
CODEX_STUB = """#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { out="$2"; shift; }; shift; done
cat >"${CODEX_PROMPT_COPY:-/dev/null}"
printf 'CODEX-STUB-REVIEW\\n' >"$out"
"""

CODEX_STUB_FAIL = """#!/usr/bin/env bash
cat >/dev/null
exit 1
"""

CLAUDE_STUB = """#!/usr/bin/env bash
cat >"${CLAUDE_PROMPT_COPY:-/dev/null}"
printf 'CLAUDE-STUB-REVIEW\\n'
"""

CLAUDE_STUB_FAIL = """#!/usr/bin/env bash
cat >/dev/null
exit 1
"""

GROK_STUB = """#!/usr/bin/env bash
pf=""
prev=""
for a in "$@"; do [ "$prev" = "--prompt-file" ] && pf="$a"; prev="$a"; done
[ -n "$pf" ] && cp "$pf" "${GROK_PROMPT_COPY:-/dev/null}"
printf 'GROK-STUB-REVIEW\\n'
"""


class PanelConsultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._stub("codex", CODEX_STUB)
        self._stub("claude", CLAUDE_STUB)
        self._stub("grok", GROK_STUB)
        self.artifact = Path(self.tmp) / "evidence.txt"
        self.artifact.write_text(
            "$ pytest test_x.py\nFAILED test_x.py::test_parse - IndexError\n"
            "=== attempt 1: guarded the index; same failure ===\n")
        self.grok_home = Path(self.tmp) / ".grok"
        self.grok_home.mkdir()
        (self.grok_home / "auth.json").write_text("{}")
        self.prompts = {n: Path(self.tmp) / f"{n}_prompt.txt"
                        for n in ("codex", "claude", "grok")}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)
        return p

    def run_panel(self, *args, env=None):
        e = dict(os.environ)
        e["PATH"] = f"{self.bin}:/usr/bin:/bin"
        e["HOME"] = self.tmp
        for var in ("ASSESS_PANEL_GROK", "ASSESS_PANEL_PI", "GROK_HOME",
                    "XAI_API_KEY", "ASSESS_PANEL_LOG", "ASSESS_PANEL_MODE",
                    "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            e.pop(var, None)
        e["CODEX_PROMPT_COPY"] = str(self.prompts["codex"])
        e["CLAUDE_PROMPT_COPY"] = str(self.prompts["claude"])
        e["GROK_PROMPT_COPY"] = str(self.prompts["grok"])
        e.update(env or {})
        cmd = ["bash", str(PANEL), "--mode", "consult", str(self.artifact), *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=e,
                              cwd=self.tmp)

    # --- the problem statement is the subject, so it is required -----------

    def test_missing_problem_statement_is_refused(self):
        r = self.run_panel()
        self.assertEqual(r.returncode, 2)
        self.assertIn("consult mode needs the problem statement", r.stderr)

    def test_unknown_mode_error_names_consult(self):
        cmd = ["bash", str(PANEL), "--mode", "advise", str(self.artifact), "task"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, HOME=self.tmp), cwd=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("'consult'", r.stderr)

    # --- distinct lenses, one shared slot ----------------------------------

    def test_lenses_are_distinct_and_all_share_first_move(self):
        r = self.run_panel("the parse test fails with IndexError after 3 attempted fixes")
        self.assertEqual(r.returncode, 0, r.stderr)
        texts = {n: p.read_text() for n, p in self.prompts.items()}
        # Every reviewer gets the verbatim problem and the evidence, framed as data —
        # and the domain guard: consult serves data analysis, research and ideation
        # as much as code, so no lens may assume the problem is software.
        for n, t in texts.items():
            self.assertIn("# PROBLEM (verbatim)", t, n)
            self.assertIn("after 3 attempted fixes", t, n)
            self.assertIn("IndexError", t, n)
            self.assertIn("FIRST MOVE", t, n)
            self.assertIn("do NOT assume it is software", t, n)
        # The shared-assumption lens carries its open-problem fallback: with no
        # attempts in evidence (ideation), it attacks the FRAMING instead of
        # demanding attempts that don't exist.
        self.assertIn("study the FRAMING instead", texts["claude"])
        # Each lens is its own: a marker present in exactly one prompt.
        self.assertIn("EXPERIMENT CARDS", texts["codex"])
        self.assertNotIn("EXPERIMENT CARDS", texts["claude"])
        self.assertNotIn("EXPERIMENT CARDS", texts["grok"])
        self.assertIn("SHARED ASSUMPTION", texts["claude"])
        self.assertNotIn("SHARED ASSUMPTION", texts["codex"])
        self.assertNotIn("SHARED ASSUMPTION", texts["grok"])
        self.assertIn("LATERAL MOVES", texts["grok"])
        self.assertNotIn("LATERAL MOVES", texts["codex"])
        self.assertNotIn("LATERAL MOVES", texts["claude"])
        # And none of them is the review or scope prompt by accident.
        for n, t in texts.items():
            self.assertNotIn("skeptical senior reviewer", t, n)
            self.assertNotIn("ASSUMPTIONS — every point the request left open", t, n)

    def test_empty_evidence_is_accepted_with_an_explicit_note(self):
        self.artifact.write_text("")
        r = self.run_panel("stuck on the flaky login test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(no evidence supplied", self.prompts["codex"].read_text())

    # --- menu reconciliation, not survival or union -------------------------

    def test_footer_reconciles_as_a_menu(self):
        r = self.run_panel("the parse test fails")
        self.assertIn("Reconcile as a MENU", r.stdout)
        self.assertIn("FIRST MOVE", r.stdout)
        self.assertIn("reviewed NOTHING", r.stdout)
        # Not the sibling modes' footers.
        self.assertNotIn("Reconcile by UNION", r.stdout)
        self.assertNotIn("Reconcile by axis", r.stdout)
        # Three reviewers answered, so the comparison wording — not the one-voice one.
        self.assertIn("compare those first", r.stdout)
        self.assertNotIn("single outside opinion", r.stdout)

    def test_single_voice_menu_says_there_is_nothing_to_compare(self):
        self._stub("codex", CODEX_STUB_FAIL)
        r = self.run_panel("the parse test fails", env={"ASSESS_PANEL_GROK": "0"})
        # Only the fresh Claude delivered: no cross-lineage voice at all, so the
        # shared no-decorrelation banner outranks the menu footer entirely.
        self.assertIn("NO CROSS-LINEAGE REVIEW HAPPENED", r.stdout)
        self.assertNotIn("Reconcile as a MENU", r.stdout)

    def test_dead_reviewer_is_loud_and_the_menu_still_prints(self):
        self._stub("codex", CODEX_STUB_FAIL)
        r = self.run_panel("the parse test fails")
        self.assertIn(">>> FAILED", r.stdout)
        self.assertIn("GROK-STUB-REVIEW", r.stdout)
        self.assertIn("Reconcile as a MENU", r.stdout)
        self.assertIn("NARROWER", r.stdout)

    def test_shared_assumption_advice_needs_the_claude_slot_delivered(self):
        # The SHARED ASSUMPTION heading belongs to the fresh Claude alone. With
        # Claude dead the menu still prints (codex+grok are foreign voices), but
        # ranking advice about a section no reviewer produced would send the
        # caller hunting for it — or read its absence as "no shared assumption
        # found", the dead-reviewer-as-agreement failure.
        self._stub("claude", CLAUDE_STUB_FAIL)
        r = self.run_panel("the parse test fails")
        self.assertIn("Reconcile as a MENU", r.stdout)
        self.assertNotIn("SHARED ASSUMPTION finding outranks", r.stdout)
        # And with all reviewers alive it does print (pinned against the
        # healthy-path test above going stale on this line).
        self._stub("claude", CLAUDE_STUB)
        r = self.run_panel("the parse test fails")
        self.assertIn("SHARED ASSUMPTION finding outranks", r.stdout)

    # --- telemetry ----------------------------------------------------------

    def test_telemetry_records_consult_mode_without_severity_counts(self):
        log = Path(self.tmp) / "log.jsonl"
        r = self.run_panel("the parse test fails", env={"ASSESS_PANEL_LOG": str(log)})
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(log.read_text().splitlines()[-1])
        self.assertEqual(rec["mode"], "consult")
        self.assertEqual(rec["reviewers"]["codex"]["status"], "ok")
        self.assertEqual(rec["reviewers"]["claude"]["status"], "ok")
        self.assertEqual(rec["reviewers"]["grok"]["status"], "ok")
        # Severity tallies are review-mode-only: consult output carries no tags,
        # and zeros here would read as "reviewed and found nothing".
        self.assertNotIn("high", rec["reviewers"]["codex"])
        self.assertIn("Panel run id:", r.stdout)


if __name__ == "__main__":
    unittest.main()
