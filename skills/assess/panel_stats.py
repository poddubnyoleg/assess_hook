#!/usr/bin/env python3
"""panel_stats.py — the caller-facing half of the panel's telemetry.

panel.sh appends one `run` record per panel: who was asked, who answered (the
same ok/empty/failed/timeout/killed/skipped taxonomy its own report() prints),
how long each took, and how many findings each tagged at which severity. That
half is objective but cannot say whether any finding was RIGHT — reconciliation
happens in the caller's session, after the panel has exited. This script closes
the loop:

  verdict <run-id>   Append the caller's reconciliation: one entry per finding,
                     naming every voice that raised it (codex/claude/grok/pi/self)
                     and whether it survived checking. Reads JSON on stdin;
                     `verdict --schema` prints the exact shape. Re-logging the
                     same run id replaces the earlier verdicts (last wins), so
                     a correction is one more call, not an edit of the log.
  report             Aggregate both halves: per-reviewer reliability, latency,
                     findings volume, and — where verdicts exist — precision,
                     unique confirmed catches, and confirmed catches the
                     session's own self-review missed.
  runs               List recent runs with their ids, for finding the one a
                     verdict call needs.

The log lives at ~/.claude/assess-panel/log.jsonl (override: --log, then
ASSESS_PANEL_LOG; the off-values that disable WRITING in panel.sh are treated
as unset here, because reading history is a different act from recording more).

A caveat that belongs in every reading of the report: verdicts are judged by
the same session whose work was reviewed. "Precision" therefore means
"survived the caller's checking", not ground truth — which is still exactly
what is needed to compare reviewers against each other.
"""
import argparse
import json
import os
import statistics
import sys
import time

REVIEWERS = ("codex", "claude", "grok", "pi")
SOURCES = REVIEWERS + ("self",)
VERDICTS = ("confirmed", "refuted", "unverifiable")
SEVERITIES = ("HIGH", "MED", "LOW")
ACTIONS = ("fixed", "deferred", "dismissed", "none")
FINDING_KEYS = {"summary", "sources", "verdict", "severity", "action"}
STATUSES = ("ok", "empty", "failed", "timeout", "killed", "skipped")

SCHEMA = """\
Reads on stdin: {"findings": [<finding>, ...], "self_clean": <bool, optional>}
(a bare JSON list of findings also works)

<finding> = {
  "summary":  "one line naming the finding" (<=300 chars —        REQUIRED
              name it, don't paste the artifact into the log)
  "sources":  ["codex"]                                           REQUIRED
              every voice that INDEPENDENTLY raised it, from:
              codex | claude | grok | pi | self
              ("self" is the session's own self-review; a finding
               only the session raised is ["self"]. A reviewer is
               creditable if its review text existed — including
               partial output printed before a timeout/crash)
  "verdict":  "confirmed" | "refuted" | "unverifiable"            REQUIRED
              for a REVIEW run: confirmed = checked against the
              work and real; refuted = checked and wrong;
              unverifiable = could not be settled either way.
              For a SCOPE run there is no artifact to check, so:
              confirmed = it changed your reading or went to the
              user as a question; refuted = noise.
              For a CONSULT run the findings are suggestions, so:
              confirmed = acted on it and it unblocked the work
              or changed the approach; refuted = tried it, dead
              end; unverifiable = untested
  "severity": "HIGH" | "MED" | "LOW"                              optional
  "action":   "fixed" | "deferred" | "dismissed" | "none"         optional
              (those four literal values only; null = absent; omit
               the key rather than inventing a synonym)
}

"self_clean": true marks that the session's own self-review COMPLETED and
found nothing — different from not logging it, and without the marker those
two are indistinguishable, which inflates the beyond-self metric.

Log the REFUTED findings too — they are the precision signal; logging only
what was confirmed flatters every reviewer equally and measures nothing.
An empty list ({"findings": []}) is itself signal: the panel ran and nothing
needed reconciling. Submit EVERYTHING in one call: a second call for the
same run REPLACES the first (last wins), it does not add to it.
"""


def die(msg):
    sys.stderr.write("panel_stats: " + msg + "\n")
    sys.exit(2)


def resolve_log(args):
    if getattr(args, "log", None):
        return args.log
    env = os.environ.get("ASSESS_PANEL_LOG", "")
    if env and env not in ("0", "off", "false", "no"):
        return env
    return os.path.expanduser("~/.claude/assess-panel/log.jsonl")


