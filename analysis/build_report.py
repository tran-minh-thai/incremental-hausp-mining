#!/usr/bin/env python3
"""Builds every summary table and figure of the paper from the results/exp*/ CSVs.

Outputs go to analysis_out/paper/{standardized,tables,figures}: standardized
copies of the raw measurement logs, Markdown summary tables, and the figure
PDFs included in the manuscript. Deterministic: the same CSVs produce the
same outputs.

Usage: python3 analysis/build_report.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ARM_DISPLAY, PAPER_UB, PAPER_UB_L1L2, PAPER_UB_L1L3, load_experiment  # noqa: E402
RESULTS = ROOT / "results"
OUT = ROOT / "analysis_out" / "paper"
FIG, TAB, STD = OUT / "figures", OUT / "tables", OUT / "standardized"
for d in (FIG, TAB, STD):
    d.mkdir(parents=True, exist_ok=True)

EXP_FILES = {
    1: "exp1/experiment1_tightness.csv",
    2: "exp2/experiment2_pruning_power.csv",
    3: "exp3/experiment3_scalability.csv",
    4: "exp4/experiment4_memory_prelarge.csv",
    5: "exp5/experiment5_accuracy.csv",
    6: "exp6/experiment6_multibatch_accuracy.csv",
    7: "exp7/experiment7_long_batch.csv",
    8: "exp8/experiment8_threshold_sensitivity.csv",
}

DS_ORDER = ["BIBLE", "BMS1_SPMF", "FIFA", "KOSARAK", "LEVIATHAN", "SIGN", "C8T1S5I8N5K"]
# Paper arm is PAPER_UB (HAUSP-UB[noEUCS], displayed "HAUSP-UB"); the legacy
# "HAUSP-UB" (with EUCS) is kept as a comparison arm and displayed HAUSP-UB_EUCS.
# Only the arms the manuscript discusses. The configurations that carried the EUCS pre-filter
# (legacy "HAUSP-UB", "HAUSP-UB*", "HAUSP-UB-L1L3") left the paper on 2026-09-10 and must not
# appear in its figures; their rows stay in the artifacts and in EXPERIMENT_CHANGELOG.
ALGO_ORDER = ["EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM",
              "HAUSP-UB-L1", PAPER_UB_L1L3, PAPER_UB_L1L2, PAPER_UB]
# manuscript file names for the figures it \includegraphics (written next to the .tex)
PAPER_FIGS = {"exp1_eta_perbatch.pdf": "Figure2_exp1_eta_perbatch.pdf",
              "exp2_time_vs_minutil.pdf": "Figure3_exp2_time_vs_minutil.pdf",
              "exp2_cand_vs_minutil.pdf": "Figure4_exp2_cand_vs_minutil.pdf",
              "exp2_mem_vs_minutil.pdf": "Figure5_exp2_memory_vs_minutil.pdf",
              "exp3_time_vs_delta.pdf": "Figure6_exp3_time_vs_delta.pdf",
              "exp7_perbatch_growth.pdf": "Figure7_exp7_perbatch_growth.pdf",
              "exp8_eta_vs_minutil.pdf": "Figure8_exp8_eta_vs_minutil.pdf"}
PAPER_DIR = ROOT.parent / "paper"
OK = {"SUCCESS", "SUCCESS_MATCH"}

plt.rcParams.update({
    "figure.dpi": 120, "savefig.bbox": "tight",
    "font.size": 9, "axes.grid": True, "grid.alpha": 0.3,
    # journal-grade vector output: embed text as TrueType (Type 42), never Type 3
    "pdf.fonttype": 42, "ps.fonttype": 42,
})
MARKERS = {a: m for a, m in zip(ALGO_ORDER, ["s", "^", "D", "v", "P", "X", "o"])}


def disp(a: str) -> str:
    """Legend label of an arm (paper naming)."""
    return ARM_DISPLAY.get(a, a)


def save_fig(fig, fname: str) -> None:
    """Save under analysis_out and, for figures the manuscript includes, under its file name in ../paper/."""
    fig.savefig(FIG / fname)
    print(f"  [figure] {(FIG / fname).relative_to(ROOT)}")
    if fname in PAPER_FIGS and PAPER_DIR.is_dir():
        fig.savefig(PAPER_DIR / PAPER_FIGS[fname])
        print(f"  [figure] ../paper/{PAPER_FIGS[fname]}")


def load(exp: int) -> pd.DataFrame:
    """Merged legacy + 2026-09 rows of one experiment (see common.load_experiment); Cand columns unified."""
    df = load_experiment(exp)
    if df is None:
        raise SystemExit(f"no results for experiment {exp}")
    df["ok"] = df["Status"].isin(OK)
    df.to_csv(STD / f"exp{exp}_all_trials.csv", index=False)
    return df


def sort_key(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "Dataset" in df.columns:
        df["_d"] = df["Dataset"].map({d: i for i, d in enumerate(DS_ORDER)}).fillna(99)
    else:
        df["_d"] = 0
    if "Algorithm" in df.columns:
        df["_a"] = df["Algorithm"].map({a: i for i, a in enumerate(ALGO_ORDER)}).fillna(99)
    else:
        df["_a"] = 0
    df = df.sort_values(["_d", "_a"] + [c for c in ("MinUtil", "DeltaRatio", "BatchID") if c in df.columns])
    return df.drop(columns=["_d", "_a"])


def ms_fmt(mean: float, std: float, nd: int = 1) -> str:
    if np.isnan(mean):
        return "—"
    if np.isnan(std):
        return f"{mean:.{nd}f}"
    return f"{mean:.{nd}f} ± {std:.{nd}f}"


def save_md(df: pd.DataFrame, name: str, title: str) -> None:
    path = TAB / f"{name}.md"
    with open(path, "w") as fh:
        fh.write(f"# {title}\n\n")
        fh.write(df.to_markdown(index=False))
        fh.write("\n")
    print(f"  [table] {path.relative_to(ROOT)}")


def agg_trials(df: pd.DataFrame, keys: list[str], cols: dict[str, str]) -> pd.DataFrame:
    """Per-trial totals -> mean ± std strings across trials. cols: {csv_col: out_label}"""
    per_trial = df[df["ok"]].groupby(keys + ["RunIndex"], as_index=False)[list(cols)].sum()
    out = per_trial.groupby(keys, as_index=False)[list(cols)].agg(["mean", "std"])
    out.columns = [f"{c}|{s}" if s else c for c, s in out.columns]
    for c, label in cols.items():
        out[label] = [ms_fmt(m, s) for m, s in zip(out[f"{c}|mean"], out[f"{c}|std"])]
    return out


def errbar(ax, sub: pd.DataFrame, x: str, algo: str) -> None:
    ax.errorbar(sub[x], sub["mean"], yerr=sub["std"].fillna(0), label=disp(algo),
                marker=MARKERS.get(algo, "o"), ms=4, lw=1.2, capsize=2)


def line_fig(df: pd.DataFrame, x: str, y: str, fname: str, ylabel: str, logy: bool = False) -> None:
    """One subplot per dataset, one line per algorithm, error bars over trials.
    minUtil is displayed as a percentage for readability."""
    df = df.copy()
    xlabel = x
    if x == "MinUtil":
        df[x] = df[x] * 100.0
        xlabel = "minUtil (%)"
    elif x == "DeltaRatio":
        df[x] = df[x] * 100.0
        xlabel = "batch size δ (%)"
    ds_list = [d for d in DS_ORDER if d in set(df["Dataset"])]
    ncol = 3
    nrow = int(np.ceil(len(ds_list) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4 * ncol, 2.35 * nrow), squeeze=False)
    for i, ds in enumerate(ds_list):
        ax = axes[i // ncol][i % ncol]
        g = df[(df["Dataset"] == ds) & df["ok"]]
        for algo in [a for a in ALGO_ORDER if a in set(g["Algorithm"])]:
            sub = (g[g["Algorithm"] == algo]
                   .groupby([x, "RunIndex"], as_index=False)[y].sum()
                   .groupby(x, as_index=False)[y].agg(["mean", "std"]).reset_index())
            errbar(ax, sub, x, algo)
        ax.set_title(ds)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        if logy:
            ax.set_yscale("log")
    for j in range(len(ds_list), nrow * ncol):
        axes[j // ncol][j % ncol].axis("off")
    seen = {}
    for row in axes:
        for ax in row:
            h, l = ax.get_legend_handles_labels()
            for hh, ll in zip(h, l):
                seen.setdefault(ll, hh)
    disp_order = [disp(a) for a in ALGO_ORDER]
    labels = [a for a in disp_order if a in seen] + [l for l in seen if l not in disp_order]
    handles = [seen[l] for l in labels]
    if len(ds_list) < nrow * ncol:
        # shared legend in the empty bottom-right grid cell (same style as exp1 eta fig)
        fig.legend(handles, labels, loc="lower right", ncol=2, bbox_to_anchor=(0.92, 0.08))
        fig.tight_layout()
    else:
        fig.legend(handles, labels, loc="lower right", ncol=min(4, len(labels)))
        fig.tight_layout(rect=(0, 0.04, 1, 1))
    save_fig(fig, fname)
    plt.close(fig)


# ------------------------------------------------------------------ exp1
def exp1() -> None:
    print("== Experiment 1: tightness + runtime")
    df = load(1)
    t = agg_trials(df, ["Dataset", "Algorithm"],
                   {"tTotal(ms)": "Runtime (ms)", "CandUnified": "Candidates"})
    save_md(sort_key(t)[["Dataset", "Algorithm", "Runtime (ms)", "Candidates"]],
            "exp1_summary", "Experiment 1 — total runtime and candidates, mean ± std over 3 trials")

    # Tightness columns are populated per algorithm: PEAU on the EHAUSM rows,
    # IAUUB/MFUUB on the HAUSP-UB rows. Pull each from its source.
    peau = (df[df["ok"] & (df["Algorithm"] == "EHAUSM-I")]
            .groupby("Dataset", as_index=False)["TightnessPEAU"].mean())
    ours = (df[df["ok"] & (df["Algorithm"] == PAPER_UB)]
            .groupby("Dataset", as_index=False)[["TightnessIAUUB", "TightnessMFUUB"]].mean())
    tight = peau.merge(ours, on="Dataset")
    save_md(sort_key(tight), "exp1_tightness",
            "Experiment 1 — mean bound tightness (PEAU from EHAUSM-I rows; IAUUB/MFUUB from HAUSP-UB rows)")

    lay = df[df["ok"] & (df["Algorithm"] == PAPER_UB)]
    lay_t = agg_trials(lay, ["Dataset"],
                       {"tLayer1(ms)": "L1 (ms)", "tLayer2(ms)": "L2 (ms)",
                        "tLayer3(ms)": "L3 (ms)", "tTotal(ms)": "Total (ms)"})
    save_md(sort_key(lay_t)[["Dataset", "L1 (ms)", "L2 (ms)", "L3 (ms)", "Total (ms)"]],
            "exp1_layer_breakdown", "Experiment 1 — per-layer time breakdown of HAUSP-UB")

    # grouped runtime bar with error bars
    per = (df[df["ok"]].groupby(["Dataset", "Algorithm", "RunIndex"], as_index=False)["tTotal(ms)"].sum())
    ds_list = [d for d in DS_ORDER if d in set(per["Dataset"])]
    algos = [a for a in ALGO_ORDER if a in set(per["Algorithm"])]
    fig, ax = plt.subplots(figsize=(10, 4))
    w = 0.8 / len(algos)
    for k, algo in enumerate(algos):
        m, s = [], []
        for ds in ds_list:
            v = per[(per["Dataset"] == ds) & (per["Algorithm"] == algo)]["tTotal(ms)"] / 1000.0
            m.append(v.mean())
            s.append(v.std())
        pos = np.arange(len(ds_list)) + (k - len(algos) / 2 + 0.5) * w
        ax.bar(pos, m, w, yerr=s, capsize=2, label=disp(algo))
    ax.set_xticks(np.arange(len(ds_list)))
    ax.set_xticklabels(ds_list, rotation=15)
    ax.set_ylabel("total runtime (s)")
    ax.set_yscale("log")
    ax.legend(ncol=len(algos), fontsize=7)
    save_fig(fig, "exp1_runtime_bar.pdf")
    plt.close(fig)


# ------------------------------------------------------------------ exp2
def exp2() -> None:
    print("== Experiment 2: ablation sweep incl. anchor thresholds")
    df = load(2)
    t = agg_trials(df, ["Dataset", "Algorithm", "MinUtil"],
                   {"tTotal(ms)": "Runtime (ms)", "CandUnified": "Candidates", "MemPeak(MB)": "Peak MB"})
    save_md(sort_key(t)[["Dataset", "Algorithm", "MinUtil", "Runtime (ms)", "Candidates", "Peak MB"]],
            "exp2_summary", "Experiment 2 — ablation sweep, mean ± std over 3 trials")
    line_fig(df, "MinUtil", "tTotal(ms)", "exp2_time_vs_minutil.pdf", "runtime (ms)", logy=True)
    line_fig(df, "MinUtil", "CandUnified", "exp2_cand_vs_minutil.pdf", "candidates", logy=True)
    line_fig(df, "MinUtil", "MemPeak(MB)", "exp2_mem_vs_minutil.pdf", "peak memory (MB)", logy=True)


# ------------------------------------------------------------------ exp3
def exp3() -> None:
    print("== Experiment 3: delta scalability")
    df = load(3)
    inc = df[df["BatchID"] > 0] if (df["BatchID"] > 0).any() else df
    t = agg_trials(inc, ["Dataset", "Algorithm", "DeltaRatio"], {"tTotal(ms)": "Runtime (ms)"})
    save_md(sort_key(t)[["Dataset", "Algorithm", "DeltaRatio", "Runtime (ms)"]],
            "exp3_summary", "Experiment 3 — incremental-batch runtime vs delta, mean ± std")
    line_fig(inc, "DeltaRatio", "tTotal(ms)", "exp3_time_vs_delta.pdf", "runtime (ms)", logy=True)


# ------------------------------------------------------------------ exp4
def exp4() -> None:
    print("== Experiment 4: memory + pool")
    df = load(4)
    per = df[df["ok"]].groupby(["Dataset", "Algorithm", "RunIndex"], as_index=False)["MemPeak(MB)"].max()
    out = per.groupby(["Dataset", "Algorithm"], as_index=False)["MemPeak(MB)"].agg(["mean", "std"]).reset_index(drop=False)
    out["Peak memory (MB)"] = [ms_fmt(m, s) for m, s in zip(out["mean"], out["std"])]
    save_md(sort_key(out)[["Dataset", "Algorithm", "Peak memory (MB)"]],
            "exp4_summary", "Experiment 4 — peak memory, mean ± std over 3 trials")

    pool_cols = [c for c in ("PoolBorrows", "PoolReuses", "PoolPeakLive") if c in df.columns]
    if pool_cols:
        pool = (df[df["ok"] & (df["Algorithm"] == PAPER_UB)]
                .groupby("Dataset", as_index=False)[pool_cols].mean())
        if pool.empty:
            # the paper arm was not measured in this experiment: say so and remove any stale table
            stale = TAB / "exp4_pool_stats.md"
            if stale.exists():
                stale.unlink()
            print(f"  [skip]  exp4_pool_stats: no {PAPER_UB} rows in experiment 4 (stale table removed)")
        else:
            save_md(sort_key(pool), "exp4_pool_stats",
                    "Experiment 4 — AU-DUL shared-pool statistics of HAUSP-UB")


# ------------------------------------------------------------------ exp5/6
def exp56() -> None:
    print("== Experiments 5 & 6: correctness vs oracle")
    # exp5: one row per (algorithm, config) with a HAUSP count column
    df5 = load(5)
    keys = [k for k in ("Dataset", "MinUtil", "BatchID") if k in df5.columns]
    piv = (df5[df5["ok"]]
           .pivot_table(index=keys, columns="Algorithm", values="HAUSP", aggfunc="first")
           .reset_index())
    algs = [c for c in piv.columns if c in set(df5["Algorithm"])]
    if len(algs) == 2:
        piv["match"] = piv[algs[0]] == piv[algs[1]]
    save_md(sort_key(piv), "exp5_correctness",
            "Experiment 5 — pattern counts per algorithm (oracle match)")

    # exp6: already pivoted (HAUSP_<algo> columns), one row per batch
    df6 = load(6)
    cnt_cols = [c for c in df6.columns if c.startswith("HAUSP_")]
    if len(cnt_cols) == 2:
        df6["match"] = df6[cnt_cols[0]] == df6[cnt_cols[1]]
    keep = [c for c in ("Dataset", "MinUtil", "BatchID") if c in df6.columns] + cnt_cols + ["Status", "match"]
    save_md(sort_key(df6[keep]), "exp6_correctness",
            "Experiment 6 — multi-batch pattern counts per algorithm (oracle match)")


# ------------------------------------------------------------------ exp7
def exp7() -> None:
    print("== Experiment 7: long-batch growth + survival")
    df = load(7)
    df["K"] = (1.0 / df["DeltaRatio"]).round().astype(int)

    ok = df[df["ok"]]
    t = agg_trials(ok.assign(K=ok["K"]), ["Dataset", "Algorithm", "K"],
                   {"tTotal(ms)": "Runtime (ms)", "MemPeak(MB)": "Sum peak MB"})
    save_md(sort_key(t)[["Dataset", "Algorithm", "K", "Runtime (ms)"]],
            "exp7_summary", "Experiment 7 — total runtime vs batch count K, mean ± std")

    # survival table: max K fully succeeded + failure mode at the next K
    rows = []
    for (ds, algo), g in df.groupby(["Dataset", "Algorithm"]):
        okk = sorted(set(g[g["Status"] == "SUCCESS"]["K"]))
        complete = [k for k in okk
                    if len(g[(g["K"] == k) & (g["Status"] == "SUCCESS") & (g["RunIndex"] == 0)]) == k]
        maxk = max(complete) if complete else 0
        fail = g[g["Status"].isin(("OT", "OOM"))]
        fmode = "/".join(sorted(set(f"{s}@K={k}" for s, k in zip(fail["Status"], fail["K"]))))
        rows.append({"Dataset": ds, "Algorithm": algo, "max K completed": maxk,
                     "failures": fmode or "—"})
    save_md(sort_key(pd.DataFrame(rows)), "exp7_survival",
            "Experiment 7 — largest completed K per algorithm and recorded failure modes")

    # per-batch growth of HAUSP-UB (no unbounded growth): mean per-batch time vs batch index
    hu = df[(df["Algorithm"] == PAPER_UB) & df["ok"]]
    hu_all = df[df["Algorithm"] == PAPER_UB]
    ds_list = [d for d in DS_ORDER if d in set(hu["Dataset"])]
    ncol = 3
    nrow = int(np.ceil(len(ds_list) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4 * ncol, 2.35 * nrow), squeeze=False)
    for i, ds in enumerate(ds_list):
        ax = axes[i // ncol][i % ncol]
        for k in sorted(set(hu[hu["Dataset"] == ds]["K"])):
            sub = (hu[(hu["Dataset"] == ds) & (hu["K"] == k)]
                   .groupby("BatchID", as_index=False)["tTotal(ms)"].mean())
            ax.plot(sub["BatchID"], sub["tTotal(ms)"], lw=1, label=f"K={k}")
        # annotate K values whose run failed (OT/OOM) so absent curves are not
        # mistaken for missing data
        g_ds = hu_all[hu_all["Dataset"] == ds]
        failed = sorted(set(g_ds[g_ds["Status"].isin(["OT", "OOM"])]["K"]))
        failed = [k for k in failed
                  if not len(hu[(hu["Dataset"] == ds) & (hu["K"] == k)])]
        if failed:
            st = g_ds[g_ds["Status"].isin(["OT", "OOM"])]["Status"].iloc[0]
            ax.text(0.97, 0.06, f"{st}: K=" + ",".join(str(k) for k in failed),
                    transform=ax.transAxes, ha="right", va="bottom",
                    fontsize=7, color="0.35", style="italic")
        ax.set_title(ds)
        ax.set_xlabel("batch index")
        ax.set_ylabel("per-batch time (ms)")
        ax.set_yscale("log")
    for j in range(len(ds_list), nrow * ncol):
        axes[j // ncol][j % ncol].axis("off")
    seen = {}
    for row in axes:
        for ax in row:
            h, l = ax.get_legend_handles_labels()
            for hh, ll in zip(h, l):
                seen.setdefault(ll, hh)
    order = sorted(seen, key=lambda s: int(s.split("=")[1]))
    fig.legend([seen[k] for k in order], order, loc="lower right", ncol=2,
               bbox_to_anchor=(0.92, 0.08))
    fig.tight_layout()
    save_fig(fig, "exp7_perbatch_growth.pdf")
    plt.close(fig)


# ------------------------------------------------------------------ exp8
def exp8() -> None:
    print("== Experiment 8: eta sensitivity near noise floor")
    df = load(8)
    df["eta"] = df["CandUnified"] / df["HAUSP"].replace(0, np.nan)
    t = df[df["ok"]].groupby(["Dataset", "Algorithm", "MinUtil"], as_index=False).agg(
        eta_mean=("eta", "mean"), eta_std=("eta", "std"),
        HAUSP=("HAUSP", "mean"))
    t["eta"] = [ms_fmt(m, s) for m, s in zip(t["eta_mean"], t["eta_std"])]
    save_md(sort_key(t)[["Dataset", "Algorithm", "MinUtil", "eta", "HAUSP"]],
            "exp8_summary", "Experiment 8 — candidate efficiency eta vs minUtil, mean ± std")
    line_fig(df.assign(**{"eta": df["eta"]}), "MinUtil", "eta",
             "exp8_eta_vs_minutil.pdf", "eta = Cand / HAUSP", logy=True)


def prehauspm_completeness() -> None:
    """Quantify the recall loss of the pre-large baseline: Pre-HAUSPM misses patterns, never finds extras."""
    print("== Pre-HAUSPM completeness vs exact algorithms")
    rows = []
    for exp in (1, 3, 4, 7):
        df = load(exp)
        ok = df[df["ok"] & (df["RunIndex"] == 0)]
        if "Pre-HAUSPM" not in set(ok["Algorithm"]):
            continue
        piv = ok.pivot_table(index=["Dataset", "BatchID", "MinUtil", "DeltaRatio"],
                             columns="Algorithm", values="HAUSP", aggfunc="first")
        exact_arm = PAPER_UB if PAPER_UB in piv.columns else "HAUSP-UB"  # both are exact (Exp 5/6)
        both = piv["Pre-HAUSPM"].notna() & piv[exact_arm].notna()
        p = piv[both]
        for ds, g in p.groupby(level="Dataset"):
            exact = g[exact_arm].sum()
            found = g["Pre-HAUSPM"].sum()
            rows.append({"Experiment": f"exp{exp}", "Dataset": ds,
                         "configs": len(g),
                         "configs missed": int((g["Pre-HAUSPM"] < g[exact_arm]).sum()),
                         "patterns exact": int(exact),
                         "patterns found": int(found),
                         "recall %": round(100 * found / exact, 3)})
    save_md(pd.DataFrame(rows), "prehauspm_completeness",
            "Pre-HAUSPM recall vs exact result (misses only, no false positives)")


def variance_report() -> None:
    """Measurement-variance audit across the trials: per-configuration CV of
    runtime and peak memory, plus determinism of counts."""
    print("== Variance across trials")
    rows = []
    det_rows = []
    for exp in (1, 2, 3, 4, 8):
        df = load(exp)
        ok = df[df["ok"]]
        keys = ["Dataset", "Algorithm", "MinUtil", "DeltaRatio"]
        # per-trial totals per config
        per = ok.groupby(keys + ["RunIndex"], as_index=False).agg(
            t=("tTotal(ms)", "sum"), m=("MemPeak(MB)", "max"))
        g = per.groupby(keys)
        cv_t = (g["t"].std() / g["t"].mean()).dropna() * 100
        cv_m = (g["m"].std() / g["m"].mean()).dropna() * 100
        # Sub-second configs show inflated relative variance (tens of ms of
        # noise on a tiny base); report the >=5s subset separately — that is
        # the defensible headline number for the paper.
        mean_t = g["t"].mean()
        cv_t_big = cv_t[mean_t[cv_t.index] >= 5000]
        # determinism: candidate & pattern counts must not vary across trials
        det = ok.groupby(keys + ["BatchID"])[["CandUnified", "HAUSP"]].nunique()
        nondet = int((det > 1).any(axis=1).sum())
        rows.append({
            "Experiment": f"exp{exp}", "configs": len(cv_t),
            "runtime CV median %": round(float(cv_t.median()), 2),
            "runtime CV p95 %": round(float(cv_t.quantile(0.95)), 2),
            "runtime CV max %": round(float(cv_t.max()), 2),
            "runtime CV max % (runs >=5s)": round(float(cv_t_big.max()), 2) if len(cv_t_big) else float("nan"),
            "memory CV median %": round(float(cv_m.median()), 2),
            "memory CV max %": round(float(cv_m.max()), 2),
        })
        det_rows.append({"Experiment": f"exp{exp}",
                         "configs with non-deterministic counts": nondet})
    # exp7: groups with 3 complete SUCCESS trials
    df7 = load(7)
    ok7 = df7[df7["ok"]].copy()
    ok7["K"] = (1.0 / ok7["DeltaRatio"]).round().astype(int)
    per7 = ok7.groupby(["Dataset", "Algorithm", "K", "RunIndex"], as_index=False).agg(
        n=("BatchID", "count"), t=("tTotal(ms)", "sum"), m=("MemPeak(MB)", "max"))
    per7 = per7.merge(per7.groupby(["Dataset", "Algorithm", "K"])["RunIndex"].nunique()
                      .rename("trials"), on=["Dataset", "Algorithm", "K"])
    full = per7[per7["trials"] == 3]
    g7 = full.groupby(["Dataset", "Algorithm", "K"])
    cv7t = (g7["t"].std() / g7["t"].mean()).dropna() * 100
    cv7m = (g7["m"].std() / g7["m"].mean()).dropna() * 100
    rows.append({
        "Experiment": "exp7", "configs": len(cv7t),
        "runtime CV median %": round(float(cv7t.median()), 2),
        "runtime CV p95 %": round(float(cv7t.quantile(0.95)), 2),
        "runtime CV max %": round(float(cv7t.max()), 2),
        "memory CV median %": round(float(cv7m.median()), 2),
        "memory CV max %": round(float(cv7m.max()), 2),
    })
    save_md(pd.DataFrame(rows), "variance_summary",
            "Run-to-run variance over 3 trials — CV of runtime and peak memory per configuration")
    save_md(pd.DataFrame(det_rows), "variance_determinism",
            "Determinism check — configs whose candidate/pattern counts differ between trials (must be 0)")
    worst = cv7t.sort_values(ascending=False).head(5)
    for (ds, a, k), v in worst.items():
        print(f"    exp7 worst CV: {ds} {a} K={k}: {v:.1f}%")


def exp1_eta_perbatch_fig() -> None:
    """Per-batch candidate efficiency, one panel per dataset (paper Figure B.1)."""
    df = load(1)
    ok = df[df["ok"] & (df["RunIndex"] == 0)].copy()
    ok["eta"] = ok["CandUnified"] / ok["HAUSP"].replace(0, np.nan)
    algos = ["EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM", PAPER_UB]
    ds_list = [d for d in DS_ORDER if d in set(ok["Dataset"])]
    ncol = 3
    nrow = int(np.ceil(len(ds_list) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4 * ncol, 2.35 * nrow), squeeze=False)
    for i, ds in enumerate(ds_list):
        ax = axes[i // ncol][i % ncol]
        for a in algos:
            g = ok[(ok["Dataset"] == ds) & (ok["Algorithm"] == a)].sort_values("BatchID")
            if len(g):
                ax.plot(g["BatchID"], g["eta"], marker=MARKERS.get(a, "o"),
                        ms=4, lw=1.2, label=disp(a))
        ax.set_title(ds)
        ax.set_yscale("log")
        ax.set_xlabel("batch")
        ax.set_ylabel(r"$\eta$")
        ax.set_xticks(range(5))
    for j in range(len(ds_list), nrow * ncol):
        axes[j // ncol][j % ncol].axis("off")
    h, l = axes[0][0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower right", ncol=2, bbox_to_anchor=(0.92, 0.08))
    fig.tight_layout()
    save_fig(fig, "exp1_eta_perbatch.pdf")
    plt.close(fig)


if __name__ == "__main__":
    for fn in (exp1, exp1_eta_perbatch_fig, exp2, exp3, exp4, exp56, exp7, exp8,
               prehauspm_completeness, variance_report):
        fn()
    print(f"\nAll outputs under {OUT.relative_to(ROOT)}/")
