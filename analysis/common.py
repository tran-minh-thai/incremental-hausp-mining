#!/usr/bin/env python3
"""Shared loaders for the result CSVs (legacy and 2026-09 schema).

Two generations of CSV exist:

* legacy files under ``results/`` (written before 2026-09-03): no ``#``
  provenance lines, no ``Recursed`` column, and for the ``HAUSP-UB*`` arms
  ``Cand`` counted only the children that passed Layers 2 and 3;
* new files (``results-2026-09/`` and later): a ``# run_id=...`` line first,
  the ``Recursed``/``ArmOrder``/``RunID``/memory-attribution columns, and
  ``Cand`` = number of utility lists assembled for every algorithm.

``read_results`` hides the difference: missing columns become NaN (never a
guessed value), every frame gets ``SourceFile``/``Legacy`` columns and the run
ids parsed from the provenance lines, and ``add_unified_counts`` derives the
comparable count columns:

    CandUnified      lists assembled
                     legacy HAUSP-UB* rows: NaN unless a counts re-run supplies it (overlay_counts);
                                            the formula Cand + PrunedL2 + PrunedL3 was refuted on 2026-09-04:
                                            it overstates the count by the node-level Layer-3 prunes
                     everything else:       Cand
    RecursedUnified  children recursed into
                     legacy HAUSP-UB* rows: Cand
                     new HAUSP-UB* rows:    Recursed
                     EHAUSM-*/Pre-HAUSPM:   Cand - PrunedL2(IAUUB)   (nodes that passed PEAU)

These identities are claims about two different builds of the code; they are
relied on only after ``verify_count_identity.py`` has confirmed them on a
re-run (``count_identity_ok``).
"""
from __future__ import annotations

import sys
import json
import os
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OLD_RESULTS = ROOT / "results"
NEW_RESULTS = ROOT / "results-2026-09"
#: Third generation (2026-09-10): HAUSP-UB arms re-measured after the per-node CPU timers were
#: removed from the hot path (EXPERIMENT_CHANGELOG 2026-09-10 afternoon). Contains HAUSP-UB
#: arms only; its rows replace the same arms of the same condition in results-2026-09/.
#: HAUSP_NEWER_RESULTS (environment) overrides the path; used only to rehearse the analysis
#: pipeline on a simulated generation 3 under results-probe/ before the real run exists.
NEWER_RESULTS = Path(os.environ["HAUSP_NEWER_RESULTS"]) if os.environ.get("HAUSP_NEWER_RESULTS") else ROOT / "results-2026-09b"
#: Fourth generation (2026-09-13): every arm of Experiments 1, 2, 3, 9 and 11 re-measured inside a
#: single campaign, one JVM per arm, back to back, after cross-campaign repeatability was found to
#: reach 28 % on two Experiment-7 cells (EXPERIMENT_CHANGELOG 2026-09-12). Unlike generation 3 it may
#: carry baseline arms; it replaces exactly the arms it contains, for the conditions it contains.
NEWEST_RESULTS = Path(os.environ["HAUSP_NEWEST_RESULTS"]) if os.environ.get("HAUSP_NEWEST_RESULTS") else ROOT / "results-2026-09c"
#: Fifth generation, memory only (2026-09-15): Experiment 4 re-measured after the per-extension
#: buffers stopped being sized by the item identifier space (commits 3b85a8c, 7d774fd).
MEM_NEWEST_RESULTS = ROOT / "results-2026-09d"
#: Sixth generation (2026-09-17): the warm-start schedule of Experiment 11 measured at every batch
#: count rather than the largest one only, so its row of the batch-count matrix has no unmeasured
#: cell. The whole sweep is taken in one campaign, including the batch count already measured:
#: cross-campaign repeatability reached 28 % on two Experiment-7 cells (EXPERIMENT_CHANGELOG
#: 2026-09-12), so a row assembled from two campaigns compares two different things.
SIXTH_RESULTS = (Path(os.environ["HAUSP_SIXTH_RESULTS"]) if os.environ.get("HAUSP_SIXTH_RESULTS")
                 else ROOT / "results-2026-09e")
#: Seventh generation (2026-09-20): the Ta-Feng database, measured in its own campaign for
#: Experiments 1, 5, 6 and 9. It carries every arm of those experiments but only one dataset, so
#: it adds a condition rather than replacing one -- six of the seven older databases have exactly
#: 1.00 item per itemset, so an I-extension is not legal on them and this is the first real
#: database on which that branch does anything. The directory keeps the run id and commit in its
#: name instead of a generation letter, so the artifact says which run wrote it.
SEVENTH_RESULTS = (Path(os.environ["HAUSP_SEVENTH_RESULTS"]) if os.environ.get("HAUSP_SEVENTH_RESULTS")
                   else ROOT / "results-20260920-0654-3b44d0a")
#: Eighth generation (2026-09-20): Experiments 4 and 10 on Ta-Feng, the two that its first
#: campaign did not cover, so its row in the memory table and the safety-margin table stops being
#: blank. Registered before the run exists; read_optional returns None until it does, so nothing
#: here depends on the campaign having happened.
EIGHTH_RESULTS = (Path(os.environ["HAUSP_EIGHTH_RESULTS"]) if os.environ.get("HAUSP_EIGHTH_RESULTS")
                  else ROOT / "results-2026-09f")
#: Ninth generation (2026-09-21): Experiments 2, 3, 7 and 11 on Ta-Feng, the four its earlier
#: campaigns did not cover. It carries one database, so it adds conditions rather than replacing
#: any. Experiment 7 in it holds no successful row at all -- every arm exceeded the per-batch
#: limit at batch 0 of the equal schedule -- and that is the record, not a gap to be filled.
NINTH_RESULTS = (Path(os.environ["HAUSP_NINTH_RESULTS"]) if os.environ.get("HAUSP_NINTH_RESULTS")
                 else ROOT / "results-2026-09g")
