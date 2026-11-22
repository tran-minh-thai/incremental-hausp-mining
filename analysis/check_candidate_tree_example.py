#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The candidate-tree arm (src/main/java/CandidateTreeAU.java) on the running example of IncUSP-Miner+.

Wang and Huang, "On Incremental High Utility Sequential Pattern Mining", ACM TIST 9(5), article 55, 2018. Table 1
(p. 4) is the database D, its increment and the external utilities; the arm runs in the paper's own setting (one
absolute min_util, utility, no length factor). Every value below is stated in the paper; pages are the journal's.

One stated value is not reproduced, on purpose: LMTSU(<a>) at min_util = 50. Example 4.1 (p. 13) and Figure 3(b)
(p. 14) give 21, the TSU of the i-extension [a, e], "the only low TSU extension sequence" of <a>. The s-extension
<a, b> is also low: it occurs in s1 only, with utility 15 + 24 = 39 and nothing after it, so its TSU is 39 (and by
Lemma 3 it cannot be below its utility). LMTSU is defined over all extension sequences, and Strategy 3 needs LMTSU at
least the TSU of every low extension; the arm keeps 39. The check expects 39 and prints the paper's 21 beside it.

Items a..f are 1..6; positions are 1-based as in the paper.

Usage: python3 analysis/check_candidate_tree_example.py [--fault]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
A, B, C, D, E = 1, 2, 3, 4, 5

# (min_util, phase, pattern, sid) -> stated values; phase 0 is D, phase 1 is D'
STATED = [
    # Figure 3(a), p. 14: TSU of tree nodes, min_util = 50
    (50, 0, "<(1)>", 2, {"tsu": 109}, "Fig. 3(a)"),
    (50, 0, "<(2)>", 1, {"tsu": 72}, "Fig. 3(a)"),
    (50, 0, "<(3)>", 2, {"tsu": 82}, "Fig. 3(a)"),
    (50, 0, "<(1,2)>", 1, {"tsu": 53}, "Fig. 3(a)"),
    (50, 0, "<(1,3)>", 2, {"tsu": 50}, "Fig. 3(a)"),
    (50, 0, "<(1)(1)>", 2, {"tsu": 50}, "Fig. 3(a)"),
    (50, 0, "<(1)(4)>", 1, {"tsu": 70}, "Fig. 3(a)"),
    (50, 0, "<(1)(5)>", 2, {"tsu": 50}, "Fig. 3(a)"),
    (50, 0, "<(2)(4)>", 1, {"tsu": 72}, "Fig. 3(a)"),
    (50, 0, "<(1,2)(2)>", 1, {"tsu": 51}, "Fig. 3(a)"),
    (50, 0, "<(1,3)(1)>", 2, {"tsu": 50}, "Fig. 3(a)"),
    (50, 0, "<(1,3)(1,3)>", 2, {"tsu": 50}, "Fig. 3(a)"),
    # Figure 3(b), p. 14: the node of <a>
    (50, 0, "<(1)>", 1, {"peu": 103, "u": 48, "seq_peu": 53, "seq_u": 15, "pos": "1:15"}, "Fig. 3(b)"),
    (50, 0, "<(1)>", 2, {"seq_peu": 29, "seq_u": 12, "pos": "1:3;2:12"}, "Fig. 3(b)"),
    (50, 0, "<(1)>", 3, {"seq_peu": 0, "seq_u": 6, "pos": "4:6"}, "Fig. 3(b)"),
    (50, 0, "<(1)>", 5, {"seq_peu": 21, "seq_u": 15, "pos": "1:3;2:15"}, "Fig. 3(b)"),
    # text
    (50, 0, "<(3)>", 2, {"seq_tsu": 26}, "p. 8, after Definition 15"),
    (50, 0, "<(1)(3)>", 2, {"seq_peu": 19, "peu": 24, "u": 18, "seq_u": 14}, "pp. 4 and 8"),
    (50, 0, "<(1,3)(3)>", 2, {"seq_tsu": 29, "tsu": 50}, "Example 3.1, p. 8; p. 10 after Definition 17"),
    (50, 0, "<(1)(4)>", 1, {"seq_tsu": 41}, "Example 3.1, p. 9"),
    (15, 0, "<(1,5)>", 5, {"tsu": 21}, "Example 4.1, p. 13 (low at min_util 50, so read at 15)"),
    (15, 0, "<(1)(3)(3)(5)>", 5, {"tsu": 16}, "Example 4.3, p. 16"),
    (15, 1, "<(1)(3)>", 5, {"u": 30, "seq_u": 16}, "Example 4.2, p. 16"),
]
# not reproduced on purpose (see the module docstring): the arm must give 39, the paper states 21
LMTSU_A = (50, 0, "<(1)>", 2, {"lmtsu": 39}, "Example 4.1, p. 13 states 21")

LINE = re.compile(r"phase (\d) (\S+) u (-?\d+) peu (-?\d+) tsu (-?\d+) lmtsu (-?\d+) sid (\d+)"
                  r"(?: u (-?\d+) peu (-?\d+) tsu (-?\d+) pos (\S*)| absent)")


def run(min_util, queries):
    args = [str(min_util)]
    for phase in (0, 1):
        if phase:
            args.append("--")
        args += [f"{pat}:{sid}" for (ph, pat, sid) in queries if ph == phase]
    p = subprocess.run(["java", "-cp", str(JAR), "CandidateTreeAU", *args], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"CandidateTreeAU failed:\n{p.stderr[-2000:]}")
    out = {}
    for m in LINE.finditer(p.stdout):
        g = m.groups()
        v = {"u": int(g[2]), "peu": int(g[3]), "tsu": int(g[4]), "lmtsu": int(g[5])}
        if g[7] is not None:
            v.update(seq_u=int(g[7]), seq_peu=int(g[8]), seq_tsu=int(g[9]), pos=g[10])
        out[(int(g[0]), g[1], int(g[6]))] = v
    absent = [l for l in p.stdout.splitlines() if l.endswith(" absent") and " sid " not in l]
    return out, absent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", action="store_true", help="add 1 to one stated value; the check must then fail")
    a = ap.parse_args()
    stated = [list(x) for x in STATED + [LMTSU_A]]
    if a.fault:
        stated[0][4] = {"tsu": 110}
    compared = 0
    bad = []
    for mu in sorted({x[0] for x in stated}):
        rows = [x for x in stated if x[0] == mu]
        got, absent = run(mu, [(x[1], x[2], x[3]) for x in rows])
        for mu_, ph, pat, sid, want, where in rows:
            g = got.get((ph, pat, sid))
            if g is None:
                bad.append(f"min_util {mu} phase {ph} {pat}: not in the tree ({where})")
                continue
            for k, v in want.items():
                compared += 1
                if g.get(k) != v:
                    bad.append(f"min_util {mu} phase {ph} {pat} sid {sid} {k}: arm {g.get(k)}, paper {v} ({where})")
    for b in bad:
        print("MISMATCH " + b)
    print(f"values compared: {compared}; mismatches: {len(bad)}; LMTSU(<a>) at min_util 50: the arm must give 39, "
          f"the paper states 21 (see the docstring); verdict {'PASS' if not bad and compared else 'FAIL'}")
    sys.exit(0 if not bad and compared else 1)


if __name__ == "__main__":
    main()
