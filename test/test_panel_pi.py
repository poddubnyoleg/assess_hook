"""End-to-end tests for the optional third reviewer in skills/assess/panel.sh.

Each test runs the real panel.sh as a subprocess against stub `codex`, `claude`
and `pi` binaries on a scratch PATH, so no tokens are spent and every reviewer's
exit status and output are controlled per test.

The panel's value is decorrelation, so the failure that matters is not "pi
errored" but "pi didn't run and nobody noticed" — the caller reads two sections
and believes three lineages agreed. Every test here pins one way that could
happen:

  - off by default: no pi section at all, and the two-reviewer wording stands
  - enabled but unavailable (no binary / no credential / unknown model) prints an
    explicit SKIPPED line naming the cause, never silence
  - enabled and working prints its output and switches the reconciliation footer
    to the three-lineage wording
  - a pi that dies, times out, or exits 0 with nothing still produces a loud
    section — an empty reviewer is never rendered as agreement
  - the provider key reaches ONLY pi: codex and claude never see it

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
printf 'CODEX-STUB-REVIEW key=[%s] openai=[%s]\\n' \\
  "${OPENROUTER_API_KEY:-unset}" "${OPENAI_API_KEY:-unset}" >"$out"
"""

CLAUDE_STUB = """#!/usr/bin/env bash
cat >/dev/null
printf 'CLAUDE-STUB-REVIEW key=[%s]\\n' "${OPENROUTER_API_KEY:-unset}"
"""

# Answers --list-models with the requested pattern (so the preflight resolves),
# echoes the prompt back as a review, and reports what it was invoked with.
PI_STUB = """#!/usr/bin/env bash
for a in "$@"; do
  # Real pi prints the model table on STDERR. A preflight that reads only stdout
  # declares every model unknown and blames the user's models.json for it.
  if [ "$a" = "--list-models" ]; then printf 'openrouter  %s  1.0M\\n' "${!#}" >&2; exit 0; fi
done
prompt="$(cat)"
printf 'PI-STUB-REVIEW key=[%s] agentdir=[%s] authjson=[%s]\\n' \
  "${OPENROUTER_API_KEY:-unset}" "${PI_CODING_AGENT_DIR:-unset}" \
  "$([ -f "${PI_CODING_AGENT_DIR:-/nonexistent}/auth.json" ] && echo yes || echo no)"
printf 'ARGS: %s\\n' "$*"
printf 'LENS: %s\\n' "$(printf '%s' "$prompt" \
  | grep -o -e 'COMPETING READINGS' -e 'PRE-MORTEM' -e 'UNDERLYING QUESTION' \
  | sort -u | tr '\\n' ' ')"
"""

PI_STUB_UNKNOWN_MODEL = """#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "--list-models" ]; then exit 0; fi
done
printf 'should not get here\\n'
"""

PI_STUB_EMPTY_OK = """#!/usr/bin/env bash
for a in "$@"; do
  # Real pi prints the model table on STDERR. A preflight that reads only stdout
  # declares every model unknown and blames the user's models.json for it.
  if [ "$a" = "--list-models" ]; then printf 'openrouter  %s  1.0M\\n' "${!#}" >&2; exit 0; fi
done
cat >/dev/null
exit 0
"""

PI_STUB_FAILS = """#!/usr/bin/env bash
for a in "$@"; do
  # Real pi prints the model table on STDERR. A preflight that reads only stdout
  # declares every model unknown and blames the user's models.json for it.
  if [ "$a" = "--list-models" ]; then printf 'openrouter  %s  1.0M\\n' "${!#}" >&2; exit 0; fi
done
cat >/dev/null
echo "401 User not found." >&2
exit 1
"""


class PanelPiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._stub("codex", CODEX_STUB)
        self._stub("claude", CLAUDE_STUB)
        self.artifact = Path(self.tmp) / "artifact.txt"
        self.artifact.write_text("diff --git a/x.py b/x.py\n+print('hi')\n")
        # An empty agent dir: no auth.json, so credential resolution depends purely
        # on what each test supplies.
        self.agent_dir = Path(self.tmp) / "pi-agent-src"
        self.agent_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)
        return p

    def run_panel(self, *args, env=None, mode="review"):
        e = dict(os.environ)
        # A minimal PATH: the stubs plus the system basics, so a real codex/claude/pi
        # installed on this machine can never satisfy the panel by accident.
        e["PATH"] = f"{self.bin}:/usr/bin:/bin"
        e["HOME"] = self.tmp
        e["PI_CODING_AGENT_DIR"] = str(self.agent_dir)
        e.pop("ASSESS_PANEL_PI", None)
        e.pop("OPENROUTER_API_KEY", None)
        e.update(env or {})
        cmd = ["bash", str(PANEL), "--mode", mode, str(self.artifact), *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=e,
                              cwd=self.tmp)

    # --- off by default -------------------------------------------------

    def test_disabled_by_default_no_pi_section(self):
        r = self.run_panel("some task")
        self.assertIn("CODEX-STUB-REVIEW", r.stdout)
        self.assertIn("CLAUDE-STUB-REVIEW", r.stdout)
        self.assertNotIn("PI (", r.stdout)
        # ">>> SKIPPED", not bare "SKIPPED": the footer always names SKIPPED as one of
        # the states that make a panel incomplete.
        self.assertNotIn(">>> SKIPPED", r.stdout)
        # The two-reviewer reconciliation wording must stand when pi is off.
        self.assertIn("both say fine", r.stdout)

    def test_disabled_scope_mode_keeps_two_reviewer_wording(self):
        r = self.run_panel("some request", mode="scope")
        self.assertIn("only slot BOTH reviewers answer", r.stdout)
        self.assertNotIn("ALL THREE", r.stdout)

    # --- enabled but unavailable: always loud ---------------------------

    def test_enabled_without_binary_reports_skip(self):
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1"})
        self.assertIn("SKIPPED", r.stdout)
        self.assertIn("not on PATH", r.stdout)
        self.assertIn("Do NOT count this as a third lineage", r.stdout)
        # The other two still ran — a missing pi degrades, it does not abort.
        self.assertIn("CODEX-STUB-REVIEW", r.stdout)
        self.assertIn("CLAUDE-STUB-REVIEW", r.stdout)

    def test_enabled_without_credential_reports_skip(self):
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1"})
        self.assertIn("SKIPPED", r.stdout)
        self.assertIn("no credential for provider 'openrouter'", r.stdout)
        self.assertIn("OPENROUTER_API_KEY", r.stdout)

    def test_unknown_model_names_the_real_cause(self):
        # pi does not fail fast on an unknown id — it warns and dies later at the
        # provider with a 401, which reads like an auth problem. The preflight must
        # name the model, not the key.
        self._stub("pi", PI_STUB_UNKNOWN_MODEL)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn("SKIPPED", r.stdout)
        self.assertIn("does not know the model", r.stdout)
        self.assertIn("models.json", r.stdout)

    def test_auth_json_entry_counts_as_a_credential(self):
        # OAuth providers carry no env var; a provider entry in pi's auth.json is
        # credential enough and must not be skipped for "no credential".
        (self.agent_dir / "auth.json").write_text('{"openrouter": {"type": "oauth"}}')
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1"})
        # ">>> SKIPPED", not bare "SKIPPED": the footer always names SKIPPED as one of
        # the states that make a panel incomplete.
        self.assertNotIn(">>> SKIPPED", r.stdout)
        self.assertIn("PI-STUB-REVIEW", r.stdout)

    # --- enabled and working --------------------------------------------

    def test_enabled_runs_and_switches_footer_to_three(self):
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn("PI-STUB-REVIEW", r.stdout)
        self.assertIn("2-1 split is NOT a vote", r.stdout)
        self.assertNotIn("both say fine", r.stdout)

    def test_scope_mode_gives_pi_its_own_lens(self):
        self._stub("pi", PI_STUB)
        r = self.run_panel("some request", mode="scope",
                           env={"ASSESS_PANEL_PI": "1", "OPENROUTER_API_KEY": "k"})
        self.assertIn("only slot ALL THREE reviewers answer", r.stdout)
        # Its lens is the competing-readings one, not a second copy of either other
        # lens — three reviewers asked the same question would cost three calls for
        # one voice, which is the failure the split exists to prevent.
        lens = next(l for l in r.stdout.splitlines() if l.startswith("LENS:"))
        self.assertIn("COMPETING READINGS", lens)
        self.assertNotIn("PRE-MORTEM", lens)
        self.assertNotIn("UNDERLYING QUESTION", lens)

    def test_reviewer_is_read_only_and_hermetic(self):
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        args = next(l for l in r.stdout.splitlines() if l.startswith("ARGS:"))
        # Read-only allowlist — pi's other built-ins are bash/edit/write.
        self.assertIn("--tools read,grep,find,ls", args)
        for flag in ("--no-session", "--no-extensions", "--no-skills",
                     "--no-prompt-templates", "--no-context-files"):
            self.assertIn(flag, args)
        # Never the user's own agent dir: no session pollution, no inherited settings.
        self.assertNotIn(f"agentdir=[{self.agent_dir}]", r.stdout)

    def test_effort_and_model_are_passed_explicitly(self):
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k",
                                             "ASSESS_PI_EFFORT": "xhigh"})
        args = next(l for l in r.stdout.splitlines() if l.startswith("ARGS:"))
        self.assertIn("--thinking xhigh", args)
        self.assertIn("--model moonshotai/kimi-k3", args)
        # The header states what was REQUESTED, not what the model granted: pi clamps an
        # unsupported level downward in silence, so "@ xhigh" would assert something that
        # may not have happened.
        self.assertIn("PI (moonshotai/kimi-k3, effort requested: xhigh", r.stdout)

    # --- effort is reported honestly ------------------------------------

    def _models_json(self, entry, api_key_name=None):
        model = {"id": "moonshotai/kimi-k3", "reasoning": True}
        model.update(entry)
        provider = {"baseUrl": "https://openrouter.ai/api/v1",
                    "api": "openai-completions", "models": [model]}
        if api_key_name:
            provider["apiKey"] = api_key_name
        (self.agent_dir / "models.json").write_text(
            __import__("json").dumps({"providers": {"openrouter": provider}}))

    def test_clamped_effort_is_announced_not_asserted(self):
        # No thinkingLevelMap => pi drops xhigh and clamps DOWN to high, silently. The
        # panel must say so; a header reading "xhigh" over a high-effort review is the
        # one degradation that also prints a positive claim about what didn't happen.
        self._models_json({})
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k",
                                             "ASSESS_PI_EFFORT": "xhigh"})
        self.assertIn("PI-STUB-REVIEW", r.stdout)
        self.assertIn("NOTE — effort 'xhigh' is NOT supported", r.stdout)
        self.assertIn("clamped it to 'high'", r.stdout)

    def test_mapped_effort_is_not_flagged(self):
        self._models_json({"thinkingLevelMap": {"off": None, "minimal": None, "low": "low",
                                                "medium": None, "high": "high",
                                                "xhigh": "max"}})
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k",
                                             "ASSESS_PI_EFFORT": "xhigh"})
        self.assertNotIn("is NOT supported", r.stdout)
        # It still reports the wire value, since "xhigh" is not what the provider sees.
        self.assertIn("reaches the provider as 'max'", r.stdout)

    # --- credential isolation -------------------------------------------

    def test_key_var_comes_from_the_provider_declaration(self):
        # models.json is authoritative for the env-var name; deriving it from the
        # provider string guesses, and a wrong guess both hides a present credential
        # and exports a name pi never reads.
        self._models_json({}, api_key_name="MY_CUSTOM_KEY")
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "MY_CUSTOM_KEY": "declared-key"})
        self.assertNotIn(">>> SKIPPED", r.stdout)
        self.assertIn("PI-STUB-REVIEW", r.stdout)

    def test_invalid_key_var_is_refused_not_dereferenced(self):
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "ASSESS_PI_KEY_VAR": "BAD;touch /tmp/pwn"})
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("not a valid shell identifier", r.stdout)

    def test_key_file_is_read_and_reaches_only_pi(self):
        keyfile = Path(self.tmp) / "project.env"
        keyfile.write_text('GEMINI_API_KEY=other\nexport OPENROUTER_API_KEY="sk-from-file"\n')
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "ASSESS_PI_KEY_FILE": str(keyfile)})
        self.assertIn("PI-STUB-REVIEW key=[sk-from-file]", r.stdout)
        # The other two reviewers must never see another provider's credential.
        self.assertIn("CODEX-STUB-REVIEW key=[unset]", r.stdout)
        self.assertIn("CLAUDE-STUB-REVIEW key=[unset]", r.stdout)

    def test_unreadable_key_file_reports_skip(self):
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "ASSESS_PI_KEY_FILE": "/nope/absent.env"})
        self.assertIn("SKIPPED", r.stdout)
        self.assertIn("not readable", r.stdout)

    def test_env_key_does_not_reach_the_other_reviewers(self):
        # The key is stripped from the parent environment after it is read, then
        # re-exported only into pi's subshell — otherwise enabling pi hands one vendor's
        # credential to two other vendors' processes.
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "sk-secret"})
        self.assertIn("PI-STUB-REVIEW key=[sk-secret]", r.stdout)
        self.assertIn("CODEX-STUB-REVIEW key=[unset]", r.stdout)
        self.assertIn("CLAUDE-STUB-REVIEW key=[unset]", r.stdout)

    def test_auth_json_is_copied_only_when_it_is_the_credential(self):
        (self.agent_dir / "auth.json").write_text('{"openrouter": {"type": "oauth"}}')
        self._stub("pi", PI_STUB)
        # An env key authenticates the run, so the multi-provider credential store has no
        # business being copied into a temp dir that may sit inside the repo.
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn("authjson=[no]", r.stdout)
        # With no env key, auth.json IS what authenticates pi, so it must travel.
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1"})
        self.assertIn("authjson=[yes]", r.stdout)

    def test_model_match_is_exact_not_substring(self):
        # A row for a different model whose id merely contains ours must not pass.
        self._stub("pi", PI_STUB.replace('"${!#}"', '"${!#}-preview"'))
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("does not know the model", r.stdout)

    def test_preflight_crash_is_not_reported_as_unknown_model(self):
        self._stub("pi", """#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "--list-models" ]; then echo "boom" >&2; exit 3; fi
done
""")
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn("model preflight", r.stdout)
        self.assertIn("could not be verified", r.stdout)
        self.assertNotIn("does not know the model", r.stdout)

    def test_unconfigured_model_effort_is_flagged_as_unverifiable(self):
        # No models.json at all: a built-in model with pi's stock levels, where an
        # unsupported effort clamps down in silence. Saying nothing would let the
        # header's "effort requested" read as "effort granted".
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn("could not verify", r.stdout)

    def _overrides_json(self, override):
        (self.agent_dir / "models.json").write_text(__import__("json").dumps({
            "providers": {"openrouter": {"baseUrl": "https://openrouter.ai/api/v1",
                                         "api": "openai-completions", "models": [],
                                         "modelOverrides": {"moonshotai/kimi-k3": override}}}}))

    def test_model_overrides_are_read_like_model_entries(self):
        # Built-in models are adjusted via modelOverrides, not a models[] entry; reading
        # only the latter made every catalog model look unconfigurable.
        self._overrides_json({"reasoning": True, "thinkingLevelMap": {"xhigh": "max"}})
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k",
                                             "ASSESS_PI_EFFORT": "xhigh"})
        self.assertNotIn("could not verify", r.stdout)
        self.assertIn("reaches the provider as 'max'", r.stdout)

    def test_override_without_reasoning_flag_is_not_claimed_as_verified(self):
        # A thinkingLevelMap says nothing about whether the BUILT-IN model reasons; if it
        # doesn't, pi runs with thinking off no matter the level. Claiming a wire value
        # here would be a confident statement about something unproven.
        self._overrides_json({"thinkingLevelMap": {"xhigh": "max"}})
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn("could not verify the effort", r.stdout)
        self.assertNotIn("reaches the provider as 'max'", r.stdout)

    def test_unrecognised_flag_value_is_loud_not_off(self):
        # Asking for the reviewer and getting silence is the one outcome the design
        # forbids; an unparsed value must not read as "off".
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "TRUE"})
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("not a recognised on/off value", r.stdout)

    def test_explicit_off_values_stay_silent(self):
        for value in ("0", "false", "no", "off"):
            r = self.run_panel("some task", env={"ASSESS_PANEL_PI": value})
            self.assertNotIn("PI (", r.stdout, f"value {value!r}")

    def test_model_on_a_different_provider_does_not_satisfy_the_check(self):
        # `pi --list-models` lists every provider. Matching the id alone accepts a row
        # from provider B while the run targets provider A, which then 401s.
        self._stub("pi", PI_STUB.replace("'openrouter  %s  1.0M\\n'", "'someoneelse  %s  1.0M\\n'"))
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn(">>> SKIPPED", r.stdout)
        self.assertIn("does not know the model", r.stdout)

    def test_literal_api_key_in_models_json_is_not_treated_as_a_var_name(self):
        # models.json may carry the key itself rather than an env-var name. Treating the
        # literal as a variable name both refuses a working config and prints the secret
        # into the panel's own output.
        self._models_json({}, api_key_name="sk-or-v1-literalsecret")
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1"})
        self.assertNotIn(">>> SKIPPED", r.stdout)
        self.assertNotIn("literalsecret", r.stdout)
        self.assertIn("PI-STUB-REVIEW", r.stdout)

    def test_shared_provider_key_is_not_unset_from_under_codex(self):
        # Protecting codex from pi's credential must not strip the credential codex runs
        # on when both are configured against the same provider.
        self._stub("pi", PI_STUB)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "ASSESS_PI_PROVIDER": "openai",
                                             "ASSESS_PI_MODEL": "gpt-x",
                                             "OPENAI_API_KEY": "sk-shared"})
        codex = next(l for l in r.stdout.splitlines() if l.startswith("CODEX-STUB"))
        self.assertIn("openai=[sk-shared]", codex)

    # --- a dead reviewer is never read as agreement ----------------------

    def test_empty_output_is_not_silent(self):
        self._stub("pi", PI_STUB_EMPTY_OK)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn("PI (", r.stdout)
        self.assertIn("exited cleanly", r.stdout)

    def test_nonzero_exit_is_reported_with_the_log(self):
        self._stub("pi", PI_STUB_FAILS)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn("FAILED (exit 1)", r.stdout)
        self.assertIn("401 User not found.", r.stdout)

    def test_timeout_kills_pi_and_says_so(self):
        self._stub("pi", PI_STUB.replace("prompt=\"$(cat)\"", "cat >/dev/null; sleep 30"))
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k",
                                             "ASSESS_PANEL_TIMEOUT": "2"})
        self.assertIn("TIMED OUT after 2s", r.stdout)
        self.assertIn("panel is INCOMPLETE", r.stdout)

    def test_dead_pi_does_not_switch_the_footer_to_three(self):
        # Launching is not answering. A timed-out pi leaves two lineages, so the footer
        # must not tell the caller to diff three assumption lists.
        self._stub("pi", PI_STUB.replace("prompt=\"$(cat)\"", "cat >/dev/null; sleep 30"))
        r = self.run_panel("some request", mode="scope",
                           env={"ASSESS_PANEL_PI": "1", "OPENROUTER_API_KEY": "k",
                                "ASSESS_PANEL_TIMEOUT": "2"})
        self.assertIn("TIMED OUT", r.stdout)
        self.assertIn("only slot BOTH reviewers answer", r.stdout)
        self.assertNotIn("ALL THREE", r.stdout)

    def test_failed_pi_does_not_switch_the_review_footer(self):
        self._stub("pi", PI_STUB_FAILS)
        r = self.run_panel("some task", env={"ASSESS_PANEL_PI": "1",
                                             "OPENROUTER_API_KEY": "k"})
        self.assertIn("FAILED (exit 1)", r.stdout)
        self.assertIn("both say fine", r.stdout)
        self.assertNotIn("2-1 split", r.stdout)


