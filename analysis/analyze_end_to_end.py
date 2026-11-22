#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Experiment 16 (end-to-end cost per batch under the mixed append model): integrity checks and registered statistics.

Reads one or more experiment16_end_to_end.csv files (a campaign and its tie-rule batch) and writes analysis.json and
analysis.md beside the first. Design approved by the author on 2026-11-22 (paper/giao_viec, end-to-end design draft):

- cell = (dataset, schedule, threshold); schedule ALL is the search-organization study on the final database;
- statistic of a cell and arm: the median over repetitions (one JVM each) of the total time of batches 1..k, where the
  time of a batch is its maintenance phase plus its search phase (System.nanoTime); batch 0 is reported apart. For
  schedule ALL the single batch 0 is the statistic;
- per-repetition ratios pair repetition r of one arm with repetition r of the other; their variability is the sample
  standard deviation;
- comparison outcome: a tie when |ratio of medians - 1| < 3 x that standard deviation, else a win (ratio < 1) or a loss;
  a cell where either arm has a time-out row (OT) is reported apart and counted in neither;
- tie rule: tied cells get five more repetitions in one extra batch; after it a cell still tied is a tie.

Comparisons of the proposed arm P = HAUSP-UB[prefix+noEUCS]:
  E2 against HAUSP-UB[rebuild+noEUCS] (re-mining with the same search). Both arms read the same structures, so the
     search does the same work (checked: equal candidate counts); a loss outside the tie band contradicts the code and
     is reported as a problem (exit 1);
  E3 against HAUSP-UB[noEUCS] (rewrite of whole touched sequences);
  E4 against Remine-PEAU (independent re-miner);
  secondary: HAUSP-UB[prefix] against HAUSP-UB[rebuild] (EUCS on), where measured.
Search organization (schedule ALL): each of HAUSP-UB[noL2+noEUCS], [noL2+L3@node+noEUCS], [nopool+noEUCS], and
Remine-PEAU against HAUSP-UB[noEUCS], on the search phase.

Integrity checks (exit 1 if one fails): every row OK or OT; one environment (JVM, heap, host, clean tree, data) with
several commits accepted only when git shows no difference in src/, pom.xml, scripts/campaign.py between them; every
threshold is a registered one for its dataset; the same repetitions for every arm of a cell; deterministic columns
(lines, appended, sequences, patterns, candidates) equal across repetitions; patterns equal across all arms at every
batch; candidates equal across the arms that share a search (the three noEUCS arms; the two EUCS arms).

Usage: python3 analysis/analyze_end_to_end.py --csv PATH [PATH ...]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P, REWRITE, REBUILD, REMINE = "HAUSP-UB[prefix+noEUCS]", "HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]", "Remine-PEAU"
P_E, REBUILD_E = "HAUSP-UB[prefix]", "HAUSP-UB[rebuild]"
COMPARISONS = (("E2_vs_rebuild", P, REBUILD), ("E3_vs_rewrite", P, REWRITE), ("E4_vs_remine", P, REMINE),
               ("eucs_on_vs_rebuild", P_E, REBUILD_E))
SEARCH_STUDY = ("HAUSP-UB[noL2+noEUCS]", "HAUSP-UB[noL2+L3@node+noEUCS]", "HAUSP-UB[nopool+noEUCS]", REMINE)
SAME_SEARCH = ((P, REWRITE, REBUILD), (P_E, REBUILD_E))
DET = ("lines", "appended", "sequences", "patterns", "candidates")
MEASURED_CODE = ("src", "pom.xml", "scripts/campaign.py")
REGISTRATIONS = {"TAFENG": "20261111-101639", "REES46_MULTI": "20261111-101645", "HM": "20261111-103222",
                 "REES46_COSMETICS": "20261120-201448"}


def registered_xi() -> dict:
    out = {}
    for ds, run in REGISTRATIONS.items():
        r = json.loads((ROOT / "results-probe" / "select-xi" / run / "registration.json").read_text())[ds]
        vals = [v["xi"] for v in r["accepted"]] if "accepted" in r else [v["xi"] for v in r.values() if v]
        out[ds] = set(vals)
    return out


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


