#!/usr/bin/env python3
"""Generate the plans scripts/campaign.py runs on the measurement machine.

Two plans, both generated, never written by hand:

  full        every quantity the manuscript prints, for every experiment, dataset and arm, one
              JVM per command, into results/ (live-heap runs into results/mem/). Commands are
              grouped per (quantity, experiment, dataset) with the arms of one comparison back
              to back, groups cheapest first.
  validation  one command of every shape in the full plan, on its cheapest cell, into
              results-probe/windows-validation/. It runs first on a new machine: a shape that
              fails there fails in minutes, and its artifacts are checked -- host, cap, heap,
              schema, and counts identical to the development machine's -- before the long run.
              One command is killed mid-run and rolled back, to test the rollback on that machine.

Durations come from the result trees of September 2026 on the development machine, frozen into
scripts/plans/costs-2026-09-mac.csv by --costs before those trees were deleted. They order the
commands and forecast the length; they are extrapolations to another machine, not measurements.

    python3 analysis/campaign_plan.py --costs     # once, while the old result trees still exist
    python3 analysis/campaign_plan.py             # write scripts/plans/{full,validation}.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, load_config, load_experiment, load_memory  # noqa: E402
from stale_cells import (DATASET_KEY, FIXES, NO_RID, PROTOCOL, _current, _git,  # noqa: E402
                         _provenance, arms_of, datasets_of)

PLANS = ROOT / "scripts" / "plans"
COSTS = PLANS / "costs-2026-09-mac.csv"
#: Every quantity a table, figure or sentence of the manuscript reads (see stale_cells.PRINTED,
#: plus Experiment 5, the single-batch half of the exactness table).
QUANTITIES = [("time", 1), ("time", 2), ("time", 3), ("time", 4), ("count", 5), ("count", 6),
              ("time", 7), ("mem", 7), ("time", 8), ("time", 9), ("time", 10), ("time", 11),
              ("mem", 3), ("mem", 4), ("mem", 9), ("mem", 11)]
#: Runners that compare their arms inside one run and write them together.
TOGETHER = {5, 6}
LIMIT_MIN = 90
FULL_DIR, PROBE_DIR = "results", "results-probe/windows-validation"


def write_costs() -> None:
    """CPU minutes and time-outs per (quantity, experiment, dataset, arm), from the current trees.

    current_code marks cells whose every row was produced by a commit that contains every code
    change in stale_cells.FIXES: only those can be compared count for count with a new machine
    without the comparison also testing a code change.
    """
    commits, _ = _provenance()
    pmap = json.loads((ROOT / "provenance_map.json").read_text())["commits"]
    fixes = [fx["commit"] for fx in FIXES]
    memo: dict = {}

    def current(rid: str) -> bool:
        if rid in NO_RID:
            return False
        if rid not in memo:
            cur = _current(commits.get(rid), pmap)
            memo[rid] = bool(cur) and all(_git("merge-base", "--is-ancestor", f, cur).returncode == 0 for f in fixes)
        return memo[rid]

    rows = []
    for kind, exp in QUANTITIES:
        df = load_memory(exp, keep_failures=True) if kind == "mem" else load_experiment(exp)
        if df is None or df.empty:
            continue
        df = df.assign(_wall=wall_minutes(df))
        # The reader labels a runner that writes its arms together (Experiment 6) as arm "all".
        for (ds, arm), g in df.groupby(["Dataset", "Algorithm"]):
            cpu = g["tTotal(ms)"].sum() / 60000 if "tTotal(ms)" in g.columns else None
            rows.append({"kind": kind, "exp": exp, "dataset": ds, "arm": arm,
                         "cpu_min": None if cpu is None else round(float(cpu), 3),
                         "wall_min": round(float(g["_wall"].sum()), 3),
                         "timeouts": int((g["Status"] == "OT").sum()) if "Status" in g.columns else 0,
                         # OT, OOM and ERROR rows carry no timestamp, so wall_min misses their time.
                         "failures": int(g["Status"].isin(["OT", "OOM", "ERROR"]).sum()) if "Status" in g.columns else 0,
                         "rows": len(g),
                         "current_code": all(current(str(r)) for r in set(g["RunID"])) if "RunID" in g.columns else False,
                         "run_ids": " ".join(sorted(set(map(str, g["RunID"])))) if "RunID" in g.columns else ""})
    PLANS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(COSTS, index=False)
    print(f"wrote {COSTS.relative_to(ROOT)}: {len(rows)} cells")


def wall_minutes(df: pd.DataFrame) -> pd.Series:
    """Wall-clock minutes behind each row: the gap to the previous row of the same run.

    CPU time alone misses what happens between batches -- the forced collections and settling
    time of RunIsolation -- which on a 100-batch schedule outweighs the mining itself (a 0.6
    CPU-minute Experiment 7 cell took 694 s on 2026-09-25). Rows of one JVM are written one after
    another, so the gap between consecutive timestamps of a run is what the row cost. The first
    row of a run is charged its own runtime; a gap longer than the row could have taken (the
    run's file was appended to in another session) is charged the same.
    """
    ts = pd.to_datetime(df["Timestamp"], errors="coerce") if "Timestamp" in df.columns else None
    own = (df["tTotal(ms)"].fillna(0) / 60000) if "tTotal(ms)" in df.columns else pd.Series(0.0, index=df.index)
    if ts is None:
        return own
    run = df["RunID"].astype(str) if "RunID" in df.columns else pd.Series("", index=df.index)
    legacy = run.isin(NO_RID)
    if "SourceFile" in df.columns:
        run = run.where(~legacy, "legacy:" + df["SourceFile"].astype(str))
    out = own.copy()
    for _, idx in df.groupby(run).groups.items():
        sub = ts.loc[idx].sort_values()
        gap = sub.diff().dt.total_seconds() / 60
        ceiling = own.loc[sub.index] + LIMIT_MIN + 10      # an OT row legitimately takes the whole limit
        ok = gap.notna() & (gap >= 0) & (gap <= ceiling)
        out.loc[sub.index[ok.values]] = gap[ok].values
    return out


def estimate(costs: pd.DataFrame, kind: str, exp: int, ds: str, arms: list[str]) -> float:
    """Wall-clock minutes of the rows the cell replaces; an unmeasured cell takes the median of the
    same arm on the other datasets."""
    total = 0.0
    for arm in arms:
        r = costs[(costs.kind == kind) & (costs.exp == exp) & (costs.dataset == ds) & (costs.arm == arm)]
        if len(r) and pd.notna(r.wall_min.iloc[0]) and r.wall_min.iloc[0] > 0:
            total += float(r.wall_min.iloc[0])
            continue
        peers = costs[(costs.kind == kind) & (costs.exp == exp) & (costs.arm == arm) & (costs.wall_min > 0)]
        if len(peers):
            total += float(peers.wall_min.median())
    return round(total, 2)


def cells(cfg: dict, costs: pd.DataFrame):
    """Every command of the full campaign as (kind, exp, dataset, algo, estimate)."""
    for kind, exp in QUANTITIES:
        if (kind, exp) not in PROTOCOL:
            raise SystemExit(f"no protocol for {kind} experiment {exp}; add it to stale_cells.PROTOCOL")
        # The toy dataset is declared for Experiment 6 too, but no table prints it.
        for ds in [d for d in datasets_of(kind, exp, cfg) if d != "example"]:
            if exp in TOGETHER:
                arms = list(cfg[exp]["algorithms"])
                est = estimate(costs, kind, exp, ds, arms) or estimate(costs, kind, exp, ds, ["all"])
                yield kind, exp, ds, ",".join(arms), est
            else:
                for arm in arms_of(kind, exp, cfg):
                    yield kind, exp, ds, arm, estimate(costs, kind, exp, ds, [arm])


def command(i: int, kind: str, exp: int, ds: str, algo: str, est: float, base: str) -> dict:
    return {"id": i, "exp": exp, "dataset": DATASET_KEY[ds], "algo": algo,
            "args": PROTOCOL[(kind, exp)].split(),
            "results_dir": base + ("/mem" if kind == "mem" else ""),
            "est_min": est, "label": f"{kind} exp{exp} {ds} {algo} [est {est:.0f} min]"}


def write_plans() -> None:
    cfg = {e["id"]: e for e in load_config()["experiments"]}
    costs = pd.read_csv(COSTS)
    all_cells = list(cells(cfg, costs))

    groups: dict = {}
    for kind, exp, ds, algo, est in all_cells:
        groups.setdefault((kind, exp, ds), []).append((algo, est))
    order = sorted(groups, key=lambda k: (sum(e for _, e in groups[k]), k))
    full, i = [], 0
    for kind, exp, ds in order:
        for algo, est in groups[(kind, exp, ds)]:
            i += 1
            full.append(command(i, kind, exp, ds, algo, est, FULL_DIR))

    # Validation: per quantity, the cheapest cell whose development-machine rows come from the
    # current code (so its counts can be compared exactly), then the cheapest cell at all.
    def rows_of(kind, exp, ds, algo):
        return costs[(costs.kind == kind) & (costs.exp == exp) & (costs.dataset == ds)
                     & costs.arm.isin(algo.split(",") + ["all"])]

    def is_current(kind, exp, ds, algo):
        r = rows_of(kind, exp, ds, algo)
        return len(r) > 0 and bool(r.current_code.all())

    def clean(kind, exp, ds, algo):
        """No time-out, out-of-memory or error on record: their duration is unknown and long."""
        r = rows_of(kind, exp, ds, algo)
        return len(r) > 0 and int(r.failures.sum()) == 0

    val, i = [], 0
    for kind, exp in QUANTITIES:
        if (kind, exp) == ("mem", 7):
            # The only printed cell is FIFA (78 min on the development machine). The shape is what
            # needs testing, so the paper arm runs it on LEVIATHAN, whose Experiment 7 runs at
            # every batch count finished well inside the limit.
            i += 1
            val.append(command(i, kind, exp, "LEVIATHAN", "HAUSP-UB[noEUCS]", 2.0, PROBE_DIR))
            val[-1]["comparable"] = False
            continue
        cand = [(est, ds, algo) for k, e, ds, algo, est in all_cells
                if (k, e) == (kind, exp) and "HAUSP-UB-L1" != algo]
        cur = [c for c in cand if 0 < c[0] < LIMIT_MIN and clean(kind, exp, c[1], c[2])
               and is_current(kind, exp, c[1], c[2])]
        known = [c for c in cand if 0 < c[0] < LIMIT_MIN and clean(kind, exp, c[1], c[2])]
        est, ds, algo = min(cur) if cur else (min(known) if known else
                                              min(cand, key=lambda c: (c[1] != "SIGN", c[1])))
        if est >= LIMIT_MIN:
            raise SystemExit(f"validation: the cheapest {kind} experiment {exp} cell ({ds} {algo}) "
                             f"is estimated at {est:.0f} min, a time-out; choose the cells by hand")
        i += 1
        val.append(command(i, kind, exp, ds, algo, est, PROBE_DIR))
        val[-1]["comparable"] = is_current(kind, exp, ds, algo)
    # Commands never run in this form before go first (arm filter on Experiments 5, 6 and 10).
    val.sort(key=lambda c: (c["exp"] not in (5, 6, 10), c["est_min"]))
    for n, c in enumerate(val, 1):
        c["id"] = n
    target = max((c for c in val if 1 <= c["est_min"] <= 15), key=lambda c: c["est_min"])
    target["selftest_interrupt_after_s"] = 20

    note = ("Generated by analysis/campaign_plan.py; do not edit. est_min is extrapolated from the "
            "September 2026 runs on the development machine, not measured.")
    for name, cmds, ledger in (("full", full, FULL_DIR), ("validation", val, PROBE_DIR)):
        plan = {"name": name, "note": note, "ledger_dir": ledger, "commands": cmds}
        (PLANS / f"{name}.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
        hours = sum(c["est_min"] for c in cmds) / 60
        print(f"wrote scripts/plans/{name}.json: {len(cmds)} commands, estimate {hours:.1f} h")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--costs", action="store_true", help="freeze the cost table from the current result trees")
    a = ap.parse_args()
    if a.costs:
        write_costs()
    else:
        write_plans()
    return 0


if __name__ == "__main__":
    sys.exit(main())
