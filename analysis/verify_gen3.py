#!/usr/bin/env python3
"""Post-run check of generation 3 (results-2026-09b/: HAUSP-UB arms re-measured without per-node timers).

Compares the new directory with generation 2 (results-2026-09/) and exits non-zero unless
  * completeness: every (arm, dataset, threshold, schedule, batch) cell that generation 2 completed
    for the planned arms is present and SUCCESS in generation 3 (the five Exp 7 OT cells are
    expected only with --with-ot), with at least 3 trials;
  * no new failures: no OT/OOM/ERROR row in generation 3 outside the five known OT cells;
  * counts identical (prediction P0): Cand, Recursed, PrunedL2, PrunedL3, PrunedL3Node, HAUSP of
    trial 0 equal generation 2 on every comparable cell;
  * provenance: every '# run_id' line has tree=clean, a commit that descends from a705348 (the
    timer fix) and no --profile-phases in its command.
Predictions P1-P5 (EXPERIMENT_CHANGELOG 2026-09-10 afternoon) are evaluated and printed, never
judged pass/fail: a refuted prediction is a result. Every check prints its denominator.

Usage: python3 analysis/verify_gen3.py [--gen3 DIR] [--with-ot]
"""
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import NEW_RESULTS, NEWER_RESULTS, OK, PAPER_UB, ROOT, load_experiment, read_optional  # noqa: E402

PLAN = {1: [PAPER_UB], 3: [PAPER_UB], 11: [PAPER_UB], 7: [PAPER_UB],
        2: [PAPER_UB, "HAUSP-UB[noL2+noEUCS]", "HAUSP-UB[noL3+noEUCS]"],
        9: ["HAUSP-UB[noL2+L3@node+nopool+noEUCS]", "HAUSP-UB[noL2+nopool+noEUCS]", "HAUSP-UB[noL2+noEUCS]", PAPER_UB]}
FILES = {1: "exp1/experiment1_tightness.csv", 2: "exp2/experiment2_pruning_power.csv", 3: "exp3/experiment3_scalability.csv",
         7: "exp7/experiment7_long_batch.csv", 9: "exp9/experiment9_attribution.csv", 11: "exp11/experiment11_warm_start.csv"}
OT7 = {("SIGN", 20), ("SIGN", 50), ("SIGN", 100), ("C8T1S5I8N5K", 50), ("C8T1S5I8N5K", 100)}
COUNT_COLS = ["Cand", "Recursed", "PrunedL2(IAUUB)", "PrunedL3(MFUUB)", "PrunedL3Node", "HAUSP"]
FIX_COMMIT = "a705348"
NS_PER_CALL = 365.0


def keys(df: pd.DataFrame) -> pd.Series:
    sched = df["Schedule"].fillna("").astype(str).replace("", "equal") if "Schedule" in df.columns else "equal"
    return (df["Algorithm"].astype(str) + "|" + df["Dataset"].astype(str) + "|" + df["MinUtil"].round(6).astype(str)
            + "|" + df["DeltaRatio"].round(3).astype(str) + "|" + sched + "|" + df["BatchID"].astype(int).astype(str))


def group_of(k: str) -> str:
    return k.rsplit("|", 1)[0]


def kcell(df: pd.DataFrame) -> pd.Series:
    return list(zip(df["Dataset"], (1.0 / df["DeltaRatio"]).round().astype(int)))


