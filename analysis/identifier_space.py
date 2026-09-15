#!/usr/bin/env python3
"""How much heap the three identifier-indexed structures of HAUSP-UB cost, per dataset.

The widths are not measured at run time; they follow from the allocation rule in
HAUSP_UB.java and from the largest item identifier present in each dataset, so they are
derived here rather than typed into the manuscript. Reference sizes follow the JVM's
compressed-oops setting, which is on for any heap below 32 GB and therefore for the 24 GB
heap the runbook uses.

    globalAUDULs  : AUDUL[]  of maxItemIdEver + 5000   (object references)
    globalItemSWU : long[]   of maxItemIdEver + 5000   (8 bytes)
    itemToCompact : int[]    of maxItemIdEver + 5000   (4 bytes)

Writes analysis_out/paper/identifier_space.json. Run from the repository root.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402

SLACK = 5000          # HAUSP_UB.java: newSize = maxItemIdEver + 5000
REF_BYTES = 4         # compressed oops, enabled by the JVM below a 32 GB heap
OUT = ROOT / "analysis_out" / "paper" / "identifier_space.json"
NAME = {"C8T1S5I8N5K": "SYN", "BMS1_SPMF": "BMS1"}


ITEM = re.compile(r"(\d+)\[")   # a token is itemID[quantity]; -1 and -2 are separators


def max_item_id(path: Path) -> tuple[int, int]:
    """Largest identifier and number of distinct identifiers in a sequence file.

    Only the part before "[" is an identifier. Matching every integer on the line would
    also catch the bracketed internal utilities and the separators, which is wrong by a
    wide margin on these files. maxItemIdEver in HAUSP_UB.java is taken from the parsed
    items of the database, so this mirrors it and ignores the EUI table.
    """
    mx, seen = 0, set()
    with path.open() as fh:
        for line in fh:
            for tok in ITEM.findall(line):
                v = int(tok)
                seen.add(v)
                if v > mx:
                    mx = v
    return mx, len(seen)


def main() -> int:
    out = {}
    for seq in sorted((ROOT / "datasets").glob("*/*_seq.txt")):
        if seq.parent.name == "example":
            continue
        key = seq.name[: -len("_seq.txt")]
        mx, distinct = max_item_id(seq)
        width = mx + SLACK
        by = width * (REF_BYTES + 8 + 4)
        out[NAME.get(key, key)] = {
            "max_item_id": mx, "distinct_items": distinct,
            "array_width": width, "bytes": by, "mb": round(by / 2**20, 2),
            "density": round(distinct / width, 6),
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True))
    w = max(len(k) for k in out)
    print(f"{'dataset':<{w}}  {'largest id':>12}  {'distinct':>9}  {'MB':>7}  density")
    for k, v in sorted(out.items(), key=lambda kv: -kv[1]["mb"]):
        print(f"{k:<{w}}  {v['max_item_id']:>12,}  {v['distinct_items']:>9,}  {v['mb']:>7.1f}  {v['density']:.4f}")
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
