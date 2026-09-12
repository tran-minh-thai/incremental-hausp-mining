#!/usr/bin/env python3
"""Post-run check of generation 4 (results-2026-09c/: every arm of Exp 1, 2, 3, 9, 11 inside one campaign).

Generation 4 exists because cross-campaign repeatability was found to reach 28 % on two Experiment-7
cells (EXPERIMENT_CHANGELOG 2026-09-12), so every table that compares arms must come from a single
campaign. This script exits non-zero unless

  * completeness: every (arm, dataset, threshold, schedule, batch) cell that an earlier generation
    completed for a planned arm is present and SUCCESS here, with at least 3 trials (a configuration
    whose mean run exceeds 60 min may have one, the runners' uniform rule);
  * no new failures: no OT/OOM/ERROR row that the earlier generations did not already record;
  * counts unchanged: trial-0 Cand, Recursed, PrunedL2, PrunedL3, PrunedL3Node and HAUSP equal the
    earlier value for the same arm and cell (the code did not change, so they must);
  * single campaign: within one experiment, the run ids of all arms lie inside SPAN_H hours, which is
    the property the whole generation was run for;
  * provenance: every '# run_id' line has tree=clean, a commit descending from the timer fix, and no
    --profile-phases.

It then prints, without judging, how each arm's runtime moved against generation 3 or 2 — the drift
this generation was meant to eliminate from the tables.

Usage: python3 analysis/verify_gen4.py [--gen4 DIR]
"""
from __future__ import annotations
import argparse, subprocess, sys
from datetime import datetime
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import NEW_RESULTS, NEWER_RESULTS, NEWEST_RESULTS, OLD_RESULTS, OK, PAPER_UB, ROOT, read_optional  # noqa: E402

UB_CHAIN = ["HAUSP-UB[noL2+L3@node+nopool+noEUCS]", "HAUSP-UB[noL2+nopool+noEUCS]",
            "HAUSP-UB[noL2+noEUCS]", PAPER_UB]
PLAN = {1: ["EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM", PAPER_UB],
        2: ["EHAUSM-I", "HAUSP-UB[noL2+noEUCS]", "HAUSP-UB[noL3+noEUCS]", PAPER_UB],
        3: ["EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM", PAPER_UB],
        9: ["EHAUSM-I", "EHAUSM-R"] + UB_CHAIN,
        11: ["EHAUSM-I", "Pre-HAUSPM", PAPER_UB]}
FILES = {1: "exp1/experiment1_tightness.csv", 2: "exp2/experiment2_pruning_power.csv",
         3: "exp3/experiment3_scalability.csv", 9: "exp9/experiment9_attribution.csv",
         11: "exp11/experiment11_warm_start.csv"}
COUNT_COLS = ["Cand", "Recursed", "PrunedL2(IAUUB)", "PrunedL3(MFUUB)", "PrunedL3Node", "HAUSP"]
FIX_COMMIT = "a705348"
SPAN_H = 48.0


def keys(df: pd.DataFrame) -> pd.Series:
    sched = df["Schedule"].fillna("").astype(str).replace("", "equal") if "Schedule" in df.columns else "equal"
    return (df["Algorithm"].astype(str) + "|" + df["Dataset"].astype(str) + "|"
            + df["MinUtil"].round(6).astype(str) + "|" + df["DeltaRatio"].round(3).astype(str)
            + "|" + sched + "|" + df["BatchID"].astype(int).astype(str))


def group_of(k: str) -> str:
    return k.rsplit("|", 1)[0]


