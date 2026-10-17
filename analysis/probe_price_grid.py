#!/usr/bin/env python3
"""PROBE, not a measurement: how the HAUSP set moves when the unit price of one item varies.

The problem under study fixes every unit price but one, lets that price p range over an interval,
and asks, for every pattern, at which prices it is a HAUSP: average utility at least minUtil times
the total utility of the database, both taken at price p. Two properties decide whether that
problem deserves an algorithm.

* Shape. u(alpha; p) is a sum over sequences of a maximum over occurrences of functions linear in
  p, hence convex in p; the total utility T(p) is linear. So u(alpha; p) - minUtil |alpha| T(p) is
  convex, and the prices at which alpha is NOT a HAUSP form one interval. On a price grid, no
  pattern may be a HAUSP at one price while failing at a lower and at a higher one (status 0..1..0).
* Significance. If hardly any pattern changes status over a realistic price range, the problem is
  trivial.

The probe mines the full database once per grid price with the threshold mode of HAUSP-UB
(src/main/java/TopKProbe.java, one batch). Every price is multiplied by 10 and the varied item's by
10 f, f = 0.5, 0.6, ..., 1.5, so that every price stays an integer and f = 1.0 reproduces the
original relative threshold. The varied item is the one with the largest total utility; with
--tafeng-source, on TAFENG it is instead every item of the product subclass with the largest total
utility, all scaled by the same factor (the utility stays linear in that one factor, so the shape
argument is unchanged). Items the
price file does not list take the parser's default price 1; they are written out explicitly at 10,
otherwise scaling would leave them at 1.

Before the miner runs, the shape property is checked on seeded random databases scored by the
definitions alone (analysis/verify_definitions.py shares no code with the miner). A fault injection
replaces the maximum over occurrences by a minimum, which makes the utility concave in p; the check
must then report violations, or it is not able to see one and the probe stops.

Decision rule registered before the first run (EXPERIMENT_CHANGELOG, 2026-10-17):
  shape         0 patterns with status 0..1..0 on every dataset; one is enough to mean that the
                theory or the code is wrong;
  significance  worth an algorithm if, over f in [0.8, 1.2], at least 1% of the patterns in the
                union of the HAUSP sets change status on at least 3 of the 6 datasets.
Recorded, not a criterion: patterns with status 1..0..1 (HAUSP at both ends of the grid, not between).

Everything goes to results-probe/price-grid/<run id>/: raw output of every JVM, summary.csv,
status/<dataset>.tsv (the status of every pattern at every grid price), evaluation.txt and
run_meta.json (command, commit, tree state, JAR hash, java version, host, thresholds, varied items).

    python3 analysis/probe_price_grid.py                  # the six registered datasets
    python3 analysis/probe_price_grid.py --self-test-only
    python3 analysis/probe_price_grid.py --datasets TAFENG --mu TAFENG=0.0012 --tafeng-source <ta_feng_all_months_merged.csv>
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import itertools
import json
import platform
import random
import re
import socket
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, load_config  # noqa: E402

_spec = importlib.util.spec_from_file_location("verify_definitions", ROOT / "analysis" / "verify_definitions.py")
_vd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vd)

JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
OUT_ROOT = ROOT / "results-probe" / "price-grid"
FILES = {"BIBLE": "bible/BIBLE", "BMS1_SPMF": "bms1_spmf/BMS1_SPMF", "FIFA": "fifa/FIFA",
         "KOSARAK": "kosarak/KOSARAK", "LEVIATHAN": "leviathan/LEVIATHAN", "SIGN": "sign/SIGN",
         "TAFENG": "tafeng/TAFENG", "C8T1S5I8N5K": "syn/C8T1S5I8N5K"}
REGISTERED = ["TAFENG", "BIBLE", "LEVIATHAN", "SIGN", "C8T1S5I8N5K", "BMS1_SPMF"]
#: Grid prices of the varied item, in tenths of its original price.
FACTORS = list(range(5, 16))
WINDOW = [8, 9, 10, 11, 12]
SIGNIFICANT_SHARE = 0.01
SIGNIFICANT_DATASETS = 3
SOURCE_EXPERIMENT = 2
HOLDS = re.compile(r"database holds (\d+) sequences")


# --------------------------------------------------------------------------- status shapes
def shape(status: str) -> tuple[bool, bool]:
    """(0..1..0 present, 1..0..1 present) for a status string over increasing prices."""
    def sandwiched(inner: str, outer: str) -> bool:
        first, last = status.find(outer), status.rfind(outer)
        return first >= 0 and any(status[j] == inner for j in range(first + 1, last))
    return sandwiched("1", "0"), sandwiched("0", "1")


def self_test_shape() -> list[str]:
    cases = {"010": (True, False), "101": (False, True), "11001": (False, True), "0011": (False, False),
             "0110": (True, False), "1010": (True, True), "1": (False, False), "": (False, False),
             "000": (False, False), "111": (False, False), "1100": (False, False), "00100": (True, False)}
    return [f"shape({s!r}) = {shape(s)}, expected {want}" for s, want in cases.items() if shape(s) != want]


# --------------------------------------------------------------------------- definition check
def random_db(rng: random.Random):
    n_items = rng.randint(3, 5)
    profits = {i: rng.randint(1, 9) for i in range(1, n_items + 1)}
    seqs = []
    for _ in range(rng.randint(4, 6)):
        s = []
        for _ in range(rng.randint(1, 4)):
            iset = sorted(rng.sample(range(1, n_items + 1), rng.randint(1, 2)))
            s.append([(i, rng.randint(1, 5)) for i in iset])
        seqs.append(s)
    return profits, seqs


def linear_occurrences(profits, sequences, item):
    """Pattern -> (length, [per sequence: [(base, coefficient) per occurrence]]), u = base + coef * p_item."""
    base = [[{i: (0 if i == item else profits[i]) * q for i, q in iset} for iset in s] for s in sequences]
    coef = [[{i: (q if i == item else 0) for i, q in iset} for iset in s] for s in sequences]
    items = sorted({i for s in sequences for iset in s for i, _ in iset})
    width = max(len(iset) for s in sequences for iset in s)
    itemsets = [tuple(c) for r in range(1, width + 1) for c in itertools.combinations(items, r)]
    out, frontier = {}, [[x] for x in itemsets]
    for _ in range(max(len(s) for s in sequences)):
        nxt = []
        for pat in frontier:
            per_seq = []
            for b, c in zip(base, coef):
                bs, cs = _vd.occurrence_utilities(pat, b), _vd.occurrence_utilities(pat, c)
                if bs:
                    per_seq.append(list(zip(bs, cs)))
            if per_seq:
                out[_vd.render(pat)] = (sum(len(x) for x in pat), per_seq)
                nxt.append(pat)
        frontier = [p + [x] for p in nxt for x in itemsets]
    return out


def statuses(profits, seqs, item, mu: Fraction, agg, grid) -> dict[str, str]:
    """Pattern -> HAUSP status at every grid price (all prices x10, the item's x f), by definition."""
    t_base = sum(q * profits[i] for s in seqs for iset in s for i, q in iset if i != item)
    t_coef = sum(q for s in seqs for iset in s for i, q in iset if i == item)
    return {pat: "".join("1" if sum(agg(10 * b + f * c for b, c in occ) for occ in per_seq)
                         >= mu * length * (10 * t_base + f * t_coef) else "0" for f in grid)
            for pat, (length, per_seq) in linear_occurrences(profits, seqs, item).items()}


#: A database built so that <(1,2)> has two occurrences whose lines in the price of item 1 cross:
#: 90 + f and 10 + 5f (prices x10). Its total utility is 120 + 6f. At minUtil 1/4 the definition
#: (maximum) makes it a HAUSP at both ends of the grid and not at f = 20: status 1..0..1. Under the
#: injected minimum at minUtil 9/40 it is a HAUSP only around f = 20: status 0..1..0.
DESIGNED = ({1: 1, 2: 1, 3: 1}, [[[(1, 1), (2, 9)], [(1, 5), (2, 1)]], [[(3, 2)]]])


def definition_check(n_db: int = 200, seed: int = 20261017) -> dict:
    """Shape counts on random databases under the definition (max) and the injected fault (min)."""
    grid = list(range(1, 31))
    report = {"databases": n_db, "seed": seed, "grid_tenths": [grid[0], grid[-1]]}
    for agg_name, agg in (("max", max), ("min", min)):
        rng = random.Random(seed)                # both aggregations see the same databases and thresholds
        patterns = nonconstant = bad = valley = 0
        for _ in range(n_db):
            profits, seqs = random_db(rng)
            totals = {}
            for s in seqs:
                for iset in s:
                    for i, q in iset:
                        totals[i] = totals.get(i, 0) + q * profits[i]
            item = min(totals, key=lambda i: (-totals[i], i))
            mu = Fraction(rng.choice([1, 3, 6, 10]), 100)
            for status in statuses(profits, seqs, item, mu, agg, grid).values():
                patterns += 1
                nonconstant += len(set(status)) > 1
                b010, b101 = shape(status)
                bad += b010
                valley += b101
        report[agg_name] = {"patterns": patterns, "nonconstant": nonconstant, "status_010": bad,
                            "status_101": valley}
    profits, seqs = DESIGNED
    report["designed"] = {f"{name}@{mu}": statuses(profits, seqs, 1, mu, agg, grid)["<(1,2)>"]
                          for name, agg, mu in (("max", max, Fraction(1, 4)), ("max", max, Fraction(9, 40)),
                                                ("min", min, Fraction(9, 40)))}
    return report


# --------------------------------------------------------------------------- miner runs
def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def read_prices(path: Path) -> dict[int, int]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line[0] in "#@":
            continue
        parts = re.split(r"[:\s\t,]+", line)
        if len(parts) >= 2:
            out[int(parts[0])] = int(parts[1])
    return out


def item_totals(seq: Path, prices: dict[int, int]) -> tuple[dict[int, int], int]:
    """Total utility of every item, with the parser's default price 1 for unlisted items; sequence lines."""
    totals: dict[int, int] = {}
    lines = 0
    for line in seq.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line[0] in "#@":
            continue
        lines += 1
        for tok in line.split():
            if tok in ("-1", "-2"):
                continue
            b = tok.find("[")
            i, q = (int(tok[:b]), int(tok[b + 1:tok.index("]")])) if b >= 0 else (int(tok), 1)
            totals[i] = totals.get(i, 0) + q * prices.get(i, 1)
    return totals, lines


def tafeng_subclasses(source: Path) -> tuple[dict[int, str], int]:
    """Item id -> product subclass, ids numbered as scripts/build_tafeng.py numbers them.

    A product listed under several subclasses is assigned the one on most of its rows (ties: the
    smaller code); the second value is how many products needed that rule.
    """
    rows: dict = {}
    with source.open(encoding="utf-8-sig", newline="") as f:
        for d in csv.DictReader(f):
            c = rows.setdefault(d["PRODUCT_ID"].strip(), {})
            sub = d["PRODUCT_SUBCLASS"].strip()
            c[sub] = c.get(sub, 0) + 1
    ids = {code: i for i, code in enumerate(sorted(rows), start=1)}
    out = {ids[code]: min(c, key=lambda x: (-c[x], x)) for code, c in rows.items()}
    return out, sum(len(c) > 1 for c in rows.values())


def mine(scratch: Path, ds: str, eui: Path, mu: float, n: int, timeout: int):
    """Run the threshold mode on the whole database; (status, patterns, reported count, text, argv)."""
    seq = ROOT / "datasets" / f"{FILES[ds]}_seq.txt"
    props = scratch / f"{ds}.properties"
    props.write_text(f"dataset.path={seq}\nminUtil.percentage={mu}\n", encoding="utf-8")
    for _ in range(2):
        cwd = Path(tempfile.mkdtemp(dir=scratch))      # fresh: no dump of an earlier price can be read
        argv = ["java", "-Xmx8g", "-XX:+UseG1GC", "-cp", str(JAR), "TopKProbe", str(eui), str(seq), str(props),
                "1", f"mu={mu}", str(n)]
        try:
            p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "OT", set(), None, "", argv
        text = p.stdout + p.stderr
        m = HOLDS.search(p.stderr)
        if p.returncode == 2 and m:
            n = int(m.group(1))                          # a line the parser skips: retry with its count
            continue
        if p.returncode != 0:
            return f"EXIT{p.returncode}", set(), None, text, argv
        pats, reported = set(), None
        for line in p.stdout.splitlines():
            f = line.split()
            if f and f[0] == "batch":
                reported = int(f[f.index("result") + 1])
            elif f and f[0] == "pattern":
                pats.add(f[2])
        return "OK", pats, reported, text, argv
    return "EXIT2", set(), None, text, argv


def evaluate(status: dict[str, str]) -> dict:
    idx = [FACTORS.index(f) for f in WINDOW]
    union = [p for p, s in status.items() if any(s[i] == "1" for i in idx)]
    changed = [p for p in union if not all(status[p][i] == "1" for i in idx)]
    shapes = [shape(s) for s in status.values()]
    return {"patterns_any_price": len(status), "nonconstant": sum(len(set(s)) > 1 for s in status.values()),
            "status_010": sum(a for a, _ in shapes), "status_101": sum(b for _, b in shapes),
            "window_union": len(union), "window_changed": len(changed),
            "window_share": (len(changed) / len(union)) if union else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--datasets", nargs="*", default=REGISTERED)
    ap.add_argument("--timeout", type=int, default=900, help="seconds per JVM")
    ap.add_argument("--self-test-only", action="store_true")
    ap.add_argument("--tafeng-source", type=Path, metavar="CSV",
                    help="Ta-Feng source file: vary the price of the whole product subclass with the largest "
                         "total utility instead of one item (TAFENG only)")
    ap.add_argument("--mu", nargs="*", default=[], metavar="DATASET=VALUE",
                    help="override the experiment threshold of a dataset (recorded in run_meta.json)")
    a = ap.parse_args()

    problems = self_test_shape()
    for p in problems:
        print("[self-test] FAIL " + p)
    defs = definition_check()
    print(f"[definitions] {defs['databases']} random databases, grid x0.1..x3.0: "
          f"max (definition): {defs['max']}; min (injected fault): {defs['min']}")
    print(f"[definitions] designed case <(1,2)>: {defs['designed']}")
    if defs["max"]["nonconstant"] == 0:
        problems.append("definition check: no pattern changes status, the shape check tested nothing")
    if defs["max"]["status_010"]:
        problems.append(f"definition check: {defs['max']['status_010']} pattern(s) with status 0..1..0")
    if defs["min"]["status_010"] == 0:
        problems.append("fault injection (min over occurrences) produced no 0..1..0: the check cannot see one")
    dz = defs["designed"]
    if shape(dz["max@1/4"]) != (False, True) or shape(dz["max@9/40"])[0] or shape(dz["min@9/40"]) != (True, False):
        problems.append(f"designed case: statuses {dz} do not have the shapes the construction predicts")
    if problems:
        for p in problems:
            print("[self-test] FAIL " + p)
        return 1
    print("[self-test] PASS: shape classifier 12/12; definition check passes; fault injection and designed case caught")
    if a.self_test_only:
        return 0

    if not JAR.exists() or any(p.stat().st_mtime > JAR.stat().st_mtime for p in (ROOT / "src").rglob("*.java")):
        raise SystemExit("the JAR is missing or older than the sources; run: mvn -q package -DskipTests")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if git("ls-files", "--error-unmatch", str(Path(__file__).resolve().relative_to(ROOT))) == "":
        raise SystemExit("commit this tool first: the commit recorded in run_meta.json must contain it")
    cfg = {e["id"]: e for e in load_config()["experiments"]}
    mu = {r["csv_name"]: r["min_util"] for r in cfg[SOURCE_EXPERIMENT]["runs"]}
    overrides = dict(x.split("=", 1) for x in a.mu)
    unknown = set(overrides) - set(mu)
    if unknown:
        raise SystemExit(f"--mu names unknown datasets: {sorted(unknown)}")
    mu.update({d: float(v) for d, v in overrides.items()})

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT_ROOT / run_id
    (out / "raw").mkdir(parents=True)
    (out / "status").mkdir()
    java_v = subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.strip().splitlines()
    meta = {"kind": "PROBE (feasibility, counts only; no timing here is a measurement)",
            "command": " ".join([Path(sys.executable).name, *sys.argv]), "commit": git("rev-parse", "HEAD"),
            "tree": "clean" if not dirty else f"MODIFIED ({len(dirty.splitlines())} files)",
            "jar_sha256": sha256(JAR), "tool_sha256": sha256(Path(__file__).resolve()), "java": java_v[:2], "host": socket.gethostname(),
            "python": platform.python_version(), "timeout_s": a.timeout, "factors_tenths": FACTORS,
            "window_tenths": WINDOW, "thresholds": {d: mu[d] for d in a.datasets},
            "threshold_source": f"experiment {SOURCE_EXPERIMENT}", "threshold_overrides": overrides, "definition_check": defs, "varied_item": {}}
    print(f"[probe] PROBE run {run_id} -> {out.relative_to(ROOT)}; tree {meta['tree']}")

    rows, evals = [], {}
    with tempfile.TemporaryDirectory(prefix="price-grid-") as tmp:
        scratch = Path(tmp)
        for ds in a.datasets:
            eui0 = ROOT / "datasets" / f"{FILES[ds]}_eui.txt"
            prices = read_prices(eui0)
            totals, n = item_totals(ROOT / "datasets" / f"{FILES[ds]}_seq.txt", prices)
            item = min(totals, key=lambda i: (-totals[i], i))
            unlisted = sorted(set(totals) - set(prices))
            group = {item}
            meta["varied_item"][ds] = {"item": item, "price": prices.get(item, 1),
                                       "share_of_total_utility": round(totals[item] / sum(totals.values()), 6),
                                       "items_without_price": len(unlisted)}
            if a.tafeng_source and ds == "TAFENG":
                subclass, multi = tafeng_subclasses(a.tafeng_source)
                if set(subclass) != set(prices):
                    raise SystemExit(f"--tafeng-source numbers {len(subclass)} products, the price file {len(prices)}: "
                                     "not the source this dataset was built from")
                by_sub: dict = {}
                for i, t in totals.items():
                    by_sub[subclass[i]] = by_sub.get(subclass[i], 0) + t
                top = min(by_sub, key=lambda x: (-by_sub[x], x))
                group = {i for i in totals if subclass[i] == top}
                meta["varied_item"][ds] = {"group": "product subclass", "subclass": top, "items": len(group),
                                           "share_of_total_utility": round(by_sub[top] / sum(totals.values()), 6),
                                           "source_sha256": sha256(a.tafeng_source),
                                           "products_with_several_subclasses": multi,
                                           "items_without_price": len(unlisted)}
            sets = {}
            for f in FACTORS:
                eui = scratch / f"{ds}_f{f}_eui.txt"
                scaled = {i: 10 * p for i, p in prices.items()}
                scaled.update({i: 10 for i in unlisted})
                scaled.update({i: f * prices.get(i, 1) for i in group})
                eui.write_text("".join(f"{i}:{p}\n" for i, p in sorted(scaled.items())), encoding="utf-8")
                written = read_prices(eui)
                assert all(written[i] == f * prices.get(i, 1) for i in group) \
                    and all(written[i] == 10 * prices.get(i, 1) for i in written if i not in group) \
                    and len(written) == len(set(prices) | set(totals)), \
                    f"{ds} f={f}: the scaled price file does not carry the intended prices"
                status, pats, reported, text, argv = mine(scratch, ds, eui, mu[ds], n, a.timeout)
                shown = " ".join(x.replace(str(ROOT) + "/", "").replace(tmp + "/", "<scratch>/") for x in argv[5:])
                text = text.replace(str(ROOT) + "/", "").replace(tmp + "/", "<scratch>/")
                (out / "raw" / f"{ds}-f{f}.txt").write_text(shown + "\n" + text, encoding="utf-8")
                rows.append({"dataset": ds, "factor_tenths": f, "status": status, "patterns": len(pats),
                             "reported": reported if reported is not None else "",
                             "consistent": (reported == len(pats)) if reported is not None else ""})
                sets[f] = pats
                print(f"[probe] {ds} f={f / 10:.1f}: {status} patterns={len(pats)} reported={reported}", flush=True)
            if any(r["status"] != "OK" for r in rows if r["dataset"] == ds):
                evals[ds] = {"incomplete": True}
                continue
            allp = set().union(*sets.values())
            status = {p: "".join("1" if p in sets[f] else "0" for f in FACTORS) for p in sorted(allp)}
            with (out / "status" / f"{ds}.tsv").open("w", encoding="utf-8") as fh:
                fh.write("pattern\tstatus_" + "_".join(str(f) for f in FACTORS) + "\n")
                fh.writelines(f"{p}\t{s}\n" for p, s in status.items())
            evals[ds] = evaluate(status)

    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    lines = []
    complete = {d: e for d, e in evals.items() if not e.get("incomplete")}
    for ds, e in evals.items():
        lines.append(f"{ds:12s} " + ("INCOMPLETE (a grid price did not finish)" if e.get("incomplete") else
                     f"patterns {e['patterns_any_price']}, nonconstant {e['nonconstant']}, "
                     f"0..1..0 {e['status_010']}, 1..0..1 {e['status_101']}; window x0.8..x1.2: "
                     f"{e['window_changed']}/{e['window_union']} change status"
                     + (f" ({100 * e['window_share']:.2f}%)" if e["window_share"] is not None else "")))
    shape_ok = all(e["status_010"] == 0 for e in complete.values())
    sig = sum(1 for e in complete.values() if (e["window_share"] or 0) >= SIGNIFICANT_SHARE)
    lines.append(f"shape: {'PASS' if shape_ok else 'FAIL'} -- 0..1..0 on "
                 f"{sum(e['status_010'] > 0 for e in complete.values())} of {len(complete)} complete datasets, "
                 f"{sum(e['nonconstant'] for e in complete.values())} nonconstant patterns tested")
    lines.append(f"significance: {sig} of {len(complete)} complete datasets reach {100 * SIGNIFICANT_SHARE:.0f}% -> "
                 + ("WORTH AN ALGORITHM" if sig >= SIGNIFICANT_DATASETS else "NOT SIGNIFICANT"))
    (out / "evaluation.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta["evaluation"] = evals
    (out / "run_meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if shape_ok else 1


if __name__ == "__main__":
    sys.exit(main())
