#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Export the batch inputs of the maintenance measurement (experiment 15) from the raw datasets.

For each dataset NAME and schedule (L5, W8 of analysis/update_profile.py) it writes
datasets/maint/<NAME>-<SCHEDULE>/ with
  db_eui.txt   the dataset's external utilities, copied
  db_seq.txt   one line per (batch, customer): that customer's baskets of the batch, in basket order,
               items of a basket in item order, as item[utility] ... -1 ... -2
  keys.txt     the customer key of each line (a line whose key is already stored appends to it)
  sizes.txt    lines per batch, comma separated (batch 0 is the base database)
and appends their sha256 to datasets/maint/MANIFEST.sha256, which Experiment15Runner checks. The data are not
redistributable; datasets/ is not committed except the manifest.

Optionally a fixed share of the customers is kept, chosen by a hash of the customer key
((key * 2654435761) mod 2^32 < share * 2^32), and the share is recorded.

Checks, before anything is written:
  * no basket has rows in two batches (else an appended part would continue a stored itemset), with the count of
    baskets compared, and a row moved into the next batch must be detected;
  * on --verify NAME the exported lines are compared byte for byte with an independent writer: the per-customer
    pandas grouping of analysis/check_prefix_encoding.py (real_lines) written in the same format.

Usage (development machine; needs numpy and pandas):
  python3 analysis/export_maintenance_inputs.py --raw DIR --data TAFENG ONLINE_RETAIL_II ... [--share NAME=0.1]
      [--verify TAFENG ONLINE_RETAIL_II]
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))
import update_profile as up  # noqa: E402

OUT = ROOT / "datasets" / "maint"
SCHEDULES = ("L5", "W8")
FAULT = __import__("os").environ.get("HAUSP_EXPORT_FAULT")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 24), b""):
            h.update(blk)
    return h.hexdigest()


def load(raw: Path, name: str, share: float):
    z = np.load(raw / name / f"{name}_rows.npz")
    cols = {k: z[k] for k in ("cust", "day", "basket", "item", "util")}
    if share < 1.0:
        keep = (cols["cust"].astype(np.uint64) * np.uint64(2654435761)) % np.uint64(2 ** 32) < np.uint64(int(share * 2 ** 32))
        cols = {k: v[keep] for k, v in cols.items()}
    return cols


def bounds_of(cols, sched):
    import pandas as pd
    b = pd.DataFrame({"cust": cols["cust"], "basket": cols["basket"], "day": cols["day"]}).drop_duplicates(["cust", "basket"])
    base_end, batches = up.schedule(b, sched)
    return [(-10 ** 9, base_end)] + list(batches)


def batch_index(day, bounds):
    edges = np.array([e for _, e in bounds[:-1]])
    return np.searchsorted(edges, day, side="right")


def basket_split(cols, bounds):
    """(baskets compared, baskets with rows in more than one batch)."""
    b = batch_index(cols["day"], bounds)
    order = np.lexsort((b, cols["basket"], cols["cust"]))
    c, k, bb = cols["cust"][order], cols["basket"][order], b[order]
    new_basket = np.r_[True, (c[1:] != c[:-1]) | (k[1:] != k[:-1])]
    gid = np.cumsum(new_basket) - 1
    first_b = bb[new_basket][gid]
    split = np.zeros(gid[-1] + 1, dtype=bool) if len(gid) else np.zeros(0, dtype=bool)
    np.logical_or.at(split, gid, bb != first_b)
    return int(new_basket.sum()), int(split.sum())


def injected_split_detected(cols, bounds):
    """Move one row of a multi-row basket into the next batch; the split check must then count exactly 1."""
    b = batch_index(cols["day"], bounds)
    order = np.lexsort((cols["basket"], cols["cust"]))
    c, k = cols["cust"][order], cols["basket"][order]
    same = np.nonzero((c[1:] == c[:-1]) & (k[1:] == k[:-1]))[0]
    for j in same:
        row = order[j + 1]
        if b[row] < len(bounds) - 1:
            day = cols["day"].copy()
            day[row] = bounds[b[row] + 1][0]
            return basket_split({**cols, "day": day}, bounds)[1] == 1
    return False


def lines_of(cols, bounds):
    """Lines in (batch, customer) order, each a list of baskets (lists of (item+1, util)), and the batch sizes."""
    b = batch_index(cols["day"], bounds)
    item_key = -cols["item"] if FAULT == "item_desc" else cols["item"]   # the fault the --verify comparison must catch
    order = np.lexsort((item_key, cols["basket"], cols["cust"], b))
    bb, c, k = b[order], cols["cust"][order], cols["basket"][order]
    it, ut = cols["item"][order] + 1, cols["util"][order]
    new_line = np.r_[True, (bb[1:] != bb[:-1]) | (c[1:] != c[:-1])]
    new_basket = new_line | np.r_[True, k[1:] != k[:-1]]
    sizes = [int(((bb == i) & new_line).sum()) for i in range(len(bounds))]
    return bb, c, it, ut, new_line, new_basket, sizes


