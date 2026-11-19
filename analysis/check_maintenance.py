#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check experiment 15 (maintenance cost, no search) against definitions computed from its input files.

Runs ExperimentLauncher --exp 15 for every arm on the given input sets, into a results-probe directory, and compares
each row with counts derived here, independently of the Java code, from db_seq.txt, keys.txt and sizes.txt:
  * lines and appended parts of each batch;
  * stored sequences after the batch (distinct keys so far);
  * positions written: the batch's own positions for the prefix arm; for the rewrite arm the full length, after
    the batch, of every customer the batch touches; for the rebuild arm every position stored so far;
  * root-list entries made readable: every stored position for the prefix and rebuild arms (each position sits
    in exactly one root list); at least that for the rewrite arm, whose lists keep entries marked deleted;
  * maint_ns > 0 in every row, and the same rows across arms.
The fault hausp.maintFault=skip_materialize (root lists of the prefix arm left unmerged, the work a search-free
run would otherwise drop) must make the root-entry check fail on the prefix arm. A run that cannot compare any
row is a failure.

Usage: python3 analysis/check_maintenance.py --sets tafeng-l5 online_retail_ii-w8 --out results-probe/maint-check/<date>
"""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
ARMS = ("HAUSP-UB[prefix+noEUCS]", "HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]", "HAUSP-UB[prefix]", "HAUSP-UB[rebuild]")


def expected(set_dir: Path):
    sizes = [int(x) for x in (set_dir / "sizes.txt").read_text().strip().split(",")]
    keys = (set_dir / "keys.txt").read_text().split()
    lens = []
    with open(set_dir / "db_seq.txt") as fh:
        for line in fh:
            lens.append(sum(1 for t in line.split() if t not in ("-1", "-2")))
    total, seen, out, i = {}, set(), [], 0
    for b, n in enumerate(sizes):
        batch_pos, touched, appended = 0, set(), 0
        for j in range(i, i + n):
            k = keys[j]
            if k in seen:
                appended += 1
            touched.add(k)
            total[k] = total.get(k, 0) + lens[j]
            batch_pos += lens[j]
        for j in range(i, i + n):
            seen.add(keys[j])
        i += n
        stored = sum(total.values())
        out.append({"lines": n, "appended": appended, "sequences": len(seen), "batch_positions": batch_pos,
                    "touched_positions": sum(total[k] for k in touched), "stored_positions": stored})
    return out


def run(sets, arms, results_dir: Path, fault=None):
    argv = ["java", "-Xmx16g", "-XX:+UseG1GC"] + ([f"-Dhausp.maintFault={fault}"] if fault else []) + [
        "-jar", str(JAR), "--exp", "15", "--dataset", ",".join(sets), "--algo", ",".join(arms), "--rep", "1",
        "--results-dir", str(results_dir), "--timeout", "90"]
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    rows = []
    f = results_dir / "exp15" / "experiment15_maintenance.csv"
    if f.exists():
        with open(f) as fh:
            rows = list(csv.DictReader(l for l in fh if not l.startswith("#")))
    return p.returncode, rows, p.stderr[-1500:]


def compare(rows, sets):
    res = {"compared_rows": 0, "wrong": [], "root_entry_rows": 0}
    exp = {s: expected(ROOT / "datasets" / "maint" / s.upper()) for s in sets}
    for r in rows:
        s = f"{r['dataset']}-{r['schedule']}".lower()
        e = exp[s][int(r["batch"])]
        arm = r["arm"]
        checks = {"lines": int(r["lines"]) == e["lines"], "appended": int(r["appended"]) == e["appended"],
                  "sequences": int(r["sequences"]) == e["sequences"], "maint_ns": int(r["maint_ns"]) > 0}
        pw = int(r["positions_written"])
        re_ = int(r["root_entries"])
        if "rebuild" in arm:
            checks["positions"] = pw == e["stored_positions"]
            checks["root_entries"] = re_ == e["stored_positions"]
        elif "prefix" in arm:
            checks["positions"] = pw == e["batch_positions"]
            checks["root_entries"] = re_ == e["stored_positions"]
        else:
            checks["positions"] = pw == e["touched_positions"]
            checks["root_entries"] = re_ >= e["stored_positions"]
        res["compared_rows"] += 1
        res["root_entry_rows"] += 1
        bad = [k for k, v in checks.items() if not v]
        if bad:
            res["wrong"].append({"set": s, "arm": arm, "batch": r["batch"], "failed": bad})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        sys.exit(f"refusing: {args.out} exists")
    rc, rows, err = run(args.sets, ARMS, args.out / "clean")
    clean = compare(rows, args.sets)
    want_rows = sum(len(expected(ROOT / "datasets" / "maint" / s.upper())) for s in args.sets) * len(ARMS)
    rc2, rows2, err2 = run(args.sets, ("HAUSP-UB[prefix+noEUCS]",), args.out / "fault-skip_materialize", "skip_materialize")
    faulty = compare(rows2, args.sets)
    caught = any("root_entries" in w["failed"] for w in faulty["wrong"])
    ok = (rc == 0 and clean["compared_rows"] == want_rows and not clean["wrong"] and rc2 == 0 and caught)
    report = {"sets": args.sets, "arms": ARMS, "rc": rc, "rows_expected": want_rows, "clean": clean,
              "fault_skip_materialize": {"rc": rc2, "rows": faulty["compared_rows"], "caught": caught,
                                         "wrong_rows": len(faulty["wrong"])},
              "stderr": err if rc else "", "verdict": "PASS" if ok else "FAIL"}
    (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "clean"} | {"clean_compared": clean["compared_rows"],
                                                                        "clean_wrong": len(clean["wrong"])}, indent=1))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