#: Eleventh generation (2026-09-23): the two Experiment-7 cells whose only rows came from the
#: campaign that predates the provenance line -- BMS1 with Pre-HAUSPM at 100 batches and SIGN
#: with Pre-HAUSPM at 20 batches. Both print OT in the batch-count table, and an OT verdict says
#: "did not finish inside the cap", so a cell with no recorded cap and no run id cannot be read
#: at all. Nothing else covers those two conditions, so they could not be dropped either. It
#: carries one arm, so the merge replaces that arm at those two conditions and leaves the rest.
ELEVENTH_RESULTS = (Path(os.environ["HAUSP_ELEVENTH_RESULTS"]) if os.environ.get("HAUSP_ELEVENTH_RESULTS")
                    else ROOT / "results-2026-09i")
#: Twelfth generation (2026-09-23): the same two Experiment-7 cells under a raised per-batch
#: cap. The ninth-generation re-measurement recorded them as OT at 90 minutes, which says only
#: "more than 90"; the quantity that decides whether that verdict is a property of the method
#: or of the limit is the time the arm actually needs, and a cell cut short does not carry it.
#: A cell that finishes here replaces the OT row of the generation above: a success owes
#: nothing to the cap, since the watchdog never fired and the repeat count follows the measured
#: time. A cell that still does not finish leaves an OT at a second cap, which the audit
#: refuses -- deliberately, because two OT cells under different caps are not one verdict.
TWELFTH_RESULTS = (Path(os.environ["HAUSP_TWELFTH_RESULTS"]) if os.environ.get("HAUSP_TWELFTH_RESULTS")
                   else ROOT / "results-2026-09j")
#: Thirteenth generation, memory only (2026-09-24): Experiment 11 -- the warm-start schedule --
#: on Ta-Feng under live-heap sampling, the one database of that experiment whose memory had never
#: been measured. Its runtime was measured on 2026-09-21; without this the memory sentence of the
#: warm-start paragraph can speak for two of its three databases only. Same protocol as the SIGN and
#: SYN rows of results-2026-09d/mem: K=100, one trial, one JVM per arm.
MEM_THIRTEENTH_RESULTS = (Path(os.environ["HAUSP_MEM_THIRTEENTH_RESULTS"])
                          if os.environ.get("HAUSP_MEM_THIRTEENTH_RESULTS") else ROOT / "results-2026-09k")
#: Fourteenth generation (2026-09-25): the re-measurement of every printed cell that
#: analysis/stale_cells.py finds stale -- runs from before the last change to HAUSP-UB's memory
#: layout and timing (6eca0e3), SYN runs whose batches the parser change shifted (098b4a9), rows
#: from the tree that predates the provenance line, and runs that put every arm in one JVM where
#: the manuscript states one JVM per arm. Every arm of each stale (experiment, quantity, dataset)
#: is re-measured in one campaign; timing lands here, memory under mem/.
FOURTEENTH_RESULTS = (Path(os.environ["HAUSP_FOURTEENTH_RESULTS"])
                      if os.environ.get("HAUSP_FOURTEENTH_RESULTS") else ROOT / "results-2026-09l")
#: Tenth generation, memory only (2026-09-22): Experiment 3 re-measured at every increment size
#: under live-heap sampling. The earlier memory run covered one arm at delta=5% only, so the
#: update-memory column of that table printed "--" for all 32 of its rows -- not a gap for one
#: database but a column that had never carried a value.
MEM_TENTH_RESULTS = (Path(os.environ["HAUSP_MEM_TENTH_RESULTS"]) if os.environ.get("HAUSP_MEM_TENTH_RESULTS")
                     else ROOT / "results-2026-09h")
#: The probe tree is versioned but must never be a source for a number in the paper. Pointing a
#: generation at it is legitimate for rehearsing this pipeline (simulate_gen3/4), and illegitimate
#: for anything else, so say which is happening rather than allow it silently. The probe CSVs carry
#: the same wide schema as a real run, timing columns included, which is exactly why this is loud.
for _name, _tree in (("NEWER_RESULTS", NEWER_RESULTS), ("NEWEST_RESULTS", NEWEST_RESULTS)):
    if "results-probe" in str(_tree):
        print(f"[common] REHEARSAL: {_name} points at {_tree}, a probe tree. Numbers from this run "
              f"are a rehearsal of the pipeline and must not reach the manuscript.", file=sys.stderr)

COUNTS_RESULTS = NEW_RESULTS / "counts"
MEM_RESULTS = NEW_RESULTS / "mem"
ANALYSIS_OUT = ROOT / "analysis_out" / "paper"
COUNT_IDENTITY_JSON = ANALYSIS_OUT / "count_identity.json"

OK = {"SUCCESS", "SUCCESS_MATCH"}
#: Display order: the real databases alphabetically, then the synthetic one last. TAFENG joins
#: the real ones; it is the eighth database and the only one besides the synthetic on which an
#: I-extension is legal.
DS_ORDER = ["BIBLE", "BMS1_SPMF", "FIFA", "KOSARAK", "LEVIATHAN", "SIGN", "TAFENG", "C8T1S5I8N5K"]
DS_TEX = {"BMS1_SPMF": "BMS1", "C8T1S5I8N5K": "SYN", "TAFENG": "Ta-Feng"}

