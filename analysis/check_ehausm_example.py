#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The independent re-miner (EHAUSM_Remining, arm Remine-PEAU) on the running example of the EHAUSM paper.

Truong, Duong, Le, Fournier-Viger, "EHAUSM: An efficient algorithm for high average utility sequence mining",
Information Sciences 515 (2020) 302-323. Table 2 (p. 305) is an integrated database (utility = quantity); the text
states, among others:
  p. 316: for mu = 126 the final set is {d, d -> c, d -> f} with au 156, 126.5 and 138;
  p. 305: au(d -> ac) = 90;
  p. 306: for mu = 75, au(d -> ac -> af) = 79.4.
The miners take a fraction of the total utility (587 here), so mu = 126 is 126/587. The re-miner must give exactly the
set at mu = 126 with its three values, and the two values at mu = 75; the proposed arm must give the same set (the
probe compares it). A changed value fails the check (fault injection: --fault adds 1 to a stated value).

This checks the result set of the re-miner against the paper, not its search: the re-miner prunes with the PEAU bound,
not with the bounds of EHAUSM, so it is named by its behavior (Remine-PEAU), not as EHAUSM.

Usage: python3 analysis/check_ehausm_example.py [--fault]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
TABLE2 = ["(d,80)>(a,5)(c,40)>(a,3)(b,25)(f,3)>(e,1)",
          "(b,35)>(c,2)(e,6)>(d,1)>(d,4)>(g,45)>(a,1)(f,9)",
          "(d,8)>(a,7)(c,35)(e,15)>(d,22)>(a,9)(f,72)",
          "(c,5)(e,6)>(a,3)>(d,50)>(a,5)(c,40)>(a,4)(c,10)(f,36)"]
IDS = {ch: i + 1 for i, ch in enumerate("abcdefg")}
# pattern in the dump notation (item ids, itemsets in parentheses) -> average utility stated in the paper
SET_126 = {"<(4)>": 156.0, "<(4)(3)>": 126.5, "<(4)(6)>": 138.0}
VALUES_75 = {"<(4)(1,3)>": 90.0, "<(4)(1,3)(1,6)>": 79.4}


def write(d: Path) -> int:
    lines, total = [], 0
    for s in TABLE2:
        toks = []
        for iset in s.split(">"):
            for it in iset.strip("()").split(")("):
                n, q = it.split(",")
                toks.append(f"{IDS[n]}[{q}]")
                total += int(q)
            toks.append("-1")
        lines.append(" ".join(toks + ["-2"]))
    (d / "db_seq.txt").write_text("\n".join(lines) + "\n")
    (d / "db_eui.txt").write_text("".join(f"{i}:1\n" for i in range(1, 8)))
    (d / "keys.txt").write_text("1\n2\n3\n4\n")
    (d / "db.properties").write_text(f"dataset.path={d / 'db_seq.txt'}\nminUtil.percentage=0.0\n")
    return total


def mine(d: Path, mu: float, total: int):
    w = d / f"w{mu}"
    w.mkdir()
    p = subprocess.run(["java", "-cp", str(JAR), "ExactnessProbe", str(d / "db_eui.txt"), str(d / "db_seq.txt"),
                        str(d / "db.properties"), repr(mu / total), "4", "@" + str(d / "keys.txt"),
                        "HAUSP-UB[prefix+noEUCS]", "-"], capture_output=True, text=True, cwd=w)
    dump = {}
    for f in (w / "out").glob("EHAUSM_Remining_*"):
        for line in f.read_text().splitlines():
            if line.startswith("<"):
                pat, au, _ = line.split("\t")
                dump[pat] = float(au)
    agree = "only_arm 0 only_reference 0" in p.stdout
    return dump, agree, p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", action="store_true", help="add 1 to one stated value; the check must then fail")
    a = ap.parse_args()
    want126 = dict(SET_126)
    if a.fault:
        want126["<(4)(6)>"] += 1
    ok = True
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        total = write(d)
        print(f"total utility of Table 2: {total}")
        got, agree, rc = mine(d, 126, total)
        same = set(got) == set(want126) and all(abs(got[k] - v) < 0.005 for k, v in want126.items())
        print(f"mu = 126: re-miner {sorted(got.items())}; paper {sorted(want126.items())}; "
              f"{'match' if same else 'MISMATCH'}; proposed arm agrees: {agree} (rc {rc})")
        ok &= same and agree and rc == 0
        got, agree, rc = mine(d, 75, total)
        hits = {k: got.get(k) for k in VALUES_75}
        same = all(got.get(k) is not None and abs(got[k] - v) < 0.005 for k, v in VALUES_75.items())
        print(f"mu = 75: {len(got)} patterns; stated values {hits} vs paper {VALUES_75}; {'match' if same else 'MISMATCH'}; "
              f"proposed arm agrees: {agree} (rc {rc})")
        ok &= same and agree and rc == 0
    print(f"values compared: {len(SET_126) + len(VALUES_75)} plus set equality at mu = 126; verdict {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