def provenance(path: Path, problems: list[str]) -> list[str]:
    ids = []
    for line in path.read_text().splitlines():
        if not line.startswith("# run_id="):
            continue
        fields = dict(f.split("=", 1) for f in line[2:].split(" cmd=")[0].split() if "=" in f)
        cmd = line.split(" cmd=", 1)[1] if " cmd=" in line else ""
        ids.append(fields.get("run_id", ""))
        if fields.get("tree") != "clean":
            problems.append(f"{path.name}: run {fields.get('run_id')} tree={fields.get('tree')}")
        if "--profile-phases" in cmd:
            problems.append(f"{path.name}: run {fields.get('run_id')} ran with --profile-phases")
        h = fields.get("git", "")
        if subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", FIX_COMMIT, h],
                          capture_output=True).returncode != 0:
            problems.append(f"{path.name}: run {fields.get('run_id')} commit {h} predates the timer fix {FIX_COMMIT}")
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gen4", default=str(NEWEST_RESULTS))
    a = ap.parse_args()
    g4 = Path(a.gen4)
    fail: list[str] = []
    info: list[str] = []
    drift: list[str] = []
    hours = 0.0
    for e, f in FILES.items():
        arms = PLAN[e]
        prev = None
        for d in (NEWER_RESULTS, NEW_RESULTS, OLD_RESULTS):   # newest first, legacy last
            cand = read_optional(d / f)
            if cand is None:
                continue
            prev = cand if prev is None else pd.concat([prev, cand], ignore_index=True)
        new = read_optional(g4 / f)
        if prev is None:
            fail.append(f"exp{e}: no earlier generation to compare against")
            continue
        prev = prev[prev["Algorithm"].isin(arms)].copy()
        prev_ok = prev[prev["Status"].isin(OK)].copy()
        # an arm measured in several generations appears several times; keep the newest row of each
        # (cell, trial), otherwise the per-arm totals below double-count it
        prev_ok = prev_ok.assign(_k=keys(prev_ok) + "|" + prev_ok["RunIndex"].astype(str)) \
                         .drop_duplicates("_k", keep="first").drop(columns="_k")
        expected = set(keys(prev_ok))
        if new is None:
            fail.append(f"exp{e}: generation-4 file missing ({len(expected)} cells expected)")
            continue
        extra = sorted(set(new["Algorithm"].unique()) - set(arms))
        if extra:
            fail.append(f"exp{e}: unplanned arms in generation 4: {extra}")
        new_ok = new[new["Status"].isin(OK)].copy()
        missing = sorted(expected - set(keys(new_ok)))
        if missing:
            fail.append(f"exp{e}: {len(missing)}/{len(expected)} expected cells missing, e.g. {missing[:3]}")
        info.append(f"exp{e}: cells present {len(expected) - len(missing)}/{len(expected)}, arms {sorted(new_ok['Algorithm'].unique())}")
        tr = new_ok.assign(g=[group_of(k) for k in keys(new_ok)]).groupby("g")["RunIndex"].nunique()
        tot = (new_ok.assign(g=[group_of(k) for k in keys(new_ok)])
               .groupby(["g", "RunIndex"])["tTotal(ms)"].sum().groupby("g").mean())
        few = [g for g, n in tr.items() if n < 3 and tot.get(g, 0) < 60 * 60000]
        if few:
            fail.append(f"exp{e}: {len(few)}/{len(tr)} groups under 3 trials without the 60-min exemption, e.g. {few[:3]}")
        bad = new[~new["Status"].isin(OK)]
        known = set(keys(prev[~prev["Status"].isin(OK)]))
        surprise = sorted(set(keys(bad)) - known)
        if surprise:
            fail.append(f"exp{e}: {len(surprise)} newly failing cells, e.g. {surprise[:3]}")
        p0 = prev_ok[prev_ok["RunIndex"] == 0].assign(k=lambda d: keys(d)).drop_duplicates("k").set_index("k")
        n0 = new_ok[new_ok["RunIndex"] == 0].assign(k=lambda d: keys(d)).drop_duplicates("k").set_index("k")
        common = p0.index.intersection(n0.index)
        cols = [c for c in COUNT_COLS if c in p0.columns and c in n0.columns]
        # a column a baseline never writes is empty on both sides; NaN != NaN would read as a mismatch
        mism = {c: int((p0.loc[common, c].astype(float).fillna(-1) != n0.loc[common, c].astype(float).fillna(-1)).sum())
                for c in cols}
        if any(mism.values()):
            fail.append(f"exp{e}: counts changed over {len(common)} cells: {mism}")
        info.append(f"exp{e}: counts identical on {len(common)} cells ({', '.join(cols)})")
        ids = provenance(g4 / f, fail)
        stamps = []
        for r in ids:
            try:
                stamps.append(datetime.strptime(r, "%Y%m%d-%H%M"))
            except ValueError:
                pass
        if stamps:
            span = (max(stamps) - min(stamps)).total_seconds() / 3600
            info.append(f"exp{e}: {len(ids)} runs spanning {span:.1f} h")
            if span > SPAN_H:
                fail.append(f"exp{e}: arms span {span:.1f} h (> {SPAN_H} h); this generation exists to keep one experiment inside one campaign")
        hours += new_ok["tTotal(ms)"].sum() / 3.6e6
        pt = prev_ok.groupby(["Dataset", "Algorithm", "RunIndex"])["tTotal(ms)"].sum().groupby(["Dataset", "Algorithm"]).mean()
        nt = new_ok.groupby(["Dataset", "Algorithm", "RunIndex"])["tTotal(ms)"].sum().groupby(["Dataset", "Algorithm"]).mean()
        both = pt.index.intersection(nt.index)
        if len(both):
            r = (nt[both] / pt[both]).groupby("Algorithm")
            drift.append(f"exp{e} gen4/earlier per arm: " + ", ".join(f"{a_.replace('HAUSP-UB','UB')}={v.min():.2f}-{v.max():.2f}" for a_, v in r))
    info.append(f"generation 4 measured CPU time: {hours:.1f} h")
    print("== hard checks"); print("\n".join("  " + s for s in info))
    print("== drift against the earlier campaigns (informational)"); print("\n".join("  " + s for s in drift))
    if fail:
        print("== FAIL"); print("\n".join("  " + s for s in fail)); return 2
    print("== PASS: generation 4 complete, counts unchanged, one campaign per experiment, provenance clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
