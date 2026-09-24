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


import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PAPER_UB, load_config, load_experiment  # noqa: E402


def load(exp: int) -> pd.DataFrame:
    """Merged legacy + 2026-09 rows of one experiment (provenance lines skipped)."""
    df = load_experiment(exp)
    if df is None:
        raise SystemExit(f"no results for experiment {exp}")
    return df


def _cfg_exp(exp: int) -> dict:
    """The launcher's own declaration of one experiment.

    Checks that ask "is every declared arm there" must take the arms from the
    declaration, never from a list typed here: a typed list cannot notice a
    rename, and it reports the rename as a missing measurement. load_config()
    refuses a dump older than the sources, so a stale declaration cannot answer.
    """
    for spec in load_config().get("experiments", []):
        if spec.get("id") == exp:
            return spec
    return {}


e1 = load(1)
e2 = load(2)
e3 = load(3)
e4 = load(4)
e5 = load(5)
e6 = load(6)
e7 = load(7)
e8 = load(8)

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

# A2: at least 3 distinct trials per successful config (exp1-4, 8); more are
# allowed (adaptive repeats for short configurations), duplicates are not.
for name, df in (("exp1", e1), ("exp2", e2), ("exp3", e3), ("exp4", e4), ("exp8", e8)):
    ok = df[df["Status"].isin(OK)]
    key = ["Dataset", "Algorithm", "BatchID", "MinUtil", "DeltaRatio"]
    cnt = ok.groupby(key)["RunIndex"].agg(["count", "nunique"])
    bad = cnt[(cnt["count"] < 3) | (cnt["nunique"] != cnt["count"])]
    if bad.empty:
        report("PASS", f"A2 {name}: every config has >= 3 distinct trials",
               f"{len(cnt)} configs, trials {cnt['count'].min()}-{cnt['count'].max()}")
    else:
        report("FAIL", f"A2 {name}: configs with fewer than 3 trials or duplicate trial indices",
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
    if len(complete_trials) >= 3 or has_fail:
        okgrp += 1
    elif long_single:
        okgrp += 1
        single7 += 1
    else:
        bad7.append((ds, algo, k, len(complete_trials)))
if not bad7:
    report("PASS", "A3 exp7: every (dataset,algo,K) has >= 3 complete trials, a recorded failure, or the single-trial rule",
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

# B3: every ablation arm of exp2 covers the full sweep grid on every database.
# A cell is covered when it has a verdict: a SUCCESS row or a recorded OT/OOM
# (the true Layer-1-only arm exceeds the limit at the first threshold everywhere).
#
# The arms are read from the config the launcher dumped, not named here. A
# hardcoded list was here and it named three arms no spec declares any more:
# they are the pre-rename names of arms that now carry a bracket suffix, so they
# exist only in artifacts older than the rename. That list reported a hole for
# every database measured after it -- a naming artifact wearing the shape of a
# missing measurement -- while the arms actually declared went unchecked.
_declared = [a for a in (_cfg_exp(2).get("algorithms") or []) if a.startswith("HAUSP-UB")]
if not _declared:
    report("FAIL", "B3: every declared ablation arm covers the full sweep grid",
           "the config dump declares no HAUSP-UB arm for exp2; nothing could be checked")
else:
    _want = e2.groupby("Dataset")["MinUtil"].nunique().to_dict()
    _cells = 0
    for variant in _declared:
        sub = e2[(e2["Algorithm"] == variant) & e2["Status"].isin(OK | {"OT", "OOM", "SKIPPED"})]
        got = sub.groupby("Dataset")["MinUtil"].nunique().to_dict()
        holes = {d: (got.get(d, 0), w) for d, w in _want.items() if got.get(d, 0) != w}
        _cells += sum(got.values())
        report("PASS" if not holes else "FAIL",
               f"B3: arm {variant} covers full sweep grid",
               f"{sum(got.values())} of {sum(_want.values())} cells"
               if not holes else f"holes (got, want) {holes}")
    print(f"      B3 denominator: {len(_declared)} declared arm(s) x "
          f"{len(_want)} database(s), {_cells} covered cells")
    _undeclared = sorted({a for a in set(e2["Algorithm"])
                          if a.startswith("HAUSP-UB") and a not in _declared})
    if _undeclared:
        print(f"      B3 note: {len(_undeclared)} arm name(s) present in artifacts but "
              f"declared by no spec, so not checked: {_undeclared}")

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
l1 = e4[(e4["Algorithm"] == "HAUSP-UB-L1") & e4["Status"].isin(OK | {"OT", "OOM"})]
report("PASS" if l1["Dataset"].nunique() == e4["Dataset"].nunique() else "FAIL",
       "B7: exp4 has a verdict (SUCCESS or OT/OOM) for HAUSP-UB-L1 on every dataset",
       f"{l1['Dataset'].nunique()} datasets, statuses {sorted(l1['Status'].unique())}")

# B8: tightness populated where defined (PEAU on EHAUSM rows;
# IAUUB/MFUUB on HAUSP-UB rows) + candidate-count reduction demonstrates
# the over-estimation of aggregate bounds empirically.
hu1 = e1[(e1["Algorithm"] == "HAUSP-UB") & e1["Status"].isin(OK)]
eh1 = e1[(e1["Algorithm"] == "EHAUSM-I") & e1["Status"].isin(OK)]
tp = eh1["TightnessPEAU"].mean()
ti, tm = hu1["TightnessIAUUB"].mean(), hu1["TightnessMFUUB"].mean()
pop = tp > 0 and ti > 0 and tm > 0
# Lists assembled use the unified count; legacy HAUSP-UB rows carry NaN until the
# counts re-run exists, in which case the ratio is reported as not computable.
cand_ratio = (eh1.groupby("Dataset")["CandUnified"].sum(min_count=1) /
              hu1.groupby("Dataset")["CandUnified"].sum(min_count=1)).dropna()
if len(cand_ratio):
    report("PASS" if pop else "WARN",
           "B8: tightness populated per source + lists-assembled ratio EHAUSM-I/HAUSP-UB",
           f"PEAU={tp:.3f} (EHAUSM-I), IAUUB={ti:.3f}, MFUUB={tm:.3f} (HAUSP-UB); "
           f"ratio {cand_ratio.min():.2f}x-{cand_ratio.max():.2f}x on {len(cand_ratio)} datasets")
else:
    report("WARN", "B8: lists-assembled ratio EHAUSM-I/HAUSP-UB not computable",
           "no HAUSP-UB rows with a lists-assembled count yet (counts re-run missing)")

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
# Oracle vs the arm the paper presents (PAPER_UB when measured, else the legacy arm).
p5 = e5[e5["Status"].isin(OK)].pivot_table(index=["Dataset", "MinUtil"],
                                           columns="Algorithm", values="HAUSP",
                                           aggfunc="first")
ub_col = PAPER_UB if PAPER_UB in p5.columns else "HAUSP-UB"
both5 = p5[["EHAUSM-R", ub_col]].dropna()
mism5 = both5[both5["EHAUSM-R"] != both5[ub_col]]
p5 = both5
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

# B14: one heap ceiling per result tree. The ceiling is part of the identity of a timing
# or memory number -- runs taken under different ones do not compare -- and nothing else
# looks at it: the CSV records it faithfully in a line every reader skips. A launcher
# shipping a different default is all it takes, and that had happened: the two Windows
# launchers defaulted to 16g while every recorded run was taken at 24g.
import re as _re
_HEAP = _re.compile(r"\bheap=(\S+)")
_root = Path(__file__).resolve().parent.parent


def _declared_arms() -> set[str]:
    """Every arm name some experiment declares, taken from the launcher's own dump."""
    out: set[str] = set()
    for spec in load_config().get("experiments", []):
        out.update(spec.get("algorithms") or [])
    return out


def _file_has_declared_ot(text: str) -> bool:
    """True when the file records an OT for an arm that is still declared somewhere.

    Reads the Algorithm and Status columns by their header positions rather than
    scanning for the substring "OT", which matches other fields and cannot tell an
    arm apart. Positional guessing is how a column-shift bug got into this file once.
    """
    import csv as _csv
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    if not lines:
        return False
    hdr = next(_csv.reader([lines[0]]))
    if "Algorithm" not in hdr or "Status" not in hdr:
        return False
    ia, ist = hdr.index("Algorithm"), hdr.index("Status")
    arms = _declared_arms()
    for l in lines[1:]:
        c = next(_csv.reader([l]))
        if len(c) > max(ia, ist) and c[ist] == "OT" and c[ia] in arms:
            return True
    return False


def _timing_trees() -> list[str]:
    """Every result tree whose files carry timing numbers, found by looking.

    A hardcoded list was here and it went stale the moment a generation was
    added: five trees were named, eleven existed, and the six unnamed ones held
    every artifact of the newest database. The ceilings below were then compared
    over part of the corpus while reporting a verdict that read like all of it.

    Two trees are excluded deliberately, not by oversight: the probe trees carry
    no timing columns at all, and the invariant tree carries counter numbers that
    are machine-independent by construction. Both distinctions are enforced by
    schema, so a file that does record a ceiling in one of them would be the
    schema violation to fix, not a ceiling to compare.
    """
    out = []
    for d in sorted(_root.iterdir()):
        if not d.is_dir() or not d.name.startswith("results"):
            continue
        if d.name.startswith("results-probe") or d.name == "results-invariant":
            continue
        out.append(d.name)
    return out


_PAPER_TREES = _timing_trees()
_seen, _files = {}, 0
for _tree in _PAPER_TREES:
    _base = _root / _tree
    if not _base.is_dir():
        continue
    for _p in sorted(_base.rglob("*.csv")):
        try:
            _head = _p.open(encoding="utf-8", errors="ignore").readline()
        except OSError:
            continue
        _m = _HEAP.search(_head)
        if _m:
            _files += 1
            _seen.setdefault(_tree, {}).setdefault(_m.group(1), []).append(str(_p.relative_to(_root)))
_mixed = {t: c for t, c in _seen.items() if len(c) > 1}
_all = sorted({c for t in _seen.values() for c in t})
if not _files:
    report("WARN", "B14: one heap ceiling per result tree",
           "no result file records a heap ceiling; nothing could be compared")
elif _mixed:
    report("FAIL", "B14: one heap ceiling per result tree",
           "; ".join(f"{t} mixes {sorted(c)} (e.g. {c[sorted(c)[0]][0]})" for t, c in _mixed.items()))
else:
    report("PASS", "B14: one heap ceiling per result tree",
           f"{_files} files across {len(_seen)} trees, all at {', '.join(_all)}")

# B15: every OT verdict that survives into a table was taken under a cap at least as long
# as the limit the launchers apply -- the limit the manuscript states.
#
# Three versions of this check had the wrong invariant, and each was corrected by a
# measurement. The first refused any tree whose files were not all at one cap, which
# demanded that a successful cell be re-measured because a neighbour was given longer;
# but the cap is a future.get(N, MINUTES) that takes no part in the computation until it
# fires, so SUCCESS and OOM do not depend on it -- only OT, and the skips behind it (the
# runner sets one flag per arm on its first failure). The second looked at files, where a
# superseded row stays for ever. The third asked every surviving OT to share one cap, and
# re-measuring two cells at 360 minutes showed why that is wrong too: both still did not
# finish batch 0, and "did not finish inside 360" implies "did not finish inside 90" on the
# same code, since a repeat would have to run four times faster and the widest run-to-run
# spread measured in Experiment 7 is 14.9%. So a cap ABOVE the stated limit is a stronger
# verdict, and only a cap BELOW it overclaims -- that cell might have finished inside the
# limit. The limit is read from the launchers, the source that enforces it.
from common import declared_time_limit, surviving_ot_cells  # noqa: E402
_limit, _limit_notes = declared_time_limit()
_arms_declared = _declared_arms()
_rows = []
for _exp in range(1, 12):
    try:
        _s = surviving_ot_cells(_exp, _arms_declared)
    except SystemExit:
        continue
    for _, _r in _s.iterrows():
        _rows.append(("exp%d %s/%s" % (_exp, _r["Dataset"], _r["Algorithm"]), _r["Caps"]))
_uncapped = sorted({n for n, c in _rows if not c})
for _n in _limit_notes:
    print("      B15 note: " + _n)
if not _rows:
    report("WARN", "B15: every surviving OT verdict holds at the stated time limit",
           "no OT verdict of a declared arm survives into any experiment; nothing to compare")
elif _limit is None:
    report("FAIL", "B15: every surviving OT verdict holds at the stated time limit",
           "the stated limit cannot be determined from the launchers: " + "; ".join(_limit_notes))
elif _uncapped:
    report("FAIL", "B15: every surviving OT verdict holds at the stated time limit",
           "%d of %d surviving OT cell(s) resolve to no cap: %s"
           % (len(_uncapped), len(_rows), "; ".join(_uncapped)))
else:
    _below = sorted({n for n, c in _rows if min(c) < _limit})
    _above: dict = {}
    for _n, _c in _rows:
        if min(_c) > _limit:
            _above.setdefault(min(_c), []).append(_n)
    if _below:
        report("FAIL", "B15: every surviving OT verdict holds at the stated time limit",
               "%d surviving OT cell(s) were cut under a cap below the %d-minute limit, so they "
               "may have finished inside it: %s" % (len(_below), _limit, "; ".join(_below)))
    else:
        _at = sum(1 for _, c in _rows if min(c) == _limit)
        report("PASS", "B15: every surviving OT verdict holds at the stated time limit",
               "%d surviving OT cell(s); %d at the %d-minute limit%s"
               % (len(_rows), _at, _limit,
                  "".join("; %d above it at %d min (%s)" % (len(v), k, ", ".join(sorted(set(v))))
                          for k, v in sorted(_above.items()))))

print()
print("=" * 78)
if issues:
    print(f"AUDIT RESULT: {len(issues)} item(s) need attention:")
    for i in issues:
        print(f"  - {i}")
else:
    print("AUDIT RESULT: ALL CHECKS PASSED — the collected data passes every consistency check.")

# This printed its findings and exited 0, so every caller that tested the exit code was told
# the data was clean no matter what it found: a gate that does not close is a log line.
import sys as _sys
_sys.exit(1 if issues else 0)
