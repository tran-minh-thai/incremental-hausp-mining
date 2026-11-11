#!/usr/bin/env python3
"""Count, at length 3, what the anchored backward search of an append batch would face (counting only; no timing).

Setting as in analysis/count_append_candidates.py: a batch appends baskets to stored customer sequences and
adds new ones; an occurrence in a touched or new sequence S' is anchored when it ends at a position of the
batch (at or after a(S'), the first appended position). With U the utility of the batch, theta = xi*totalU(D),
theta' = theta + xi*U, per batch:

  nodes2      #{sigma of length 2 : B(sigma) > xi*U}: the length-2 nodes the backward search extends
              (B(sigma) = sum over S' of the anchored bound b(sigma, S'), Definition 4; every such node also
              has its last item extended, since B is antimonotone under backward extension)
  c3_uhat     #{alpha of length 3 : sum over S' of uhat(alpha, S') > 3 xi U}: the search's candidate set
              (Theorem 5: the candidates are generated only below extended nodes, which loses none)
  c3_du       #{alpha : du(alpha) > 3 xi U}
  c3_C        #{alpha in c3_du : alpha not a HAUSP of D}: c3', the set C at length 3
  h3, p3      length-3 HAUSPs after the batch, and those promoted at this batch
  anchored3   sum over S' of the number of distinct length-3 patterns with an anchored occurrence in S'
  bound3      sum over S' of n_A(S') * C(|S'| - 1, 2) (Proposition 8), the bound on anchored3

Every promoted pattern must lie in c3_C (Theorem 2); the tool exits non-zero if one does not. Exact integer
arithmetic throughout (the bound is compared as 6*b against 6*xi*U).

u_D and u_{D'} are computed only for patterns of items whose SWU over D' is at least 3 theta: no other
length-3 pattern can be a HAUSP of D or of D'.

Self-test (--self-test): on generated databases every count is recomputed from the definitions with the
functions of analysis/check_append_theory.py (exhaustive over every length-3 subpattern), and three injected
faults must be caught: no_anchor (every occurrence counts as anchored), prune_3xi (nodes extended only when
B > 3 xi U, which over-prunes) and udelta_total (U replaced by totalU(D')).

Usage: python3 analysis/count_backward_length3.py --data NAME=DIR --xi NAME=X [--schedules L5] [--out DIR]
       python3 analysis/count_backward_length3.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import platform
import random
import subprocess
import sys
import time
from collections import defaultdict
from fractions import Fraction
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import count_append_candidates as cac  # noqa: E402
import update_profile as up  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAULT = None


def flat(itemsets):
    items, utils, tids = [], [], []
    for t, its in enumerate(itemsets):
        for i, u in sorted(its):
            items.append(i)
            utils.append(u)
            tids.append(t)
    return items, utils, tids


def key2(items, tids, q, r):
    return (items[q], 0 if tids[q] == tids[r] else 1, items[r])


def key3(items, tids, p, q, r):
    return (items[p], 0 if tids[p] == tids[q] else 1, items[q], 0 if tids[q] == tids[r] else 1, items[r])


def bound2_six(items, utils, tids, a):
    """6 * b(sigma, S') for every length-2 sigma anchored in S'."""
    L = len(items)
    pre, mx = [0] * (L + 1), [0] * (L + 1)
    for k in range(L):
        pre[k + 1] = pre[k] + utils[k]
        mx[k + 1] = max(mx[k], utils[k])
    best_at = {}
    lo = 0 if FAULT == "no_anchor" else a
    for r in range(lo, L):
        for q in range(r):
            k = key2(items, tids, q, r)
            v = utils[q] + utils[r]
            kk = (k, q)
            if v > best_at.get(kk, -1):
                best_at[kk] = v
    out = {}
    for (k, q), uh in best_at.items():
        six = min(2 * (uh + pre[q]), max(3 * uh, 6 * mx[q]))
        if six > out.get(k, -1):
            out[k] = six
    return out


def seq_length3(items, utils, tids, a, nodes2):
    """Per pattern: uhat over anchored occurrences under extended suffixes; distinct anchored patterns; u(alpha, S)."""
    L = len(items)
    lo = 0 if FAULT == "no_anchor" else a
    anchored_all = set()
    uh = {}
    for r in range(lo, L):
        for q in range(r):
            k2 = key2(items, tids, q, r)
            keep = k2 in nodes2
            for p in range(q):
                k3 = key3(items, tids, p, q, r)
                anchored_all.add(k3)
                if keep:
                    v = utils[p] + utils[q] + utils[r]
                    if v > uh.get(k3, -1):
                        uh[k3] = v
    old = {}
    if uh and a > 0:
        need = {x for k in uh for x in (k[0], k[2], k[4])}
        pos = [k for k in range(a) if items[k] in need]
        for p, q, r in itertools.combinations(pos, 3):
            k3 = key3(items, tids, p, q, r)
            if k3 in uh:
                v = utils[p] + utils[q] + utils[r]
                if v > old.get(k3, -1):
                    old[k3] = v
    return uh, old, len(anchored_all)


def utilities3(items, utils, tids, a, P):
    """u(alpha, S) on the positions before a and u(alpha, S') on all, for length-3 patterns of items of P."""
    pos = [k for k in range(len(items)) if items[k] in P]
    uD, uDp = {}, {}
    for p, q, r in itertools.combinations(pos, 3):
        k3 = key3(items, tids, p, q, r)
        v = utils[p] + utils[q] + utils[r]
        if v > uDp.get(k3, -1):
            uDp[k3] = v
        if r < a and v > uD.get(k3, -1):
            uD[k3] = v
    return uD, uDp


def batch_counts(rows, s, e, xi):
    day, util = rows["day"], rows["util"]
    before, inb = day < s, (day >= s) & (day < e)
    totD, U = int(util[before].sum()), int(util[inb].sum())
    u_thr = int(util[day < e].sum()) if FAULT == "udelta_total" else U
    num, den = xi.numerator, xi.denominator
    upd = np.unique(rows["cust"][inb])
    seqs = cac.sequences(rows, upd, e)
    flats = {}
    B2 = defaultdict(int)
    for c, (its, days) in seqs.items():
        fn = next(j for j, d in enumerate(days) if d >= s)
        items, utils_, tids = flat(its)
        a = sum(len(x) for x in its[:fn])
        flats[c] = (items, utils_, tids, a)
        for k, v in bound2_six(items, utils_, tids, a).items():
            B2[k] += v
    factor = 3 if FAULT == "prune_3xi" else 1
    nodes2 = {k for k, v in B2.items() if v * den > 6 * factor * num * u_thr}
    uh_sum, du = defaultdict(int), defaultdict(int)
    anchored3 = bound3 = 0
    for c, (items, utils_, tids, a) in flats.items():
        uh, old, n_anch = seq_length3(items, utils_, tids, a, nodes2)
        anchored3 += n_anch
        L = len(items)
        bound3 += (L - a) * comb(L - 1, 2)
        for k, v in uh.items():
            uh_sum[k] += v
            du[k] += max(0, v - old.get(k, 0))
    need = 3 * num * u_thr
    c3_uhat = {k for k, v in uh_sum.items() if v * den > need}
    c3_du = {k for k, v in du.items() if v * den > need}
    # SWU over D'; patterns of items below 3 theta can be HAUSPs of neither D nor D'
    seq_u = pd.Series(util[day < e]).groupby(rows["cust"][day < e]).sum()
    cu = pd.DataFrame({"c": rows["cust"][day < e], "i": rows["item"][day < e]}).drop_duplicates()
    swu = cu.assign(u=seq_u.reindex(cu["c"]).to_numpy()).groupby("i")["u"].sum()
    P = set(swu.index[swu * den >= 3 * num * totD].tolist())
    uD, uDp = defaultdict(int), defaultdict(int)
    allc = np.unique(rows["cust"][day < e])
    for c, (its, days) in cac.sequences(rows, allc, e).items():
        items, utils_, tids = flat(its)
        fn = next((j for j, d in enumerate(days) if d >= s), len(days))
        a = sum(len(x) for x in its[:fn])
        o, n = utilities3(items, utils_, tids, a, P)
        for k, v in n.items():
            uDp[k] += v
            uD[k] += o.get(k, 0)
    H = {k for k, v in uD.items() if v * den >= 3 * num * totD}
    h3 = {k for k, v in uDp.items() if v * den >= 3 * num * (totD + U)}
    p3 = h3 - H
    c3_C = c3_du - H
    return {"U": U, "nodes2": len(nodes2), "c3_uhat": len(c3_uhat), "c3_du": len(c3_du), "c3_C": len(c3_C),
            "h3": len(h3), "p3": len(p3), "anchored3": anchored3, "bound3": bound3,
            "touched_or_new": len(flats),
            "median_len_touched": float(np.median([len(f[0]) for f in flats.values()])) if flats else 0.0,
            "promoted_missing": len(p3 - c3_C), "promoted_compared": len(p3)}


# ------------------------------------------------------------------ self-test against the definitions

def brute(rows, s, e, xi):
    import check_append_theory as ct
    seqs_e, seqs_s = {}, {}
    for c in np.unique(rows["cust"]):
        for end, store in ((e, seqs_e), (s, seqs_s)):
            got = cac.sequences(rows, [c], end)
            if got:
                store[int(c)] = tuple(tuple(sorted(x)) for x in got[int(c)][0])
    upd = {int(c) for c in np.unique(rows["cust"][(rows["day"] >= s) & (rows["day"] < e)])}
    U = int(rows["util"][(rows["day"] >= s) & (rows["day"] < e)].sum())
    totD = int(rows["util"][rows["day"] < s].sum())
    theta, theta_p = xi * totD, xi * (totD + U)
    a = {c: (len(ct.positions(seqs_s[c])) if c in seqs_s else 0) for c in upd}
    pats3, pats2 = set(), set()
    for S in seqs_e.values():
        for p in ct.subpatterns(S):
            (pats3 if ct.plen(p) == 3 else pats2 if ct.plen(p) == 2 else set()).add(p)

    def key(p):
        flat_ = [(i, 0 if j > 0 else 1) for its in p for j, i in enumerate(its)]
        out = [flat_[0][0]]
        for i, sep in flat_[1:]:
            out += [sep, i]
        return tuple(out)

    nodes2 = {key(p) for p in pats2 if sum(ct.bound(p, seqs_e[c], a[c]) for c in upd) > xi * U}
    res = {k: 0 for k in ("c3_uhat", "c3_du", "c3_C", "h3", "p3")}
    anchored3 = 0
    for p in pats3:
        uh = du = 0
        for c in upd:
            h = ct.uhat(p, seqs_e[c], a[c])
            anchored3 += h > 0
            uh += h
            du += max(0, h - (ct.u_of(p, seqs_s[c]) if c in seqs_s else 0))
        uD = sum(ct.u_of(p, S) for S in seqs_s.values())
        uDp = sum(ct.u_of(p, S) for S in seqs_e.values())
        res["c3_uhat"] += uh > 3 * xi * U
        if du > 3 * xi * U:
            res["c3_du"] += 1
            res["c3_C"] += not uD >= 3 * theta
        if uDp >= 3 * theta_p:
            res["h3"] += 1
            res["p3"] += not uD >= 3 * theta
    bound3 = sum((len(ct.positions(seqs_e[c])) - a[c]) * comb(len(ct.positions(seqs_e[c])) - 1, 2) for c in upd)
    return {"nodes2": len(nodes2), **res, "anchored3": anchored3, "bound3": bound3}


def self_test():
    global FAULT
    keys = ("nodes2", "c3_uhat", "c3_du", "c3_C", "h3", "p3", "anchored3", "bound3")
    report = {}
    for fault in (None, "no_anchor", "prune_3xi", "udelta_total"):
        rng = random.Random(3)
        bad = cells = acting = 0
        for _ in range(60):
            rows = cac.gen_rows(rng)
            days = np.unique(rows["day"])
            if len(days) < 3:
                continue
            s = int(days[len(days) // 2])
            e = int(days[-1]) + 1
            xi = Fraction(rng.choice([1, 2, 3]), rng.choice([20, 40, 80]))
            FAULT = None
            ref = brute(rows, s, e, xi)
            FAULT = fault
            got = batch_counts(rows, s, e, xi)
            FAULT = None
            cells += 1
            acting += ref["c3_uhat"] > 0
            bad += any(got[k] != ref[k] for k in keys)
        report[fault or "none"] = {"batches": cells, "with_candidates": acting, "mismatching": bad}
    ok = (report["none"]["mismatching"] == 0 and report["none"]["with_candidates"] > 0
          and all(report[f]["mismatching"] > 0 for f in ("no_anchor", "prune_3xi", "udelta_total")))
    return ok, report


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="*", default=[])
    ap.add_argument("--xi", nargs="*", default=[])
    ap.add_argument("--schedules", nargs="*", default=["L5"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if args.self_test:
        ok, rep = self_test()
        print(json.dumps(rep, indent=2), "\nSELF-TEST", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    if [l for l in git("status", "--porcelain", "--untracked-files=no").splitlines() if l]:
        sys.exit(f"refusing: the working tree at {ROOT} has modified tracked files")
    xis = {k: Fraction(v) for k, v in (x.split("=", 1) for x in args.xi)}
    out = args.out or ROOT / "results-invariant" / "backward-length3" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    records, inputs = [], {}
    for spec in args.data:
        name, d = spec.split("=", 1)
        npz = Path(d) / f"{name}_rows.npz"
        inputs[name] = {"file": npz.name, "sha256": up.sha256(npz)}
        z = np.load(npz)
        rows = {k: z[k] for k in z.files}
        btab = pd.DataFrame({"cust": rows["cust"], "basket": rows["basket"], "day": rows["day"]}) \
            .drop_duplicates(["cust", "basket"])
        for sched in args.schedules:
            _, batches = up.schedule(btab, sched)
            for k, (s, e) in enumerate(batches, 1):
                t0 = time.time()
                rec = {"dataset": name, "schedule": sched, "batch": k, "start": s, "end": e, "xi": str(xis[name]),
                       **batch_counts(rows, s, e, xis[name])}
                rec["seconds"] = round(time.time() - t0, 1)
                records.append(rec)
                print(json.dumps(rec), flush=True)
                (out / "batches.json").write_text(json.dumps(records, indent=1) + "\n")
    summary = {"kind": "INVARIANT (pattern counts; no timing)", "inputs": inputs, "per_dataset": {}}
    for name in inputs:
        for sched in args.schedules:
            rs = [r for r in records if r["dataset"] == name and r["schedule"] == sched]
            ratio = [r["c3_C"] / r["h3"] if r["h3"] else float("inf") for r in rs]
            summary["per_dataset"][f"{name}/{sched}"] = {
                "median_c3C_over_h3": float(np.median(ratio)) if rs else None,
                "median_anchored3_over_bound3": float(np.median([r["anchored3"] / r["bound3"] for r in rs if r["bound3"]]))
                if rs else None,
                "promoted_missing_total": sum(r["promoted_missing"] for r in rs),
                "promoted_compared_total": sum(r["promoted_compared"] for r in rs)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    meta = {"command": "python3 analysis/count_backward_length3.py " + " ".join(
                a.split("=")[0] + "=" + Path(a.split("=", 1)[1]).name if a.count("=") and "/" in a else a
                for a in sys.argv[1:]),
            "git_commit": git("rev-parse", "HEAD"), "tree": "clean",
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python": platform.python_version()}
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    sys.exit(1 if any(r["promoted_missing"] for r in records) else 0)


if __name__ == "__main__":
    main()