#: Arm name (as written in the CSVs) of the algorithm the paper presents since decision (B),
#: 2026-09-09: the configuration without the EUCS pre-filter. Displayed as "HAUSP-UB";
#: the legacy arm "HAUSP-UB" (with EUCS) is displayed as HAUSP-UB_EUCS and appears only
#: in the attribution table.
PAPER_UB = "HAUSP-UB[noEUCS]"
PAPER_UB_L1L3 = "HAUSP-UB[noL2+noEUCS]"
PAPER_UB_L1L2 = "HAUSP-UB[noL3+noEUCS]"
#: Baselines are displayed under the name of the bound they implement (author decision 2026-09-10):
#: the published EHAUSM uses BiUB/AMUB, not this bound, so calling the re-implementations EHAUSM
#: would attribute them to that paper. CSV arm labels never change.
ARM_DISPLAY = {PAPER_UB: "HAUSP-UB", "HAUSP-UB": r"HAUSP-UB$_{\mathrm{EUCS}}$",
               "EHAUSM-R": "APEAU-R", "EHAUSM-I": "APEAU-I",
               # The Layer-1-only arm keeps the EUCS co-occurrence pre-filter, unlike every other
               # HAUSP-UB variant the paper reports. Author decision 2026-09-17: say so in the
               # label rather than re-measure it, which would cost about 37 hours and could only
               # confirm the same verdicts -- without the pre-filter the search is larger, and the
               # arm already exceeds the time limit with it.
               "HAUSP-UB-L1": r"HAUSP-UB$^{L1}_{\mathrm{EUCS}}$", PAPER_UB_L1L3: r"HAUSP-UB$^{L1L3}$",
               PAPER_UB_L1L2: r"HAUSP-UB$^{L1L2}$", "HAUSP-UB-L1L3": r"HAUSP-UB$^{L1L3}_{\mathrm{EUCS}}$",
               # HAUSP-UB* was produced by HAUSP_UB_IAUUB, removed 2026-09-17. The name stays
               # because both legacy trees still hold its rows; nothing runs it any more.
               "HAUSP-UB*": r"HAUSP-UB$^{L1L2}_{\mathrm{EUCS}}$"}

#: Columns that exist only in the new schema; filled with NaN when absent.
NEW_COLUMNS = ["Recursed", "ArmOrder", "Schedule", "PoolBytes", "FlatBytes", "EucsBytes", "AudulRootBytes",
               "RescanTriggered", "BufferUtil", "BufferTested", "SafetyBound", "PrunedL3Node", "PrunedL1Root",
               "MemMode", "MemLive(MB)", "MemRetained(MB)", "GcForced", "RunID"]

_RUN_ID_RE = re.compile(r"run_id=(\S+)")


class MergeRefused(RuntimeError):
    """Raised when two runs with different arm sets would be merged."""


def ds_tex(ds: str) -> str:
    return DS_TEX.get(ds, ds)


