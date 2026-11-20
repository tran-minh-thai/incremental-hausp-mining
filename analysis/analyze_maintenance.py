#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Experiment 15 (maintenance cost without search): integrity checks and the registered statistics.

Reads results-gen2/maintenance/exp15/experiment15_maintenance.csv (one row per run, dataset, schedule, arm,
repetition and batch; a '#' provenance line before the rows of every run) and writes, beside it, analysis.json and
analysis.md. Registered before the campaign (design approved by the author on 2026-11-19):

- statistic of a cell (dataset x schedule) and arm: the median over the five independent JVMs of the total
  maintenance time of batches 1..k (batch 0, building the base, is the same work in every arm and is reported apart);
- claim 1: prefix maintenance <= 0.5 x rebuild, EUCS off, on at least 3 datasets;
- claim 2: the prefix arm writes fewer positions than the rewrite arm (deterministic counts, one value per cell);
- claim 3: the prefix arm is faster than the rewrite arm (EUCS off);
- the EUCS-on row (prefix and rebuild only) is secondary and measured only where the heap allowed it;
- tie rule: a cell whose ratio lies within three times its measured variability of 0.5 gets five more repetitions,
  in one extra batch for the measurement machine.

Choices this script fixes because the registration did not (stated here and in the output, before any reading):
- the per-repetition ratio pairs repetition r of one arm with repetition r of the other (each in its own JVM, arm
  order rotated by repetition); its variability is the sample standard deviation of the five per-repetition ratios;
- a dataset meets claim 1 under the strict reading when both schedules pass, under the loose reading when one does;
  the strict count is reported first.

Integrity checks (exit 1 if one fails): every row OK; five repetitions per cell and arm; one provenance (commit,
JVM, heap, host, clean tree, data manifest) for every run; the deterministic columns (lines, appended, sequences,
positions_written, root_entries) equal across repetitions; lines, appended and sequences equal across arms.

With several --csv files (a campaign and its tie-rule batch) the rows are read together; a cell and arm then has
repetitions 1..5, or 1..10 where the tie batch added 6..10, and the statistics use every repetition it has. The
outputs go beside the first file.

Usage: python3 analysis/analyze_maintenance.py [--csv PATH [PATH ...]]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "results-gen2" / "maintenance" / "exp15" / "experiment15_maintenance.csv"
PREFIX, REWRITE, REBUILD = "HAUSP-UB[prefix+noEUCS]", "HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]"
PREFIX_E, REBUILD_E = "HAUSP-UB[prefix]", "HAUSP-UB[rebuild]"
DET = ("lines", "appended", "sequences", "positions_written", "root_entries")
SAME_ACROSS_ARMS = ("lines", "appended", "sequences")


