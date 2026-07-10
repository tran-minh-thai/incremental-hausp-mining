#!/usr/bin/env python3
"""Consistency audit of the collected measurements.

Verifies the results/exp*/ CSVs directly (not the configuration): trial
counts, baseline coverage, schedule uniformity, exactness against the
re-mining oracle, and threshold anchoring. Prints PASS / FAIL / WARN per
check with evidence.

Usage: python3 analysis/audit_results.py
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
R = ROOT / "results"
OK = {"SUCCESS", "SUCCESS_MATCH"}
issues: list[str] = []


def report(status: str, label: str, detail: str = "") -> None:
    print(f"[{status:4s}] {label}" + (f" — {detail}" if detail else ""))
    if status != "PASS":
        issues.append(f"{status}: {label} — {detail}")


def load(p: str) -> pd.DataFrame:
    df = pd.read_csv(R / p)
    df.columns = [c.strip() for c in df.columns]
    return df


e1 = load("exp1/experiment1_tightness.csv")
e2 = load("exp2/experiment2_pruning_power.csv")
e3 = load("exp3/experiment3_scalability.csv")
e4 = load("exp4/experiment4_memory_prelarge.csv")
e5 = load("exp5/experiment5_accuracy.csv")
e6 = load("exp6/experiment6_multibatch_accuracy.csv")
e7 = load("exp7/experiment7_long_batch.csv")
e8 = load("exp8/experiment8_threshold_sensitivity.csv")

print("=" * 78)
print("A. DATA INTEGRITY")
print("=" * 78)

# A1: duplicates — same config key must not appear twice
for name, df in (("exp1", e1), ("exp2", e2), ("exp3", e3), ("exp4", e4),
                 ("exp7", e7), ("exp8", e8)):
    key = ["Dataset", "Algorithm", "BatchID", "RunIndex", "MinUtil", "DeltaRatio"]
    dup = df[df.duplicated(subset=key, keep=False)]
    if dup.empty:
        report("PASS", f"A1 {name}: no duplicate config rows")
    else:
        report("FAIL", f"A1 {name}: duplicate rows",
               f"{len(dup)} rows, e.g. {dup[key].iloc[0].to_dict()}")

# A2: exactly 3 trials per successful config (exp1-4, 8)
for name, df in (("exp1", e1), ("exp2", e2), ("exp3", e3), ("exp4", e4), ("exp8", e8)):
    ok = df[df["Status"].isin(OK)]
    key = ["Dataset", "Algorithm", "BatchID", "MinUtil", "DeltaRatio"]
    cnt = ok.groupby(key)["RunIndex"].agg(["count", "nunique"])
    bad = cnt[(cnt["count"] != 3) | (cnt["nunique"] != 3)]
    if bad.empty:
        report("PASS", f"A2 {name}: every config has exactly 3 distinct trials",
               f"{len(cnt)} configs")
    else:
        report("FAIL", f"A2 {name}: configs without exactly 3 trials",
               f"{len(bad)} configs, e.g. {bad.index[0]}")

# A3: exp7 — each (ds, algo, K): 3-trial complete, or a recorded failure, or a
# single-trial-rule group (trial 0 complete with compute time > 60 min; the
# uniform rule skips trials 2-3 for such long-running groups).
RULE_MS = 60 * 60000
e7 = e7.assign(K=(1.0 / e7["DeltaRatio"]).round().astype(int))
bad7, okgrp, single7 = [], 0, 0
for (ds, algo, k), g in e7.groupby(["Dataset", "Algorithm", "K"]):
    succ = g[g["Status"] == "SUCCESS"]
    complete_trials = [r for r in sorted(set(succ["RunIndex"]))
                       if len(succ[succ["RunIndex"] == r]) == k]
    has_fail = bool(len(g[g["Status"].isin(("OT", "OOM", "ERROR"))]))
    t0_ms = succ[succ["RunIndex"] == 0]["tTotal(ms)"].sum()
    long_single = (0 in complete_trials) and t0_ms > RULE_MS
    if len(complete_trials) == 3 or has_fail:
        okgrp += 1
    elif long_single:
        okgrp += 1
        single7 += 1
    else:
        bad7.append((ds, algo, k, len(complete_trials)))
if not bad7:
    report("PASS", "A3 exp7: every (dataset,algo,K) is 3-trial complete, failed, or single-trial-rule",
           f"{okgrp} groups ({single7} single-trial by the uniform >60-min rule)")
else:
    report("FAIL", "A3 exp7: incomplete groups without failure record", str(bad7[:5]))

print()
print("=" * 78)
print("B. REVIEWER REQUIREMENTS vs DATA")
print("=" * 78)

# B1: runtime reported with >=3 trials and nonzero
for name, df in (("exp1", e1), ("exp2", e2), ("exp3", e3), ("exp4", e4), ("exp8", e8)):
    ok = df[df["Status"].isin(OK)]
    zero = ok[ok["tTotal(ms)"] <= 0]
    report("PASS" if zero.empty else "WARN",
           f"B1 {name}: runtime present and > 0 in all SUCCESS rows",
           "" if zero.empty else f"{len(zero)} rows with tTotal<=0")

# B2: Pre-HAUSPM present in exp1 for every dataset; baseline coverage map
ds_all = sorted(set(e1["Dataset"]))
missing = [ds for ds in ds_all
           if e1[(e1["Dataset"] == ds) & (e1["Algorithm"] == "Pre-HAUSPM")
                 & e1["Status"].isin(OK)].empty]
report("PASS" if not missing else "FAIL",
       "B2: Pre-HAUSPM present in exp1 on every dataset",
       f"7 datasets checked" if not missing else f"missing on {missing}")
print("      baseline coverage per experiment (needs written justification where absent):")
for name, df in (("exp1", e1), ("exp2", e2), ("exp3", e3), ("exp4", e4),
                 ("exp5", e5), ("exp7", e7), ("exp8", e8)):
    if "Algorithm" in df.columns:
        print(f"        {name}: {sorted(set(df['Algorithm']))}")

# B3: ablation variants L1-only and L1+L3 in exp2, full grid
for variant in ("HAUSP-UB-L1", "HAUSP-UB-L1L3", "HAUSP-UB*", "HAUSP-UB"):
    sub = e2[(e2["Algorithm"] == variant) & e2["Status"].isin(OK)]
    got = sub.groupby("Dataset")["MinUtil"].nunique().to_dict()
    want = e2.groupby("Dataset")["MinUtil"].nunique().to_dict()
    holes = {d: (got.get(d, 0), w) for d, w in want.items() if got.get(d, 0) != w}
    report("PASS" if not holes else "FAIL",
           f"B3: variant {variant} covers full sweep grid",
           f"{sum(got.values())} configs" if not holes else f"holes {holes}")

# B4: every anchor threshold of exp1/3/4/6/7 appears in exp2 sweep (per dataset)
anchors: dict[str, set] = {}
for df in (e1, e3, e4, e7):
    for ds, g in df.groupby("Dataset"):
        anchors.setdefault(ds, set()).update(np.round(g["MinUtil"], 6))
for ds, g in e6.groupby("Dataset"):
    if ds.lower() == "example":
        continue
    ds_map = {"example": "EXAMPLE"}
    key = ds.upper() if ds.upper() in anchors else ds
    if key in anchors:
        anchors[key].update(np.round(g["MinUtil"], 6))
swept = {ds: set(np.round(g["MinUtil"], 6)) for ds, g in e2.groupby("Dataset")}
holes = {ds: sorted(a - swept.get(ds, set())) for ds, a in anchors.items()
         if a - swept.get(ds, set())}
report("PASS" if not holes else "FAIL",
       "B4: all anchor thresholds included in exp2 sweep (from data)",
       "" if not holes else str(holes))

# B5: K sweep coverage — which datasets demonstrate K=100
surv = {}
for (ds, algo), g in e7.groupby(["Dataset", "Algorithm"]):
    ks = []
    for k, gg in g[g["Status"] == "SUCCESS"].groupby("K"):
        if any(len(gg[gg["RunIndex"] == r]) == k for r in set(gg["RunIndex"])):
            ks.append(k)
    surv[(ds, algo)] = max(ks) if ks else 0
k100 = sorted({ds for (ds, a), k in surv.items() if a == "HAUSP-UB" and k >= 100})
report("PASS" if k100 else "FAIL",
       "B5: HAUSP-UB demonstrates K=100 (volume fixed) on at least one dataset",
       f"K=100 complete on {k100}")
capped = sorted({ds for (ds, a), k in surv.items() if k <= 20})
print(f"      NOTE: datasets not shown beyond K=20 (needs written justification): {capped}")

# B6: eta computable in exp8 and sweep extends below exp2 range
ok8 = e8[e8["Status"].isin(OK)]
zero_h = ok8[ok8["HAUSP"] <= 0]
report("PASS" if zero_h.empty else "WARN",
       "B6a: HAUSP > 0 in all exp8 rows (eta well-defined)",
       "" if zero_h.empty else f"{len(zero_h)} rows with 0 patterns")
below = {}
for ds in sorted(set(ok8["Dataset"])):
    lo8 = ok8[ok8["Dataset"] == ds]["MinUtil"].min()
    lo2 = e2[e2["Dataset"] == ds]["MinUtil"].min()
    below[ds] = (round(lo8, 6), round(lo2, 6), bool(lo8 < lo2))
bad6 = {d: v for d, v in below.items() if not v[2]}
report("PASS" if not bad6 else "WARN",
       "B6b: exp8 sweep extends BELOW exp2 range on every dataset",
       "" if not bad6 else f"not lower on {bad6}")
# spot value quoted in the paper: BIBLE at 0.00025 (135,751 patterns)
b25 = ok8[(ok8["Dataset"] == "BIBLE") & (np.isclose(ok8["MinUtil"], 0.00025))]
report("PASS" if len(b25) else "WARN",
       "B6c: BIBLE eta measured at the paper-quoted 0.025% (0.00025)",
       f"{len(b25)} rows" if len(b25) else "exp8 BIBLE stops at 0.0003; expected 0.00025")

# B7: engineering-vs-pruning ablation in exp4 (L1 variant present)
l1 = e4[(e4["Algorithm"] == "HAUSP-UB-L1") & e4["Status"].isin(OK)]
report("PASS" if len(l1) else "FAIL",
       "B7: exp4 contains HAUSP-UB-L1 (same engineering, no AU bounds)",
       f"{l1['Dataset'].nunique()} datasets")

# B8: tightness populated where defined (PEAU on EHAUSM rows;
# IAUUB/MFUUB on HAUSP-UB rows) + candidate-count reduction demonstrates
# the over-estimation of aggregate bounds empirically.
hu1 = e1[(e1["Algorithm"] == "HAUSP-UB") & e1["Status"].isin(OK)]
eh1 = e1[(e1["Algorithm"] == "EHAUSM-I") & e1["Status"].isin(OK)]
tp = eh1["TightnessPEAU"].mean()
ti, tm = hu1["TightnessIAUUB"].mean(), hu1["TightnessMFUUB"].mean()
pop = tp > 0 and ti > 0 and tm > 0
cand_ratio = (eh1.groupby("Dataset")["Cand"].sum() /
              hu1.groupby("Dataset")["Cand"].sum())
report("PASS" if pop and cand_ratio.min() > 1 else "WARN",
       "B8: tightness populated per source + candidate reduction vs PEAU",
       f"PEAU={tp:.3f} (EHAUSM-I), IAUUB={ti:.3f}, MFUUB={tm:.3f} (HAUSP-UB); "
       f"cand reduction {cand_ratio.min():.0f}x–{cand_ratio.max():.0f}x")

# B9: layer breakdown populated for HAUSP-UB
lay = hu1[["tLayer1(ms)", "tLayer2(ms)", "tLayer3(ms)"]].sum().sum()
report("PASS" if lay > 0 else "FAIL",
       "B9: per-layer time breakdown populated",
       f"sum(tLayer1..3) = {lay:.0f} ms over exp1 HAUSP-UB rows")

# B10: pool statistics populated
pool = hu1[["PoolBorrows", "PoolReuses", "PoolPeakLive"]].sum().sum() if "PoolBorrows" in hu1 else 0
report("PASS" if pool > 0 else "WARN",
       "B10 R7: AU-DUL pool statistics populated", f"sum = {pool:.0f}")

# B11: correctness — every exp5 config matches; exp6 all SUCCESS_MATCH
p5 = e5[e5["Status"].isin(OK)].pivot_table(index=["Dataset", "MinUtil"],
                                           columns="Algorithm", values="HAUSP",
                                           aggfunc="first")
mism5 = p5[p5.iloc[:, 0] != p5.iloc[:, 1]] if p5.shape[1] == 2 else p5
report("PASS" if len(mism5) == 0 else "FAIL",
       "B11a: exp5 pattern counts identical to oracle on every config",
       f"{len(p5)} configs compared")
cnt6 = [c for c in e6.columns if c.startswith("HAUSP_")]
mism6 = e6[e6[cnt6[0]] != e6[cnt6[1]]] if len(cnt6) == 2 else e6
nm6 = e6[e6["Status"] != "SUCCESS_MATCH"]
report("PASS" if (len(mism6) == 0 and len(nm6) == 0) else "FAIL",
       "B11b: exp6 multi-batch counts match on every batch",
       f"{len(e6)} batches compared")

# B13: exact algorithms mutually consistent; Pre-HAUSPM never exceeds exact
mismatch, excess = 0, 0
for df in (e1, e3, e4, e7):
    ok = df[df["Status"].isin(OK) & (df["RunIndex"] == 0)]
    piv = ok.pivot_table(index=["Dataset", "BatchID", "MinUtil", "DeltaRatio"],
                         columns="Algorithm", values="HAUSP", aggfunc="first")
    exact = [c for c in ("HAUSP-UB", "EHAUSM-R", "EHAUSM-I", "HAUSP-UB-L1") if c in piv.columns]
    ref = piv[exact[0]]
    for c in exact[1:]:
        mismatch += int((piv[c].notna() & ref.notna() & (piv[c] != ref)).sum())
    if "Pre-HAUSPM" in piv.columns:
        both = piv["Pre-HAUSPM"].notna() & ref.notna()
        excess += int((piv.loc[both, "Pre-HAUSPM"] > ref[both]).sum())
report("PASS" if mismatch == 0 else "FAIL",
       "B13a exact algorithms (EHAUSM-R/I, HAUSP-UB, -L1) agree on every config",
       f"mismatch = {mismatch}")
report("PASS" if excess == 0 else "FAIL",
       "B13b Pre-HAUSPM never reports MORE patterns than exact (misses only)",
       f"excess = {excess}")

# B12: std magnitude sanity — CV of runtime
per = (e1[e1["Status"].isin(OK)]
       .groupby(["Dataset", "Algorithm", "RunIndex"])["tTotal(ms)"].sum()
       .groupby(["Dataset", "Algorithm"]).agg(["mean", "std"]))
per = per[per["mean"] > 5000]
cv = (per["std"] / per["mean"]).max()
report("PASS" if cv < 0.10 else "WARN",
       "B12: runtime CV < 10% on all configs with runtime > 5 s",
       f"max CV = {cv*100:.1f}%")

print()
print("=" * 78)
if issues:
    print(f"AUDIT RESULT: {len(issues)} item(s) need attention:")
    for i in issues:
        print(f"  - {i}")
else:
    print("AUDIT RESULT: ALL CHECKS PASSED — the collected data passes every consistency check.")
