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
from common import (PAPER_UB, ROOT, declared_time_limit, load_config, load_experiment,  # noqa: E402
                    load_memory)

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
    ("time", 7): "--k 10,20,50,100 --repeats 3 --repeats-min-seconds 10",   # run 20260912-0034
    ("time", 4): "--repeats 3",                  # the legacy campaign: 3 trials per configuration
    ("time", 8): "--repeats 3",                  # results-2026-09, run 20260909-1603
    ("count", 6): "--repeats 1",                 # results-2026-09, run 20260909-1517
    ("count", 5): "--repeats 1",                 # results-2026-09, run 20260909-1425
    ("time", 10): "--repeats 3",                 # results-2026-09, run 20260905-0344
    ("time", 11): "--k 10,20,50,100 --repeats 3",  # results-2026-09e, runs 20260917-1737/1855/2319
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
#: Outside the repository, so that requesting a stop never dirties the tree a run records.
STOP_FILE = "/tmp/hausp-stop"


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
            ot = int((g["Status"] == "OT").sum()) if "Status" in g.columns else 0
            rows.append({"kind": kind, "exp": exp, "dataset": ds, "arm": arm, "cpu_min": cpu, "ot": ot,
                         "stale": bool(reasons), "reasons": "; ".join(sorted(reasons))})
    return pd.DataFrame(rows)


def emit(c: pd.DataFrame, datasets: list[str], out: Path, results_dir: str) -> int:
    """Write the per-arm commands that re-measure every stale (quantity, experiment, dataset).

    One group per (quantity, experiment, dataset), its arms back to back, so an interruption
    splits at most one comparison. Groups run cheapest first, priced by the CPU and time-out
    waits of the rows they replace; a cell with no row is priced at the median of the same arm
    on the other datasets. Every price is an extrapolation from earlier runs, not a measurement.
    """
    cfg = {e["id"]: e for e in load_config()["experiments"]}
    want = {d.upper() if d.upper() in DATASET_KEY else next(k for k, v in DATASET_KEY.items() if v == d)
            for d in datasets}
    limit, _ = declared_time_limit()
    if limit is None:
        print("emit: REFUSED -- the per-batch time limit cannot be read from the launchers")
        return 1

    def price(kind, exp, ds, arm, cap) -> float:
        r = c[(c.kind == kind) & (c.exp == exp) & (c.dataset == ds) & (c.arm == arm)]
        if len(r) and pd.notna(r.cpu_min.iloc[0]):
            return float(r.cpu_min.iloc[0]) + cap * int(r.ot.iloc[0])
        peers = c[(c.kind == kind) & (c.exp == exp) & (c.arm == arm) & c.cpu_min.notna()]
        return float((peers.cpu_min + limit * peers.ot).median()) if len(peers) else 0.0

    stale = c[c.stale & c.dataset.isin(want)]
    groups, missing = [], []
    for (kind, exp, ds), g in stale.groupby(["kind", "exp", "dataset"]):
        if (kind, exp) not in PROTOCOL:
            missing.append(f"{kind} exp{exp} on {ds}: no protocol recorded -- decide it before measuring")
            continue
        if exp in TOGETHER:
            arms = [("all", ",".join(cfg[exp]["algorithms"]))]
        elif kind == "time":
            arms = [(a, a) for a in arms_of(kind, exp, cfg)]
        else:
            # Memory and counts arm by arm (module docstring): only the stale (dataset, arm) pairs.
            arms = [(a, a) for a in arms_of(kind, exp, cfg) if a in set(g.arm)]
        cmds = []
        for label, algo in arms:
            # Every batch runs under the stated limit, including cells once re-run under a wider
            # cap: those rows are superseded by this campaign (decided 2026-09-25).
            cmds.append((algo, price(kind, exp, ds, label, limit)))
        groups.append((kind, exp, ds, cmds, sum(m for _, m in cmds)))
    groups.sort(key=lambda t: (t[4], t[0], t[1], t[2]))

    body = ["#!/usr/bin/env bash",
            "# Generated by analysis/stale_cells.py -- do not edit by hand; regenerate instead.",
            f"# Datasets: {', '.join(sorted(want))}. One JVM per arm; run.sh supplies heap, cap and caffeinate.",
            "# Groups run cheapest first; each group measures every arm of one comparison back to back.",
            "# Stops at the first failing command, so a partial campaign is visible rather than silent;",
            "# every command carries --resume, so running this file again continues where it stopped.",
            "# Times in [est ...] are extrapolated from earlier runs of the same cells, not measured.",
            "# DRY_RUN=1 prints the commands instead of running them.",
            f"# To stop cleanly: touch {STOP_FILE} -- the command running now finishes, then the file",
            "# exits. Ctrl-C instead abandons a trial half-written, and its re-run appends a second copy",
            "# of the batches already written (the audit reports it; see duplicated_keys).",
            "set -euo pipefail", 'cd "$(dirname "$0")/.."',
            'run() { if [ -n "${DRY_RUN:-}" ]; then printf "%q " "$@"; echo; else "$@"; fi; }',
            f'stop_check() {{ if [ -e {STOP_FILE} ]; then rm -f {STOP_FILE}; '
            'echo "[batch] $(date) stop requested; run this file again to continue"; exit 0; fi; }',
            f"rm -f {STOP_FILE}",
            'echo "[batch] $(date) start at $(git rev-parse --short HEAD)"']
    n = sum(len(t[3]) for t in groups)
    i, cum = 0, 0.0
    for kind, exp, ds, cmds, _ in groups:
        rd = results_dir + ("/mem" if kind == "mem" else "")
        for algo, minutes in cmds:
            i += 1
            cum += minutes
            cmd = (f'./scripts/run.sh {exp} --dataset {DATASET_KEY[ds]} --algo "{algo}" '
                   f'{PROTOCOL[(kind, exp)]} --results-dir {rd} --resume')
            tag = f"{i}/{n} [est {minutes:.0f} min, cumulative {cum / 60:.1f} h] {kind} exp{exp} {ds} {algo}"
            body.append("stop_check")
            safe = "".join(ch for ch in tag if ch not in "\"$`\\")
            body.append(f'echo "[batch] $(date +%H:%M) {safe}"')
            if exp in TOGETHER:
                # This runner has no --resume: a finished dataset is skipped here instead, or a
                # second run would append a second copy of every batch.
                f = f"{rd}/{cfg[exp]['output_subdir']}/{cfg[exp]['log_file']}"
                nb = len(next(r for r in cfg[exp]["runs"] if r["csv_name"] == ds)["batch_ratios"])
                body.append(f'if [ -f {f} ] && [ "$(grep -c "^{ds}," {f})" -ge {nb} ]; then '
                            f"echo '[batch] exp{exp} {ds} already complete in {f}'; else run {cmd}; fi")
            else:
                body.append("run " + cmd)
    body += ['echo "[batch] $(date) done"']
    out.write_text("\n".join(body) + "\n")
    out.chmod(0o755)
    print(f"wrote {out}: {n} command(s), {len(groups)} group(s), estimated {cum / 60:.1f} h")
    for m in missing:
        print("NOT EMITTED:", m)
    return 1 if missing else 0