def read_log(path):
    """Parse the JSONL log, skipping unparseable lines (a torn concurrent
    append must cost one record, never the whole report)."""
    records = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    records.append(r)
    except OSError as e:
        die(f"cannot read the log at {path}: {e}")
    return records


def split_log(records):
    """Return (runs, verdicts): id->record each, later records winning.

    A verdicts record without a findings LIST is dropped here — it parses as JSON,
    so read_log keeps it, but every consumer indexes into findings; one hand-edited
    or future-schema record must cost one record, never the whole report."""
    runs, verdicts = {}, {}
    for r in records:
        if r.get("t") == "run" and r.get("id"):
            runs[r["id"]] = r
        elif (r.get("t") == "verdicts" and r.get("id")
              and isinstance(r.get("findings"), list)):
            verdicts[r["id"]] = r
    return runs, verdicts


def produced_text(info):
    """Did this reviewer produce review text — findings someone could reconcile?
    ok did; a timed-out/killed/failed reviewer did IF partial output survived
    (panel.sh prints it and the skill says it is worth reading); empty and
    skipped did not."""
    if info.get("status") == "ok":
        return True
    if info.get("status") in ("timeout", "killed", "failed"):
        return info.get("out_bytes", 0) > 0
    return False


def delivered_judgement(info):
    """Did this reviewer deliver ANY judgement, including 'no findings'? This is
    the denominator for per-run rates: an empty review is an opportunity that
    yielded zero catches, and excluding it would overstate the reviewer's rate."""
    return info.get("status") == "empty" or produced_text(info)


# --- verdict -----------------------------------------------------------------

def validate_finding(f, i):
    tag = f"finding {i}"
    if not isinstance(f, dict):
        return [f"{tag}: not an object"]
    errs = []
    unknown = set(f) - FINDING_KEYS
    if unknown:
        errs.append(f"{tag}: unknown key(s) {sorted(unknown)} — allowed: "
                    f"{sorted(FINDING_KEYS)} (a typo here would silently drop data)")
    if not (isinstance(f.get("summary"), str) and f["summary"].strip()):
        errs.append(f"{tag}: 'summary' must be a non-empty string")
    elif len(f["summary"]) > 300:
        # Refused, not truncated: the run half caps its one free-text field for the
        # same reason, and a silent cut would hide that data was dropped. This is the
        # only place artifact text could leak into the long-lived log — a summary
        # that needs 300+ chars is quoting the artifact, not naming the finding.
        errs.append(f"{tag}: 'summary' is {len(f['summary'])} chars — keep it to one "
                    "line (<=300); name the finding, don't paste the artifact")
    src = f.get("sources")
    if not (isinstance(src, list) and src and all(s in SOURCES for s in src)):
        errs.append(f"{tag}: 'sources' must be a non-empty list drawn from {list(SOURCES)}")
    if f.get("verdict") not in VERDICTS:
        errs.append(f"{tag}: 'verdict' must be one of {list(VERDICTS)}")
    if "severity" in f and f["severity"] not in SEVERITIES:
        errs.append(f"{tag}: 'severity' is {f['severity']!r} — when given, must be "
                    f"one of {list(SEVERITIES)}")
    if "action" in f and f["action"] not in ACTIONS:
        errs.append(f"{tag}: 'action' is {f['action']!r} — when given, must be "
                    f"one of {list(ACTIONS)}")
    return errs


def strip_null_optionals(f):
    """A JSON-emitting caller routinely writes null for an optional field it has no
    value for; treat that as the field being absent rather than failing the batch."""
    if not isinstance(f, dict):
        return f
    return {k: v for k, v in f.items()
            if not (k in ("severity", "action") and v is None)}


