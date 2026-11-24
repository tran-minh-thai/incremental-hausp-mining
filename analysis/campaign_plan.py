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
#: Last: the Layer-1-only arm of the ablation, which exceeds the time limit at its first batch in
#: every generation-1 run, so it is mostly 90-minute waits. Its runs in the runtime and memory
#: experiments were dropped on 2026-10-16: the manuscript no longer prints that column or the
#: sentence that read them.
GEN2_L1 = [("time", 2)]


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
                label=f"{kind} exp{exp} {ds} HAUSP-UB-L1 [est {e:.0f} min]")

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


#: Experiment 15, maintenance cost without search (design approved by the author on 2026-11-19).
#: The three EUCS-off arms run on every set; the two EUCS-on arms only on the sets where the memory
#: probes on the development machine show every batch finishing at the campaign heap (author's option 2).
#: Valued Shoppers: the largest of 1/2, 1/4, 1/8, 1/16 at which the three EUCS-off arms finish (1/2).
MAINT_DIR = "results-gen2/maintenance"
MAINT_MAIN = ["HAUSP-UB[prefix+noEUCS]", "HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]"]
MAINT_EUCS = ["HAUSP-UB[prefix]", "HAUSP-UB[rebuild]"]
MAINT_SETS = ["online_retail_ii", "tafeng", "dunnhumby", "rees46_multi", "hm", "valued_shoppers_s2"]   # small to large
MAINT_REPS = 5
MAINT_EST_MIN = {"online_retail_ii": 0.2, "tafeng": 0.2, "dunnhumby": 0.4, "rees46_multi": 0.4, "hm": 3.0,
                 "valued_shoppers_s2": 12.0}   # rough development-machine guesses per JVM, not measurements


def maintenance_probe_fits() -> dict:
    """{(set-schedule, arm): finished every batch} from the experiment-15 probes (memory and check trees)."""
    import csv
    sizes = {}
    for d in (ROOT / "datasets" / "maint").glob("*-*"):
        if (d / "sizes.txt").exists():
            sizes[d.name.lower()] = len((d / "sizes.txt").read_text().strip().split(","))
    rows = {}
    for f in list((ROOT / "results-probe" / "maint-memory").rglob("experiment15_maintenance.csv")) + \
            list((ROOT / "results-probe" / "maint-check").glob("*/clean/exp15/experiment15_maintenance.csv")):
        with open(f) as fh:
            for r in csv.DictReader(l for l in fh if not l.startswith("#")):
                k = (f"{r['dataset']}-{r['schedule']}".lower(), r["arm"])
                rows[k] = rows.get(k, set()) | {int(r["batch"])}
    return {k: len(v) == sizes.get(k[0], -1) for k, v in rows.items()}


#: Experiment 15 measured again after the single-item lists of the prefix arm stopped being rebuilt on read
#: (author's option A, 2026-11-20). Same cells, arms, repetitions and rotation; its own ledger, so the first
#: campaign stays as it was measured. est_min is the median wall time of the same (set, arm) in the first campaign.
MAINT_V2_DIR = "results-gen2/maintenance-v2"


def maintenance_durations(ledger_dir: str = MAINT_DIR, name: str = "maintenance") -> dict:
    """{(set-schedule, arm): median minutes of its commands} from an experiment-15 ledger (default: the first one)."""
    import statistics
    led = ROOT / ledger_dir / f"campaign-{name}.ledger.jsonl"
    start, out = {}, {}
    for line in led.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("event") == "start":
            start[e["id"]] = e
        elif e.get("event") == "done" and e["id"] in start:
            s0 = start[e["id"]]
            argv = s0["argv"]
            key = (argv[argv.index("--dataset") + 1], argv[argv.index("--algo") + 1])
            t = e["wall_s"] / 60
            out.setdefault(key, []).append(t)
    return {k: statistics.median(v) for k, v in out.items()}


#: Tie rule of the experiment-15 design (approved 2026-11-19): a cell whose prefix/rebuild ratio lies within three
#: standard deviations of 0.5 gets five more repetitions (6..10) of the two arms of that ratio, in one extra batch.
MAINT_V2_TIES_DIR = "results-gen2/maintenance-v2-ties"


