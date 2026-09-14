#!/usr/bin/env python3
"""Compare a short probe run with the recorded artifacts: every deterministic count must be equal.

Used as a pre-flight before a long measurement campaign when the code changed in a way that must not
alter any result (memory-layout work). Exits non-zero on the first mismatch, so a runbook step can
abort in a minute instead of discovering the problem after hours.

Usage: python3 analysis/verify_counts_probe.py --probe results-probe/<dir>/exp1/experiment1_tightness.csv
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OK, ROOT, read_optional  # noqa: E402

COLS = ["HAUSP", "Cand", "Recursed", "PrunedL1(SWU)", "PrunedL2(IAUUB)",
        "PrunedL3(MFUUB)", "PrunedL3Node", "PrunedL1Root", "SHAUS"]
REFERENCE = ["results-2026-09c", "results-2026-09b", "results-2026-09"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", required=True)
    ap.add_argument("--file", default="exp1/experiment1_tightness.csv")
    a = ap.parse_args()
    probe = read_optional(Path(a.probe))
    if probe is None:
        print(f"FAIL: probe file {a.probe} is missing or empty"); return 2
    probe = probe[probe["Status"].isin(OK)]
    ref = None
    for gen in REFERENCE:
        r = read_optional(ROOT / gen / a.file)
        if r is None:
            continue
        r = r[r["Status"].isin(OK)]
        ref = r if ref is None else pd.concat([ref, r], ignore_index=True)
    if ref is None:
        print("FAIL: no recorded artifact to compare against"); return 2
    key = ["Algorithm", "Dataset", "BatchID", "MinUtil"]
    ref = ref.sort_values("RunIndex").drop_duplicates(key)
    m = probe.merge(ref, on=key, suffixes=("_p", "_r"))
    if m.empty:
        print("FAIL: the probe shares no cell with the artifacts; nothing was compared"); return 2
    cols = [c for c in COLS if f"{c}_p" in m.columns and f"{c}_r" in m.columns]
    bad = {}
    for c in cols:
        p, r = m[f"{c}_p"].astype(float), m[f"{c}_r"].astype(float)
        both = p.notna() & r.notna()
        n = int((p[both] != r[both]).sum())
        if n:
            bad[c] = f"{n}/{int(both.sum())}"
    print(f"compared {len(m)} cells on {len(cols)} count columns: " + ", ".join(cols))
    if bad:
        print("FAIL: counts changed —", bad)
        for c in bad:
            d = m[m[f"{c}_p"].astype(float) != m[f"{c}_r"].astype(float)]
            for row in d.head(3).itertuples():
                print(f"   {row.Dataset} batch {row.BatchID}: {c} probe={getattr(row, c + '_p')} recorded={getattr(row, c + '_r')}")
        return 2
    print("PASS: every deterministic count matches the recorded artifacts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
