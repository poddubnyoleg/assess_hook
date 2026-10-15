"""End-to-end tests for the default-on grok reviewer in skills/assess/panel.sh.

Each test runs the real panel.sh as a subprocess against stub `codex`, `claude`
and `grok` binaries on a scratch PATH, so no tokens are spent and every
reviewer's exit status and output are controlled per test.

Grok differs from pi in one structural way: it is ON BY DEFAULT. That flips
which silent failure matters most — pi's risk was "asked for and quietly
absent", grok's is "believed present on a machine that cannot run it". Every
test here pins one way that could go unnoticed:

  - on by default: the section and the three-voice footer appear with no env
    var asked for; explicit off values are the only silent state
  - unavailable (no binary / no credential / refused tunnel) prints an explicit
    SKIPPED line naming the cause, never silence
  - the reviewer is hermetic: throwaway GROK_HOME, auth symlinked in, read-only
    sandbox and tool allowlist, prompt via file with --verbatim
  - XAI_API_KEY reaches ONLY grok: codex and claude never see it
  - a grok that dies still produces a loud section and never inflates the
    footer's voice count

Run:  python3 -m unittest discover -s test
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent / "skills" / "assess" / "panel.sh"

CODEX_STUB = """#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { out="$2"; shift; }; shift; done
cat >/dev/null
printf 'CODEX-STUB-REVIEW xai=[%s]\\n' "${XAI_API_KEY:-unset}" >"$out"
"""

CLAUDE_STUB = """#!/usr/bin/env bash
cat >/dev/null
printf 'CLAUDE-STUB-REVIEW xai=[%s]\\n' "${XAI_API_KEY:-unset}"
"""

# Reports what it was invoked with. The prompt arrives via --prompt-file (NOT stdin —
# reading stdin here would block on the suite's inherited descriptor), so the prompt
# check greps the file the panel wrote.
GROK_STUB = """#!/usr/bin/env bash
args="$*"
pf=""
prev=""
for a in "$@"; do [ "$prev" = "--prompt-file" ] && pf="$a"; prev="$a"; done
printf 'GROK-STUB-REVIEW key=[%s] home=[%s] authjson=[%s]\\n' \\
  "${XAI_API_KEY:-unset}" "${GROK_HOME:-unset}" \\
  "$([ -e "${GROK_HOME:-/nonexistent}/auth.json" ] && echo yes || echo no)"
printf 'ARGS: %s\\n' "$args"
[ -n "$pf" ] && printf 'LENS: %s\\n' "$(grep -o -e 'ASSUMPTIONS' -e 'TRACTABILITY' \\
  -e 'MAXIMAL READING' -e 'COMPETING READINGS' -e 'WHY HERE' "$pf" | sort -u | tr '\\n' ' ')"
exit 0
"""

# Same CONNECT-status stub as in test_panel_pi.py: answers 403 for hosts named in
# CURL_STUB_REFUSE, logs every probed host.
CURL_STUB = """#!/usr/bin/env bash
url=""
for a in "$@"; do case "$a" in https://*) url="$a" ;; esac; done
host="${url#https://}"; host="${host%%/*}"
[ -n "${CURL_STUB_LOG:-}" ] && printf '%s\\n' "$host" >>"$CURL_STUB_LOG"
for h in ${CURL_STUB_REFUSE:-}; do
  [ "$h" = "$host" ] && { printf '403'; exit 0; }
