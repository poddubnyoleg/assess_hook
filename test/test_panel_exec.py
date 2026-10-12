"""End-to-end tests for the exec seats in skills/assess/panel.sh.

Each test runs the real panel.sh as a subprocess against stub `codex`, `claude` and
`grok` binaries on a scratch PATH, from inside a throwaway git repository that has a
commit, an uncommitted edit, an untracked file and a gitignored dependency dir — the
four states a scratch copy must reproduce. No tokens are spent.

What is pinned, and why each would otherwise go unnoticed:

  - exec is ON by default in review mode: codex gets --sandbox workspace-write in a
    scratch copy, the fresh Claude gets Bash + an Edit(//scratch/**) rule + the
    --settings that switch its sandbox on, both rooted in a copy that is NOT the repo
  - the copy IS the artifact: HEAD plus the uncommitted edit plus the untracked file
    plus a populated submodule's working tree, with the dependency dir symlinked
    read-through; it is a self-contained clone with no remote (never a worktree, whose
    shared .git the fresh Claude's sandbox leaves writable), built with hooks off and a
    diff that survives the user's diff config
  - the fresh Claude's seat is granted only after a CANARY proves the fence: a nonce the
    shell must read from disk, no reported write to the real tree or the real .git, and
    neither canary file existing afterwards; a canary that writes, lies, quotes the
    command in an error, errors, or says nothing falls the seat back LOUDLY and leaves
    no file behind
  - every way exec can be unavailable (off, non-git root, no commits, size cap, bad
    flag value) is either silent-by-request or an explicit NOTE — never a quiet
    read-only run dressed as an exec one
  - scope and consult modes stay read-only without comment
  - the review prompt carries the evidence contract (CHECKED/FOUND/[UNTESTED]) and the
    obligations check for every seat, and the scratch-copy paragraph only for exec seats
  - nothing is registered in or written to the real repo; telemetry records exec and
    the [UNTESTED] count per seat; a short budget with exec on is announced

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

# Reports its sandbox mode, its cwd, and what that cwd contains; one [UNTESTED] tag
# gives telemetry's new counter something to count.
CODEX_STUB = r"""#!/usr/bin/env bash
out=""; cwd=""; sandbox=""; cfg=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift ;; -C) cwd="$2"; shift ;; --sandbox) sandbox="$2"; shift ;; -c) cfg="$cfg $2"; shift ;; esac
  shift
