#!/usr/bin/env python3
"""Check a validation run of scripts/campaign.py, and compare it with a reference run.

A validation plan runs one command of every shape of the full campaign on a new machine. Before
that machine may measure the paper, this checks what the run recorded and exits non-zero on any
problem:

  * the ledger: every command done, none failed, and the self-test interruption really killed a
    command that had written rows (a kill before any row would have tested nothing);
  * every provenance line: one host, one JVM, heap 24g, a clean tree, and --timeout 90 in the
    command;
  * no key written twice;
  * against a reference run of the same plan on another machine, every deterministic column
    (counts, bounds, statuses) of every row both runs share, trial 0 only -- the number of trials
    may differ because --repeats-min-seconds depends on speed. The denominator is printed: a
    comparison over zero rows is reported as such, never as a pass.

    python3 analysis/check_validation.py results-probe/windows-validation --reference results-probe/mac-validation
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
KEYS = ["Algorithm", "UBArm", "Dataset", "MinUtil", "mu", "DeltaRatio", "Schedule", "BatchID", "RunIndex", "MemMode"]
#: Columns fixed by (code, data, parameters): they must agree across machines and JVMs.
DETERMINISTIC = ["Status", "HAUSP", "SHAUS", "Cand", "Recursed", "PrunedL1(SWU)", "PrunedL2(IAUUB)",
                 "PrunedL3(MFUUB)", "PrunedL3Node", "PrunedL1Root", "TightnessPEAU", "TightnessIAUUB",
                 "TightnessMFUUB", "TotalDBUtil", "CumulativeDBSize", "RescanTriggered", "BufferTested",
                 "SafetyBound", "PoolBorrows", "PoolReuses", "PoolPeakLive", "AudulActive",
                 "HAUSP_EHAUSM-R", "HAUSP_HAUSP-UB"]
HEAP, LIMIT = "24g", "90"


def read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, comment="#", dtype=str, keep_default_na=False)


def provenance(run_dir: Path) -> tuple[list[dict], list[str]]:
    lines, problems = [], []
    for f in sorted(run_dir.rglob("*.csv")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# run_id="):
                fields = dict(re.findall(r"(\w+)=(\S+)", line.split(" cmd=")[0]))
                fields["cmd"] = line.split(" cmd=", 1)[1] if " cmd=" in line else ""
                fields["file"] = f.relative_to(run_dir).as_posix()
                lines.append(fields)
    for p in lines:
        if p.get("heap") != HEAP:
            problems.append(f"{p['file']} run {p.get('run_id')}: heap {p.get('heap')}, expected {HEAP}")
        if p.get("tree") != "clean":
            problems.append(f"{p['file']} run {p.get('run_id')}: tree {p.get('tree')}")
        if f"--timeout {LIMIT}" not in p["cmd"]:
            problems.append(f"{p['file']} run {p.get('run_id')}: no '--timeout {LIMIT}' in {p['cmd']!r}")
    for field in ("host", "jvm", "git"):
        values = sorted({p.get(field) for p in lines})
        if len(values) > 1:
            problems.append(f"{len(values)} different {field} values: {values}")
    return lines, problems


def ledger(run_dir: Path) -> tuple[dict, list[str]]:
    files = sorted(run_dir.glob("campaign-*.ledger.jsonl"))
    if len(files) != 1:
        return {}, [f"expected one ledger in {run_dir}, found {len(files)}"]
    events = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    done = {e["id"] for e in events if e["event"] == "done"}
    failed = [e for e in events if e["event"] == "failed"]
    selftests = [e for e in events if e["event"] == "rolled_back" and e.get("reason") == "self-test"]
    started = {e["id"] for e in events if e["event"] == "start"}
    problems = [f"command {e['id']} failed: {e.get('problems') or e.get('rc')}" for e in failed]
    if started - done:
        problems.append(f"commands started but not done: {sorted(started - done)}")
    if not selftests:
        problems.append("no self-test interruption recorded")
    for e in selftests:
        if not e.get("killed") or not e.get("files_grown"):
            problems.append(f"self-test of command {e['id']} tested nothing (killed={e.get('killed')}, "
                            f"files grown={e.get('files_grown')})")
    session = next((e for e in events if e["event"] == "session"), {})
    walls = {e["id"]: e.get("wall_s") for e in events if e["event"] == "done"}
    return {"done": len(done), "selftests": selftests, "session": session, "walls": walls}, problems


def duplicates(run_dir: Path) -> list[str]:
    out = []
    for f in sorted(run_dir.rglob("*.csv")):
        if "exp11" in f.parts:
            continue
        d = read(f)
        keys = [k for k in KEYS if k in d.columns]
        n = int(d.duplicated(keys, keep=False).sum())
        if n:
            out.append(f"{f.relative_to(run_dir).as_posix()}: {n} rows share a key")
    return out


def compare(run_dir: Path, ref_dir: Path) -> tuple[list[str], list[str]]:
    report, problems = [], []
    for f in sorted(run_dir.rglob("*.csv")):
        rel = f.relative_to(run_dir)
        g = ref_dir / rel
        if not g.exists():
            problems.append(f"{rel.as_posix()}: no counterpart in {ref_dir}")
            continue
        a, b = read(f), read(g)
        keys = [k for k in KEYS if k in a.columns and k in b.columns]
        if "RunIndex" in keys:
            a, b = a[a["RunIndex"] == "0"], b[b["RunIndex"] == "0"]
        m = a.merge(b, on=keys, suffixes=("", "__ref"))
        cols = [c for c in DETERMINISTIC if c in a.columns and c in b.columns]
        diffs = {c: int((m[c] != m[c + "__ref"]).sum()) for c in cols}
        bad = {c: n for c, n in diffs.items() if n}
        line = (f"{rel.as_posix()}: {len(m)} of {len(a)} rows matched to the reference, "
                f"{len(cols)} deterministic columns compared, mismatches {bad or 0}")
        report.append(line)
        if not len(m):
            problems.append(f"{rel.as_posix()}: no row could be compared (0 matched)")
        if bad:
            problems.append(line)
    return report, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--reference", type=Path)
    a = ap.parse_args()
    run = a.run if a.run.is_absolute() else ROOT / a.run
    problems = []

    info, p = ledger(run)
    problems += p
    print(f"ledger: {info.get('done', 0)} commands done; self-tests: "
          f"{[(e['id'], e.get('killed'), e.get('files_grown')) for e in info.get('selftests', [])]}")
    env = info.get("session", {}).get("environment", {})
    print(f"environment: host {env.get('host')}, {env.get('os')}, {env.get('processor')}, "
          f"RAM {env.get('ram_gb')} GB, python {env.get('python')}")
    print(f"java: {(env.get('java_version') or '?').splitlines()[0]}")

    prov, p = provenance(run)
    problems += p
    print(f"provenance: {len(prov)} lines; hosts {sorted({x.get('host') for x in prov})}; "
          f"jvms {sorted({x.get('jvm') for x in prov})}; commits {sorted({x.get('git') for x in prov})}")

    d = duplicates(run)
    problems += d
    print(f"duplicated keys: {len(d)} file(s)")

    if a.reference:
        ref = a.reference if a.reference.is_absolute() else ROOT / a.reference
        report, p = compare(run, ref)
        problems += p
        for line in report:
            print("  " + line)

    for x in problems:
        print("PROBLEM", x)
    print("check_validation: " + ("PASS" if not problems else f"FAIL -- {len(problems)} problem(s)"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