done
printf '%s' "${CURL_STUB_STATUS:-200}"
"""


class PanelGrokTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._stub("codex", CODEX_STUB)
        self._stub("claude", CLAUDE_STUB)
        self._stub("grok", GROK_STUB)
        self.artifact = Path(self.tmp) / "artifact.txt"
        self.artifact.write_text("diff --git a/x.py b/x.py\n+print('hi')\n")
        # HOME points at the scratch dir, so this is the auth.json the credential
        # preflight resolves. Tests that exercise "no credential" remove it.
        self.grok_home = Path(self.tmp) / ".grok"
        self.grok_home.mkdir()
        (self.grok_home / "auth.json").write_text("{}")

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
        for var in ("ASSESS_PANEL_GROK", "ASSESS_PANEL_AGY", "ASSESS_PANEL_PI", "GROK_HOME",
                    "AGY_HOME", "XAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                    "JETSKI_OAUTH_TOKEN", "AGY_LLM_GATEWAY_API_KEY",
                    "GROK_CLI_CHAT_PROXY_BASE_URL",
                    "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            e.pop(var, None)
        e["ASSESS_PANEL_AGY"] = "0"
        e.update(env or {})
        cmd = ["bash", str(PANEL), "--mode", mode, str(self.artifact), *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=e,
                              cwd=self.tmp)

    # --- on by default ---------------------------------------------------

    def test_on_by_default_and_counted_as_a_voice(self):
        r = self.run_panel("some task")
        self.assertIn("GROK-STUB-REVIEW", r.stdout)
        # Three reviewers answered, so the review footer must use the multi-voice
        # wording — and not the two-voice one.
        self.assertIn("2-1 split is NOT a vote", r.stdout)
        self.assertNotIn("both say fine", r.stdout)

    def test_explicit_off_values_stay_silent(self):
        for value in ("0", "false", "no", "off"):
            r = self.run_panel("some task", env={"ASSESS_PANEL_GROK": value})
            self.assertNotIn("GROK (", r.stdout, f"value {value!r}")
            self.assertIn("both say fine", r.stdout, f"value {value!r}")

    def test_unrecognised_flag_value_is_loud_not_a_guess(self):
        # For a default-on reviewer the ambiguity cuts BOTH ways: "OFF " with a stray
        # space silently running grok is as wrong as "TRUE" silently dropping it.
        r = self.run_panel("some task", env={"ASSESS_PANEL_GROK": "TRUE"})
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("not a recognised on/off value", r.stdout)
        self.assertNotIn("GROK-STUB-REVIEW", r.stdout)

    # --- unavailable: always loud ----------------------------------------

    def test_missing_binary_reports_skip(self):
        (self.bin / "grok").unlink()
        r = self.run_panel("some task")
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("not on PATH", r.stdout)
        self.assertIn("ASSESS_PANEL_GROK=0", r.stdout)
        # The rest of the panel still delivers.
        self.assertIn("CODEX-STUB-REVIEW", r.stdout)
        self.assertIn("CLAUDE-STUB-REVIEW", r.stdout)

    def test_missing_credential_reports_skip(self):
        (self.grok_home / "auth.json").unlink()
        r = self.run_panel("some task")
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("no credential", r.stdout)
        self.assertIn("grok login", r.stdout)

    def test_env_key_counts_as_a_credential(self):
        (self.grok_home / "auth.json").unlink()
        r = self.run_panel("some task", env={"XAI_API_KEY": "xai-secret"})
        self.assertNotIn(">>> SKIPPED", r.stdout)
        self.assertIn("GROK-STUB-REVIEW key=[xai-secret]", r.stdout)

    # --- hermetic and read-only ------------------------------------------

    def test_reviewer_is_read_only_and_hermetic(self):
        r = self.run_panel("some task")
        args = next(l for l in r.stdout.splitlines() if l.startswith("ARGS:"))
        self.assertIn("--sandbox read-only", args)
        self.assertIn("--tools read_file,grep,list_dir", args)
        # The allowlist does NOT remove grok's always-on MCP meta-tools, and Claude
        # Code compatibility loads the user's ~/.claude.json servers by $HOME —
        # without these two flags the "read-only" reviewer can reach write-capable
        # MCP tools (verified against the real CLI).
        self.assertIn("--disallowed-tools search_tool,use_tool", args)
        self.assertIn("--deny MCPTool", args)
        self.assertIn("--verbatim", args)
        self.assertIn("--prompt-file", args)
        # Never the user's own home: a throwaway, with the auth symlinked into it.
        self.assertNotIn(f"home=[{self.grok_home}]", r.stdout)
        self.assertIn("authjson=[yes]", r.stdout)

    def test_model_and_effort_are_passed_explicitly(self):
        r = self.run_panel("some task", env={"ASSESS_GROK_MODEL": "grok-4.5",
                                             "ASSESS_GROK_EFFORT": "high"})
        args = next(l for l in r.stdout.splitlines() if l.startswith("ARGS:"))
        self.assertIn("-m grok-4.5", args)
        self.assertIn("--effort high", args)
        self.assertIn("GROK (grok-4.5 @ high", r.stdout)

    def test_env_key_does_not_reach_the_other_reviewers(self):
        r = self.run_panel("some task", env={"XAI_API_KEY": "xai-secret"})
        self.assertIn("GROK-STUB-REVIEW key=[xai-secret]", r.stdout)
        self.assertIn("CODEX-STUB-REVIEW xai=[unset]", r.stdout)
        self.assertIn("CLAUDE-STUB-REVIEW xai=[unset]", r.stdout)

    # --- scope mode -------------------------------------------------------

    def test_scope_mode_gives_grok_the_same_prompt_as_the_others(self):
        r = self.run_panel("some request", mode="scope")
        # Codex + claude + grok answered, so assumptions diff across three lists.
        self.assertIn("THREE answered", r.stdout)
        lens = next(l for l in r.stdout.splitlines() if l.startswith("LENS:"))
        self.assertIn("ASSUMPTIONS", lens)
        self.assertIn("TRACTABILITY", lens)
        self.assertNotIn("MAXIMAL READING", lens)
        self.assertNotIn("COMPETING READINGS", lens)

    def test_scope_footer_names_no_per_seat_slots(self):
        r = self.run_panel("some request", mode="scope")
        self.assertNotIn("Grok: minimal/maximal reading", r.stdout)
        self.assertIn("same headings", r.stdout)

    # --- a dead grok is never read as agreement ---------------------------

    def test_failed_grok_is_loud_and_does_not_switch_the_footer(self):
        self._stub("grok", "#!/usr/bin/env bash\necho boom >&2\nexit 1\n")
        r = self.run_panel("some task")
        self.assertIn("FAILED (exit 1)", r.stdout)
        self.assertIn("boom", r.stdout)
        # Two reviewers actually answered, so the two-voice wording must stand.
        self.assertIn("both say fine", r.stdout)
        self.assertNotIn("2-1 split", r.stdout)

    def test_grok_alone_still_counts_as_cross_lineage(self):
        # Codex dead + grok alive is a degraded panel, NOT a single-lineage self-check:
        # grok is a foreign voice, so the banner must not fire.
        self._stub("codex", "#!/usr/bin/env bash\ncat >/dev/null\nexit 1\n")
        r = self.run_panel("some task")
        self.assertIn("GROK-STUB-REVIEW", r.stdout)
        self.assertNotIn("NO CROSS-LINEAGE REVIEW HAPPENED", r.stdout)

    def test_codex_and_grok_both_dead_triggers_the_banner(self):
        self._stub("codex", "#!/usr/bin/env bash\ncat >/dev/null\nexit 1\n")
        self._stub("grok", "#!/usr/bin/env bash\nexit 1\n")
        r = self.run_panel("some task")
        self.assertIn("NO CROSS-LINEAGE REVIEW HAPPENED", r.stdout)

    def test_codex_skip_banner_names_grok_as_a_remaining_voice(self):
        # The "only cross-lineage voice is gone" wording over a live grok would be the
        # skip banner overclaiming — grok still speaks for a foreign lineage.
        self._stub("curl", CURL_STUB)
        (Path(self.tmp) / ".codex").mkdir(exist_ok=True)
        (Path(self.tmp) / ".codex" / "auth.json").write_text('{"auth_mode": "chatgpt"}')
        r = self.run_panel("some task", env={
            "HTTPS_PROXY": "http://127.0.0.1:1/",
            "CURL_STUB_REFUSE": "chatgpt.com"})
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("Grok", r.stdout.split("FRESH CLAUDE")[0])
        self.assertNotIn("ONLY cross-lineage voice", r.stdout)

    # --- network preflight ------------------------------------------------

    def test_refused_tunnel_skips_grok_with_the_host(self):
        # OAuth (auth.json) authenticates against the CLI chat proxy; that is the host
        # whose refusal must be named, and grok must not be launched into it.
        self._stub("curl", CURL_STUB)
        r = self.run_panel("some task", env={
            "HTTPS_PROXY": "http://127.0.0.1:1/",
            "CURL_STUB_LOG": str(Path(self.tmp) / "curl.log"),
            "CURL_STUB_REFUSE": "cli-chat-proxy.grok.com"})
        hosts = (Path(self.tmp) / "curl.log").read_text().split()
        self.assertIn("cli-chat-proxy.grok.com", hosts)
        self.assertIn("refused a CONNECT tunnel to cli-chat-proxy.grok.com", r.stdout)
        self.assertNotIn("GROK-STUB-REVIEW", r.stdout)
        self.assertIn("CODEX-STUB-REVIEW", r.stdout)

    def test_api_key_auth_is_probed_at_api_x_ai(self):
        # auth.json stays PRESENT on purpose: the CLI gives an API key precedence
        # over browser credentials, so with both available grok calls api.x.ai —
        # probing the proxy would judge it against a host it never talks to.
        self._stub("curl", CURL_STUB)
        self.run_panel("some task", env={
            "XAI_API_KEY": "xai-k",
            "HTTPS_PROXY": "http://127.0.0.1:1/",
            "CURL_STUB_LOG": str(Path(self.tmp) / "curl.log")})
        hosts = (Path(self.tmp) / "curl.log").read_text().split()
        self.assertIn("api.x.ai", hosts)
        self.assertNotIn("cli-chat-proxy.grok.com", hosts)


class RealGrokContractTest(unittest.TestCase):
    """Pins the parts of grok's CLI the panel actually depends on.

    The stubs above keep the suite green even if a grok release renames a flag —
    after which every real panel would print a permanent FAILED section. These run
    against the real binary and skip when it is absent; --help is local, so no
    model is ever invoked and no tokens are spent.
    """

    @classmethod
    def setUpClass(cls):
        if shutil.which("grok") is None:
            raise unittest.SkipTest("grok is not installed")

    def test_help_still_advertises_every_flag_the_panel_passes(self):
        out = subprocess.run(["grok", "--help"], capture_output=True, text=True,
                             timeout=60)
        text = out.stdout + out.stderr
        # Pin the EXACT spellings panel.sh passes — including the short/alias forms.
        # Asserting only the canonical names would stay green while `-m` or the
        # `--effort` alias vanished, and the stub suite is argv-blind, so this is
        # the one test that can catch a renamed flag before every real panel FAILs.
        for flag in ("--prompt-file", "--verbatim", "-m, --model", "--effort",
                     "--reasoning-effort", "--sandbox", "--tools",
                     "--disallowed-tools", "--deny", "--output-format"):
            self.assertIn(flag, text, f"grok no longer advertises {flag}")


if __name__ == "__main__":
    unittest.main()