def provenance_ok(path: Path, problems: list[str]) -> int:
    n = 0
    for line in path.read_text().splitlines():
        if not line.startswith("# run_id="):
            continue
        n += 1
        fields = dict(f.split("=", 1) for f in line[2:].split(" cmd=")[0].split() if "=" in f)
        cmd = line.split(" cmd=", 1)[1] if " cmd=" in line else ""
        if fields.get("tree") != "clean":
            problems.append(f"{path.name}: run {fields.get('run_id')} tree={fields.get('tree')}")
        if "--profile-phases" in cmd:
            problems.append(f"{path.name}: run {fields.get('run_id')} ran with --profile-phases")
        h = fields.get("git", "")
        r = subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", FIX_COMMIT, h], capture_output=True)
        if r.returncode != 0:
            problems.append(f"{path.name}: run {fields.get('run_id')} commit {h} does not contain the timer fix {FIX_COMMIT}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gen3", default=str(NEWER_RESULTS)); ap.add_argument("--gen2", default=str(NEW_RESULTS))
    ap.add_argument("--with-ot", action="store_true", help="step c7ot ran: the five OT cells are expected too")
    a = ap.parse_args(); g3, g2 = Path(a.gen3), Path(a.gen2)
    fail: list[str] = []; info: list[str] = []
    hours = 0.0
    for e, f in FILES.items():
        old = read_optional(g2 / f); new = read_optional(g3 / f)
        arms = PLAN[e]
        if old is None:
            fail.append(f"exp{e}: generation-2 file missing"); continue
        old = old[old["Algorithm"].isin(arms)].copy()
        exp_ok = old[old["Status"].isin(OK)].copy()
        own_ok = exp_ok  # the arm's own generation-2 rows: the reference for the count check
        if e == 9:
            # the EUCS-free chain has two arms with no generation-2 rows: expect for every planned arm
            # the cells the paper arm completed in generation 2
            ref = exp_ok[exp_ok["Algorithm"] == PAPER_UB]
            exp_ok = pd.concat([ref.assign(Algorithm=a_) for a_ in arms], ignore_index=True)
        if e == 7 and not a.with_ot:
            exp_ok = exp_ok[[c not in OT7 for c in kcell(exp_ok)]]
        expected = set(keys(exp_ok))
        if new is None:
            fail.append(f"exp{e}: generation-3 file missing ({len(expected)} cells expected)"); continue
        new = new[new["Algorithm"].isin(arms)].copy()
        foreign = sorted(set(read_optional(g3 / f)["Algorithm"].unique()) - set(arms))
        if foreign:
            fail.append(f"exp{e}: unplanned arms in generation 3: {foreign}")
        new_ok = new[new["Status"].isin(OK)].copy()
        present = set(keys(new_ok))
        missing = sorted(expected - present)
        if missing:
            fail.append(f"exp{e}: {len(missing)}/{len(expected)} expected cells missing, e.g. {missing[:3]}")
        info.append(f"exp{e}: cells present {len(expected - set(missing))}/{len(expected)}")
        # trials per group
        tr3 = new_ok.assign(g=[group_of(k) for k in keys(new_ok)]).groupby("g")["RunIndex"].nunique()
        tr2 = exp_ok.assign(g=[group_of(k) for k in keys(exp_ok)]).groupby("g")["RunIndex"].nunique()
        # the runners' single-trial rule: a configuration whose run exceeds 60 min is measured once
        tot3 = new_ok.assign(g=[group_of(k) for k in keys(new_ok)]).groupby(["g", "RunIndex"])["tTotal(ms)"].sum().groupby("g").mean()
        few = [g for g, n in tr3.items() if n < 3 and tot3.get(g, 0) < 60 * 60000]
        if few:
            fail.append(f"exp{e}: {len(few)}/{len(tr3)} groups have fewer than 3 trials, e.g. {few[:3]}")
        fewer = [g for g in tr3.index if g in tr2.index and tr3[g] < tr2[g]]
        if fewer:
            info.append(f"exp{e}: {len(fewer)}/{len(tr3)} groups have fewer trials than generation 2 (adaptive rule; inspect): {fewer[:2]}")
        # new failures
        bad = new[~new["Status"].isin(OK)]
        if e == 7:
            bad = bad[[c not in OT7 for c in kcell(bad)]]
        if len(bad):
            fail.append(f"exp{e}: {len(bad)} failed rows in generation 3 outside the known OT cells: "
                        + ", ".join(f"{r.Dataset}/{r.Algorithm}/{r.Status}" for r in bad.head(3).itertuples()))
        # P0 counts
        o0 = own_ok[own_ok["RunIndex"] == 0].assign(k=lambda d: keys(d)).set_index("k")
        n0 = new_ok[new_ok["RunIndex"] == 0].assign(k=lambda d: keys(d)).set_index("k")
        common = o0.index.intersection(n0.index)
        cols = [c for c in COUNT_COLS if c in o0.columns and c in n0.columns]
        mism = {c: int((o0.loc[common, c].astype(float) != n0.loc[common, c].astype(float)).sum()) for c in cols}
        if any(mism.values()):
            fail.append(f"exp{e}: P0 count mismatches over {len(common)} cells: {mism}")
        info.append(f"exp{e}: P0 counts identical on {len(common)} cells ({', '.join(cols)})")
        nprov = provenance_ok(g3 / f, fail)
        info.append(f"exp{e}: {nprov} provenance lines checked")
        hours += new_ok["tTotal(ms)"].sum() / 3.6e6
    info.append(f"generation 3 measured CPU time: {hours:.1f} h")

    # predictions (informational)
    pred: list[str] = []
    try:
        o9 = read_optional(g2 / FILES[9]); n9 = read_optional(g3 / FILES[9])
        if n9 is not None:
            def tot(df, arm):
                d = df[(df["Algorithm"] == arm) & df["Status"].isin(OK)]
                return d.groupby(["Dataset", "RunIndex"])["tTotal(ms)"].sum().groupby("Dataset").mean() / 1000
            node, child = tot(n9, "HAUSP-UB[noL2+L3@node+nopool+noEUCS]"), tot(n9, "HAUSP-UB[noL2+nopool+noEUCS]")
            er = tot(o9, "EHAUSM-R")
            pred.append("P1 node-entry/child ratio (gen3), predicted <= 1.6: " + ", ".join(f"{d}={node[d]/child[d]:.2f}" for d in node.index if d in child.index))
            pred.append("P5 EHAUSM-R(gen2)/UB_layout(gen3), predicted 1.5-2.4 (SYN ~1.0): " + ", ".join(f"{d}={er[d]/node[d]:.2f}" for d in node.index if d in er.index))
        o1 = read_optional(g2 / FILES[1]); n1 = read_optional(g3 / FILES[1])
        if n1 is not None:
            def tot1(df, arm):
                d = df[(df["Algorithm"] == arm) & df["Status"].isin(OK)]
                return d.groupby(["Dataset", "RunIndex"])["tTotal(ms)"].sum().groupby("Dataset").mean()
            t2, t3 = tot1(o1, PAPER_UB), tot1(n1, PAPER_UB)
            d0 = o1[(o1["Algorithm"] == PAPER_UB) & o1["Status"].isin(OK) & (o1["RunIndex"] == 0)]
            tax = (d0.groupby("Dataset").apply(lambda g: (2 * g["Recursed"] + 2 * (g["Recursed"] - g["PrunedL3Node"].fillna(0))).sum() * NS_PER_CALL / 1e6)
                   / d0.groupby("Dataset")["tTotal(ms)"].sum())
            pred.append("P2 Exp 1 paper arm gen3/gen2 (predicted ~ 1 - tax share): " + ", ".join(f"{d}={t3[d]/t2[d]:.3f} (pred {1-tax[d]:.3f})" for d in t3.index if d in t2.index))
            er1 = tot1(load_experiment(1, unified=False), "EHAUSM-R")  # baselines live in the legacy directory
            pred.append("     speed-up vs EHAUSM-R with gen3: " + ", ".join(f"{d}={er1[d]/t3[d]:.2f}x" for d in t3.index if d in er1.index))
        o11 = read_optional(g2 / FILES[11]); n11 = read_optional(g3 / FILES[11])
        if n11 is not None:
            def m11(df, arm):
                d = df[(df["Algorithm"] == arm) & df["Status"].isin(OK)]
                return d.groupby(["Dataset", "RunIndex"])["tTotal(ms)"].sum().groupby("Dataset").mean() / 60000
            ub, ei = m11(n11, PAPER_UB), m11(o11, "EHAUSM-I")
            pred.append("P3 Exp 11 minutes, HAUSP-UB(gen3) vs EHAUSM-I(gen2), predicted SIGN ~6 < 9.1, SYN ~0.7 > 0.4: "
                        + ", ".join(f"{d}: {ub[d]:.1f} vs {ei[d]:.1f}" for d in ub.index if d in ei.index))
        if a.with_ot:
            n7 = read_optional(g3 / FILES[7])
            if n7 is not None:
                st = n7[[c in OT7 for c in kcell(n7)]].groupby(["Dataset", "DeltaRatio"])["Status"].agg(lambda s: "/".join(sorted(set(s))))
                pred.append("P4 Exp 7 OT cells (gen3 statuses, predicted OT@0 unchanged): " + "; ".join(f"{d}/K{round(1/r)}={s}" for (d, r), s in st.items()))
    except Exception as ex:  # predictions must never mask the hard checks
        pred.append(f"(prediction block error: {ex})")

    print("== hard checks"); print("\n".join("  " + s for s in info))
    print("== predictions (informational)"); print("\n".join("  " + s for s in pred))
    if fail:
        print("== FAIL"); print("\n".join("  " + s for s in fail)); return 2
    print("== PASS: generation 3 complete, counts identical, provenance clean"); return 0


if __name__ == "__main__":
    sys.exit(main())
