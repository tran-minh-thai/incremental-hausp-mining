#!/usr/bin/env python3
"""Recompute the published table cells straight from the CSVs, without common.py.

Every other check in this repository reads its data through ``common.load_experiment``, and
so does every table generator. A fault in that shared path would move the tables and the
checks together, and they would agree the whole way down -- the checks compare two things
that were computed the same way.

This one reads the CSV files itself: it opens each result tree in order, applies the same
rule the generators apply (a later tree replaces the arms it carries, a cell is a mean over
the trials that completed every batch, otherwise the first failure verdict), and compares
what it derives with what the .tex prints, cell by cell. It shares no code with the
generators beyond the Python standard library.

    python3 analysis/verify_tables.py            # exit 1 on any mismatch
    python3 analysis/verify_tables.py --list     # print every cell compared

It prints how many cells it could compare. A run that compares nothing is not a pass.
"""
from __future__ import annotations

import argparse
import collections
import csv
import pathlib
import re
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LATEX = ROOT / "analysis_out" / "paper" / "latex"
#: Result trees oldest first; a later one replaces the arms it carries.
TREES = ["results", "results-2026-09", "results-2026-09b", "results-2026-09c", "results-2026-09e"]
MEM_TREES = ["results-2026-09/mem", "results-2026-09c/mem", "results-2026-09d/mem"]
DONE = {"SUCCESS", "SUCCESS_MATCH"}
#: Column heading in the .tex -> dataset name in the CSVs.
DS = {"BIBLE": "BIBLE", "BMS1": "BMS1_SPMF", "FIFA": "FIFA", "KOSARAK": "KOSARAK",
      "LEVIATHAN": "LEVIATHAN", "SIGN": "SIGN", "SYN": "C8T1S5I8N5K"}
ORDER = ["BIBLE", "BMS1_SPMF", "FIFA", "KOSARAK", "LEVIATHAN", "SIGN", "C8T1S5I8N5K"]
#: Arm order of the five-arm tables, as the generator writes their columns.
ARMS5 = ["EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM", "HAUSP-UB-L1", "HAUSP-UB[noEUCS]"]
TOL = 0.005          # the tables print three significant figures


def read(tree: str, name: str):
    p = ROOT / tree / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8", errors="ignore") as f:
        return list(csv.DictReader([l for l in f if not l.startswith("#")]))


def cells(name: str, nbatch: int, trees=TREES, value="tTotal(ms)", where=None, reduce="sum"):
    """(dataset, arm) -> ('num', mean over complete trials) or ('fail', 'OT@b').

    ``reduce`` is how a trial's batches become one number, and it is not decoration: a
    runtime cell is the sum over the batches, a peak-memory cell is the largest of them.
    Summing a memory series gives a number four to five times too big, and it looks like a
    plausible memory figure.
    """
    out: dict = {}
    for tree in trees:
        rows = [r for r in read(tree, name) if where is None or where(r)]
        if not rows:
            continue
        by = collections.defaultdict(list)
        for r in rows:
            by[(r["Dataset"], r["Algorithm"])].append(r)
        for key, group in by.items():
            trials = collections.defaultdict(list)
            for r in group:
                if r.get("Status") in DONE:
                    trials[r["RunIndex"]].append(r)
            fold = sum if reduce == "sum" else max
            full = [fold(float(x[value]) for x in v) for v in trials.values() if len(v) == nbatch]
            if full:
                out[key] = ("num", statistics.mean(full))
            else:
                bad = sorted((r for r in group if r.get("Status") in ("OT", "OOM")),
                             key=lambda r: (int(r["RunIndex"]), int(r["BatchID"])))
                if bad:
                    out[key] = ("fail", "%s@%s" % (bad[0]["Status"], bad[0]["BatchID"]))
    return out


def body(fname: str) -> list[str]:
    t = (LATEX / fname).read_text(encoding="utf-8")
    return t[t.find(r"\midrule"):t.find(r"\bottomrule")].split("\n")


