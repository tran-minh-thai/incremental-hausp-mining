#!/usr/bin/env python3
"""Refuse a timing artifact that came from a machine other than the declared one.

A runtime or a peak-heap figure is a property of the machine as much as of the algorithm, so
the paper is only coherent while every such number comes from one machine. MEASUREMENT_MACHINE.txt
names it. This check is what makes that file more than a note: it reads the declaration and
compares it against the provenance line of every artifact that carries a timing.

    python3 analysis/check_measurement_machine.py           # exit 1 on a foreign host
    python3 analysis/check_measurement_machine.py --list    # print every host seen, with counts

Which trees are covered, and why the split matters:

  results*/            covered -- these carry tTotal(ms) and MemPeak(MB), which are machine-bound
  results-probe*/      NOT covered -- feasibility runs, deliberately allowed anywhere, and no
                       number in the paper may come from them
  results-invariant/   NOT covered -- counts only, determined by commit, data and seed

A file with no provenance line at all is reported separately rather than counted as a pass: the
legacy generation under results/ predates the line, and silence there is a known gap, not a
clean bill of health. The count of files actually compared is printed, because "no foreign
host" over zero comparable files would be no check at all.
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DECLARATION = ROOT / "MEASUREMENT_MACHINE.txt"
HOST = re.compile(r"host=(\S+)")
#: A tree is exempt when its numbers are not machine-bound; the prefix is the whole rule.
EXEMPT_PREFIXES = ("results-probe", "results-invariant")


def declared_host(text: str) -> str | None:
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.lower().startswith("host") and "=" in line:
            return line.split("=", 1)[1].strip()
    return None


def covered(path: pathlib.Path) -> bool:
    top = path.parts[0]
    return top.startswith("results") and not top.startswith(EXEMPT_PREFIXES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if not DECLARATION.exists():
        print("check_measurement_machine: ABORT -- %s does not exist. The measurement machine is "
              "declared by the author, never detected from whatever host is running, so this "
              "check cannot fall back to the current hostname." % DECLARATION)
        return 2
    want = declared_host(DECLARATION.read_text(encoding="utf-8"))
    if not want:
        print("check_measurement_machine: ABORT -- %s has no 'host = ...' line, so nothing is "
              "declared to compare against" % DECLARATION)
        return 2

    seen: collections.Counter = collections.Counter()
    foreign, silent, compared = [], [], 0
    for p in sorted(ROOT.rglob("*.csv")):
        rel = p.relative_to(ROOT)
        if not covered(rel):
            continue
        head = p.open(encoding="utf-8", errors="ignore").readline()
        m = HOST.search(head)
        if not m:
            silent.append(str(rel))
            continue
        compared += 1
        seen[m.group(1)] += 1
        if m.group(1) != want:
            foreign.append((str(rel), m.group(1)))

    print("check_measurement_machine: declared host is %s" % want)
    if a.list:
        for h, n in seen.most_common():
            print("  %-34s %d file(s)" % (h, n))
    print("check_measurement_machine: compared %d timing artifact(s); %d carry no provenance line"
          % (compared, len(silent)))
    for rel in silent:
        print("  NO PROVENANCE  %s" % rel)
    for rel, host in foreign:
        print("  FOREIGN HOST   %s was written on %s" % (rel, host))
    if compared == 0:
        print("check_measurement_machine: FAIL -- nothing was compared, which is not a pass")
        return 1
    if foreign:
        print("check_measurement_machine: FAIL -- %d artifact(s) came from a machine other than "
              "the declared one; their timings are not comparable with the rest" % len(foreign))
        return 1
    print("check_measurement_machine: PASS -- every timing artifact compared names the declared "
          "machine")
    return 0


if __name__ == "__main__":
    sys.exit(main())
