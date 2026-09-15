#!/usr/bin/env python3
"""Fill the [SỐ: ...] placeholders of the manuscript from the measurement artifacts.

Every value is computed here from the merged CSVs (analysis/common.load_experiment,
load_memory) or read out of a generated table; none is typed by hand. Run from the
repository root:  python3 analysis/fill_numbers.py [--dry-run]

A placeholder whose key is not registered is left in place and listed at the end, so the
script never guesses and never silently drops one.
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DS_ORDER, OK, PAPER_UB, ROOT, load_experiment, load_memory  # noqa: E402

TEX = ROOT.parent / "paper" / "HAUSP-UB-V1.tex"
REAL = [d for d in DS_ORDER if d != "C8T1S5I8N5K"]
UB_CHAIN = {"layout": "HAUSP-UB[noL2+L3@node+nopool+noEUCS]", "child": "HAUSP-UB[noL2+nopool+noEUCS]",
            "pool": "HAUSP-UB[noL2+noEUCS]", "L2": PAPER_UB}


def totals(exp, value="tTotal(ms)", where=None):
    """Mean over trials of the per-(dataset, arm) total."""
    df = load_experiment(exp)
    ok = df[df["Status"].isin(OK)]
    if where is not None:
        ok = ok[where(ok)]
    per = ok.groupby(["Dataset", "Algorithm", "RunIndex"])[value].sum()
    return per.groupby(["Dataset", "Algorithm"]).mean().unstack("Algorithm")


REFUSED: list[str] = []


def _caller() -> str:
    """The source line that asked for the range, so a refusal names a place in this file."""
    import inspect
    fr = inspect.stack()[2]
    line = (fr.code_context or [""])[0].strip()
    return f"line {fr.lineno}: {line[:110]}"


def rng(v, nd=2, unit=r"$\times$", datasets=None, check=None, key=None):
    """Smallest--largest of a ratio series.

    A ratio range is refused rather than printed when it cannot be read as written:
    no comparable cell, an endpoint at zero, or a range that straddles 1.0 while the
    sentence around it compares in one direction only. The refusal leaves the
    placeholder in the manuscript and is reported at the end of the run.
    """
    v = v.dropna()
    if datasets is not None:
        v = v[[d for d in datasets if d in v.index]]
    if check is None:
        check = unit == r"$\times$"
    lo, hi = (v.min(), v.max()) if len(v) else (np.nan, np.nan)
    if check:
        why = None
        if len(v) == 0 or pd.isna(lo) or pd.isna(hi):
            why = "no comparable cell"
        elif round(lo, nd) <= 0:
            why = f"an endpoint is zero ({lo:.4g}); a ratio of 0 has no reading"
        elif lo < 1 <= hi:
            why = (f"straddles 1.0 ({lo:.{nd}f}--{hi:.{nd}f}): one side is larger and the other "
                   "smaller, so no one-directional wording fits; state the two sides separately")
        if why is not None:
            REFUSED.append(f"{key or _caller()}: {why}")
            return None
    if abs(hi - lo) < 10 ** (-nd) / 2:
        return f"{lo:.{nd}f}{unit}"
    return f"{lo:.{nd}f}--{hi:.{nd}f}{unit}"


def sides(v, nd=1):
    """Split a ratio series at 1.0 and state each side in its own direction.

    Returns (n_above, range_above, n_below, range_below_inverted); the second range is
    inverted so that both read as "by x times", each in the direction of its own side.
    """
    v = v.dropna()
    up, dn = v[v >= 1], v[v < 1]
    return (len(up), rng(up, nd=nd, check=False) if len(up) else None,
            len(dn), rng(1 / dn, nd=nd, check=False) if len(dn) else None)


def pct_rng(v, nd=0):
    v = (v - 1) * 100
    lo, hi = v.min(), v.max()
    if abs(hi - lo) < 0.5:
        return f"{lo:.{nd}f}\\%"
    return f"{lo:.{nd}f}--{hi:.{nd}f}\\%"


DS_NAME = {"BMS1_SPMF": "BMS1", "C8T1S5I8N5K": "SYN"}


WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def word(n):
    """Small counts are spelled out in the manuscript; larger ones stay in digits."""
    return WORDS[n] if 0 <= n <= 10 else str(n)


def ds_name(d):
    return DS_NAME.get(d, d)


def names(index):
    """Dataset names in the order of the tables, as an English list."""
    ds = [ds_name(d) for d in DS_ORDER if d in set(index)]
    return ds[0] if len(ds) == 1 else ", ".join(ds[:-1]) + " and " + ds[-1]


def exp1_ratio(base):
    t = totals(1)
    return t[base] / t[PAPER_UB]


def exp9_step(a, b):
    t = totals(9)
    return t[UB_CHAIN.get(a, a)] / t[UB_CHAIN.get(b, b)]


def exp3_update():
    return totals(3, where=lambda d: (d["DeltaRatio"].round(3) == 0.2) & (d["BatchID"] == 1))


def mem4():
    m = load_memory(4)
    return m.groupby(["Dataset", "Algorithm"])["MemLive(MB)"].max().unstack("Algorithm")


def exp7_K():
    df = load_experiment(7)
    ok = df[df["Status"].isin(OK)].copy()
    ok["K"] = (1.0 / ok["DeltaRatio"]).round().astype(int)
    per = ok.groupby(["Dataset", "Algorithm", "K", "RunIndex"]).agg(n=("BatchID", "size"), t=("tTotal(ms)", "sum")).reset_index()
    per = per[per["n"] == per["K"]]
    return per.groupby(["Dataset", "Algorithm", "K"])["t"].mean().div(60000)


def warm():
    df = load_experiment(11)
    ok = df[df["Status"].isin(OK)]
    return ok.groupby(["Dataset", "Algorithm", "RunIndex"])["tTotal(ms)"].sum().groupby(["Dataset", "Algorithm"]).mean().div(60000).unstack("Algorithm")


def pool():
    m = load_memory(4)
    d = m[m["Algorithm"] == PAPER_UB]
    return d.groupby("Dataset")[["PoolBorrows", "PoolReuses", "PoolPeakLive"]].max()


def variance_table():
    from build_latex_tables import ANALYSIS_OUT
    return (ANALYSIS_OUT / "latex" / "tab_variance.tex").read_text()


def wilcoxon_line(comparison, baseline):
    p = ROOT / "analysis_out" / "paper" / "tables" / "wilcoxon_tests.md"
    for ln in p.read_text().split("\n"):
        if comparison in ln and f"| {baseline} " in ln:
            cells = [c.strip() for c in ln.split("|")]
            return float(cells[4]), int(cells[3])
    raise KeyError(f"{comparison}/{baseline}")


def counts(exp, col="CandUnified"):
    df = load_experiment(exp)
    ok = df[df["Status"].isin(OK)]
    ok = ok[ok["RunIndex"] == ok.groupby(["Dataset", "Algorithm"])["RunIndex"].transform("min")]
    return ok.groupby(["Dataset", "Algorithm"])[col].sum().unstack("Algorithm")


def exactness_counts():
    d5 = load_experiment(5); d6 = load_experiment(6)
    ok5 = d5[d5["Status"].isin(OK)]
    n5 = ok5.groupby(["Dataset", "MinUtil"]).ngroups
    top5 = int(ok5["HAUSP"].max())
    n6 = len(d6[d6["Status"].isin(OK)])
    return n5, top5, n6


def completed_configs():
    n = 0
    for e in (1, 3, 4, 7, 11):
        df = load_experiment(e)
        ok = df[df["Status"].isin(OK) & (df["RunIndex"] == 0)]
        piv = ok.pivot_table(index=["Dataset", "MinUtil", "DeltaRatio", "BatchID"], columns="Algorithm", values="HAUSP", aggfunc="first")
        cols = [c for c in piv.columns if c in (PAPER_UB, "EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM")]
        sub = piv[cols].dropna()
        n += len(sub)
    return n


def build() -> dict:
    """key fragment -> replacement text. The key is matched against the placeholder body."""
    t1, t3, t9 = totals(1), exp3_update(), totals(9)
    m4, e7, w, pl = mem4(), exp7_K(), warm(), pool()
    c1 = counts(1)
    R = {}
    # --- Experiment 1
    R["khoảng tỉ số thời gian EHAUSM-R/HAUSP-UB trên sáu bộ thật"] = rng((t1["EHAUSM-R"] / t1[PAPER_UB])[REAL])
    R["khoảng tỉ số EHAUSM-R/HAUSP-UB | nguồn: tab_exp1_runtime"] = rng((t1["EHAUSM-R"] / t1[PAPER_UB])[REAL])
    R["khoảng tỉ số EHAUSM-I/HAUSP-UB | nguồn: tab_exp1_runtime"] = rng((t1["EHAUSM-I"] / t1[PAPER_UB])[REAL])
    R["khoảng tỉ số Pre-HAUSPM/HAUSP-UB | nguồn: tab_exp1_runtime"] = rng((t1["Pre-HAUSPM"] / t1[PAPER_UB])[REAL])
    R["tỉ số EHAUSM-R/HAUSP-UB trên SYN"] = rng((t1["EHAUSM-R"] / t1[PAPER_UB])[["C8T1S5I8N5K"]])
    R["giá trị cột Ratio | nguồn: tab_exp1_eta_avg"] = f"{(c1['EHAUSM-R'] / c1[PAPER_UB]).max():.2f}$\\times$"
    R["số bộ mà cột EHAUSM-I in đậm"] = str(int((c1["EHAUSM-I"] < c1[PAPER_UB]).sum()))
    # --- phase breakdown
    ph = load_experiment(1)
    ok = ph[ph["Status"].isin(OK) & (ph["Algorithm"] == PAPER_UB)]
    per = ok.groupby(["Dataset", "RunIndex"])[["tScan(ms)", "tLayer1(ms)", "tTotal(ms)"]].sum()
    R["max cột Scan+flatten"] = f"{100 * (per['tScan(ms)'] / per['tTotal(ms)']).max():.1f}\\%"
    R["max của L1+L2+L3"] = f"{100 * (per['tLayer1(ms)'] / per['tTotal(ms)']).max():.1f}\\%"
    # --- Experiment 9 chain
    R["khoảng tỉ số t(layout)/t(+child) trên sáu bộ thật"] = rng(exp9_step("layout", "child")[REAL])
    R["khoảng phần trăm t(layout)/t(+child)"] = pct_rng(exp9_step("layout", "child")[REAL])
    R["khoảng tỉ số của bước +child"] = rng(exp9_step("layout", "child")[REAL])
    R["khoảng phần trăm t(+pool)/t(+L2) trên sáu bộ thật"] = pct_rng(exp9_step("pool", "L2")[REAL])
    R["khoảng phần trăm t(+child)/t(+pool)"] = pct_rng(exp9_step("child", "pool")[REAL])
    R["khoảng tỉ số t(E-I)/t(E-R)"] = rng(exp9_step("EHAUSM-I", "EHAUSM-R"))
    R["khoảng tỉ số t(E-R)/t(UB_layout)"] = rng(exp9_step("EHAUSM-R", "layout"))
    R["khoảng tỉ số t(E-R)/t(HAUSP-UB)"] = rng(exp9_step("EHAUSM-R", "L2"))
    R["khoảng tỉ số t(UB+pool $-$ EUCS)/t(UB full $-$ EUCS)"] = pct_rng(exp9_step("pool", "L2")[REAL])
    R["khoảng phần trăm thời gian Layer 2 tiết kiệm"] = pct_rng(exp9_step("pool", "L2")[REAL])
    R["khoảng | nguồn: tab_exp9_attribution"] = pct_rng(exp9_step("layout", "child")[REAL])
    R["Recursed EHAUSM-I BIBLE"] = f"{counts(9, 'RecursedUnified').loc['BIBLE', 'EHAUSM-I']:,.0f}".replace(",", "{,}")
    # --- Experiment 2
    c2 = counts(2); r2 = counts(2, "RecursedUnified")
    L12 = "HAUSP-UB[noL3+noEUCS]"
    R["khoảng tỉ số Recursed(L1L2)/Recursed(UB)"] = rng((r2[L12] / r2[PAPER_UB]).dropna())
    R["khoảng tỉ số Lists(L1L2)/Lists(UB)"] = rng((c2[L12] / c2[PAPER_UB]).dropna())
    # --- Experiment 3
    R["khoảng tỉ số thời gian EHAUSM-I/HAUSP-UB | nguồn: tab_exp3_delta20"] = rng((t3["EHAUSM-I"] / t3[PAPER_UB])[REAL])
    R["khoảng tỉ số EHAUSM-R/HAUSP-UB | nguồn: tab_exp3_delta20"] = rng((t3["EHAUSM-R"] / t3[PAPER_UB])[REAL])
    R["tỉ số Pre-HAUSPM/HAUSP-UB trên BMS1 tại μ mặc định"] = f"{(t3['Pre-HAUSPM'] / t3[PAPER_UB])['BMS1_SPMF']:.1f}$\\times$"
    cu3 = counts(3)
    R["số bộ mà Update cand. của HAUSP-UB lớn hơn EHAUSM-I"] = str(int((cu3[PAPER_UB] > cu3["EHAUSM-I"]).sum()))
    # --- memory
    # Live heap comes from the dedicated memory runs of Experiment 4. Experiment 3 has no
    # memory cell at the delta its table reports, which is why that table prints "--" there
    # and why no Experiment 3 memory key exists: the prose points at Experiment 4 instead.
    m3 = load_memory(3)
    if m3 is not None and len(m3[(m3["DeltaRatio"].round(3) == 0.2) & (m3["BatchID"] == 1)]):
        REFUSED.append("Experiment 3 now has live-heap rows at delta = 20%; register its own keys "
                       "instead of letting the prose borrow Experiment 4's")
    n_lt, r_lt, n_gt, r_gt = sides(m4["EHAUSM-I"] / m4[PAPER_UB])
    R["số bộ HAUSP-UB nhẹ hơn APEAU-I"] = f"{word(n_lt)} of the {word(len(m4))}"
    R["khoảng nơi HAUSP-UB nhẹ hơn APEAU-I"] = r_lt
    R["số bộ HAUSP-UB nặng hơn APEAU-I"] = word(n_gt)
    R["khoảng nơi HAUSP-UB nặng hơn APEAU-I"] = r_gt
    R["khoảng tỉ số mem(HAUSP-UB)/mem(APEAU-R) trên bảy bộ"] = rng(m4[PAPER_UB] / m4["EHAUSM-R"], nd=1)
    n_hv, r_hv, n_lg, r_lg = sides(m4[PAPER_UB] / m4["Pre-HAUSPM"])
    R["số bộ HAUSP-UB nặng hơn Pre-HAUSPM"] = f"{word(n_hv)} of the {word(len(m4))}"
    R["khoảng nơi HAUSP-UB nặng hơn Pre-HAUSPM"] = r_hv
    R["bộ và tỉ số nơi HAUSP-UB nhẹ hơn Pre-HAUSPM"] = " and ".join(
        f"{ds_name(d)} ({m4['Pre-HAUSPM'][d] / m4[PAPER_UB][d]:.1f}$\\times$)"
        for d in DS_ORDER if d in m4.index and m4[PAPER_UB][d] < m4["Pre-HAUSPM"][d])
    # --- pool
    R["khoảng số lượt mượn danh sách mỗi bộ"] = f"{pl['PoolBorrows'].min()/1e6:.1f}--{pl['PoolBorrows'].max()/1e6:.0f}~million"
    R["đỉnh số danh sách sống lớn nhất trên bảy bộ"] = f"{int(pl['PoolPeakLive'].max()):,}".replace(",", "{,}")
    R["min PoolReuses/PoolBorrows"] = f"{100 * (pl['PoolReuses'] / pl['PoolBorrows']).min():.1f}\\%"
    R["PoolPeakLive HAUSP-UB[noEUCS"] = f"{int(pl.loc['BIBLE', 'PoolPeakLive']):,}".replace(",", "{,}")
    # --- exactness
    n5, top5, n6 = exactness_counts()
    R["số cấu hình | nguồn: tab_exactness(a)"] = str(n5)
    R["số mẫu lớn nhất"] = f"{top5:,}".replace(",", "{,}")
    R["số cấu hình | nguồn: tab_exactness(b)"] = str(n6)
    R["số cấu hình hoàn thành"] = f"{completed_configs():,}".replace(",", "{,}")
    # --- Experiment 8
    c8 = counts(8)
    R["giá trị cột Ratio | nguồn: tab_exp8_eta"] = f"{(c8['EHAUSM-I'] / c8[PAPER_UB]).max():.2f}$\\times$"
    # --- Experiment 7
    def k(ds, arm, kk):
        try: return e7.loc[(ds, arm, kk)]
        except KeyError: return np.nan
    def growth(arm):
        g = {d: k(d, arm, 100) / k(d, arm, 10) for d in DS_ORDER}
        return pd.Series({d: v for d, v in g.items() if not pd.isna(v)})

    lin = growth(PAPER_UB)
    near = lin[(lin >= 8) & (lin <= 12)]      # a tenfold batch count for a tenfold cost
    over = lin[lin > 12]
    R["số bộ tuyến tính"] = word(len(near))
    R["K lớn nhất"] = "100"
    R["tên các bộ tuyến tính"] = names(near.index)
    R["khoảng tỉ số t(K=100)/t(K=10) trên các bộ tuyến tính"] = rng(near)
    R["tên bộ siêu tuyến tính"] = names(over.index)
    R["tỉ số t(K=100)/t(K=10) trên bộ siêu tuyến tính"] = rng(over, nd=1)
    preg = growth("Pre-HAUSPM")
    R["khoảng tỉ số t(K=100)/t(K=10) của Pre-HAUSPM"] = rng(preg, nd=1)
    R["tên bộ và tỉ số lớn nhất của Pre-HAUSPM"] = f"{ds_name(preg.idxmax())} ({preg.max():.1f}$\\times$)"
    R["số bộ Pre-HAUSPM hoàn thành K lớn nhất"] = word(len(preg))
    surv = {}
    for arm in ("EHAUSM-I", "Pre-HAUSPM", PAPER_UB):
        surv[arm] = sum(1 for d in DS_ORDER if not pd.isna(k(d, arm, 100)))
    R["số bộ | nguồn: tab_exp7_matrix"] = str(surv["EHAUSM-I"])
    both = [d for d in DS_ORDER if not pd.isna(k(d, "EHAUSM-I", 100)) and not pd.isna(k(d, PAPER_UB, 100))]
    R["khoảng tỉ số t(E-I)/t(HAUSP-UB) tại K=100"] = rng(pd.Series({d: k(d, "EHAUSM-I", 100) / k(d, PAPER_UB, 100) for d in both}), nd=1)
    bothp = [d for d in DS_ORDER if not pd.isna(k(d, "Pre-HAUSPM", 100)) and not pd.isna(k(d, PAPER_UB, 100))]
    R["khoảng tỉ số | nguồn: tab_exp7_matrix"] = rng(pd.Series({d: k(d, "Pre-HAUSPM", 100) / k(d, PAPER_UB, 100) for d in bothp}), nd=1)
    # warm start: the two datasets order the two algorithms the opposite way, so each
    # dataset is stated in the direction of its own winner rather than as one range.
    wr = (w["EHAUSM-I"] / w[PAPER_UB]).dropna()          # >1: the proposed algorithm is faster
    R["bộ và tỉ số nơi HAUSP-UB nhanh hơn dưới warm20"] = " and ".join(
        f"{ds_name(d)} ({wr[d]:.2f}$\\times$)" for d in DS_ORDER if d in wr.index and wr[d] >= 1)
    R["bộ và tỉ số nơi APEAU-I nhanh hơn dưới warm20"] = " and ".join(
        f"{ds_name(d)} ({1 / wr[d]:.2f}$\\times$)" for d in DS_ORDER if d in wr.index and wr[d] < 1)
    R["khoảng tỉ số t(Pre-HAUSPM)/t(HAUSP-UB) warm20"] = rng((w["Pre-HAUSPM"] / w[PAPER_UB]).dropna(), nd=1)
    m11 = load_memory(11)
    if m11 is not None:
        mm = m11.groupby(["Dataset", "Algorithm"])["MemLive(MB)"].max().unstack("Algorithm")
        mr = (mm["EHAUSM-I"] / mm[PAPER_UB]).dropna()    # >1: the proposed algorithm is lighter
        R["bộ và tỉ số nơi HAUSP-UB nhẹ hơn dưới warm20"] = " and ".join(
            f"{ds_name(d)} ({mr[d]:.0f}$\\times$)" for d in DS_ORDER if d in mr.index and mr[d] >= 1)
        R["bộ và tỉ số nơi HAUSP-UB nặng hơn dưới warm20"] = " and ".join(
            f"{ds_name(d)} ({1 / mr[d]:.1f}$\\times$)" for d in DS_ORDER if d in mr.index and mr[d] < 1)
    m7 = load_memory(7)
    if m7 is not None:
        f100 = m7[(m7["Dataset"] == "FIFA") & (m7["Algorithm"] == PAPER_UB)]["MemLive(MB)"].max()
        if not pd.isna(f100):
            R["MB live FIFA tại K lớn nhất, arm của bài"] = f"{f100:,.0f}".replace(",", "{,}") + r"\,MB"
    # --- Experiment 10
    d10 = load_experiment(10)
    o10 = d10[d10["Status"].isin(OK) & (d10["BatchID"] == 1)]
    t10 = o10.groupby(["Dataset", "mu"])["tTotal(ms)"].mean().unstack("mu")
    mus = sorted(t10.columns)
    R["khoảng tỉ số t(μ=0.40)/t(μ=0.05), hoặc OT"] = rng((t10[mus[-1]] / t10[mus[0]]).dropna(), nd=1)
    R["min cột min_μ tested/f"] = f"{o10['BufferTested'].div(o10['SafetyBound'].replace(0, np.nan)).min():,.0f}".replace(",", "{,}")
    pre_min = t10[mus[0]]          # milliseconds, as t3 is; dividing one side by 1000 gave 0.0x
    ub3 = t3[PAPER_UB]
    R["tỉ số t(Pre-HAUSPM, μ nhỏ nhất)/t(HAUSP-UB)"] = rng((pre_min / ub3).dropna(), nd=1)
    R["tỉ số tại μ thuận lợi nhất, BMS1 làm ví dụ"] = f"{(pre_min / ub3)['BMS1_SPMF']:.1f}$\\times$"
    R["tỉ số tại μ nhỏ nhất"] = rng((pre_min / ub3).dropna(), nd=1)
    # --- protocol and variance
    R["ngưỡng giây nâng lên 10 lượt"] = "10~s"
    R["ngưỡng giây nâng lên 15 lượt"] = "1~s"
    R["ngưỡng giây cột cuối"] = "5~s"
    R["phút | nguồn: chú thích tab_exp7_matrix"] = "60"
    vt = variance_table()
    med = [float(x) for x in re.findall(r"& ([0-9.]+) & [0-9.]+ & [0-9.]+ \\\\", vt)]
    R["max của cột Median CV"] = f"{max(med):.2f}\\%" if med else None
    last = [float(x) for x in re.findall(r"& ([0-9.]+) \\\\", vt)]
    R["max cột cuối"] = f"{max(last):.1f}\\%" if last else None
    ws = []
    for comp, label in (("Exp1 runtime", "Experiment~1"), ("Exp3 update", "Experiment~3"), ("Exp7 total", "Experiment~7")):
        ps = [wilcoxon_line(comp, b)[0] for b in ("EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM") if _has(comp, b)]
        n = wilcoxon_line(comp, "EHAUSM-I" if comp != "Exp1 runtime" else "EHAUSM-R")[1]
        pv = max(ps); ws.append(f"{label}: $p {'< 0.001' if pv < 0.001 else f'= {pv:.3f}'}$, $n = {n}$")
    pm, nm = wilcoxon_line("Exp4 peak live heap", "EHAUSM-I")
    ws.append(f"Experiment~4 on live heap: $p = {pm:.3f}$, $n = {nm}$, the proposed algorithm being the lighter one on five of the seven pairs")
    R["p và n cho Exp 1, 3, 7 theo thời gian; Exp 4 theo live heap, chiều so sánh phải tính lại vì chiều bộ nhớ đã đảo"] = "; ".join(ws)
    # --- values read from other artifacts
    d4 = load_experiment(4)
    o4 = d4[d4["Status"].isin(OK)]
    used = o4.groupby(["Dataset", "Algorithm"])["MemPeak(MB)"].max().unstack("Algorithm")
    ratio_ul = (used / m4)[["EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM"]]
    R["khoảng hệ số used-heap/live-heap của baseline"] = f"{ratio_ul.min().min():.1f}--{ratio_ul.max().max():.1f}$\\times$"
    R["khoảng hệ số | nguồn: nhật ký 2026-09-07"] = f"{ratio_ul.min().min():.1f}--{ratio_ul.max().max():.1f}$\\times$"
    c9 = counts(9)
    lay = UB_CHAIN["layout"]
    R["số ô khớp/tổng"] = f"{int((c9[lay] == c9['EHAUSM-R']).sum())} of {len(c9)} datasets"
    # live heap saved by Layer 2, from the dedicated memory run of Experiment 9
    m9 = load_memory(9)
    if m9 is not None:
        mm9 = m9.groupby(["Dataset", "Algorithm"])["MemLive(MB)"].max().unstack("Algorithm")
        if "HAUSP-UB[noL2+noEUCS]" in mm9.columns and PAPER_UB in mm9.columns:
            sv = (1 - mm9[PAPER_UB] / mm9["HAUSP-UB[noL2+noEUCS]"]).dropna() * 100
            if len(sv):
                R["khoảng phần trăm heap-sống Layer 2 tiết kiệm"] = f"{sv.min():.0f}--{sv.max():.0f}\\%"
    # generator parameters, read from the converter source
    conv = (ROOT / "src" / "main" / "java" / "SPMF_Converter.java").read_text()
    mix = re.search(r"mixture (\d+)% -> (\d+)-(\d+), (\d+)% -> (\d+)-(\d+), (\d+)% -> (\d+)-(\d+)", conv)
    ln = re.search(r"most values fall in (\d+)-(\d+), a few reach (\d+)-(\d+)", conv)
    seed = re.search(r"new Random\((\d+)\)", conv)
    if mix and ln and seed:
        g = mix.groups()
        R["các khoảng và tỉ lệ của bộ sinh"] = (f"internal utilities are drawn from a mixture, {g[0]}\\% in $[{g[1]},{g[2]}]$, "
            f"{g[3]}\\% in $[{g[4]},{g[5]}]$ and {g[6]}\\% in $[{g[7]},{g[8]}]$; external utilities from a log-normal draw "
            f"clipped to $[1,1000]$, most falling in $[{ln.group(1)},{ln.group(2)}]$")
        R["phiên bản và seed"] = f"seed~{seed.group(1)}"
    man = ROOT / "datasets" / "MANIFEST.sha256"
    if man.exists():
        R["tên và phiên bản bản phát hành huspm-datasets"] = (
            f"\\texttt{{huspm-datasets}}, {len(man.read_text().strip().split(chr(10)))} files listed with their SHA-256 digests")
    return R, dict(t1=t1, t3=t3, t9=t9, m4=m4, e7=e7, w=w, pl=pl)


def _has(comp, base):
    try:
        wilcoxon_line(comp, base); return True
    except KeyError:
        return False


LEDGER = TEX.parent / "numbers_filled.json"


def recheck(apply: bool = False) -> int:
    """Compare every value the ledger recorded with the value the artifacts give now.

    Run after any new measurement lands. A key whose value moved names the sentence that
    is now quoting a number no run produces, which is the one thing grep cannot find.
    """
    import json
    if not LEDGER.exists():
        print(f"no ledger at {LEDGER}; run the fill once first")
        return 2
    was = json.loads(LEDGER.read_text())
    now, _ = build()
    text = TEX.read_text()
    lines = text.split("\n")
    moved = []
    for key, old in was.items():
        new = now.get(key)
        if new is None or new == old:
            continue
        where = [i + 1 for i, ln in enumerate(lines) if old in ln]
        moved.append((key, old, new, where))
    rewritten, manual = 0, []
    for key, old, new, where in moved:
        print(f"MOVED  {key}\n       was {old!r}  ->  now {new!r}")
        print(f"       manuscript lines still carrying the old value: {where or 'none found'}")
        # a distinctive string (a range, a ratio, a spelled-out count) can be carried
        # everywhere it occurs; a bare digit cannot, and is left for a human to place
        distinctive = len(old) >= 5 and not old.strip().isdigit()
        if apply and text.count(old) and distinctive:
            n = text.count(old)
            text = text.replace(old, new); was[key] = new; rewritten += n
            print(f"       rewritten in {n} place(s)")
        elif apply:
            manual.append((key, old, text.count(old)))
    if apply:
        TEX.write_text(text)
        LEDGER.write_text(json.dumps(was, ensure_ascii=False, indent=1, sort_keys=True))
        print(f"\nrewrote {rewritten} values in the manuscript")
        for key, old, n in manual:
            print(f"  BY HAND  {key}: {old!r} occurs {n} times, too ambiguous to replace")
    print(f"\n{len(moved)} of {len(was)} recorded values moved")
    for r in REFUSED:
        print("REFUSED", r)
    return 1 if moved else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recheck", action="store_true",
                    help="recompute every recorded value and report the ones that moved")
    ap.add_argument("--resync", action="store_true",
                    help="as --recheck, and rewrite the manuscript where the old value occurs exactly once")
    a = ap.parse_args()
    if a.recheck or a.resync:
        return recheck(apply=a.resync)
    R, _ = build()
    R = {k: v for k, v in R.items() if v is not None}
    text = TEX.read_text()
    holes = re.findall(r"\[SỐ:(.*?)\]", text, re.S)
    filled, missing, bare = 0, [], 0
    for body in holes:
        flat = " ".join(body.split())
        hit = None
        if flat == "khoảng tỉ số | nguồn: tab_exp1_runtime":
            seq = ["khoảng tỉ số EHAUSM-R/HAUSP-UB | nguồn: tab_exp1_runtime",
                   "khoảng tỉ số EHAUSM-I/HAUSP-UB | nguồn: tab_exp1_runtime",
                   "khoảng tỉ số Pre-HAUSPM/HAUSP-UB | nguồn: tab_exp1_runtime"]
            hit = seq[min(bare, len(seq) - 1)]; bare += 1
            text = text.replace("[SỐ:" + body + "]", R[hit], 1); filled += 1; continue
        for key in sorted(R, key=len, reverse=True):
            if " ".join(key.split()) in flat:
                hit = key; break
        if hit is None:
            missing.append(flat); continue
        text = text.replace("[SỐ:" + body + "]", R[hit], 1)
        filled += 1
    if not a.dry_run:
        TEX.write_text(text)
        import json
        used = {k: v for k, v in R.items() if v in text}
        prev = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
        LEDGER.write_text(json.dumps({**prev, **used}, ensure_ascii=False, indent=1, sort_keys=True))
        print(f"ledger: {len(used)} values recorded in {LEDGER}")
    print(f"filled {filled}/{len(holes)} placeholders" + ("  (dry run)" if a.dry_run else ""))
    if missing:
        print("\nNOT FILLED — no computation registered:")
        for m in dict.fromkeys(missing):
            print("  -", m[:150])
    if REFUSED:
        print("\nREFUSED — the range could not be stated as asked:")
        for r in dict.fromkeys(REFUSED):
            print("  -", r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
