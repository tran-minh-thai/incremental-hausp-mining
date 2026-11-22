#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fault injection for analysis/analyze_end_to_end.py.

Copies a fixture CSV of experiment 16 (default: the probe results-probe/e2e-analysis-test, three repetitions of four
arms on one cell plus the search-organization study), applies one fault at a time, and requires the analysis to exit 1
naming the fault, while the unchanged fixture exits 0. Each fault must change exactly the rows it targets (checked).

Usage: python3 analysis/check_analyze_end_to_end.py [--fixture CSV]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "results-probe" / "e2e-analysis-test" / "exp16" / "experiment16_end_to_end.csv"


def rows_of(lines):
    hdr = next(l for l in lines if l.startswith("dataset,")).split(",")
    return hdr, [i for i, l in enumerate(lines) if l and not l.startswith("#") and not l.startswith("dataset,")]


def fault(lines, name):
    hdr, idx = rows_of(lines)
    col = {k: i for i, k in enumerate(hdr)}
    out, changed = list(lines), 0

    def edit(i, **kv):
        nonlocal changed
        f = out[i].split(",")
        for k, v in kv.items():
            f[col[k]] = v(f[col[k]]) if callable(v) else v
        out[i] = ",".join(f)
        changed += 1

    def find(**kv):
        return [i for i in idx if all(lines[i].split(",")[col[k]] == v for k, v in kv.items())]

    if name == "patterns_differ_across_arms":
        edit(find(arm="Remine-PEAU", rep="1", batch="2", schedule="L5")[0], patterns=lambda v: str(int(v) + 1))
    elif name == "candidates_differ_same_search":
        for i in find(arm="HAUSP-UB[noEUCS]", batch="3", schedule="L5"):
            edit(i, candidates=lambda v: str(int(v) + 1))
    elif name == "deterministic_differs_across_reps":
        edit(find(arm="HAUSP-UB[rebuild+noEUCS]", rep="2", batch="1")[0], appended=lambda v: str(int(v) - 1))
    elif name == "bad_status":
        edit(find(arm="HAUSP-UB[noEUCS]", rep="1", batch="0", schedule="L5")[0], status="CRASH")
    elif name == "unregistered_xi":
        for i in find(arm="Remine-PEAU", schedule="ALL"):
            edit(i, xi="0.0015")
    elif name == "duplicate_row":
        i = find(arm="HAUSP-UB[prefix+noEUCS]", rep="3", batch="4")[0]
        out.insert(i + 1, lines[i])
        changed += 1
    elif name == "prefix_slower_than_rebuild":
        for i in find(arm="HAUSP-UB[prefix+noEUCS]"):
            edit(i, search_ns=lambda v: str(int(v) * 3))
    elif name == "missing_repetition":
        drop = set(find(arm="HAUSP-UB[noEUCS]", rep="2", schedule="L5"))
        changed += len(drop)
        out = [l for i, l in enumerate(out) if i not in drop]
    elif name == "modified_tree":
        for i, l in enumerate(out):
            if l.startswith("#") and "tree=clean" in l:
                out[i] = l.replace("tree=clean", "tree=MODIFIED")
                changed += 1
                break
    elif name == "measured_code_differs":
        for i, l in enumerate(out):
            if l.startswith("#") and "git=" in l:
                out[i] = __import__("re").sub(r"git=\w+", "git=d1f682a", l)   # an older commit whose src/ differs
                changed += 1
                break
    return out, changed


# the phrase of the analysis problem each fault must produce (so an exit 1 for another reason does not count)
EXPECT = {"patterns_differ_across_arms": "pattern counts differ", "candidates_differ_same_search": "same-search group",
          "deterministic_differs_across_reps": "deterministic columns differ", "bad_status": "neither OK nor OT",
          "unregistered_xi": "not a registered threshold", "duplicate_row": "two rows",
          "prefix_slower_than_rebuild": "code does not allow it", "missing_repetition": "have a gap",
          "modified_tree": "not one clean environment", "measured_code_differs": "measured code differs"}

FAULTS = ("patterns_differ_across_arms", "candidates_differ_same_search", "deterministic_differs_across_reps",
          "bad_status", "unregistered_xi", "duplicate_row", "prefix_slower_than_rebuild", "missing_repetition",
          "modified_tree", "measured_code_differs")


def run(csv: Path):
    p = subprocess.run([sys.executable, "-B", "-E", str(ROOT / "analysis" / "analyze_end_to_end.py"), "--csv", str(csv)],
                       capture_output=True, text=True)
    return p.returncode, p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    a = ap.parse_args()
    base = a.fixture.read_text().split("\n")
    ok = True
    with tempfile.TemporaryDirectory(dir=ROOT / "results-probe") as t:
        d = Path(t)
        f = d / "experiment16_end_to_end.csv"
        f.write_text("\n".join(base))
        rc, _ = run(f)
        print(f"unchanged fixture: exit {rc} (want 0)")
        ok &= rc == 0
        for name in FAULTS:
            lines, changed = fault(base, name)
            f.write_text("\n".join(lines))
            rc, out = run(f)
            named = EXPECT[name] in out
            others = [l for l in out.splitlines() if l.startswith("- ") and EXPECT[name] not in l]
            caught = rc == 1 and named
            print(f"{name}: rows changed {changed}, exit {rc}, {'caught' if caught else 'NOT CAUGHT'}"
                  f"{'' if named else ' (problem not named)'}; other problems {len(others)}")
            ok &= caught and changed > 0
    print("verdict", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
