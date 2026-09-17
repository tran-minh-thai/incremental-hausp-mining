#!/usr/bin/env python3
"""The arms an experiment runs by default must be the arms the paper reports.

The implementation keeps the EUCS co-occurrence pre-filter behind a flag that defaults to
on, so the arm named plainly `HAUSP-UB` is the configuration *with* EUCS. The paper reports
the configuration without it, `HAUSP-UB[noEUCS]`. Those two names differ by one option and
name two different algorithms.

Nothing downstream notices when a run picks the wrong one. The CSV records the arm label
faithfully, the tables look for the paper's arm, find nothing, and print blanks or fall
back to older rows -- after the machine time has already been spent. It happened twice on
2026-09-17: once to a hand-written campaign command, and once to every experiment
declaration in the repository, which had named the EUCS arm since before the author dropped
EUCS from the paper.

This check reads the declarations out of the launcher itself and refuses any HAUSP-UB arm
that does not switch EUCS off, except the ones listed in EXCEPTIONS with a reason.

    python3 analysis/check_arms.py          # exit 1 on an undeclared EUCS-carrying arm
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PAPER_UB  # noqa: E402

#: Arms that carry EUCS and are nevertheless declared, each with the reason it is still so.
#: An entry here is a statement that someone looked; an arm missing from it is an accident.
EXCEPTIONS = {
    "HAUSP-UB-L1": (
        "The Layer-1-only ablation keeps EUCS while the other four HAUSP-UB variants of the paper "
        "do not. Author decision 2026-09-17: the label says so -- the tables display it as "
        "HAUSP-UB^L1_EUCS -- rather than re-measure it. Re-measuring costs about 37 hours, of "
        "which nearly all is spent exhausting the time limit on cells that would time out again: "
        "without the pre-filter the search is larger, and the arm already exceeds the limit with "
        "it. The verdict the paper draws from this arm is therefore conservative.")
}


def declared() -> dict[int, list[str]]:
    jar = next(ROOT.glob("build/incremental-hausp-mining-*.jar"), None)
    if jar is None or "original" in jar.name:
        sys.exit("check_arms: no built jar under build/; run mvn -q package -DskipTests first")
    out = subprocess.run(["java", "-jar", str(jar), "--dump-config", "json"],
                         capture_output=True, text=True, check=True).stdout
    return {e["id"]: e["algorithms"] for e in json.loads(out)["experiments"]}


def main() -> int:
    per_exp = declared()
    arms = sorted({a for v in per_exp.values() for a in v})
    ub = [a for a in arms if a.startswith("HAUSP-UB")]
    offenders = [a for a in ub if "noEUCS" not in a and a not in EXCEPTIONS]
    excepted = [a for a in ub if a in EXCEPTIONS]

    print("check_arms: %d experiments declare %d distinct arms, %d of them HAUSP-UB variants"
          % (len(per_exp), len(arms), len(ub)))
    print("  the paper's algorithm is %s" % PAPER_UB)
    for a in excepted:
        print("  ALLOWED  %s carries EUCS by declared exception" % a)
        print("           %s" % " ".join(EXCEPTIONS[a].split())[:300])
    if offenders:
        for a in offenders:
            where = sorted(e for e, v in per_exp.items() if a in v)
            print("  WRONG ARM  %s keeps the EUCS pre-filter and is declared by experiment(s) %s"
                  % (a, ", ".join(str(e) for e in where)))
        print("check_arms: FAIL -- %d arm(s) would measure an algorithm the paper does not report"
              % len(offenders))
        return 1
    print("check_arms: PASS -- every declared HAUSP-UB arm switches EUCS off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
