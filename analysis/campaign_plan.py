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
    python3 analysis/campaign_plan.py --controls  # write scripts/plans/controls{,-probe}.json
    python3 analysis/campaign_plan.py --rebuild-k100  # write scripts/plans/rebuild-k100{,-probe}.json
    python3 analysis/campaign_plan.py --layer2    # write scripts/plans/layer2{,-probe}.json

The controls plan (campaign 12) is separate because it runs after the full campaign: it uses
campaign 9's protocol, takes its estimates from the measurement machine's own durations in the
full campaign's ledger, and pins the JDK that campaign ran under, so its rows can stand beside
the old ones. Its probe twin runs every command shape on the toy dataset.
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
    """Wall-clock minutes of the rows the cell replaces, plus the full limit for every time-out
    (whose row has no timestamp); an unmeasured cell takes the median of the same arm on the other
    datasets. Out-of-memory rows also carry no timestamp, and their minutes stay uncounted."""
    total = 0.0
    for arm in arms:
        r = costs[(costs.kind == kind) & (costs.exp == exp) & (costs.dataset == ds) & (costs.arm == arm)]
        # A time-out row carries no timestamp, so its wait is added here: the full limit each.
        if len(r) and pd.notna(r.wall_min.iloc[0]) and (r.wall_min.iloc[0] > 0 or r.timeouts.iloc[0] > 0):
            total += float(r.wall_min.iloc[0]) + LIMIT_MIN * int(r.timeouts.iloc[0])
            continue
        peers = costs[(costs.kind == kind) & (costs.exp == exp) & (costs.arm == arm)
                      & ((costs.wall_min > 0) | (costs.timeouts > 0))]
        if len(peers):
            total += float((peers.wall_min + LIMIT_MIN * peers.timeouts).median())
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


CONTROLS_EXP, CONTROLS_LIKE = 12, 9      # campaign 12 runs exactly as campaign 9 does
CONTROLS_NEW = ("HAUSP-UB[rebuild+noEUCS]", "HAUSP-UB[eagerC+noEUCS]")
#: The measured arm of campaign 9 whose duration stands in for each control arm's estimate.
CONTROLS_COST_ARM = {"HAUSP-UB[noL2+noEUCS]": "HAUSP-UB[noL2+noEUCS]", "HAUSP-UB[noEUCS]": "HAUSP-UB[noEUCS]",
                     "HAUSP-UB[rebuild+noEUCS]": "HAUSP-UB[noEUCS]", "HAUSP-UB[eagerC+noEUCS]": "HAUSP-UB[noEUCS]"}


