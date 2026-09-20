#!/usr/bin/env python3
"""Compare the mined pattern SETS, not their sizes, and record what was compared.

Experiments 5 and 6 declare a match when the two arms report the same *number* of patterns
(`reminingRes.hauspFound == fullRes.hauspFound`). Two different sets of equal size pass that.
The pattern dumps written under out/ carry the patterns themselves, one per line as
"<(a,b)(c)>\tutility\tsupport", so the stronger statement is available for free: identical
sets, and identical utility for every pattern in them.

It also answers a question no CSV column does. An I-extension -- an item appended inside the
last itemset rather than after it -- is only legal where an itemset holds more than one item,
and six of the seven original databases have exactly 1.00 item per itemset. Counting the mined
patterns whose itemsets are larger than one item is what shows that branch did something on
real data, rather than being reached only by the synthetic database and the unit tests.

    python3 analysis/verify_pattern_sets.py             # exit 1 on any disagreement
    python3 analysis/verify_pattern_sets.py --record    # also write the verdict to results-invariant/

The dumps live under out/, which is not in the repository: they are large and are rewritten by
each campaign. So this check is meaningful only on the machine that just ran one, and --record
is what keeps its finding afterwards. The recorded file holds counts alone, which is why it
belongs in results-invariant/ rather than beside the timings.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
INVARIANT = ROOT / "results-invariant"
#: Longest prefix first, so EHAUSM_Remining is not read as EHAUSM.
ARMS = ["EHAUSM_Remining", "EHAUSM_Inc", "Pre_HUSPM_adapt", "HAUSP"]
ORACLE, UNIFIED, INCREMENTAL = "EHAUSM_Remining", "HAUSP", "EHAUSM_Inc"
LINE = re.compile(r"^(<\(.*\)>)\t([^\t]*)")


def parse_name(name: str):
    """(arm, dataset, batch, mu) from 'EHAUSM_Remining_TAFENG_B4_mu0.0016.txt', or None.

    ``mu`` is None for a dump written before the threshold went into the file name. That
    distinction decides what a disagreement is allowed to mean: experiments 1, 5 and 6 mine the
    same dataset and batch at different thresholds, so two untagged dumps may be two different
    mining runs, and comparing them proves nothing either way.
    """
    m = re.match(r"^(.+?)_B(\d+)(?:_mu([0-9.eE-]+))?\.txt$", name)
    if not m:
        return None
    stem, batch, mu = m.group(1), int(m.group(2)), m.group(3)
    for arm in ARMS:
        if stem.startswith(arm + "_"):
            return arm, stem[len(arm) + 1:], batch, mu
    return None


def load(path: pathlib.Path) -> dict:
    """pattern -> utility as printed. Comment lines are skipped."""
    out = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def itemset_sizes(pattern: str) -> list[int]:
    """Sizes of each itemset of '<(a,b)(c)>'."""
    inner = pattern[2:-2]
    return [len([x for x in g.split(",") if x]) for g in inner.split(")(")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true",
                    help="write the verdict to results-invariant/, since out/ is not kept")
    a = ap.parse_args()

    if not OUT.is_dir():
        print("verify_pattern_sets: ABORT -- %s does not exist. The dumps are written by a "
              "campaign whose experiment has enableIO set, so run one first." % OUT)
        return 2

    dumps: dict = {}
    for p in sorted(OUT.glob("*.txt")):
        key = parse_name(p.name)
        if key:
            dumps[key] = p

    pairs = sorted({(ds, b, mu) for (arm, ds, b, mu) in dumps if arm == ORACLE}
                   & {(ds, b, mu) for (arm, ds, b, mu) in dumps if arm == UNIFIED})
    tagged = [k for k in pairs if k[2] is not None]
    untagged = [k for k in pairs if k[2] is None]
    if not pairs:
        print("verify_pattern_sets: FAIL -- no (dataset, batch) has a dump from both %s and %s, "
              "so nothing could be compared, which is not a pass" % (ORACLE, UNIFIED))
        return 1

    problems, per_dataset = [], collections.defaultdict(
        lambda: {"batches": 0, "patterns": 0, "multi": 0, "max_itemset": 0, "inc_compared": 0})
    total = multi_total = 0
    inconclusive = []
    for ds, b, mu in pairs:
        o = load(dumps[(ORACLE, ds, b, mu)])
        u = load(dumps[(UNIFIED, ds, b, mu)])
        # Where the threshold is not in the name, a disagreement cannot be attributed: it is
        # equally explained by the two files coming from different experiments. Agreement still
        # means something -- two different thresholds do not produce identical sets with
        # identical utilities -- so that half of the evidence is kept.
        strict = mu is not None
        only_o, only_u = set(o) - set(u), set(u) - set(o)
        common = set(o) & set(u)
        valdiff = [k for k in common if o[k] != u[k]]
        d = per_dataset[ds]
        d["batches"] += 1
        d["patterns"] += len(common)
        total += len(common)
        for k in common:
            s = itemset_sizes(k)
            if max(s) > 1:
                d["multi"] += 1
                multi_total += 1
            d["max_itemset"] = max(d["max_itemset"], max(s))
        bucket = problems if strict else inconclusive
        if only_o or only_u or valdiff:
            bucket.append("%s batch %d (mu=%s): %d oracle-only, %d UB-only, %d differing "
                          "utilities%s" % (ds, b, mu, len(only_o), len(only_u), len(valdiff),
                                           "" if strict else " -- the threshold is not in the file "
                                           "name, so this may be two different experiments"))
            for k in sorted(only_o)[:2]:
                bucket.append("    only in the oracle: %s" % k)
            for k in sorted(valdiff)[:2]:
                bucket.append("    %s: utility %s in the oracle, %s in HAUSP-UB" % (k, o[k], u[k]))
        key_i = (INCREMENTAL, ds, b, mu)
        if key_i in dumps:
            i = load(dumps[key_i])
            d["inc_compared"] += len(set(i) & set(u))
            sym = set(i) ^ set(u)
            if sym:
                (problems if strict else inconclusive).append(
                    "%s batch %d (mu=%s): the incremental baseline and HAUSP-UB differ on %d "
                    "pattern(s)%s" % (ds, b, mu, len(sym),
                                      "" if strict else " -- may be two different experiments"))

    print("%-16s %8s %10s %14s %13s %14s"
          % ("dataset", "batches", "patterns", "multi-item", "max itemset", "vs APEAU-I"))
    for ds in sorted(per_dataset):
        d = per_dataset[ds]
        print("%-16s %8d %10d %14d %13d %14d"
              % (ds, d["batches"], d["patterns"], d["multi"], d["max_itemset"], d["inc_compared"]))
    print("\nverify_pattern_sets: compared %d pattern(s) as sets over %d (dataset, batch) pair(s) "
          "-- %d with the threshold in the file name, %d without; %d pattern(s) carry an itemset "
          "of more than one item" % (total, len(pairs), len(tagged), len(untagged), multi_total))
    for line in inconclusive[:20]:
        print("  INCONCLUSIVE  %s" % line)
    if inconclusive:
        print("verify_pattern_sets: %d untagged pair(s) disagree. Run a campaign with the current "
              "build, whose dumps carry the threshold, before reading anything into them."
              % len([x for x in inconclusive if not x.startswith("    ")]))

    if a.record:
        INVARIANT.mkdir(parents=True, exist_ok=True)
        rec = {"written": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
               "note": "counts only; no timing, so this file is machine-independent",
               "pairs_compared": len(pairs), "patterns_compared": total,
               "patterns_with_multi_item_itemset": multi_total,
               "disagreements": len(problems),
               "per_dataset": {k: dict(v) for k, v in sorted(per_dataset.items())}}
        path = INVARIANT / "pattern_set_equality.json"
        path.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("verify_pattern_sets: recorded to %s" % path.relative_to(ROOT))

    if problems:
        for p in problems[:40]:
            print("  MISMATCH  %s" % p)
        print("verify_pattern_sets: FAIL -- %d disagreement(s)" % len(problems))
        return 1
    print("verify_pattern_sets: PASS -- identical pattern sets and identical utility per pattern")
    return 0


if __name__ == "__main__":
    sys.exit(main())
