#!/usr/bin/env python3
"""Why the number of patterns jumps between batches on one dataset (counts only, no timing).

On Ta-Feng at minUtil 0.16% the five-batch exactness run reports 22, 38,766 and 1,863 patterns
at batches 1, 2 and 3. This tool reads the dataset and the pattern lists the re-mining oracle
wrote for those batches (EHAUSM_Remining with enableIO, files out/EHAUSM_Remining_<DS>_B<b>_mu<m>.txt)
and answers three questions from the data:

  * how long the sequences and how large the itemsets of the dataset are;
  * how many sequences support each pattern of a batch, recomputed here by subsequence
    containment in the cumulative database, and checked against the support the oracle printed;
  * whether the patterns of a batch concentrate in a few sequences;
  * for the sequence holding most of them, the patterns it generates on its own: every
    subsequence whose average utility in that sequence reaches the threshold, enumerated from
    the sequence alone and compared, as a set, with the patterns the oracle reports with that
    sequence as their only support.

The batches are cut exactly as QSDB_Parser.loadDBByRatios cuts them (file order, sizes rounded,
the last batch takes the remainder), and the threshold of each batch is recomputed from the data
and compared with the one printed in the oracle's file, so a dump from another schedule or another
version of the data is refused rather than read.

It covers the two settings in which the paper reports the jump: the five-batch schedule at
0.16% (batches 1-4) and the single pass at 0.15% and 0.1%. The oracle's files are not in the
repository (out/ is ignored); they are written by the exactness experiments run with the
oracle's enableIO on, and this tool refuses a file whose threshold the data does not reproduce.

    python3 analysis/diagnose_pattern_jump.py          # writes analysis_out/paper/pattern_jump.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_db(seq_path: Path, eui_path: Path):
    """Sequences as lists of itemsets of (item, utility), parsed as QSDB_Parser parses them."""
    profit = {}
    for line in eui_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("@"):
            continue
        k, v = line.split(":")
        profit[int(k)] = int(v)
    db = []
    for line in seq_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("@"):
            continue
        seq, cur = [], []
        for tok in line.split():
            if tok == "-1":
                if cur:
                    seq.append(sorted(cur))
                cur = []
            elif tok == "-2":
                break
            else:
                m = re.fullmatch(r"(\d+)(?:\[(\d+)\])?", tok)
                if m is None:
                    continue
                item, q = int(m.group(1)), int(m.group(2) or 1)
                cur.append((item, q * profit.get(item, 1)))
        if cur:
            seq.append(sorted(cur))
        if seq:
            db.append(seq)
    return db, profit


def cut(n: int, ratios: list[float]) -> list[tuple[int, int]]:
    """[start, end) of each batch, as loadDBByRatios computes them (Java Math.round)."""
    out, at = [], 0
    for i, r in enumerate(ratios):
        size = n - at if i == len(ratios) - 1 else int((n * r) + 0.5)
        out.append((at, min(n, at + size)))
        at = min(n, at + size)
    return out


def parse_pattern(text: str) -> list[frozenset[int]]:
    return [frozenset(int(x) for x in grp.split(",")) for grp in re.findall(r"\(([^)]*)\)", text)]


def read_dump(path: Path):
    threshold, rows = None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# Threshold:"):
            threshold = float(line.split(":", 1)[1])
        elif line.startswith("<"):
            pat, au, sup = line.split("\t")
            rows.append((parse_pattern(pat), float(au), int(sup)))
    return threshold, rows


def contains(seq_sets: list[set[int]], pattern: list[frozenset[int]]) -> bool:
    """Earliest-match subsequence containment, itemset by itemset."""
    j = 0
    for s in seq_sets:
        if pattern[j] <= s:
            j += 1
            if j == len(pattern):
                return True
    return False


def alone(seq, threshold: float) -> set[tuple]:
    """Patterns the sequence supports on its own with average utility >= threshold.

    The utility of a pattern in one sequence is its largest over all embeddings, the average
    divides by the number of items, and an embedding is a set of item positions (positions in
    one itemset of the sequence form one itemset of the pattern). A pattern of L items needs
    the L largest utilities of the sequence to reach threshold * L, which bounds L.
    """
    pos = [(t, i, u) for t, its in enumerate(seq) for i, u in its]
    top = sorted((u for _, _, u in pos), reverse=True)
    lmax = max((L for L in range(1, len(pos) + 1) if sum(top[:L]) >= threshold * L), default=0)
    best: dict[tuple, int] = {}
    for L in range(1, lmax + 1):
        for comb in combinations(range(len(pos)), L):
            u = sum(pos[k][2] for k in comb)
            if u < threshold * L:
                continue
            groups: dict[int, list[int]] = defaultdict(list)
            for k in comb:
                groups[pos[k][0]].append(pos[k][1])
            pat = tuple(frozenset(groups[g]) for g in sorted(groups))
            if any(len(g) != len(set(groups[k])) for g, k in zip(pat, sorted(groups))):
                continue                      # the same item twice in one itemset is no pattern
            best[pat] = max(best.get(pat, 0), u)
    return set(best)


#: Settings in which the paper reports the jump: (batch ratios, minUtil as in the file name, batches).
RUNS = {"five_batches_0.0016": ((0.2, 0.2, 0.2, 0.2, 0.2), "0.0016", (1, 2, 3, 4)),
        "single_pass_0.0015": ((1.0,), "0.0015", (0,)),
        "single_pass_0.001": ((1.0,), "0.001", (0,))}


def diagnose(db, profit, dataset: str, dumps: Path, ratios, mu_text: str, batch: int) -> dict:
    spans = cut(len(db), list(ratios))
    su = [sum(u for its in s for _, u in its) for s in db]
    path = dumps / f"EHAUSM_Remining_{dataset}_B{batch}_mu{mu_text}.txt"
    if not path.exists():
        sys.exit(f"{path.relative_to(ROOT)} not found")
    printed, rows = read_dump(path)
    end = spans[batch][1]
    threshold = float(mu_text) * sum(su[:end])
    if printed is None or abs(threshold - printed) > 1e-6 * max(1.0, printed):
        sys.exit(f"{path.name}: threshold recomputed from the data is {threshold:.4f}, the file prints "
                 f"{printed}; the file comes from another schedule or another version of the data")
    seq_sets = [[{i for i, _ in its} for its in s] for s in db]
    index = defaultdict(set)
    for sid in range(end):
        for its in seq_sets[sid]:
            for i in its:
                index[i].add(sid)
    support_of, holders = Counter(), Counter()
    only_by: dict[int, set] = defaultdict(set)
    mismatch = 0
    for pat, _au, printed_sup in rows:
        cands = set.intersection(*(index[i] for i in set().union(*pat)))
        hold = [sid for sid in cands if contains(seq_sets[sid], pat)]
        mismatch += len(hold) != printed_sup
        support_of[len(hold)] += 1
        if len(hold) == 1:
            only_by[hold[0]].add(tuple(pat))
        if len(hold) <= 2:
            for sid in hold:
                holders[sid] += 1
    top = holders.most_common(3)
    info = {"patterns": len(rows), "threshold": threshold, "cumulative_sequences": end,
            "support_mismatches_against_oracle": mismatch,
            "support_histogram": {str(k): v for k, v in sorted(support_of.items())},
            "top_holders": [{"sid": sid, "patterns_with_support_at_most_2": c,
                             "batch": next(i for i, (s, e) in enumerate(spans) if s <= sid < e),
                             "itemsets": len(db[sid]), "items": sum(len(x) for x in db[sid]),
                             "utility": su[sid]} for sid, c in top],
            "lead_holder": None}
    if top:
        lead = top[0][0]
        own = alone(db[lead], threshold)
        reported = only_by.get(lead, set())
        dump = {tuple(p) for p, _, _ in rows}
        item, u = max(((i, u) for its in db[lead] for i, u in its), key=lambda x: x[1])
        info["lead_holder"] = {
            "sid": lead, "largest_line": {"item": item, "utility": u, "unit_profit": profit.get(item, 1),
                                          "quantity": u // profit.get(item, 1)},
            "second_largest_line_utility": sorted((u for its in db[lead] for _, u in its), reverse=True)[1],
            "generated_alone": len(own), "generated_alone_in_dump": len(own & dump),
            "max_items_generated": max((sum(len(g) for g in p) for p in own), default=0),
            "reported_with_it_as_only_support": len(reported), "reported_not_generated": len(reported - own)}
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="TAFENG")
    ap.add_argument("--seq", default="datasets/tafeng/TAFENG_seq.txt")
    ap.add_argument("--eui", default="datasets/tafeng/TAFENG_eui.txt")
    ap.add_argument("--dumps", default="out", help="directory holding the oracle's pattern files")
    ap.add_argument("--out", default="analysis_out/paper/pattern_jump.json")
    a = ap.parse_args()

    db, profit = load_db(ROOT / a.seq, ROOT / a.eui)
    lines = [u for s in db for its in s for _, u in its]
    result = {"dataset": a.dataset, "sequences": len(db), "purchase_lines": len(lines),
              "max_itemsets_per_sequence": max(len(s) for s in db),
              "max_items_per_itemset": max(len(its) for s in db for its in s),
              "largest_line_utilities": sorted(lines, reverse=True)[:2],
              "total_utility": sum(lines), "runs": {}}
    print(f"{a.dataset}: {len(db)} sequences; longest {result['max_itemsets_per_sequence']} itemsets; "
          f"largest itemset {result['max_items_per_itemset']} items; largest purchase lines "
          f"{result['largest_line_utilities']}")
    for name, (ratios, mu_text, batches) in RUNS.items():
        for b in batches:
            info = diagnose(db, profit, a.dataset, ROOT / a.dumps, ratios, mu_text, b)
            result["runs"].setdefault(name, {})[str(b)] = info
            lh = info["lead_holder"] or {}
            print(f"{name} batch {b}: {info['patterns']} patterns (threshold {info['threshold']:.0f}); "
                  f"support recomputed for all, {info['support_mismatches_against_oracle']} differ from the oracle; "
                  f"sequence {lh.get('sid')} alone generates {lh.get('generated_alone')} "
                  f"({lh.get('generated_alone_in_dump')} in the oracle's list, up to {lh.get('max_items_generated')} "
                  f"items), {lh.get('reported_not_generated')} of its sole-support patterns not generated")
    out = ROOT / a.out
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