def full_ledger() -> tuple[dict, list[str]]:
    """Wall minutes per (kind, exp, dataset key, arm) on the measurement machine, and the first two
    lines of `java -version` its single session recorded, from results/campaign-full.ledger.jsonl."""
    starts, minutes, javas = {}, {}, []
    for line in (ROOT / "results" / "campaign-full.ledger.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("event") == "session":
            javas.append(e["environment"].get("java_version", "").splitlines()[:2])
        elif e.get("event") == "start":
            starts[e["id"]] = e["argv"]
        elif e.get("event") == "done" and e.get("rc") == 0 and e["id"] in starts:
            a = starts[e["id"]]
            kind = "mem" if "--mem-mode" in a else "time"
            key = (kind, int(a[a.index("--exp") + 1]), a[a.index("--dataset") + 1], a[a.index("--algo") + 1])
            minutes[key] = e["wall_s"] / 60
    if len({tuple(j) for j in javas}) != 1:
        raise SystemExit(f"the full campaign's ledger records {len(javas)} sessions with different JDKs; "
                         "pin one by hand")
    return minutes, javas[0]


def write_controls() -> None:
    cfg = {e["id"]: e for e in load_config()["experiments"]}
    if CONTROLS_EXP not in cfg:
        raise SystemExit(f"experiment {CONTROLS_EXP} is not in the configuration; rebuild the jar first")
    arms = list(cfg[CONTROLS_EXP]["algorithms"])
    minutes, java = full_ledger()
    cmds, probe = [], []
    for kind in ("time", "mem"):
        for ds in datasets_of(kind, CONTROLS_LIKE, cfg):
            for arm in arms:
                est = minutes.get((kind, CONTROLS_LIKE, DATASET_KEY[ds], CONTROLS_COST_ARM[arm]))
                if est is None:
                    raise SystemExit(f"no measured duration for {kind} experiment {CONTROLS_LIKE} {ds} "
                                     f"{CONTROLS_COST_ARM[arm]} in the full ledger")
                cmds.append((kind, ds, arm, round(est, 2)))
    # The two never-run arms go first on the cheapest dataset, timing then memory, so a broken shape
    # fails in minutes; then each (quantity, dataset) group, cheapest first, its arms back to back.
    cheapest = min({ds for _, ds, _, _ in cmds}, key=lambda d: sum(e for k, x, _, e in cmds if x == d))
    first = [c for c in cmds if c[1] == cheapest and c[2] in CONTROLS_NEW]
    first.sort(key=lambda c: (c[0] != "time", CONTROLS_NEW.index(c[2])))
    rest = [c for c in cmds if c not in first]
    groups: dict = {}
    for c in rest:
        groups.setdefault((c[0], c[1]), []).append(c)
    rest = [c for g in sorted(groups, key=lambda g: (sum(x[3] for x in groups[g]), g)) for c in groups[g]]
    plan_cmds = []
    for i, (kind, ds, arm, est) in enumerate(first + rest, 1):
        plan_cmds.append({"id": i, "exp": CONTROLS_EXP, "dataset": DATASET_KEY[ds], "algo": arm,
                          "args": PROTOCOL[(kind, CONTROLS_LIKE)].split(),
                          "results_dir": FULL_DIR + ("/mem" if kind == "mem" else ""),
                          "est_min": est, "label": f"{kind} exp{CONTROLS_EXP} {ds} {arm} [est {est:.0f} min]"})
    for i, arm in enumerate([a for kind in ("time", "mem") for a in arms], 1):
        kind = "time" if i <= len(arms) else "mem"
        probe.append({"id": i, "exp": CONTROLS_EXP, "dataset": "example", "algo": arm,
                      "args": PROTOCOL[(kind, CONTROLS_LIKE)].split(),
                      "results_dir": "results-probe/controls-probe" + ("/mem" if kind == "mem" else ""),
                      "est_min": 0.1, "label": f"{kind} exp{CONTROLS_EXP} example {arm} [probe]"})
    note = ("Generated by analysis/campaign_plan.py --controls; do not edit. est_min is the wall time of the "
            "matching campaign-9 command on the measurement machine (results/campaign-full.ledger.jsonl); the "
            "rebuild and eager arms take the full algorithm's, so their real cost may differ.")
    for name, cmdl, ledger in (("controls", plan_cmds, FULL_DIR), ("controls-probe", probe, "results-probe/controls-probe")):
        plan = {"name": name, "note": note, "ledger_dir": ledger, "java_pin": java, "commands": cmdl}
        (PLANS / f"{name}.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
        print(f"wrote scripts/plans/{name}.json: {len(cmdl)} commands, estimate "
              f"{sum(c['est_min'] for c in cmdl) / 60:.1f} h; JDK pinned to {java[-1]}")


#: Campaigns 13 and 14 run the rebuild control beside the full algorithm at K = 100, on the
#: schedules of campaigns 7 (equal) and 11 (warm start), with those campaigns' protocols narrowed
#: to K = 100, on the datasets where the full algorithm completed all 100 batches.
REBUILD_K100 = ((13, 7, "--k 100 --repeats 3 --repeats-min-seconds 10"), (14, 11, "--k 100 --repeats 3"))
STEP_MS = 15.625


def k100_trials(camp: int) -> dict:
    """Per dataset, the complete K = 100 trials of the full algorithm in campaign 7 or 11, each as
    its 100 batch rows in order. Batch 0 of the warm-start schedule carries ratio 0.2 for every K,
    so it is tied to its trial by file order: the row just before that trial's batch 1."""
    from common import OK, PAPER_UB
    d = load_experiment(camp)
    d = d[(d["Algorithm"] == PAPER_UB) & d["Status"].isin(OK)]
    out: dict = {}
    for (ds, _), x in d.groupby(["Dataset", "RunIndex"], sort=False):
        x = x.reset_index(drop=True)
        if camp == 7:
            y = x[x["DeltaRatio"].round(4) == 0.01].sort_values("BatchID")
            if list(y["BatchID"]) == list(range(100)):
                out.setdefault(ds, []).append(y)
            continue
        for i in x.index[(x["DeltaRatio"].round(4) == 0.008) & (x["BatchID"] == 1)]:
            y = x.iloc[i - 1:i + 99]
            if list(y["BatchID"]) == list(range(100)):
                out.setdefault(ds, []).append(y)
    return out


def write_rebuild_k100() -> None:
    import numpy as np
    minutes, java = full_ledger()
    rows = []
    for camp, like, args in REBUILD_K100:
        for ds, trials in k100_trials(like).items():
            T = float(np.mean([y["tTotal(ms)"].sum() for y in trials]))
            again = np.arange(99, -1, -1, dtype=float)
            extra = float(np.mean([(y["tScan(ms)"].to_numpy(float) + STEP_MS) @ again for y in trials]))
            n = 1 if T > 60 * 60e3 else 3          # the runner's single-trial rule: one trial past 60 min
            rows.append((camp, ds, args, n * T / 60e3, n * (T + extra) / 60e3))
    cmds = []
    rows.sort(key=lambda r: r[3])
    # The never-run arm first, on the cheapest cell; then each dataset with its two arms back to back.
    first = rows[0]
    order = [(first, "HAUSP-UB[rebuild+noEUCS]"), (first, "HAUSP-UB[noEUCS]")]
    order += [(r, a) for r in rows[1:] for a in ("HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]")]
    for i, ((camp, ds, args, est_ub, est_rb), arm) in enumerate(order, 1):
        est = round(est_rb if "rebuild" in arm else est_ub, 2)
        cmds.append({"id": i, "exp": camp, "dataset": DATASET_KEY[ds], "algo": arm, "args": args.split(),
                     "results_dir": FULL_DIR, "est_min": est,
                     "label": f"time exp{camp} {ds} {arm} K=100 [est {est:.0f} min]"})
    probe = []
    for i, (camp, ds, arm) in enumerate([(13, "LEVIATHAN", a) for a in ("HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]")]
                                        + [(14, "C8T1S5I8N5K", a) for a in ("HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]")], 1):
        probe.append({"id": i, "exp": camp, "dataset": DATASET_KEY[ds], "algo": arm, "args": ["--k", "100", "--repeats", "1"],
                      "results_dir": "results-probe/rebuild-k100-probe", "est_min": 2.0,
                      "label": f"time exp{camp} {ds} {arm} K=100 [probe]"})
    note = ("Generated by analysis/campaign_plan.py --rebuild-k100; do not edit. est_min is CPU time of the mining thread "
            "from the full algorithm's K = 100 trials in campaigns 7 and 11 on the measurement machine; the rebuild arm "
            "adds every earlier batch's measured scan time again, with one clock step per reading, so it is an upper "
            "estimate. JVM start and data loading are not included.")
    for name, cmdl, ledger in (("rebuild-k100", cmds, FULL_DIR), ("rebuild-k100-probe", probe, "results-probe/rebuild-k100-probe")):
        plan = {"name": name, "note": note, "ledger_dir": ledger, "java_pin": java, "commands": cmdl}
        (PLANS / f"{name}.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
        print(f"wrote scripts/plans/{name}.json: {len(cmdl)} commands, estimate {sum(c['est_min'] for c in cmdl) / 60:.1f} h")


#: Generation 2 (author decision of 2026-10-16: Layer 2 became the coupled test on every child
#: before recursion). Every HAUSP-UB arm of every experiment the manuscript reports is measured
#: again into its own tree; the baselines keep their generation-1 rows. The block first in the
#: plan measures, in one session, the code of generation 1 (OLD_UB_COMMIT) beside the new code
#: with the arm order alternating, and APEAU-I, against the numbers recorded for them.
GEN2_DIR = "results-gen2"
OLD_UB_COMMIT = "205ec42f985998935420c3c87e8d6421970a381c"
H1_ORDER = ("old", "new", "new", "old", "old", "new")   # three JVM launches per arm, order alternating
GEN2_TIMED = [  # (kind, experiment, arms) in run order after the calibration block and the exactness pair
    ("time", 1, ["HAUSP-UB[noEUCS]"]),
    ("time", 4, ["HAUSP-UB[noEUCS]"]), ("mem", 4, ["HAUSP-UB[noEUCS]"]),
    ("time", 3, ["HAUSP-UB[noEUCS]"]), ("mem", 3, ["HAUSP-UB[noEUCS]"]),
    ("time", 9, None), ("mem", 9, None),          # None: every HAUSP-UB arm of the experiment
    ("time", 12, None), ("mem", 12, None),
    ("time", 2, ["HAUSP-UB[noL2+noEUCS]", "HAUSP-UB[noEUCS]"]),
    ("time", 11, ["HAUSP-UB[noEUCS]"]), ("mem", 11, ["HAUSP-UB[noEUCS]"]),
    ("time", 7, ["HAUSP-UB[noEUCS]"]), ("mem", 7, ["HAUSP-UB[noEUCS]"]),
]
#: Last, as their own blocks: the Layer-1-only arm, which exceeds the time limit at its first batch
#: in every generation-1 run, so it is mostly 90-minute waits. The ablation's first, then the
#: runtime and memory tables' runs, which the text alone reads; the campaign can be stopped there.
GEN2_L1 = [("time", 2), ("time", 1), ("mem", 4)]


def ledger_minutes(name: str) -> dict:
    """Wall minutes per (kind, exp, dataset key, arm) of one finished campaign ledger under results/."""
    starts, minutes = {}, {}
    for line in (ROOT / "results" / f"campaign-{name}.ledger.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("event") == "start" and "argv" in e and "--exp" in e["argv"]:
            starts[e["id"]] = e["argv"]
        elif e.get("event") == "done" and e.get("rc") == 0 and e["id"] in starts:
            a = starts[e["id"]]
            kind = "mem" if "--mem-mode" in a else "time"
            minutes[(kind, int(a[a.index("--exp") + 1]), a[a.index("--dataset") + 1], a[a.index("--algo") + 1])] = e["wall_s"] / 60
    return minutes


def write_layer2() -> None:
    cfg = {e["id"]: e for e in load_config()["experiments"]}
    full, java = full_ledger()
    ctl = ledger_minutes("controls")
    # Estimate per command: the same command's wall time in generation 1 on the measurement machine
    # (campaign 12's for its arms, the full algorithm's for an arm generation 1 never ran there).
    # The new full algorithm ran 8-22 % faster than the old one in campaign 12, so these are upper
    # estimates except where a time-out decides the duration.
    def est(kind, exp, ds, arm):
        v = (ctl if exp == 12 else full).get((kind, exp, DATASET_KEY[ds], arm))
        if v is None:
            raise SystemExit(f"no generation-1 duration for {kind} experiment {exp} {ds} {arm}")
        return round(v, 2)

    cmds = []
    def add(**c):
        c["id"] = len(cmds) + 1
        cmds.append(c)

    # 1. calibration block, cheapest dataset first
    ds1 = sorted(datasets_of("time", 1, cfg), key=lambda d: est("time", 12, d, "HAUSP-UB[noEUCS]"))
    for ds in ds1:
        e_ub = est("time", 12, ds, "HAUSP-UB[noEUCS]")
        for which in H1_ORDER:
            c = dict(exp=1, dataset=DATASET_KEY[ds], algo="HAUSP-UB[noEUCS]", args=["--repeats", "3"],
                     results_dir=f"{GEN2_DIR}/calib/{which}", est_min=e_ub,
                     label=f"calib exp1 {ds} HAUSP-UB {which} code [est {e_ub:.0f} min]")
            if which == "old":
                c["jar_commit"] = OLD_UB_COMMIT
            add(**c)
        e_ai = round(full[("time", 1, DATASET_KEY[ds], "EHAUSM-I")], 2)
        add(exp=1, dataset=DATASET_KEY[ds], algo="EHAUSM-I", args=PROTOCOL[("time", 1)].split(),
            results_dir=f"{GEN2_DIR}/calib/new", est_min=e_ai, label=f"calib exp1 {ds} APEAU-I [est {e_ai:.0f} min]")
    # 2. exactness, then the pattern-set comparison on this machine's dumps
    for exp in (5, 6):
        for ds in sorted(datasets_of("count", exp, cfg), key=lambda d: full[("time", exp, DATASET_KEY[d], "EHAUSM-R,HAUSP-UB[noEUCS]")]):
            e = round(full[("time", exp, DATASET_KEY[ds], "EHAUSM-R,HAUSP-UB[noEUCS]")], 2)
            add(exp=exp, dataset=DATASET_KEY[ds], algo="EHAUSM-R,HAUSP-UB[noEUCS]", args=PROTOCOL[("count", exp)].split(),
                results_dir=GEN2_DIR, est_min=e, label=f"count exp{exp} {ds} APEAU-R + HAUSP-UB [est {e:.0f} min]")
    add(check=["analysis/verify_pattern_sets.py", "--record-to", f"{GEN2_DIR}/pattern_set_equality.json"],
        results_dir=GEN2_DIR, est_min=1.0, label="check: pattern sets of Experiments 5 and 6, recorded")
    # 3. every HAUSP-UB arm of the timed and live-heap experiments
    def timed(kind, exp, arms):
        like = 9 if exp == 12 else exp
        # The baselines keep their generation-1 rows; only HAUSP-UB arms are measured again.
        arms = arms or [a for a in cfg[exp]["algorithms"] if a.startswith("HAUSP-UB")]
        rows = []
        for ds in datasets_of(kind, like, cfg):
            for arm in arms:
                key = DATASET_KEY[ds]
                v = (ctl.get((kind, 12, key, arm)) if exp == 12 else full.get((kind, exp, key, arm)))
                if v is None and exp == 12:
                    v = ctl.get((kind, 12, key, "HAUSP-UB[noEUCS]"))
                if v is None:
                    raise SystemExit(f"no generation-1 duration for {kind} experiment {exp} {ds} {arm}")
                rows.append((ds, arm, round(v, 2)))
        groups = {}
        for r in rows:
            groups.setdefault(r[0], []).append(r)
        for ds in sorted(groups, key=lambda d: sum(r[2] for r in groups[d])):
            for _, arm, e in groups[ds]:
                add(exp=exp, dataset=DATASET_KEY[ds], algo=arm, args=PROTOCOL[(kind, like)].split(),
                    results_dir=GEN2_DIR + ("/mem" if kind == "mem" else ""), est_min=e,
                    label=f"{kind} exp{exp} {ds} {arm} [est {e:.0f} min]")
    for kind, exp, arms in GEN2_TIMED:
        timed(kind, exp, arms)
    # 4. the rebuild control at K = 100 beside the new full algorithm, as campaigns 13 and 14 were planned
    import numpy as np
    rows = []
    for camp, like, args in REBUILD_K100:
        for ds, trials in k100_trials(like).items():
            T = float(np.mean([y["tTotal(ms)"].sum() for y in trials]))
            again = np.arange(99, -1, -1, dtype=float)
            extra = float(np.mean([(y["tScan(ms)"].to_numpy(float) + STEP_MS) @ again for y in trials]))
            n = 1 if T > 60 * 60e3 else 3
            rows.append((camp, ds, args, round(n * T / 60e3, 2), round(n * (T + extra) / 60e3, 2)))
    for camp, ds, args, e_ub, e_rb in sorted(rows, key=lambda r: r[3]):
        for arm, e in (("HAUSP-UB[noEUCS]", e_ub), ("HAUSP-UB[rebuild+noEUCS]", e_rb)):
            add(exp=camp, dataset=DATASET_KEY[ds], algo=arm, args=args.split(), results_dir=GEN2_DIR,
                est_min=e, label=f"time exp{camp} {ds} {arm} K=100 [est {e:.0f} min]")
    # 5. the Layer-1-only arm, last
    for kind, exp in GEN2_L1:
        for ds in sorted(datasets_of(kind, exp, cfg), key=lambda d: full.get((kind, exp, DATASET_KEY[d], "HAUSP-UB-L1"), 0)):
            e = round(full[(kind, exp, DATASET_KEY[ds], "HAUSP-UB-L1")], 2)
            add(exp=exp, dataset=DATASET_KEY[ds], algo="HAUSP-UB-L1", args=PROTOCOL[(kind, exp)].split(),
                results_dir=GEN2_DIR + ("/mem" if kind == "mem" else ""), est_min=e,
                label=f"{kind} exp{exp} {ds} HAUSP-UB-L1 [est {e:.0f} min]" + ("" if exp == 2 else " (optional block)"))

    # probe twin: one command of every shape, on small inputs, into results-probe
    pdir = "results-probe/layer2-probe"
    probe = []
    def padd(**c):
        c["id"] = len(probe) + 1
        c.setdefault("est_min", 0.5)
        probe.append(c)
    padd(exp=1, dataset="example", algo="HAUSP-UB[noEUCS]", args=["--repeats", "1"], results_dir=f"{pdir}/calib/old",
         jar_commit=OLD_UB_COMMIT, label="probe: generation-1 code on the toy dataset")
    padd(exp=1, dataset="example", algo="HAUSP-UB[noEUCS]", args=["--repeats", "1"], results_dir=f"{pdir}/calib/new",
         label="probe: new code on the toy dataset")
    for exp in (5, 6):
        padd(exp=exp, dataset="leviathan", algo="EHAUSM-R,HAUSP-UB[noEUCS]", args=["--repeats", "1"], results_dir=pdir,
             label=f"probe: exactness exp{exp} LEVIATHAN")
    padd(check=["analysis/verify_pattern_sets.py", "--record-to", f"{pdir}/pattern_set_equality.json"], results_dir=pdir,
         label="probe: pattern-set check")
    small = {"time": ["--repeats", "1"], "mem": ["--repeats", "1", "--mem-mode", "live"]}
    for kind, exp, arms in GEN2_TIMED:
        like = 9 if exp == 12 else exp
        ds = "syn_c8t1s5i8n5k" if exp in (7, 11) else "leviathan"
        if (kind, exp) == ("mem", 7):
            ds = "syn_c8t1s5i8n5k"
        for arm in (arms or [a for a in cfg[exp]["algorithms"] if a.startswith("HAUSP-UB")]):
            args = small[kind] + (["--k", "10"] if exp in (7, 11) else [])
            padd(exp=exp, dataset=ds, algo=arm, args=args, results_dir=pdir + ("/mem" if kind == "mem" else ""),
                 label=f"probe: {kind} exp{exp} {ds} {arm}")
    for camp in (13, 14):
        for arm in ("HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]"):
            padd(exp=camp, dataset="syn_c8t1s5i8n5k", algo=arm, args=["--k", "10", "--repeats", "1"], results_dir=pdir,
                 label=f"probe: time exp{camp} SYN {arm} K=10")

    note = ("Generated by analysis/campaign_plan.py --layer2; do not edit. Generation 2: every HAUSP-UB arm measured "
            "again into results-gen2/ after Layer 2 became the coupled test on every child before recursion. The first "
            "block measures the generation-1 code (jar_commit) beside the new code with alternating order, and APEAU-I, "
            "into results-gen2/calib/; it is read against the recorded numbers and reports, it does not stop the plan "
            "(author decision, 2026-10-16). est_min is the same command's wall time in generation 1 on the measurement "
            "machine, an upper estimate except where a time-out decides the duration.")
    for name, cmdl, ledger in (("layer2", cmds, GEN2_DIR), ("layer2-probe", probe, pdir)):
        plan = {"name": name, "note": note, "ledger_dir": ledger, "java_pin": java, "commands": cmdl}
        (PLANS / f"{name}.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
        print(f"wrote scripts/plans/{name}.json: {len(cmdl)} commands, estimate {sum(c['est_min'] for c in cmdl) / 60:.1f} h")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--costs", action="store_true", help="freeze the cost table from the current result trees")
    ap.add_argument("--controls", action="store_true", help="write the campaign-12 plan and its probe twin")
    ap.add_argument("--rebuild-k100", action="store_true", help="write the campaign-13/14 plan and its probe twin")
    ap.add_argument("--layer2", action="store_true", help="write the generation-2 plan and its probe twin")
    a = ap.parse_args()
    if not (a.layer2 or a.costs):
        # Those plans write into results/, the generation-1 tree; the driver refuses them now.
        raise SystemExit("superseded on 2026-10-16: results/ is generation 1 and takes no new rows; "
                         "use --layer2 for the generation-2 plan")
    if a.costs:
        write_costs()
    elif a.controls:
        write_controls()
    elif a.rebuild_k100:
        write_rebuild_k100()
    elif a.layer2:
        write_layer2()
    else:
        write_plans()
    return 0


if __name__ == "__main__":
    sys.exit(main())
