#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write the raw-sequence files of a fixed share of the customers of a dataset built by build_raw_sequences.py.

The customers kept are those of analysis/export_maintenance_inputs.py, (key * 2654435761) mod 2^32 < share * 2^32 on
the customer code of <NAME>_rows.npz, so the sample is the same set of customers as the experiment-15 inputs of the
same share. The rows keep their order (customer, basket rank, item) and the item numbering of the full dataset; the
item-profit file is copied. Output: <OUT>/<NAME>_seq.txt, _eui.txt, _rows.npz and _manifest.json (sha256 of the inputs
and outputs, share, counts).

Usage: python3 analysis/sample_raw_sequences.py --raw DIR --name VALUED_SHOPPERS --share 0.5 --out-name VALUED_SHOPPERS_S2 --out DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--share", type=float, required=True)
    ap.add_argument("--out-name", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    src = a.raw / f"{a.name}_rows.npz"
    z = np.load(src)
    cols = {k: z[k] for k in ("cust", "day", "basket", "item", "util")}
    keep = (cols["cust"].astype(np.uint64) * np.uint64(2654435761)) % np.uint64(2 ** 32) < np.uint64(int(a.share * 2 ** 32))
    cols = {k: v[keep] for k, v in cols.items()}
    c, b = cols["cust"], cols["basket"]
    new_c = np.r_[True, c[1:] != c[:-1]]
    new_b = new_c | np.r_[True, b[1:] != b[:-1]]
    if (np.diff(c.astype(np.int64)) < 0).any():
        raise SystemExit("refusing: rows are not grouped by customer")
    a.out.mkdir(parents=True, exist_ok=True)
    seq = a.out / f"{a.out_name}_seq.txt"
    items = cols["item"].astype(np.int64) + 1
    utils = cols["util"]
    with open(seq, "w", newline="\n") as fh:
        out = []
        for r in range(len(c)):
            if new_c[r] and r:
                out.append(" -1 -2\n")
            elif new_b[r] and r:
                out.append(" -1 ")
            elif r:
                out.append(" ")
            out.append(f"{items[r]}[{utils[r]}]")
            if len(out) > 200_000:
                fh.write("".join(out))
                out = []
        if len(c):
            out.append(" -1 -2\n")
        fh.write("".join(out))
    eui = a.out / f"{a.out_name}_eui.txt"
    eui.write_bytes((a.raw / f"{a.name}_eui.txt").read_bytes())
    npz = a.out / f"{a.out_name}_rows.npz"
    np.savez(npz, **cols)
    man = {"source_rows": src.name, "source_sha256": sha256(src), "share": a.share,
           "rule": "(cust * 2654435761) mod 2^32 < share * 2^32", "rows": int(len(c)),
           "sequences": int(new_c.sum()), "outputs": {p.name: sha256(p) for p in (seq, eui, npz)}}
    (a.out / f"{a.out_name}_manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    print(json.dumps(man))


if __name__ == "__main__":
    main()