def provenance_lines(path: Path) -> list[str]:
    """The ``#`` lines at the top or inside a CSV (one per JVM that wrote to it)."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                out.append(line.rstrip("\n"))
    return out


def read_results(path: Path | str) -> pd.DataFrame:
    """Read one result CSV of either schema; never invents values."""
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        first = fh.readline()
        # Column header is the first non-comment line.
        while first.startswith("#"):
            first = fh.readline()
    original_cols = [c.strip() for c in first.rstrip("\n").split(",")]
    df = pd.read_csv(path, comment="#")
    df.columns = [c.strip() for c in df.columns]
    legacy = "Recursed" not in original_cols
    for c in NEW_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    if "RunIndex" not in df.columns:
        df["RunIndex"] = 0
    df["Legacy"] = legacy
    try:
        rel = str(path.resolve().relative_to(ROOT))
    except ValueError:
        rel = str(path)
    df["SourceFile"] = rel
    run_ids = []
    for line in provenance_lines(path):
        m = _RUN_ID_RE.search(line)
        if m and m.group(1) not in run_ids:
            run_ids.append(m.group(1))
    if legacy and df["RunID"].isna().all():
        df["RunID"] = "legacy"
    df.attrs["run_ids"] = run_ids
    df.attrs["source"] = rel
    df.attrs["legacy"] = legacy
    return df


def read_optional(path: Path | str) -> pd.DataFrame | None:
    path = Path(path)
    return read_results(path) if path.exists() and path.stat().st_size > 0 else None


def count_identity_ok() -> tuple[bool, str]:
    """Whether verify_count_identity.py has confirmed the identities the tables rely on.

    Required: (a) legacy ``Cand`` of the HAUSP-UB arms equals the new
    ``Recursed`` (so RecursedUnified may be read from legacy files) and (b)
    legacy ``Cand`` of the baselines equals the new ``Cand``. The legacy
    "Cand + PrunedL2 + PrunedL3" formula for lists assembled is NOT relied on:
    the re-run showed it overstates the count by the node-level share of the
    Layer-3 prunes, which legacy files do not record separately.
    """
    if not COUNT_IDENTITY_JSON.exists():
        return False, f"{COUNT_IDENTITY_JSON.relative_to(ROOT)} missing: run analysis/verify_count_identity.py first"
    info = json.loads(COUNT_IDENTITY_JSON.read_text())
    if info.get("rows_compared", 0) == 0:
        return False, "count identity was checked on 0 rows (vacuous); not accepted"
    if not (info.get("recursed_identity_holds") and info.get("baseline_cand_identity_holds")):
        return False, f"count identity FAILED on {info.get('checked_at')}: {info.get('summary')}"
    return True, (f"recursed/baseline identities verified on {info['rows_compared']} rows ({info.get('checked_at')}); "
                  f"legacy Cand formula holds: {info.get('cand_formula_holds')}")


def add_unified_counts(df: pd.DataFrame, require_identity: bool = True) -> pd.DataFrame:
    """Add CandUnified / RecursedUnified / CountSource; see module docstring.

    Legacy HAUSP-UB rows get ``CandUnified = NaN`` unless a counts re-run
    overlay (``overlay_counts``) supplies the value: the legacy files cannot
    yield "lists assembled" exactly.
    """
    df = df.copy()
    is_ub = df["Algorithm"].astype(str).str.startswith("HAUSP-UB")
    legacy = df["Legacy"].astype(bool)
    if require_identity and (legacy & is_ub).any():
        ok, why = count_identity_ok()
        if not ok:
            raise RuntimeError("legacy HAUSP-UB rows present but the count identity is not verified: " + why)
    cand = df["Cand"].astype(float)
    l2 = df["PrunedL2(IAUUB)"].astype(float)
    df["CandUnified"] = np.where(legacy & is_ub, np.nan, cand)
    df["RecursedUnified"] = np.where(is_ub, np.where(legacy, cand, df["Recursed"].astype(float)), cand - l2)
    df["CountSource"] = np.where(legacy & is_ub, "none", df["SourceFile"].astype(str))
    return df


COUNT_KEY = ["Dataset", "Algorithm", "BatchID", "MinUtil", "DeltaRatio"]


def overlay_counts(df: pd.DataFrame, counts: pd.DataFrame | None) -> pd.DataFrame:
    """Fill CandUnified of legacy HAUSP-UB rows from a counts-only re-run.

    ``counts`` is a new-schema frame of the same experiment (typically a
    single-trial run under results-2026-09/counts/). Only the deterministic
    columns are taken; timings stay with the legacy rows. The HAUSP count of
    the two rows must agree, otherwise the overlay is refused.
    """
    if counts is None or df is None or "CandUnified" not in df.columns:
        return df
    df = df.copy()
    c = counts[counts["Status"].isin(OK)].copy()
    c = c[c["RunIndex"] == c.groupby(COUNT_KEY)["RunIndex"].transform("min")]
    c["MinUtil"] = c["MinUtil"].round(6)
    c["DeltaRatio"] = c["DeltaRatio"].round(3)
    lookup = c.set_index(COUNT_KEY)
    need = df["CandUnified"].isna() & df["Status"].isin(OK)
    filled = 0
    for idx in df.index[need]:
        r = df.loc[idx]
        key = (r["Dataset"], r["Algorithm"], int(r["BatchID"]), round(float(r["MinUtil"]), 6), round(float(r["DeltaRatio"]), 3))
        if key in lookup.index:
            src = lookup.loc[key]
            if isinstance(src, pd.DataFrame):
                src = src.iloc[0]
            if int(src["HAUSP"]) != int(r["HAUSP"]):
                raise RuntimeError(f"counts overlay refused for {key}: HAUSP differs ({src['HAUSP']} vs {r['HAUSP']})")
            df.at[idx, "CandUnified"] = float(src["Cand"])
            df.at[idx, "RecursedUnified"] = float(src["Recursed"])
            df.at[idx, "CountSource"] = str(src["SourceFile"])
            filled += 1
    df.attrs["counts_overlay_filled"] = filled
    return df


#: The generation ladder for timing artifacts, oldest first. Every reader of a generation
#: order takes it from here. Three copies of this order existed by hand -- the merge in
#: load_experiment, the provenance bookkeeping beside it, and a second list in
#: verify_tables -- and adding a generation meant remembering all three; four generations
#: were once added to the first and to neither of the others. The second element of each
#: pair says whether that generation is HAUSP-UB-only, which one of them is by design.
TIMING_LADDER: list[tuple[Path, bool]] = [
    (OLD_RESULTS, False), (NEW_RESULTS, False), (NEWER_RESULTS, True), (NEWEST_RESULTS, False),
    (SIXTH_RESULTS, False), (SEVENTH_RESULTS, False), (EIGHTH_RESULTS, False),
    (NINTH_RESULTS, False), (ELEVENTH_RESULTS, False), (TWELFTH_RESULTS, False),
    (FOURTEENTH_RESULTS, False),
]


#: The memory-generation ladder, NEWEST first, as load_memory walks it. A hand-kept tuple lived
#: inside load_memory and a second, oldest-first copy in verify_tables; the copy had already missed
#: the tenth generation when the thirteenth was added. Both now read this one list.
MEMORY_LADDER: list[Path] = []  # filled below, once every generation constant is defined


MEMORY_LADDER[:] = [FOURTEENTH_RESULTS / "mem", MEM_THIRTEENTH_RESULTS / "mem", MEM_TENTH_RESULTS / "mem", EIGHTH_RESULTS / "mem",
                    MEM_NEWEST_RESULTS / "mem", NEWEST_RESULTS / "mem", MEM_RESULTS]


def memory_ladder_names() -> list[str]:
    """Memory trees relative to ROOT, OLDEST first (the order verify_tables layers them in)."""
    out = []
    for d in reversed(MEMORY_LADDER):
        try:
            out.append(str(d.relative_to(ROOT)))
        except ValueError:          # an override pointing outside the repository
            out.append(str(d))
    return out


def ladder_names() -> list[str]:
    """Directory names of the timing ladder, oldest first."""
    return [d.name for d, _ in TIMING_LADDER]


def _cond_key(df: pd.DataFrame) -> pd.Series:
    """Measurement condition: dataset x threshold x batch schedule (x schedule label).

    Experiment 6 has its own narrow schema (no DeltaRatio/Algorithm); missing
    parts of the key are left empty.
    """
    key = df["Dataset"].astype(str) + "|" + df["MinUtil"].round(6).astype(str) + "|"
    if "DeltaRatio" in df.columns:
        key = key + df["DeltaRatio"].round(3).astype(str)
    # Legacy files have no Schedule column; their equal-batch rows must match the
    # new files' explicit "equal" label, so the empty label is normalised to "equal".
    sched = df["Schedule"].fillna("").astype(str).replace("", "equal") if "Schedule" in df.columns else "equal"
    key = key + "|" + sched
    return key


def merge_runs(old: pd.DataFrame | None, new: pd.DataFrame | None,
               replace_arms: tuple[str, ...] = (),
               drop_arms_from_old: tuple[str, ...] = ()) -> tuple[pd.DataFrame, list[dict]]:
    """Merge a legacy run with a re-run, condition by condition.

    For every measurement condition present in ``new`` (dataset, threshold,
    batch schedule):

    * if the arm set of ``new`` equals the arm set of ``old`` for that
      condition, all old rows of the condition are replaced by all new rows
      (all trials of the new run, none of the old: trials are never mixed);
    * if the new arms are a subset of ``replace_arms`` (an arm re-measured on
      its own by design, e.g. the corrected HAUSP-UB-L1), only those arms are
      replaced;
    * otherwise the merge is refused (``MergeRefused``): a run with a
      different arm set is a different measurement condition and must not
      overwrite cells silently.

    ``drop_arms_from_old`` removes arms from the legacy file everywhere, even
    where no re-run exists (used for the old HAUSP-UB-L1 rows, which were not
    L1 measurements at all).

    Returns the merged frame and a per-condition provenance list.
    """
    prov: list[dict] = []
    if old is None and new is None:
        return pd.DataFrame(), prov
    if old is None:
        for c, g in new.groupby(_cond_key(new)):
            prov.append({"condition": c, "arms": sorted(g["Algorithm"].unique()) if "Algorithm" in g.columns else ["all"], "from": new.attrs.get("source"),
                         "run_ids": new.attrs.get("run_ids")})
        return new.copy(), prov
    old = old.copy()
    if drop_arms_from_old:
        old = old[~old["Algorithm"].isin(drop_arms_from_old)]
    if new is None:
        for c, g in old.groupby(_cond_key(old)):
            prov.append({"condition": c, "arms": sorted(g["Algorithm"].unique()) if "Algorithm" in g.columns else ["all"], "from": old.attrs.get("source"),
                         "run_ids": old.attrs.get("run_ids") or ["legacy"]})
        return old, prov
    if "Algorithm" not in old.columns:
        old["Algorithm"] = "all"
    if "Algorithm" not in new.columns:
        new = new.assign(Algorithm="all")
    old_key = _cond_key(old)
    new_key = _cond_key(new)
    keep_old = pd.Series(True, index=old.index)
    take_new = pd.Series(False, index=new.index)
    for cond in sorted(new_key.unique()):
        arms_new = set(new.loc[new_key == cond, "Algorithm"].unique())
        arms_old = set(old.loc[old_key == cond, "Algorithm"].unique())
        # Arms declared in replace_arms are re-measured by design and were dropped from
        # the legacy file; they do not count when comparing the two arm sets.
        core_new = arms_new - set(replace_arms)
        core_old = arms_old - set(replace_arms)
        if (core_new == core_old and core_new) or not arms_old:
            keep_old &= ~(old_key == cond)
            take_new |= (new_key == cond)
            prov.append({"condition": cond, "arms": sorted(arms_new), "from": new.attrs.get("source"),
                         "run_ids": new.attrs.get("run_ids"), "mode": "replaced-all"})
        elif arms_new <= set(replace_arms):
            keep_old &= ~((old_key == cond) & old["Algorithm"].isin(arms_new))
            take_new |= (new_key == cond)
            prov.append({"condition": cond, "arms": sorted(arms_new), "from": new.attrs.get("source"),
                         "run_ids": new.attrs.get("run_ids"), "mode": "replaced-arms",
                         "kept_from_old": sorted(arms_old - arms_new)})
        else:
            raise MergeRefused(
                f"refusing to merge condition {cond}: new run measured arms {sorted(arms_new)} "
                f"but the legacy run measured {sorted(arms_old)}; arm sets differ and the new arms are not "
                f"declared in replace_arms={sorted(replace_arms)} (handbook 2.3.16)")
    old_rows = old[keep_old]
    for cond in sorted(_cond_key(old_rows).unique()):
        g = old_rows[_cond_key(old_rows) == cond]
        prov.append({"condition": cond, "arms": sorted(g["Algorithm"].unique()), "from": old.attrs.get("source"),
                     "run_ids": old.attrs.get("run_ids") or ["legacy"], "mode": "legacy"})
    merged = pd.concat([old_rows, new[take_new]], ignore_index=True)
    merged.attrs["sources"] = [old.attrs.get("source"), new.attrs.get("source")]
    return merged, prov


def source_comment(frames: list[pd.DataFrame]) -> str:
    """``% source: file run_id=...`` line for a generated LaTeX table."""
    parts = []
    if not [f for f in frames if f is not None]:
        return "% source: (no result file for this table yet)"
    for f in frames:
        if f is None:
            continue
        src = f.attrs.get("source") or ";".join(str(s) for s in f.attrs.get("sources", []) if s)
        rids = f.attrs.get("run_ids") or []
        if not rids and "RunID" in f.columns:
            rids = [r for r in f["RunID"].dropna().astype(str).unique().tolist() if r]
        parts.append(f"{src} run_id={','.join(rids) if rids else 'legacy'}")
    return "% source: " + " | ".join(parts)


def fmt_sig(v: float, sig: int = 3) -> str:
    """Format with ``sig`` significant figures (handbook 3.3.13); no trailing dot."""
    if v is None or not np.isfinite(v):
        return "--"
    if v == 0:
        return "0"
    digits = sig - int(math.floor(math.log10(abs(v)))) - 1
    r = round(v, digits)
    if digits <= 0:
        return f"{int(r):,}".replace(",", "{,}")
    return f"{r:.{digits}f}"


def human(v) -> str:
    """Compact count: 951 -> 951; 36,287 -> 36.3K; 286,090,251 -> 286.1M."""
    v = float(v)
    if not np.isfinite(v):
        return "--"
    for cut, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= cut:
            return f"{v / cut:.1f}{suf}"
    return f"{v:.0f}"


def ms_std(vals, scale=1.0, nd=1) -> str:
    """Mean and standard deviation of a cell, at enough precision to recompute ratios.

    A cell printed to a fixed number of decimals loses the precision a reader needs when
    the value is small: 0.13 and 1.36 both printed at one decimal give a ratio of 14
    where the measurement says 10.4. Small values therefore carry extra decimals.
    """
    vals = [float(v) for v in vals]
    m = np.mean(vals) / scale
    s = (np.std(vals, ddof=1) / scale) if len(vals) > 1 else 0.0
    nd = nd + (1 if abs(m) < 10 else 0) + (1 if abs(m) < 1 else 0)
    if s < 0.5 * 10 ** (-nd):
        return f"{m:.{nd}f}"
    return f"{m:.{nd}f} $\\pm$ {s:.{nd}f}"


# --------------------------------------------------------------------------- experiment config
CONFIG_JSON = ANALYSIS_OUT / "experiment_config.json"


def load_config() -> dict:
    """ExperimentConfig as JSON: dumped from the built jar when possible, else the cached copy.

    Every threshold, schedule and sweep printed in a table comes from here, never
    from a literal in an analysis script.
    """
    import subprocess
    jars = [j for j in (ROOT / "build").glob("incremental-hausp-mining-*.jar") if not j.name.startswith("original-")]
    if jars:
        # A jar older than the sources describes configuration that is no longer declared, and
        # this function OVERWRITES the cached dump with what it reads. On 2026-09-22 it wrote an
        # eight-database list back over a freshly dumped three-database one, and the coverage
        # check then reported five cells as never run. Every threshold, schedule and sweep in
        # every table comes from here, so a stale read is a silent change to published numbers.
        newest = max((f.stat().st_mtime for f in list((ROOT / "src").rglob("*.java"))
                      + [ROOT / "pom.xml"] if f.exists()), default=0)
        if newest > jars[0].stat().st_mtime:
            raise RuntimeError(
                "%s is older than the sources, and reading it would overwrite the cached "
                "configuration with one that is no longer declared. Rebuild first: "
                "mvn -q package -DskipTests" % jars[0].relative_to(ROOT))
        try:
            out = subprocess.run(["java", "-jar", str(jars[0]), "--dump-config", "json"],
                                 capture_output=True, text=True, check=True, cwd=ROOT).stdout
            cfg = json.loads(out)
            ANALYSIS_OUT.mkdir(parents=True, exist_ok=True)
            CONFIG_JSON.write_text(json.dumps(cfg, indent=1))
            return cfg
        except Exception as e:  # noqa: BLE001
            print(f"warning: could not dump config from {jars[0].name} ({e}); using cached copy")
    if CONFIG_JSON.exists():
        return json.loads(CONFIG_JSON.read_text())
    raise SystemExit("no jar under build/ and no cached experiment_config.json; build the project first")


# --------------------------------------------------------------------------- merge policy
#: How each experiment's legacy CSV and its 2026-09 re-run are combined.
#: ``replace_arms``: arms that may be re-measured on their own (their legacy rows are invalid);
#: ``drop_old``: arms removed from the legacy file everywhere (old HAUSP-UB-L1 was an L1L3 measurement).
NEW_ARMS = (PAPER_UB, PAPER_UB_L1L3, PAPER_UB_L1L2)
MERGE_POLICY = {
    1: dict(file="exp1/experiment1_tightness.csv", replace_arms=("HAUSP-UB-L1",) + NEW_ARMS, drop_old=("HAUSP-UB-L1",)),
    2: dict(file="exp2/experiment2_pruning_power.csv", replace_arms=("HAUSP-UB-L1",) + NEW_ARMS, drop_old=("HAUSP-UB-L1",)),
    3: dict(file="exp3/experiment3_scalability.csv", replace_arms=NEW_ARMS, drop_old=()),
    4: dict(file="exp4/experiment4_memory_prelarge.csv", replace_arms=("HAUSP-UB-L1",) + NEW_ARMS, drop_old=("HAUSP-UB-L1",)),
    5: dict(file="exp5/experiment5_accuracy.csv", replace_arms=("EHAUSM-R",) + NEW_ARMS, drop_old=()),
    6: dict(file="exp6/experiment6_multibatch_accuracy.csv", replace_arms=(), drop_old=()),
    7: dict(file="exp7/experiment7_long_batch.csv", replace_arms=NEW_ARMS, drop_old=()),
    8: dict(file="exp8/experiment8_threshold_sensitivity.csv", replace_arms=NEW_ARMS, drop_old=()),
    9: dict(file="exp9/experiment9_attribution.csv", replace_arms=(), drop_old=()),
    10: dict(file="exp10/experiment10_prelarge_mu.csv", replace_arms=(), drop_old=()),
    11: dict(file="exp11/experiment11_warm_start.csv", replace_arms=NEW_ARMS, drop_old=()),
}


def load_experiment(exp: int, unified: bool = True) -> pd.DataFrame | None:
    """Merged data of one experiment (legacy ``results/`` + ``results-2026-09/``).

    Returns None when neither file exists. ``df.attrs['provenance']`` lists,
    per measurement condition, which file and run ids the rows came from.
    """
    pol = MERGE_POLICY[exp]
    old = read_optional(OLD_RESULTS / pol["file"])
    new = read_optional(NEW_RESULTS / pol["file"])
    if old is None and new is None:
        return None
    df, prov = merge_runs(old, new, replace_arms=pol["replace_arms"], drop_arms_from_old=pol["drop_old"])
    # Later generations are layered on in order; each replaces exactly the arms it carries,
    # for the conditions it carries (merge_runs' "replaced-arms" branch), and leaves the rest.
    # Read from TIMING_LADDER, so the merge, the provenance bookkeeping at the end of this
    # function and verify_tables' check all walk one list. They used to be separate lists and
    # they drifted: four generations were added to the merge and to none of the bookkeeping,
    # so every table generated from this frame carried a "% source:" line naming files that
    # did not hold its newest rows -- a traceability tag pointing away from its measurement.
    # The first two rungs are the base merge above, so the layering starts at the third.
    generations = [(d, read_optional(d / pol["file"]), ub_only)
                   for d, ub_only in TIMING_LADDER[2:]]
    for gen_dir, gen_df, ub_only in generations:
        if gen_df is None:
            continue
        arms_here = tuple(sorted(gen_df["Algorithm"].unique())) if "Algorithm" in gen_df.columns else ()
        if ub_only:
            # generation 3 re-measured the HAUSP-UB arms only; a baseline row in it would mean the
            # file was written by a run that is not what that generation is for.
            foreign = [a_ for a_ in arms_here if not str(a_).startswith("HAUSP-UB")]
            if foreign:
                raise MergeRefused(f"{gen_dir.name}/{pol['file']} carries non-HAUSP-UB arms {foreign}; "
                                   "generation 3 is HAUSP-UB-only")
        df.attrs["source"] = ";".join(x for x in df.attrs.get("sources", []) if x) or (
            new.attrs.get("source") if new is not None else old.attrs.get("source"))
        df, prov_gen = merge_runs(df, gen_df, replace_arms=arms_here)
        prov = [p_ for p_ in prov if p_.get("mode") != "legacy"] + prov_gen
    counts = read_optional(COUNTS_RESULTS / pol["file"])
    if unified and "Cand" in df.columns and "Algorithm" in df.columns:
        df = add_unified_counts(df)
        df = overlay_counts(df, counts)
        if counts is not None:
            prov.append({"condition": "*", "arms": sorted(counts["Algorithm"].unique()), "from": counts.attrs.get("source"),
                         "run_ids": counts.attrs.get("run_ids"), "mode": "counts-overlay",
                         "cells_filled": int(df.attrs.get("counts_overlay_filled", 0))})
    df.attrs["provenance"] = prov
    contributing = [old, new] + [g[1] for g in generations] + [counts]  # same order as TIMING_LADDER
    df.attrs["sources"] = [s for s in (f.attrs.get("source") if f is not None else None
                                       for f in contributing) if s]
    rids = []
    for f in contributing:
        if f is not None:
            for r in (f.attrs.get("run_ids") or (["legacy"] if f.attrs.get("legacy") else [])):
                if r not in rids:
                    rids.append(r)
    df.attrs["run_ids"] = rids
    df.attrs["source"] = ";".join(df.attrs["sources"])
    return df


def load_memory(exp: int, keep_failures: bool = False) -> pd.DataFrame | None:
    """Live-heap memory run of one experiment (results-2026-09/mem/, MemMode=live).

    These rows come from dedicated runs with a forced full collection every
    second and one JVM per arm; their runtimes include the collections and are
    never used for timing. Returns None when the run does not exist.
    """
    pol = MERGE_POLICY[exp]
    frames, sources = [], []
    # Newest generation first, each replacing the arms it carries: results-2026-09d/mem was measured
    # after the per-extension buffers stopped being sized by the identifier space (2026-09-15), and
    # results-2026-09c/mem after the layout work of 2026-09-14.
    # Newest first. The eighth generation holds Ta-Feng's memory rows; it carries one database,
    # so it adds a row rather than replacing any.
    for d in MEMORY_LADDER:
        df = read_optional(d / pol["file"])
        if df is None:
            continue
        # An arm that timed out never reached the live sampler, so its row carries MemMode=used
        # and no heap value. Dropping it loses the verdict: Ta-Feng's L1 arm timed out inside the
        # memory campaign, and the table printed "--" for it while every other database printed
        # OT@0 borrowed from the timing campaign. keep_failures lets a caller that wants the
        # verdict see those rows; it stays off by default, because a caller computing a mean over
        # heap values must not see them.
        keep = df["MemMode"].astype(str) == "live"
        if keep_failures:
            keep = keep | ~df["Status"].astype(str).isin(OK)
        live = df[keep].copy()
        if not len(live):
            continue
        live.attrs.update(df.attrs)
        frames.append(live)
        sources.append(df.attrs.get("source"))
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    # Keep the newest row of each (arm, dataset, threshold, INCREMENT SIZE, schedule, batch,
    # trial). DeltaRatio and Schedule belong in this key: Experiment 3 measures the same arm,
    # database, threshold, batch and trial at four increment sizes, and Experiments 7 and 11 at
    # two schedules. Without them all four collapsed to one key and three were discarded, which
    # is why the update-memory column of the Experiment 3 table printed "--" in all 32 of its
    # rows -- the only surviving rows were the delta=5% ones, and that table reports delta=20%.
    key = (out["Algorithm"].astype(str) + "|" + out["Dataset"].astype(str) + "|"
           + out["MinUtil"].round(6).astype(str) + "|"
           + out["DeltaRatio"].astype(float).round(3).astype(str) + "|"
           + out.get("Schedule", pd.Series([""] * len(out))).astype(str) + "|"
           + out["BatchID"].astype(str) + "|" + out["RunIndex"].astype(str))
    out = out.assign(_k=key).drop_duplicates("_k", keep="first").drop(columns="_k")
    out.attrs["source"] = ";".join(x for x in sources if x)
    return out


# ---------------------------------------------------------------------------------------
# The per-batch time limit, and the cap each OT verdict was actually taken under.
#
# An OT row says "this batch did not finish inside the cap", so what it claims depends on
# a number the row does not carry: the cap sits in the cmd= part of a provenance line, and
# the row carries only its RunID. These helpers are the one path from a row to its cap and
# from the launchers to the limit the paper states, so that the audit and the quantity
# export read both the same way.
# ---------------------------------------------------------------------------------------
_CAP_RE = re.compile(r"--timeout\s+(\d+)")
_RID_RE = re.compile(r"\brun_id=(\S+)")
_NO_RID = {"", "nan", "None", "legacy"}


def run_caps() -> dict[str, set[int]]:
    """run id -> per-batch caps (minutes) its provenance lines record.

    Scans the trees of TIMING_LADDER, the only trees a row surviving the merge can come
    from. Every line is read, not only the first: a file two runs appended to carries a
    provenance line for each, the second one in the middle of the data.
    """
    out: dict[str, set[int]] = {}
    for d, _ in TIMING_LADDER:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.csv")):
            try:
                with p.open(encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if not line.startswith("#"):
                            continue
                        r, c = _RID_RE.search(line), _CAP_RE.search(line)
                        if r and c:
                            out.setdefault(r.group(1), set()).add(int(c.group(1)))
            except OSError:
                continue
    return out


def declared_time_limit() -> tuple[int | None, list[str]]:
    """The per-batch time limit the launchers apply, read from the launchers.

    The shell launchers default ALGO_TIMEOUT_MIN and pass it as --timeout; the limit is
    defined only when every such default agrees. A launcher that starts the JAR without
    --timeout falls back to the timeout each experiment declares, and that fallback must
    not be BELOW the limit: an OT recorded under a smaller cap would claim less than the
    limit the manuscript states. A fallback at or above it only strengthens the verdict,
    because a batch that did not finish inside a longer cap did not finish inside a
    shorter one. Returns (limit or None, notes worth printing).
    """
    notes: list[str] = []
    defaults: dict[str, set[int]] = {}
    no_flag: list[str] = []
    for p in sorted((ROOT / "scripts").iterdir()):
        if p.suffix not in (".sh", ".bat", ".ps1", ".cmd") or not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "-jar" not in text:
            continue
        found = {int(x) for x in re.findall(r"ALGO_TIMEOUT_MIN:-(\d+)", text)}
        if found:
            defaults[p.name] = found
        elif "--timeout" not in text:
            no_flag.append(p.name)
    values = set().union(*defaults.values()) if defaults else set()
    if len(values) != 1:
        return None, [f"launcher defaults disagree or are absent: "
                      f"{ {k: sorted(v) for k, v in defaults.items()} }"]
    limit = values.pop()
    if no_flag:
        specs = load_config().get("experiments", [])
        fallback = [int(e["timeout_minutes"]) for e in specs if e.get("timeout_minutes") is not None]
        low = min(fallback) if fallback else None
        notes.append(f"{', '.join(no_flag)} start the JAR without --timeout, so each experiment's "
                     f"own limit applies there (lowest {low} min)")
        if low is None or low < limit:
            return None, notes + [f"that fallback is below the {limit}-minute limit, so an OT "
                                  f"recorded through it would claim less than the limit stated"]
    return limit, notes


def surviving_ot_cells(exp: int, arms: set[str] | None = None) -> pd.DataFrame:
    """OT rows of one experiment that survive the merge, each with the caps of its run.

    Adds a 'Caps' column (a sorted tuple of minutes, empty when the run id resolves to no
    cap or the row has no run id). ``arms`` restricts to the arms some spec still declares,
    since pre-rename arm names survive in old artifacts and no table reads them.
    """
    df = load_experiment(exp)
    if df is None or "Status" not in df.columns or "Algorithm" not in df.columns:
        return pd.DataFrame()
    sub = df[df["Status"] == "OT"].copy()
    if arms is not None:
        sub = sub[sub["Algorithm"].isin(arms)]
    caps = run_caps()
    rid = sub["RunID"].astype(str).str.strip() if "RunID" in sub.columns else pd.Series("", index=sub.index)
    sub["Caps"] = [tuple(sorted(caps.get(r, ()))) if r not in _NO_RID else () for r in rid]
    return sub


def exp3_update_live_heap(delta_ratio: float) -> dict[tuple[str, str], list[float]]:
    """Peak live heap of the update batch in Experiment 3, one value per trial.

    For each (dataset, arm): the rows of the dedicated live-heap runs at this increment
    size and batch 1, reduced to the maximum sample of each trial. This is the quantity
    the Experiment 3 table prints (as mean and spread over trials) and the one the prose
    must quote. It lives here, not in either reader, because the quantity export had a
    helper that looked similar and was not the same: live_heap() takes the maximum over
    every row, which for Experiment 3 mixes all increment sizes and the initial batch.
    """
    mem = load_memory(3)
    if mem is None or mem.empty:
        return {}
    sel = mem[mem["Status"].isin(OK) & (mem["DeltaRatio"].round(3) == round(float(delta_ratio), 3))
              & (mem["BatchID"] == 1)]
    out: dict[tuple[str, str], list[float]] = {}
    for (ds, arm), g in sel.groupby(["Dataset", "Algorithm"]):
        out[(str(ds), str(arm))] = [float(g[g["RunIndex"] == r]["MemLive(MB)"].max())
                                    for r in sorted(g["RunIndex"].unique())]
    return out
