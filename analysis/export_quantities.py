#!/usr/bin/env python3
"""Export every measured quantity the manuscript quotes, as data.

This is where the code side of the project stops. It reads the artifacts and writes
`analysis_out/paper/quantities.json`: numbers, series and counts, keyed by names taken
from the experiments and the CSV columns. It contains no wording, no markup, no display
names and no manuscript vocabulary -- turning a number into a sentence is the other
side's work, and the file below is the whole of the handover between them.

Two rules hold the boundary:

* **Aggregation over trials belongs here**, because it is a statement about the
  measurement: a mean over repeats, a max over samples of a memory series.
* **Reduction across datasets does not**, because which end of a range gets said, and in
  which direction, is a property of the sentence. Series come out per dataset, in the CSV
  names, and the reader reduces them.

A quantity that cannot be computed is written as null with its reason next to it, never
omitted: a missing key must be visible to whoever reads the file, not silently absent.

    python3 analysis/export_quantities.py            # write the file
    python3 analysis/export_quantities.py --check    # exit 1 if the file is out of date
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DS_ORDER, OK, PAPER_UB, ROOT, load_experiment, load_memory  # noqa: E402

OUT = ROOT / "analysis_out" / "paper" / "quantities.json"

# The ablation chain of the proposed algorithm, by the arm labels the CSVs carry.
UB_CHAIN = {"layout": "HAUSP-UB[noL2+L3@node+nopool+noEUCS]",
            "child": "HAUSP-UB[noL2+nopool+noEUCS]",
            "pool": "HAUSP-UB[noL2+noEUCS]",
            "L2": PAPER_UB}
UB_L1L2 = "HAUSP-UB[noL3+noEUCS]"

MISSING: dict[str, str] = {}


def _frame(df):
    """A dataset x arm frame as plain nested dictionaries, NaN dropped."""
    if df is None:
        return None
    out = {}
    for ds, row in df.iterrows():
        cells = {arm: float(v) for arm, v in row.items() if pd.notna(v)}
        if cells:
            out[str(ds)] = cells
    return out


def _series(s):
    if s is None:
        return None
    return {str(k): float(v) for k, v in s.dropna().items()}


def totals(exp, value="tTotal(ms)", where=None):
    """Mean over trials of the per-(dataset, arm) total."""
    df = load_experiment(exp)
    ok = df[df["Status"].isin(OK)]
    if where is not None:
        ok = ok[where(ok)]
    per = ok.groupby(["Dataset", "Algorithm", "RunIndex"])[value].sum()
    return per.groupby(["Dataset", "Algorithm"]).mean().unstack("Algorithm")


def counts(exp, col="CandUnified"):
    """Counts of the first trial: a count is deterministic, so repeats add nothing."""
    df = load_experiment(exp)
    ok = df[df["Status"].isin(OK)]
    ok = ok[ok["RunIndex"] == ok.groupby(["Dataset", "Algorithm"])["RunIndex"].transform("min")]
    return ok.groupby(["Dataset", "Algorithm"])[col].sum().unstack("Algorithm")


def live_heap(exp):
    m = load_memory(exp)
    if m is None:
        return None
    return m.groupby(["Dataset", "Algorithm"])["MemLive(MB)"].max().unstack("Algorithm")


def exp1_list_identity():
    """Lists built by the proposed algorithm against the re-mining reference, cell by cell."""
    e = load_experiment(1)
    ok = e[e["Status"].isin(OK)]
    cells = (ok.groupby(["Dataset", "Algorithm", "BatchID"])["CandUnified"].max()
               .unstack("Algorithm").dropna(subset=[PAPER_UB, "EHAUSM-R"]))
    return {"matched": int((cells[PAPER_UB] == cells["EHAUSM-R"]).sum()),
            "total": int(len(cells)),
            "datasets": int(cells.index.get_level_values(0).nunique()),
            "batches": int(cells.index.get_level_values(1).nunique())}


def exp1_phase_share():
    """Share of total runtime spent scanning and in the first pruning layer, per dataset.

    Mean over trials, which is what the phase-breakdown table publishes. It used to be the
    maximum over trials, and the two differ: on the synthetic dataset the scan share is 6.67
    on the mean and 6.97 on the maximum. A sentence pointing at that table while quoting the
    other statistic sends a reader to a cell that disagrees with it -- the table is the
    published artifact, so it decides.
    """
    e = load_experiment(1)
    ok = e[e["Status"].isin(OK) & (e["Algorithm"] == PAPER_UB)]
    per = ok.groupby(["Dataset", "RunIndex"])[["tScan(ms)", "tLayer1(ms)", "tTotal(ms)"]].sum()
    scan = (per["tScan(ms)"] / per["tTotal(ms)"]).groupby("Dataset").mean()
    lay1 = (per["tLayer1(ms)"] / per["tTotal(ms)"]).groupby("Dataset").mean()
    return {str(d): {"scan": float(scan[d]), "layer1": float(lay1[d])} for d in scan.index}


def exp4_peak_vs_retained():
    """Peak live heap against the heap still held at the end of a batch."""
    m = load_memory(4)
    if m is None:
        return None
    d = m[(m["Algorithm"] == PAPER_UB) & m["Status"].isin(OK)]
    if "MemRetained(MB)" not in d.columns:
        return None
    g = d.groupby("Dataset").agg(peak=("MemLive(MB)", "max"), retained=("MemRetained(MB)", "max"))
    return {str(k): {"peak": float(v["peak"]), "retained": float(v["retained"])}
            for k, v in g.iterrows()}


def exp4_pool():
    m = load_memory(4)
    if m is None:
        return None
    d = m[m["Algorithm"] == PAPER_UB]
    g = d.groupby("Dataset")[["PoolBorrows", "PoolReuses", "PoolPeakLive"]].max()
    return {str(k): {c.lower(): float(v[c]) for c in g.columns} for k, v in g.iterrows()}


def exp7_runtime_min():
    """Total minutes per (dataset, arm, batch count), keeping only schedules that completed."""
    df = load_experiment(7)
    ok = df[df["Status"].isin(OK)].copy()
    ok["K"] = (1.0 / ok["DeltaRatio"]).round().astype(int)
    per = (ok.groupby(["Dataset", "Algorithm", "K", "RunIndex"])
             .agg(n=("BatchID", "size"), t=("tTotal(ms)", "sum")).reset_index())
    per = per[per["n"] == per["K"]]
    s = per.groupby(["Dataset", "Algorithm", "K"])["t"].mean().div(60000)
    out: dict = {}
    for (ds, arm, k), v in s.items():
        out.setdefault(str(ds), {}).setdefault(str(arm), {})[str(int(k))] = float(v)
    return out


def exp11_runtime_min():
    """Total minutes per (dataset, arm, batch count) under the warm-start schedule.

    Keyed by batch count, like Experiment 7. It used to be one number per (dataset, arm),
    which was the same thing only while the experiment ran at a single batch count: once it
    swept four, that number silently became the sum of all four. A sum over batch counts is
    not a runtime anyone quotes, and it reads as a plausible measurement.
    """
    df = load_experiment(11)
    ok = df[df["Status"].isin(OK)].copy()
    # A trial writes its batch counts one after another, each restarting at BatchID 0.
    ok["_sweep"] = ok.groupby(["Dataset", "Algorithm", "RunIndex"])["BatchID"].transform(
        lambda x: x.eq(0).cumsum())
    key = ["Dataset", "Algorithm", "RunIndex", "_sweep"]
    per = ok.groupby(key).agg(K=("BatchID", "size"), t=("tTotal(ms)", "sum")).reset_index()
    bad = per[per["K"] != ok.groupby(key)["BatchID"].max().values + 1]
    if len(bad):
        MISSING["exp11.runtime_min"] = ("a trial's rows are not in file order; batch count and "
                                        "highest batch index disagree on %d run(s)" % len(bad))
        return None
    s = per.groupby(["Dataset", "Algorithm", "K"])["t"].mean().div(60000)
    out: dict = {}
    for (ds, arm, k), v in s.items():
        out.setdefault(str(ds), {}).setdefault(str(arm), {})[str(int(k))] = float(v)
    return out


def exp10():
    d = load_experiment(10)
    ok = d[d["Status"].isin(OK) & (d["BatchID"] == 1)]
    t = ok.groupby(["Dataset", "mu"])["tTotal(ms)"].mean().unstack("mu")
    runtime = {str(ds): {str(mu): float(v) for mu, v in row.items() if pd.notna(v)}
               for ds, row in t.iterrows()}
    margin = ok["BufferTested"].div(ok["SafetyBound"].replace(0, np.nan)).min()
    return runtime, (None if pd.isna(margin) else float(margin))


def exactness():
    d5, d6 = load_experiment(5), load_experiment(6)
    ok5 = d5[d5["Status"].isin(OK)]
    return {"configurations_exp5": int(ok5.groupby(["Dataset", "MinUtil"]).ngroups),
            "max_patterns_exp5": int(ok5["HAUSP"].max()),
            "configurations_exp6": int(len(d6[d6["Status"].isin(OK)]))}


def completed_configurations():
    """Configurations where the proposed algorithm and every baseline all reported a count."""
    n = 0
    for e in (1, 3, 4, 7, 11):
        df = load_experiment(e)
        ok = df[df["Status"].isin(OK) & (df["RunIndex"] == 0)]
        piv = ok.pivot_table(index=["Dataset", "MinUtil", "DeltaRatio", "BatchID"],
                             columns="Algorithm", values="HAUSP", aggfunc="first")
        cols = [c for c in piv.columns if c in (PAPER_UB, "EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM")]
        n += len(piv[cols].dropna())
    return int(n)


def variance():
    """The run-to-run variability table, as the numbers behind it rather than its markup."""
    from build_latex_tables import ANALYSIS_OUT
    p = ANALYSIS_OUT / "latex" / "tab_variance.tex"
    if not p.exists():
        MISSING["variance"] = "tab_variance.tex not generated yet; run build_latex_tables.py"
        return None
    text = p.read_text()
    median = [float(x) for x in re.findall(r"& ([0-9.]+) & [0-9.]+ & [0-9.]+ \\\\", text)]
    last = [float(x) for x in re.findall(r"& ([0-9.]+) \\\\", text)]
    return {"median_cv_percent": median, "max_cv_percent_long_runs": last}


def wilcoxon():
    p = ROOT / "analysis_out" / "paper" / "tables" / "wilcoxon_tests.md"
    if not p.exists():
        MISSING["wilcoxon"] = "wilcoxon_tests.md not generated yet; run wilcoxon_tests.py"
        return None
    out: dict = {}
    for ln in p.read_text().split("\n"):
        cells = [c.strip() for c in ln.split("|")]
        if len(cells) < 6 or not cells[1] or cells[1].startswith("-"):
            continue
        try:
            pv, n, lower = float(cells[4]), int(cells[3]), int(cells[5])
        except ValueError:
            continue
        out.setdefault(cells[1], {})[cells[2]] = {"p": pv, "pairs": n, "pairs_lower": lower}
    return out or None


def passthrough(name, keep=None):
    p = ROOT / "analysis_out" / "paper" / f"{name}.json"
    if not p.exists():
        MISSING[name] = f"{p.name} not generated yet"
        return None
    data = json.loads(p.read_text())
    if keep is None:
        return data
    return {k: {c: v[c] for c in keep if c in v} for k, v in data.items() if k in DS_ORDER}


def generator_parameters():
    """The utility generator's own declaration, read from its source."""
    src = (ROOT / "src" / "main" / "java" / "SPMF_Converter.java").read_text()
    mix = re.search(r"mixture (\d+)% -> (\d+)-(\d+), (\d+)% -> (\d+)-(\d+), (\d+)% -> (\d+)-(\d+)", src)
    logn = re.search(r"most values fall in (\d+)-(\d+), a few reach (\d+)-(\d+)", src)
    seed = re.search(r"new Random\((\d+)\)", src)
    if not (mix and logn and seed):
        MISSING["generator"] = "SPMF_Converter.java no longer declares the mixture in the expected form"
        return None
    g = [int(x) for x in mix.groups()]
    return {"internal_mixture": [{"share_percent": g[i], "low": g[i + 1], "high": g[i + 2]}
                                 for i in (0, 3, 6)],
            "external_lognormal_bulk": [int(logn.group(1)), int(logn.group(2))],
            "external_clip": [1, 1000],
            "seed": int(seed.group(1))}


