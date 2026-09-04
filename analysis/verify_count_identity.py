#!/usr/bin/env python3
"""Test the counting identities between the legacy and the new CSV schema.

The 2026-09-03 counting fix changed what ``Cand`` means for the HAUSP-UB
arms (utility lists assembled instead of children recursed into). This
script re-runs nothing; it compares a re-run in the new schema with the
legacy file, cell by cell, and records which identities hold:

    (R)  new.Recursed == old.Cand                                   HAUSP-UB arms
    (B)  new.Cand     == old.Cand                                   EHAUSM-*, Pre-HAUSPM
    (F)  new.Cand     == old.Cand + old.PrunedL2 + old.PrunedL3     HAUSP-UB arms  (the column-sum formula)
    (C)  new.Cand     == old.Cand + old.PrunedL2 + (new.PrunedL3 - new.PrunedL3Node) + new.PrunedL1Root
                                                                    HAUSP-UB arms  (corrected identity)
    plus HAUSP and PrunedL1/L2/L3 equal for every arm.

(F) was refuted on 2026-09-04: prunedL3 is incremented both on node entry
(the node was already recursed into and therefore already in old.Cand) and
on the child-level test; only the child-level share adds new lists. The new
build also counts a root list before the root test (as the baselines do),
adding PrunedL1Root roots the legacy build skipped. Legacy files separate
neither term, so (C) can be checked only against the new file's columns, and
legacy HAUSP-UB counts cannot be converted to "lists assembled" by formula.

Counts are deterministic, so every cell must match exactly. The join key is
(Dataset, Algorithm, BatchID, MinUtil, DeltaRatio), first trial of each file;
the script also confirms that counts do not vary across trials.

Usage:
    python3 analysis/verify_count_identity.py --old results/exp1/experiment1_tightness.csv \\
        --new results-2026-09/exp1_probe/exp1/experiment1_tightness.csv

Writes analysis_out/paper/count_identity.json (read by build_latex_tables.py).
Exit 0 when (R) and (B) hold on at least one row each and no count column
disagrees; the verdict on (F) and (C) is reported in the JSON and on stdout.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ANALYSIS_OUT, COUNT_IDENTITY_JSON, OK, ROOT, read_results  # noqa: E402

KEY = ["Dataset", "Algorithm", "BatchID", "MinUtil", "DeltaRatio"]
COUNT_COLS = ["Cand", "PrunedL1(SWU)", "PrunedL2(IAUUB)", "PrunedL3(MFUUB)", "HAUSP"]


def first_trial(df: pd.DataFrame) -> pd.DataFrame:
    ok = df[df["Status"].isin(OK)].copy()
    ok["MinUtil"] = ok["MinUtil"].round(6)
    ok["DeltaRatio"] = ok["DeltaRatio"].round(3)
    r0 = ok["RunIndex"].min() if len(ok) else 0
    return ok[ok["RunIndex"] == r0]


def trial_determinism(df: pd.DataFrame, label: str) -> list[str]:
    """Counts must be identical across trials inside one file."""
    ok = df[df["Status"].isin(OK)].copy()
    ok["MinUtil"] = ok["MinUtil"].round(6)
    ok["DeltaRatio"] = ok["DeltaRatio"].round(3)
    cols = [c for c in COUNT_COLS + ["Recursed"] if c in ok.columns and ok[c].notna().any()]
    var = ok.groupby(KEY)[cols].nunique()
    bad = var[(var > 1).any(axis=1)]
    return [f"{label}: counts differ across trials for {idx}: {row.to_dict()}" for idx, row in bad.iterrows()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True, action="append", help="legacy CSV (repeatable)")
    ap.add_argument("--new", required=True, action="append", help="re-run CSV in the new schema (repeatable)")
    ap.add_argument("--out", default=str(COUNT_IDENTITY_JSON))
    args = ap.parse_args()

    old = pd.concat([read_results(p) for p in args.old], ignore_index=True)
    new = pd.concat([read_results(p) for p in args.new], ignore_index=True)
    if not old["Legacy"].all():
        print("ERROR: --old must be legacy files (no Recursed column)")
        return 2
    if new["Legacy"].any():
        print("ERROR: --new must be files of the new schema (with Recursed column)")
        return 2

    problems: list[str] = []
    problems += trial_determinism(old, "old")
    problems += trial_determinism(new, "new")

    o = first_trial(old)
    n = first_trial(new)
    j = o.merge(n, on=KEY, suffixes=("_old", "_new"), how="inner")
    is_ub = j["Algorithm"].str.startswith("HAUSP-UB")
    j["Cand_expected"] = np.where(is_ub, j["Cand_old"] + j["PrunedL2(IAUUB)_old"] + j["PrunedL3(MFUUB)_old"], j["Cand_old"])
    j["Recursed_expected"] = np.where(is_ub, j["Cand_old"], np.nan)

    for c in ("PrunedL3Node", "PrunedL1Root"):
        if c + "_new" in j.columns:
            j[c] = j[c + "_new"]
    has_node = "PrunedL3Node" in j.columns and j["PrunedL3Node"].notna().any()
    if has_node:
        # Roots rejected by the root test were not counted by the legacy build
        # (counted after the test) but are counted by the new build (before it).
        l1root = j["PrunedL1Root"].fillna(0) if "PrunedL1Root" in j.columns else 0
        j["Cand_corrected"] = np.where(is_ub, j["Cand_old"] + j["PrunedL2(IAUUB)_old"]
                                       + (j["PrunedL3(MFUUB)_new"] - j["PrunedL3Node"].fillna(0)) + l1root, j["Cand_old"])
    checks = {
        "(R) Recursed_new == Cand_old (HAUSP-UB*)": (~is_ub) | (j["Recursed_new"] == j["Recursed_expected"]),
        "(B) Cand_new == Cand_old (baselines)": is_ub | (j["Cand_new"] == j["Cand_old"]),
        "(F) Cand_new == Cand_old + L2 + L3 (HAUSP-UB*)": (~is_ub) | (j["Cand_new"] == j["Cand_expected"]),
        "HAUSP equal": (j["HAUSP_new"] == j["HAUSP_old"]),
        "PrunedL1 equal": (j["PrunedL1(SWU)_new"] == j["PrunedL1(SWU)_old"]),
        "PrunedL2 equal": (j["PrunedL2(IAUUB)_new"] == j["PrunedL2(IAUUB)_old"]),
        "PrunedL3 equal": (j["PrunedL3(MFUUB)_new"] == j["PrunedL3(MFUUB)_old"]),
    }
    if has_node:
        checks["(C) Cand_new == Cand_old + L2 + (L3 - L3node) + L1root (HAUSP-UB*)"] = (~is_ub) | (j["Cand_new"] == j["Cand_corrected"])
    required = {"(R) Recursed_new == Cand_old (HAUSP-UB*)", "(B) Cand_new == Cand_old (baselines)",
                "HAUSP equal", "PrunedL1 equal", "PrunedL2 equal", "PrunedL3 equal"}

    pd.set_option("display.width", 250)
    cols = KEY + ["Cand_old", "PrunedL2(IAUUB)_old", "PrunedL3(MFUUB)_old", "Cand_expected", "Cand_new", "Recursed_new", "HAUSP_old", "HAUSP_new"]
    if has_node:
        cols += ["PrunedL3Node"] + (["PrunedL1Root"] if "PrunedL1Root" in j.columns else []) + ["Cand_corrected"]
    show = j[cols]
    print(f"rows in old (first trial, OK): {len(o)}   rows in new: {len(n)}   joined: {len(j)}")
    print(show.to_string(index=False))
    print()
    per_arm = j.groupby("Algorithm").size().to_dict()
    print("joined rows per arm:", per_arm)
    verdict = {}
    for name, ok in checks.items():
        nbad = int((~ok).sum())
        verdict[name] = nbad == 0
        print(f"  [{'PASS' if nbad == 0 else 'FAIL'}] {name}: {int(ok.sum())}/{len(ok)} cells match")
        if nbad and name in required:
            problems.append(f"{name}: {nbad} mismatching cells, e.g. {j.loc[~ok, KEY].iloc[0].to_dict()}")
    ub_rows = int(is_ub.sum())
    base_rows = int((~is_ub).sum())
    if ub_rows == 0:
        problems.append("no HAUSP-UB rows compared (identity (R) unchecked)")
    if base_rows == 0:
        problems.append("no baseline rows compared (identity (B) unchecked)")
    unmatched_new = n.merge(o, on=KEY, how="left", indicator=True)
    unmatched_new = unmatched_new[unmatched_new["_merge"] == "left_only"]
    if len(unmatched_new):
        print(f"  note: {len(unmatched_new)} new rows have no legacy counterpart (not counted): "
              f"{sorted(unmatched_new['Algorithm'].unique())}")
    if len(j) == 0:
        problems.append("0 rows could be compared (vacuous check)")

    passed = not problems
    ANALYSIS_OUT.mkdir(parents=True, exist_ok=True)
    fkey = "(F) Cand_new == Cand_old + L2 + L3 (HAUSP-UB*)"
    ckey = "(C) Cand_new == Cand_old + L2 + (L3 - L3node) + L1root (HAUSP-UB*)"
    info = {
        "passed": passed,
        "checked_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rows_compared": int(len(j)),
        "rows_per_arm": {k: int(v) for k, v in per_arm.items()},
        "recursed_identity_holds": bool(verdict.get("(R) Recursed_new == Cand_old (HAUSP-UB*)", False)) and ub_rows > 0,
        "baseline_cand_identity_holds": bool(verdict.get("(B) Cand_new == Cand_old (baselines)", False)) and base_rows > 0,
        "cand_formula_holds": bool(verdict.get(fkey, False)),
        "corrected_identity_holds": (bool(verdict.get(ckey, False)) if ckey in verdict else None),
        "old_sources": args.old,
        "new_sources": args.new,
        "new_run_ids": sorted({r for r in new["RunID"].dropna().astype(str).unique()}),
        "summary": ("required identities hold" if passed else "; ".join(problems[:5]))
                   + ("; column-sum formula (F) holds" if verdict.get(fkey) else "; column-sum formula (F) REFUTED"),
        "note": "legacy HAUSP-UB Cand is converted to lists assembled only from a counts re-run, never by formula",
    }
    Path(args.out).write_text(json.dumps(info, indent=2))
    print()
    if passed:
        print(f"RESULT: PASS (required identities) -- {info['summary']}; wrote {Path(args.out).relative_to(ROOT)}")
        return 0
    print("RESULT: FAIL")
    for p in problems:
        print("  -", p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
