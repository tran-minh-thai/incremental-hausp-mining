#!/usr/bin/env python3
"""Attribute the per-batch heap peak of HAUSP-UB to its persistent structures.

Reads the Experiment 7 CSV produced with
    --exp 7 --dataset fifa --k 100 --algo HAUSP-UB --repeats 1 --results-dir results-2026-09/exp7_memprobe
and prints, per batch, the four attribution columns sampled at the moment the
batch's heap peak was recorded (PoolBytes, FlatBytes, EucsBytes,
AudulRootBytes), their sum and the share of MemPeak(MB) they explain.

Decision rule (fixed before the run): if the four columns together explain
less than 50% of the peak on the batches where the peak is largest, the
source of the growth is NOT located by this probe and the manuscript keeps
its limitation sentence; otherwise the dominant column names the cause.

Usage: python3 analysis/memprobe_report.py [--csv PATH] [--dataset FIFA] [--k 100]
Writes analysis_out/paper/exp7_memprobe.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ANALYSIS_OUT, NEW_RESULTS, OK, ROOT, read_results  # noqa: E402

MB = 1024.0 * 1024.0
COLS = ["PoolBytes", "FlatBytes", "EucsBytes", "AudulRootBytes"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=str(NEW_RESULTS / "exp7_memprobe" / "exp7" / "experiment7_long_batch.csv"))
    ap.add_argument("--dataset", default="FIFA")
    ap.add_argument("--k", type=int, default=100)
    ap.add_argument("--threshold", type=float, default=0.5, help="minimum explained share to call the cause located")
    args = ap.parse_args()

    p = Path(args.csv)
    if not p.exists():
        print(f"ERROR: {p} does not exist; run the memory probe first (see RUNBOOK)")
        return 2
    df = read_results(p)
    df = df[(df["Algorithm"] == "HAUSP-UB") & (df["Dataset"] == args.dataset) & df["Status"].isin(OK)].copy()
    df["K"] = df.groupby(["RunIndex"])["BatchID"].transform("count")
    df = df[df["K"] == args.k]
    if df.empty:
        print(f"ERROR: no complete HAUSP-UB group with K={args.k} on {args.dataset} in {p}")
        return 2
    df = df[df["RunIndex"] == df["RunIndex"].min()].sort_values("BatchID")
    if df[COLS].isna().any().any():
        print("ERROR: attribution columns are empty; the CSV was produced by a build without the memory probe")
        return 2

    for c in COLS:
        df[c + "(MB)"] = df[c] / MB
    df["Sum(MB)"] = sum(df[c + "(MB)"] for c in COLS)
    df["Share"] = df["Sum(MB)"] / df["MemPeak(MB)"]

    top = df.nlargest(5, "MemPeak(MB)")
    share_top = float(top["Share"].mean())
    dominant = max(COLS, key=lambda c: float(top[c + "(MB)"].mean()))
    located = share_top >= args.threshold

    lines = [
        f"# Memory attribution of HAUSP-UB on {args.dataset}, K={args.k}",
        "",
        f"Source: `{df['SourceFile'].iloc[0]}` run_id={df['RunID'].iloc[0]} (trial {int(df['RunIndex'].iloc[0])}).",
        "Values sampled at the moment each batch's heap peak was recorded; bytes of array payload.",
        "",
        "| Batch | MemPeak (MB) | Pool (MB) | Flat DB (MB) | EUCS (MB) | Root AU-DUL (MB) | Sum (MB) | Explained |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    step = max(1, args.k // 20)
    for _, r in df.iterrows():
        b = int(r["BatchID"])
        if b % step == 0 or b == args.k - 1 or b in set(top["BatchID"]):
            lines.append(f"| {b} | {r['MemPeak(MB)']:.0f} | {r['PoolBytes(MB)']:.0f} | {r['FlatBytes(MB)']:.0f} | "
                         f"{r['EucsBytes(MB)']:.0f} | {r['AudulRootBytes(MB)']:.0f} | {r['Sum(MB)']:.0f} | {100*r['Share']:.0f}% |")
    lines += [
        "",
        f"Batches with the five largest peaks: mean explained share = {100*share_top:.0f}% "
        f"(threshold {100*args.threshold:.0f}%); largest component there: {dominant} "
        f"({float(top[dominant + '(MB)'].mean()):.0f} MB of {float(top['MemPeak(MB)'].mean()):.0f} MB).",
        "",
        ("CONCLUSION: located -- the tracked structures account for the peak; the dominant term is " + dominant + ".")
        if located else
        "CONCLUSION: not located -- the four tracked structures explain less than half of the peak; "
        "the remainder is transient (per-batch scratch arrays, GC lag, DFS working set) and the manuscript keeps "
        "its limitation sentence until a finer probe is run.",
    ]
    ANALYSIS_OUT.mkdir(parents=True, exist_ok=True)
    out = ANALYSIS_OUT / "exp7_memprobe.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
