#!/usr/bin/env python3
"""Measure the dataset characteristics quoted in the paper from the files themselves.

For every dataset declared in ExperimentConfig (read through
``--dump-config json`` or the cached analysis_out/paper/experiment_config.json)
this script parses the QSDB files and records:

    sequences          number of sequences (|D|)
    items              number of distinct item ids in the sequence file (|I|)
    avg_itemsets       mean number of itemsets per sequence
    avg_items          mean number of item occurrences per sequence
    total_utility      sum over all occurrences of quantity x external utility
    sha256_seq/eui     checksums of the two files

Output: analysis_out/paper/dataset_stats.json, consumed by build_latex_tables.py
for the datasets table, so that no characteristic is typed by hand.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ANALYSIS_OUT, ROOT, load_config  # noqa: E402

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_eui(path: Path) -> dict[int, int]:
    eui: dict[int, int] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, v = line.replace(",", ":").split(":", 1)
            eui[int(k)] = int(float(v))
    return eui


def measure(seq_path: Path, eui_path: Path) -> dict:
    eui = read_eui(eui_path)
    n_seq = 0
    n_itemsets = 0
    n_items = 0
    items: set[int] = set()
    total_util = 0
    with open(seq_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # A line with no item is not a sequence: the parser drops it (QSDB_Parser, since the
            # fix that closes every itemset), so counting it here printed a |D| one larger than
            # the database the algorithms mine -- the synthetic file ends with such a line.
            if not any(tok not in ("-1", "-2") for tok in line.split()):
                continue
            n_seq += 1
            for tok in line.split():
                if tok == "-1":
                    n_itemsets += 1
                elif tok == "-2":
                    continue
                else:
                    i, q = tok[:-1].split("[")
                    i, q = int(i), int(q)
                    items.add(i)
                    n_items += 1
                    total_util += q * eui[i]
    return {
        "sequences": n_seq,
        "items": len(items),
        "avg_itemsets": n_itemsets / n_seq if n_seq else 0.0,
        "avg_items": n_items / n_seq if n_seq else 0.0,
        "total_utility": total_util,
        "sha256_seq": sha256(seq_path),
        "sha256_eui": sha256(eui_path),
    }


def main() -> int:
    cfg = load_config()
    stats = {}
    for d in cfg["datasets"]:
        seq, eui = ROOT / d["seq_path"], ROOT / d["eui_path"]
        if not seq.exists():
            print(f"skip {d['name']}: {seq} missing")
            continue
        s = measure(seq, eui)
        s["name"] = d["name"]
        stats[d["csv_name"]] = s
        print(f"{d['csv_name']:>12}: |D|={s['sequences']:>8,} |I|={s['items']:>6,} "
              f"avg itemsets={s['avg_itemsets']:.2f} avg items={s['avg_items']:.2f} total util={s['total_utility']:,}")
    ANALYSIS_OUT.mkdir(parents=True, exist_ok=True)
    out = ANALYSIS_OUT / "dataset_stats.json"
    out.write_text(json.dumps(stats, indent=1))
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
