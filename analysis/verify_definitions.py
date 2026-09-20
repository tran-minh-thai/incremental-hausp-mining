#!/usr/bin/env python3
"""Run the miner against a reference written from the paper's definitions, on boundary cases.

The worked example of the manuscript is self-consistent, but self-consistency on one database
says nothing about the corners. This builds a set of small quantitative sequence databases, each
aimed at one boundary the description could get wrong, mines each with the real implementation,
and compares the reported pattern set with an exhaustive reference that applies Definition
"average utility" and Definition HAUSP literally:

    au(alpha) = ( sum over supporting sequences of the largest utility of an occurrence )
                / ( number of items in alpha )
    alpha is a HAUSP  <=>  au(alpha) >= minUtil * totalU(DB)

Nothing of the algorithm is reused: the reference enumerates patterns and scores them from the
definition, so agreement is evidence and not a restatement.

Each database is written into the toy dataset slot, mined with --dataset example (the only
in-session run the launcher permits), and the slot is restored afterwards. The miner reports one
pattern file per batch, so every case is checked at all five accumulation points, which tests the
incremental result as well as the final one.

Fault injection, 2026-09-16, both directions and on different cases:

  * dropping the reportability half of the child-level Layer-3 test (the defect the manuscript's
    own definition used to describe) loses patterns on "wide-itemsets", five batches;
  * dropping the same-itemset guard on maxK admits I-extensions across itemset boundaries and
    reports patterns the definition does not, on "zero-residual" and "reportable-but-terminal".

So the suite is not a nodding machine. Note that the case which catches a fault is usually not
the one its name suggests: "wide-itemsets" is what exercises the Layer-3 disjunct, because six of
the seven benchmark datasets and most of these cases hold single-item itemsets.

Usage:  python3 analysis/verify_definitions.py [--keep] [--case NAME]
Exit status is non-zero if any case disagrees. The toy dataset slot is restored from the commit,
not from whatever was on disk when the run started: a previous --keep would otherwise become the
next run's backup, which is how this tool once overwrote the toy dataset.
"""
from __future__ import annotations
import argparse, itertools, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SLOT = ROOT / "datasets" / "example"
OUT = ROOT / "out"
JAR = next(iter(sorted(ROOT.glob("build/incremental-hausp-mining-*.jar"))), None)
MINUTIL = 0.05           # the toy slot's Experiment 1 threshold, from the experiment config
N_BATCH = 5              # Experiment 1 splits a dataset into five consecutive batches

# --------------------------------------------------------------------------- counterexamples
# Each case is (profit table, sequences). A sequence is a list of itemsets, an itemset a list of
# (item, quantity). Items inside an itemset are written in increasing identifier order, which is
# the storage order the method section fixes.
CASES: dict[str, tuple[dict[int, int], list]] = {
    # a single item, a single sequence: the smallest database that reports anything
    "singleton": ({1: 5}, [[[(1, 2)]]]),

    # every utility equal: nothing distinguishes occurrences, so ties are everywhere
    "all-equal": ({1: 1, 2: 1, 3: 1},
                  [[[(1, 1)], [(2, 1)], [(3, 1)]], [[(1, 1)], [(2, 1)]], [[(3, 1)]]]),

    # the same item in consecutive itemsets: the S-extension of a pattern by itself
    "self-s-extension": ({1: 5, 2: 3},
                         [[[(1, 2)], [(1, 3)]], [[(1, 1)], [(2, 2)], [(1, 4)]]]),

    # several occurrences of one pattern in one sequence: the per-sequence maximum must win
    "repeated-occurrence": ({1: 5, 2: 2},
                            [[[(1, 1)], [(2, 1)], [(1, 9)], [(2, 1)]],
                             [[(1, 3)], [(2, 4)]]]),

    # wide itemsets: the I-extension path, which six of the seven benchmarks never exercise
    "wide-itemsets": ({1: 4, 2: 3, 3: 2, 4: 6},
                      [[[(1, 2), (2, 1), (3, 3)], [(4, 1)]],
                       [[(1, 1), (4, 2)], [(2, 2), (3, 1)]],
                       [[(2, 3), (3, 2), (4, 1)]]]),

    # two items carrying exactly the same sequence-weighted utility: compact ids are assigned by
    # a sort on that key, and equal keys leave the order unspecified
    "swu-tie": ({1: 3, 2: 3, 3: 4},
                [[[(1, 2), (2, 2)], [(3, 1)]], [[(1, 1), (2, 1)]], [[(3, 2)]]]),

    # an item that occurs only at the very end of every sequence, so its residual is zero
    "zero-residual": ({1: 5, 2: 7},
                      [[[(1, 2)], [(2, 1)]], [[(1, 1)], [(2, 3)]], [[(1, 4)], [(2, 2)]]]),

    # a long chain inside one itemset: I-extension depth
    "i-extension-chain": ({1: 2, 2: 2, 3: 2, 4: 2, 5: 2},
                          [[[(1, 3), (2, 3), (3, 3), (4, 3), (5, 3)]],
                           [[(1, 1), (3, 1), (5, 1)]]]),

    # one dominant sequence and several thin ones: patterns sit right at the threshold
    "threshold-edge": ({1: 10, 2: 1},
                       [[[(1, 10)]], [[(2, 1)]], [[(2, 1)]], [[(2, 1)]], [[(2, 1)]]]),

    # a pattern that is itself worth reporting but can support no extension, which is the case
    # that separates the two conditions of the Layer-3 rule
    "reportable-but-terminal": ({1: 6, 2: 9},
                                [[[(1, 5)], [(2, 9)]], [[(1, 1)], [(2, 8)]]]),
}


