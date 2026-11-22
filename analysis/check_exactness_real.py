#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exactness of the incremental arms under the mixed update model, batch by batch, against re-mining.

Drives src/main/java/ExactnessProbe.java: the arm under test is fed the batches as the runners feed them (new
sequences and itemsets appended to stored sequences), and after every batch a fresh EHAUSM_Remining instance mines
the accumulated database from scratch. The pattern dumps of the two (pattern, average utility to two decimals,
support) must be the same set at every batch.

  generated  seeded random databases under the mixed model (the generator of check_prefix_encoding.py), at a high
             and a low threshold. Every fault of HAUSP_UB.FAULT is run on them; the report says, per fault, in how
             many batches the result differed. Faults that only loosen a pruning bound (swu_full, eucs_full) are
             expected to leave the result unchanged; every other fault must change it somewhere, otherwise this
             check could not see the defect the fault models.
  real       --data NAME=DIR --xi NAME=X,Y,Z: the raw rows of analysis/build_raw_sequences.py under schedule L5 or W8
             (analysis/update_profile.py), one line per customer and batch, baskets never split across batches.
             The total utility of the accumulated database is also recomputed here from the raw rows, so a parse or
             schedule error shows up even where both miners would agree on it.

The check is refutable only if it compares something: the report gives, per case, the number of batches, of
batches with appended itemsets, and of patterns compared, and a case with no pattern at any batch fails.