def write_maintenance_ties() -> None:
    a = json.loads((ROOT / MAINT_V2_DIR / "exp15" / "analysis.json").read_text(encoding="utf-8"))
    if a["problems"]:
        raise SystemExit(f"refusing: the analysis of {MAINT_V2_DIR} reports problems")
    java = json.loads((PLANS / "maintenance-v2.json").read_text(encoding="utf-8"))["java_pin"]
    dur = maintenance_durations(MAINT_V2_DIR, "maintenance-v2")
    arms = ["HAUSP-UB[prefix+noEUCS]", "HAUSP-UB[rebuild+noEUCS]"]
    cmds = []
    for rep in range(MAINT_REPS + 1, 2 * MAINT_REPS + 1):
        for cell in a["tie_cells"]:
            key = cell.replace("/", "-").lower()
            r = (rep - 1) % len(arms)
            for arm in arms[r:] + arms[:r]:
                cmds.append({"id": len(cmds) + 1, "exp": 15, "dataset": key, "algo": arm, "args": ["--rep", str(rep)],
                             "results_dir": MAINT_V2_TIES_DIR, "est_min": round(dur[(key, arm)], 2),
                             "label": f"time exp15 {key} {arm} rep {rep} (tie rule)"})
    note = ("Generated by analysis/campaign_plan.py --maintenance-v2-ties; do not edit. Tie rule of experiment 15: the cells "
            "of results-gen2/maintenance-v2 whose prefix/rebuild ratio lies within three standard deviations of 0.5 "
            + json.dumps(a["tie_cells"]) + " get repetitions 6..10 of the two arms of that ratio. est_min is the median wall "
            "time of the same command in the maintenance-v2 campaign.")
    plan = {"name": "maintenance-v2-ties", "note": note, "ledger_dir": MAINT_V2_TIES_DIR, "java_pin": java, "commands": cmds}
    (PLANS / "maintenance-v2-ties.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
    print(f"wrote scripts/plans/maintenance-v2-ties.json: {len(cmds)} commands, estimate {sum(c['est_min'] for c in cmds) / 60:.1f} h")


def write_maintenance(version: int = 1) -> None:
    fits = maintenance_probe_fits()
    dur = maintenance_durations() if version == 2 else {}
    target = MAINT_DIR if version == 1 else MAINT_V2_DIR
    java = json.loads((PLANS / "layer2.json").read_text(encoding="utf-8"))["java_pin"]
    cells = []
    for ds in MAINT_SETS:
        for sched in ("l5", "w8"):
            key = f"{ds}-{sched}"
            for arm in MAINT_MAIN:
                if not fits.get((key, arm)):
                    raise SystemExit(f"{key} {arm}: no memory probe shows every batch finishing; probe it first")
            eucs = [a for a in MAINT_EUCS if fits.get((key, a))]
            cells.append((key, ds, MAINT_MAIN + eucs, [a for a in MAINT_EUCS if a not in eucs]))
    cmds = []
    for rep in range(1, MAINT_REPS + 1):
        for key, ds, arms, _ in cells:
            # rotate the arm order with the repetition so that no arm always runs first or last
            r = (rep - 1) % len(arms)
            for arm in arms[r:] + arms[:r]:
                cmds.append({"id": len(cmds) + 1, "exp": 15, "dataset": key, "algo": arm, "args": ["--rep", str(rep)],
                             "results_dir": target,
                             "est_min": round(dur[(key, arm)], 2) if version == 2 else MAINT_EST_MIN[ds],
                             "label": f"time exp15 {key} {arm} rep {rep}"})
    probe = []
    for key in ("online_retail_ii-w8", "tafeng-l5"):
        for arm in MAINT_MAIN + MAINT_EUCS:
            probe.append({"id": len(probe) + 1, "exp": 15, "dataset": key, "algo": arm, "args": ["--rep", "1"],
                          "results_dir": "results-probe/maintenance-probe", "est_min": 0.2,
                          "label": f"probe exp15 {key} {arm}"})
    excluded = {key: miss for key, _, _, miss in cells if miss}
    note = ("Generated by analysis/campaign_plan.py --maintenance; do not edit. Experiment 15: maintenance cost without "
            "search, five independent JVMs per cell, arm order rotated by repetition. EUCS-on arms are left out where "
            "the memory probes show the heap running out: " + json.dumps(excluded) + ". est_min are rough guesses.")
    if version == 2:
        note = ("Generated by analysis/campaign_plan.py --maintenance-v2; do not edit. Experiment 15 measured again after the "
                "single-item lists of the prefix arm stopped being rebuilt on read (author's option A, 2026-11-20); same "
                "cells, arms, repetitions and rotation as the plan 'maintenance'. EUCS-on arms left out: "
                + json.dumps(excluded) + ". est_min is the median wall time of the same command in the first campaign.")
        for c in probe:
            c["results_dir"] = "results-probe/maintenance-v2-probe"
    plans = ((("maintenance", cmds, MAINT_DIR), ("maintenance-probe", probe, "results-probe/maintenance-probe"))
             if version == 1 else
             (("maintenance-v2", cmds, MAINT_V2_DIR), ("maintenance-v2-probe", probe, "results-probe/maintenance-v2-probe")))
    for name, cmdl, ledger in plans:
        plan = {"name": name, "note": note, "ledger_dir": ledger, "java_pin": java, "commands": cmdl}
        (PLANS / f"{name}.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
        print(f"wrote scripts/plans/{name}.json: {len(cmdl)} commands, estimate {sum(c['est_min'] for c in cmdl) / 60:.1f} h")


#: Experiment 16, end-to-end cost per batch under the mixed append model (design approved by the author on
#: 2026-11-22, paper/giao_viec end-to-end design). Cells: four datasets x L5, W8 x the three registered thresholds;
#: schedule ALL (--whole) is the search-organization study on the final database, one cell per dataset and threshold.
E2E_DIR = "results-gen2/end-to-end"
E2E_SETS = ["tafeng", "rees46_cosmetics", "hm", "rees46_multi"]   # small to large
E2E_FIVE = ["HAUSP-UB[prefix+noEUCS]", "HAUSP-UB[rebuild+noEUCS]"]          # E2: five repetitions
E2E_THREE = ["HAUSP-UB[noEUCS]", "Remine-PEAU"]                               # E3, E4: three, then the tie rule
E2E_EUCS = ["HAUSP-UB[prefix]", "HAUSP-UB[rebuild]"]                           # secondary, three, where the heap allows
E2E_STUDY = ["HAUSP-UB[noEUCS]", "HAUSP-UB[noL2+noEUCS]", "HAUSP-UB[noL2+L3@node+noEUCS]", "HAUSP-UB[nopool+noEUCS]",
             "Remine-PEAU"]
#: The costly level (author decision Q3, 2026-11-22): three repetitions of the proposed arm, re-mining with the same
#: search and the independent re-miner; no rewrite arm and no EUCS arm. The same three repetitions for the study.
E2E_COSTLY = {("rees46_multi", 7.8125e-05)}
E2E_LIMIT_S = 1800
#: Levels in their own plan: static mining on the development machine over five minutes, on the whole data at the
#: registration or on batch 0 (results-probe/first-batch-static): REES46 multi 1637 s whole, cosmetics 521 s on L5 batch 0.
E2E_DEAR = {("rees46_multi", 7.8125e-05), ("rees46_cosmetics", 0.00011048543456039807)}
#: Per-batch limit at the dear levels (author decision, 2026-11-24): 3600 s for the arms of the main comparisons, so
#: that their late batches (about 860-1490 s on PC-THAI, extrapolated from the development machine) do not hit the
#: limit; 1800 s for the independent re-miner, whose time-out still bounds the ratio. Elsewhere 1800 s for every arm.
E2E_DEAR_LIMIT_S = 3600


def e2e_thresholds() -> dict:
    sys.path.insert(0, str(ROOT / "analysis"))
    import analyze_end_to_end as ae
    return {k.lower(): sorted(v, reverse=True) for k, v in ae.registered_xi().items()}


def e2e_static_seconds() -> dict:
    """{(set, xi): seconds of one static mining at the registration (development machine, a probe, not a measurement)}."""
    sys.path.insert(0, str(ROOT / "analysis"))
    import analyze_end_to_end as ae
    out = {}
    for ds, run in ae.REGISTRATIONS.items():
        runs = json.loads((ROOT / "results-probe" / "select-xi" / run / "runs.json").read_text())
        runs = runs if isinstance(runs, list) else list(runs.values())[0]
        for r in runs:
            if r.get("status") == "OK":
                out[(ds.lower(), r["xi"])] = r["seconds"]
    return out


def e2e_eucs_fits(ds: str) -> bool:
    fits = maintenance_probe_fits()
    keys = [(f"{ds}-{s}", a) for s in ("l5", "w8") for a in E2E_EUCS]
    if all(k in fits for k in keys):
        return all(fits[k] for k in keys)
    probe = ROOT / "results-probe" / "end-to-end-probe" / "exp16" / "experiment16_end_to_end.csv"
    if not probe.exists():
        raise SystemExit(f"{ds}: no memory probe for the EUCS arms; run the end-to-end probe plan first")
    import csv
    seen = {}
    with open(probe) as fh:
        for r in csv.DictReader(l for l in fh if not l.startswith("#")):
            if r["dataset"].lower() == ds and r["arm"] in E2E_EUCS:
                seen.setdefault((r["schedule"].lower(), r["arm"]), set()).add((int(r["batch"]), r["status"]))
    sizes = {s: len((ROOT / "datasets" / "maint" / f"{ds.upper()}-{s.upper()}" / "sizes.txt").read_text().split(","))
             for s in ("l5", "w8")}
    return all(len({b for b, st in seen.get((s, a), set()) if st == "OK"}) == sizes[s] for s in ("l5", "w8") for a in E2E_EUCS)


def write_end_to_end() -> None:
    xis, static = e2e_thresholds(), e2e_static_seconds()
    java = json.loads((PLANS / "layer2.json").read_text(encoding="utf-8"))["java_pin"]
    nbatch = {(ds, s): len((ROOT / "datasets" / "maint" / f"{ds.upper()}-{s.upper()}" / "sizes.txt").read_text().split(","))
              for ds in E2E_SETS for s in ("l5", "w8")}
    cmds = []

    def add(ds, sched, xi, arm, rep, whole):
        n = 1 if whole else nbatch[(ds, sched)]
        limit = E2E_DEAR_LIMIT_S if (ds, xi) in E2E_DEAR and arm != "Remine-PEAU" else E2E_LIMIT_S
        per = static.get((ds, xi), 60.0) * (3.0 if arm == "Remine-PEAU" else 1.2)
        # a batch over the limit writes OT and ends the command
        est = (min(per, limit) + (n - 1) * per if per <= limit else limit) / 60 + 0.3
        cmds.append({"id": len(cmds) + 1, "exp": 16, "dataset": f"{ds}-{sched}", "algo": arm,
                     "args": ["--xi", repr(xi), "--rep", str(rep), "--limit-s", str(limit)] + (["--whole"] if whole else []),
                     "results_dir": E2E_DIR, "est_min": round(est, 2),
                     "label": f"time exp16 {ds}-{'all' if whole else sched} xi={xi} {arm} rep {rep}"})

    try:
        eucs_of = {ds: e2e_eucs_fits(ds) for ds in E2E_SETS}
    except SystemExit as e:
        print(f"{e}; writing only the probe plan")
        eucs_of = None
    for rep in range(1, 6):
        for ds in E2E_SETS:
            if eucs_of is None:
                break
            eucs = eucs_of[ds]
            for xi in xis[ds]:
                costly = (ds, xi) in E2E_COSTLY
                if costly and rep > 3:
                    continue
                for sched in ("l5", "w8"):
                    arms = list(E2E_FIVE)
                    if rep <= 3:
                        arms += ["Remine-PEAU"] if costly else E2E_THREE + (E2E_EUCS if eucs else [])
                    r = (rep - 1) % len(arms)
                    for arm in arms[r:] + arms[:r]:
                        add(ds, sched, xi, arm, rep, False)
                study = E2E_STUDY[(rep - 1) % len(E2E_STUDY):] + E2E_STUDY[:(rep - 1) % len(E2E_STUDY)]
                for arm in study:
                    add(ds, "l5", xi, arm, rep, True)
    main_cmds = cmds
    cmds = []
    xi0 = xis["tafeng"][0]
    for arm in E2E_FIVE + E2E_THREE + E2E_EUCS:
        add("tafeng", "l5", xi0, arm, 1, False)
    for arm in E2E_STUDY:
        add("tafeng", "l5", xi0, arm, 1, True)
    probe = [dict(c, results_dir="results-probe/end-to-end-probe", label=c["label"].replace("time", "probe")) for c in cmds]
    cmds = main_cmds
    for sched in ("l5", "w8"):
        for arm in E2E_EUCS:
            probe.append({"id": len(probe) + 1, "exp": 16, "dataset": f"rees46_cosmetics-{sched}", "algo": arm,
                          "args": ["--xi", repr(xis["rees46_cosmetics"][0]), "--rep", "1", "--limit-s", str(E2E_LIMIT_S)],
                          "results_dir": "results-probe/end-to-end-probe", "est_min": 1.0,
                          "label": f"probe exp16 rees46_cosmetics-{sched} EUCS memory {arm}"})
    note = ("Generated by analysis/campaign_plan.py --end-to-end; do not edit. Experiment 16 (design approved 2026-11-22): "
            "cells = 4 datasets x L5, W8 x 3 registered thresholds, plus the search-organization study on the final "
            "database (--whole). Five repetitions of the proposed arm and of re-mining with the same search; three of the "
            "rewrite arm, the independent re-miner and the EUCS-on arms (where the heap allows), then the tie rule. "
            "REES46 multi at xi=7.8125e-05: three repetitions, no rewrite and no EUCS arm (decision Q3). Arm order "
            "rotated by repetition. Per-batch limit " + str(E2E_LIMIT_S) + " s. est_min: static mining time of the "
            "threshold selection on the development machine times the batches, a rough guess, not a measurement.")
    out = [("end-to-end-probe", probe, "results-probe/end-to-end-probe")]
    if eucs_of is not None:
        # The two levels whose static mining takes minutes go in their own plan (same ledger directory, same CSV), so
        # the rest of the campaign does not wait behind them and the author can decide them apart.
        dear = [c for c in cmds if (c["dataset"].split("-")[0], float(c["args"][1])) in E2E_DEAR]
        cheap = [c for c in cmds if c not in dear]
        for lst in (cheap, dear):
            for i, c in enumerate(lst, 1):
                c["id"] = i
        out[:0] = [("end-to-end", cheap, E2E_DIR), ("end-to-end-dear", dear, E2E_DIR)]
    for name, cmdl, ledger in out:
        plan = {"name": name, "note": note, "ledger_dir": ledger, "java_pin": java, "commands": cmdl}
        (PLANS / f"{name}.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
        print(f"wrote scripts/plans/{name}.json: {len(cmdl)} commands, estimate {sum(c['est_min'] for c in cmdl) / 60:.1f} h")


#: Tie rule of the experiment-16 design (approved 2026-11-22): a cell tied in a comparison (A, B) gets five more
#: repetitions of both arms, in one extra batch; both arms are brought to (paired repetitions + 5), numbered on from each
#: arm's last repetition so that the analyzer's gap check holds. A cell and arm tied in several comparisons takes the
#: largest target. Its own ledger directory; the analyzer reads both CSV files.
E2E_TIES_DIR = "results-gen2/end-to-end-ties"


def write_end_to_end_ties() -> None:
    import collections
    import csv
    import statistics
    sys.path.insert(0, str(ROOT / "analysis"))
    import analyze_end_to_end as ae
    a = json.loads((ROOT / E2E_DIR / "exp16" / "analysis.json").read_text(encoding="utf-8"))
    if a["problems"]:
        raise SystemExit(f"refusing: the analysis of {E2E_DIR} reports problems")
    java = json.loads((PLANS / "end-to-end.json").read_text(encoding="utf-8"))["java_pin"]
    durs = collections.defaultdict(list)      # (dataset key, arm, whole) -> wall minutes in the main campaign
    start = {}
    for line in (ROOT / E2E_DIR / "campaign-end-to-end.ledger.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("event") == "start":
            start[e["id"]] = e
        elif e.get("event") == "done" and e["id"] in start:
            v = start[e["id"]]["argv"]
            durs[(v[v.index("--dataset") + 1], v[v.index("--algo") + 1], "--whole" in v)].append(e["wall_s"] / 60)
    dur = {k: statistics.median(v) for k, v in durs.items()}
    reps = collections.defaultdict(set)
    with open(ROOT / E2E_DIR / "exp16" / "experiment16_end_to_end.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(l for l in fh if not l.startswith("#")):
            reps[(r["dataset"], r["schedule"], float(r["xi"]), r["arm"])].add(int(r["rep"]))
    arms_of = {name: (num, den) for name, num, den in ae.COMPARISONS}
    for arm in ae.SEARCH_STUDY:
        arms_of[f"search_full_vs_{arm}"] = (ae.REWRITE, arm)
    target = {}
    for name, cells in a["tie_cells"].items():
        x, y = arms_of[name]
        for cell in cells:
            ds, sched, xi = cell.split("/")
            k = (ds, sched, float(xi))
            paired = len(reps[k + (x,)] & reps[k + (y,)])
            for arm in (x, y):
                target[k + (arm,)] = max(target.get(k + (arm,), 0), paired + 5)
    cmds = []
    rounds = max(t - max(reps[k]) for k, t in target.items())
    cells = sorted({k[:3] for k in target}, key=lambda c: (c[0], c[1], -c[2]))
    for step in range(1, rounds + 1):
        for c in cells:
            arms = sorted(k[3] for k in target if k[:3] == c)
            r = (step - 1) % len(arms)
            for arm in arms[r:] + arms[:r]:          # arm order rotated by repetition, as in the main plan
                k = c + (arm,)
                rep = max(reps[k]) + step
                if rep > target[k]:
                    continue
                ds, sched, xi = c
                whole = sched == "ALL"
                key = f"{ds.lower()}-l5" if whole else f"{ds.lower()}-{sched.lower()}"
                cmds.append({"id": len(cmds) + 1, "exp": 16, "dataset": key, "algo": arm,
                             "args": ["--xi", repr(xi), "--rep", str(rep), "--limit-s", str(E2E_LIMIT_S)]
                             + (["--whole"] if whole else []),
                             "results_dir": E2E_TIES_DIR, "est_min": round(dur[(key, arm, whole)], 2),
                             "label": f"time exp16 {ds.lower()}-{sched.lower()} xi={xi} {arm} rep {rep} (tie rule)"})
    note = ("Generated by analysis/campaign_plan.py --end-to-end-ties; do not edit. Tie rule of experiment 16: every cell tied "
            "in a comparison of " + E2E_DIR + " gets five more repetitions of both arms of that comparison, numbered on from "
            "each arm's last repetition. Tied cells: " + json.dumps(a["tie_cells"]) + ". est_min is the median wall time of "
            "the same (dataset, arm) in the end-to-end campaign.")
    plan = {"name": "end-to-end-ties", "note": note, "ledger_dir": E2E_TIES_DIR, "java_pin": java, "commands": cmds}
    (PLANS / "end-to-end-ties.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
    print(f"wrote scripts/plans/end-to-end-ties.json: {len(cmds)} commands, estimate {sum(c['est_min'] for c in cmds) / 60:.1f} h")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--costs", action="store_true", help="freeze the cost table from the current result trees")
    ap.add_argument("--controls", action="store_true", help="write the campaign-12 plan and its probe twin")
    ap.add_argument("--rebuild-k100", action="store_true", help="write the campaign-13/14 plan and its probe twin")
    ap.add_argument("--layer2", action="store_true", help="write the generation-2 plan and its probe twin")
    ap.add_argument("--maintenance", action="store_true", help="write the experiment-15 plan and its probe twin")
    ap.add_argument("--maintenance-v2", action="store_true", help="write the experiment-15 re-measurement plan and its probe twin")
    ap.add_argument("--maintenance-v2-ties", action="store_true", help="write the tie-rule batch of the re-measurement")
    ap.add_argument("--end-to-end", action="store_true", help="write the experiment-16 plan and its probe twin")
    ap.add_argument("--end-to-end-ties", action="store_true", help="write the tie-rule batch of experiment 16")
    a = ap.parse_args()
    if a.maintenance:
        write_maintenance()
        return 0
    if a.maintenance_v2:
        write_maintenance(2)
        return 0
    if a.maintenance_v2_ties:
        write_maintenance_ties()
        return 0
    if a.end_to_end:
        write_end_to_end()
        return 0
    if a.end_to_end_ties:
        write_end_to_end_ties()
        return 0
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
