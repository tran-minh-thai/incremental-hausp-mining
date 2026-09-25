#!/usr/bin/env python3
"""Which cells the manuscript prints are no longer what the current code measures -- and the
commands that re-measure them.

A cell is one (experiment, quantity, dataset, arm). It is STALE when any of these holds:

1. legacy -- its rows carry no run id. They come from the tree that predates the provenance line,
   so neither the code nor the machine that produced them is known.
2. code -- the quantity depends on code that changed after the run. The changes are listed in
   FIXES. They were established on 2026-09-25 by compiling the sources of every commit that
   produced a surviving row with -g:none and comparing each class file with HEAD; every other
   difference found that way was read and shown not to reach a measured quantity (a pattern-dump
   file name, argument checks, output gated behind --profile-phases, comment-only edits).
   A run includes a fix exactly when the fix commit is an ancestor of the commit the run records.
3. protocol -- the manuscript states one JVM per arm for every memory number and for the runtime
   of Experiment 9. A run whose command carries no --algo ran every arm in one JVM.
4. missing -- the quantity covers the (dataset, arm) but no row of it exists, so the table prints
   a dash or falls back to another campaign. Checked per (dataset, arm), not per condition.

Memory is read with its failure rows: a printed memory table carries OT/OOM verdicts, and the
default reader drops them, which hid three stale verdicts from the first version of this tool.

A stale runtime cell is re-measured together with every other arm of the same experiment and
dataset, in one campaign: a runtime comparison assembled from two campaigns compares two
different things (cross-campaign repeatability reached 28% on Experiment 7 cells, 2026-09-12).
Memory and count cells are re-measured arm by arm: counts are deterministic, and live heap after
a forced full collection reproduced exactly across campaigns (re-mining arm on SYN, 171 MB,
2026-09-25 -- one control, so this is a working assumption the next campaign re-tests).

    python3 analysis/stale_cells.py                         # report; exit 1 if any printed cell is stale
    python3 analysis/stale_cells.py --emit leviathan,sign,tafeng --out scripts/x.sh --results-dir results-2026-09l
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PAPER_UB, ROOT, load_config, load_experiment, load_memory  # noqa: E402

#: Quantities the manuscript prints, per experiment: every table it \input's, every figure it
#: includes, every quantity paper/tools reads from quantities.json. Kinds: "time" (runtime and the
#: OT/OOM verdicts beside it), "mem" (live heap and its verdicts), "count" (counts printed with no
#: runtime). Counts inside timing rows need no kind of their own: the code changes below alter
#: memory layout and timing, not pruning (lists, recursions and patterns of the paper arm on SYN
#: matched exactly across them, 4 of 4 cells, 2026-09-25); only rules 1 and the SYN change reach them.
PRINTED = {
    ("time", 1), ("time", 2), ("time", 3), ("time", 7), ("time", 9), ("time", 10), ("time", 11),
    # No runtime of Experiments 4 and 8 is printed, but the variability table pools the
    # trial-to-trial spread of both timing campaigns.
    ("time", 4), ("time", 8),
    ("mem", 3), ("mem", 4), ("mem", 7), ("mem", 9), ("mem", 11),
    # Exactness table, part (b): pattern counts at every batch of a five-batch schedule.
    ("count", 6),
}
#: Quantities that pool whatever rows exist, so a (dataset, arm) without rows is not a gap.
POOLED = {("time", 4), ("time", 8)}
#: Experiments whose runner measures its arms together and writes one row labelled "all".
TOGETHER = {6}

#: Code changes after which earlier runs no longer describe the current code.
FIXES = [
    {"what": "HAUSP-UB memory layout and timing",
     "commit": "6eca0e3",           # last of 59a8a6e, ffee0fc, e376ec7, 819f4c6, fad8423, 6eca0e3
     "arms": lambda arm: arm.startswith("HAUSP-UB"), "datasets": None, "experiments": None,
     "kinds": {"time", "mem"},
     "why": "EUCS no longer built for noEUCS arms (09-08), per-node timers removed (09-10), per-depth "
            "scratch arrays and EUCS caches re-sized (09-14), per-extension buffers sized by the "
            "promising set and started empty (09-15)"},
    {"what": "SYN batch composition",
     "commit": "098b4a9",
     "arms": lambda arm: True, "datasets": {"C8T1S5I8N5K"}, "experiments": {1, 4, 6, 9, 11},
     "kinds": {"time", "mem", "count"},
     "why": "the parser now drops the empty last line of the SYN file; parsing with both versions "
            "shows the five-batch and warm-start schedules shift by one sequence, the others do not"},
]

#: Launch flags per experiment and quantity, copied from the per-arm campaigns that produced the
#: current rows (results-2026-09c for timing, results-2026-09d/k for memory).
PROTOCOL = {
    ("time", 1): "--repeats 3 --repeats-min-seconds 10",
    ("time", 2): "--repeats 3",
    ("time", 3): "--repeats 3 --repeats-min-seconds 10",
    ("time", 9): "--repeats 3",
    ("time", 4): "--repeats 3",                  # the legacy campaign: 3 trials per configuration
    ("time", 8): "--repeats 3",                  # results-2026-09, run 20260909-1603
    ("count", 6): "--repeats 1",                 # results-2026-09, run 20260909-1517
    ("mem", 3): "--repeats 3 --mem-mode live",
    ("mem", 4): "--repeats 3 --mem-mode live",   # results-2026-09d/mem, per arm
    ("mem", 9): "--repeats 1 --mem-mode live",
    ("mem", 11): "--k 100 --repeats 1 --mem-mode live",
    ("mem", 7): "--k 100 --repeats 1 --mem-mode live",   # results-2026-09d/mem, run 20260916-1900
}
#: The manuscript's own statements of one JVM per arm.
PER_ARM_STATED = {("mem", 3), ("mem", 4), ("mem", 9), ("mem", 11), ("time", 9)}

DATASET_KEY = {"BIBLE": "bible", "BMS1_SPMF": "bms1_spmf", "FIFA": "fifa", "KOSARAK": "kosarak",
               "LEVIATHAN": "leviathan", "SIGN": "sign", "TAFENG": "tafeng",
               "C8T1S5I8N5K": "syn_c8t1s5i8n5k"}
NO_RID = {"", "nan", "None", "legacy"}


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _provenance() -> tuple[dict, dict]:
    """run id -> (commit, command) from every provenance line of every timing tree."""
    commits, cmds = {}, {}
    for tree in sorted(p for p in ROOT.iterdir() if p.is_dir() and p.name.startswith("results")
                       and not p.name.startswith("results-probe")):
        for f in tree.rglob("*.csv"):
            with f.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if not line.startswith("#"):
                        continue
                    r, g, c = (re.search(p, line) for p in (r"run_id=(\S+)", r"git=(\w+)", r"cmd=(.*)$"))
                    if r:
                        commits[r.group(1)] = g.group(1) if g else None
                        cmds[r.group(1)] = c.group(1).strip() if c else ""
    return commits, cmds


def _current(commit: str, pmap: dict) -> str | None:
    if commit and _git("cat-file", "-e", commit + "^{commit}").returncode == 0:
        return commit
    entry = pmap.get(commit)
    return entry["current"] if entry else None


def arms_of(kind: str, exp: int, cfg: dict) -> list[str]:
    """The arms whose rows of this quantity the manuscript prints."""
    if exp in TOGETHER:
        return ["all"]
    if (kind, exp) == ("mem", 7):
        return [PAPER_UB]              # one probe: the paper arm's live heap at the largest K
    arms = list(cfg[exp]["algorithms"])
    if (kind, exp) == ("time", 4):
        # Its only printed verdict, the Layer-1-only arm's time-out, is a fallback of the memory
        # table for datasets the memory campaign never ran it on -- rule 4 asks for those rows
        # instead, so re-timing this arm here would spend 90 minutes per dataset on nothing printed.
        arms.remove("HAUSP-UB-L1")
    return arms


def datasets_of(kind: str, exp: int, cfg: dict) -> list[str]:
    if (kind, exp) == ("mem", 7):
        return ["FIFA"]
    return [r["csv_name"] for r in cfg[exp]["runs"]]


def cells() -> pd.DataFrame:
    cfg = {e["id"]: e for e in load_config()["experiments"]}
    commits, cmds = _provenance()
    pmap = json.loads((ROOT / "provenance_map.json").read_text())["commits"]
    has_fix: dict = {}
    rows = []
    for kind, exp in sorted(PRINTED):
        df = load_memory(exp, keep_failures=True) if kind == "mem" else load_experiment(exp)
        if df is None:
            df = pd.DataFrame(columns=["Dataset", "Algorithm", "RunID", "tTotal(ms)"])
        arms = arms_of(kind, exp, cfg)
        df = df[df["Algorithm"].isin(arms)]
        if (kind, exp) not in POOLED:
            seen = set(zip(df["Dataset"], df["Algorithm"]))
            for ds in datasets_of(kind, exp, cfg):
                for arm in arms:
                    if (ds, arm) not in seen:
                        rows.append({"kind": kind, "exp": exp, "dataset": ds, "arm": arm,
                                     "cpu_min": float("nan"), "stale": True,
                                     "reasons": "missing: no row of this quantity was ever measured"})
        for (ds, arm), g in df.groupby(["Dataset", "Algorithm"]):
            reasons = set()
            for rid in sorted(set(g["RunID"].astype(str))) if "RunID" in g.columns else ["legacy"]:
                if rid in NO_RID:
                    reasons.add("legacy: no run id, code and machine unknown"); continue
                cur = _current(commits.get(rid), pmap)
                if cur is None:
                    reasons.add(f"run {rid}: recorded commit not found"); continue
                for fx in FIXES:
                    if not fx["arms"](arm) or kind not in fx["kinds"]:
                        continue
                    if fx["datasets"] is not None and ds not in fx["datasets"]:
                        continue
                    if fx["experiments"] is not None and exp not in fx["experiments"]:
                        continue
                    key = (fx["commit"], cur)
                    if key not in has_fix:
                        has_fix[key] = _git("merge-base", "--is-ancestor", fx["commit"], cur).returncode == 0
                    if not has_fix[key]:
                        reasons.add(f"code: run {rid} predates the {fx['what']} change ({fx['commit']})")
                if (kind, exp) in PER_ARM_STATED and "--algo" not in cmds.get(rid, ""):
                    reasons.add(f"protocol: run {rid} ran every arm in one JVM; the manuscript states one JVM per arm")
            # Experiment 6 records no runtime column: its cost is unknown, not zero.
            cpu = g["tTotal(ms)"].sum() / 60000 if "tTotal(ms)" in g.columns else float("nan")
            rows.append({"kind": kind, "exp": exp, "dataset": ds, "arm": arm, "cpu_min": cpu,
                         "stale": bool(reasons), "reasons": "; ".join(sorted(reasons))})
    return pd.DataFrame(rows)


def emit(c: pd.DataFrame, datasets: list[str], out: Path, results_dir: str) -> int:
    """Write the per-arm commands that re-measure every stale (experiment, quantity, dataset)."""
    cfg = {e["id"]: e for e in load_config()["experiments"]}
    want = {d.upper() if d.upper() in DATASET_KEY else next(k for k, v in DATASET_KEY.items() if v == d)
            for d in datasets}
    groups = c[c.stale & c.dataset.isin(want)].groupby(["kind", "exp"])["dataset"].apply(lambda s: sorted(set(s)))
    # Cheapest group first, and a group never measured before (no cost on record) ahead of all:
    # a command shape that fails should fail in minutes, not after the long groups have run.
    known = c[c.dataset.isin(want)].groupby(["kind", "exp"])["cpu_min"].sum(min_count=1)
    groups = groups.loc[sorted(groups.index, key=lambda k: (0 if pd.isna(known.get(k)) else 1,
                                                           0.0 if pd.isna(known.get(k)) else known[k]))]
    stale = c[c.stale & c.dataset.isin(want)]
    lines, missing = [], []
    for (kind, exp), dss in groups.items():
        if (kind, exp) not in PROTOCOL:
            missing.append(f"{kind} exp{exp} on {dss}: no protocol recorded -- decide it before measuring")
            continue
        rd = results_dir + ("/mem" if kind == "mem" else "")
        if exp in TOGETHER:
            plan = [(",".join(cfg[exp]["algorithms"]), dss)]
        elif kind == "time":
            plan = [(arm, dss) for arm in arms_of(kind, exp, cfg)]
        else:
            # Memory and counts arm by arm (module docstring): only the stale (dataset, arm) pairs.
            sub = stale[(stale.kind == kind) & (stale.exp == exp)]
            plan = [(arm, sorted(set(sub[sub.arm == arm].dataset)))
                    for arm in arms_of(kind, exp, cfg) if (sub.arm == arm).any()]
        for arm, dsl in plan:
            # --resume, as every original per-arm campaign carried: re-running this file after an
            # interruption skips the cells already recorded instead of measuring them twice.
            lines.append(f'./scripts/run.sh {exp} --dataset {",".join(DATASET_KEY[d] for d in dsl)} '
                         f'--algo "{arm}" {PROTOCOL[(kind, exp)]} --results-dir {rd} --resume')
    body = ["#!/usr/bin/env bash",
            "# Generated by analysis/stale_cells.py -- do not edit by hand; regenerate instead.",
            f"# Datasets: {', '.join(sorted(want))}. One JVM per arm; run.sh supplies heap, cap and caffeinate.",
            "# Stops at the first failing command, so a partial campaign is visible rather than silent;",
            "# every command carries --resume, so running this file again continues where it stopped.",
            "# DRY_RUN=1 prints the commands instead of running them.",
            "set -euo pipefail", 'cd "$(dirname "$0")/.."',
            'run() { if [ -n "${DRY_RUN:-}" ]; then printf "%q " "$@"; echo; else "$@"; fi; }',
            'echo "[batch] $(date) start at $(git rev-parse --short HEAD)"']
    for i, ln in enumerate(lines):
        body.append(f"echo '[batch] {i + 1}/{len(lines)}: {ln[16:].replace(chr(39), '')}'")
        body.append("run " + ln)
    body += ['echo "[batch] $(date) done"']
    out.write_text("\n".join(body) + "\n")
    out.chmod(0o755)
    print(f"wrote {out}: {len(lines)} command(s)")
    for m in missing:
        print("NOT EMITTED:", m)
    return 1 if missing else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", help="comma-separated dataset keys to generate commands for")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--results-dir", default="results-2026-09l")
    ap.add_argument("--csv", type=Path, help="also write the full cell table here")
    a = ap.parse_args()
    c = cells()
    if a.csv:
        c.to_csv(a.csv, index=False)
    st = c[c.stale]
    print(f"stale_cells: {len(c)} printed cells examined, {len(st)} stale "
          f"({len(c) - len(st)} current)")
    if len(st):
        by = st.groupby(["kind", "exp"]).size()
        print("  stale by quantity: " + ", ".join(f"{k} exp{e}={n}" for (k, e), n in by.items()))
        grp = c[c.stale].groupby(["kind", "exp", "dataset"]).size().reset_index()[["kind", "exp", "dataset"]]
        cost = c.merge(grp, on=["kind", "exp", "dataset"]).groupby("dataset")["cpu_min"].sum() / 60
        print("  CPU hours to re-measure (all arms of each stale group, as originally measured;"
              " time-outs and missing cells not included):")
        for ds, h in cost.sort_values().items():
            print(f"    {ds:12} {h:6.2f}")
        miss = st[st.reasons.str.startswith("missing")]
        if len(miss):
            print(f"  missing (never measured, no cost on record): {len(miss)} -- "
                  + ", ".join(f"{k} exp{e} {d} {a}" for k, e, d, a in
                              miss[["kind", "exp", "dataset", "arm"]].itertuples(index=False)))
    if a.emit:
        return emit(c, a.emit.split(","), a.out, a.results_dir)
    return 1 if len(st) else 0


if __name__ == "__main__":
    sys.exit(main())