def read(path: Path):
    prov, rows, hdr = collections.Counter(), [], None
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            m = dict(re.findall(r"(\w+)=(\S+)", line))
            prov[tuple(m.get(k) for k in ("git", "jvm", "heap", "host", "tree", "data"))] += 1
        elif line.startswith("dataset,"):
            hdr = line.split(",")
        elif line:
            rows.append(dict(zip(hdr, line.split(","))))
    return prov, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, nargs="+", default=[CSV])
    args = ap.parse_args()
    paths = [c.resolve() for c in args.csv]
    prov, rows = collections.Counter(), []
    for c in paths:
        p_, r_ = read(c)
        prov.update(p_)
        rows += r_
    args.csv = paths[0]
    problems = []
    if len(prov) != 1 or next(iter(prov))[4] != "clean":
        problems.append(f"provenance is not one clean environment: {dict(prov)}")
    bad = [r for r in rows if r["status"] != "OK"]
    if bad:
        problems.append(f"{len(bad)} rows not OK")
    runs = collections.defaultdict(dict)          # (ds, sched, arm, rep) -> batch -> row
    for r in rows:
        runs[(r["dataset"], r["schedule"], r["arm"], int(r["rep"]))][int(r["batch"])] = r
    cells = sorted({(d, s) for d, s, _, _ in runs})
    arms_of = {c: sorted({a for d, s, a, _ in runs if (d, s) == c}) for c in cells}
    det_compared = 0
    for c in cells:
        for a in arms_of[c]:
            reps = sorted(rp for d, s, aa, rp in runs if (d, s) == c and aa == a)
            if reps not in ([1, 2, 3, 4, 5], list(range(1, 11))):
                problems.append(f"{c} {a}: repetitions {reps}")
            ref = runs[(*c, a, reps[0])]
            for rp in reps[1:]:
                cur = runs[(*c, a, rp)]
                if sorted(cur) != sorted(ref):
                    problems.append(f"{c} {a} rep {rp}: batches differ")
                    continue
                for b in ref:
                    det_compared += 1
                    if any(cur[b][k] != ref[b][k] for k in DET):
                        problems.append(f"{c} {a} rep {rp} batch {b}: deterministic columns differ")
        base = runs[(*c, arms_of[c][0], 1)]
        for a in arms_of[c][1:]:
            other = runs[(*c, a, 1)]
            for b in base:
                det_compared += 1
                if b not in other or any(other[b][k] != base[b][k] for k in SAME_ACROSS_ARMS):
                    problems.append(f"{c} {a} batch {b}: lines/appended/sequences differ from {arms_of[c][0]}")

    def reps_of(c, a):
        return sorted(rp for d, s, aa, rp in runs if (d, s) == c and aa == a)

    def total(c, a, rp, col="maint_ns"):
        r = runs[(*c, a, rp)]
        return sum(int(r[b][col]) for b in r if b >= 1)

    def stat(c, a, col="maint_ns"):
        return statistics.median(total(c, a, rp, col) for rp in reps_of(c, a))

    out = {"source": [str(c.relative_to(ROOT)) for c in paths], "provenance": [list(k) + [v] for k, v in prov.items()],
           "rows": len(rows), "runs": len(runs), "cells": len(cells),
           "deterministic_values_compared": det_compared, "problems": problems, "cells_detail": {}}
    tie_cells = []
    for c in cells:
        key = f"{c[0]}/{c[1]}"
        d = {"batches": len(runs[(*c, PREFIX, 1)]) - 1}
        for a in arms_of[c]:
            d[a] = {"median_total_ms": round(stat(c, a) / 1e6, 3), "median_gc_ms": stat(c, a, "gc_ms"),
                    "batch0_median_ms": round(statistics.median(int(runs[(*c, a, rp)][0]["maint_ns"])
                                                                for rp in reps_of(c, a)) / 1e6, 3),
                    "positions_written": total(c, a, 1, "positions_written")}
        for name, num, den in (("prefix_over_rebuild", PREFIX, REBUILD), ("prefix_over_rewrite", PREFIX, REWRITE),
                               ("prefix_over_rebuild_eucs_on", PREFIX_E, REBUILD_E)):
            if num not in arms_of[c] or den not in arms_of[c]:
                d[name] = None
                continue
            both = sorted(set(reps_of(c, num)) & set(reps_of(c, den)))
            per = [total(c, num, rp) / total(c, den, rp) for rp in both]
            med = stat(c, num) / stat(c, den)
            sd = statistics.stdev(per)
            d[name] = {"ratio_of_medians": round(med, 4), "repetitions": len(both), "per_rep": [round(x, 4) for x in per],
                       "per_rep_sd": round(sd, 4)}
            if name == "prefix_over_rebuild":
                tie = abs(med - 0.5) < 3 * sd
                d[name]["passes_0_5"] = med <= 0.5
                d[name]["within_three_sd_of_0_5"] = tie
                if tie:
                    tie_cells.append(key)
        d["positions_prefix_over_rewrite"] = round(d[PREFIX]["positions_written"] / d[REWRITE]["positions_written"], 4)
        out["cells_detail"][key] = d
    ds = sorted({c[0] for c in cells})
    per_ds = {x: [out["cells_detail"][f"{x}/{s}"]["prefix_over_rebuild"]["passes_0_5"] for s in ("L5", "W8")]
              for x in ds}
    out["claim1"] = {"strict_datasets_both_schedules": sorted(x for x, v in per_ds.items() if all(v)),
                     "loose_datasets_one_schedule": sorted(x for x, v in per_ds.items() if any(v)),
                     "registered_minimum": 3}
    out["claim2_cells_prefix_writes_fewer"] = sum(v["positions_prefix_over_rewrite"] < 1
                                                  for v in out["cells_detail"].values())
    out["claim3_cells_prefix_faster_than_rewrite"] = sum(v["prefix_over_rewrite"]["ratio_of_medians"] < 1
                                                         for v in out["cells_detail"].values())
    out["tie_cells"] = tie_cells
    dst = args.csv.parent
    (dst / "analysis.json").write_text(json.dumps(out, indent=1) + "\n")
    lines = ["# Experiment 15: maintenance cost without search (generated by analysis/analyze_maintenance.py)", "",
             "Measured on the measurement machine; median over five JVMs of the total maintenance time of batches 1..k.",
             "", "| dataset | schedule | batches | prefix ms | rewrite ms | rebuild ms | prefix/rebuild (sd) | "
             "prefix/rewrite | positions prefix/rewrite | prefix/rebuild, EUCS on |", "|" + "---|" * 10]
    for key, d in out["cells_detail"].items():
        e = d["prefix_over_rebuild_eucs_on"]
        lines.append(f"| {key.split('/')[0]} | {key.split('/')[1]} | {d['batches']} | {d[PREFIX]['median_total_ms']} | "
                     f"{d[REWRITE]['median_total_ms']} | {d[REBUILD]['median_total_ms']} | "
                     f"{d['prefix_over_rebuild']['ratio_of_medians']} ({d['prefix_over_rebuild']['per_rep_sd']}) | "
                     f"{d['prefix_over_rewrite']['ratio_of_medians']} | {d['positions_prefix_over_rewrite']} | "
                     f"{e['ratio_of_medians'] if e else 'not measured'} |")
    lines += ["", f"Claim 1 (prefix <= 0.5 x rebuild on >= 3 datasets): strict {out['claim1']['strict_datasets_both_schedules']}, "
              f"loose {out['claim1']['loose_datasets_one_schedule']}.",
              f"Claim 2: prefix writes fewer positions in {out['claim2_cells_prefix_writes_fewer']}/{len(cells)} cells.",
              f"Claim 3: prefix faster than rewrite in {out['claim3_cells_prefix_faster_than_rewrite']}/{len(cells)} cells.",
              f"Cells within three standard deviations of 0.5: {tie_cells or 'none'}.",
              f"Integrity: {len(problems)} problems; {det_compared} deterministic values compared."]
    (dst / "analysis.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