# --------------------------------------------------------------------------- reference miner
def occurrence_utilities(pattern, sequence) -> list[int]:
    """Utility of every occurrence of the pattern in the sequence, by the sub-pattern definition."""
    out: list[int] = []

    def walk(pi: int, start: int, acc: int) -> None:
        if pi == len(pattern):
            out.append(acc); return
        for t in range(start, len(sequence)):
            iset = sequence[t]
            if all(i in iset for i in pattern[pi]):
                walk(pi + 1, t + 1, acc + sum(iset[i] for i in pattern[pi]))

    walk(0, 0, 0)
    return out


def reference_hausp(profits, sequences, minutil) -> set[str]:
    """Every pattern whose average utility reaches the threshold, by definition."""
    seqs = [[{i: profits[i] * q for i, q in iset} for iset in s] for s in sequences]
    total = sum(v for s in seqs for iset in s for v in iset.values())
    theta = minutil * total
    items = sorted({i for s in seqs for iset in s for i in iset})
    max_sets = max(len(s) for s in seqs)
    max_width = max(len(iset) for s in seqs for iset in s)
    itemsets = [tuple(c) for r in range(1, max_width + 1) for c in itertools.combinations(items, r)]

    found, frontier = set(), [[i] for i in itemsets]
    for _ in range(max_sets):
        nxt = []
        for pat in frontier:
            tot = 0
            for s in seqs:
                us = occurrence_utilities(pat, s)
                if us: tot += max(us)
            if tot == 0:
                continue                      # no support: no extension can gain any
            if tot / sum(len(x) for x in pat) >= theta:
                found.add(render(pat))
            nxt.append(pat)
        frontier = [p + [i] for p in nxt for i in itemsets]
    return found


def render(pattern) -> str:
    return "<" + "".join("(" + ",".join(str(i) for i in iset) + ")" for iset in pattern) + ">"


# --------------------------------------------------------------------------- the real miner
def write_slot(profits, sequences) -> None:
    (SLOT / "example_eui.txt").write_text(
        "# generated by analysis/verify_definitions.py\n"
        + "".join(f"{i}:{p}\n" for i, p in sorted(profits.items())))
    lines = []
    for s in sequences:
        toks = []
        for iset in s:
            toks += [f"{i}[{q}]" for i, q in sorted(iset)] + ["-1"]
        lines.append(" ".join(toks + ["-2"]))
    (SLOT / "example_seq.txt").write_text(
        "# generated by analysis/verify_definitions.py\n" + "\n".join(lines) + "\n")


