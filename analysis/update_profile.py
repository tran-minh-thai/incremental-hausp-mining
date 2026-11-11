#!/usr/bin/env python3
"""Update profile of real retail data split into time batches (counting only; no mining, no timing).

For each dataset, schedule and batch k, a basket of the batch belongs to an existing customer when that
customer has a basket before the batch starts (it is appended to that customer's sequence); otherwise
the customer's baskets of the batch form a new sequence.

  r_k   baskets of existing customers / baskets of the batch
  t_k   touched sequences / sequences of the database before the batch
  g_k   utility of the batch / utility of the database before the batch
  a_k   utility of the appended part / utility of the batch
  l_k   median length, in baskets, of a touched sequence before the batch
  w_k   rows of the touched sequences after the batch / rows appended to them (the rewrite factor of a
        design that replaces a touched sequence whole)
  kappa U_batch / max over touched and new sequences of the largest row utility; for any ratio xi the
        local index of the anchored search is lambda = xi * kappa

Schedules (registered 2026-11-11, from the measurement request):
  L5  base = every basket up to the first day at which the cumulative basket count reaches 50%; the
      remaining days are split into 5 batches with equal numbers of days (the last takes the remainder)
  W8  base = every basket before the last 8 weeks; each week is a batch

A row is a distinct (basket, item) pair with positive utility, utilities summed over repeated lines.
Utilities are integers: the source value times a per-dataset scale, rounded once per source line.

Two variants are reported: "raw" (every customer) and "min5" (customers with at least 5 baskets over
the whole data, the filter of the prepared files). Decision rule (registered): the mixed model is the
main case if the median r_k over the L5 batches is >= 0.3 on at least 3 of the 6 datasets.

Self-test (--self-test): a second, loop-based implementation is compared with the vectorised one on
generated and real subsamples, and injected faults must each make the comparison fail.

Usage: python3 analysis/update_profile.py --data NAME=PATH ... [--out DIR]
       python3 analysis/update_profile.py --self-test
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import platform
import random
import re
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FAULT = None
SCALE = {"TAFENG": 1, "DUNNHUMBY": 100, "ONLINE_RETAIL_II": 1000, "HM": 10**6,
         "VALUED_SHOPPERS": 100, "REES46_MULTI": 100}
CHUNK = 2_000_000

# ------------------------------------------------------------------ readers
# Each reader yields DataFrames with integer columns cust, basket, day, item, util (util > 0).


class Codes:
    def __init__(self):
        self.d = {}

    def map(self, values):
        out = np.empty(len(values), dtype=np.int64)
        d = self.d
        for n, v in enumerate(values):
            c = d.get(v)
            if c is None:
                c = d[v] = len(d)
            out[n] = c
        return out


def _finish(df, cust, basket, item, codes):
    df = df[df["util"] > 0]
    return pd.DataFrame({"cust": codes["cust"].map(df[cust].tolist()),
                         "basket": codes["basket"].map(df[basket].tolist()),
                         "day": df["day"].to_numpy(np.int64),
                         "item": codes["item"].map(df[item].tolist()),
                         "util": df["util"].to_numpy(np.int64)})


def iso_day(s):
    return pd.to_datetime(s.str.slice(0, 10), format="%Y-%m-%d").map(date.toordinal).to_numpy(np.int64) \
        if len(s) else np.empty(0, np.int64)


def read_tafeng(path, codes):
    for ch in pd.read_csv(path, dtype=str, chunksize=CHUNK, encoding="utf-8-sig"):
        d = pd.to_datetime(ch["TRANSACTION_DT"], format="%m/%d/%Y")
        ch["day"] = d.map(date.toordinal)
        ch["basket"] = ch["CUSTOMER_ID"] + "|" + ch["TRANSACTION_DT"]
        ch["util"] = (ch["SALES_PRICE"].astype(float) * SCALE["TAFENG"]).round().astype(np.int64)
        yield _finish(ch, "CUSTOMER_ID", "basket", "PRODUCT_ID", codes)


def read_dunnhumby(path, codes):
    for ch in pd.read_csv(path, dtype=str, chunksize=CHUNK):
        ch["day"] = ch["DAY"].astype(np.int64)
        ch["util"] = (ch["SALES_VALUE"].astype(float) * SCALE["DUNNHUMBY"]).round().astype(np.int64)
        yield _finish(ch, "household_key", "BASKET_ID", "PRODUCT_ID", codes)


def read_hm(path, codes):
    for ch in pd.read_csv(path, dtype=str, chunksize=CHUNK):
        ch["day"] = iso_day(ch["t_dat"])
        ch["basket"] = ch["customer_id"] + "|" + ch["t_dat"]
        ch["util"] = (ch["price"].astype(float) * SCALE["HM"]).round().astype(np.int64)
        yield _finish(ch, "customer_id", "basket", "article_id", codes)


def read_valued(path, codes):
    for ch in pd.read_csv(path, dtype=str, chunksize=CHUNK):
        ch["day"] = iso_day(ch["date"])
        ch["basket"] = ch["id"] + "|" + ch["date"]
        ch["item"] = ch["category"] + "|" + ch["company"] + "|" + ch["brand"]
        ch["util"] = (ch["purchaseamount"].astype(float) * SCALE["VALUED_SHOPPERS"]).round().astype(np.int64)
        yield _finish(ch, "id", "basket", "item", codes)


def read_rees46(path, codes):
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.endswith(".csv")):
            with z.open(name) as fh:
                for ch in pd.read_csv(fh, dtype=str, chunksize=CHUNK):
                    ch = ch[ch["event_type"] == "purchase"].copy()
                    if ch.empty:
                        continue
                    ch["day"] = iso_day(ch["event_time"])
                    ch["basket"] = ch["user_id"] + "|" + ch["user_session"].fillna("")
                    ch["util"] = (ch["price"].astype(float) * SCALE["REES46_MULTI"]).round().astype(np.int64)
                    yield _finish(ch, "user_id", "basket", "product_id", codes)


def xlsx_rows(path):
    """Rows of every worksheet of an xlsx file, as lists of cell strings (standard library only)."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for _, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
                if el.tag == ns + "si":
                    shared.append("".join(t.text or "" for t in el.iter(ns + "t")))
                    el.clear()
        sheets = sorted((n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        for sheet in sheets:
            for _, el in ET.iterparse(z.open(sheet)):
                if el.tag == ns + "row":
                    row = {}
                    for c in el.iter(ns + "c"):
                        ref = re.match(r"[A-Z]+", c.get("r")).group(0)
                        col = 0
                        for ch in ref:
                            col = col * 26 + ord(ch) - 64
                        v = c.find(ns + "v")
                        if v is None:
                            is_ = c.find(ns + "is")
                            val = "".join(t.text or "" for t in is_.iter(ns + "t")) if is_ is not None else ""
                        else:
                            val = shared[int(v.text)] if c.get("t") == "s" else v.text
                        row[col - 1] = val
                    yield [row.get(i, "") for i in range(max(row) + 1)] if row else []
                    el.clear()


def read_online_retail(path, codes):
    header, buf = None, []
    for row in xlsx_rows(path):
        if row and row[0] == "Invoice":
            header = row
            continue
        if header is None or not row:
            continue
        buf.append(dict(zip(header, row)))
    df = pd.DataFrame(buf)
    df = df[df["Customer ID"].fillna("") != ""].copy()
    df["day"] = np.floor(df["InvoiceDate"].astype(float)).astype(np.int64)
    df["util"] = (df["Quantity"].astype(float) * df["Price"].astype(float) * SCALE["ONLINE_RETAIL_II"]).round() \
        .astype(np.int64)
    df["cust"] = df["Customer ID"].astype(float).astype(np.int64).astype(str)
    yield _finish(df, "cust", "Invoice", "StockCode", codes)


READERS = {"TAFENG": read_tafeng, "DUNNHUMBY": read_dunnhumby, "HM": read_hm,
           "VALUED_SHOPPERS": read_valued, "REES46_MULTI": read_rees46, "ONLINE_RETAIL_II": read_online_retail}


def _baskets(rows):
    rows = rows.groupby(["cust", "basket", "item"], sort=False).agg(day=("day", "min"), util=("util", "sum")) \
        .reset_index()
    return rows.groupby(["cust", "basket"], sort=False).agg(day=("day", "min"), util=("util", "sum"),
                                                            rows=("item", "size"), maxrow=("util", "max")) \
        .reset_index()[["cust", "day", "util", "rows", "maxrow"]]


def basket_table(chunks, sorted_by_customer=False):
    """One row per basket: cust, day, util, rows (distinct items), maxrow (largest row utility).

    With sorted_by_customer, each chunk is reduced to baskets at once, holding back the rows of its last
    customer; customer codes are assigned in order of first appearance, so the file is sorted by customer
    exactly when the codes never decrease, and the reader refuses otherwise."""
    if not sorted_by_customer:
        return _baskets(pd.concat(list(chunks), ignore_index=True))
    parts, carry, last = [], None, -1
    for ch in chunks:
        c = ch["cust"].to_numpy()
        if c.size and (c[0] < last or (np.diff(c) < 0).any()):
            sys.exit("refusing: the file is not sorted by customer")
        if c.size:
            last = int(c[-1])
        ch = ch if carry is None else pd.concat([carry, ch], ignore_index=True)
        hold = ch["cust"].to_numpy() == last
        carry = ch[hold]
        parts.append(_baskets(ch[~hold]))
    if carry is not None and len(carry):
        parts.append(_baskets(carry))
    return pd.concat(parts, ignore_index=True)


# ------------------------------------------------------------------ schedules and the profile

def schedule(b, name):
    """Return (base_end_day_exclusive, [(start, end_exclusive), ...]) in day ordinals."""
    days = np.sort(b["day"].to_numpy())
    d_min, d_max = int(days[0]), int(days[-1])
    if name == "L5":
        half = int(np.ceil(0.5 * len(days)))
        cut_day = int(days[half - 1])                  # first day at which the cumulative count reaches 50%
        start = cut_day + 1
        n = d_max - start + 1
        step = n // 5
        bounds = [start + i * step for i in range(5)] + [d_max + 1]
        return start, [(bounds[i], bounds[i + 1]) for i in range(5)]
    start = d_max + 1 - 56
    return start, [(start + 7 * i, start + 7 * (i + 1)) for i in range(8)]


def profile(b, sched):
    base_end, batches = schedule(b, sched)
    cust = b["cust"].to_numpy()
    day = b["day"].to_numpy()
    util = b["util"].to_numpy()
    rows = b["rows"].to_numpy()
    maxrow = b["maxrow"].to_numpy()
    first = pd.Series(day).groupby(cust).transform("min").to_numpy()
    out = []
    for k, (s, e) in enumerate(batches, 1):
        inb = (day >= s) & (day < e)
        before = day < s
        cmp_first = first <= s if FAULT == "first_day_inclusive" else first < s
        exist = inb & cmp_first
        n_b = int(inb.sum())
        if n_b == 0:
            out.append({"batch": k, "start": s, "end": e, "baskets": 0})
            continue
        touched = np.unique(cust[exist])
        seqs_before = np.unique(cust[before]).size
        u_before = int(util[before].sum())
        u_batch = int(util[inb].sum())
        u_app = int(util[exist].sum())
        tmask = np.isin(cust, touched)
        len_before = pd.Series(before[tmask].astype(np.int64)).groupby(cust[tmask]).sum().to_numpy()
        rows_after = int(rows[tmask & (day < e)].sum())
        rows_app = int(rows[exist].sum())
        upd = np.unique(cust[inb])
        umask = np.isin(cust, upd) & (day < e)
        max_row = int(maxrow[umask].max())
        out.append({"batch": k, "start": s, "end": e, "baskets": n_b,
                    "r": int(exist.sum()) / n_b,
                    "t": touched.size / seqs_before,
                    "g": u_batch / u_before,
                    "a": (u_app / u_batch) if FAULT != "a_over_db" else u_app / u_before,
                    "l": float(np.median(len_before)) if len_before.size else None,
                    "w": rows_after / rows_app if rows_app else None,
                    "kappa": u_batch / max_row,
                    "touched": int(touched.size), "new_sequences": int(upd.size - touched.size)})
    return base_end, out


def profile_loops(b, sched):
    """Independent loop-based computation of the same quantities (self-test reference)."""
    base_end, batches = schedule(b, sched)
    recs = list(zip(b["cust"].tolist(), b["day"].tolist(), b["util"].tolist(), b["rows"].tolist(),
                    b["maxrow"].tolist()))
    first = {}
    for c, d, *_ in recs:
        first[c] = min(first.get(c, d), d)
    out = []
    for k, (s, e) in enumerate(batches, 1):
        batch = [r for r in recs if s <= r[1] < e]
        if not batch:
            out.append({"batch": k, "start": s, "end": e, "baskets": 0})
            continue
        exist = [r for r in batch if first[r[0]] < s]
        touched = {r[0] for r in exist}
        before = [r for r in recs if r[1] < s]
        lens = sorted(sum(1 for r in before if r[0] == c) for c in touched)
        med = None
        if lens:
            m = len(lens)
            med = float(lens[m // 2]) if m % 2 else (lens[m // 2 - 1] + lens[m // 2]) / 2
        rows_after = sum(r[3] for r in recs if r[0] in touched and r[1] < e)
        rows_app = sum(r[3] for r in exist)
        upd = {r[0] for r in batch}
        u_batch = sum(r[2] for r in batch)
        out.append({"batch": k, "start": s, "end": e, "baskets": len(batch),
                    "r": len(exist) / len(batch),
                    "t": len(touched) / len({r[0] for r in before}),
                    "g": u_batch / sum(r[2] for r in before),
                    "a": sum(r[2] for r in exist) / u_batch,
                    "l": med, "w": rows_after / rows_app if rows_app else None,
                    "kappa": u_batch / max(r[4] for r in recs if r[0] in upd and r[1] < e),
                    "touched": len(touched), "new_sequences": len(upd) - len(touched)})
    return base_end, out


def min5(b):
    n = b.groupby("cust")["day"].transform("size")
    return b[n >= 5].reset_index(drop=True)


# ------------------------------------------------------------------ self-test

def synthetic(rng):
    recs = []
    for c in range(rng.randint(5, 40)):
        start = rng.randint(0, 120)
        for _ in range(rng.randint(1, 12)):
            d = start + rng.randint(0, 150)
            rows = rng.randint(1, 6)
            utils = [rng.randint(1, 50) for _ in range(rows)]
            recs.append((c, d, sum(utils), rows, max(utils)))
    return pd.DataFrame(recs, columns=["cust", "day", "util", "rows", "maxrow"])


def same(p, q):
    if len(p) != len(q):
        return False
    for x, y in zip(p, q):
        for key in x:
            a, b = x[key], y.get(key)
            if isinstance(a, float) or isinstance(b, float):
                if a is None or b is None or abs(a - b) > 1e-12 * max(1, abs(a)):
                    if not (a is None and b is None):
                        return False
            elif a != b:
                return False
    return True


def self_test(real):
    global FAULT
    rng = random.Random(20261111)
    cases = [synthetic(rng) for _ in range(150)] + real
    report = {}
    for fault in [None, "first_day_inclusive", "a_over_db"]:
        FAULT = fault
        compared = bad = 0
        for b in cases:
            for sched in ("L5", "W8"):
                p = profile(b, sched)[1]
                FAULT, f0 = None, FAULT
                q = profile_loops(b, sched)[1]
                FAULT = f0
                compared += sum(1 for x in q if x["baskets"])
                if not same(p, q):
                    bad += 1
        report[fault or "none"] = {"batches_compared": compared, "profiles_disagreeing": bad}
    FAULT = None
    # shared code paths, checked against their definitions
    sched_cells = sched_bad = 0
    for b in cases:
        days = np.sort(b["day"].to_numpy())
        start, bt = schedule(b, "L5")
        sched_cells += 1
        n = len(days)
        if not ((days < start).sum() >= 0.5 * n and (days < start - 1).sum() < 0.5 * n
                and bt[0][0] == start and bt[-1][1] == days[-1] + 1
                and all(bt[i][1] == bt[i + 1][0] for i in range(4))
                and len({e - s for s, e in bt[:4]}) == 1 and bt[4][1] - bt[4][0] >= bt[0][1] - bt[0][0]):
            sched_bad += 1
        start, bt = schedule(b, "W8")
        sched_cells += 1
        if not (len(bt) == 8 and all(e - s == 7 for s, e in bt) and bt[-1][1] == days[-1] + 1 and start == bt[0][0]):
            sched_bad += 1
    report["schedule_definition"] = {"cells": sched_cells, "violations": sched_bad}
    # basket table on a hand example: basket 1 holds item 7 twice (3 + 4) and item 8 (5)
    rows = pd.DataFrame({"cust": [0, 0, 0, 1], "basket": [1, 1, 1, 2], "day": [10, 10, 10, 11],
                         "item": [7, 7, 8, 7], "util": [3, 4, 5, 9]})
    bt = basket_table(iter([rows])).sort_values("cust").to_dict("records")
    hand = [{"cust": 0, "day": 10, "util": 12, "rows": 2, "maxrow": 7},
            {"cust": 1, "day": 11, "util": 9, "rows": 1, "maxrow": 9}]
    report["basket_table_hand_example"] = {"cells": 2, "violations": int(bt != hand)}
    # streaming reduction equals the in-memory one on generated rows sorted by customer, split in chunks
    rr = random.Random(5)
    big = pd.DataFrame([(c, c * 10 + bk, d, rr.randint(1, 6), rr.randint(1, 9))
                        for c in range(60) for bk in range(rr.randint(1, 5)) for d in [rr.randint(0, 99)]
                        for _ in range(rr.randint(1, 6))], columns=["cust", "basket", "day", "item", "util"])
    pieces = [big.iloc[i:i + 37] for i in range(0, len(big), 37)]
    a1 = basket_table(iter(pieces), sorted_by_customer=True).sort_values(["cust", "day", "util"]).reset_index(drop=True)
    a2 = basket_table(iter([big])).sort_values(["cust", "day", "util"]).reset_index(drop=True)
    report["streaming_equals_in_memory"] = {"cells": int(len(a2)), "violations": int(not a1.equals(a2))}
    ok = report["none"]["profiles_disagreeing"] == 0 and report["none"]["batches_compared"] > 0 and \
        all(report[f]["profiles_disagreeing"] > 0 for f in ("first_day_inclusive", "a_over_db")) and \
        all(report[k]["violations"] == 0 and report[k]["cells"] > 0
            for k in ("schedule_definition", "basket_table_hand_example", "streaming_equals_in_memory"))
    return ok, report


# ------------------------------------------------------------------ main

def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 24), b""):
            h.update(blk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="*", default=[])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    dirty = [l for l in git("status", "--porcelain", "--untracked-files=no").splitlines() if l]
    if dirty and not args.self_test:
        sys.exit(f"refusing: the working tree at {ROOT} has {len(dirty)} modified tracked files")

    tables, inputs = {}, {}
    for spec in args.data:
        name, path = spec.split("=", 1)
        t0 = time.time()
        codes = {"cust": Codes(), "basket": Codes(), "item": Codes()}
        tables[name] = basket_table(READERS[name](path, codes), sorted_by_customer=name == "VALUED_SHOPPERS")
        inputs[name] = {"file": Path(path).name, "sha256": sha256(path),
                        "baskets": int(len(tables[name])), "customers": int(tables[name]["cust"].nunique()),
                        "rows": int(tables[name]["rows"].sum()), "read_seconds": round(time.time() - t0, 1)}
        print(name, inputs[name], flush=True)

    if args.self_test:
        real = []
        for b in tables.values():
            keep = set(random.Random(7).sample(sorted(b["cust"].unique().tolist()),
                                               min(300, b["cust"].nunique())))
            real.append(b[b["cust"].isin(keep)].reset_index(drop=True))
        ok, rep = self_test(real)
        print(json.dumps(rep, indent=2))
        print("SELF-TEST", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)

    out = args.out or ROOT / "results-invariant" / "update-profile" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    rows_out, verdict = [], {}
    for name, b in tables.items():
        for variant, bb in (("raw", b), ("min5", min5(b))):
            for sched in ("L5", "W8"):
                base_end, prof = profile(bb, sched)
                for p in prof:
                    rows_out.append({"dataset": name, "variant": variant, "schedule": sched,
                                     "base_end_day": base_end, **p})
                if sched == "L5":
                    rs = [p["r"] for p in prof if p["baskets"]]
                    verdict.setdefault(variant, {})[name] = float(np.median(rs)) if rs else None
    cols = ["dataset", "variant", "schedule", "base_end_day", "batch", "start", "end", "baskets", "r", "t", "g",
            "a", "l", "w", "kappa", "touched", "new_sequences"]
    with open(out / "profile.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        for r in rows_out:
            wr.writerow({c: r.get(c, "") for c in cols})
    rule = {v: {"median_r_L5": m, "datasets_at_or_above_0.3": sum(1 for x in m.values() if x is not None and x >= 0.3),
                "datasets": len(m), "passes": sum(1 for x in m.values() if x is not None and x >= 0.3) >= 3}
            for v, m in verdict.items()}
    summary = {"kind": "INVARIANT (data property: counts and utility ratios; no mining, no timing)",
               "scales": {k: SCALE[k] for k in tables}, "inputs": inputs, "decision_rule": rule}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    meta = {"command": "python3 analysis/update_profile.py " + " ".join(
                a.split("=")[0] + "=" + Path(a.split("=", 1)[1]).name if "=" in a else a for a in sys.argv[1:]),
            "git_commit": git("rev-parse", "HEAD"), "tree": "clean",
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__}
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(rule, indent=2))
    print("wrote", out.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
