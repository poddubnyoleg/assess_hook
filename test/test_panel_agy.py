"""End-to-end tests for the Antigravity (`agy`) reviewer in skills/assess/panel.sh.

Each test runs the real panel.sh as a subprocess against stub `codex`, `claude`,
`grok`, `agy` and `security` binaries on a scratch PATH, so no tokens are spent and
every reviewer's exit status and output are controlled per test.

Antigravity is ON by default — a fourth lineage (Google Gemini) alongside Codex,
Claude, and Grok — so the failure modes that matter mirror Grok's:

  - on by default, and silent ONLY when explicitly turned off (ASSESS_PANEL_AGY=0);
    an unrecognised flag value (ASSESS_PANEL_AGY=TRUE) is a loud skip, not a silent
    drop and not a silent run
  - a machine without `agy` or without a sign-in (no keychain item, no
    `~/.gemini/jetski-standalone-oauth-token`, no GEMINI_API_KEY) gets a stated
    SKIPPED with the fix and the opt-out var, never a three-section panel that looks
    complete
  - the reviewer is launched in plan mode with `--sandbox` and WITHOUT
    `--disable-slash-commands` (which would switch plan mode off), hermetic (throwaway
    HOME with the token file and ~/Library/Keychains symlinked in, never the user's
    `~/.gemini`), and with the one-line settings file that lets sandboxed shell
    commands run headless
  - a claude-*/gpt-* model id is refused: the seat exists to be a non-Anthropic,
    non-OpenAI lineage
  - GEMINI_API_KEY / GOOGLE_API_KEY / JETSKI_OAUTH_TOKEN do not leak into the other
    reviewers' environments
  - in scope and consult modes Antigravity gets its own lens and shares the
    ASSUMPTIONS / FIRST MOVE slot with the other voices

Run:  python3 -m unittest discover -s test
Against the real binary (needs agy signed in and a free localhost port, i.e. NOT inside
a Bash sandbox):
      ASSESS_PANEL_AGY_SMOKE=1 python3 -m unittest discover -s test -p test_panel_agy.py -k RealAgyContractTest -v
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent / "skills" / "assess" / "panel.sh"
REPO = PANEL.parent.parent.parent

CODEX_STUB = """#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { out="$2"; shift; }; shift; done
cat >/dev/null
printf 'CODEX-STUB-REVIEW gemini=[%s]\\n' "${GEMINI_API_KEY:-unset}" >"$out"
"""

CLAUDE_STUB = """#!/usr/bin/env bash
cat >/dev/null
printf 'CLAUDE-STUB-REVIEW gemini=[%s]\\n' "${GEMINI_API_KEY:-unset}"
"""

GROK_STUB = """#!/usr/bin/env bash
printf 'GROK-STUB-REVIEW gemini=[%s]\\n' "${GEMINI_API_KEY:-unset}"
"""

AGY_STUB = """#!/usr/bin/env bash
prompt="$(cat)"
printf 'AGY-STUB-REVIEW key=[%s] home=[%s] token=[%s] keychain=[%s]\\n' \\
  "${GEMINI_API_KEY:-unset}" "${HOME:-unset}" \\
  "$([ -L "${HOME:-}/.gemini/jetski-standalone-oauth-token" ] && echo yes || echo no)" \\
  "$([ -L "${HOME:-}/Library/Keychains" ] && echo yes || echo no)"
printf 'ARGS: %s\\n' "$*"
printf 'SETTINGS: %s\\n' "$(cat "${HOME:-}/.gemini/antigravity-cli/settings.json" 2>/dev/null)"
printf 'LENS: %s\\n' "$(printf '%s' "$prompt" | grep -E '^[0-9]+\\. |^(CONTRAST CASE|FAULT LINE|MINIMAL REDUCER|FIRST MOVE)' | tr '\\n' '|')"
"""

# The panel asks macOS `security` whether agy's keychain item exists. The real binary
# would answer for the developer's real keychain, so it is shadowed: 44 is the real
# "item not found" status; a test that wants a keychain sign-in sets SECURITY_STUB_RC=0.
SECURITY_STUB = """#!/usr/bin/env bash
exit "${SECURITY_STUB_RC:-44}"
"""

CURL_STUB = """#!/usr/bin/env bash
url="${!#}"
host="${url#*://}"; host="${host%%/*}"
[ -n "${CURL_STUB_LOG:-}" ] && printf '%s\\n' "$host" >>"$CURL_STUB_LOG"
for h in ${CURL_STUB_REFUSE:-}; do
  [ "$h" = "$host" ] && { printf '403'; exit 0; }
