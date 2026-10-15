"""End-to-end tests for the consult mode in skills/assess/panel.sh.

Each test runs the real panel.sh as a subprocess against stub `codex`, `claude`
and `grok` binaries on a scratch PATH, so no tokens are spent and every
reviewer's exit status and output are controlled per test.

Consult is the third mode — mid-task, generative — and what needs pinning is
what makes it one:

  - the problem statement is REQUIRED, like scope's request: a consult about
    "(no task description given)" would return confident nonsense
  - every reviewer gets the SAME prompt (the decorrelation is the lineages' job,
    not the question's), carrying the output contract — WHAT / WHY HERE / CHECK
    per proposal — and FIRST MOVE last, the cheapest cross-lineage comparison
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
# test env, so prompt assertions read what the panel actually sent — codex and
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

AGY_STUB = """#!/usr/bin/env bash
cat >"${AGY_PROMPT_COPY:-/dev/null}"
printf 'AGY-STUB-REVIEW\\n'
"""


class PanelConsultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._stub("codex", CODEX_STUB)
        self._stub("claude", CLAUDE_STUB)
        self._stub("grok", GROK_STUB)
        self._stub("agy", AGY_STUB)
        self.artifact = Path(self.tmp) / "evidence.txt"
        self.artifact.write_text(
            "$ pytest test_x.py\nFAILED test_x.py::test_parse - IndexError\n"
            "=== attempt 1: guarded the index; same failure ===\n")
        self.grok_home = Path(self.tmp) / ".grok"
        self.grok_home.mkdir()
        (self.grok_home / "auth.json").write_text("{}")
        self.agy_home = Path(self.tmp) / ".gemini"
        self.agy_home.mkdir()
        (self.agy_home / "jetski-standalone-oauth-token").write_text("{}")
        self.prompts = {n: Path(self.tmp) / f"{n}_prompt.txt"
                        for n in ("codex", "claude", "grok", "agy")}

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
        for var in ("ASSESS_PANEL_GROK", "ASSESS_PANEL_AGY", "ASSESS_PANEL_PI",
                    "GROK_HOME", "AGY_HOME", "XAI_API_KEY", "GEMINI_API_KEY",
                    "GOOGLE_API_KEY", "JETSKI_OAUTH_TOKEN", "AGY_LLM_GATEWAY_API_KEY",
                    "ASSESS_PANEL_LOG", "ASSESS_PANEL_MODE",
                    "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            e.pop(var, None)
        e["CODEX_PROMPT_COPY"] = str(self.prompts["codex"])
        e["CLAUDE_PROMPT_COPY"] = str(self.prompts["claude"])
        e["GROK_PROMPT_COPY"] = str(self.prompts["grok"])
        e["AGY_PROMPT_COPY"] = str(self.prompts["agy"])
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

    # --- one prompt for every seat, FIRST MOVE last ----------------------------

    def test_every_reviewer_gets_the_same_prompt_with_first_move_last(self):
        r = self.run_panel("the parse test fails with IndexError after 3 attempted fixes")
        self.assertEqual(r.returncode, 0, r.stderr)
        texts = {n: p.read_text() for n, p in self.prompts.items()}
        # Identical, byte for byte: a seat asked a different question is a lens, and a
        # lens confounds the seat with its question and dies with its seat.
        self.assertEqual(1, len(set(texts.values())), sorted(texts))
        t = texts["codex"]
        # The verbatim problem and the evidence, framed as data — and the domain guard:
        # consult serves data analysis, research and ideation as much as code.
        self.assertIn("# PROBLEM (verbatim)", t)
        self.assertIn("after 3 attempted fixes", t)
        self.assertIn("IndexError", t)
        self.assertIn("do NOT assume it is software", t)
        # The contract: grounded, testable proposals, and the shared slot LAST.
        for heading in ("WHAT —", "WHY HERE —", "CHECK —", "FIRST MOVE"):
            self.assertIn(heading, t)
        self.assertGreater(t.index("FIRST MOVE"), t.index("CHECK —"))
        # None of the withdrawn lenses, and not the review or scope prompt by accident.
        for gone in ("EXPERIMENT CARDS", "SHARED ASSUMPTION", "LATERAL MOVES",
                     "BOUNDARY & INVERSION PROBES", "MUNDANE", "skeptical senior reviewer",
                     "ASSUMPTIONS — every point the request left open"):
            self.assertNotIn(gone, t)

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
        # Four reviewers answered, so the comparison wording — not the one-voice one.
        self.assertIn("compare those first", r.stdout)
        self.assertNotIn("single outside opinion", r.stdout)

    def test_single_voice_menu_says_there_is_nothing_to_compare(self):
        self._stub("codex", CODEX_STUB_FAIL)
        r = self.run_panel("the parse test fails",
                           env={"ASSESS_PANEL_GROK": "0", "ASSESS_PANEL_AGY": "0"})
        # Only the fresh Claude delivered: no cross-lineage voice at all, so the
        # shared no-decorrelation banner outranks the menu footer entirely.
        self.assertIn("NO CROSS-LINEAGE REVIEW HAPPENED", r.stdout)
        self.assertNotIn("Reconcile as a MENU", r.stdout)

    def test_dead_reviewer_is_loud_and_the_menu_still_prints(self):
        self._stub("codex", CODEX_STUB_FAIL)
        r = self.run_panel("the parse test fails")
        self.assertIn(">>> FAILED", r.stdout)
        self.assertIn("GROK-STUB-REVIEW", r.stdout)
        self.assertIn("AGY-STUB-REVIEW", r.stdout)
        self.assertIn("Reconcile as a MENU", r.stdout)
        self.assertIn("NARROWER", r.stdout)

    def test_menu_advice_names_no_slot_a_single_seat_owns(self):
        # With one prompt for every seat there is no heading only one reviewer produces,
        # so the footer ranks by convergence, not by a named section — and a dead fresh
        # Claude changes none of it.
        r = self.run_panel("the parse test fails")
        self.assertNotIn("SHARED ASSUMPTION", r.stdout)
        self.assertIn("two lineages propose independently", r.stdout)
        self._stub("claude", CLAUDE_STUB_FAIL)
        r = self.run_panel("the parse test fails")
        self.assertIn("Reconcile as a MENU", r.stdout)
        self.assertIn("two lineages propose independently", r.stdout)

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
        self.assertEqual(rec["reviewers"]["agy"]["status"], "ok")
        # Severity tallies are review-mode-only: consult output carries no tags,
        # and zeros here would read as "reviewed and found nothing".
        self.assertNotIn("high", rec["reviewers"]["codex"])
        self.assertIn("Panel run id:", r.stdout)


if __name__ == "__main__":
    unittest.main()