def protocol():
    """Constants of the measurement protocol, read from the sources that enforce them."""
    cfg = (ROOT / "src" / "main" / "java" / "ExperimentConfig.java").read_text()
    e7 = (ROOT / "src" / "main" / "java" / "Experiment7Runner.java").read_text()
    tiers = re.findall(r"trial0TotalMs < ([0-9_]+)L\) return Math\.max\(REPEATS, (\d+)\)", cfg)
    single = re.search(r"SINGLE_TRIAL_RULE_MS = (\d+)L \* ([0-9_]+)L", e7)
    big = re.search(r'g\.mean\(\)\[cv\.index\] >= (\d+)', (ROOT / "analysis" / "build_latex_tables.py").read_text())
    out = {"adaptive_repeat_tiers": [{"under_seconds": int(ms.replace("_", "")) / 1000.0,
                                      "trials": int(n)} for ms, n in tiers]}
    out["exp7_single_trial_rule_minutes"] = (
        int(single.group(1)) * int(single.group(2).replace("_", "")) / 60000.0 if single else None)
    out["variance_long_run_threshold_seconds"] = int(big.group(1)) / 1000.0 if big else None
    for k, v in out.items():
        if v is None:
            MISSING["protocol." + k] = "the constant is no longer declared in the expected form"
    return out


