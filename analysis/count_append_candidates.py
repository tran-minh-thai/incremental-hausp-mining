#!/usr/bin/env python3
"""Count the length-2 promotion candidates of an append batch on real data (counting only; no timing).

A batch appends baskets to stored customer sequences and adds new ones (analysis/update_profile.py).
For a length-2 pattern a (S-extension <{x},{y}> or I-extension <{x,y}>, x < y) and each touched or new
sequence S', an anchored occurrence ends at a position of the batch; uhat(a, S') is the largest utility
of such an occurrence and du(a) = sum over S' of max(0, uhat(a, S') - u(a, S)). With U the utility of
the batch, theta = xi * totalU(D) and theta' = theta + xi * U:

  c2_uhat  #{a : sum uhat(a, S') > 2 xi U}          (the count named c2 in the measurement request)
  c2_du    #{a : du(a) > 2 xi U}
  c2_C     #{a in c2_du : a not a HAUSP of D}       (the set C restricted to length 2)
  h2       #{a : u_{D'}(a) >= 2 theta'}             (length-2 HAUSPs after the batch)
  p2       #{a in h2 : u_D(a) < 2 theta}            (promoted at this batch)
  *_swu    the same counts restricted to patterns whose two items have SWU over D' >= 2 theta'; no length-2
           HAUSP of D' has an item below that, so the restriction loses no promoted pattern, but it reads
           one number per item from the untouched sequences. The registered rule names c2 without it; both
           readings are reported, the unrestricted one first.

Every promoted pattern must lie in c2_C; the tool exits non-zero if one does not.

The last item y of a candidate is pruned by the anchored bound: no length-2 pattern ending in y can
reach the candidate condition unless B(<y>) = sum over S' of b(<y>, S') > xi * U, with
b(<y>, S') = max over anchored positions k of y of min((u + Putil(k)) / 2, max(u, Mx(k))). This is the
anchored-bound statement checked exhaustively by analysis/check_append_theory.py, so the counts stay exact.
u_{D'} is computed only for patterns of items whose SWU over D' is at least 2 theta (no other length-2
pattern can be a HAUSP of D or of D').

Usage: python3 analysis/count_append_candidates.py --data NAME=DIR ... --xi NAME=X ... [--out DIR]
       python3 analysis/count_append_candidates.py --self-test
DIR holds <NAME>_rows.npz from analysis/build_raw_sequences.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import subprocess
import sys
import time
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_profile as up  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAULT = None
CAP = 50_000_000      # candidate pairs held at once; above it the batch is reported as exceeded


def sequences(rows, custs, end_day):
    """Per customer: list of itemsets (list of (item, util)) of baskets before end_day, and basket days."""
    out = {}
    sel = np.isin(rows["cust"], custs) & (rows["day"] < end_day)
    c, b, d, i, u = (rows[k][sel] for k in ("cust", "basket", "day", "item", "util"))
    o = np.lexsort((i, b, c))
    c, b, d, i, u = c[o], b[o], d[o], i[o], u[o]
    if not len(c):
        return out
    starts = np.r_[0, np.nonzero(np.diff(c))[0] + 1, len(c)]
    for s, e in zip(starts[:-1], starts[1:]):
        bs = b[s:e]
        cuts = np.r_[0, np.nonzero(np.diff(bs))[0] + 1, e - s]
        itemsets = [list(zip(i[s + x:s + y].tolist(), u[s + x:s + y].tolist())) for x, y in zip(cuts[:-1], cuts[1:])]
        days = [int(d[s + x]) for x in cuts[:-1]]
        out[int(c[s])] = (itemsets, days)
    return out


def seq_bound_y(itemsets, first_new):
    """b(<y>, S') for every item y with an anchored occurrence."""
    flat = [u for its in itemsets for (_, u) in its]
    pos, best, putil, mx = 0, {}, 0, 0
    for j, its in enumerate(itemsets):
        for (y, u) in its:
            if j >= first_new or FAULT == "no_anchor":
                v = min(Fraction(u + putil, 2), Fraction(max(u, mx)))
                if v > best.get(y, -1):
                    best[y] = v
            putil += u
            mx = max(mx, u)
            pos += 1
    return best


def pair_utils(itemsets, ys, first_new, xs=None):
    """For pairs ending in an item of ys: (u over occurrences ending before first_new, uhat over anchored ones).

    Keys: ("S", x, y) for <{x},{y}> and ("I", x, y) for <{x,y}>. xs restricts the first item."""
    old, anc = {}, {}
    pref = {}                       # item -> largest utility in the itemsets before the current one
    for j, its in enumerate(itemsets):
        anchored = j >= first_new or FAULT == "no_anchor"
        target = anc if anchored else old
        for (y, uy) in its:
            if y not in ys:
                continue
            for x, ux in pref.items():
                if xs is not None and x not in xs:
                    continue
                key = ("S", x, y)
                v = ux + uy
                if v > target.get(key, 0):
                    target[key] = v
            for (x, ux) in its:
                if x >= y:
                    break
                if xs is not None and x not in xs:
                    continue
                key = ("I", x, y)
                v = ux + uy
                if v > target.get(key, 0):
                    target[key] = v
        for (x, ux) in its:
            if ux > pref.get(x, 0):
                pref[x] = ux
    return old, anc


def batch_counts(rows, s, e, xi):
    day = rows["day"]
    util = rows["util"]
    before = day < s
    inb = (day >= s) & (day < e)
    totD = int(util[before].sum())
    U = int(util[inb].sum())
    u_thr = int(util[day < e].sum()) if FAULT == "udelta_total" else U
    theta = xi * totD
    theta_p = theta + xi * U
    upd = np.unique(rows["cust"][inb])
    seqs = sequences(rows, upd, e)
    # anchored bound per last item
    B = defaultdict(Fraction)
    firsts = {}
    for c, (its, days) in seqs.items():
        fn = next(j for j, d in enumerate(days) if d >= s)
        firsts[c] = fn
        for y, v in seq_bound_y(its, fn).items():
            B[y] += v
    ys = {y for y, v in B.items() if v > xi * U}
    # candidate quantities over touched and new sequences
    uh_sum, du = defaultdict(int), defaultdict(int)
    held = 0
    for c, (its, _) in seqs.items():
        old, anc = pair_utils(its, ys, firsts[c])
        for k, v in anc.items():
            uh_sum[k] += v
            du[k] += max(0, v - old.get(k, 0))
        held = len(uh_sum)
        if held > CAP:
            return {"exceeded": True, "pairs_held": held, "ys": len(ys), "U": U}
    c2_uhat = {k for k, v in uh_sum.items() if v > 2 * xi * u_thr}
    c2_du = {k for k, v in du.items() if v > 2 * xi * u_thr}
    # SWU over D' and over D, per item
    seq_u = pd.Series(util[day < e]).groupby(rows["cust"][day < e]).sum()
    cu_items = pd.DataFrame({"c": rows["cust"][day < e], "i": rows["item"][day < e]}).drop_duplicates()
    swu_p = cu_items.assign(u=seq_u.reindex(cu_items["c"]).to_numpy()).groupby("i")["u"].sum()
    P = set(swu_p.index[swu_p >= 2 * theta].tolist())
    # u over D' and D for pairs of items of P, over every sequence of D'
    allc = np.unique(rows["cust"][day < e])
    uDp, uD = defaultdict(int), defaultdict(int)
    for c, (its, days) in sequences(rows, allc, e).items():
        its_p = [[(i, u) for (i, u) in x if i in P] for x in its]
        fn = next((j for j, d in enumerate(days) if d >= s), len(days))
        old, anc = pair_utils([x for x in its_p], P, fn, xs=P)
        for k in set(old) | set(anc):
            o = old.get(k, 0)
            uD[k] += o
            uDp[k] += max(o, anc.get(k, 0))
    Pp = set(swu_p.index[swu_p >= 2 * theta_p].tolist())
    swu_ok = lambda key: key[1] in Pp and key[2] in Pp       # noqa: E731
    H = {k for k, v in uD.items() if v >= 2 * theta}
    h2 = {k for k, v in uDp.items() if v >= 2 * theta_p}
    p2 = h2 - H
    c2_C = c2_du - H
    missing = p2 - c2_C
    return {"exceeded": False, "U": U, "theta": float(theta), "ys": len(ys), "pairs_held": held,
            "c2_uhat": len(c2_uhat), "c2_du": len(c2_du), "c2_C": len(c2_C), "h2": len(h2), "p2": len(p2),
            "c2_uhat_swu": sum(map(swu_ok, c2_uhat)), "c2_du_swu": sum(map(swu_ok, c2_du)),
            "c2_C_swu": sum(map(swu_ok, c2_C)), "promoted_missing_swu": sum(1 for k in p2 if not (k in c2_C and swu_ok(k))),
            "promoted_missing": len(missing), "promoted_compared": len(p2)}


# ------------------------------------------------------------------ self-test against the definitions

def brute(rows, s, e, xi):
    """Every quantity recomputed from the definitions by analysis/check_append_theory.py's functions."""
    import check_append_theory as ct
    seqs_e, seqs_s = {}, {}
    for c in np.unique(rows["cust"]):
        for end, store in ((e, seqs_e), (s, seqs_s)):
            got = sequences(rows, [c], end)
            if got:
                its, _ = got[int(c)]
                store[int(c)] = tuple(tuple(sorted(x)) for x in its)
    upd = {int(c) for c in np.unique(rows["cust"][(rows["day"] >= s) & (rows["day"] < e)])}
    U = int(rows["util"][(rows["day"] >= s) & (rows["day"] < e)].sum())
    totD = int(rows["util"][rows["day"] < s].sum())
    theta, theta_p = xi * totD, xi * totD + xi * U
    pats = set()
    for S in seqs_e.values():
        pats |= {p for p in ct.subpatterns(S) if ct.plen(p) == 2}
    res = {k: set() for k in ("c2_uhat", "c2_du", "c2_C", "h2", "p2")}
    swu = defaultdict(int)
    for S in seqs_e.values():
        for i in {x for its in S for (x, _) in its}:
            swu[i] += sum(ct.positions(S))
    for p in pats:
        uh = du = 0
        for c in upd:
            Sp = seqs_e[c]
            a = len(ct.positions(seqs_s[c])) if c in seqs_s else 0
            h = ct.uhat(p, Sp, a)
            uh += h
            du += max(0, h - (ct.u_of(p, seqs_s[c]) if c in seqs_s else 0))
        uD = sum(ct.u_of(p, S) for S in seqs_s.values())
        uDp = sum(ct.u_of(p, S) for S in seqs_e.values())
        key = ("S", p[0][0], p[1][0]) if len(p) == 2 else ("I", p[0][0], p[0][1])
        if uh > 2 * xi * U:
            res["c2_uhat"].add(key)
        if du > 2 * xi * U:
            res["c2_du"].add(key)
            if not uD >= 2 * theta:
                res["c2_C"].add(key)
        if uDp >= 2 * theta_p:
            res["h2"].add(key)
            if not uD >= 2 * theta:
                res["p2"].add(key)
    out = {k: len(v) for k, v in res.items()}
    ok = lambda key: swu[key[1]] >= 2 * theta_p and swu[key[2]] >= 2 * theta_p   # noqa: E731
    for k in ("c2_uhat", "c2_du", "c2_C"):
        out[k + "_swu"] = sum(map(ok, res[k]))
    return out


def gen_rows(rng):
    recs = []
    for c in range(rng.randint(3, 8)):
        for b in range(rng.randint(1, 5)):
            d = rng.randint(0, 20)
            for i in rng.sample(range(5), rng.randint(1, 3)):
                recs.append((c, b, d, i, rng.randint(1, 30)))
    if rng.random() < 0.5:          # a heavy untouched part on other items, so that the SWU restriction acts
        for c in range(100, 100 + rng.randint(1, 4)):
            for b in range(rng.randint(1, 3)):
                for i in rng.sample(range(5, 8), rng.randint(1, 2)):
                    recs.append((c, b, rng.randint(0, 9), i, rng.randint(40, 200)))
    df = pd.DataFrame(recs, columns=["cust", "basket", "day", "item", "util"])
    df = df.sort_values(["cust", "day", "basket"])
    df["basket"] = df.groupby("cust")["basket"].transform(lambda x: pd.factorize(x)[0])   # rank in day order
    df["day"] = df.groupby(["cust", "basket"])["day"].transform("min")
    return {k: df[k].to_numpy() for k in df.columns}


def self_test():
    global FAULT
    rng = random.Random(3)
    report = {}
    for fault in (None, "no_anchor", "udelta_total"):
        FAULT = fault
        compared = bad = missing = 0
        r = random.Random(3)
        for _ in range(120):
            rows = gen_rows(r)
            s, e = 10, 21
            if not ((rows["day"] < s).any() and ((rows["day"] >= s) & (rows["day"] < e)).any()):
                continue
            xi = Fraction(r.choice([1, 2, 3]), 20)
            got = batch_counts(rows, s, e, xi)
            missing += got["promoted_missing"]
            FAULT, f0 = None, FAULT
            ref = brute(rows, s, e, xi)
            FAULT = f0
            compared += 1
            if any(got[k] != ref[k] for k in ref):
                bad += 1
        report[fault or "none"] = {"batches_compared": compared, "disagreeing": bad, "promoted_missing": missing}
    FAULT = None
    # the shared sequence builder against a direct construction from the rows
    r = random.Random(9)
    sb = 0
    for _ in range(100):
        rows = gen_rows(r)
        got = sequences(rows, np.unique(rows["cust"]), 15)
        df = pd.DataFrame(rows)
        df = df[df["day"] < 15]
        ref = {int(c): ([sorted(zip(gg["item"].tolist(), gg["util"].tolist())) for _, gg in g.groupby("basket")],
                        [int(gg["day"].iloc[0]) for _, gg in g.groupby("basket")])
               for c, g in df.groupby("cust")}
        sb += int({c: ([sorted(x) for x in v[0]], v[1]) for c, v in got.items()} != ref)
    report["sequence_builder"] = {"cases": 100, "disagreeing": sb}
    ok = (report["sequence_builder"]["disagreeing"] == 0 and report["none"]["disagreeing"] == 0 and report["none"]["batches_compared"] > 0
          and report["none"]["promoted_missing"] == 0
          and report["no_anchor"]["disagreeing"] > 0
          and report["udelta_total"]["promoted_missing"] > 0)
    return ok, report


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="*", default=[])
    ap.add_argument("--xi", nargs="*", default=[])
    ap.add_argument("--schedules", nargs="*", default=["L5", "W8"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if args.self_test:
        ok, rep = self_test()
        print(json.dumps(rep, indent=2), "\nSELF-TEST", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    dirty = [l for l in git("status", "--porcelain", "--untracked-files=no").splitlines() if l]
    if dirty:
        sys.exit(f"refusing: the working tree at {ROOT} has {len(dirty)} modified tracked files")
    xis = {k: Fraction(v) for k, v in (x.split("=", 1) for x in args.xi)}
    out = args.out or ROOT / "results-invariant" / "append-candidates" / time.strftime("%Y%m%d-%H%M%S")
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
                rec = {"dataset": name, "schedule": sched, "batch": k, "start": s, "end": e,
                       "xi": str(xis[name]), **batch_counts(rows, s, e, xis[name])}
                rec["seconds"] = round(time.time() - t0, 1)
                records.append(rec)
                print(json.dumps(rec), flush=True)
                (out / "batches.json").write_text(json.dumps(records, indent=1) + "\n")
    rule, verdict = {}, {}
    for col in ("c2_uhat", "c2_uhat_swu"):
        rule[col] = {}
        for name in inputs:
            per = {}
            for sched in args.schedules:
                rs = [r[col] / r["h2"] if (not r["exceeded"] and r["h2"]) else float("inf")
                      for r in records if r["dataset"] == name and r["schedule"] == sched]
                per[sched] = float(np.median(rs)) if rs else None
            rule[col][name] = {"median_c2_over_h2": per,
                               "meets": all(v is not None and v <= 10 for v in per.values())}
        verdict[col] = {"datasets_meeting": sum(v["meets"] for v in rule[col].values()), "datasets": len(inputs),
                        "passes": sum(v["meets"] for v in rule[col].values()) >= 3}
    verdict.update({
               "promoted_missing_total": sum(r.get("promoted_missing", 0) for r in records),
               "promoted_missing_swu_total": sum(r.get("promoted_missing_swu", 0) for r in records)})
    summary = {"kind": "INVARIANT (pattern counts; no timing)", "inputs": inputs, "rule": rule, "verdict": verdict,
               "note": "a batch with h2 = 0 or an exceeded count enters the median as infinity"}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    meta = {"command": "python3 analysis/count_append_candidates.py " + " ".join(
                a.split("=")[0] + "=" + Path(a.split("=", 1)[1]).name if a.count("=") and "/" in a else a
                for a in sys.argv[1:]),
            "git_commit": git("rev-parse", "HEAD"), "tree": "clean",
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python": platform.python_version()}
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    sys.exit(1 if verdict["promoted_missing_total"] or verdict["promoted_missing_swu_total"] else 0)


if __name__ == "__main__":
    main()
