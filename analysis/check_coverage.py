#!/usr/bin/env python3
"""Every (experiment, database) the configuration declares must have measured rows.

Answering "is anything still unrun?" by reading tables is how a count goes wrong. This asks the
configuration what was declared, asks the merged data what exists, and reports the difference,
with the denominator. Nothing here is typed by hand.

    python3 analysis/check_coverage.py          # exit 1 when a declared cell has no rows
    python3 analysis/check_coverage.py --all    # list every cell, not only the gaps

Three outcomes per cell, and they are not the same thing:

  measured   at least one successful row exists
  failed     rows exist and none succeeded -- a timeout is a RESULT, not a gap, and the paper
             prints it as OT@b. Reported separately so it is never counted as missing.
  MISSING    the configuration declares the run and no row exists at all

Experiment 4 is checked against the dedicated live-heap tree as well, because its table reads
that one; a cell measured only for timing would otherwise look complete while the memory table
prints dashes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OK, ROOT, load_experiment, load_memory  # noqa: E402

CONFIG = ROOT / "analysis_out" / "paper" / "experiment_config.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    if not CONFIG.exists():
        print("check_coverage: ABORT -- %s is missing; run the launcher with --dump-config" % CONFIG)
        return 2
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))

    declared = []
    for e in cfg["experiments"]:
        for r in e.get("runs", []):
            declared.append((int(e["id"]), r["csv_name"]))
    if not declared:
        print("check_coverage: FAIL -- the configuration declares no runs at all")
        return 1

    cache: dict = {}
    def rows_for(exp: int, memory: bool = False):
        k = (exp, memory)
        if k not in cache:
            try:
                cache[k] = load_memory(exp) if memory else load_experiment(exp)
            except Exception as ex:                       # a loader that refuses is not a pass
                cache[k] = ex
        return cache[k]

    missing, failed, measured, errors = [], [], 0, []
    for exp, ds in sorted(set(declared)):
        checks = [(exp, False)] + ([(4, True)] if exp == 4 else [])
        state = "MISSING"
        for e, mem in checks:
            df = rows_for(e, mem)
            if isinstance(df, Exception):
                errors.append("experiment %d%s: the loader raised %s" % (e, " (memory)" if mem else "", df))
                continue
            if df is None:
                continue
            sub = df[df["Dataset"] == ds]
            if len(sub) == 0:
                continue
            state = "measured" if sub["Status"].isin(OK).any() else ("failed" if state != "measured" else state)
        if state == "measured":
            measured += 1
        elif state == "failed":
            failed.append((exp, ds))
        else:
            missing.append((exp, ds))
        if a.all:
            print("  %-9s experiment %-3d %s" % (state, exp, ds))

    print("check_coverage: %d declared (experiment, database) cells; %d measured, %d hold only "
          "failed rows, %d have no row at all"
          % (len(set(declared)), measured, len(failed), len(missing)))
    for exp, ds in failed:
        print("  FAILED-ONLY  experiment %-3d %-14s every row is a timeout or a skip -- a result, "
              "not a gap" % (exp, ds))
    for exp, ds in missing:
        print("  MISSING      experiment %-3d %-14s declared in the configuration, no row exists"
              % (exp, ds))
    for e in errors:
        print("  LOADER       %s" % e)
    if errors:
        print("check_coverage: FAIL -- a loader refused, so coverage could not be established")
        return 1
    if missing:
        print("check_coverage: FAIL -- %d declared cell(s) have never been run" % len(missing))
        return 1
    print("check_coverage: PASS -- every declared cell has rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
