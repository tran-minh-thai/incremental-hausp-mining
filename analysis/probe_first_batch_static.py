#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""PROBE (pattern counts; seconds are not measurements): static mining of the FIRST batch of a maintenance dataset.

The thresholds of analysis/select_xi.py were chosen on the whole data. The first batch of a schedule is a smaller
database, and the threshold is a ratio of its own total utility, so the cost of mining it can differ from the cost on
the whole data. This probe mines batch 0 alone (the lines of datasets/maint/<NAME>-<SCHED>/db_seq.txt up to the first
entry of sizes.txt) with the same call as select_xi.count (TopKProbe, threshold mode, one batch).

Usage: python3 analysis/probe_first_batch_static.py --set REES46_COSMETICS --schedule L5 W8
           --xi 0.00015625 0.00011048543456039807 7.8125e-05 [--limit 1800] [--heap 6g] --out results-probe/...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))
import select_xi  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--schedule", nargs="+", default=["L5"])
    ap.add_argument("--xi", nargs="+", required=True)
    ap.add_argument("--limit", type=int, default=1800)
    ap.add_argument("--heap", default="6g")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    out = (a.out if a.out.is_absolute() else ROOT / a.out).resolve()
    rel = out.relative_to(ROOT) if out.is_relative_to(ROOT) else None
    if rel is None or not rel.parts or not rel.parts[0].startswith("results-probe"):
        sys.exit(f"refusing: {out} is not under {ROOT}/results-probe*/ (this is a probe on the development machine)")
    if not select_xi.JAR.exists() or any(p.stat().st_mtime > select_xi.JAR.stat().st_mtime
                                         for p in (ROOT / "src").rglob("*.java")):
        sys.exit(f"refusing: {select_xi.JAR} is missing or older than src/; rebuild it first")
    out.mkdir(parents=True, exist_ok=False)
    runs = {}
    with tempfile.TemporaryDirectory() as t:
        for sched in a.schedule:
            src = ROOT / "datasets" / "maint" / f"{a.set}-{sched}"
            n0 = int((src / "sizes.txt").read_text().split(",")[0])
            d = Path(t) / sched
            d.mkdir()
            with open(src / "db_seq.txt") as fi, open(d / f"{a.set}_seq.txt", "w") as fo:
                for i, line in enumerate(fi):
                    if i == n0:
                        break
                    fo.write(line)
            got = sum(1 for _ in open(d / f"{a.set}_seq.txt"))
            if got != n0:
                sys.exit(f"refusing: {src}/db_seq.txt has {got} lines, sizes.txt says batch 0 has {n0}")
            shutil.copyfile(src / "db_eui.txt", d / f"{a.set}_eui.txt")
            logs = out / f"{sched}_logs"
            logs.mkdir()
            rows = []
            for xi in map(float, a.xi):
                r = select_xi.count(a.set, d, xi, a.limit, a.heap, logs)
                r["batch0_lines"] = n0
                rows.append(r)
                print(sched, json.dumps(r), flush=True)
            runs[sched] = rows
    (out / "runs.json").write_text(json.dumps(runs, indent=1) + "\n")
    git = lambda *x: subprocess.run(["git", "-C", str(ROOT), *x], capture_output=True, text=True).stdout.strip()
    meta = {"kind": "PROBE (pattern counts on the development machine; seconds are not measurements)",
            "command": "python3 analysis/probe_first_batch_static.py " + " ".join(sys.argv[1:]),
            "git_commit": git("rev-parse", "HEAD"),
            "tree": "clean" if not git("status", "--porcelain", "--", "src", "pom.xml") else "MODIFIED",
            "jar_sha256": hashlib.sha256(select_xi.JAR.read_bytes()).hexdigest(),
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "data_sha256": {s: hashlib.sha256((ROOT / "datasets" / "maint" / f"{a.set}-{s}" / "db_seq.txt")
                                              .read_bytes()).hexdigest() for s in a.schedule},
            "java": subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.splitlines()[:2],
            "heap": a.heap, "limit_seconds": a.limit, "python": platform.python_version(),
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "ran beside another JVM (the real-data exactness check), so seconds are only indicative"}
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")


if __name__ == "__main__":
    main()
