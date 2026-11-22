#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exactness of the candidate-tree arm (CandidateTree-AU) under the mixed update model, against re-mining.

Drives src/main/java/ExactnessProbe.java with the arm CandidateTree-AU: after every batch the arm's result set must
equal that of a fresh EHAUSM_Remining on the accumulated database (pattern, average utility to two decimals,
support). The databases are the seeded generator of check_prefix_encoding.py (new sequences and itemsets appended to
stored ones): half in the generator's default family (12 keys, 5 batches), half in a family of long schedules
(8 or 12 batches, up to 8 parts per key), at three thresholds; the threshold is a ratio of the accumulated total utility, so it rises from batch
to batch, which is where the arm departs from the paper (see the class comment of CandidateTreeAU).

Faults (-Dhausp.ctFault), each run on every database:
  keep_low_child     without change 3 (a low child in the tree is kept in the Strategy 6 scan): must change a result.
                     On the default family alone it changed none (first run, 2026-11-22: 397 such children, 0
                     differing batches in 18 runs); a search found it on long schedules (a child kept with stale values
                     misses later increments), hence the second family.
  no_result_filter   without change 4 (results not filtered at the current threshold): must change a result
  paper_line6        the paper's test PEU_D(t) < threshold in place of the expanded flag: must leave every result
                     unchanged (the class comment argues the two rebuild the same nodes)
The report also gives the batches compared, the batches with appends, the patterns compared, the nodes dropped by
changes 2 and 3 (they must be exercised, otherwise the faults above test nothing) and the count of high children
found missing from the tree in the Strategy 6 scan, which must be 0.

Usage: python3 analysis/check_candidate_tree_exactness.py [--dbs 40] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
sys.path.insert(0, str(ROOT / "analysis"))
import check_prefix_encoding as cpe  # noqa: E402
from check_exactness_real import LINE, summary  # noqa: E402

TREE = re.compile(r"tree (\d+) built (\d+) dropped (\d+) low_child (\d+) unexpected_high_missing (\d+)")
FAULTS = {"keep_low_child": "changed", "no_result_filter": "changed", "paper_line6": "unchanged"}
# xi = 0.01 left out: up to millions of patterns per small database, and the tree ran out of a 4 GB heap on 4 of 40
# databases (first full run, 2026-11-22; every batch before the stop matched). Memory is measured in the campaign.
XIS = ("0.1", "0.05", "0.03")


def probe(d: Path, sizes, xi, fault):
    w = d / f"w_{xi}_{fault or 'none'}"
    w.mkdir()
    props = [f"-Dhausp.ctFault={fault}"] if fault else []
    p = subprocess.run(["java", "-Xmx4g", *props, "-cp", str(JAR), "ExactnessProbe", str(d / "db_eui.txt"),
                        str(d / "db_seq.txt"), str(d / "db.properties"), xi, ",".join(map(str, sizes)),
                        "@" + str(d / "keys.txt"), "CandidateTree-AU", "-"], capture_output=True, text=True, cwd=w)
    rows = [dict(zip(("batch", "lines", "appended", "sequences", "total_utility", "arm", "reference", "only_arm",
                      "only_reference"), map(int, m.groups()))) for m in LINE.finditer(p.stdout)]
    tree = [tuple(map(int, m.groups())) for m in TREE.finditer(p.stdout)]
    return {"rc": p.returncode, "batches": rows, "tree": tree, "oom": "OutOfMemoryError" in p.stderr,
            "diffs": [l for l in p.stdout.splitlines() if l.startswith("diff ")][:6],
            "stderr": p.stderr[-600:] if p.returncode not in (0, 1) or not rows else ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbs", type=int, default=40)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    if not JAR.exists() or any(p.stat().st_mtime > JAR.stat().st_mtime for p in (ROOT / "src").rglob("*.java")):
        sys.exit(f"refusing: {JAR} is missing or older than src/; rebuild it first")
    report = {"clean": {}, "faults": {}}
    ok = True
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        rng = random.Random(20261122)
        dbs = []
        for i in range(a.dbs):
            d = t / f"g{i}"
            d.mkdir()
            if i % 2 == 0:
                profits, lines, sizes = cpe.generated(rng, 12, 8, 4, 5)
            else:   # long schedules: the first family (five batches) never exposed the fault keep_low_child
                profits, lines, sizes = cpe.generated(rng, rng.choice([6, 10, 16]), rng.choice([4, 6, 8]),
                                                      rng.choice([3, 5, 8]), rng.choice([8, 12]),
                                                      short=rng.random() < 0.5)
            cpe.write_db(d, profits, lines)
            dbs.append((d, sizes))
        for xi in XIS:
            tot = {"runs": 0, "failed_runs": 0, "batches": 0, "batches_with_appends": 0, "patterns_compared": 0,
                   "batches_differing": 0, "nodes_dropped": 0, "low_children_dropped": 0, "stopped_out_of_memory": 0,
                   "unexpected_high_missing": 0}
            for d, sizes in dbs:
                r = probe(d, sizes, xi, None)
                tot["runs"] += 1
                if r["oom"]:
                    tot["stopped_out_of_memory"] += 1
                elif r["rc"] != 0 or len(r["batches"]) != len(sizes):
                    tot["failed_runs"] += 1
                    tot.setdefault("first_failure", r)
                for k, v in summary(r).items():
                    tot[k] += v
                tot["nodes_dropped"] += sum(x[2] for x in r["tree"])
                tot["low_children_dropped"] += sum(x[3] for x in r["tree"])
                tot["unexpected_high_missing"] = max([tot["unexpected_high_missing"]] + [x[4] for x in r["tree"]])
            report["clean"][f"xi={xi}"] = tot
            ok &= (tot["failed_runs"] == 0 and tot["batches_differing"] == 0 and tot["patterns_compared"] > 0
                   and tot["batches_with_appends"] > 0 and tot["unexpected_high_missing"] == 0)
        dropped = sum(v["nodes_dropped"] for v in report["clean"].values())
        ok &= dropped > 0
        for fault, expected in FAULTS.items():
            n = crash = 0
            for d, sizes in dbs:
                for xi in XIS:
                    r = probe(d, sizes, xi, fault)
                    if not r["oom"] and (r["rc"] not in (0, 1) or len(r["batches"]) != len(sizes)):
                        crash += 1
                    n += summary(r)["batches_differing"]
            got = "changed" if n or crash else "unchanged"
            report["faults"][fault] = {"batches_differing": n, "crashed_runs": crash, "expected": expected,
                                       "got": got, "runs": len(dbs) * len(XIS)}
            ok &= got == expected
    report["nodes_dropped_by_changes_2_and_3"] = dropped
    report["verdict"] = "PASS" if ok else "FAIL"
    git = lambda *x: subprocess.run(["git", "-C", str(ROOT), *x], capture_output=True, text=True).stdout.strip()
    report["provenance"] = {"command": " ".join(["analysis/check_candidate_tree_exactness.py"] + sys.argv[1:]),
                            "git_commit": git("rev-parse", "HEAD"),
                            "tree": "clean" if not git("status", "--porcelain", "--", "src", "analysis") else "MODIFIED",
                            "jar_sha256": __import__("hashlib").sha256(JAR.read_bytes()).hexdigest(),
                            "java": subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.splitlines()[0]}
    text = json.dumps(report, indent=1, default=str)
    print(text)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "report.json").write_text(text + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
