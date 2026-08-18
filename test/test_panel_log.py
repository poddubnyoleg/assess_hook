"""Tests for the panel's telemetry: the run records panel.sh appends and the
verdict/report loop in panel_stats.py.

The telemetry exists to answer "which reviewer earns its slot", so the failures
that matter are the ones that would quietly corrupt that answer or make the
panel pay for its own bookkeeping:

  - a run record that misstates an outcome (a timeout logged as ok) poisons the
    reliability numbers forever
  - a log line carrying the artifact, the task, or a provider key turns
    telemetry into an exposure
  - a logging failure that takes the panel down with it makes the measurement
    cost more than the thing measured
  - verdicts accepted with a typo'd key ("source" for "sources") silently drop
    the one signal the whole loop exists to collect

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
STATS = Path(__file__).resolve().parent.parent / "skills" / "assess" / "panel_stats.py"

# Reviews with severity tags, so the volume counters have something to count.
CODEX_STUB = """#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { out="$2"; shift; }; shift; done
cat >/dev/null
printf '[HIGH] codex finding one\\n[MED] codex finding two\\n[MED] codex finding three\\n' >"$out"
"""

CLAUDE_STUB = """#!/usr/bin/env bash
cat >/dev/null
printf '[LOW] claude finding\\n'
"""

# grok takes its prompt via --prompt-file, not stdin — a stub that read stdin here
# would block on the suite's inherited descriptor.
GROK_STUB = """#!/usr/bin/env bash
printf '[HIGH] grok finding\\n[LOW] grok nit\\n'
"""

PI_STUB = """#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "--list-models" ]; then printf 'openrouter  %s  1.0M\\n' "${!#}" >&2; exit 0; fi
done
cat >/dev/null
printf '[HIGH] pi finding\\n'
"""


class PanelLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._stub("codex", CODEX_STUB)
        self._stub("claude", CLAUDE_STUB)
        self._stub("grok", GROK_STUB)
        self.artifact = Path(self.tmp) / "artifact.txt"
        self.artifact.write_text("diff --git a/x.py b/x.py\n+print('hi')\n")
        self.log = Path(self.tmp) / "log.jsonl"
        self.agent_dir = Path(self.tmp) / "pi-agent-src"
        self.agent_dir.mkdir()
        # grok is default-ON and preflights a credential; HOME points at self.tmp, so
        # this auth.json is what lets the stubbed grok pass that gate.
        grok_home = Path(self.tmp) / ".grok"
        grok_home.mkdir()
        (grok_home / "auth.json").write_text("{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)

    def run_panel(self, *args, env=None, mode="review"):
        e = dict(os.environ)
        e["PATH"] = f"{self.bin}:/usr/bin:/bin"
        e["HOME"] = self.tmp
        e["PI_CODING_AGENT_DIR"] = str(self.agent_dir)
        e["ASSESS_PANEL_LOG"] = str(self.log)
        e.pop("ASSESS_PANEL_PI", None)
        e.pop("OPENROUTER_API_KEY", None)
        e.pop("ASSESS_PANEL_GROK", None)
        e.pop("XAI_API_KEY", None)
        e.pop("GROK_HOME", None)
        for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            e.pop(var, None)
        e.update(env or {})
        cmd = ["bash", str(PANEL), "--mode", mode, str(self.artifact), *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=e,
                              cwd=self.tmp)

    def records(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def run_stats(self, *args, stdin=None, env=None):
        e = dict(os.environ)
        e["ASSESS_PANEL_LOG"] = str(self.log)
        e.update(env or {})
        return subprocess.run(["python3", str(STATS), *args], input=stdin,
                              capture_output=True, text=True, timeout=60, env=e)

    # --- run records ------------------------------------------------------

    def test_run_record_matches_what_actually_happened(self):
        r = self.run_panel("some task")
        recs = self.records()
        self.assertEqual(1, len(recs))
        rec = recs[0]
        self.assertEqual("run", rec["t"])
        self.assertEqual("review", rec["mode"])
        self.assertGreater(rec["artifact_bytes"], 0)
        codex = rec["reviewers"]["codex"]
        self.assertEqual("ok", codex["status"])
        # The volume counters must count what the reviewer tagged, not lines or items.
        self.assertEqual((1, 2, 0), (codex["high"], codex["med"], codex["low"]))
        self.assertIn("dur_s", codex)
        claude = rec["reviewers"]["claude"]
        self.assertEqual("ok", claude["status"])
        self.assertEqual((0, 0, 1), (claude["high"], claude["med"], claude["low"]))
        # grok is default-ON, so it must be in the record without any env asked for it.
        grok = rec["reviewers"]["grok"]
        self.assertEqual("ok", grok["status"])
        self.assertEqual((1, 0, 1), (grok["high"], grok["med"], grok["low"]))
        # pi off => absent from the record entirely: absence IS the off state.
        self.assertNotIn("pi", rec["reviewers"])
        # The footer names the id so the caller can attach verdicts to this run.
        self.assertIn(f"Panel run id: {rec['id']}", r.stdout)
        self.assertIn("verdict", r.stdout)

    def test_failed_and_skipped_are_recorded_as_such(self):
        # A timeout logged as ok — or a skip logged as a delivery — poisons the
        # reliability numbers the whole record exists for.
        self._stub("codex", "#!/usr/bin/env bash\ncat >/dev/null\necho boom >&2\nexit 1\n")
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1"})  # pi: not on PATH
        rec = self.records()[0]
        self.assertEqual("failed", rec["reviewers"]["codex"]["status"])
        pi = rec["reviewers"]["pi"]
        self.assertEqual("skipped", pi["status"])
        self.assertIn("not on PATH", pi["skip"])
        self.assertEqual("ok", rec["reviewers"]["claude"]["status"])

    def test_timeout_is_recorded_with_the_budget_as_duration(self):
        self._stub("pi", PI_STUB.replace("cat >/dev/null",
                                         "cat >/dev/null; sleep 30"))
        self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                         "OPENROUTER_API_KEY": "k",
                                         "ASSESS_PANEL_TIMEOUT": "2"})
        pi = self.records()[0]["reviewers"]["pi"]
        self.assertEqual("timeout", pi["status"])
        self.assertEqual(2, pi["dur_s"])

    def test_grok_off_is_absent_from_the_record(self):
        # Explicit off is the one grok state that is rightly silent — same rule as pi:
        # absence IS the off state, a skipped entry would imply it had been asked for.
        self.run_panel("some task", env={"ASSESS_PANEL_GROK": "0"})
        self.assertNotIn("grok", self.records()[0]["reviewers"])

    def test_grok_missing_binary_is_recorded_skipped(self):
        # Default-on means a machine without the CLI logs a skip on every run — that is
        # the honest price of a default, and the record must carry the reason.
        (self.bin / "grok").unlink()
        self.run_panel("some task")
        grok = self.records()[0]["reviewers"]["grok"]
        self.assertEqual("skipped", grok["status"])
        self.assertIn("not on PATH", grok["skip"])

    def test_empty_output_is_not_ok(self):
        self._stub("claude", "#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n")
        self.run_panel("some task")
        self.assertEqual("empty", self.records()[0]["reviewers"]["claude"]["status"])

    def test_scope_mode_is_logged_without_severity_counts(self):
        self.run_panel("some request", mode="scope")
        rec = self.records()[0]
        self.assertEqual("scope", rec["mode"])
        # Scope output has no tag contract, so counting tags would manufacture data.
        self.assertNotIn("high", rec["reviewers"]["codex"])

    def test_footer_pins_the_log_path_into_the_verdict_command(self):
        # A later verdict call runs in a fresh shell where ASSESS_PANEL_LOG may be
        # unset; against the default log the id would be missing — with an error
        # listing ids from the WRONG log, inviting misattachment.
        r = self.run_panel("some task")
        self.assertIn(f'--log "{self.log}" verdict', r.stdout)

    def test_overlong_skip_reason_is_capped_in_the_record(self):
        # The skip reason is the record's only field carrying text this script did not
        # author end-to-end; a misbehaving CLI must not persist unbounded output.
        self._stub("pi", PI_STUB)
        longpath = str(Path(self.tmp) / ("x" * 400))
        self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                         "ASSESS_PI_KEY_FILE": longpath})
        pi = self.records()[0]["reviewers"]["pi"]
        self.assertEqual("skipped", pi["status"])
        self.assertLessEqual(len(pi["skip"]), 300)

    def test_default_log_path_lives_under_home(self):
        e = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin", HOME=self.tmp)
        for var in ("ASSESS_PANEL_LOG", "HTTPS_PROXY", "https_proxy",
                    "ALL_PROXY", "all_proxy", "ASSESS_PANEL_PI",
                    "ASSESS_PANEL_GROK", "GROK_HOME", "XAI_API_KEY"):
            e.pop(var, None)
        subprocess.run(["bash", str(PANEL), str(self.artifact), "task"],
                       capture_output=True, text=True, timeout=120, env=e, cwd=self.tmp)
        default = Path(self.tmp) / ".claude" / "assess-panel" / "log.jsonl"
        self.assertTrue(default.exists())

    def test_off_value_disables_logging_and_the_footer(self):
        r = self.run_panel("some task", env={"ASSESS_PANEL_LOG": "off"})
        self.assertFalse(self.log.exists())
        self.assertNotIn("Panel run id", r.stdout)
        self.assertIn("END PANEL", r.stdout)

    def test_unwritable_log_never_costs_the_panel(self):
        # The parent "directory" is a regular file, so the append cannot succeed.
        blocker = Path(self.tmp) / "blocker"
        blocker.write_text("")
        r = self.run_panel("some task",
                           env={"ASSESS_PANEL_LOG": str(blocker / "log.jsonl")})
        self.assertEqual(0, r.returncode)
        self.assertIn("END PANEL", r.stdout)
        self.assertIn("could not append the run record", r.stderr)
        # No id in the footer: an id pointing at a record that does not exist would
        # send verdicts into a log that never saw the run.
        self.assertNotIn("Panel run id", r.stdout)

    def test_no_secret_and_no_free_text_reaches_the_log(self):
        self._stub("pi", PI_STUB)
        self.run_panel("task text with a $secret inside",
                       env={"ASSESS_PANEL_PI": "1",
                            "OPENROUTER_API_KEY": "sk-or-verysecret",
                            "XAI_API_KEY": "xai-alsoverysecret"})
        raw = self.log.read_text()
        self.assertNotIn("sk-or-verysecret", raw)
        self.assertNotIn("xai-alsoverysecret", raw)
        self.assertNotIn("task text with", raw)          # task never logged
        self.assertNotIn("print('hi')", raw)             # artifact never logged

    # --- verdicts ---------------------------------------------------------

    def _seed_run(self, run_id="20260730-100000.11", mode="review", reviewers=None):
        rec = {"t": "run", "id": run_id, "ts": 1785000000, "mode": mode,
               "wall_s": 100, "reviewers": reviewers or {
                   "codex": {"status": "ok", "dur_s": 90, "high": 1, "med": 0, "low": 0},
                   "claude": {"status": "ok", "dur_s": 80, "high": 0, "med": 1, "low": 0}}}
        with open(self.log, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return run_id

    def test_verdict_appends_and_report_reads_it(self):
        rid = self._seed_run()
        payload = json.dumps({"findings": [
            {"summary": "real bug", "sources": ["codex"], "severity": "HIGH",
             "verdict": "confirmed", "action": "fixed"},
            {"summary": "false alarm", "sources": ["codex"], "verdict": "refuted"},
            {"summary": "both saw it", "sources": ["claude", "self"],
             "verdict": "confirmed", "action": "fixed"},
        ]})
        r = self.run_stats("verdict", rid, stdin=payload)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("logged 3 verdict(s)", r.stdout)
        rep = self.run_stats("report").stdout
        # codex: 1 confirmed / 1 refuted -> 50%, and its confirmed one is unique
        # AND beyond self; claude: confirmed but shared with self -> not beyond.
        codex_row = next(l for l in rep.splitlines()
                         if l.startswith("codex") and "%" in l)
        self.assertIn("50%", codex_row)
        claude_row = next(l for l in rep.splitlines()
                          if l.startswith("claude") and "%" in l)
        self.assertIn("100%", claude_row)
        self.assertIn("1 of 1 verdict-logged review run(s)", rep)
        # Self coverage sits next to the headline it qualifies; the third finding
        # carries `self`, so 1 of 1.
        self.assertIn("Self coverage: 1 of 1", rep)

    def test_verdict_refuses_an_unknown_run_id(self):
        self._seed_run()
        r = self.run_stats("verdict", "no-such-run",
                           stdin='{"findings": []}')
        self.assertNotEqual(0, r.returncode)
        self.assertIn("not in", r.stderr)
        self.assertIn("20260730-100000.11", r.stderr)   # names what DOES exist

    def test_verdict_refuses_typoed_keys_loudly(self):
        # "source" for "sources" accepted silently would drop the one signal the
        # loop exists to collect, while looking logged.
        rid = self._seed_run()
        r = self.run_stats("verdict", rid, stdin=json.dumps({"findings": [
            {"summary": "x", "source": ["codex"], "verdict": "confirmed"}]}))
        self.assertNotEqual(0, r.returncode)
        self.assertIn("unknown key", r.stderr)
        # Nothing was appended.
        self.assertEqual(1, len(self.records()))

    def test_verdict_refuses_invalid_json(self):
        rid = self._seed_run()
        r = self.run_stats("verdict", rid, stdin="not json")
        self.assertNotEqual(0, r.returncode)
        self.assertIn("not valid JSON", r.stderr)

    def test_empty_findings_are_valid_signal(self):
        rid = self._seed_run()
        r = self.run_stats("verdict", rid, stdin='{"findings": []}')
        self.assertEqual(0, r.returncode, r.stderr)
        rep = self.run_stats("report").stdout
        self.assertIn("1 of 1 run(s) have verdicts", rep)

    def test_sources_must_have_delivered_in_that_run(self):
        # The seeded run has codex+claude ok and no pi at all; crediting pi would let
        # a misremembered attribution poison every relevance metric downstream.
        rid = self._seed_run()
        r = self.run_stats("verdict", rid, stdin=json.dumps({"findings": [
            {"summary": "x", "sources": ["pi"], "verdict": "confirmed"}]}))
        self.assertNotEqual(0, r.returncode)
        self.assertIn("cannot be credited", r.stderr)
        self.assertIn("'self'", r.stderr)
        self.assertEqual(1, len(self.records()))   # nothing appended

    def test_partial_output_from_a_dead_reviewer_is_creditable(self):
        # panel.sh prints a timed-out reviewer's partial output and the skill calls it
        # worth reconciling; refusing the credit would force the caller to drop the
        # attribution or mislabel it "self" — corrupting the metric either way.
        rid = self._seed_run(reviewers={
            "codex": {"status": "timeout", "dur_s": 540, "out_bytes": 800},
            "claude": {"status": "ok", "dur_s": 80}})
        r = self.run_stats("verdict", rid, stdin=json.dumps({"findings": [
            {"summary": "caught before the watchdog", "sources": ["codex"],
             "verdict": "confirmed"}]}))
        self.assertEqual(0, r.returncode, r.stderr)
        # ...and the run counts in codex's beyond/run denominator: 1 catch over the
        # 1 run it (partially) delivered in.
        rep = self.run_stats("report").stdout
        codex_row = next(l for l in rep.splitlines()
                         if l.startswith("codex") and "%" in l)
        self.assertIn("1.00", codex_row)

    def test_a_timeout_with_no_output_stays_uncreditable(self):
        rid = self._seed_run(reviewers={
            "codex": {"status": "timeout", "dur_s": 540},
            "claude": {"status": "ok", "dur_s": 80}})
        r = self.run_stats("verdict", rid, stdin=json.dumps({"findings": [
            {"summary": "x", "sources": ["codex"], "verdict": "confirmed"}]}))
        self.assertNotEqual(0, r.returncode)
        self.assertIn("cannot be credited", r.stderr)

    def test_empty_reviews_count_in_the_denominator_not_as_sources(self):
        # An empty review is a delivered judgement ("no findings") — an opportunity
        # that yielded nothing. Excluding it would overstate the reviewer's rate;
        # crediting it as a source would invent findings it explicitly did not raise.
        rid1 = self._seed_run("20260730-100000.41")
        rid2 = self._seed_run("20260730-100100.42", reviewers={
            "codex": {"status": "empty", "dur_s": 60},
            "claude": {"status": "ok", "dur_s": 70}})
        r = self.run_stats("verdict", rid2, stdin=json.dumps({"findings": [
            {"summary": "x", "sources": ["codex"], "verdict": "confirmed"}]}))
        self.assertNotEqual(0, r.returncode)   # empty codex is not a source
        self.run_stats("verdict", rid1, stdin=json.dumps({"findings": [
            {"summary": "y", "sources": ["codex"], "verdict": "confirmed"}]}))
        self.run_stats("verdict", rid2, stdin=json.dumps({"findings": []}))
        rep = self.run_stats("report").stdout
        codex_row = next(l for l in rep.splitlines()
                         if l.startswith("codex") and "%" in l)
        # 1 beyond-self catch over TWO delivered runs (ok + empty) -> 0.50.
        self.assertIn("0.50", codex_row)

    def test_self_clean_marks_coverage_without_inventing_findings(self):
        # A clean self-review and a forgotten one must be distinguishable, or the
        # beyond-self headline silently inflates on exactly the compliant runs.
        rid = self._seed_run()
        r = self.run_stats("verdict", rid, stdin=json.dumps(
            {"findings": [{"summary": "panel catch", "sources": ["codex"],
                           "verdict": "confirmed"}],
             "self_clean": True}))
        self.assertEqual(0, r.returncode, r.stderr)
        rep = self.run_stats("report").stdout
        self.assertIn("Self coverage: 1 of 1", rep)

    def test_overlong_summary_is_refused_not_truncated(self):
        # The verdict half must hold the same free-text discipline the run half was
        # engineered for: a 300+ char summary is quoting the artifact into the log.
        rid = self._seed_run()
        r = self.run_stats("verdict", rid, stdin=json.dumps({"findings": [
            {"summary": "x" * 301, "sources": ["codex"], "verdict": "confirmed"}]}))
        self.assertNotEqual(0, r.returncode)
        self.assertIn("300", r.stderr)
        self.assertEqual(1, len(self.records()))

    def test_report_survives_a_malformed_verdicts_record(self):
        # Valid JSON with the wrong shape (hand-edit, schema drift) must cost one
        # record, never the whole report.
        rid = self._seed_run()
        with open(self.log, "a") as f:
            f.write(json.dumps({"t": "verdicts", "id": rid}) + "\n")   # no findings
        r = self.run_stats("report")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("Reliability", r.stdout)

    def test_relevance_is_split_by_mode_not_pooled(self):
        # Review's `confirmed` means "real"; scope's means "changed the reading", and
        # scope lenses make every finding single-source by design. One pooled table
        # would rank reviewers on incommensurable signals.
        rid_r = self._seed_run("20260730-100000.21", mode="review")
        rid_s = self._seed_run("20260730-100100.22", mode="scope")
        f = {"summary": "x", "sources": ["codex"], "verdict": "confirmed"}
        self.run_stats("verdict", rid_r, stdin=json.dumps({"findings": [f]}))
        self.run_stats("verdict", rid_s, stdin=json.dumps({"findings": [f]}))
        rep = self.run_stats("report").stdout
        self.assertIn("Relevance — review", rep)
        self.assertIn("Relevance — scope", rep)
        scope_section = rep.split("Relevance — scope")[1]
        self.assertNotIn("beyond-self", scope_section)

    def test_beyond_self_is_also_a_rate_over_delivered_runs(self):
        # Two verdict-logged runs; codex delivered in only one of them. The raw count
        # alone would punish the reviewer for the run it never got to see.
        rid1 = self._seed_run("20260730-100000.31")
        rid2 = self._seed_run("20260730-100100.32", reviewers={
            "codex": {"status": "timeout", "dur_s": 540},
            "claude": {"status": "ok", "dur_s": 70}})
        self.run_stats("verdict", rid1, stdin=json.dumps({"findings": [
            {"summary": "x", "sources": ["codex"], "verdict": "confirmed"}]}))
        self.run_stats("verdict", rid2, stdin=json.dumps({"findings": []}))
        rep = self.run_stats("report").stdout
        codex_row = next(l for l in rep.splitlines()
                         if l.startswith("codex") and "%" in l)
        # runs=1 (delivered once), beyond=1 -> rate 1.00, not 0.50.
        self.assertIn("1.00", codex_row)

    def test_relogging_a_run_replaces_not_doubles_and_says_so(self):
        rid = self._seed_run()
        one = {"summary": "x", "sources": ["codex"], "verdict": "confirmed"}
        first = self.run_stats("verdict", rid, stdin=json.dumps({"findings": [one]}))
        # First call: nothing to replace, no note.
        self.assertNotIn("REPLACES", first.stdout)
        second = self.run_stats("verdict", rid, stdin=json.dumps({"findings": [one]}))
        # Last-wins is the correction path, but silent last-wins turns a caller who
        # split one reconciliation across two calls into a data-loss bug.
        self.assertIn("REPLACES 1 verdict(s)", second.stdout)
        rep = self.run_stats("report").stdout
        codex_row = next(l for l in rep.splitlines()
                         if l.startswith("codex") and "%" in l)
        # judged (column 3: source, runs, judged) must be 1, not 2.
        self.assertEqual(1, int(codex_row.split()[2]))

    def test_report_reliability_counts_statuses(self):
        self._seed_run("20260730-100000.11")
        self._seed_run("20260730-100100.12", reviewers={
            "codex": {"status": "timeout", "dur_s": 540},
            "claude": {"status": "ok", "dur_s": 70},
            "pi": {"status": "skipped", "skip": "`pi` is not on PATH. etc"}})
        rep = self.run_stats("report").stdout
        lines = rep.splitlines()
        codex_row = next(l for l in lines if l.startswith("codex"))
        # asked 2, ok 1, timeout 1
        self.assertRegex(codex_row, r"codex\s+2\s+1\s+0\s+0\s+1")
        self.assertIn("skip reasons:", rep)
        self.assertIn("not on PATH", rep)

    def test_schema_is_printable_without_a_log(self):
        r = self.run_stats("verdict", "--schema",
                           env={"ASSESS_PANEL_LOG": "/nonexistent/x.jsonl"})
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn('"sources"', r.stdout)


if __name__ == "__main__":
    unittest.main()