def cmd_verdict(args):
    if args.schema:
        print(SCHEMA, end="")
        return
    if not args.run_id:
        die("verdict needs a run id (panel.sh prints it as 'Panel run id: ...'), "
            "or --schema for the input shape")
    log = resolve_log(args)
    records = read_log(log)
    runs, _ = split_log(records)
    if args.run_id not in runs:
        recent = ", ".join(sorted(runs)[-5:]) or "(none)"
        die(f"run id '{args.run_id}' is not in {log} — verdicts must attach to a "
            f"logged run, and ids are NOT substitutable: if your panel printed no "
            f"run id, telemetry was off for that run, so skip verdict logging. "
            f"Recent ids (only for spotting a typo in YOURS): {recent}")
    # Only voices whose review text existed in this run may be credited: skipped and
    # empty reviewers produced no findings to raise, so crediting them would be a
    # misremembered attribution poisoning every relevance metric. A timed-out/failed
    # reviewer CAN be credited when partial output survived — panel.sh prints it and
    # the skill calls it worth reconciling; refusing it would force the caller to
    # either drop the attribution (hiding that the slowest reviewer still produced a
    # real catch) or mislabel it "self" (poisoning beyond-self the other way).
    creditable = {n for n, i in runs[args.run_id].get("reviewers", {}).items()
                  if produced_text(i)}
    allowed_sources = creditable | {"self"}
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError as e:
        die(f"stdin is not valid JSON: {e}")
    if isinstance(data, list):
        findings = data
    elif isinstance(data, dict) and isinstance(data.get("findings"), list):
        findings = data["findings"]
    else:
        die('expected {"findings": [...]} (or a bare list) on stdin — '
            'run `verdict --schema` for the shape')
    self_clean = None
    if isinstance(data, dict) and "self_clean" in data:
        if not isinstance(data["self_clean"], bool):
            die("'self_clean' must be a boolean — it marks that the self-review "
                "COMPLETED and found nothing, which is different from not logging it")
        self_clean = data["self_clean"]
    findings = [strip_null_optionals(f) for f in findings]
    errs = [e for i, f in enumerate(findings) for e in validate_finding(f, i)]
    if errs:
        die("invalid findings, nothing logged:\n  " + "\n  ".join(errs))
    bad = sorted({s for f in findings for s in f.get("sources", [])} - allowed_sources)
    if bad:
        die(f"sources {bad} cannot be credited for run {args.run_id}: only voices "
            f"whose review text existed in that run may appear "
            f"(creditable: {sorted(creditable) or '(none)'}, plus 'self').")
    rec = {"t": "verdicts", "id": args.run_id, "ts": int(time.time()),
           "findings": findings}
    if self_clean is not None:
        rec["self_clean"] = self_clean
    # Last-wins is the correction path, but it must never be SILENT: a caller who
    # splits one reconciliation across two calls would otherwise wipe the first half
    # and read "logged 1 verdict(s)" as success.
    prior = sum(len(r["findings"]) for r in records
                if r.get("t") == "verdicts" and r.get("id") == args.run_id
                and isinstance(r.get("findings"), list))
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(rec, separators=(",", ":")) + "\n").encode())
    finally:
        os.close(fd)
    print(f"logged {len(findings)} verdict(s) for run {args.run_id}")
    if prior:
        print(f"note: this REPLACES {prior} verdict(s) previously logged for this run "
              "(last call wins) — submit every finding, self included, in ONE call")


# --- report ------------------------------------------------------------------

def fmt_row(cols, widths):
    return "  ".join(str(c).rjust(w) if i else str(c).ljust(w)
                     for i, (c, w) in enumerate(zip(cols, widths)))