def write(cols, bounds, d: Path):
    bb, c, it, ut, new_line, new_basket, sizes = lines_of(cols, bounds)
    d.mkdir(parents=True, exist_ok=True)
    starts = np.nonzero(new_line)[0]
    ends = np.r_[starts[1:], len(c)]
    with open(d / "db_seq.txt", "w", newline="\n") as fs, open(d / "keys.txt", "w", newline="\n") as fk:
        for s, e in zip(starts.tolist(), ends.tolist()):
            toks = []
            nb = new_basket[s:e].tolist()
            for j, (i, u) in enumerate(zip(it[s:e].tolist(), ut[s:e].tolist())):
                if nb[j] and j > 0:
                    toks.append("-1")
                toks.append(f"{i}[{u}]")
            toks.append("-1")
            toks.append("-2")
            fs.write(" ".join(toks) + "\n")
            fk.write(f"{int(c[s])}\n")
    (d / "sizes.txt").write_text(",".join(map(str, sizes)) + "\n")
    return sizes


def reference_text(raw: Path, name: str, sched: str):
    """The same lines from the independent pandas grouping of the prefix-encoding check, in the same format."""
    import check_prefix_encoding as cpe
    lines, sizes, _ = cpe.real_lines(name, raw / name, sched)
    seq = "".join(" ".join([t for iset in its for t in [f"{i}[{q}]" for i, q in iset] + ["-1"]] + ["-2"]) + "\n"
                  for _, its in lines)
    keys = "".join(f"{k}\n" for k, _ in lines)
    return seq, keys, sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--data", nargs="+", required=True)
    ap.add_argument("--share", nargs="*", default=[])
    ap.add_argument("--verify", nargs="*", default=[])
    args = ap.parse_args()
    shares = {k: float(v) for k, v in (s.split("=") for s in args.share)}
    report = {}
    manifest = OUT / "MANIFEST.sha256"
    existing = {}
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                h, f = line.split(None, 1)
                existing[f.strip()] = h
    for name in args.data:
        share = shares.get(name, 1.0)
        cols = load(args.raw, name, share)
        for sched in SCHEDULES:
            bounds = bounds_of(cols, sched)
            checked, split = basket_split(cols, bounds)
            inj = injected_split_detected(cols, bounds)
            if checked == 0 or split or not inj:
                sys.exit(f"refusing {name}-{sched}: baskets compared {checked}, split {split}, injected split detected {inj}")
            d = OUT / f"{name}-{sched}"
            sizes = write(cols, bounds, d)
            (d / "db_eui.txt").write_bytes((args.raw / name / f"{name}_eui.txt").read_bytes())
            rec = {"share": share, "rows": int(len(cols["cust"])), "sizes": sizes, "baskets_compared": checked,
                   "baskets_split": split, "injected_split_detected": inj,
                   "source_rows_sha256": sha(args.raw / name / f"{name}_rows.npz")}
            if name in args.verify:
                seq, keys, rsizes = reference_text(args.raw, name, sched)
                rec["verify"] = {"seq_identical": (d / "db_seq.txt").read_text() == seq,
                                 "keys_identical": (d / "keys.txt").read_text() == keys,
                                 "sizes_identical": rsizes == sizes}
                if not all(rec["verify"].values()):
                    sys.exit(f"refusing {name}-{sched}: exported lines differ from the reference writer: {rec['verify']}")
            for f in ("db_eui.txt", "db_seq.txt", "keys.txt", "sizes.txt"):
                existing[f"{name}-{sched}/{f}"] = sha(d / f)
            report[f"{name}-{sched}"] = rec
            print(name, sched, json.dumps(rec), flush=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("# sha256 of the experiment-15 inputs, written by analysis/export_maintenance_inputs.py\n"
                        + "".join(f"{h}  {f}\n" for f, h in sorted(existing.items())))
    (OUT / "EXPORT_REPORT.json").write_text(json.dumps(report, indent=1) + "\n") if not (OUT / "EXPORT_REPORT.json").exists() else None
    prev = json.loads((OUT / "EXPORT_REPORT.json").read_text())
    prev.update(report)
    (OUT / "EXPORT_REPORT.json").write_text(json.dumps(prev, indent=1) + "\n")


if __name__ == "__main__":
    main()