class RealPiContractTest(unittest.TestCase):
    """Pins the parts of pi's CLI the panel actually depends on.

    Every other test here drives a stub, so the whole suite would stay green if a pi
    release renamed a flag or reshaped `--list-models` output — and the panel would
    degrade to a permanent `SKIPPED — pi does not know the model`, sending the user to
    edit a models.json that was never the problem. These run against the real binary and
    skip when it is absent, so they cost nothing in CI and catch that drift locally.

    No model is ever invoked: listing and --help are local operations.
    """

    @classmethod
    def setUpClass(cls):
        if shutil.which("pi") is None:
            raise unittest.SkipTest("pi is not installed")
        cls.agent = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "agent", ""), ignore_errors=True)

    def _pi(self, *args):
        e = dict(os.environ, PI_CODING_AGENT_DIR=self.agent)
        return subprocess.run(["pi", *args], capture_output=True, text=True,
                              timeout=120, env=e)

    def test_help_still_advertises_every_flag_the_panel_passes(self):
        out = self._pi("--help")
        text = out.stdout + out.stderr
        for flag in ("--print", "--provider", "--model", "--thinking", "--mode",
                     "--no-session", "--no-extensions", "--no-skills",
                     "--no-prompt-templates", "--no-context-files", "--offline",
                     "--tools", "--list-models"):
            self.assertIn(flag, text, f"pi no longer advertises {flag}")

    def test_list_models_output_is_still_provider_then_model_columns(self):
        # The preflight matches `$1 == provider && $2 == model` over this output, and
        # reads BOTH streams because pi emits the table on stderr despite using
        # console.log. Both facts are load-bearing and neither is documented.
        out = self._pi("--list-models", "gpt")
        combined = out.stdout + out.stderr
        rows = [l.split() for l in combined.splitlines() if len(l.split()) >= 2]
        rows = [r for r in rows if r[0] != "provider"]      # drop the header line
        self.assertTrue(rows, f"no model rows parsed from --list-models:\n{combined}")
        # awk field 1 must be the provider and field 2 the model id: every row's first
        # field has to look like a provider name, not part of a model id.
        for r in rows:
            self.assertNotIn("/", r[0],
                             f"column 1 is not a bare provider — preflight would misparse: {r}")

    def test_agent_dir_env_var_redirects_config_lookup(self):
        # The whole hermetic-reviewer design rests on PI_CODING_AGENT_DIR pointing pi at
        # a throwaway dir; if it stopped being honoured, the reviewer would silently read
        # the user's real settings, packages and session history again.
        marker = os.path.join(self.agent, "models.json")
        with open(marker, "w") as f:
            f.write('{"providers": {"panelcheck": {"baseUrl": "https://example.invalid",'
                    ' "apiKey": "PANEL_CHECK_KEY", "api": "openai-completions",'
                    ' "models": [{"id": "panel/canary", "reasoning": true}]}}}')
        out = self._pi("--list-models", "panel/canary")
        self.assertIn("panel/canary", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