def run_miner(results_dir: Path) -> tuple[dict[int, set[str]], dict[int, int]]:
    """Returns the pattern set per batch and, from the run's own CSV, how many sequences
    that batch had accumulated. Reading the accumulation instead of re-deriving the split
    keeps one assumption out of the check."""
    for f in OUT.glob("HAUSP_example_B*.txt"):
        f.unlink()
    shutil.rmtree(results_dir, ignore_errors=True)
    r = subprocess.run([ "java", "-jar", str(JAR), "--exp", "1", "--dataset", "example",
                         "--algo", "HAUSP-UB[noEUCS]", "--repeats", "1",
                         "--results-dir", str(results_dir)],
                       cwd=ROOT, capture_output=True, text=True, check=False)
    csv = results_dir / "exp1" / "experiment1_tightness.csv"
    if r.returncode != 0 or not csv.exists():
        raise SystemExit(f"the miner did not produce a result file; exit {r.returncode}\n"
                         f"{(r.stdout + r.stderr)[-800:]}")
    header, rows = None, []
    for ln in csv.read_text().split("\n"):
        if ln.startswith("#") or not ln.strip():
            continue
        if header is None:
            header = ln.split(","); continue
        rows.append(dict(zip(header, ln.split(","))))
    accumulated = {int(x["BatchID"]): int(x["TotalDBUtil"]) for x in rows}
    got: dict[int, set[str]] = {}
    for f in sorted(OUT.glob("HAUSP_example_B*.txt")):
        # The dump name carries the threshold since 2026-09-20 (_B<batch>_mu<threshold>.txt),
        # because Experiments 1, 5 and 6 mine the same database and batch at different thresholds
        # and used to overwrite one another. Accept both shapes rather than crash on the new one.
        m = re.search(r"_B(\d+)(?:_mu[0-9.eE-]+)?\.txt$", f.name)
        if m is None:
            continue
        b = int(m.group(1))
        got[b] = {ln.split("\t")[0] for ln in f.read_text().split("\n") if ln.strip()}
    return got, accumulated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", help="run one case by name")
    ap.add_argument("--keep", action="store_true", help="leave the generated database in the slot")
    a = ap.parse_args()
    if JAR is None:
        print("no jar under build/; run mvn package first"); return 2

    committed = subprocess.run(["git", "status", "--porcelain", "--", "datasets/example"],
                               cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if committed:
        print("datasets/example is not clean; restore it with git checkout before running")
        return 2
    cases = {a.case: CASES[a.case]} if a.case else CASES
    failures, compared = [], 0
    try:
        for name, (profits, sequences) in cases.items():
            # five batches of a fifth each: a database has to hold at least five sequences
            # before any batch but the last is non-empty, so short cases are cycled up to ten.
            # That also makes each case test accumulation across batches, not just the final set.
            while len(sequences) < 10:
                sequences = sequences + [list(x) for x in sequences]
            sequences = sequences[:12]
            write_slot(profits, sequences)
            got, accumulated = run_miner(ROOT / "results-probe" / "definitions" / name)
            # map each batch to the prefix whose total utility it reports; an unmatched value
            # means the harness misread the run, which is not a disagreement about the algorithm
            running, by_util = 0, {0: 0}
            for k, seq in enumerate(sequences, 1):
                running += sum(profits[i] * q for iset in seq for i, q in iset)
                by_util[running] = k
            marks = []
            for b in sorted(accumulated):
                u = accumulated[b]
                if u not in by_util:
                    raise SystemExit(f"{name} batch {b}: reported total utility {u} matches no prefix "
                                     f"{sorted(by_util)}; the harness cannot place this batch")
                prefix = sequences[:by_util[u]]
                if not prefix:
                    continue
                want = reference_hausp(profits, prefix, MINUTIL)
                have = got.get(b, set())
                compared += 1
                if want == have:
                    marks.append("ok")
                else:
                    marks.append("FAIL")
                    failures.append((name, b, sorted(want - have), sorted(have - want)))
            print(f"  {name:24s} batches {' '.join(marks)}")
    finally:
        if not a.keep:
            subprocess.run(["git", "checkout", "--", "datasets/example"], cwd=ROOT, check=False)
            left = subprocess.run(["git", "status", "--porcelain", "--", "datasets/example"],
                                  cwd=ROOT, capture_output=True, text=True).stdout.strip()
            print("toy dataset restored" if not left else f"RESTORE FAILED: {left}")

    print(f"\ncompared {compared} pattern sets across {len(cases)} databases")
    if failures:
        print(f"{len(failures)} disagreements:")
        for name, b, missing, extra in failures:
            print(f"  {name} batch {b}: definition has {len(missing)} the miner does not "
                  f"{missing[:6]}; miner has {len(extra)} the definition does not {extra[:6]}")
        return 1
    print("every pattern set matches the definition")
    return 0


if __name__ == "__main__":
    sys.exit(main())
