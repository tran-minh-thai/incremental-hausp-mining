#!/usr/bin/env python3
"""Build the Ta-Feng grocery dataset in the quantitative sequential format.

Ta-Feng is a four-month transaction log from a Taiwanese supermarket. It is the only
dataset in this collection whose utilities are MEASURED rather than generated: the other
databases carry per-item profits drawn from a log-normal distribution, while here the unit
price comes from the source file itself. Nothing in this script is random, so it needs no
seed and its output is byte-reproducible from the same input.

The roles assigned to the raw columns, which is the decision that makes or breaks the
dataset and is therefore stated rather than buried:

    CUSTOMER_ID       identity of a sequence
    TRANSACTION_DT    identity of an itemset, together with the customer, and the order
                      of itemsets inside a sequence
    PRODUCT_ID        an item
    AMOUNT            the quantity of that item
    SALES_PRICE/AMOUNT  the external utility of that item, as a unit price

The itemset is a shopping trip, so everything a customer bought on one day is ONE itemset.
An earlier build of this dataset made every purchase line its own itemset; that yields 1.00
item per itemset and throws away the basket structure, which is the only reason to want
this dataset at all. Measured on the source: 101,723 of 119,578 baskets hold more than one
item, and the mean is 6.84.

The transaction date has day resolution, which is exactly why the day is the itemset: there
is no finer order to recover inside it.

    python3 scripts/build_tafeng.py --source <ta_feng_all_months_merged.csv>

Two files are written, the same pair every dataset here has: NAME_seq.txt with
"itemID[quantity]" tokens, "-1" closing an itemset and "-2" closing a sequence, and
NAME_eui.txt with "itemID:profit" lines. Profits are integers because the parser reads them
with Long.parseLong; a decimal point there is a crash, not a rounding difference.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ["CUSTOMER_ID", "TRANSACTION_DT", "PRODUCT_ID", "AMOUNT", "SALES_PRICE"]


def read_rows(source: Path):
    """Rows of the source, with the columns this build needs and nothing else."""
    with source.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit("build_tafeng: %s has no column(s) %s; the columns present are %s"
                     % (source, ", ".join(missing), ", ".join(reader.fieldnames or [])))
        for line, d in enumerate(reader, start=2):
            try:
                amount = int(float(d["AMOUNT"]))
                price = float(d["SALES_PRICE"])
            except (TypeError, ValueError):
                sys.exit("build_tafeng: %s line %d has an unreadable AMOUNT or SALES_PRICE"
                         % (source, line))
            if amount <= 0 or price < 0:
                # Measured on the published file: no such row exists. Refusing rather than
                # silently dropping, because a return or a correction would change what a
                # basket means and that is a decision, not a cleanup step.
                sys.exit("build_tafeng: %s line %d has AMOUNT=%s SALES_PRICE=%s; returns and "
                         "zero-quantity rows are not handled by this build"
                         % (source, line, d["AMOUNT"], d["SALES_PRICE"]))
            yield (d["CUSTOMER_ID"].strip(), d["TRANSACTION_DT"].strip(),
                   d["PRODUCT_ID"].strip(), amount, price)


def build(source: Path, out_dir: Path, name: str) -> dict:
    baskets: dict = collections.defaultdict(collections.Counter)   # (customer, day) -> item -> qty
    unit_prices: dict = collections.defaultdict(list)
    rows = 0
    for customer, day, product, amount, price in read_rows(source):
        rows += 1
        baskets[(customer, day)][product] += amount
        unit_prices[product].append(price / amount)

    # Compact ids: the product code is a 13-digit EAN, which does not fit the parser's int.
    # Sorted by code so the mapping is a function of the input alone.
    ids = {code: i for i, code in enumerate(sorted(unit_prices), start=1)}

    # One price per item: the median of its unit prices, because a product sold in a promotion
    # and at full price has two prices in the file and a mean would sit between them at a value
    # that was never charged. At least 1, since a zero profit makes the item invisible to a
    # utility measure rather than merely cheap.
    profit = {code: max(1, int(round(statistics.median(v)))) for code, v in unit_prices.items()}

    by_customer: dict = collections.defaultdict(list)
    for (customer, day), items in baskets.items():
        by_customer[customer].append((day, items))

    out_dir.mkdir(parents=True, exist_ok=True)
    seq_path, eui_path = out_dir / f"{name}_seq.txt", out_dir / f"{name}_eui.txt"
    n_itemsets = n_items = 0
    sizes: collections.Counter = collections.Counter()
    total_utility = 0
    with seq_path.open("w", encoding="utf-8", newline="\n") as f:
        for customer in sorted(by_customer):
            parts = []
            # Day resolution, so days sort as dates; the key parses the American order used
            # by the source file rather than trusting string order.
            for day, items in sorted(by_customer[customer],
                                     key=lambda t: tuple(int(x) for x in
                                                         (t[0].split("/")[2], t[0].split("/")[0], t[0].split("/")[1]))):
                toks = []
                for code in sorted(items, key=lambda c: ids[c]):
                    qty = items[code]
                    toks.append(f"{ids[code]}[{qty}]")
                    n_items += 1
                    total_utility += qty * profit[code]
                sizes[len(toks)] += 1
                n_itemsets += 1
                parts.append(" ".join(toks))
            f.write(" -1 ".join(parts) + " -1 -2\n")
    with eui_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# itemID:profit, measured unit price (SALES_PRICE / AMOUNT, median per item)\n")
        for code in sorted(ids, key=lambda c: ids[c]):
            f.write(f"{ids[code]}:{profit[code]}\n")

    def sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    return {"rows": rows, "sequences": len(by_customer), "items": len(ids),
            "itemsets": n_itemsets, "itemsets_per_seq": n_itemsets / len(by_customer),
            "items_per_itemset": n_items / n_itemsets, "total_utility": total_utility,
            "sizes": sizes, "seq": seq_path, "eui": eui_path,
            "sha": {seq_path.name: sha(seq_path), eui_path.name: sha(eui_path)}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="ta_feng_all_months_merged.csv")
    ap.add_argument("--out-dir", default=str(ROOT / "datasets" / "tafeng"))
    ap.add_argument("--name", default="TAFENG")
    a = ap.parse_args()
    s = build(Path(a.source), Path(a.out_dir), a.name)
    multi = sum(v for k, v in s["sizes"].items() if k > 1)
    print("build_tafeng: read %s rows" % f"{s['rows']:,}")
    print("  sequences (customers)   %s" % f"{s['sequences']:,}")
    print("  distinct items          %s" % f"{s['items']:,}")
    print("  itemsets (shopping trips) %s" % f"{s['itemsets']:,}")
    print("  itemsets per sequence   %.2f" % s["itemsets_per_seq"])
    print("  items per itemset       %.2f   (sizes %d..%d, %s baskets hold more than one, %.1f%%)"
          % (s["items_per_itemset"], min(s["sizes"]), max(s["sizes"]),
             f"{multi:,}", 100 * multi / s["itemsets"]))
    print("  total utility           %s" % f"{s['total_utility']:,}")
    for n, h in s["sha"].items():
        print("  %s  %s" % (h, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