done
cat >/dev/null
{
  printf 'CODEX-STUB-REVIEW sandbox=[%s] cwd=[%s]\n' "$sandbox" "$cwd"
  printf 'tmpdir=[%s] cfg=[%s]\n' "${TMPDIR:-unset}" "$cfg"
  printf 'tracked=[%s] untracked=[%s] deps=[%s] gitdir=[%s]\n' \
    "$(cat "$cwd/tracked.txt" 2>/dev/null | tr -d '\n')" \
    "$([ -f "$cwd/new_module.py" ] && echo yes || echo no)" \
    "$([ -L "$cwd/node_modules" ] && echo link || echo none)" \
    "$(cd "$cwd" 2>/dev/null && git rev-parse --is-inside-work-tree 2>/dev/null)"
  printf 'gitkind=[%s] remotes=[%s] sub=[%s]\n' \
    "$([ -d "$cwd/.git" ] && echo dir || { [ -f "$cwd/.git" ] && echo file || echo none; })" \
    "$(cd "$cwd" 2>/dev/null && git remote 2>/dev/null | tr '\n' ' ')" \
    "$(cat "$cwd/sub/VERSION" 2>/dev/null)"
  printf '[HIGH] stub finding\n[UNTESTED] stub suspicion\n'
} >"$out"
"""

# Plays BOTH roles the panel asks of `claude`: the canary (prompt mentions CANARY) and
# the reviewer. Every invocation is appended to $HOME/claude_calls.log so a test can see
# how many times it was called and with what. The canary's answer is controlled by
# CLAUDE_STUB_CANARY: denied (default), wrote (and it really touches the file the prompt
# names, as an unfenced shell would), error, or silent.
CLAUDE_STUB = r"""#!/usr/bin/env bash
prompt="$(cat)"
printf 'ARGS: %s\nCWD: %s\n---\n' "$*" "$PWD" >>"$HOME/claude_calls.log"
case "$prompt" in
  *CANARY*)
    # The real command is: touch "<tree>" … && echo CANARY_WROTE; touch "<git>" … ;
    # echo CANARY_$(cat "<nonce file>"). Parse those three paths out of it.
    tree="$(printf '%s' "$prompt" | sed -n 's/.*touch "\([^"]*\)" 2>\/dev\/null && echo CANARY_WROTE; touch "\([^"]*\)".*/\1/p')"
    gitf="$(printf '%s' "$prompt" | sed -n 's/.*touch "\([^"]*\)" 2>\/dev\/null && echo CANARY_WROTE; touch "\([^"]*\)".*/\2/p')"
    npath="$(printf '%s' "$prompt" | sed -n 's/.*cat "\([^"]*\)".*/\1/p')"
    nonce="$(cat "$npath" 2>/dev/null)"
    printf 'CANARY tree=%s git=%s nonce=%s\n' "$tree" "$gitf" "$npath" >>"$HOME/claude_calls.log"
    case "${CLAUDE_STUB_CANARY:-denied}" in
      denied)   echo "CANARY_$nonce" ;;                                 # a working fence
      wrote)    : >"$tree"; echo CANARY_WROTE; echo "CANARY_$nonce" ;;  # an unfenced shell
      gitwrote) : >"$gitf"; echo CANARY_WROTE; echo "CANARY_$nonce" ;;  # .git left writable
      liar)     : >"$tree"; echo "CANARY_$nonce" ;;                     # wrote, reported nothing
      gitliar)  : >"$gitf"; echo "CANARY_$nonce" ;;
      quoted)   echo "CANARY_ERROR Bash failed: sandbox init error running bash -c 'touch ...; echo CANARY_\$(cat \"$npath\")'" ;;
      noshell)  echo "CANARY_DENIED" ;;                                 # composed without running: no nonce
      error)    echo "boom" >&2; exit 1 ;;
      silent)   ;;
    esac
    exit 0 ;;
esac
settings=""; tools=""; prev=""; intools=0
for a in "$@"; do
  [ "$prev" = "--settings" ] && settings="$a"
  case "$a" in --allowedTools) intools=1; prev="$a"; continue ;; --*) intools=0 ;; esac
  [ "$intools" = 1 ] && tools="$tools $a"
  prev="$a"
done
printf 'CLAUDE-STUB-REVIEW cwd=[%s]\n' "$PWD"
printf 'settings=[%s]\ntools=[%s]\n' "$settings" "$tools"
case "$prompt" in *"SCRATCH COPY"*) echo "ACCESS=exec" ;; *"READ-ONLY access"*) echo "ACCESS=ro" ;; *) echo "ACCESS=none" ;; esac
printf '[MED] claude stub finding\n'
"""

# Reports which contract markers its prompt file carries.
GROK_STUB = r"""#!/usr/bin/env bash
pf=""; prev=""
for a in "$@"; do [ "$prev" = "--prompt-file" ] && pf="$a"; prev="$a"; done
printf 'GROK-STUB-REVIEW\n'
[ -n "$pf" ] && printf 'MARKERS: %s\n' "$(grep -o -e 'CHECKED:' -e 'FOUND:' -e '\[UNTESTED\]' \
  -e "TASK's obligations" -e 'READ-ONLY access' -e 'SCRATCH COPY' "$pf" | sort -u | tr '\n' ' ')"
