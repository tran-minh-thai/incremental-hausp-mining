#!/usr/bin/env python3
"""Check the in-place append of stored sequences (arm option prefix) against the definitions.

Drives src/main/java/PrefixEncodingCheck.java, which after every batch compares the miner's stored
positions, u(S), remaining utilities, SWU, EUCS and single-item lists with values computed from the
input parts again (see its header). This script supplies the inputs and the fault injection:

  generated  seeded random databases fed under the mixed update model: each key arrives in one to
             four parts, two parts of one key may fall in the same batch, itemsets hold several items
             (so the same-itemset EUCS and the item order inside an itemset are exercised), and one
             long database has more batches than the list-run limit, so the run merge is exercised;
  real       optional, --data NAME=DIR: the raw dataset of analysis/build_raw_sequences.py fed by a
             schedule of analysis/update_profile.py (base part, then one line per customer and batch).
             On it the miner also runs through TopKProbe at --mu on the rewrite arm (HAUSP-UB) and on
             HAUSP-UB[prefix]: the two must report the same HAUSPs in every batch, and, with
             --reference-jar (a build from before the prefix option), the rewrite arm's whole output
             must be byte-identical to that build's.

Every fault of HAUSP_UB.FAULT must make at least one check fail on the generated databases; the run
without a fault must pass every check with a nonzero number of compared values, on every arm.

Usage:
  python3 analysis/check_prefix_encoding.py [--data TAFENG=DIR] [--schedule L5] [--out DIR]
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
FAULTS = ("no_useq", "pre_from_zero", "swu_full", "eucs_full", "no_run", "stale_root", "touch_old")
ARMS = ("HAUSP-UB[prefix]", "HAUSP-UB[prefix+noEUCS]")


def write_db(d: Path, profits, lines):
    (d / "db_eui.txt").write_text("".join(f"{i}:{p}\n" for i, p in sorted(profits.items())))
    out = []
    for _, s in lines:
        toks = []
        for iset in s:
            toks += [f"{i}[{q}]" for i, q in sorted(iset)] + ["-1"]
        out.append(" ".join(toks + ["-2"]))
    (d / "db_seq.txt").write_text("\n".join(out) + "\n")
    (d / "db.properties").write_text(f"dataset.path={d / 'db_seq.txt'}\nminUtil.percentage=0.0\n")
    (d / "keys.txt").write_text("\n".join(str(k) for k, _ in lines) + "\n")


def generated(rng: random.Random, n_keys: int, n_items: int, max_parts: int, n_batches: int, short=False):
    profits = {i: rng.randint(1, 5) for i in range(1, n_items + 1)}
    parts = []
    for key in range(n_keys):
        for _ in range(rng.randint(1, max_parts)):
            width, count = (2, 1) if short else (3, 3)
            part = [{(i, rng.randint(1, 4)) for i in rng.sample(range(1, n_items + 1), rng.randint(1, width))}
                    for _ in range(rng.randint(1, count))]
            parts.append((key, [sorted(x) for x in part]))
    # keep the order of the parts of one key; interleave keys at random
    order = sorted(range(len(parts)), key=lambda j: (rng.random(), j))
    by_key = {}
    for j in range(len(parts)):
        by_key.setdefault(parts[j][0], []).append(parts[j])
    queue = {k: list(v) for k, v in by_key.items()}
    lines = []
    for j in order:
        k = parts[j][0]
        lines.append(queue[k].pop(0))
    cuts = sorted(rng.sample(range(1, len(lines)), min(n_batches - 1, len(lines) - 1)))
    sizes = [b - a for a, b in zip([0] + cuts, cuts + [len(lines)])]
    return profits, lines, sizes


def run(d: Path, sizes, arm, fault, mu="0.05", eucs_pairs=2000, keys="@keys.txt"):
    argv = ["java", "-Xmx8g", "-cp", str(JAR), "PrefixEncodingCheck", str(d / "db_eui.txt"), str(d / "db_seq.txt"),
            str(d / "db.properties"), mu, ",".join(map(str, sizes)),
            keys.replace("@keys.txt", "@" + str(d / "keys.txt")), arm, fault or "-", str(eucs_pairs)]
    p = subprocess.run(argv, capture_output=True, text=True)
    checks = {m.group(1): (int(m.group(2)), int(m.group(3)))
              for m in re.finditer(r"check (\w+) compared (\d+) wrong (\d+)", p.stdout)}
    head = re.search(r"batches_with_appends (\d+) positions_arrived (\d+) positions_written (\d+) run_merges (\d+)",
                     p.stdout)
    return {"rc": p.returncode, "checks": checks,
            "batches_with_appends": int(head.group(1)) if head else None,
            "positions_arrived": int(head.group(2)) if head else None,
            "positions_written": int(head.group(3)) if head else None,
            "run_merges": int(head.group(4)) if head else None,
            "examples": [l.strip() for l in p.stdout.splitlines() if l.strip().startswith("example")][:3],
            "stderr": p.stderr[-500:] if p.returncode not in (0, 1) else ""}


def real_lines(name, d, sched):
    import numpy as np
    import pandas as pd
    import update_profile as up
    z = np.load(Path(d) / f"{name}_rows.npz")
    f = pd.DataFrame({k: z[k] for k in ("cust", "day", "basket", "item", "util")})
    btab = f[["cust", "basket", "day"]].drop_duplicates(["cust", "basket"])
    base_end, batches = up.schedule(btab, sched)
    bounds = [(-10 ** 9, base_end)] + list(batches)
    lines, sizes = [], []
    for s, e in bounds:
        g = f[(f["day"] >= s) & (f["day"] < e)].sort_values(["cust", "basket", "item"])
        n = 0
        for c, gc in g.groupby("cust", sort=True):
            its = [sorted(zip((gb["item"] + 1).tolist(), gb["util"].tolist())) for _, gb in gc.groupby("basket", sort=True)]
            lines.append((int(c), its))
            n += 1
        sizes.append(n)
    return lines, sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="*", default=[])
    ap.add_argument("--schedule", default="L5")
    ap.add_argument("--mu", default="0.01")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--reference-jar", type=Path, default=None)
    args = ap.parse_args()
    if not JAR.exists() or any(p.stat().st_mtime > JAR.stat().st_mtime for p in (ROOT / "src").rglob("*.java")):
        sys.exit(f"refusing: {JAR} is missing or older than src/; rebuild it first")
    report = {"generated": [], "faults": {}, "real": {}}
    ok = True
    rng = random.Random(20261111)
    # (keys, items, parts per key, batches, short parts, minUtil): the last two databases have more
    # batches than the list-run limit; their parts are one short itemset and the threshold is high,
    # because the checks need the structures, not a deep search over 40-itemset sequences
    specs = [(12, 8, 4, 5, False, "0.05")] * 20 + [(30, 15, 3, 6, False, "0.05")] * 10 + [(6, 3, 80, 400, True, "0.3")] * 2
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        dbs = []
        for i, (nk, ni, mp, nb, short, mu) in enumerate(specs):
            d = t / f"g{i}"
            d.mkdir()
            profits, lines, sizes = generated(rng, nk, ni, mp, nb, short)
            write_db(d, profits, lines)
            dbs.append((d, sizes, mu))
        totals = {arm: {} for arm in ARMS}
        for d, sizes, mu in dbs:
            for arm in ARMS:
                r = run(d, sizes, arm, None, mu=mu)
                if r["rc"] != 0:
                    ok = False
                    report["generated"].append({"db": d.name, "arm": arm, **r})
                for c, (n, w) in r["checks"].items():
                    a = totals[arm].setdefault(c, [0, 0])
                    a[0] += n
                    a[1] += w
                totals[arm].setdefault("batches_with_appends", [0, 0])[0] += r["batches_with_appends"] or 0
                totals[arm].setdefault("run_merges", [0, 0])[0] += r["run_merges"] or 0
        report["generated_totals"] = totals
        for arm in ARMS:
            for c in ("invariance", "sequences", "swu", "roots") + (("eucs",) if "noEUCS" not in arm else ()):
                n, w = totals[arm].get(c, [0, 0])
                if n == 0 or w:
                    ok = False
            if totals[arm]["run_merges"][0] == 0:     # the merge of a list reaching MAX_RUNS was never exercised
                ok = False
        for fault in FAULTS:
            caught = {}
            for d, sizes, mu in dbs:
                r = run(d, sizes, ARMS[0], fault, mu=mu)
                for c, (n, w) in r["checks"].items():
                    caught[c] = caught.get(c, 0) + w
                if r["rc"] not in (0, 1):
                    caught["crash"] = caught.get("crash", 0) + 1
            report["faults"][fault] = caught
            if sum(v for k, v in caught.items() if k != "crash") == 0:
                ok = False
        # the invariance check must be refutable: the fault that edits a stored position is caught by it
        if report["faults"]["touch_old"].get("invariance", 0) == 0:
            ok = False
        for spec in args.data:
            name, dd = spec.split("=", 1)
            lines, sizes = real_lines(name, dd, args.schedule)
            d = t / name
            d.mkdir()
            (d / "db_eui.txt").write_text((Path(dd) / f"{name}_eui.txt").read_text())
            out = []
            for _, s in lines:
                toks = []
                for iset in s:
                    toks += [f"{i}[{q}]" for i, q in iset] + ["-1"]
                out.append(" ".join(toks + ["-2"]))
            (d / "db_seq.txt").write_text("\n".join(out) + "\n")
            (d / "db.properties").write_text(f"dataset.path={d / 'db_seq.txt'}\nminUtil.percentage=0.0\n")
            (d / "keys.txt").write_text("\n".join(str(k) for k, _ in lines) + "\n")
            for arm in ARMS:
                r = run(d, sizes, arm, None, mu=args.mu)
                report["real"][f"{name}/{args.schedule}/{arm}"] = {"sizes": sizes, **r}
                ok &= r["rc"] == 0 and all(n > 0 for c, (n, w) in r["checks"].items() if c != "eucs" or "noEUCS" not in arm)
            r = run(d, sizes, "HAUSP-UB", None, mu=args.mu)
            report["real"][f"{name}/{args.schedule}/HAUSP-UB (rewrite, for the written-position count)"] = {
                "positions_arrived": r["positions_arrived"], "positions_written": r["positions_written"]}
            outs = {}
            keys = ",".join(str(k) for k, _ in lines)
            for tag, jar, arm in (("rewrite", JAR, "HAUSP-UB"), ("prefix", JAR, "HAUSP-UB[prefix]"),
                                  ("reference", args.reference_jar, "HAUSP-UB")):
                if jar is None:
                    continue
                w = d / f"probe_{tag}"
                w.mkdir()
                p = subprocess.run(["java", "-Xmx16g", "-cp", str(jar), "TopKProbe", str(d / "db_eui.txt"),
                                    str(d / "db_seq.txt"), str(d / "db.properties"), "1", f"mu={args.mu}",
                                    ",".join(map(str, sizes)), keys, arm], capture_output=True, text=True, cwd=w)
                outs[tag] = p.stdout
                ok &= p.returncode == 0
            pats = {t: {} for t in outs}
            lines_of = {t: [l for l in o.splitlines() if l.startswith("batch ")] for t, o in outs.items()}
            for t, o in outs.items():
                for l in o.splitlines():
                    if l.startswith("pattern "):
                        b, pat = l.split()[1:3]
                        pats[t].setdefault(b, set()).add(pat)
            same = pats["rewrite"] == pats["prefix"]
            cmp_ = {"batch_lines": lines_of, "patterns_per_batch": {t: {b: len(v) for b, v in x.items()} for t, x in pats.items()},
                    "rewrite_equals_prefix_patterns": same}
            ok &= same and sum(len(v) for v in pats["prefix"].values()) > 0
            if "reference" in outs:
                cmp_["rewrite_byte_identical_to_reference"] = outs["rewrite"] == outs["reference"]
                cmp_["reference_jar_sha256"] = hashlib.sha256(args.reference_jar.read_bytes()).hexdigest()
                ok &= outs["rewrite"] == outs["reference"]
            report["real"][f"{name}/{args.schedule}/arm comparison at mu={args.mu}"] = cmp_
    report["verdict"] = "PASS" if ok else "FAIL"
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
        meta = {"kind": "INVARIANT (structure checks; no timing)",
                "command": "python3 analysis/check_prefix_encoding.py " + " ".join(
                    a.split("=")[0] + "=" + Path(a.split("=", 1)[1]).name if "=" in a and "/" in a else a
                    for a in sys.argv[1:] if not a.startswith("/")),
                "git_commit": git("rev-parse", "HEAD"),
                "tree": "clean" if not git("status", "--porcelain", "--untracked-files=no") else "MODIFIED",
                "jar_sha256": hashlib.sha256(JAR.read_bytes()).hexdigest(),
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "python": platform.python_version(), "date": time.strftime("%Y-%m-%d %H:%M:%S")}
        (args.out / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "generated"}, indent=1))
    print("prefix-encoding check:", report["verdict"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