Usage:
  python3 analysis/check_exactness_real.py [--data TAFENG=DIR ...] [--schedule L5 W8] [--xi TAFENG=X,Y ...] [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
sys.path.insert(0, str(ROOT / "analysis"))
import check_prefix_encoding as cpe  # noqa: E402

ARMS = ("HAUSP-UB[prefix]", "HAUSP-UB[prefix+noEUCS]", "HAUSP-UB")
# Faults that only make a pruning bound looser leave the result unchanged. pre_from_zero is one of them: it starts the
# prefix utility of an appended part at 0 instead of u(S), so the remaining utility u(S) - pre[k] - u_k is too large.
# (First run, 2026-11-20: it was registered as "changed" and changed nothing; reading the code showed why.) A bound made
# tighter, as by no_useq, must change the result.
BOUND_ONLY = ("swu_full", "eucs_full", "pre_from_zero")
LINE = re.compile(r"batch (\d+) lines (\d+) appended (\d+) sequences (\d+) total_utility (\d+) arm (\d+) reference (\d+)"
                  r" only_arm (\d+) only_reference (\d+)")


def probe(d: Path, sizes, xi, arm, fault, heap="16g", props=()):
    w = d / f"w_{re.sub(r'[^A-Za-z0-9]', '_', arm)}_{fault or 'none'}_{xi}_{len(props)}{'_'.join(re.sub(r'[^A-Za-z0-9]', '', x) for x in props)}"
    w.mkdir()
    p = subprocess.run(["java", f"-Xmx{heap}", *props, "-cp", str(JAR), "ExactnessProbe", str(d / "db_eui.txt"),
                        str(d / "db_seq.txt"), str(d / "db.properties"), str(xi), ",".join(map(str, sizes)),
                        "@" + str(d / "keys.txt"), arm, fault or "-"], capture_output=True, text=True, cwd=w)
    rows = [dict(zip(("batch", "lines", "appended", "sequences", "total_utility", "arm", "reference", "only_arm",
                      "only_reference"), map(int, m.groups()))) for m in LINE.finditer(p.stdout)]
    diffs = [l for l in p.stdout.splitlines() if l.startswith("diff ")][:6]
    return {"rc": p.returncode, "batches": rows, "diffs": diffs,
            "stderr": p.stderr[-400:] if p.returncode not in (0, 1) or not rows else ""}


def summary(r):
    b = r["batches"]
    return {"batches": len(b), "batches_with_appends": sum(x["appended"] > 0 for x in b),
            "patterns_compared": sum(x["reference"] for x in b),
            "batches_differing": sum(x["only_arm"] + x["only_reference"] > 0 for x in b)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="*", default=[])
    ap.add_argument("--schedule", nargs="*", default=["L5"])
    ap.add_argument("--xi", nargs="*", default=[], help="NAME=X,Y,Z: the registered thresholds of one dataset")
    ap.add_argument("--heap", default="16g")
    ap.add_argument("--skip-generated", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if not JAR.exists() or any(p.stat().st_mtime > JAR.stat().st_mtime for p in (ROOT / "src").rglob("*.java")):
        sys.exit(f"refusing: {JAR} is missing or older than src/; rebuild it first")
    xis = {}
    for spec in args.xi:
        n, v = spec.split("=", 1)
        xis[n] = v.split(",")
    missing = [s.split("=", 1)[0] for s in args.data if s.split("=", 1)[0] not in xis]
    if missing:
        sys.exit(f"refusing: no --xi NAME=... for {missing}")
    report = {"generated": {}, "faults": {}, "real": {}}
    ok = True
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        if not args.skip_generated:
            rng = random.Random(20261120)
            dbs = []
            for i in range(30):
                d = t / f"g{i}"
                d.mkdir()
                profits, lines, sizes = cpe.generated(rng, 12, 8, 4, 5)
                cpe.write_db(d, profits, lines)
                dbs.append((d, sizes))
            for xi in ("0.05", "0.02"):
                for arm in ARMS:
                    tot = {"batches": 0, "batches_with_appends": 0, "patterns_compared": 0, "batches_differing": 0,
                           "failed_runs": 0}
                    for d, sizes in dbs:
                        r = probe(d, sizes, xi, arm, None)
                        s = summary(r)
                        for k in s:
                            tot[k] += s[k]
                        if r["rc"] != 0 or len(r["batches"]) != len(sizes):
                            tot["failed_runs"] += 1
                            tot.setdefault("first_failure", r)
                    report["generated"][f"{arm} xi={xi}"] = tot
                    ok &= (tot["failed_runs"] == 0 and tot["batches_differing"] == 0 and tot["patterns_compared"] > 0
                           and tot["batches_with_appends"] > 0)
            for fault in cpe.FAULTS:
                n = 0
                crash = 0
                for d, sizes in dbs:
                    for xi in ("0.05", "0.02"):
                        r = probe(d, sizes, xi, ARMS[0], fault)
                        if r["rc"] not in (0, 1) or len(r["batches"]) != len(sizes):   # a run that stops early is a crash
                            crash += 1
                        n += summary(r)["batches_differing"]
                expected = "unchanged" if fault in BOUND_ONLY else "changed"
                seen = "changed" if n + crash > 0 else "unchanged"
                report["faults"][fault] = {"batches_differing": n, "crashed_runs": crash, "expected": expected,
                                           "as_expected": seen == expected}
                ok &= seen == expected
            # One reference object reused over the batches, as a timing runner uses it: it must still agree; with its
            # per-sequence cache kept across batches (the code before 2026-11-22) an appended sequence is read stale.
            for name, props, expected in (("reference_reused", ("-Dhausp.reuseReference=true",), "unchanged"),
                                          ("reference_reused_keep_cache", ("-Dhausp.reuseReference=true",
                                                                           "-Dhausp.refFault=keep_cache"), "changed")):
                n = crash = 0
                for d, sizes in dbs:
                    for xi in ("0.05", "0.02"):
                        r = probe(d, sizes, xi, ARMS[0], None, props=props)
                        if r["rc"] not in (0, 1) or len(r["batches"]) != len(sizes):   # a run that stops early is a crash
                            crash += 1
                        n += summary(r)["batches_differing"]
                seen = "changed" if n + crash > 0 else "unchanged"
                report["faults"][name] = {"batches_differing": n, "crashed_runs": crash, "expected": expected,
                                          "as_expected": seen == expected}
                ok &= seen == expected
        for spec in args.data:
            name, dd = spec.split("=", 1)
            for sched in args.schedule:
                lines, sizes, split_check = cpe.real_lines(name, dd, sched)
                ok &= split_check["baskets_split"] == 0 and split_check["baskets_checked"] > 0
                d = t / f"{name}-{sched}"
                d.mkdir()
                (d / "db_eui.txt").write_text((Path(dd) / f"{name}_eui.txt").read_text())
                write_lines(d, lines)
                # independent total utility of the accumulated database after every batch
                expect, acc, start = [], 0, 0
                for n in sizes:
                    acc += sum(q for _, s in lines[start:start + n] for iset in s for _, q in iset)
                    expect.append(acc)
                    start += n
                for xi in xis[name]:
                    for arm in (ARMS[0], ARMS[2]):
                        r = probe(d, sizes, xi, arm, None, heap=args.heap)
                        s = summary(r)
                        totals_ok = [x["total_utility"] for x in r["batches"]] == expect
                        case = {"sizes": sizes, "basket_split": split_check, **s,
                                "total_utility_matches_raw_rows": totals_ok, "rc": r["rc"],
                                "per_batch": r["batches"], "diffs": r["diffs"], "stderr": r["stderr"]}
                        report["real"][f"{name}/{sched}/xi={xi}/{arm}"] = case
                        ok &= (r["rc"] == 0 and s["batches"] == len(sizes) and s["batches_differing"] == 0
                               and s["patterns_compared"] > 0 and totals_ok)
    report["verdict"] = "PASS" if ok else "FAIL"
    print(json.dumps({k: v for k, v in report.items() if k != "real"}, indent=1))
    for k, v in report["real"].items():
        print(k, {x: v[x] for x in ("batches", "batches_with_appends", "patterns_compared", "batches_differing",
                                     "total_utility_matches_raw_rows", "rc")})
    print("verdict", report["verdict"])
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
        meta = {"kind": "INVARIANT (pattern-set comparison; no timing)",
                "command": "python3 analysis/check_exactness_real.py " + " ".join(
                    a.split("=")[0] + "=" + Path(a.split("=", 1)[1]).name if "=" in a and "/" in a else a
                    for a in sys.argv[1:] if not a.startswith("/")),
                "git_commit": git("rev-parse", "HEAD"),
                "tree": "clean" if not git("status", "--porcelain", "--untracked-files=no") else "MODIFIED",
                "jar_sha256": hashlib.sha256(JAR.read_bytes()).hexdigest(),
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "python": platform.python_version(), "date": time.strftime("%Y-%m-%d %H:%M:%S")}
        (args.out / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    sys.exit(0 if ok else 1)


def write_lines(d: Path, lines):
    out = []
    for _, s in lines:
        toks = []
        for iset in s:
            toks += [f"{i}[{q}]" for i, q in iset] + ["-1"]
        out.append(" ".join(toks + ["-2"]))
    (d / "db_seq.txt").write_text("\n".join(out) + "\n")
    (d / "db.properties").write_text(f"dataset.path={d / 'db_seq.txt'}\nminUtil.percentage=0.0\n")
    (d / "keys.txt").write_text("\n".join(str(k) for k, _ in lines) + "\n")


if __name__ == "__main__":
    main()
