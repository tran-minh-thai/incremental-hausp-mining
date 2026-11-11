#!/usr/bin/env python3
"""Exhaustive checks of the append-model statements against the definitions, with fault injection.

A batch on a quantitative sequence database D holds new sequences N and touched sequences T; each
touched S becomes S' = S . A_S (whole itemsets appended after the last itemset of S). Every
quantity below is recomputed from the definition of occurrence and utility on small random
databases; nothing is taken from the miner.

  decomposition   u(a, S') = max(u(a, S), uhat(a, S'))                  (uhat: anchored occurrences,
                                                                         i.e. ending at a position
                                                                         >= the first appended one)
  promotion       every promoted pattern has  du(a) > xi * |a| * U_delta, and the exact formula for
                  H' (kept members of H, plus candidates C, plus the degenerate xi*|a| >= 1 case)
                  equals the HAUSP set mined from scratch on D'
  anchored bound  for every proper backward extension b of s (s is b with leading items removed):
                  uhat(b, S')/|b| <= bnd(s, S')  and  bnd(b, S') <= bnd(s, S')
  backward search the anchored backward search returns a candidate set containing C, and filtering
                  it by du(a) > xi*|a|*U_delta and a not in H gives exactly C

Faults (each must make its check report at least one violation):
  merge_into_last   the appended part is merged into the last itemset of S (quantities summed on a
                    repeated item) instead of being appended; the anchor is the first changed position.
                    Expected NOT to break any statement: with that anchor every old occurrence that
                    changed is anchored, so the statements need only "positions before the anchor are
                    unchanged and no utility decreases". Reported as a finding, not as a fault.
  merge_naive_anchor  the same merge, with the anchor left at the old length of S
  strict_anchor     uhat counts occurrences ending strictly after the first appended position
  udelta_total      U_delta replaced by totalU(D') in the promotion condition
  no_mx             the second component of the anchored bound drops Mx
  putil_short       Putil(S', k) sums the positions before k-1 instead of before k
  no_backward_i     the backward search never adds an item to the first itemset

Usage: python3 analysis/check_append_theory.py [--dbs 50] [--seed 20261111] [--out DIR]
Exit status is non-zero if a check fails on the real definitions, or if a fault goes undetected.
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
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAULTS = ["merge_into_last", "merge_naive_anchor", "strict_anchor", "udelta_total", "no_mx", "putil_short", "no_backward_i"]
XIS = [Fraction(1, 6), Fraction(1, 5), Fraction(1, 4), Fraction(1, 3), Fraction(1, 2)]
FAULT = None

# ------------------------------------------------------------------ data model
# A sequence is a tuple of itemsets; an itemset is a tuple of (item, utility) sorted by item.
# A pattern is a tuple of itemsets; an itemset of a pattern is a sorted tuple of items.


def positions(seq):
    """Utility at each absolute position, itemset by itemset, items in increasing order."""
    return [u for itemset in seq for (_, u) in itemset]


def index(seq):
    """Per itemset: item -> (absolute position, utility)."""
    out, k = [], 0
    for itemset in seq:
        d = {}
        for item, u in itemset:
            d[item] = (k, u)
            k += 1
        out.append(d)
    return out


def occurrences(alpha, seq):
    """Every occurrence of alpha in seq as (start position, end position, utility)."""
    idx = index(seq)
    res = []

    def rec(t, j0, util, start, end):
        if t == len(alpha):
            res.append((start, end, util))
            return
        for j in range(j0, len(idx)):
            d = idx[j]
            if all(i in d for i in alpha[t]):
                rec(t + 1, j + 1, util + sum(d[i][1] for i in alpha[t]),
                    d[alpha[t][0]][0] if t == 0 else start, d[alpha[t][-1]][0])

    rec(0, 0, 0, None, None)
    return res


def u_of(alpha, seq):
    occ = occurrences(alpha, seq)
    return max((o[2] for o in occ), default=0)


def anchored(o, a):
    return o[1] > a if FAULT == "strict_anchor" else o[1] >= a


def uhat(alpha, seq, a):
    return max((o[2] for o in occurrences(alpha, seq) if anchored(o, a)), default=0)


def plen(alpha):
    return sum(len(x) for x in alpha)


def bound(sigma, seq, a):
    """Anchored bound bnd(sigma, S'): max over anchored start positions k of min(first, second)."""
    pu = positions(seq)
    best_at = {}
    for o in occurrences(sigma, seq):
        if anchored(o, a):
            best_at[o[0]] = max(best_at.get(o[0], 0), o[2])
    if not best_at:
        return Fraction(0)
    L = plen(sigma)
    best = None
    for k, uk in best_at.items():
        cut = k - 1 if FAULT == "putil_short" else k
        putil = sum(pu[:max(cut, 0)])
        mx = max(pu[:k], default=0)
        first = Fraction(uk + putil, L + 1)
        second = Fraction(uk, L) if FAULT == "no_mx" else max(Fraction(uk, L), Fraction(mx))
        v = min(first, second)
        best = v if best is None or v > best else best
    return best


def subpatterns(seq):
    """Every pattern that occurs in seq."""
    out = set()
    sets = [[x[0] for x in itemset] for itemset in seq]

    def nonempty_subsets(items):
        n = len(items)
        for mask in range(1, 1 << n):
            yield tuple(items[i] for i in range(n) if mask >> i & 1)

    def rec(j, cur):
        if cur:
            out.add(tuple(cur))
        for jj in range(j, len(sets)):
            for sub in nonempty_subsets(sets[jj]):
                cur.append(sub)
                rec(jj + 1, cur)
                cur.pop()

    rec(0, [])
    return out


def suffixes(beta):
    """Proper backward suffixes of beta: repeatedly drop the first item."""
    out, cur = [], [list(x) for x in beta]
    while plen(cur) > 1:
        cur[0] = cur[0][1:]
        if not cur[0]:
            cur = cur[1:]
        out.append(tuple(tuple(x) for x in cur))
    return out


# ------------------------------------------------------------------ instances

def random_instance(rng: random.Random):
    n_items = rng.choice([4, 5])
    price = {i: rng.randint(1, 6) for i in range(1, n_items + 1)}

    def itemset():
        size = rng.choices([1, 2, 3], weights=[50, 35, 15])[0]
        items = sorted(rng.sample(range(1, n_items + 1), size))
        return tuple((i, price[i] * rng.randint(1, 5)) for i in items)

    def seq(lo, hi):
        return tuple(itemset() for _ in range(rng.randint(lo, hi)))

    D = {sid: seq(1, 3) for sid in range(rng.randint(2, 5))}
    touched = rng.sample(sorted(D), rng.randint(1, len(D)))
    appended = {sid: seq(1, 2) for sid in touched}
    new = {100 + j: seq(1, 3) for j in range(rng.randint(0, 2))}
    return D, appended, new


def apply_batch(D, appended, new):
    """Return D', and for each touched or new sid the first appended position a(S')."""
    Dp, a = dict(D), {}
    for sid, A in appended.items():
        S = D[sid]
        if FAULT in ("merge_into_last", "merge_naive_anchor"):
            last = dict(S[-1])
            for item, u in A[0]:
                last[item] = last.get(item, 0) + u
            merged = S[:-1] + (tuple(sorted(last.items())),) + A[1:]
            old = [x for itemset in S for x in itemset]
            newp = [x for itemset in merged for x in itemset]
            k = 0
            while k < len(old) and old[k] == newp[k]:
                k += 1
            Dp[sid], a[sid] = merged, (len(old) if FAULT == "merge_naive_anchor" else k)
        else:
            Dp[sid] = S + A
            a[sid] = len(positions(S))
    for sid, S in new.items():
        Dp[sid], a[sid] = S, 0
    return Dp, a


def tot(db):
    return sum(sum(positions(S)) for S in db.values())


# ------------------------------------------------------------------ one database

def check_database(D, appended, new, counters):
    Dp, a = apply_batch(D, appended, new)
    upd = sorted(a)                                   # touched and new sequence ids
    U_delta = tot(Dp) - tot(D)
    universe = set()
    for S in Dp.values():
        universe |= subpatterns(S)
    in_D = set()
    for S in D.values():
        in_D |= subpatterns(S)

    uD = {al: sum(u_of(al, S) for S in D.values()) for al in universe}
    uDp = {al: sum(u_of(al, S) for S in Dp.values()) for al in universe}
    du, uh_sum = {}, {}
    for al in universe:
        d, h = 0, 0
        for sid in upd:
            old = u_of(al, D[sid]) if sid in D else 0
            uh = uhat(al, Dp[sid], a[sid])
            new_u = u_of(al, Dp[sid])
            # decomposition
            if new_u > 0 or old > 0:
                counters["decomposition"]["cells"] += 1
                if new_u != max(old, uh):
                    counters["decomposition"]["violations"] += 1
            d += max(0, uh - old)
            h += uh
        du[al], uh_sum[al] = d, h

    # exact increment equals the re-scored one (corollary)
    for al in universe:
        counters["increment"]["cells"] += 1
        if uDp[al] != uD[al] + du[al]:
            counters["increment"]["violations"] += 1

    # anchored bound: every (beta, proper backward suffix sigma, updated S') with beta anchored
    bcache = {}

    def bnd(p, sid):
        key = (p, sid)
        if key not in bcache:
            bcache[key] = bound(p, Dp[sid], a[sid])
        return bcache[key]

    for sid in upd:
        for beta in subpatterns(Dp[sid]):
            ub = uhat(beta, Dp[sid], a[sid])
            if ub == 0:
                continue
            for sigma in suffixes(beta):
                counters["anchored_bound"]["cells"] += 1
                bs = bnd(sigma, sid)
                if Fraction(ub, plen(beta)) > bs or bnd(beta, sid) > bs:
                    counters["anchored_bound"]["violations"] += 1

    items = sorted({i for S in Dp.values() for x in S for (i, _) in x})
    for xi in XIS:
        theta = xi * tot(D)
        theta_p = theta + xi * U_delta
        ud_thr = (tot(Dp) if FAULT == "udelta_total" else U_delta)
        H = {al for al in in_D if uD.get(al, 0) >= plen(al) * theta}
        Hp = {al for al in universe if uDp[al] >= plen(al) * theta_p}
        promoted = Hp - H
        for al in promoted:
            counters["promotion"]["cells"] += 1
            if not du[al] > xi * plen(al) * ud_thr:
                counters["promotion"]["violations"] += 1
        C = {al for al in universe - H if du[al] > xi * plen(al) * ud_thr and xi * plen(al) < 1}
        degenerate = {al for al in universe - H if xi * plen(al) >= 1 and uDp[al] >= plen(al) * theta_p}
        counters["degenerate_found"] += len(degenerate)
        formula = {al for al in H if uD[al] + du[al] >= plen(al) * theta_p} | \
                  {al for al in C if uD[al] + du[al] >= plen(al) * theta_p} | degenerate
        counters["h_prime_formula"]["cells"] += 1
        counters["h_prime_formula"]["patterns_compared"] += len(Hp | formula)
        if formula != Hp:
            counters["h_prime_formula"]["violations"] += 1

        # anchored backward search
        starts = sorted({i for sid in upd for k, d in enumerate(index(Dp[sid]))
                         for i, (pos, _) in d.items() if pos >= a[sid]})
        cand, stack, visited = set(), [((s,),) for s in starts], 0
        while stack:
            sig = stack.pop()
            visited += 1
            L = plen(sig)
            if sum(uhat(sig, Dp[sid], a[sid]) for sid in upd) > xi * L * U_delta:
                cand.add(sig)
            if sum(bnd(sig, sid) for sid in upd) > xi * U_delta and xi * (L + 1) < 1:
                first = sig[0]
                if FAULT != "no_backward_i":
                    for i in items:
                        if i < first[0]:
                            stack.append(((i,) + first,) + sig[1:])
                for i in items:
                    stack.append(((i,),) + sig)
        counters["backward_search"]["cells"] += 1
        counters["backward_search"]["c_members"] += len(C)
        counters["backward_search"]["nodes_visited"] += visited
        filtered = {al for al in cand if du[al] > xi * plen(al) * U_delta and al not in H}
        if not C <= cand or filtered != C:
            counters["backward_search"]["violations"] += 1


def run(n_dbs, seed, fault):
    global FAULT
    FAULT = fault
    rng = random.Random(seed)
    counters = {k: {"cells": 0, "violations": 0} for k in
                ["decomposition", "increment", "promotion", "h_prime_formula", "anchored_bound",
                 "backward_search"]}
    counters["h_prime_formula"]["patterns_compared"] = 0
    counters["backward_search"]["c_members"] = 0
    counters["backward_search"]["nodes_visited"] = 0
    counters["degenerate_found"] = 0
    stats = {"touched_sequences": 0, "new_sequences": 0, "multi_item_itemsets": 0}
    for _ in range(n_dbs):
        D, appended, new = random_instance(rng)
        stats["touched_sequences"] += len(appended)
        stats["new_sequences"] += len(new)
        stats["multi_item_itemsets"] += sum(len(x) > 1 for S in list(D.values()) + list(appended.values())
                                            + list(new.values()) for x in S)
        check_database(D, appended, new, counters)
    FAULT = None
    return counters, stats


# ------------------------------------------------------------------ provenance

def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbs", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    dirty = [l for l in git("status", "--porcelain", "--untracked-files=no").splitlines() if l]
    if dirty:
        sys.exit(f"refusing: the working tree at {ROOT} has {len(dirty)} modified tracked files")

    t0 = time.time()
    clean, stats = run(args.dbs, args.seed, None)
    fail = [k for k, v in clean.items() if isinstance(v, dict) and v["violations"]]
    empty = [k for k, v in clean.items() if isinstance(v, dict) and v["cells"] == 0]
    faults = {}
    for f in FAULTS:
        c, _ = run(args.dbs, args.seed, f)
        faults[f] = {k: v["violations"] for k, v in c.items() if isinstance(v, dict)}
    undetected = [f for f, v in faults.items() if sum(v.values()) == 0 and f != "merge_into_last"]

    summary = {"kind": "INVARIANT (exhaustive check on generated databases; no timing)",
               "dbs": args.dbs, "seed": args.seed, "xi_values": [str(x) for x in XIS],
               "instance_stats": stats, "checks": clean, "faults": faults,
               "failed_checks": fail, "empty_checks": empty, "undetected_faults": undetected,
               "seconds": round(time.time() - t0, 1)}
    out = args.out or ROOT / "results-invariant" / "theory-checks" / ("append-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    code = Path(__file__).read_bytes()
    meta = {"command": "python3 analysis/check_append_theory.py " + " ".join(sys.argv[1:]),
            "git_commit": git("rev-parse", "HEAD"), "tree": "clean",
            "code_sha256": hashlib.sha256(code).hexdigest(),
            "python": platform.python_version(), "machine": platform.machine()}
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print("wrote", out.relative_to(ROOT).as_posix())
    sys.exit(1 if fail or empty or undetected else 0)


if __name__ == "__main__":
    main()
