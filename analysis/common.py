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
DS_ORDER = ["BIBLE", "BMS1_SPMF", "FIFA", "KOSARAK", "LEVIATHAN", "SIGN", "C8T1S5I8N5K"]
DS_TEX = {"BMS1_SPMF": "BMS1", "C8T1S5I8N5K": "SYN"}

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
               "HAUSP-UB-L1": r"HAUSP-UB$^{L1}$", PAPER_UB_L1L3: r"HAUSP-UB$^{L1L3}$",
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
    newer = read_optional(NEWER_RESULTS / pol["file"])
    newest = read_optional(NEWEST_RESULTS / pol["file"])
    for gen_dir, gen_df, ub_only in ((NEWER_RESULTS, newer, True), (NEWEST_RESULTS, newest, False)):
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
    df.attrs["sources"] = [s for s in (old.attrs.get("source") if old is not None else None,
                                       new.attrs.get("source") if new is not None else None,
                                       newer.attrs.get("source") if newer is not None else None,
                                       newest.attrs.get("source") if newest is not None else None,
                                       counts.attrs.get("source") if counts is not None else None) if s]
    rids = []
    for f in (old, new, newer, newest, counts):
        if f is not None:
            for r in (f.attrs.get("run_ids") or (["legacy"] if f.attrs.get("legacy") else [])):
                if r not in rids:
                    rids.append(r)
    df.attrs["run_ids"] = rids
    df.attrs["source"] = ";".join(df.attrs["sources"])
    return df


def load_memory(exp: int) -> pd.DataFrame | None:
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
    for d in (MEM_NEWEST_RESULTS / "mem", NEWEST_RESULTS / "mem", MEM_RESULTS):
        df = read_optional(d / pol["file"])
        if df is None:
            continue
        live = df[df["MemMode"].astype(str) == "live"].copy()
        if not len(live):
            continue
        live.attrs.update(df.attrs)
        frames.append(live)
        sources.append(df.attrs.get("source"))
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    # keep the newest row of each (arm, dataset, threshold, batch, trial)
    key = (out["Algorithm"].astype(str) + "|" + out["Dataset"].astype(str) + "|"
           + out["MinUtil"].round(6).astype(str) + "|" + out["BatchID"].astype(str)
           + "|" + out["RunIndex"].astype(str))
    out = out.assign(_k=key).drop_duplicates("_k", keep="first").drop(columns="_k")
    out.attrs["source"] = ";".join(x for x in sources if x)
    return out