def cmd_report(args):
    log = resolve_log(args)
    runs, verdicts = split_log(read_log(log))
    if args.mode:
        runs = {i: r for i, r in runs.items() if r.get("mode") == args.mode}
        verdicts = {i: v for i, v in verdicts.items() if i in runs}
    if not runs:
        die(f"no runs in {log}" + (f" with mode={args.mode}" if args.mode else "") +
            " — run the panel first (or point --log at the right file)")

    by_mode = {}
    for r in runs.values():
        by_mode[r.get("mode", "?")] = by_mode.get(r.get("mode", "?"), 0) + 1
    mode_txt = ", ".join(f"{m} {n}" for m, n in sorted(by_mode.items()))
    print(f"Panel telemetry — {len(runs)} run(s) ({mode_txt})  ·  log: {log}")

    # Reliability & latency: every run the reviewer appeared in (pi absent = off).
    print("\nReliability & latency")
    widths = (8, 6, 5, 6, 7, 8, 7, 8, 9)
    print(fmt_row(("reviewer", "asked", "ok", "empty", "failed", "timeout",
                   "killed", "skipped", "median_s"), widths))
    skip_reasons = {}
    for name in REVIEWERS:
        infos = [r["reviewers"][name] for r in runs.values()
                 if name in r.get("reviewers", {})]
        if not infos:
            continue
        counts = {s: sum(1 for i in infos if i.get("status") == s) for s in STATUSES}
        durs = [i["dur_s"] for i in infos if i.get("status") == "ok" and "dur_s" in i]
        med = round(statistics.median(durs)) if durs else "—"
        print(fmt_row((name, len(infos), counts["ok"], counts["empty"],
                       counts["failed"], counts["timeout"], counts["killed"],
                       counts["skipped"], med), widths))
        for i in infos:
            if i.get("skip"):
                key = (name, i["skip"][:70])
                skip_reasons[key] = skip_reasons.get(key, 0) + 1
    if skip_reasons:
        print("  skip reasons:")
        for (name, reason), n in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {name}: {reason}… ({n})")

    # Findings volume — review mode only; scope output carries no severity tags.
    # med_kb (median delivered output) is the sanity column: a reviewer that wrote a
    # full prose review with off-spec tags shows 0.0 per-run but a real med_kb, so
    # "no tags" and "no findings" stay distinguishable.
    review_runs = [r for r in runs.values() if r.get("mode") == "review"]
    if review_runs:
        # UNTESTED is the reviewer's own admission that a claim was not checked (it
        # replaces the severity tag), so it is NOT in per-run; exec counts the runs in
        # which the seat could execute, so volume can later be read against access.
        print("\nFindings volume (review runs, as tagged by the reviewer)")
        widths = (8, 5, 5, 5, 5, 9, 8, 7, 5)
        print(fmt_row(("reviewer", "runs", "HIGH", "MED", "LOW", "UNTESTED", "per-run",
                       "med_kb", "exec"), widths))
        for name in REVIEWERS:
            infos = [r["reviewers"][name] for r in review_runs
                     if r.get("reviewers", {}).get(name, {}).get("status") in ("ok", "empty")]
            if not infos:
                continue
            h = sum(i.get("high", 0) for i in infos)
            m = sum(i.get("med", 0) for i in infos)
            l = sum(i.get("low", 0) for i in infos)
            u = sum(i.get("untested", 0) for i in infos)
            ex = sum(1 for i in infos if i.get("exec"))
            kbs = [i["out_bytes"] / 1024 for i in infos if "out_bytes" in i]
            med_kb = f"{statistics.median(kbs):.1f}" if kbs else "—"
            print(fmt_row((name, len(infos), h, m, l, u,
                           f"{(h + m + l) / len(infos):.1f}", med_kb, ex), widths))

    # Relevance — split by mode, never pooled: the modes' verdicts answer different
    # questions (review: "was the finding real"; scope: "did it change the reading";
    # consult: "did acting on it unblock"), and scope/consult hand each reviewer its
    # own lens, so their findings are single-source BY DESIGN — pooled, each would
    # read as a unique catch and the precision column would average incommensurable
    # signals. A mode missing from this tuple would be counted in the header and then
    # silently dropped here — the one silent degradation this report must not have —
    # so every mode panel.sh accepts needs an entry.
    for mname, conf_means, prec_label in (
            ("review", "checked and real", "precision"),
            ("scope", "changed the reading / went to the user", "useful"),
            ("consult", "acted on and it unblocked or changed the approach", "useful")):
        sub = {i: r for i, r in runs.items() if r.get("mode") == mname}
        if not sub:
            continue
        judged = {i: verdicts[i] for i in sub if i in verdicts}
        print(f"\nRelevance — {mname} ({len(judged)} of {len(sub)} run(s) have "
              f"verdicts; confirmed = {conf_means})")
        if not judged:
            print("  none yet — after reconciling a panel, log verdicts with the "
                  "command its footer printed")
            continue
        findings = [f for v in judged.values() for f in v["findings"]
                    if isinstance(f, dict)]
        # "runs" is the denominator the raw counts need: how many verdict-logged runs
        # the source delivered a judgement in — ok, empty ("no findings" is a
        # judgement), or partial output. Without it, an opt-in or often-skipped
        # reviewer with one catch per run reads worse than an always-on one with the
        # same rate; with empties excluded, a reviewer that honestly returns empty
        # would have its rate computed only over the runs where it spoke.
        review_mode = mname == "review"
        if review_mode:
            widths = (8, 5, 7, 7, 10, 8, 8, 10, 7, 12, 11)
            print(fmt_row(("source", "runs", "judged", "tagged", "confirmed",
                           "refuted", "unverif", prec_label, "unique",
                           "beyond-self", "beyond/run"), widths))
        else:
            widths = (8, 5, 7, 10, 8, 8, 10)
            print(fmt_row(("source", "runs", "judged", "confirmed", "refuted",
                           "unverif", prec_label), widths))
        for name in SOURCES:
            mine = [f for f in findings if name in (f.get("sources") or [])]
            opp = (len(judged) if name == "self" else
                   sum(1 for i in judged
                       if delivered_judgement(sub[i].get("reviewers", {}).get(name, {}))))
            if not mine and not opp:
                continue
            conf = sum(1 for f in mine if f.get("verdict") == "confirmed")
            refu = sum(1 for f in mine if f.get("verdict") == "refuted")
            unv = sum(1 for f in mine if f.get("verdict") == "unverifiable")
            # Precision over the SETTLED findings only: an unverifiable one is a fact
            # about the artifact's checkability, not about the reviewer's judgement.
            prec = f"{100 * conf / (conf + refu):.0f}%" if conf + refu else "—"
            if review_mode:
                # tagged: what the reviewer PUT UP in these runs, per its own severity
                # tags. Beside `judged` it exposes flooding — the main way a panelist
                # fails to earn its slot: precision alone scores only the findings the
                # caller chose to reconcile, so a reviewer tagging 12 and getting 4
                # judged would otherwise score on its best 4.
                if name == "self":
                    tagged = "—"
                else:
                    tagged = sum(sub[i].get("reviewers", {}).get(name, {}).get(k, 0)
                                 for i in judged for k in ("high", "med", "low"))
                uniq = sum(1 for f in mine if f.get("verdict") == "confirmed"
                           and f.get("sources") == [name])
                if name == "self":
                    beyond, rate = "—", "—"
                else:
                    beyond = sum(1 for f in mine if f.get("verdict") == "confirmed"
                                 and "self" not in (f.get("sources") or []))
                    rate = f"{beyond / opp:.2f}" if opp else "—"
                print(fmt_row((name, opp, len(mine), tagged, conf, refu, unv, prec,
                               uniq, beyond, rate), widths))
            else:
                print(fmt_row((name, opp, len(mine), conf, refu, unv, prec), widths))
        if review_mode:
            catch = sum(1 for v in judged.values()
                        if any(f.get("verdict") == "confirmed"
                               and "self" not in (f.get("sources") or [])
                               for f in v["findings"] if isinstance(f, dict)))
            # A run counts as self-covered when self findings were logged OR the
            # caller explicitly marked the self-review clean — without the marker, a
            # compliant clean run is indistinguishable from a forgotten one and
            # coverage would read 0 on exactly the runs that did everything right.
            selfruns = sum(1 for v in judged.values()
                           if v.get("self_clean") is True
                           or any("self" in (f.get("sources") or [])
                                  for f in v["findings"] if isinstance(f, dict)))
            print(f"\n  Panel value: {catch} of {len(judged)} verdict-logged review "
                  "run(s) had >=1 confirmed finding the session's own self-review "
                  "missed.")
            print(f"  Self coverage: {selfruns} of {len(judged)} of those run(s) "
                  "logged `self` findings or `\"self_clean\": true` — a run with "
                  "neither reads as \"self-review found nothing\"; if it just wasn't "
                  "logged, beyond-self is inflated.")
    print("\nCaveat: verdicts are judged by the same session that did the work — read "
          "precision as\n\"survived the caller's checking\", not ground truth.")


