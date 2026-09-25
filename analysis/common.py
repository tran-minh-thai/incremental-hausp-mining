#!/usr/bin/env python3
"""Shared loaders for the result CSVs of the measurement campaign.

Every number the paper prints comes from one campaign on the declared measurement machine
(MEASUREMENT_MACHINE.txt), written by scripts/campaign.py into ``results/`` -- live-heap runs
into ``results/mem/`` -- one CSV per experiment. Every row carries a RunID, and every file the
``# run_id=...`` provenance lines of the JVMs that wrote it.

Earlier result trees, measured under several generations of code and merged by rules kept here,
were removed on 2026-09-25; they live in the git history up to commit 0231424. Nothing here reads
them any more: a row in the old schema reaching these readers is an error, not a case to handle.

``read_results`` fills a column the file lacks with NaN (never a guessed value) and adds
``SourceFile``; ``add_unified_counts`` derives the two count columns the tables compare:

    CandUnified      lists assembled                 Cand
    RecursedUnified  children recursed into          HAUSP-UB* arms: Recursed
                                                     baselines:      Cand - PrunedL2(IAUUB)
                                                                     (nodes that passed PEAU)
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
#: The measurement campaign: the only tree the readers load. HAUSP_CAMPAIGN_RESULTS points them at
#: another one (a stand-in built from a validation run, a test).
CAMPAIGN_RESULTS = (Path(os.environ["HAUSP_CAMPAIGN_RESULTS"]) if os.environ.get("HAUSP_CAMPAIGN_RESULTS")
                    else ROOT / "results")
ANALYSIS_OUT = ROOT / "analysis_out" / "paper"

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


def add_unified_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Add CandUnified / RecursedUnified / CountSource; see the module docstring."""
    legacy = df[df["Legacy"].astype(bool)]
    if len(legacy):
        raise RuntimeError(f"{len(legacy)} row(s) in the old schema (no Recursed column) in "
                           f"{sorted(set(legacy['SourceFile']))}: the campaign never writes that schema")
    df = df.copy()
    is_ub = df["Algorithm"].astype(str).str.startswith("HAUSP-UB")
    cand = df["Cand"].astype(float)
    df["CandUnified"] = cand
    df["RecursedUnified"] = np.where(is_ub, df["Recursed"].astype(float), cand - df["PrunedL2(IAUUB)"].astype(float))
    df["CountSource"] = df["SourceFile"].astype(str)
    return df


#: Trees the readers load, kept as lists because run_caps, verify_tables and the audit walk them.
#: One campaign now, so one entry each.
TIMING_LADDER: list[tuple[Path, bool]] = [(CAMPAIGN_RESULTS, False)]
MEMORY_LADDER: list[Path] = [CAMPAIGN_RESULTS / "mem"]


def memory_ladder_names() -> list[str]:
    """Memory trees relative to ROOT."""
    out = []
    for d in MEMORY_LADDER:
        try:
            out.append(str(d.relative_to(ROOT)))
        except ValueError:          # an override pointing outside the repository
            out.append(str(d))
    return out


def ladder_names() -> list[str]:
    """Directory names of the timing trees."""
    return [d.name for d, _ in TIMING_LADDER]


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


#: Result file of each experiment, relative to the campaign tree (and to its mem/ subtree).
RESULT_FILE = {
    1: "exp1/experiment1_tightness.csv",
    2: "exp2/experiment2_pruning_power.csv",
    3: "exp3/experiment3_scalability.csv",
    4: "exp4/experiment4_memory_prelarge.csv",
    5: "exp5/experiment5_accuracy.csv",
    6: "exp6/experiment6_multibatch_accuracy.csv",
    7: "exp7/experiment7_long_batch.csv",
    8: "exp8/experiment8_threshold_sensitivity.csv",
    9: "exp9/experiment9_attribution.csv",
    10: "exp10/experiment10_prelarge_mu.csv",
    11: "exp11/experiment11_warm_start.csv",
}


def load_experiment(exp: int, unified: bool = True) -> pd.DataFrame | None:
    """The campaign's rows of one experiment, or None when it has none yet."""
    df = read_optional(CAMPAIGN_RESULTS / RESULT_FILE[exp])
    if df is None:
        return None
    if "Algorithm" not in df.columns:           # Experiment 6 writes its two arms as one row
        df["Algorithm"] = "all"
    if unified and "Cand" in df.columns:
        df = add_unified_counts(df)
    return df


def load_memory(exp: int, keep_failures: bool = False) -> pd.DataFrame | None:
    """Live-heap rows of one experiment (results/mem/, MemMode=live), or None.

    They come from dedicated runs with a forced full collection every second and one JVM per arm;
    their runtimes include the collections and are never used for timing.

    An arm that timed out never reached the live sampler, so its row carries MemMode=used and no
    heap value. keep_failures keeps those rows for a caller that needs the verdict; it stays off
    by default, because a caller computing a statistic over heap values must not see them.
    """
    df = read_optional(CAMPAIGN_RESULTS / "mem" / RESULT_FILE[exp])
    if df is None:
        return None
    keep = df["MemMode"].astype(str) == "live"
    if keep_failures:
        keep = keep | ~df["Status"].astype(str).isin(OK)
    out = df[keep].copy()
    out.attrs.update(df.attrs)
    # One row per (arm, dataset, threshold, increment, schedule, batch, trial). Commands of a
    # campaign are atomic, so a second row under the same key means something wrote twice; it
    # used to be dropped here without a word, which is exactly how such a fault would go unseen.
    key = ["Algorithm", "Dataset", "MinUtil", "DeltaRatio", "Schedule", "BatchID", "RunIndex"]
    k = out[key].astype(str).agg("|".join, axis=1)
    if k.duplicated().any():
        raise RuntimeError(f"{out.attrs.get('source')}: {int(k.duplicated().sum())} memory row(s) repeat a key, "
                           f"e.g. {k[k.duplicated()].iloc[0]}")
    return out if len(out) else None


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
        if p.suffix not in (".sh", ".bat", ".ps1", ".cmd", ".py") or not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "-jar" not in text:
            continue
        # Shell launchers default ALGO_TIMEOUT_MIN; the campaign driver (the measurement
        # machine's entry point) states its limit as a module constant.
        found = {int(x) for x in re.findall(r"ALGO_TIMEOUT_MIN:-(\d+)", text)}
        found |= {int(x) for x in re.findall(r"(?m)^TIMEOUT_MIN\s*=\s*(\d+)", text)}
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