printf '[LOW] grok stub nit\n'
"""


class PanelExecTest(unittest.TestCase):
    def setUp(self):
        # resolve(): the panel reports cwd as the kernel sees it, and macOS /tmp is a symlink.
        self.tmp = str(Path(tempfile.mkdtemp()).resolve())
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._stub("codex", CODEX_STUB)
        self._stub("claude", CLAUDE_STUB)
        self._stub("grok", GROK_STUB)
        grok_home = Path(self.tmp) / ".grok"
        grok_home.mkdir()
        (grok_home / "auth.json").write_text("{}")
        self.artifact = Path(self.tmp) / "artifact.txt"
        self.artifact.write_text("diff --git a/tracked.txt b/tracked.txt\n-v1\n+v2\n")
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        (self.repo / "tracked.txt").write_text("v1")
        (self.repo / ".gitignore").write_text("node_modules/\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")
        # The three uncommitted states a scratch copy must carry.
        (self.repo / "tracked.txt").write_text("v2")              # tracked, edited
        (self.repo / "new_module.py").write_text("print('new')\n")  # untracked
        (self.repo / "node_modules").mkdir()                        # ignored dependency dir
        (self.repo / "node_modules" / "dep.js").write_text("x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)

    def _git(self, *args, cwd=None):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              cwd=cwd or self.repo, check=True, capture_output=True, text=True)

    def run_panel(self, *args, env=None, mode="review", cwd=None):
        e = dict(os.environ)
        e["PATH"] = f"{self.bin}:/usr/bin:/bin"
        e["HOME"] = self.tmp
        for var in ("ASSESS_PANEL_GROK", "ASSESS_PANEL_PI", "ASSESS_PANEL_EXEC", "GROK_HOME",
                    "XAI_API_KEY", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                    "ASSESS_PANEL_LOG", "CLAUDE_STUB_CANARY", "GIT_DIR", "GIT_WORK_TREE"):
            e.pop(var, None)
        e.setdefault("ASSESS_PANEL_LOG", "off")
        e.update(env or {})
        cmd = ["bash", str(PANEL), "--mode", mode, str(self.artifact), *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=e,
                              cwd=str(cwd or self.repo))

    def claude_calls(self):
        p = Path(self.tmp) / "claude_calls.log"
        return p.read_text().split("---\n")[:-1] if p.exists() else []

    def worktrees(self):
        return [l for l in self._git("worktree", "list").stdout.splitlines() if l.strip()]

    # --- on by default, and the copy is the artifact ------------------------

    def test_exec_is_on_by_default_in_scratch_copies(self):
        r = self.run_panel("the task")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("sandbox=[workspace-write]", r.stdout)
        self.assertNotIn(f"cwd=[{self.repo}]", r.stdout)
        # HEAD + the uncommitted edit + the untracked file + the linked dependency dir.
        self.assertIn("tracked=[v2] untracked=[yes] deps=[link] gitdir=[true]", r.stdout)
        self.assertIn("ACCESS=exec", r.stdout)
        self.assertIn('"enabled":true', r.stdout)
        self.assertIn(" Bash", r.stdout)
        self.assertIn("Edit(/", r.stdout)
        self.assertIn("exec in a scratch copy)", r.stdout)
        self.assertIn("worked in scratch copies", r.stdout)
        # No seat fell back and exec was not off; the only NOTE allowed here is the
        # budget one, which the 540s default earns with exec on.
        self.assertNotIn("ran READ-ONLY", r.stdout)
        self.assertNotIn("exec is OFF", r.stdout)

    def test_exec_seats_cannot_reach_the_panels_own_temp_or_each_other(self):
        # Each seat's writable set must not include the panel's working dir, where the
        # OTHER seat's output lands. Codex gets a private TMPDIR beside its scratch copy
        # and loses /tmp; the fresh Claude's sandbox pins its own TMPDIR and denies the
        # real temp root by itself (verified 2026-10-12), so only the network is made
        # explicit for it. Verified by probe, pinned here by the flags that encode it.
        r = self.run_panel("the task", env={"TMPDIR": self.tmp})
        tmpdir = r.stdout.split("tmpdir=[", 1)[1].split("]")[0]
        codex_cwd = r.stdout.split("CODEX-STUB-REVIEW sandbox=[workspace-write] cwd=[", 1)[1].split("]")[0]
        self.assertNotEqual(self.tmp, tmpdir)
        self.assertEqual(os.path.dirname(codex_cwd), os.path.dirname(tmpdir))   # a sibling of the scratch
        self.assertTrue(os.path.basename(tmpdir).startswith("tmp-codex"), tmpdir)
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", r.stdout)
        self.assertIn("sandbox_workspace_write.network_access=false", r.stdout)
        self.assertIn('"network":{"allowedDomains":[]}', r.stdout)

    def test_read_only_codex_keeps_the_callers_tmpdir_and_no_overrides(self):
        r = self.run_panel("the task", env={"ASSESS_PANEL_EXEC": "0", "TMPDIR": self.tmp})
        self.assertIn(f"tmpdir=[{self.tmp}] cfg=[", r.stdout)
        self.assertNotIn("exclude_slash_tmp", r.stdout)

    def test_scratch_copies_are_separate_and_not_the_repo(self):
        r = self.run_panel("the task")
        codex_cwd = r.stdout.split("cwd=[", 1)[1].split("]")[0]
        claude_cwd = r.stdout.split("CLAUDE-STUB-REVIEW cwd=[", 1)[1].split("]")[0]
        self.assertNotEqual(codex_cwd, claude_cwd)
        self.assertNotEqual(str(self.repo), codex_cwd)
        self.assertNotEqual(str(self.repo), claude_cwd)
        # The Edit rule is scoped to the fresh Claude's OWN copy, as an absolute path.
        self.assertIn(f"Edit(/{claude_cwd}/**)", r.stdout)
        self.assertIn(f'"allowWrite":["{claude_cwd}"]', r.stdout)
        # The real tree is untouched: still v2, still exactly one worktree registered.
        self.assertEqual("v2", (self.repo / "tracked.txt").read_text())
        self.assertEqual(1, len(self.worktrees()))

    # --- the canary --------------------------------------------------------

    def test_canary_runs_first_with_the_same_sandbox_settings(self):
        self.run_panel("the task")
        calls = self.claude_calls()
        self.assertEqual(2, len(calls), calls)
        canary, reviewer = calls
        self.assertIn("--settings", canary)
        self.assertIn('"enabled":true', canary)
        self.assertIn("--allowedTools Bash", canary)
        self.assertNotIn("Read", canary.split("--allowedTools", 1)[1])
        self.assertIn("--model haiku", canary)
        self.assertIn("--model opus", reviewer)
        # Both run in the scratch copy, not the repo.
        for c in calls:
            self.assertNotIn(f"CWD: {self.repo}\n", c)

    def test_canary_that_writes_falls_the_seat_back_loudly_and_leaves_nothing(self):
        r = self.run_panel("the task", env={"CLAUDE_STUB_CANARY": "wrote"})
        self.assertIn("could not be PROVEN", r.stdout)
        self.assertIn("CANARY_WROTE", r.stdout)
        self.assertIn("a file appeared in the real tree", r.stdout)
        self.assertIn("fails OPEN", r.stdout)
        self.assertIn("FRESH CLAUDE (clean context — no anchoring; read-only)", r.stdout)
        self.assertIn("ACCESS=ro", r.stdout)
        self.assertNotIn(" Bash", r.stdout.split("CLAUDE-STUB-REVIEW", 1)[1].split("ACCESS", 1)[0])
        self.assertIn(f"CLAUDE-STUB-REVIEW cwd=[{self.repo}]", r.stdout)
        # Codex's seat is independent of the canary.
        self.assertIn("sandbox=[workspace-write]", r.stdout)
        self.assertIn("exec in a scratch copy)", r.stdout)
        # The file the unfenced canary wrote into the repo is gone.
        self.assertEqual([], list(self.repo.glob(".assess_canary*")))

    def test_canary_error_or_silence_also_falls_back(self):
        for how in ("error", "silent"):
            r = self.run_panel("the task", env={"CLAUDE_STUB_CANARY": how})
            self.assertIn("could not be PROVEN", r.stdout, how)
            self.assertIn("ACCESS=ro", r.stdout, how)

    def test_canary_cannot_be_passed_by_text_alone(self):
        # The nonce lives on disk, not in the prompt: an error that quotes the command,
        # or an answer composed without running the tool, carries no nonce -> no seat.
        for how in ("quoted", "noshell"):
            r = self.run_panel("the task", env={"CLAUDE_STUB_CANARY": how})
            self.assertIn("the nonce never came back", r.stdout, how)
            self.assertIn("ACCESS=ro", r.stdout, how)
            (Path(self.tmp) / "claude_calls.log").unlink()

    def test_canary_checks_the_files_not_just_the_words(self):
        # A shell that wrote and said nothing about it (the nonce came back fine) is
        # caught by the file it left — in the real tree, or in the real .git, the
        # directory a worktree-based copy once left writable.
        for how, where in (("liar", "a file appeared in the real tree"),
                           ("gitliar", "a file appeared in the real .git"),
                           ("gitwrote", "a file appeared in the real .git")):
            r = self.run_panel("the task", env={"CLAUDE_STUB_CANARY": how})
            self.assertIn(where, r.stdout, how)
            self.assertIn("ACCESS=ro", r.stdout, how)
            self.assertEqual([], list(self.repo.glob(".assess_canary*")), how)
            self.assertEqual([], list((self.repo / ".git").glob(".assess_canary*")), how)
            (Path(self.tmp) / "claude_calls.log").unlink()

    def test_canary_targets_the_real_tree_and_the_real_git_dir(self):
        self.run_panel("the task")
        log = (Path(self.tmp) / "claude_calls.log").read_text()
        line = [l for l in log.splitlines() if l.startswith("CANARY ")][0]
        self.assertIn(f"tree={self.repo}/.assess_canary.", line)
        self.assertIn(f"git={self.repo}/.git/.assess_canary.", line)
        # The nonce file lives beside the copies, never in the panel's working dir.
        nonce_path = line.split("nonce=", 1)[1]
        self.assertTrue(nonce_path.endswith("/canary.nonce"), nonce_path)
        self.assertNotIn("/.assess_panel.", nonce_path)

    # --- the copy is a clone, built to survive the user's git configuration ---------

    def test_scratch_is_a_self_contained_clone_with_no_remote(self):
        r = self.run_panel("the task")
        self.assertIn("gitkind=[dir] remotes=[] ", r.stdout)
        self.assertEqual(1, len(self.worktrees()))   # nothing registered in the real repo

    def test_populated_submodule_working_tree_is_copied_with_its_edits(self):
        src = Path(self.tmp) / "subsrc"
        src.mkdir()
        self._git("init", "-q", cwd=src)
        (src / "VERSION").write_text("1")
        self._git("add", "-A", cwd=src)
        self._git("commit", "-q", "-m", "sub", cwd=src)
        self._git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "sub")
        self._git("commit", "-q", "-m", "add sub")
        (self.repo / "sub" / "VERSION").write_text("2")   # uncommitted, inside the submodule
        r = self.run_panel("the task")
        self.assertIn("sub=[2]", r.stdout)
        self.assertIn("exec in a scratch copy)", r.stdout)
        self.assertNotIn("could not be built", r.stdout)

    def test_diff_survives_noprefix_and_mnemonic_prefix_config(self):
        self._git("config", "diff.noprefix", "true")
        self._git("config", "diff.mnemonicPrefix", "true")
        r = self.run_panel("the task")
        self.assertIn("tracked=[v2]", r.stdout)
        self.assertNotIn("could not be built", r.stdout)

    def test_unreadable_untracked_file_falls_back_loudly(self):
        if os.geteuid() == 0:
            self.skipTest("root can read anything")
        secret = self.repo / "secret.txt"
        secret.write_text("x")
        secret.chmod(0)
        try:
            r = self.run_panel("the task")
        finally:
            secret.chmod(0o644)
        self.assertEqual(2, r.stdout.count("copying the untracked files failed"))
        self.assertIn("sandbox=[read-only]", r.stdout)

    def test_users_checkout_hooks_do_not_run(self):
        hooks = Path(self.tmp) / "hooks"
        hooks.mkdir()
        (hooks / "post-checkout").write_text("#!/usr/bin/env bash\necho ran >>\"$HOME/hook.log\"\n")
        (hooks / "post-checkout").chmod(0o755)
        r = self.run_panel("the task", env={"GIT_CONFIG_COUNT": "1",
                                            "GIT_CONFIG_KEY_0": "core.hooksPath",
                                            "GIT_CONFIG_VALUE_0": str(hooks)})
        self.assertIn("exec in a scratch copy)", r.stdout)
        self.assertFalse((Path(self.tmp) / "hook.log").exists())

    def test_short_budget_with_exec_is_announced(self):
        # The number printed is the budget AFTER the preflight was charged, so match the
        # shape, not the figure.
        r = self.run_panel("the task", env={"ASSESS_PANEL_TIMEOUT": "100"})
        self.assertRegex(r.stdout, r"exec seats on a \d+s budget")
        r = self.run_panel("the task", env={"ASSESS_PANEL_TIMEOUT": "1500"})
        self.assertNotIn("budget", r.stdout)
        r = self.run_panel("the task", env={"ASSESS_PANEL_TIMEOUT": "100", "ASSESS_PANEL_EXEC": "0"})
        self.assertNotIn("budget", r.stdout)

    # --- every way exec is unavailable ---------------------------------------

    def test_explicit_off_is_silent_and_read_only(self):
        r = self.run_panel("the task", env={"ASSESS_PANEL_EXEC": "0"})
        self.assertIn(f"sandbox=[read-only] cwd=[{self.repo}]", r.stdout)
        self.assertIn(f"CLAUDE-STUB-REVIEW cwd=[{self.repo}]", r.stdout)
        self.assertIn("settings=[]", r.stdout)
        self.assertIn("ACCESS=ro", r.stdout)
        self.assertIn("different lineage; read-only)", r.stdout)
        self.assertNotIn(">>> NOTE", r.stdout)
        self.assertNotIn("scratch copies", r.stdout)
        self.assertEqual(1, len(self.claude_calls()))   # no canary
        self.assertEqual(1, len(self.worktrees()))

    def test_non_git_root_is_loud_and_read_only(self):
        plain = Path(self.tmp) / "plain"
        plain.mkdir()
        r = self.run_panel("the task", cwd=plain)
        self.assertIn(">>> NOTE — exec is OFF for this run", r.stdout)
        self.assertIn("not inside a git repository", r.stdout)
        self.assertIn("sandbox=[read-only]", r.stdout)
        self.assertIn("ACCESS=ro", r.stdout)
        self.assertEqual(1, len(self.claude_calls()))

    def test_repo_without_commits_is_loud(self):
        empty = Path(self.tmp) / "empty"
        empty.mkdir()
        self._git("init", "-q", cwd=empty)
        r = self.run_panel("the task", cwd=empty)
        self.assertIn("no commits yet", r.stdout)
        self.assertIn("sandbox=[read-only]", r.stdout)

    def test_unrecognised_flag_value_is_loud_not_a_guess(self):
        r = self.run_panel("the task", env={"ASSESS_PANEL_EXEC": "maybe"})
        self.assertIn("not a recognised on/off value", r.stdout)
        self.assertIn("sandbox=[read-only]", r.stdout)

    def test_untracked_size_cap_falls_the_seats_back_per_seat(self):
        r = self.run_panel("the task", env={"ASSESS_PANEL_EXEC_MAX_MB": "0"})
        self.assertEqual(2, r.stdout.count("ran READ-ONLY: its scratch copy could not be built"))
        self.assertIn("ASSESS_PANEL_EXEC_MAX_MB=0 cap", r.stdout)
        self.assertIn("sandbox=[read-only]", r.stdout)
        self.assertIn("ACCESS=ro", r.stdout)
        self.assertEqual(1, len(self.worktrees()))   # the failed builds were cleaned up

    def test_scope_and_consult_stay_read_only_without_comment(self):
        for mode in ("scope", "consult"):
            r = self.run_panel("the request or problem", mode=mode)
            self.assertEqual(0, r.returncode, (mode, r.stderr))
            self.assertIn(f"sandbox=[read-only] cwd=[{self.repo}]", r.stdout, mode)
            self.assertNotIn("exec is OFF", r.stdout, mode)
            self.assertNotIn("scratch", r.stdout.lower(), mode)
            self.assertEqual(1, len(self.claude_calls()), mode)
            (Path(self.tmp) / "claude_calls.log").unlink()

    # --- the evidence contract -------------------------------------------------

    def test_every_seat_gets_the_contract_and_only_exec_seats_the_scratch_paragraph(self):
        r = self.run_panel("the task")
        markers = r.stdout.split("MARKERS: ", 1)[1].splitlines()[0]
        for m in ("CHECKED:", "FOUND:", "[UNTESTED]", "TASK's obligations", "READ-ONLY access"):
            self.assertIn(m, markers)
        self.assertNotIn("SCRATCH COPY", markers)      # grok is read-only by design
        self.assertIn("ACCESS=exec", r.stdout)         # the fresh Claude got the scratch paragraph
        # And the footer tells the caller how to treat both.
        self.assertIn("repeat the CHECKED step in the real tree", r.stdout)
        self.assertIn("[UNTESTED] items are leads, not findings", r.stdout)

    # --- telemetry ---------------------------------------------------------------

    def test_telemetry_records_exec_and_untested_per_seat(self):
        log = Path(self.tmp) / "log.jsonl"
        self.run_panel("the task", env={"ASSESS_PANEL_LOG": str(log)})
        rec = json.loads(log.read_text().splitlines()[0])
        self.assertIs(True, rec["reviewers"]["codex"]["exec"])
        self.assertIs(True, rec["reviewers"]["claude"]["exec"])
        self.assertNotIn("exec", rec["reviewers"]["grok"])
        self.assertEqual(1, rec["reviewers"]["codex"]["untested"])
        self.assertEqual(0, rec["reviewers"]["claude"]["untested"])

    def test_telemetry_exec_false_when_the_seat_fell_back(self):
        log = Path(self.tmp) / "log.jsonl"
        self.run_panel("the task", env={"ASSESS_PANEL_LOG": str(log), "CLAUDE_STUB_CANARY": "wrote"})
        rec = json.loads(log.read_text().splitlines()[0])
        self.assertIs(True, rec["reviewers"]["codex"]["exec"])
        self.assertIs(False, rec["reviewers"]["claude"]["exec"])

    def test_telemetry_has_no_exec_key_outside_review_mode(self):
        log = Path(self.tmp) / "log.jsonl"
        self.run_panel("the request", mode="scope", env={"ASSESS_PANEL_LOG": str(log)})
        rec = json.loads(log.read_text().splitlines()[0])
        self.assertNotIn("exec", rec["reviewers"]["codex"])
        self.assertNotIn("exec", rec["reviewers"]["claude"])


if __name__ == "__main__":
    unittest.main()