def duplicated_keys(results_dir: str) -> list[str]:
    """Rows of one file that share (arm, dataset, threshold, increment, schedule, batch, trial).

    A trial interrupted between batches is re-run whole by --resume, which appends a second copy
    of the batches already written; the timing reader does not remove it, so a runtime summed
    per trial would count those batches twice. Experiment 11 is not checked: its four schedules
    share one head batch under one key by design (108 such rows on 2026-09-25).
    """
    root = ROOT / results_dir
    found = []
    for f in sorted(root.rglob("*.csv")) if root.is_dir() else []:
        if "exp11" in f.parts:
            continue
        df = pd.read_csv(f, comment="#")
        cols = [k for k in ("Algorithm", "UBArm", "Dataset", "MinUtil", "mu", "DeltaRatio", "Schedule",
                            "BatchID", "RunIndex", "MemMode") if k in df.columns]
        dup = df.duplicated(cols, keep=False)
        if dup.any():
            found.append(f"{f.relative_to(ROOT)}: {int(dup.sum())} rows share a key, e.g. "
                         + ", ".join(f"{k}={df[dup].iloc[0][k]}" for k in cols))
    return found


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
    dups = duplicated_keys(a.results_dir)
    print(f"  trials written twice in {a.results_dir}: {len(dups)} file(s)")
    for d in dups:
        print("  DUPLICATED", d)
    if a.emit:
        return emit(c, a.emit.split(","), a.out, a.results_dir)
    return 1 if len(st) or dups else 0


if __name__ == "__main__":
    sys.exit(main())