def compare(tot, cell, num, den):
    """Outcome of num against den in one cell; tot[(cell, arm)] maps repetition -> total ns, or None for a time-out."""
    a, b = tot.get((cell, num)), tot.get((cell, den))
    if a is None or b is None:
        return None
    if any(v is None for v in a.values()) or any(v is None for v in b.values()):
        return {"outcome": "time-out", "ot_arms": [x for x, t in ((num, a), (den, b)) if any(v is None for v in t.values())]}
    reps = sorted(set(a) & set(b))
    per = [a[r] / b[r] for r in reps]
    med = statistics.median(a[r] for r in reps) / statistics.median(b[r] for r in reps)
    sd = statistics.stdev(per) if len(per) > 1 else float("inf")
    outcome = "tie" if abs(med - 1) < 3 * sd else ("win" if med < 1 else "loss")
    return {"ratio_of_medians": round(med, 4), "repetitions": len(reps), "per_rep": [round(x, 4) for x in per],
            "per_rep_sd": round(sd, 4), "outcome": outcome, "repetitions_above_1": sum(x > 1 for x in per)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, nargs="+", required=True)
    args = ap.parse_args()
    paths = [c.resolve() for c in args.csv]
    prov, rows = collections.Counter(), []
    for c in paths:
        p_, r_ = read(c)
        prov.update(p_)
        rows += r_
    problems = []
    envs, commits, code_diff = {k[1:] for k in prov}, sorted({k[0] for k in prov}), {}
    if len(envs) != 1 or any(k[4] != "clean" for k in prov):
        problems.append(f"provenance is not one clean environment: {dict(prov)}")
    for c in commits[1:]:
        r = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", commits[0], c, "--", *MEASURED_CODE],
                           capture_output=True, text=True)
        code_diff[f"{commits[0]}..{c}"] = r.stdout.split() if r.returncode == 0 else [f"git failed: {r.stderr.strip()}"]
        if r.returncode != 0 or r.stdout.strip():
            problems.append(f"measured code differs between {commits[0]} and {c}: {code_diff[f'{commits[0]}..{c}']}")
    bad = [r for r in rows if r["status"] not in ("OK", "OT")]
    if bad:
        problems.append(f"{len(bad)} rows neither OK nor OT")
    reg = registered_xi()
    for r in rows:
        if float(r["xi"]) not in reg.get(r["dataset"], set()):
            problems.append(f"{r['dataset']} xi={r['xi']} is not a registered threshold")
            break
    runs = collections.defaultdict(dict)                     # (cell, arm, rep) -> batch -> row
    for r in rows:
        cell = (r["dataset"], r["schedule"], r["xi"])
        key = (cell, r["arm"], int(r["rep"]))
        if int(r["batch"]) in runs[key]:
            problems.append(f"{key} batch {r['batch']}: two rows")
        runs[key][int(r["batch"])] = r
    cells = sorted({k[0] for k in runs})
    arms_of = {c: sorted({a for cc, a, _ in runs if cc == c}) for c in cells}
    reps_of = {(c, a): sorted(rp for cc, aa, rp in runs if cc == c and aa == a) for c in cells for a in arms_of[c]}
    compared = 0
    for c in cells:
        rs = {tuple(reps_of[(c, a)]) for a in arms_of[c]}
        if len(rs) != 1:
            problems.append(f"{c}: arms have different repetitions {sorted(rs)}")
        for a in arms_of[c]:
            reps = reps_of[(c, a)]
            ref = runs[(c, a, reps[0])]
            for rp in reps[1:]:
                cur = runs[(c, a, rp)]
                for b in set(ref) & set(cur):
                    if ref[b]["status"] == cur[b]["status"] == "OK":
                        compared += 1
                        if any(ref[b][k] != cur[b][k] for k in DET):
                            problems.append(f"{c} {a} rep {rp} batch {b}: deterministic columns differ")
        rep1 = {a: runs[(c, a, reps_of[(c, a)][0])] for a in arms_of[c]}
        batches = sorted(set().union(*rep1.values()))
        for b in batches:
            ok = {a: rep1[a][b] for a in arms_of[c] if b in rep1[a] and rep1[a][b]["status"] == "OK"}
            if len({v["patterns"] for v in ok.values()}) > 1:
                problems.append(f"{c} batch {b}: pattern counts differ across arms "
                                f"{ {a: v['patterns'] for a, v in ok.items()} }")
            compared += max(0, len(ok) - 1)
            for group in SAME_SEARCH:
                g = {a: ok[a]["candidates"] for a in group if a in ok}
                if len(set(g.values())) > 1:
                    problems.append(f"{c} batch {b}: candidates differ inside a same-search group {g}")
                compared += max(0, len(g) - 1)

    tot, share = {}, {}
    for c in cells:
        for a in arms_of[c]:
            t, sh = {}, []
            for rp in reps_of[(c, a)]:
                r = runs[(c, a, rp)]
                first = 0 if c[1] == "ALL" else 1
                use = [r[b] for b in sorted(r) if b >= first]
                if any(x["status"] == "OT" for x in use) or not use:
                    t[rp] = None
                    continue
                col = ("search_ns",) if c[1] == "ALL" else ("maint_ns", "search_ns")
                t[rp] = sum(int(x[k]) for x in use for k in col)
                sh.append(sum(int(x["maint_ns"]) for x in use) / max(1, sum(int(x["maint_ns"]) + int(x["search_ns"]) for x in use)))
            tot[(c, a)] = t
            share[(c, a)] = round(statistics.median(sh), 4) if sh else None

    out = {"source": [str(p.relative_to(ROOT)) for p in paths], "provenance": [list(k) + [v] for k, v in prov.items()],
           "commits": commits, "measured_code_diff": code_diff, "rows": len(rows), "runs": len(runs),
           "cells": len(cells), "values_compared": compared, "problems": problems, "cells_detail": {}, "counts": {}}
    tie_cells = collections.defaultdict(list)
    for c in cells:
        key = "/".join(c)
        d = {"arms": {}}
        for a in arms_of[c]:
            t = tot[(c, a)]
            okv = [v for v in t.values() if v is not None]
            last = [runs[(c, a, rp)][max(runs[(c, a, rp)])] for rp in reps_of[(c, a)]]
            d["arms"][a] = {"median_total_ms": round(statistics.median(okv) / 1e6, 3) if len(okv) == len(t) else None,
                            "maintenance_share": share[(c, a)],
                            "time_outs": sum(v is None for v in t.values()),
                            "heap_live_last_batch_mb": round(statistics.median(int(x["heap_live_bytes"]) for x in last) / 2**20, 1),
                            "heap_peak_max_mb": round(max(int(x["heap_peak_bytes"]) for rp in reps_of[(c, a)]
                                                          for x in runs[(c, a, rp)].values()) / 2**20, 1)}
        pairs = COMPARISONS if c[1] != "ALL" else tuple((f"search_{s}", s, REWRITE) for s in SEARCH_STUDY)
        for name, num, den in pairs:
            res = compare(tot, c, num, den)
            d[name] = res
            if res is None:
                continue
            out["counts"].setdefault(name, collections.Counter())[res["outcome"]] += 1
            if res["outcome"] == "tie":
                tie_cells[name].append(key)
            if name == "E2_vs_rebuild" and res["outcome"] == "loss":
                problems.append(f"{key}: the prefix arm is slower than re-mining with the same search beyond the tie "
                                f"band (ratio {res['ratio_of_medians']}); the code does not allow it, find the cause")
        out["cells_detail"][key] = d
    out["counts"] = {k: dict(v) for k, v in out["counts"].items()}
    out["tie_cells"] = dict(tie_cells)
    dst = paths[0].parent
    (dst / "analysis.json").write_text(json.dumps(out, indent=1) + "\n")
    lines = ["# Experiment 16: end-to-end cost per batch (generated by analysis/analyze_end_to_end.py)", "",
             "Median over repetitions of the total time of batches 1..k (maintenance + search, System.nanoTime); "
             "schedule ALL: the search phase on the final database.", "",
             "| cell | arm | median ms | maintenance share | time-outs | live heap MB (last batch) | peak heap MB |",
             "|---|---|---|---|---|---|---|"]
    for key, d in out["cells_detail"].items():
        for a, v in d["arms"].items():
            lines.append(f"| {key} | {a} | {v['median_total_ms']} | {v['maintenance_share']} | {v['time_outs']} | "
                         f"{v['heap_live_last_batch_mb']} | {v['heap_peak_max_mb']} |")
    lines += ["", "| cell | comparison | ratio of medians (sd) | outcome |", "|---|---|---|---|"]
    for key, d in out["cells_detail"].items():
        for name, res in d.items():
            if name == "arms" or res is None:
                continue
            ratio = f"{res['ratio_of_medians']} ({res['per_rep_sd']})" if "ratio_of_medians" in res else "-"
            lines.append(f"| {key} | {name} | {ratio} | {res['outcome']} |")
    lines += ["", f"Counts: {json.dumps(out['counts'])}", f"Tied cells: {json.dumps(out['tie_cells'])}",
              f"Integrity: {len(problems)} problems; {compared} values compared."]
    lines += [f"- {p}" for p in problems[:20]]
    (dst / "analysis.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[-(6 + min(len(problems), 20)):]))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
