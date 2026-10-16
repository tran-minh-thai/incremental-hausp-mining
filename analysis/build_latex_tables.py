#!/usr/bin/env python3
"""Generate every numeric table of the manuscript from the result CSVs.

Sources: the measurement campaign under ``results/`` (live heap under ``results/mem/``), read
through ``common.load_experiment`` and ``common.load_memory``. Every table starts with
``% source: <csv> run_id=...`` naming the files and run ids behind it.

Candidate counts use ONE definition for every algorithm, "utility lists assembled" (``Cand``);
nodes expanded (passed every test, children generated) is ``Recursed - PrunedL3Node`` for the
HAUSP-UB arms and ``Cand - PrunedL2`` for the baselines (``ExpandedUnified`` in
``common.add_unified_counts``); arms that explore the same tree give the same value.

Thresholds, sweeps and schedules come from ``ExperimentLauncher --dump-config
json``; dataset characteristics from ``dataset_stats.py``. Nothing is typed
by hand. Ratios are printed with three significant figures.

Output: analysis_out/paper/latex/*.tex plus a byte-identical copy in
../paper/tables/ (the directory the manuscript ``\\input``s) and
tables_manifest.json listing file, label and sources of every table.

Usage: python3 analysis/build_latex_tables.py
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (pool_per_run, ANALYSIS_OUT, ARM_DISPLAY, DS_ORDER, exp3_update_live_heap, OK, PAPER_UB, PAPER_UB_L1L2, PAPER_UB_L1L3, ROOT,  # noqa: E402
                    ds_tex, fmt_sig, human, load_config, load_experiment, load_memory, ms_std,
                    paper_experiment, source_comment, RESULT_FILE, group_digits)

OUT = ANALYSIS_OUT / "latex"
PAPER_TABLES = ROOT.parent / "paper" / "tables"
MANIFEST = OUT / "tables_manifest.json"
#: Arms of the runtime and memory tables. The Layer-1-only variant exceeds the limit at the first
#: batch everywhere; the text says so, and its all-failure column was dropped (author, 2026-10-16).
ALGO4 = ["EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM", PAPER_UB]
#: The Layer-1-only arm is the one variant that also keeps the co-occurrence pre-filter, so
#: every caption printing it has to say what the subscript means. Defined once, used in each.
DISP = ARM_DISPLAY
TABLE_OPEN = r"\begin{table}[!htbp]"

#: Page layout per output file, for the journal's text block (31 pc in referee mode): the float
#: environment, and the size line that follows \centering (font size and column separation). A
#: table wider than the block by more than about 40 pt is set sideways; a narrower overflow is
#: absorbed by a smaller font or tighter columns. Never \resizebox: it gives every table a
#: different type size.
LAYOUT = {
    "tab_datasets.tex":         {"env": "sidewaystable", "size": r"\scriptsize\setlength{\tabcolsep}{2.5pt}"},
    "tab_exp9_attribution.tex": {"env": "sidewaystable", "size": r"\tiny\setlength{\tabcolsep}{0.8pt}",
                                 "tight_pm": True},
    "tab_variance.tex":         {"env": "sidewaystable"},
    "tab_pool.tex":             {"env": "sidewaystable"},
    "tab_exp10_mu.tex":         {"env": "sidewaystable"},
    "tab_exp7_matrix.tex":      {},
    "tab_exp2_pruned.tex":      {"env": "sidewaystable"},
    "tab_exp8_eta.tex":         {"size": r"\scriptsize"},
    "tab_exp1_runtime.tex":     {"size": r"\footnotesize\setlength{\tabcolsep}{3pt}"},
    "tab_exp4_memory.tex":      {"size": r"\footnotesize\setlength{\tabcolsep}{3pt}"},
}


def apply_layout(name: str, lines: list[str]) -> list[str]:
    """The float environment and size line of LAYOUT applied to a built table, refusing a table
    whose shape this does not recognise rather than leaving it in the default layout."""
    spec = LAYOUT.get(name, {})
    out = list(lines)
    if TABLE_OPEN not in out or r"\end{table}" not in out or r"\centering" not in out:
        raise SystemExit(f"apply_layout: {name} does not open with {TABLE_OPEN!r}, \\centering and a size line")
    if spec.get("env", "table") != "table":
        env = spec["env"]
        # The class redefines sidewaystable with an empty default placement, which LaTeX reports
        # as "No positions in optional float specifier"; a rotated table sits on its own page, so
        # [p] states what happens anyway (same page for every label, same PDF text, 2026-10-16).
        out[out.index(TABLE_OPEN)] = rf"\begin{{{env}}}[p]"
        out[len(out) - 1 - out[::-1].index(r"\end{table}")] = rf"\end{{{env}}}"
    if "size" in spec:
        out[out.index(r"\centering") + 1] = spec["size"]
    if spec.get("tight_pm"):
        out = [ln.replace(" $\pm$ ", r"$\,\pm\,$") for ln in out]
    return out


def exp_span(nums) -> str:
    """Manuscript experiment numbers as a phrase: '2 to~6' for a run of three or more, else
    '2, 3, 4 and 6'."""
    n = sorted(set(nums))
    if len(n) >= 3 and n == list(range(n[0], n[-1] + 1)):
        return f"{n[0]} to~{n[-1]}"
    return n and (", ".join(str(x) for x in n[:-1]) + (" and " if len(n) > 1 else "") + str(n[-1]))
GEN_NOTE = "% Generated by analysis/build_latex_tables.py from the CSVs named above; do not edit by hand."

#: Captions are one line: a title and nothing else. What a symbol means, how a quantity was
#: measured and what a schedule is belong in the body, where a reader looks for an explanation
#: rather than under a table. Keeping them here, keyed by label, means a caption cannot drift
#: from the table it belongs to, and check_captions.py refuses one that grows past a line.
CAPTIONS = {
    "tab:variance":     r"Run-to-run variability of total runtime, by experiment.",
    "tab:datasets":     r"Dataset characteristics and $minUtil$ sweeps.",
    "tab:exp1_eta_avg": r"Mean candidate-generation efficiency $\eta$ over five batches.",
    "tab:exp1_runtime": r"Total runtime (s) over five batches.",
    "tab:exp2_pruning": r"Ablation of the pruning layers, summed over the $minUtil$ sweep.",
    "tab:exp3_delta20": r"Update processing at $\delta = 20\%$ (batch~1).",
    "tab:exp4_memory":  r"Peak sampled live heap (MB) over five update batches.",
    "tab:pool":         r"Behavior of the shared list pool of HAUSP-UB over five update batches.",
    "tab:exactness":    r"Exactness against the APEAU-R re-mining reference.",
    "tab:exp7_matrix":  r"Total runtime (min) for $K$ batches at fixed total volume.",
    "tab:exp8_eta":     r"Candidate-generation efficiency $\eta$ at low thresholds.",
    "tab:attribution":  r"Attribution of the runtime gap, per arm and dataset.",
    "tab:exp10_mu":     r"Pre-HAUSPM update time and rescan decision under four safety margins.",
}


def one_line_caption(lines: list[str], label: str) -> list[str]:
    """Replace whatever caption the table was built with by the one-line title for its label."""
    want = CAPTIONS.get(label)
    if want is None:
        return lines
    out, seen = [], False
    for ln in lines:
        if not seen and r"\caption{" in ln:
            head = ln[:ln.index(r"\caption{")]
            out.append(head + r"\caption{" + want + "}")
            seen = True
            continue
        if not seen and out and r"\caption{" in "".join(out[-3:]):
            pass
        out.append(ln)
    if not seen:
        raise ValueError("no caption line in the table for %s" % label)
    # drop the continuation lines of the old caption: everything between the new caption and
    # the label line that is not a label, a tabular opener or a rule
    res, dropping = [], False
    for ln in out:
        if r"\caption{" in ln:
            res.append(ln); dropping = True; continue
        if dropping:
            if "\\label{" in ln or ln.strip().startswith((r"\begin{tabular}", r"\toprule", r"(a)", r"\centering")):
                dropping = False
            else:
                continue
        res.append(ln)
    return res


manifest: list[dict] = []
_config = None
_frames: dict[int, pd.DataFrame | None] = {}


def cfg() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def data(exp: int) -> pd.DataFrame | None:
    if exp not in _frames:
        _frames[exp] = load_experiment(exp)
    return _frames[exp]


def disp(a: str) -> str:
    return DISP.get(a, a)


#: Tables the manuscript no longer reports, each with the decision that removed it. They are still
#: built (and verified) here, but no copy goes to the manuscript's tables/ directory, and
#: check_inputs.py refuses one that the manuscript inputs again. Every other table must be input.
UNREPORTED = {
    "tab_exp8_eta.tex":    "author, 2026-10-16: the low-threshold study is no longer reported",
    "tab_exp2_pruned.tex": "author, 2026-10-16: the ablation is reported in the text of the attribution experiment",
    "tab_pool.tex":        "author, 2026-10-16: the pool figures are given in the text",
    "tab_variance.tex":    "author, 2026-10-16: the variability figures are given in the text",
    "tab_exp3_delta20.tex": "author, 2026-10-16: the update figures are given in the text",
    "tab_exp10_mu.tex":    "author, 2026-10-16: the safety-margin figures and the rescan count are given in the text",
}


def emit(name: str, label: str, lines: list[str], frames: list[pd.DataFrame | None]) -> None:
    lines = apply_layout(name, one_line_caption(lines, label))
    text = "\n".join([source_comment([f for f in frames if f is not None]), GEN_NOTE] + lines) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(text)
    targets = [str((OUT / name).relative_to(ROOT))]
    # A table the manuscript no longer reports gets no copy there; an old copy is named, never
    # refreshed, so a dead table cannot look current.
    if PAPER_TABLES.exists() and name not in UNREPORTED:
        (PAPER_TABLES / name).write_text(text)
        targets.append(str(PAPER_TABLES / name))
    elif (PAPER_TABLES / name).exists():
        print(f"  [stale] {PAPER_TABLES / name}: not reported ({UNREPORTED[name]}), copy not refreshed")
    manifest.append({"file": name, "label": label, "reported": name not in UNREPORTED,
                     "sources": [f.attrs.get("source") for f in frames if f is not None],
                     "run_ids": sorted({r for f in frames if f is not None for r in (f.attrs.get("run_ids") or [])})})
    print(f"  [latex] {' , '.join(targets)}")


def table_head(caption: str, label: str, colspec: str, header: str, size: str = r"\small") -> list[str]:
    return [TABLE_OPEN, r"\centering", size, r"\renewcommand{\arraystretch}{1.15}",
            r"\caption{" + caption + "}", label, r"\begin{tabular}{" + colspec + "}", r"\toprule", header, r"\midrule"]


def table_tail(extra: list[str] | None = None) -> list[str]:
    return [r"\bottomrule"] + (extra or []) + [r"\end{tabular}", r"\end{table}"]


def bold_best(cells: list[tuple[float | None, str]], lower_is_better: bool = True) -> list[str]:
    """Wrap the best numeric cell of a group in \\textbf; non-numeric cells pass through."""
    nums = [v for v, _ in cells if v is not None and np.isfinite(v)]
    if not nums:
        return [s for _, s in cells]
    best = min(nums) if lower_is_better else max(nums)
    return [(r"\textbf{" + s + "}" if (v is not None and np.isfinite(v) and v == best) else s) for v, s in cells]


def complete_trials(g: pd.DataFrame, n_batches: int) -> list[int]:
    succ = g[g["Status"].isin(OK)]
    return [r for r in sorted(succ["RunIndex"].unique()) if len(succ[succ["RunIndex"] == r]) == n_batches]


def fail_cell(g: pd.DataFrame) -> str | None:
    f = g[g["Status"].isin(["OT", "OOM"])].sort_values(["RunIndex", "BatchID"])
    if len(f):
        r = f.iloc[0]
        return f"{r['Status']}@{int(r['BatchID'])}"
    return None


def pct(v: float) -> str:
    if not np.isfinite(v):
        return "--"
    if 0 < v < 0.05:
        return "$<$0.1"
    return f"{v:.1f}"


def pct_std(m: float, sd: float) -> str:
    """A percentage with its spread over trials, at the precision the table prints.

    A cell below the printing precision is shown as an upper bound with no spread: "0.03 +- 0.01"
    claims a resolution the table does not have. Everything else carries its std, including the
    cells where it rounds to zero, so a reader can tell "measured as zero spread" from "not said".
    """
    if not np.isfinite(m):
        return "--"
    if 0 < m < 0.05:
        return "$<$0.1"
    s = 0.0 if not np.isfinite(sd) else sd
    return f"{m:.1f} $\\pm$ {s:.1f}"


def thousands(v) -> str:
    return group_digits(v)


def nsum(series) -> float:
    """Sum that stays NaN when every value is NaN (a legacy HAUSP-UB count not yet re-measured)."""
    return float(series.sum(min_count=1)) if len(series) else float("nan")


def fmt_eta(v: float, nd: int = 1) -> str:
    if v is None or not np.isfinite(v):
        return "--"
    if v >= 1000:
        for cut, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if v >= cut:
                return f"{v / cut:.{nd}f}{suf}"
    return f"{v:,.{nd}f}"


def fmt_eta_row(values: list[float]) -> list[str]:
    """One decimal, or more where two different values of the row would otherwise print alike.

    A row whose best cell is chosen on unrounded values must also show why: on Ta-Feng the three
    means are 4.08M, 4.12M and 4.12M, and at one decimal all three read 4.1M next to a bold one.
    """
    for nd in (1, 2, 3):
        cells = [fmt_eta(v, nd) for v in values]
        clash = any(cells[i] == cells[j] and values[i] != values[j]
                    for i in range(len(values)) for j in range(i + 1, len(values))
                    if np.isfinite(values[i]) and np.isfinite(values[j]))
        if not clash:
            return cells
    return cells


# ----------------------------------------------------------------------------- tables
def paper_arms(exp: int) -> set[str]:
    """The arms an experiment declares, i.e. the ones its tables print."""
    return set(next(e for e in cfg()["experiments"] if e["id"] == exp)["algorithms"])


def tab_variance() -> None:
    # Only the arms the paper prints: the spread of an arm no table shows says nothing about the
    # measurements a reader is asked to trust (when legacy arms were pooled here, they made up to
    # two fifths of one row's configurations).
    rows = []
    frames = []
    # Which configuration holds each row's largest CV, written beside the table: a sentence that
    # names it ("for Pre-HAUSPM on LEVIATHAN in Experiment 1") is then checked against this file,
    # computed here once, instead of against a second implementation of the same statistic.
    where = []
    # Runtime rows. Experiment 4 of the paper is a memory experiment, so its row below reports the
    # spread of the quantity its table prints (peak live heap), not of runtime.
    for exp in (1, 2, 3, 8, 9):
        df = data(exp)
        if df is None:
            continue
        frames.append(df)
        ok = df[df["Status"].isin(OK) & df["Algorithm"].isin(paper_arms(exp))]
        keys = ["Dataset", "Algorithm", "MinUtil", "DeltaRatio"]
        per = ok.groupby(keys + ["RunIndex"], as_index=False).agg(t=("tTotal(ms)", "sum"))
        g = per.groupby(keys)["t"]
        cv = (g.std() / g.mean()).dropna() * 100
        ntr = g.count()
        big = cv[g.mean()[cv.index] >= 5000]
        if paper_experiment(exp) is None:
            continue
        rows.append((f"Exp.~{paper_experiment(exp)}", "runtime", len(cv), f"{ntr.min()}--{ntr.max()}" if ntr.min() != ntr.max() else str(ntr.min()),
                     cv.median(), cv.quantile(0.95), big.max() if len(big) else float("nan")))
        if len(big):
            ds_, algo_, mu_, dr_ = big.idxmax()
            where.append({"experiment": exp, "max_cv_percent": round(float(big.max()), 1), "dataset": ds_,
                          "algorithm": algo_, "min_util": float(mu_), "delta_ratio": float(dr_)})
    mem4 = load_memory(4)
    if mem4 is not None:
        frames.append(mem4)
        okm = mem4[mem4["Status"].isin(OK) & mem4["Algorithm"].isin(paper_arms(4))]
        perm = okm.groupby(["Dataset", "Algorithm", "RunIndex"]).agg(n=("BatchID", "nunique"), heap=("MemLive(MB)", "max"),
                                                                   t=("tTotal(ms)", "sum")).reset_index()
        nb = perm["n"].max()
        perm = perm[perm["n"] == nb]                     # complete trials only, as the memory table
        gm = perm.groupby(["Dataset", "Algorithm"])
        cvm = (gm["heap"].std() / gm["heap"].mean()).dropna() * 100
        nm = gm["heap"].count()
        bigm = cvm[gm["t"].mean()[cvm.index] >= 5000]
        rows.append((f"Exp.~{paper_experiment(4)}", "live heap", len(cvm), f"{nm.min()}--{nm.max()}" if nm.min() != nm.max() else str(nm.min()),
                     cvm.median(), cvm.quantile(0.95), bigm.max() if len(bigm) else float("nan")))
        if len(bigm):
            ds_, algo_ = bigm.idxmax()
            where.append({"experiment": 4, "quantity": "live heap", "max_cv_percent": round(float(bigm.max()), 1),
                          "dataset": ds_, "algorithm": algo_})
    df7 = data(7)
    if df7 is not None:
        frames.append(df7)
        ok7 = df7[df7["Status"].isin(OK) & df7["Algorithm"].isin(paper_arms(7))].copy()
        ok7["K"] = (1.0 / ok7["DeltaRatio"]).round().astype(int)
        per7 = ok7.groupby(["Dataset", "Algorithm", "K", "RunIndex"], as_index=False).agg(n=("BatchID", "count"), t=("tTotal(ms)", "sum"))
        per7 = per7[per7["n"] == per7["K"]]
        tri = per7.groupby(["Dataset", "Algorithm", "K"])["RunIndex"].nunique()
        full = per7.set_index(["Dataset", "Algorithm", "K"]).loc[tri[tri >= 3].index].reset_index()
        g7 = full.groupby(["Dataset", "Algorithm", "K"])["t"]
        cv7 = (g7.std() / g7.mean()).dropna() * 100
        n7 = g7.count()
        big7 = cv7[g7.mean()[cv7.index] >= 5000]
        rows.append((f"Exp.~{paper_experiment(7)}", "runtime", len(cv7), f"{n7.min()}--{n7.max()}" if len(n7) and n7.min() != n7.max() else (str(n7.min()) if len(n7) else "--"),
                     cv7.median(), cv7.quantile(0.95), big7.max() if len(big7) else float("nan")))
        if len(big7):
            ds_, algo_, k_ = big7.idxmax()
            where.append({"experiment": 7, "max_cv_percent": round(float(big7.max()), 1), "dataset": ds_,
                          "algorithm": algo_, "K": int(k_)})
    (ANALYSIS_OUT / "variance_max.json").write_text(json.dumps(where, indent=1) + "\n")
    lines = table_head(
        r"Run-to-run variability of total runtime: CV = std/mean per configuration; trials per configuration"
        r" (3 by default, 10 for configurations under 10\,s and 15 under 1\,s); last column: configurations with mean runtime at least 5\,s.",
        r"\label{tab:variance}", "llrrrrr",
        r"Experiment & Quantity & Configs & Trials & Median CV (\%) & 95th pct.\ CV (\%) & Max CV (\%), runs $\geq$ 5\,s \\")
    # Rows in the manuscript's numbering, which is not the campaign's (paper_experiment_numbers.json).
    rows.sort(key=lambda r: int(r[0].split("~")[1]))
    for name, qty, n, tr, med, p95, mx in rows:
        mxs = f"{mx:.1f}" if np.isfinite(mx) else "--"
        lines.append(f"{name} & {qty} & {n} & {tr} & {med:.2f} & {p95:.2f} & {mxs} \\\\")
    lines += table_tail()
    emit("tab_variance.tex", "tab:variance", lines, frames)


def tab_datasets() -> None:
    stats_p = ANALYSIS_OUT / "dataset_stats.json"
    if not stats_p.exists():
        raise SystemExit("analysis_out/paper/dataset_stats.json missing: run analysis/dataset_stats.py first")
    stats = json.loads(stats_p.read_text())
    exp2 = next(e for e in cfg()["experiments"] if e["id"] == 2)
    sweeps = {r["csv_name"]: r["min_utils"] for r in exp2["runs"]}
    # Which threshold each fixed-threshold experiment runs at, per database, read from the
    # configuration. The anchor (bold) is the one of the five-batch experiment (campaign 1); an
    # experiment that runs a database at another threshold is named in a superscript on that value.
    # Not every experiment reuses the anchor (Experiments 3 and 4 run BIBLE and KOSARAK elsewhere),
    # and a table that marked only the anchor would tell the reader they all do. The exactness
    # experiment prints its own thresholds and is left out.
    exp1 = next(e for e in cfg()["experiments"] if e["id"] == 1)
    anchors = {r["csv_name"]: r["min_util"] for r in exp1["runs"]}
    exactness = paper_experiment(5)
    used: dict[str, dict[float, set[int]]] = {}
    for e in cfg()["experiments"]:
        if (any(r.get("thresholds") or r.get("min_utils") for r in e["runs"])
                or paper_experiment(e["id"]) in (exactness, None)):
            continue
        for r in e["runs"]:
            if r["csv_name"] in sweeps:
                used.setdefault(r["csv_name"], {}).setdefault(round(r["min_util"], 12), set()).add(paper_experiment(e["id"]))
    unmatched = [f"{d} {v}" for d, vs in used.items() for v in vs if not any(abs(v - s) < 1e-12 for s in sweeps[d])]
    unmatched += [d for d in anchors if d in sweeps and not any(abs(v - anchors[d]) < 1e-12 for v in sweeps[d])]
    if unmatched:
        raise SystemExit("tab_datasets: thresholds not in the sweep: " + ", ".join(unmatched))
    fixed_exps = sorted({x for vs in used.values() for s in vs.values() for x in s})
    # A database named in DS_ORDER but absent from the stats file used to be dropped in silence.
    # That is how this table stood at seven rows after an eighth database had been measured: the
    # stats file is a cached artifact and nothing re-ran it, so the row simply was not there and
    # the paper introduced a database its own data table did not list.
    missing = [d for d in DS_ORDER if d not in stats]
    if missing:
        raise SystemExit("tab_datasets: %s named in DS_ORDER but absent from %s. Run "
                         "analysis/dataset_stats.py; it reads the dataset files themselves, so a "
                         "database added since the last run is missing until it does."
                         % (", ".join(missing), stats_p.relative_to(ROOT)))
    order = sorted(DS_ORDER, key=lambda d: stats[d]["sequences"])
    lines = table_head(
        r"Statistical characteristics of the experimental datasets (measured from the files: sequences $|D|$,"
        r" distinct items $|I|$, mean itemsets per sequence, mean items per itemset, total utility) and the"
        r" per-dataset $minUtil$ sweep of Experiment~" + str(paper_experiment(2)) + r" (--: not in that experiment). An itemset of"
        r" one item admits no I-extension.",
        r"\label{tab:datasets}", "lrrrrrl",
        r"Dataset & $|D|$ & $|I|$ & Itemsets/seq.\ & Items/itemset & Total utility & $minUtil$ sweep (\%) \\",
        size=r"\scriptsize")
    for d in order:
        s = stats[d]
        # An empty cell reads as a rendering fault; "--" says the sweep was not measured, which
        # is what a database outside Experiment 2 means.
        def cell(v):
            s_ = f"{v*100:.3f}".rstrip("0").rstrip(".")
            if d in anchors and abs(v - anchors[d]) < 1e-12:
                return r"\textbf{" + s_ + "}"
            others = used.get(d, {}).get(round(v, 12))
            return s_ + (r"$^{" + ",".join(str(x) for x in sorted(others)) + "}$" if others else "")
        sw = ";\; ".join(cell(v) for v in sweeps.get(d, [])) or "--"
        per_iset = s["avg_items"] / s["avg_itemsets"] if s["avg_itemsets"] else 0.0
        lines.append(f"{ds_tex(d)} & {thousands(s['sequences'])} & {thousands(s['items'])} & {s['avg_itemsets']:.2f} & "
                     f"{per_iset:.2f} & {thousands(s['total_utility'])} & {sw} \\\\")
    lines += table_tail([r"\multicolumn{7}{@{}l}{Bold: the threshold of Experiments~" + exp_span(fixed_exps)
                         + r"; a superscript names the experiments that run the dataset at that threshold instead.} \\"])
    # datasets table has no CSV source; record the stats file and the config dump instead
    fake = pd.DataFrame(); fake.attrs["source"] = "analysis_out/paper/dataset_stats.json;analysis_out/paper/experiment_config.json"
    fake.attrs["run_ids"] = ["measured-from-files"]
    emit("tab_datasets.tex", "tab:datasets", lines, [fake])


def tab_exp1_eta_avg() -> None:
    df = data(1)
    ok = df[df["Status"].isin(OK)].copy()
    ok = ok[ok["RunIndex"] == ok.groupby(["Dataset", "Algorithm"])["RunIndex"].transform("min")]
    ok["eta"] = ok["CandUnified"] / ok["HAUSP"].replace(0, np.nan)
    m = ok.groupby(["Dataset", "Algorithm"])["eta"].mean().unstack("Algorithm")
    lines = table_head(
        r"Mean candidate-generation efficiency $\eta = $ lists assembled$/|\mathit{HAUSP}|$ over five batches"
        r" (the same operational count for every algorithm).",
        r"\label{tab:exp1_eta_avg}", "lrrr",
        r"Dataset & $\eta$ APEAU-I & $\eta$ APEAU-R & $\eta$ HAUSP-UB \\")

    for ds in DS_ORDER:
        if ds not in m.index:
            continue
        r_ = m.loc[ds]
        # Lower is better; the best cell is chosen on the unrounded mean, so two cells that print
        # alike are told apart, and cells that are exactly equal are both bold.
        arms = ["EHAUSM-I", "EHAUSM-R", PAPER_UB]
        vals = [float(r_.get(a, np.nan)) for a in arms]
        cells = bold_best(list(zip(vals, fmt_eta_row(vals))))
        lines.append(ds_tex(ds) + " & " + " & ".join(cells) + r" \\")
    lines += table_tail()
    emit("tab_exp1_eta_avg.tex", "tab:exp1_eta_avg", lines, [df])


def runtime_cell(g: pd.DataFrame, n_batches: int, scale: float, nd: int) -> tuple[float | None, str]:
    trials = complete_trials(g, n_batches)
    if trials:
        succ = g[g["Status"].isin(OK)]
        per = [succ[succ["RunIndex"] == r]["tTotal(ms)"].sum() / scale for r in trials]
        m = float(np.mean(per))
        nd1 = nd + (1 if abs(m) < 10 else 0) + (1 if abs(m) < 1 else 0)   # as ms_std, for the single-trial cell
        return m, (ms_std(per, nd=nd) if len(per) >= 3 else f"{per[0]:.{nd1}f}$^{{\\dagger}}$")
    fc = fail_cell(g)
    return None, (fc if fc else "--")


def tab_exp1_runtime() -> None:
    df = data(1)
    n_b = int(df.groupby(["Dataset", "Algorithm", "RunIndex"])["BatchID"].nunique().max())
    lines = table_head(r"Total runtime (s) over five batches (mean $\pm$ std over trials; OT@$b$: time limit exceeded at batch $b$).",
                       r"\label{tab:exp1_runtime}", "l" + "r" * len(ALGO4),
                       "Dataset & " + " & ".join(disp(a) for a in ALGO4) + r" \\")
    for ds in DS_ORDER:
        cells = [runtime_cell(df[(df["Dataset"] == ds) & (df["Algorithm"] == a)], n_b, 1000.0, 1) for a in ALGO4]
        lines.append(ds_tex(ds) + " & " + " & ".join(bold_best(cells)) + r" \\")
    lines += table_tail()
    emit("tab_exp1_runtime.tex", "tab:exp1_runtime", lines, [df])


def tab_exp2_pruned() -> None:
    df = data(2)
    variants = ["EHAUSM-I", "HAUSP-UB-L1", PAPER_UB_L1L3, PAPER_UB_L1L2, PAPER_UB]
    exp2 = next(e for e in cfg()["experiments"] if e["id"] == 2)
    n_sweep = {r["csv_name"]: len(r["min_utils"]) for r in exp2["runs"]}
    ok = df[df["Status"].isin(OK)]
    ok = ok[ok["RunIndex"] == ok.groupby(["Dataset", "Algorithm", "MinUtil"])["RunIndex"].transform("min")]
    lines = table_head(
        r"Ablation of the pruning layers, summed over the $minUtil$ sweep (counts are deterministic across trials):"
        r" candidates rejected by each layer, utility lists assembled (the same count for every algorithm), and"
        r" children recursed into. HAUSP-UB$^{L1}_{\mathrm{EUCS}}$ = Layer~1 only, and the only variant here that also keeps a co-occurrence pre-filter on item pairs, which makes its verdict conservative; HAUSP-UB$^{L1L3}$ = Layers 1+3; HAUSP-UB$^{L1L2}$ = Layers 1+2;"
        r" A superscript $k/n$ marks a variant that"
        r" completed only $k$ of the $n$ thresholds (the others exceeded the time limit); its sums cover those $k$"
        r" thresholds only. Compact units (K/M/B).",
        r"\label{tab:exp2_pruning}", "llrrrrr",
        r"Dataset & Variant & L1 (SWU) & L2 (decoupled) & L3 (APEAU) & Lists assembled & Expanded \\", size=r"\scriptsize")
    first_block = True
    for ds in DS_ORDER:
        block = []
        for a in variants:
            g = ok[(ok["Dataset"] == ds) & (ok["Algorithm"] == a)]
            if len(g) == 0:
                fc = fail_cell(df[(df["Dataset"] == ds) & (df["Algorithm"] == a)])
                if fc:
                    block.append(f" & {disp(a)} & \\multicolumn{{5}}{{l}}{{{fc} at every threshold}} \\\\")
                continue
            k, n = g["MinUtil"].nunique(), n_sweep.get(ds, g["MinUtil"].nunique())
            name = disp(a) + (f"$^{{{k}/{n}}}$" if k < n else "")
            l2, l3 = g["PrunedL2(IAUUB)"].sum(), g["PrunedL3(MFUUB)"].sum()
            if not str(a).startswith("HAUSP-UB"):
                # A baseline has no decoupled estimate: the counter it writes as PrunedL2(IAUUB) is the
                # coupled bound tested on node entry (EHAUSM_Inc, EHAUSM_Remining, Pre_HUSPM_adapt), so
                # it belongs under the coupled-bound column. They never write PrunedL3; refuse if one does.
                if l3 != 0:
                    raise SystemExit(f"tab_exp2_pruned: {a} on {ds} writes PrunedL3(MFUUB) = {l3}; the column "
                                     "mapping for baselines assumes it never does")
                l2, l3 = 0, l2
            block.append(f" & {name} & {human(g['PrunedL1(SWU)'].sum())} & {human(l2)} & "
                         f"{human(l3)} & {human(nsum(g['CandUnified']))} & {human(nsum(g['ExpandedUnified']))} \\\\")
        if not block:
            continue
        if not first_block:
            lines.append(r"\addlinespace")
        first_block = False
        block[0] = ds_tex(ds) + block[0]
        lines += block
    lines += table_tail()
    emit("tab_exp2_pruned.tex", "tab:exp2_pruning", lines, [df])


def tab_exp3_delta20() -> None:
    df = data(3)
    mem = load_memory(3)
    ok = df[df["Status"].isin(OK)]
    d20 = float(cfg()["exp3_deltas"][-1])
    b1 = ok[(ok["DeltaRatio"].round(3) == round(d20, 3)) & (ok["BatchID"] == 1)]
    # One definition of the update-memory cell, shared with the quantity export so the
    # number the prose quotes is the number this table prints.
    upd_mem = exp3_update_live_heap(d20)
    algos = ["EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM", PAPER_UB]
    lines = table_head(
        rf"Update processing at $\delta = {int(round(d20*100))}\%$ (batch~1): update time is mean $\pm$ std over trials;"
        r" update memory is the peak live heap of the update batch (used heap after a forced full collection, dedicated"
        r" memory run, one JVM per arm); update candidates are lists assembled (deterministic). Bold marks the best value"
        r" per dataset and metric; --: not measured.",
        r"\label{tab:exp3_delta20}", "llrrr",
        r"Dataset & Algorithm & Update $t$ (s) & Update mem.\ (MB) & Update cand. \\")
    first_block = True
    for ds in DS_ORDER:
        rows = []
        for a in algos:
            g = b1[(b1["Dataset"] == ds) & (b1["Algorithm"] == a)]
            if len(g) == 0:
                continue
            t = [g[g["RunIndex"] == r]["tTotal(ms)"].sum() / 1000 for r in sorted(g["RunIndex"].unique())]
            m = upd_mem.get((ds, a), [])
            mcell = (float(np.mean(m)), ms_std(m, nd=0)) if m else (None, "--")
            c = nsum(g[g["RunIndex"] == g["RunIndex"].min()]["CandUnified"])
            rows.append((a, (float(np.mean(t)), ms_std(t, nd=1)), mcell, (c, human(c))))
        if not rows:
            continue
        if not first_block:
            lines.append(r"\addlinespace")
        first_block = False
        tc = bold_best([r[1] for r in rows]); mc = bold_best([r[2] for r in rows]); cc = bold_best([r[3] for r in rows])
        for i, r in enumerate(rows):
            lines.append((ds_tex(ds) if i == 0 else "") + f" & {disp(r[0])} & {tc[i]} & {mc[i]} & {cc[i]} \\\\")
    lines += table_tail()
    emit("tab_exp3_delta20.tex", "tab:exp3_delta20", lines, [df, mem])


def tab_exp4_memory() -> None:
    """Peak live heap per arm from the dedicated memory runs (MemMode=live).

    The legacy used-heap column (results/exp4, sampled under lazy GC) is not
    used: it depends on the heap history of the JVM (see EXPERIMENT_CHANGELOG,
    2026-09-05). Failure cells come from the timing run of Exp 4 when the
    memory run has no row for the arm.
    """
    mem = load_memory(4)
    # Separate frame for the verdicts: an arm that timed out inside the memory campaign has no
    # heap value and is filtered out of `mem`, so without this its cell falls through to the
    # timing campaign -- which has no row at all for a database measured only for memory.
    mem_with_failures = load_memory(4, keep_failures=True)
    timing = data(4)
    n_b = int(timing.groupby(["Dataset", "Algorithm", "RunIndex"])["BatchID"].nunique().max())
    lines = table_head(
        r"Peak live heap (MB) over five update batches: used heap right after a forced full collection, sampled"
        r" every second and at the end of each batch, one JVM per arm (mean $\pm$ std over trials;"
        r" OT@$b$ / OOM@$b$: run stopped at batch $b$; --: not measured).",
        r"\label{tab:exp4_memory}", "l" + "r" * len(ALGO4),
        "Dataset & " + " & ".join(disp(a) for a in ALGO4) + r" \\")
    for ds in DS_ORDER:
        cells = []
        for a in ALGO4:
            g = mem[(mem["Dataset"] == ds) & (mem["Algorithm"] == a)] if mem is not None else pd.DataFrame()
            trials = complete_trials(g, n_b) if len(g) else []
            if trials:
                succ = g[g["Status"].isin(OK)]
                per = [succ[succ["RunIndex"] == r]["MemLive(MB)"].max() for r in trials]
                cells.append((float(np.mean(per)), ms_std(per, nd=0)))
            else:
                gf = (mem_with_failures[(mem_with_failures["Dataset"] == ds)
                                        & (mem_with_failures["Algorithm"] == a)]
                      if mem_with_failures is not None else pd.DataFrame())
                fc = fail_cell(gf) if len(gf) else (fail_cell(g) if len(g) else None)
                if fc is None:
                    fc = fail_cell(timing[(timing["Dataset"] == ds) & (timing["Algorithm"] == a)])
                cells.append((None, fc if fc else "--"))
        lines.append(ds_tex(ds) + " & " + " & ".join(bold_best(cells)) + r" \\")
    lines += table_tail()
    emit("tab_exp4_memory.tex", "tab:exp4_memory", lines, [mem, timing])


def tab_pool() -> None:
    """Shared-pool figures of the proposed algorithm over each whole run of the live-heap runs of Exp 4.

    The pool counters restart at every batch while pooled objects and single-item lists carry over,
    so every figure here is taken over the run (common.pool_per_run), not from one batch row: lists
    borrowed, fresh lists allocated, the share of borrows served by reuse, the single-item lists kept
    across batches, and the largest number of child lists alive at once (what Proposition live_lists
    bounds). Allocated lists equal the peak of all lists alive at once; a run where they differ, or
    trials that disagree, are refused rather than printed.
    """
    df = load_memory(4)
    lines = table_head(
        r"Behavior of the shared list pool of HAUSP-UB over the five update batches.",
        r"\label{tab:pool}", "lrrrrr",
        r"Dataset & Lists borrowed & Lists allocated & Reuse rate (\%) & Single-item lists & Peak child lists alive \\")
    if df is not None:
        runs = pool_per_run(df, PAPER_UB)
        if len(runs) and not runs["identity"].all():
            raise SystemExit("tab_pool: allocated lists differ from the peak of lists alive in "
                             + ", ".join(f"{r.Dataset}/trial {r.RunIndex}" for r in runs[~runs["identity"]].itertuples()))
        for ds in DS_ORDER:
            g = runs[runs["Dataset"] == ds]
            if not len(g):
                continue
            cols = ["borrowed", "allocated", "single_item", "peak_child"]
            if (g[cols].nunique() > 1).any():
                raise SystemExit(f"tab_pool: trials of {ds} disagree on a deterministic pool count")
            r_ = g.iloc[0]
            # Rounded down: a rate rounded up to 100.00 would read as "no list ever allocated" next
            # to a non-zero allocated count.
            rate = math.floor(10000.0 * (1.0 - r_["allocated"] / r_["borrowed"])) / 100.0
            lines.append(f"{ds_tex(ds)} & {human(r_['borrowed'])} & {thousands(r_['allocated'])} & {rate:.2f} & "
                         f"{thousands(r_['single_item'])} & {thousands(r_['peak_child'])} \\\\")
    lines += table_tail()
    emit("tab_pool.tex", "tab:pool", lines, [df])


def set_equality_covers(record: dict) -> tuple[bool, str]:
    """Whether the recorded pattern-set comparisons cover every (dataset, batch, threshold) that the
    pattern-writing experiments produce. Only experiments with enable_io write the pattern files
    the comparison reads, so when its count per dataset equals the number of distinct (batch,
    threshold) pairs those experiments produce, the pairs compared are exactly those pairs."""
    exps = {e["id"]: e for e in cfg()["experiments"]}
    writers = [i for i, e in exps.items() if e.get("enable_io")]
    for ds, v in record["per_dataset"].items():
        if ds == "example":
            continue
        pairs = set()
        for i in writers:
            run = next((r for r in exps[i]["runs"] if r["csv_name"] == ds), None)
            if run is None:
                continue
            mus = run.get("thresholds") or run.get("min_utils") or [run["min_util"]]
            batches = range(len(run.get("batch_ratios") or [1.0]))
            pairs |= {(b, round(float(m), 9)) for m in mus for b in batches}
        if len(pairs) != int(v["batches"]):
            return False, f"{ds}: {v['batches']} set comparisons recorded, {len(pairs)} (batch, threshold) pairs written"
    return True, ""


def tab_exactness() -> None:
    """Three settings, each with the number of configurations checked, the criterion and the result.

    One batch (Experiment 5's threshold sweep) and five consecutive batches (Experiment 6) are
    checked set by set: results-invariant/pattern_set_equality.json, written by
    verify_pattern_sets.py from the pattern files of the measurement machine, compares every
    pattern and its utility, and set_equality_covers() proves it covers every configuration of
    both. Every other completed run is checked by its pattern count (completed_configurations).
    A result other than full agreement is refused, never printed.
    """
    sys.path.insert(0, str(ROOT / "analysis"))
    from export_quantities import completed_configurations
    d5, d6 = data(5), data(6)
    ok5 = d5[d5["Status"].isin(OK)]
    n5 = int(ok5.groupby(["Dataset", "MinUtil"]).ngroups)
    d6 = d6[d6["Dataset"].astype(str).str.lower() != "example"]
    n6 = int(len(d6[d6["Status"].isin(OK)]))
    if (d6["Status"] != "SUCCESS_MATCH").any():
        raise SystemExit("tab_exactness: a five-batch configuration does not match the reference")
    rec_p = ROOT / "results-invariant" / "pattern_set_equality.json"
    rec = json.loads(rec_p.read_text(encoding="utf-8"))
    covered, why = set_equality_covers(rec)
    if int(rec["disagreements"]) != 0 or not covered:
        raise SystemExit(f"tab_exactness: set equality cannot be printed ({why or str(rec['disagreements']) + ' disagreements'})")
    cc = completed_configurations()
    if cc["disagreeing"] != 0:
        raise SystemExit(f"tab_exactness: {cc['disagreeing']} completed configuration(s) disagree on the pattern count")
    lines = table_head("", r"\label{tab:exactness}", "lrll",
                       r"Setting & Configurations & Criterion & Result \\")
    lines += [f"one batch & {thousands(n5)} & set equality per configuration & identical \\\\",
              f"five consecutive batches & {thousands(n6)} & set equality after every batch & identical \\\\",
              f"all other completed runs & {thousands(cc['agreeing'])} & equal pattern counts & equal \\\\"]
    lines += table_tail()
    emit("tab_exactness.tex", "tab:exactness", lines, [d5, d6])


def rebuild_ratios(frame: pd.DataFrame | None, k: int, warm: bool) -> dict:
    """Per dataset, the mean total runtime of the rebuild arm over that of the full algorithm, both
    from the same campaign (13 or 14) at k batches, when both completed every batch of every trial.
    The two arms report the same counts (audit check B20), so the ratio compares one search."""
    if frame is None or not len(frame):
        return {}
    from common import control_identities
    _, bad = control_identities(frame)
    if bad:
        raise SystemExit(f"tab_exp7_matrix: rebuild counts disagree with the full algorithm ({bad[0]})")
    ok = frame[frame["Status"].isin(OK)]
    out = {}
    for ds, g in ok.groupby("Dataset"):
        means = {}
        for a in ("HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]"):
            x = g[g["Algorithm"] == a]
            per = x.groupby("RunIndex").agg(t=("tTotal(ms)", "sum"), n=("BatchID", "nunique"))
            failed = frame[(frame["Dataset"] == ds) & (frame["Algorithm"] == a) & ~frame["Status"].isin(OK)]
            if len(per) and (per["n"] == k).all() and not len(failed):
                means[a] = per["t"].mean()
        if len(means) == 2:
            out[ds] = means["HAUSP-UB[rebuild+noEUCS]"] / means["HAUSP-UB[noEUCS]"]
    return out


def tab_exp7_matrix() -> None:
    df = data(7)
    d11 = data(11)
    # The smallest and the largest batch count (author, 2026-10-16), read from the configuration.
    ks = [int(k) for k in cfg()["exp7_batch_counts"]]
    ks = [min(ks), max(ks)]
    algos = [PAPER_UB, "EHAUSM-I", "Pre-HAUSPM"]
    df = df.copy()
    df["K"] = (1.0 / df["DeltaRatio"]).round().astype(int)
    warm_note = ""
    if d11 is not None and len(d11):
        d11 = d11.copy()
        # A trial of the warm-start sweep writes one batch count after another, each starting
        # again at BatchID 0, so (dataset, arm, trial) is not one run. Counting all its rows
        # merges the whole sweep into a single number -- with four batch counts measured it
        # reported K = 180. Segment on the restarts instead, and refuse if a segment's length
        # disagrees with its own highest batch index.
        d11 = d11.copy()
        d11["_sweep"] = d11.groupby(["Dataset", "Algorithm", "RunIndex"])["BatchID"].transform(
            lambda s: s.eq(0).cumsum())
        key = ["Dataset", "Algorithm", "RunIndex", "_sweep"]
        d11["K"] = d11.groupby(key)["BatchID"].transform("count")
        bad = d11[d11["K"] != d11.groupby(key)["BatchID"].transform("max") + 1]
        if len(bad):
            raise ValueError("experiment 11: %d row(s) where the batch count and the highest batch "
                             "index disagree, e.g. %s; the rows of a trial are not in file order"
                             % (len(bad), bad.iloc[0][["Dataset", "Algorithm", "RunIndex"]].to_dict()))
        first = float(cfg()["warm_start_first_ratio"]) * 100
        # Which batch counts the warm-start schedule was actually run at, read from the runs
        # rather than typed: the blank cells in those rows are the counts it was never run at,
        # and a blank sitting beside OT@0 and OOM@0 reads as a third kind of failure unless the
        # caption says otherwise.
        warm_ks = sorted({int(k) for k in d11["K"].unique()})
        at = ("$K{=}%d$ only" % warm_ks[0] if len(warm_ks) == 1
              else "$K \\in \\{%s\\}$" % ", ".join(str(k) for k in warm_ks))
        warm_note = (rf" Rows marked \emph{{warm20}} use the warm-start schedule: batch~0 holds {first:.0f}\% of the data"
                     r" and the remaining $K{-}1$ batches share the rest equally; every other row uses $K$ equal batches."
                     rf" That schedule was run at {at}.")
    blank_note = ""
    lines = table_head(
        r"Total runtime (min) for $K$ batches with the total data volume held fixed (mean $\pm$ std over trials;"
        r" $\dagger$: single trial under the uniform rule; OT@$b$ / OOM@$b$: limit exceeded at batch $b$)." + warm_note
        + "%BLANK%",
        r"\label{tab:exp7_matrix}", "ll" + "r" * len(ks),
        "Dataset & Algorithm & " + " & ".join(f"$K{{=}}{k}$" for k in ks) + r" \\")
    first_block = True

    def block_for(frame, ds, label):
        rows = {a: [] for a in algos}
        for k in ks:
            col = []
            for a in algos:
                g = frame[(frame["Dataset"] == ds) & (frame["Algorithm"] == a) & (frame["K"] == k)]
                col.append(runtime_cell(g, k, 60000.0, 1) if len(g) else (None, "--"))
            for a, c in zip(algos, bold_best(col)):
                rows[a].append(c)
        out = []
        for i, a in enumerate(algos):
            out.append((label if i == 0 else "") + f" & {disp(a)} & " + " & ".join(rows[a]) + r" \\")
        return out

    rebuild = {13: rebuild_ratios(data(13), ks[-1], warm=False), 14: rebuild_ratios(data(14), ks[-1], warm=True)}

    def ratio_row(camp, ds):
        r = rebuild[camp].get(ds)
        if r is None:
            return []
        return [r" & UB$_{\mathrm{rebuild}}$/HAUSP-UB$^{\S}$ & " + " & ".join(
            [f"{r:.2f}$\\times$" if k == ks[-1] else "--" for k in ks]) + r" \\"]

    for ds in DS_ORDER:
        if len(df[df["Dataset"] == ds]) == 0:
            continue
        if not first_block:
            lines.append(r"\addlinespace")
        first_block = False
        lines += block_for(df, ds, ds_tex(ds)) + ratio_row(13, ds)
        if d11 is not None and len(d11[d11["Dataset"] == ds]):
            lines.append(r"\addlinespace")
            lines += block_for(d11, ds, ds_tex(ds) + r" (\emph{warm20})") + ratio_row(14, ds)
    extra = []
    if any(rebuild.values()):
        extra = [r"\multicolumn{" + str(2 + len(ks)) + r"}{@{}l}{$^{\S}$Ratio of total runtimes measured together in a"
                 r" separate session.} \\"]
    lines += table_tail(extra)
    # The legend for "--" is printed only when a cell actually carries one: a key for a symbol
    # the table does not use sends a reader looking for something that is not there.
    blank = any(re.search(r"(?<![-\w$])--(?![-\w])", ln) for ln in lines)
    lines = [ln.replace("%BLANK%", " --: not measured." if blank else "") for ln in lines]
    emit("tab_exp7_matrix.tex", "tab:exp7_matrix", lines, [df, d11])


def tab_exp8_eta() -> None:
    """One row per dataset, its thresholds across in three groups (minUtil, |HAUSP|, eta).

    A dataset's trend is then read along one line, which is how the experiment is discussed; the
    thresholds differ per dataset, so each group repeats them position by position (highest
    first) and a dataset with fewer thresholds leaves its last cells empty.
    """
    df = data(8)
    ok = df[df["Status"].isin(OK)].copy()
    ok = ok[ok["RunIndex"] == ok.groupby(["Dataset", "Algorithm", "MinUtil"])["RunIndex"].transform("min")]
    ok["eta"] = ok["CandUnified"] / ok["HAUSP"].replace(0, np.nan)
    width = int(ok.groupby("Dataset")["MinUtil"].nunique().max()) if len(ok) else 1
    groups = [r"$minUtil$ (\%)", r"$|\mathit{HAUSP}|$", r"$\eta$"]
    lines = table_head(
        r"Candidate-generation efficiency $\eta$ at low thresholds.",
        r"\label{tab:exp8_eta}", "l" + "r" * (3 * width),
        "Dataset & " + " & ".join(rf"\multicolumn{{{width}}}{{c}}{{{g}}}" for g in groups) + r" \\",
        size=r"\footnotesize")
    lines.insert(lines.index(r"\begin{tabular}{" + "l" + "r" * (3 * width) + "}"), r"\setlength{\tabcolsep}{1.5pt}")
    # The column groups are separated by partial rules instead of a full rule under the header.
    lines[-1] = "".join(rf"\cmidrule(lr){{{2 + g * width}-{1 + (g + 1) * width}}}" for g in range(3))
    for ds in DS_ORDER:
        sub = ok[ok["Dataset"] == ds]
        if len(sub) == 0:
            continue
        mus, hs, etas = [], [], []
        for mu in sorted(sub["MinUtil"].unique(), reverse=True):
            r = sub[sub["MinUtil"] == mu]
            # One value per threshold, and it carries no algorithm name on purpose. Every batch
            # here is a first batch (BatchID 0, DeltaRatio 1.0), so the persistent-tree baseline
            # has no delta to append and assembles every list afresh: the two arms record the same
            # Cand and the same |HAUSP| at every threshold (audit check B19). Printing one arm's
            # number under both names was printing one measurement twice.
            e_i = float(r[r["Algorithm"] == "EHAUSM-I"]["eta"].mean())
            ru = r[r["Algorithm"] == PAPER_UB]
            e_u = float(ru["eta"].mean()) if len(ru) else np.nan
            if np.isfinite(e_i) and np.isfinite(e_u) and abs(e_i - e_u) > 1e-9 * max(1.0, abs(e_u)):
                raise RuntimeError(
                    "exp8 eta differs between the arms at %s minUtil=%s (%r vs %r); the single "
                    "value is only sound while they agree" % (ds, mu, e_i, e_u))
            mus.append(f"{mu*100:.3f}".rstrip("0").rstrip("."))
            hs.append(human(int(r["HAUSP"].iloc[0])))
            etas.append(fmt_eta(e_u))
        pad = [""] * (width - len(mus))
        lines.append(ds_tex(ds) + " & " + " & ".join(mus + pad + hs + pad + etas + pad) + r" \\")
    lines += table_tail()
    emit("tab_exp8_eta.tex", "tab:exp8_eta", lines, [df])


EXP9_ARMS = [("EHAUSM-I", "persistent tree, coupled bound at node, fresh lists (reference)"),
             ("EHAUSM-R", "no retention: re-mine each batch"),
             ("HAUSP-UB[noL2+L3@node+nopool+noEUCS]", "flat arrays, item-level SWU test, coupled bound at node"),
             ("HAUSP-UB[noL2+nopool+noEUCS]", "coupled bound tested on the child before recursion"),
             ("HAUSP-UB[noL2+noEUCS]", "shared list pool"),
             ("HAUSP-UB[noEUCS]", "decoupled estimate during assembly (Layer~2); the algorithm of the paper")]
# Attribution chain since 2026-09-10 (author decision): no EUCS pre-filter at any step. The earlier
# chain with EUCS arms lives in results-2026-09/exp9 and EXPERIMENT_CHANGELOG (2026-09-07..09).
EXP9_SHORT = {"EHAUSM-I": "APEAU-I", "EHAUSM-R": "APEAU-R",
              "HAUSP-UB[noL2+L3@node+nopool+noEUCS]": r"UB$_{\mathrm{layout}}$",
              "HAUSP-UB[noL2+nopool+noEUCS]": r"UB$_{\mathrm{+child}}$",
              "HAUSP-UB[noL2+noEUCS]": r"UB$_{\mathrm{+pool}}$",
              "HAUSP-UB[noEUCS]": r"UB$_{\mathrm{+L2}}$ (HAUSP-UB)"}


#: Short names of the campaign-12 arms, which are read only against each other.
EXP12_SHORT = {"HAUSP-UB[noL2+noEUCS]": r"UB$_{\mathrm{+pool}}$", "HAUSP-UB[noEUCS]": r"UB$_{\mathrm{+L2}}$ (HAUSP-UB)",
               "HAUSP-UB[rebuild+noEUCS]": r"UB$_{\mathrm{rebuild}}$", "HAUSP-UB[eagerC+noEUCS]": r"UB$_{\mathrm{eagerC}}$"}


def time_cells(frame: pd.DataFrame, arms: list[str], value: str = "tTotal(ms)") -> dict:
    """Per dataset, (mean seconds, 'mean +- std') of each arm over trials, summed over batches."""
    ok = frame[frame["Status"].isin(OK)]
    per = ok.groupby(["Dataset", "Algorithm", "RunIndex"])[value].sum().div(1000)
    m = per.groupby(["Dataset", "Algorithm"]).mean()
    return {ds: [(float(m.loc[(ds, a)]), ms_std(per.loc[(ds, a)].tolist(), nd=1)) if (ds, a) in m.index
                 else (None, "--") for a in arms] for ds in DS_ORDER}


def exp12_block(df12: pd.DataFrame) -> list[str]:
    """Rows of the campaign-12 arms, measured in their own session: total time of each arm (bold:
    fastest of the four), and the preprocessing time (scan and build) of the full and rebuild arms.
    Their counts are those of the full algorithm (audit check B20), so none is printed."""
    from common import control_identities
    n, bad = control_identities(df12)
    if bad:
        raise SystemExit(f"tab_exp9_attribution: campaign 12 counts disagree ({bad[0]}); not printed")
    arms = [a for a in EXP12_SHORT if a in set(df12["Algorithm"])]
    tot = time_cells(df12, arms)
    bolded = {ds: bold_best(tot[ds]) for ds in DS_ORDER}
    scan = time_cells(df12, arms, "tScan(ms)")
    out = [r"\midrule", r"\multicolumn{" + str(2 + len(DS_ORDER)) + r"}{@{}l}{Measured in a separate session:} \\"]
    for i, a in enumerate(arms):
        out.append(EXP12_SHORT[a] + r" & $t$ (s) & " + " & ".join(bolded[ds][i] for ds in DS_ORDER) + r" \\")
        if "rebuild" in a or a == PAPER_UB:
            out.append(r" & scan (s) & " + " & ".join(scan[ds][i][1] for ds in DS_ORDER) + r" \\")
    return out


def tab_exp9_attribution() -> None:
    """Runtime per attribution arm and dataset, then the two counts behind it, each printed once.

    Every arm from APEAU-R onwards assembles the same lists, and every arm, APEAU-I included,
    expands the same nodes; both are checked here before a single row stands for several arms.
    The control arms of campaign 12 follow in their own block, read only against each other.
    """
    df = data(9)
    arms = [a for a, _ in EXP9_ARMS]
    lines = table_head("", r"\label{tab:attribution}", "@{}ll" + "r" * len(DS_ORDER) + "@{}",
                       "Arm & Quantity & " + " & ".join(ds_tex(d) for d in DS_ORDER) + r" \\",
                       size=r"\tiny\setlength{\tabcolsep}{3pt}")
    if df is None:
        raise SystemExit("tab_exp9_attribution: no campaign-9 rows")
    tot = time_cells(df, arms)
    bolded = {ds: bold_best(tot[ds]) for ds in DS_ORDER}
    for i, a in enumerate(arms):
        lines.append(EXP9_SHORT[a] + r" & $t$ (s) & " + " & ".join(bolded[ds][i] for ds in DS_ORDER) + r" \\")
    ok = df[df["Status"].isin(OK)]
    first = ok[ok["RunIndex"] == ok.groupby(["Dataset", "Algorithm"])["RunIndex"].transform("min")]
    agg = first.groupby(["Dataset", "Algorithm"])[["CandUnified", "ExpandedUnified"]].sum()
    shared = arms[arms.index("EHAUSM-R"):]
    for ds in DS_ORDER:
        lists = {agg.loc[(ds, a), "CandUnified"] for a in shared if (ds, a) in agg.index}
        expanded = {agg.loc[(ds, a), "ExpandedUnified"] for a in arms if (ds, a) in agg.index}
        if len(lists) != 1 or len(expanded) != 1:
            raise SystemExit(f"tab_exp9_attribution: on {ds} the arms from APEAU-R onwards assemble {len(lists)} "
                             f"different list counts and the arms expand {len(expanded)} different node counts; "
                             "a single row cannot stand for them")
    cell = lambda a, col, ds: human(agg.loc[(ds, a), col]) if (ds, a) in agg.index else "--"
    lines += [r"\midrule",
              r"APEAU-I & lists & " + " & ".join(cell("EHAUSM-I", "CandUnified", ds) for ds in DS_ORDER) + r" \\",
              r"APEAU-R onwards & lists & " + " & ".join(cell("EHAUSM-R", "CandUnified", ds) for ds in DS_ORDER) + r" \\",
              r"every arm & expanded & " + " & ".join(cell("EHAUSM-R", "ExpandedUnified", ds) for ds in DS_ORDER) + r" \\"]
    d12 = data(12)
    if d12 is not None and len(d12):
        lines += exp12_block(d12)
    note = (r"\multicolumn{" + str(2 + len(DS_ORDER)) + r"}{@{}l}{Every arm from APEAU-R onwards assembles the same lists,"
            r" and every arm expands the same nodes.} \\")
    lines += table_tail([note])
    emit("tab_exp9_attribution.tex", "tab:attribution", lines, [df, d12])


def prehauspm_rescans() -> tuple[int, int, int, list[int]]:
    """(rescans, completed batches, batches that added no sequence, manuscript experiments) for
    Pre-HAUSPM over every timing and live-heap log.

    A batch without a rescan is allowed only when it added no sequence (its cumulative size equals
    that of the batch before it in the same run); any other one is refused, since the manuscript
    states that Pre-HAUSPM rescans at every batch that adds sequences.
    """
    resc = total = empty = 0
    exps, bad = set(), []
    for camp in sorted(int(k) for k in RESULT_FILE):
        for frame in (data(camp), load_memory(camp)):
            if frame is None or "RescanTriggered" not in frame.columns:
                continue
            p = frame[(frame["Algorithm"] == "Pre-HAUSPM") & frame["Status"].isin(OK)]
            if not len(p):
                continue
            if paper_experiment(camp) is not None:
                exps.add(paper_experiment(camp))
            keys = [c for c in ("Dataset", "MinUtil", "mu", "Schedule", "RunIndex", "MemMode") if c in p.columns]
            prev: dict = {}
            for _, r in p.iterrows():                 # a run writes its batches in order
                k = tuple(r[c] for c in keys)
                b, size = int(r["BatchID"]), r["CumulativeDBSize"]
                grew = not (b > 0 and prev.get(k, (None, None))[0] == b - 1 and prev[k][1] == size)
                prev[k] = (b, size)
                total += 1
                if int(r["RescanTriggered"]) == 1:
                    resc += 1
                elif grew:
                    bad.append(f"exp{camp} {r['Dataset']} batch {b}")
                else:
                    empty += 1
    if bad:
        raise SystemExit("Pre-HAUSPM skipped a rescan on a batch that added sequences: " + ", ".join(bad[:5]))
    return resc, total, empty, sorted(exps)


def tab_exp10_mu() -> None:
    df = data(10)
    mus = [float(m) for m in cfg()["mu_sweep"]]
    d20 = float(cfg()["exp3_deltas"][-1])
    lines = table_head(
        rf"Pre-HAUSPM under the safety margin $\mu \in \{{{', '.join(f'{m:.2f}' for m in mus)}\}}$ at $\delta = {int(round(d20*100))}\%$"
        r" (Experiment~" + str(paper_experiment(3)) + r" setting): update time in seconds (mean $\pm$ std over trials); \emph{rescan}: whether the update batch"
        r" triggered a rescan for every $\mu$; last column: smallest ratio over $\mu$ of the tested buffer to the safety value"
        r" $f$ (a ratio above 1 means the bound was violated).",
        r"\label{tab:exp10_mu}", "l" + "r" * len(mus) + "cr",
        "Dataset & " + " & ".join(rf"$\mu{{=}}{m:.2f}$" for m in mus) + r" & Rescan & $\min_\mu$ tested$/f$ \\")
    for ds in DS_ORDER:
        cells = []
        resc = []
        ratios = []
        for m in mus:
            g = df[(df["Dataset"] == ds) & (df["mu"].round(3) == round(m, 3)) & (df["BatchID"] == 1)] if df is not None else pd.DataFrame()
            ok = g[g["Status"].isin(OK)] if len(g) else g
            if len(ok):
                t = [ok[ok["RunIndex"] == r]["tTotal(ms)"].sum() / 1000 for r in sorted(ok["RunIndex"].unique())]
                cells.append(ms_std(t, nd=1))
                resc.append(bool((ok["RescanTriggered"] == 1).all()))
                fvals = ok["SafetyBound"].astype(float)
                tested = ok["BufferTested"].astype(float)
                ratios.append(float((tested / fvals.replace(0, np.nan)).min()))
            else:
                fc = fail_cell(g) if len(g) else None
                cells.append(fc if fc else "--")
        if not cells or all(c == "--" for c in cells):
            lines.append(ds_tex(ds) + " & " + " & ".join(cells or ["--"] * len(mus)) + r" & -- & -- \\")
            continue
        rs = "yes" if resc and all(resc) else ("no" if resc else "--")
        rt = fmt_sig(min(ratios), 3) if ratios and np.isfinite(min(ratios)) else "--"
        lines.append(ds_tex(ds) + " & " + " & ".join(cells) + f" & {rs} & {rt} \\\\")
    # The manuscript says Pre-HAUSPM rescans at every batch that adds sequences, in every run; this
    # table is where Pre-HAUSPM's rescan rule is studied, so the count over all runs is printed here.
    resc, total, empty, exps = prehauspm_rescans()
    note = (rf"All completed runs of Experiments~{exp_span(exps)}: Pre-HAUSPM rescans at {thousands(resc)} of {thousands(total)}"
            rf" batches; the other {empty} add no sequence.")
    lines += table_tail([r"\multicolumn{" + str(len(mus) + 3) + r"}{@{}l}{\footnotesize " + note + r"} \\"])
    emit("tab_exp10_mu.tex", "tab:exp10_mu", lines, [df])


def prose_numbers() -> None:
    """Derived numbers quoted in prose (handbook 3.4.8b), with their sources, as JSON."""
    out = {}
    d1 = data(1)
    hu = d1[(d1["Algorithm"] == PAPER_UB) & d1["Status"].isin(OK)]
    if len(hu):
        out["exp1_pool"] = {"PoolBorrows_min": int(hu.groupby(["Dataset", "RunIndex"])["PoolBorrows"].sum().min()),
                            "PoolBorrows_max": int(hu.groupby(["Dataset", "RunIndex"])["PoolBorrows"].sum().max()),
                            "PoolPeakLive_max": int(hu["PoolPeakLive"].max()),
                            "reuse_share": float(hu["PoolReuses"].sum() / max(1, hu["PoolBorrows"].sum())),
                            "source": d1.attrs.get("source")}
    else:
        out["exp1_pool"] = {"note": f"no rows for {PAPER_UB} in Exp 1 yet"}
    d2 = data(2)
    ok2 = d2[d2["Status"].isin(OK) & (d2["RunIndex"] == 0)]
    out["exp2_bible"] = {a: {"lists_assembled": (lambda v: None if not np.isfinite(v) else int(v))(nsum(ok2[(ok2["Dataset"] == "BIBLE") & (ok2["Algorithm"] == a)]["CandUnified"])),
                             "recursed": (lambda v: None if not np.isfinite(v) else int(v))(nsum(ok2[(ok2["Dataset"] == "BIBLE") & (ok2["Algorithm"] == a)]["RecursedUnified"]))}
                        for a in ["EHAUSM-I", "HAUSP-UB-L1", PAPER_UB_L1L3, PAPER_UB_L1L2, PAPER_UB]
                        if len(ok2[(ok2["Dataset"] == "BIBLE") & (ok2["Algorithm"] == a)])}
    out["exp2_bible"]["source"] = d2.attrs.get("source")
    exact = 0
    for e in (1, 3, 4, 7):
        d = data(e)
        ok = d[d["Status"].isin(OK) & (d["RunIndex"] == 0)]
        exact += int(ok.groupby(["Dataset", "BatchID", "MinUtil", "DeltaRatio"]).ngroups)
    out["completed_configs_exp1347_trial0"] = exact
    for e, key in ((7, "exp7_fifa_k100_live_peak_mb"), (11, "exp11_live_peak_mb")):
        mem = load_memory(e)
        if mem is not None:
            ok = mem[mem["Status"].isin(OK)]
            out[key] = {f"{ds}|{a}": float(g["MemLive(MB)"].max())
                        for (ds, a), g in ok.groupby(["Dataset", "Algorithm"])}
            out[key]["source"] = mem.attrs.get("source")
    p = ANALYSIS_OUT / "prose_numbers.json"
    p.write_text(json.dumps(out, indent=1))
    print(f"  [json]  {p.relative_to(ROOT)}")


def main() -> int:
    for fn in (tab_variance, tab_datasets, tab_exp1_eta_avg, tab_exp1_runtime,
               tab_exp2_pruned, tab_exp3_delta20, tab_exp4_memory, tab_pool, tab_exactness, tab_exp7_matrix,
               tab_exp8_eta, tab_exp9_attribution, tab_exp10_mu, prose_numbers):
        fn()
    MANIFEST.write_text(json.dumps(manifest, indent=1))
    print(f"  [json]  {MANIFEST.relative_to(ROOT)}  ({len(manifest)} tables)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