def agrees(printed: str, derived, scale: float) -> bool:
    """Compare at the precision the cell was printed with, not at full precision.

    A cell printed as an integer cannot carry more than half a unit, so 44.40 printed as 44
    is right and a fixed relative tolerance calls it wrong. The comparison allows half of the
    last printed digit, plus a small relative slack for the cells printed to three
    significant figures.
    """
    printed = printed.replace(r"\textbf{", "").replace("}", "").strip()
    if derived[0] == "fail":
        return derived[1] in printed
    m = re.search(r"([0-9]+\.?[0-9]*)", printed)
    if not m:
        return False
    shown = m.group(1)
    want = derived[1] / scale
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    half_digit = 0.5 * 10 ** (-decimals)
    return abs(float(shown) - want) <= half_digit + TOL * abs(want)


def row_dataset(line: str):
    first = re.sub(r"[^A-Za-z0-9]", "", line.split("&")[0])
    return DS.get(first)


def check_wide(fname, source, nbatch, arms, scale, label, trees=TREES, value="tTotal(ms)",
               where=None, reduce="sum"):
    """A table with one row per dataset and one column per arm."""
    derived = cells(source, nbatch, trees=trees, value=value, where=where, reduce=reduce)
    found, bad = 0, []
    for line in body(fname):
        if "&" not in line:
            continue
        ds = row_dataset(line)
        if ds is None:
            continue
        printed = [c.strip() for c in line.replace(r"\\", "").split("&")][1:]
        for arm, cell in zip(arms, printed):
            d = derived.get((ds, arm))
            if d is None:
                continue
            found += 1
            if not agrees(cell, d, scale):
                bad.append("%s / %s / %s: printed %r, derived %s"
                           % (label, ds, arm, cell.strip(), d[1]))
    return found, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    total, problems, covered = 0, [], []
    plans = [
        ("tab_exp1_runtime.tex", "exp1/experiment1_tightness.csv", 5, ARMS5, 1000.0,
         "Exp 1 runtime (s)", TREES, "tTotal(ms)", None, "sum"),
        ("tab_exp4_memory.tex", "exp4/experiment4_memory_prelarge.csv", 5, ARMS5, 1.0,
         "Exp 4 live heap (MB)", MEM_TREES, "MemLive(MB)", None, "max"),
    ]
    for fname, source, nbatch, arms, scale, label, trees, value, where, reduce in plans:
        if not (LATEX / fname).exists():
            problems.append("%s is not generated any more; update this check" % fname)
            continue
        n, bad = check_wide(fname, source, nbatch, arms, scale, label, trees, value, where, reduce)
        total += n
        problems += bad
        covered.append("%-22s %3d cells" % (label, n))

    # Experiment 9: the runtime rows of the merged attribution table.
    if (LATEX / "tab_exp9_attribution.tex").exists():
        derived = cells("exp9/experiment9_attribution.csv", 5)
        short = {"APEAU-I": "EHAUSM-I", "APEAU-R": "EHAUSM-R"}
        n = 0
        for line in body("tab_exp9_attribution.tex"):
            if "runtime" not in line:
                continue
            parts = [c.strip() for c in line.replace(r"\\", "").split("&")]
            arm = short.get(parts[0].strip())
            if arm is None:
                continue                      # the UB chain arms are named by subscript, skipped
            for ds, cell in zip(ORDER, parts[2:]):
                d = derived.get((ds, arm))
                if d is None:
                    continue
                n += 1
                if not agrees(cell, d, 1000.0):
                    problems.append("Exp 9 runtime / %s / %s: printed %r, derived %s"
                                    % (ds, arm, cell, d[1]))
        total += n
        covered.append("%-22s %3d cells" % ("Exp 9 runtime (s)", n))

    for line in covered:
        print("  " + line)
    print("verify_tables: compared %d published cells against the CSVs, without common.py" % total)
    if total == 0:
        print("verify_tables: FAIL -- nothing was compared, which is not a pass")
        return 1
    if problems:
        for p in problems:
            print("  MISMATCH  %s" % p)
        print("verify_tables: FAIL -- %d cell(s) disagree" % len(problems))
        return 1
    print("verify_tables: PASS -- every cell compared matches the rows behind it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