def stamp() -> dict:
    """Where this file came from, so a reader can tell a current export from a stale one."""
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    # The file this export is about to overwrite does not count as an uncommitted change:
    # that edit IS the export. Counting it would make every honest run report a dirty tree,
    # and a warning that always fires is a warning nobody reads.
    rel = OUT.relative_to(ROOT).as_posix()
    dirty = [ln for ln in git("status", "--porcelain", "--untracked-files=no").split("\n")
             if ln.strip() and not ln.endswith(" " + rel)]
    return {"written_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "commit": git("rev-parse", "--short", "HEAD") or "unknown",
            "tree": "clean" if not dirty else "MODIFIED(%d)" % len(dirty)}


def manifest():
    p = ROOT / "datasets" / "MANIFEST.sha256"
    if not p.exists():
        MISSING["manifest"] = "datasets/MANIFEST.sha256 is absent"
        return None
    return {"files": len([l for l in p.read_text().strip().split("\n") if l.strip()])}


def collect() -> dict:
    exp10_runtime, exp10_margin = exp10()
    m3 = load_memory(3)
    q = {
        "datasets_in_table_order": list(DS_ORDER),
        "paper_arm": PAPER_UB,
        "ablation_chain": UB_CHAIN,

        "exp1.runtime_ms": _frame(totals(1)),
        "exp1.lists": _frame(counts(1)),
        "exp1.list_identity": exp1_list_identity(),
        "exp1.phase_share": exp1_phase_share(),

        "exp2.lists": _frame(counts(2)),
        "exp2.recursions": _frame(counts(2, "RecursedUnified")),
        "exp2.arm_layers_1_2": UB_L1L2,

        "exp3.update_runtime_ms": _frame(totals(
            3, where=lambda d: (d["DeltaRatio"].round(3) == 0.2) & (d["BatchID"] == 1))),
        "exp3.lists": _frame(counts(3)),
        "exp3.live_heap_rows_at_delta_20": (
            0 if m3 is None else
            int(len(m3[(m3["DeltaRatio"].round(3) == 0.2) & (m3["BatchID"] == 1)]))),

        "exp4.live_heap_mb": _frame(live_heap(4)),
        "exp4.used_heap_mb": _frame(
            load_experiment(4)[load_experiment(4)["Status"].isin(OK)]
            .groupby(["Dataset", "Algorithm"])["MemPeak(MB)"].max().unstack("Algorithm")),
        "exp4.peak_vs_retained_mb": exp4_peak_vs_retained(),
        "exp4.pool": exp4_pool(),

        "exp7.runtime_min": exp7_runtime_min(),
        "exp7.fifa_memprobe_live_mb": None,

        "exp8.lists": _frame(counts(8)),

        "exp9.runtime_ms": _frame(totals(9)),
        "exp9.lists": _frame(counts(9)),
        "exp9.recursions": _frame(counts(9, "RecursedUnified")),
        "exp9.live_heap_mb": _frame(live_heap(9)),

        "exp10.runtime_ms_by_mu": exp10_runtime,
        "exp10.buffer_margin_min": exp10_margin,

        "exp11.runtime_min": exp11_runtime_min(),
        "exp11.live_heap_mb": _frame(live_heap(11)),

        "exactness": exactness(),
        "completed_configurations": completed_configurations(),
        "variance": variance(),
        "wilcoxon": wilcoxon(),

        "identifier_space": passthrough("identifier_space"),
        "dataset_stats": passthrough("dataset_stats", keep=["avg_items", "avg_itemsets"]),
        "generator": generator_parameters(),
        "protocol": protocol(),
        "manifest": manifest(),
    }
    m7 = load_memory(7)
    if m7 is not None:
        v = m7[(m7["Dataset"] == "FIFA") & (m7["Algorithm"] == PAPER_UB)]["MemLive(MB)"].max()
        q["exp7.fifa_memprobe_live_mb"] = None if pd.isna(v) else float(v)
    if q["exp7.fifa_memprobe_live_mb"] is None:
        MISSING["exp7.fifa_memprobe_live_mb"] = "no live-heap row for that probe"

    for key, value in q.items():
        if value is None and key not in MISSING:
            MISSING[key] = "no artifact supplies it"
    q["_missing"] = MISSING
    q["_stamp"] = stamp()
    return q


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when the written file differs from what the artifacts give now")
    a = ap.parse_args()

    q = collect()
    text = json.dumps(q, indent=1, sort_keys=True) + "\n"
    n = sum(1 for k in q if not k.startswith("_"))

    def without_stamp(d):
        # the stamp records when the export ran, so it differs from itself every time
        return {k: v for k, v in d.items() if k != "_stamp"}

    if a.check:
        if not OUT.exists():
            print("export_quantities: %s has never been written" % OUT)
            return 1
        if without_stamp(json.loads(OUT.read_text())) == without_stamp(q):
            print("export_quantities: %s is current (%d quantities, %d unavailable)"
                  % (OUT.name, n, len(MISSING)))
            return 0
        print("export_quantities: %s is STALE; run without --check" % OUT.name)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print("export_quantities: wrote %d quantities to %s" % (n, OUT))
    for key, why in sorted(MISSING.items()):
        print("  unavailable  %-38s %s" % (key, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
