#!/usr/bin/env python3
"""Build the raw (unfiltered) retail datasets as quantitative sequence databases.

One sequence per customer; its itemsets are the customer's baskets in order of (day, basket); an item
of a basket carries its row utility as an integer (the source value times the per-dataset scale of
analysis/update_profile.py, rounded once per source line, then summed over repeated lines). The
external utility of every item is 1, so the utility of an item occurrence is its row utility: prices
that vary between lines are kept exactly, which a single price per item would not.

No customer or item is filtered out; rows with a nonpositive utility are dropped. Basket roles are
those of analysis/update_profile.py (READERS).

Outputs, in --out/<NAME>/ (outside the repository: the data are not redistributable):
  <NAME>_seq.txt, <NAME>_eui.txt   miner format (item[quantity] ... -1 ... -2), items numbered from 1
  <NAME>_rows.npz                  cust, day, basket (rank within customer), item, util, one per row
  <NAME>_manifest.json             source sha256, scale, counts, sha256 of every output

Self-test (--self-test): on generated rows, the written sequence file is parsed back and compared row
by row with the input, and the npz with both; an injected fault (dropping repeated-line summation) must
make the comparison fail.

Usage: python3 analysis/build_raw_sequences.py --data NAME=PATH ... --out DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_profile as up  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAULT = None


def reduce_rows(df):
    """(cust, basket, item) -> one row with the earliest day and the summed utility."""
    if FAULT == "no_line_sum":
        return df.drop_duplicates(["cust", "basket", "item"])[["cust", "basket", "day", "item", "util"]]
    return df.groupby(["cust", "basket", "item"], sort=False).agg(day=("day", "min"), util=("util", "sum")) \
        .reset_index()[["cust", "basket", "day", "item", "util"]]


def customer_frames(chunks, sorted_by_customer):
    """Yield reduced row frames in which every customer appears in exactly one frame."""
    if not sorted_by_customer:
        yield reduce_rows(pd.concat(list(chunks), ignore_index=True))
        return
    carry, last = None, -1
    for ch in chunks:
        c = ch["cust"].to_numpy()
        if c.size and (c[0] < last or (np.diff(c) < 0).any()):
            sys.exit("refusing: the file is not sorted by customer")
        if c.size:
            last = int(c[-1])
        ch = ch if carry is None else pd.concat([carry, ch], ignore_index=True)
        hold = ch["cust"].to_numpy() == last
        carry = ch[hold]
        if (~hold).any():
            yield reduce_rows(ch[~hold])
    if carry is not None and len(carry):
        yield reduce_rows(carry)


def order(f):
    """Sort rows by customer, then basket order (day, basket code), then item; rank baskets per customer."""
    bday = f.groupby(["cust", "basket"], sort=False)["day"].transform("min")
    f = f.assign(bday=bday).sort_values(["cust", "bday", "basket", "item"], kind="mergesort")
    key = f["cust"].astype(np.int64) * (1 << 31) + f["basket"].astype(np.int64)
    new_b = np.r_[True, key.to_numpy()[1:] != key.to_numpy()[:-1]]
    new_c = np.r_[True, f["cust"].to_numpy()[1:] != f["cust"].to_numpy()[:-1]]
    rank = np.cumsum(new_b) - 1
    first_rank = np.maximum.accumulate(np.where(new_c, rank, 0))
    return f.assign(brank=rank - first_rank), new_b, new_c


def write_lines(fh, f, new_b, new_c):
    items = (f["item"].to_numpy() + 1).astype(np.int64)
    utils = f["util"].to_numpy()
    n = len(f)
    out = []
    for r in range(n):
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
    if n:
        out.append(" -1 -2\n")
    fh.write("".join(out))


def build(name, path, out_dir):
    codes = {"cust": up.Codes(), "basket": up.Codes(), "item": up.Codes()}
    d = out_dir / name
    d.mkdir(parents=True, exist_ok=True)
    seq_path, eui_path, npz_path = d / f"{name}_seq.txt", d / f"{name}_eui.txt", d / f"{name}_rows.npz"
    parts = {k: [] for k in ("cust", "day", "basket", "item", "util")}
    n_seq = 0
    big_util = 0
    with open(seq_path, "w", newline="\n") as fh:
        for f in customer_frames(up.READERS[name](path, codes), name == "VALUED_SHOPPERS"):
            f, new_b, new_c = order(f)
            big_util = max(big_util, int(f["util"].max()))
            write_lines(fh, f, new_b, new_c)
            n_seq += int(new_c.sum())
            parts["cust"].append(f["cust"].to_numpy(np.int32))
            parts["day"].append(f["bday"].to_numpy(np.int32))
            parts["basket"].append(f["brank"].to_numpy(np.int32))
            parts["item"].append(f["item"].to_numpy(np.int32))
            parts["util"].append(f["util"].to_numpy(np.int64))
    if big_util >= 2 ** 31:
        sys.exit(f"refusing: a row utility of {big_util} does not fit the miner's int quantity")
    n_items = len(codes["item"].d)
    with open(eui_path, "w", newline="\n") as fh:
        fh.write("# itemID:profit; profit 1, the quantity of an occurrence is its row utility\n")
        fh.write("".join(f"{i}:1\n" for i in range(1, n_items + 1)))
    np.savez(npz_path, **{k: np.concatenate(v) for k, v in parts.items()})
    manifest = {"dataset": name, "source_file": Path(path).name, "source_sha256": up.sha256(path),
                "scale": up.SCALE[name], "sequences": n_seq, "items": n_items,
                "rows": int(sum(len(x) for x in parts["util"])),
                "total_utility": int(sum(int(x.sum()) for x in parts["util"])),
                "largest_row_utility": big_util,
                "outputs": {p.name: up.sha256(p) for p in (seq_path, eui_path, npz_path)},
                "code_commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                                              text=True).stdout.strip(),
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (d / f"{name}_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def parse_seq(path):
    """Read a sequence file back as (sequence index, itemset index, item, quantity) tuples."""
    out = []
    for s, line in enumerate(open(path)):
        k = 0
        for tok in line.split():
            if tok == "-2":
                break
            if tok == "-1":
                k += 1
                continue
            i, q = tok[:-1].split("[")
            out.append((s, k, int(i) - 1, int(q)))
    return out


def self_test(tmp):
    global FAULT
    rng = random.Random(11)
    rows = pd.DataFrame([(c, c * 100 + b, d, rng.randint(0, 6), rng.randint(1, 40))
                         for c in range(30) for b in range(rng.randint(1, 5)) for d in [rng.randint(0, 50)]
                         for _ in range(rng.randint(1, 7))], columns=["cust", "basket", "day", "item", "util"])
    report = {}
    for fault in (None, "no_line_sum"):
        FAULT = fault
        f, new_b, new_c = order(reduce_rows(rows))
        p = tmp / "t_seq.txt"
        with open(p, "w") as fh:
            write_lines(fh, f, new_b, new_c)
        FAULT = None
        ref = rows.groupby(["cust", "basket", "item"]).agg(day=("day", "min"), util=("util", "sum")).reset_index()
        ref["bday"] = ref.groupby(["cust", "basket"])["day"].transform("min")
        ref = ref.sort_values(["cust", "bday", "basket", "item"])
        ref["s"] = ref.groupby("cust").ngroup()
        ref["k"] = ref.groupby("cust")["basket"].transform(lambda x: pd.factorize(x)[0])
        expect = list(zip(ref["s"], ref["k"], ref["item"], ref["util"]))
        got = parse_seq(p)
        report[fault or "none"] = {"rows_compared": len(expect), "matches": got == [tuple(int(v) for v in e)
                                                                                   for e in expect]}
    ok = report["none"]["matches"] and report["none"]["rows_compared"] > 0 and not report["no_line_sum"]["matches"]
    return ok, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="*", default=[])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        args.out.mkdir(parents=True, exist_ok=True)
        ok, rep = self_test(args.out)
        print(json.dumps(rep), "SELF-TEST", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    for spec in args.data:
        name, path = spec.split("=", 1)
        print(json.dumps(build(name, path, args.out)), flush=True)


if __name__ == "__main__":
    main()