done
printf '%s' "${CURL_STUB_STATUS:-200}"
"""

TOKEN = "jetski-standalone-oauth-token"


class PanelAgyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._stub("codex", CODEX_STUB)
        self._stub("claude", CLAUDE_STUB)
        self._stub("grok", GROK_STUB)
        self._stub("agy", AGY_STUB)
        self._stub("security", SECURITY_STUB)
        self.artifact = Path(self.tmp) / "artifact.txt"
        self.artifact.write_text("diff --git a/x.py b/x.py\n+print('hi')\n")
        self.grok_home = Path(self.tmp) / ".grok"
        self.grok_home.mkdir()
        (self.grok_home / "auth.json").write_text("{}")
        self.agy_home = Path(self.tmp) / ".gemini"
        self.agy_home.mkdir()
        (self.agy_home / TOKEN).write_text("{}")
        # A login keychain dir, as on macOS: the throwaway home must link to it or agy
        # cannot find its keychain item (verified: "authentication required" without it).
        (Path(self.tmp) / "Library" / "Keychains").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)
        return p

    def run_panel(self, *args, env=None, mode="review"):
        e = dict(os.environ)
        e["PATH"] = f"{self.bin}:/usr/bin:/bin"
        e["HOME"] = self.tmp
        for var in ("ASSESS_PANEL_GROK", "ASSESS_PANEL_AGY", "ASSESS_PANEL_PI",
                    "GROK_HOME", "AGY_HOME", "XAI_API_KEY", "GEMINI_API_KEY",
                    "GOOGLE_API_KEY", "JETSKI_OAUTH_TOKEN", "AGY_LLM_GATEWAY_API_KEY",
                    "AGY_LLM_GATEWAY_URL", "GOOGLE_GEMINI_BASE_URL",
                    "GROK_CLI_CHAT_PROXY_BASE_URL", "SECURITY_STUB_RC",
                    "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            e.pop(var, None)
        e.update(env or {})
        cmd = ["bash", str(PANEL), "--mode", mode, str(self.artifact), *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=e,
                              cwd=self.tmp)

    @staticmethod
    def _line(out, prefix):
        return next(l for l in out.splitlines() if l.startswith(prefix))

    # --- on by default ---------------------------------------------------

    def test_on_by_default_and_counted_as_a_voice(self):
        r = self.run_panel("some task")
        self.assertIn("AGY-STUB-REVIEW", r.stdout)
        self.assertIn("ANTIGRAVITY (", r.stdout)
        # Four reviewers answered, so the review footer uses the >=4 wording and
        # names Antigravity among the foreign voices.
        self.assertIn("a split is NOT a vote", r.stdout)
        self.assertIn("Antigravity", r.stdout)

    def test_explicit_off_values_stay_silent(self):
        for value in ("0", "false", "no", "off"):
            r = self.run_panel("some task", env={"ASSESS_PANEL_AGY": value})
            self.assertNotIn("ANTIGRAVITY (", r.stdout, f"value {value!r}")
            self.assertIn("2-1 split is NOT a vote", r.stdout, f"value {value!r}")

    def test_unrecognised_flag_value_is_loud_not_a_guess(self):
        r = self.run_panel("some task", env={"ASSESS_PANEL_AGY": "TRUE"})
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("not a recognised on/off value", r.stdout)
        self.assertNotIn("AGY-STUB-REVIEW", r.stdout)

    # --- unavailable: always loud ----------------------------------------

    def test_missing_binary_reports_skip(self):
        (self.bin / "agy").unlink()
        r = self.run_panel("some task")
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("not on PATH", r.stdout)
        self.assertIn("ASSESS_PANEL_AGY=0", r.stdout)
        # There is no `agy auth` subcommand; the fix must name the real login route.
        self.assertNotIn("agy auth login", r.stdout)
        self.assertIn("sign in once", r.stdout)
        self.assertIn("CODEX-STUB-REVIEW", r.stdout)
        self.assertIn("CLAUDE-STUB-REVIEW", r.stdout)
        self.assertIn("GROK-STUB-REVIEW", r.stdout)

    def test_missing_sign_in_reports_skip(self):
        (self.agy_home / TOKEN).unlink()
        r = self.run_panel("some task")
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("no sign-in", r.stdout)
        self.assertIn("Run `agy` once and sign in", r.stdout)
        self.assertNotIn("agy auth login", r.stdout)
        self.assertNotIn("AGY-STUB-REVIEW", r.stdout)

    def test_keychain_item_counts_as_a_sign_in(self):
        # No token file, but the OS keychain holds agy's item: that is the normal macOS
        # state, and the seat must run with no symlink needed.
        (self.agy_home / TOKEN).unlink()
        r = self.run_panel("some task", env={"SECURITY_STUB_RC": "0"})
        self.assertNotIn(">>> SKIPPED", r.stdout)
        self.assertIn("AGY-STUB-REVIEW key=[unset]", r.stdout)
        self.assertIn("token=[no]", r.stdout)
        self.assertNotIn("modelProvider", self._line(r.stdout, "SETTINGS:"))

    def test_env_key_counts_as_a_credential(self):
        (self.agy_home / TOKEN).unlink()
        r = self.run_panel("some task", env={"GEMINI_API_KEY": "AIza-secret"})
        self.assertNotIn(">>> SKIPPED", r.stdout)
        self.assertIn("AGY-STUB-REVIEW key=[AIza-secret]", r.stdout)
        # An API key as the ONLY credential: agy is told to use the gemini provider so it
        # does not demand an OAuth login it cannot perform headless.
        self.assertIn('"modelProvider":"gemini"', self._line(r.stdout, "SETTINGS:"))

    # --- hermetic and read-only ------------------------------------------

    def test_reviewer_is_read_only_and_hermetic(self):
        r = self.run_panel("some task")
        args = self._line(r.stdout, "ARGS:")
        self.assertIn("--mode plan", args)
        self.assertIn("--sandbox", args)
        self.assertIn("--output-format text", args)
        # In print mode plan mode IS a slash-command expansion; this flag switches it off.
        self.assertNotIn("--disable-slash-commands", args)
        self.assertNotIn(f"home=[{self.tmp}]", r.stdout)
        self.assertIn("token=[yes]", r.stdout)
        self.assertIn("keychain=[yes]", r.stdout)
        settings = self._line(r.stdout, "SETTINGS:")
        self.assertIn('"toolPermission":"proceed-in-sandbox"', settings)
        self.assertIn('"enableTerminalSandbox":true', settings)
        # The root is $PWD, which bash reports as a real path (/tmp -> /private/tmp on macOS).
        root = os.path.realpath(self.tmp)
        self.assertIn(f'"permissions":{{"allow":["read_file({root})"]}}', settings)
        self.assertNotIn("modelProvider", settings)

    def test_model_and_effort_are_passed_explicitly(self):
        r = self.run_panel("some task", env={"ASSESS_AGY_MODEL": "gemini-4-pro",
                                             "ASSESS_AGY_EFFORT": "max"})
        args = self._line(r.stdout, "ARGS:")
        self.assertIn("--model gemini-4-pro", args)
        self.assertIn("--effort max", args)
        self.assertIn("ANTIGRAVITY (gemini-4-pro @ max", r.stdout)

    def test_non_google_model_is_refused(self):
        # agy can drive Anthropic and OpenAI models too. This seat is the Google lineage;
        # letting it become a second Claude would make the footer count one lineage twice.
        for model in ("claude-opus-5-5-high", "gpt-oss-120b-medium"):
            r = self.run_panel("some task", env={"ASSESS_AGY_MODEL": model})
            self.assertIn(">>> SKIPPED", r.stdout, model)
            self.assertIn("not a Google model", r.stdout, model)
            self.assertNotIn("AGY-STUB-REVIEW", r.stdout, model)

    def test_env_key_does_not_reach_the_other_reviewers(self):
        r = self.run_panel("some task", env={"GEMINI_API_KEY": "AIza-secret"})
        self.assertIn("AGY-STUB-REVIEW key=[AIza-secret]", r.stdout)
        self.assertIn("CODEX-STUB-REVIEW gemini=[unset]", r.stdout)
        self.assertIn("CLAUDE-STUB-REVIEW gemini=[unset]", r.stdout)
        self.assertIn("GROK-STUB-REVIEW gemini=[unset]", r.stdout)

    # --- scope & consult modes -------------------------------------------

    def test_scope_mode_gives_agy_its_own_lens(self):
        r = self.run_panel("some request", mode="scope")
        self.assertIn("only slot ALL FOUR reviewers answer", r.stdout)
        lens = self._line(r.stdout, "LENS:")
        self.assertIn("INVARIANTS", lens)
        self.assertIn("BLAST RADIUS", lens)
        self.assertNotIn("MAXIMAL READING", lens)
        self.assertNotIn("PRE-MORTEM", lens)
        self.assertNotIn("COMPETING READINGS", lens)
        self.assertIn("Antigravity: invariants, blast radius", r.stdout)

    def test_consult_mode_gives_agy_its_own_lens(self):
        r = self.run_panel("some problem", mode="consult")
        lens = self._line(r.stdout, "LENS:")
        self.assertIn("CONTRAST CASE", lens)
        self.assertIn("FAULT LINE", lens)
        self.assertIn("MINIMAL REDUCER", lens)

    # --- a dead agy is never read as agreement ---------------------------

    def test_failed_agy_is_loud_and_does_not_count_as_agreement(self):
        self._stub("agy", "#!/usr/bin/env bash\ncat >/dev/null\necho boom >&2\nexit 1\n")
        r = self.run_panel("some task")
        self.assertIn("FAILED (exit 1)", r.stdout)
        self.assertIn("boom", r.stdout)
        self.assertIn("2-1 split is NOT a vote", r.stdout)

    def test_agy_alone_still_counts_as_cross_lineage(self):
        self._stub("codex", "#!/usr/bin/env bash\ncat >/dev/null\nexit 1\n")
        self._stub("grok", "#!/usr/bin/env bash\nexit 1\n")
        r = self.run_panel("some task")
        self.assertIn("AGY-STUB-REVIEW", r.stdout)
        self.assertNotIn("NO CROSS-LINEAGE REVIEW HAPPENED", r.stdout)

    # --- network preflight ------------------------------------------------

    def test_refused_tunnel_skips_agy_with_the_host(self):
        self._stub("curl", CURL_STUB)
        r = self.run_panel("some task", env={
            "HTTPS_PROXY": "http://127.0.0.1:1/",
            "CURL_STUB_LOG": str(Path(self.tmp) / "curl.log"),
            "CURL_STUB_REFUSE": "daily-cloudcode-pa.googleapis.com"})
        hosts = (Path(self.tmp) / "curl.log").read_text().split()
        self.assertIn("daily-cloudcode-pa.googleapis.com", hosts)
        self.assertIn("refused a CONNECT tunnel to daily-cloudcode-pa.googleapis.com", r.stdout)
        self.assertNotIn("AGY-STUB-REVIEW", r.stdout)
        self.assertIn("CODEX-STUB-REVIEW", r.stdout)

    def test_api_key_alone_is_probed_at_generativelanguage(self):
        (self.agy_home / TOKEN).unlink()
        self._stub("curl", CURL_STUB)
        self.run_panel("some task", env={
            "GEMINI_API_KEY": "AIza-k",
            "HTTPS_PROXY": "http://127.0.0.1:1/",
            "CURL_STUB_LOG": str(Path(self.tmp) / "curl.log")})
        hosts = (Path(self.tmp) / "curl.log").read_text().split()
        self.assertIn("generativelanguage.googleapis.com", hosts)
        self.assertNotIn("daily-cloudcode-pa.googleapis.com", hosts)

    def test_sign_in_wins_over_api_key_for_the_probe(self):
        # agy prefers its sign-in when both exist, so the probe must follow the sign-in:
        # probing the API host would diagnose a refusal the reviewer never meets.
        self._stub("curl", CURL_STUB)
        self.run_panel("some task", env={
            "GEMINI_API_KEY": "AIza-k",
            "HTTPS_PROXY": "http://127.0.0.1:1/",
            "CURL_STUB_LOG": str(Path(self.tmp) / "curl.log")})
        hosts = (Path(self.tmp) / "curl.log").read_text().split()
        self.assertIn("daily-cloudcode-pa.googleapis.com", hosts)
        self.assertNotIn("generativelanguage.googleapis.com", hosts)


class RealAgyContractTest(unittest.TestCase):
    """Pins the parts of agy's CLI the panel actually depends on."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("agy") is None:
            raise unittest.SkipTest("agy is not installed")

    def test_help_advertises_every_flag_the_panel_passes(self):
        out = subprocess.run(["agy", "--help"], capture_output=True, text=True,
                             timeout=60)
        text = out.stdout + out.stderr
        for flag in ("--mode", "--model", "--effort", "--sandbox", "--output-format"):
            self.assertIn(flag, text, f"agy no longer advertises {flag}")
        self.assertIn("plan", text, "agy no longer advertises a plan mode")

    def test_real_seat_answers(self):
        """The one check the stubs cannot make: the launch line, against the real binary
        and the real sign-in, produces a review. Everything that broke the first version
        of this seat (a model the account could not see, a permission soft-denial ending
        in exit 0 with EMPTY output) is invisible to --help and to stubs. Plan mode being
        silently off is NOT visible here either — agy only warns on stderr, which the
        panel keeps in a log it deletes — so test_real_launch_line_keeps_plan_mode pins
        that one. Opt-in because it spends a model call and needs a free localhost port,
        which a Bash sandbox forbids."""
        if os.environ.get("ASSESS_PANEL_AGY_SMOKE") != "1":
            raise unittest.SkipTest("set ASSESS_PANEL_AGY_SMOKE=1 to run against the real agy")
        tmp = tempfile.mkdtemp()
        try:
            bin_ = Path(tmp) / "bin"
            bin_.mkdir()
            for name, body in (("codex", CODEX_STUB), ("claude", CLAUDE_STUB)):
                p = bin_ / name
                p.write_text(body)
                p.chmod(0o755)
            artifact = Path(tmp) / "artifact.txt"
            artifact.write_text(
                "diff --git a/skills/assess/panel.sh b/skills/assess/panel.sh\n"
                "-      --sandbox --disable-slash-commands --output-format text \\\n"
                "+      --sandbox --output-format text \\\n")
            e = dict(os.environ)
            e["PATH"] = f"{bin_}:{os.environ['PATH']}"
            e["ASSESS_PANEL_LOG"] = "off"
            e["ASSESS_PANEL_GROK"] = "0"
            e["ASSESS_PANEL_PI"] = "0"
            e["ASSESS_PANEL_EXEC"] = "0"
            e["ASSESS_PANEL_TIMEOUT"] = "300"
            r = subprocess.run(
                ["bash", str(PANEL), "--mode", "review", str(artifact),
                 "drop the slash-command flag from the agy launch line so plan mode takes effect"],
                capture_output=True, text=True, timeout=420, env=e, cwd=str(REPO))
            head, sep, rest = r.stdout.partition("================ ANTIGRAVITY (")
            self.assertTrue(sep, "no Antigravity section in the panel output:\n"
                            + r.stdout[-1500:] + "\n--- stderr ---\n" + r.stderr[-800:])
            section = rest.split("\n================", 1)[0]
            self.assertNotIn(">>> SKIPPED", section)
            self.assertNotIn(">>> FAILED", section)
            self.assertNotIn(">>> TIMED OUT", section)
            self.assertNotIn("No findings — reviewer exited cleanly", section)
            self.assertNotIn("auto-denied", section)
            self.assertGreater(len(section.strip()), 200, section)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_real_launch_line_keeps_plan_mode(self):
        """The launch line, run directly so its stderr is visible: agy prints
        'warning: --mode plan has no effect ...' when a flag switches plan mode off, and
        'auto-denied' when a tool hit the headless permission wall. The throwaway home
        here mirrors panel.sh's (token file and ~/Library/Keychains linked in, the same
        settings file); keep the flags in step with the launch line in panel.sh."""
        if os.environ.get("ASSESS_PANEL_AGY_SMOKE") != "1":
            raise unittest.SkipTest("set ASSESS_PANEL_AGY_SMOKE=1 to run against the real agy")
        home = Path(tempfile.mkdtemp())
        try:
            (home / ".gemini" / "antigravity-cli").mkdir(parents=True)
            real = Path(os.environ["HOME"])
            tok = real / ".gemini" / TOKEN
            if tok.exists():
                os.symlink(tok, home / ".gemini" / TOKEN)
            kc = real / "Library" / "Keychains"
            if kc.is_dir():
                (home / "Library").mkdir()
                os.symlink(kc, home / "Library" / "Keychains")
            (home / ".gemini" / "antigravity-cli" / "settings.json").write_text(
                '{"toolPermission":"proceed-in-sandbox","enableTerminalSandbox":true}\n')
            e = dict(os.environ)
            e["HOME"] = str(home)
            for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "JETSKI_OAUTH_TOKEN",
                        "AGY_LLM_GATEWAY_API_KEY"):
                e.pop(var, None)
            r = subprocess.run(
                ["agy", "--mode", "plan", "--model", "gemini-4-argon", "--effort", "high",
                 "--sandbox", "--output-format", "text"],
                input="Reply with the single word OK and nothing else.",
                capture_output=True, text=True, timeout=300, env=e, cwd=str(REPO))
            self.assertEqual(0, r.returncode, r.stderr[-800:])
            self.assertNotIn("has no effect", r.stderr, r.stderr[-800:])
            self.assertNotIn("auto-denied", r.stderr, r.stderr[-800:])
            self.assertTrue(r.stdout.strip(), "empty stdout; stderr: " + r.stderr[-800:])
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