# --- runs --------------------------------------------------------------------

def cmd_runs(args):
    log = resolve_log(args)
    runs, verdicts = split_log(read_log(log))
    if not runs:
        die(f"no runs in {log}")
    for rid in sorted(runs)[-args.last:]:
        r = runs[rid]
        revs = []
        for name in REVIEWERS:
            info = r.get("reviewers", {}).get(name)
            if not info:
                continue
            part = f"{name}:{info.get('status', '?')}"
            if "dur_s" in info:
                part += f"({info['dur_s']}s)"
            revs.append(part)
        print(f"{rid}  {r.get('mode', '?'):7}  {' '.join(revs)}  "
              f"verdicts:{'yes' if rid in verdicts else 'no'}")


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log", help="path to the JSONL log (default: $ASSESS_PANEL_LOG "
                                 "or ~/.claude/assess-panel/log.jsonl)")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verdict", help="append reconciliation verdicts for a run")
    v.add_argument("run_id", nargs="?", help="the id panel.sh printed")
    v.add_argument("--schema", action="store_true", help="print the stdin shape and exit")
    v.set_defaults(fn=cmd_verdict)
    r = sub.add_parser("report", help="aggregate reliability + relevance per reviewer")
    r.add_argument("--mode", choices=("review", "scope", "consult"))
    r.set_defaults(fn=cmd_report)
    ls = sub.add_parser("runs", help="list recent runs and their ids")
    ls.add_argument("--last", type=int, default=10)
    ls.set_defaults(fn=cmd_runs)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
