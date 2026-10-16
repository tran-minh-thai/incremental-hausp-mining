#!/usr/bin/env python3
"""Compare HAUSP-UB's top-k mode with a top-k computed from the definitions, batch by batch.

The reference enumerates every pattern with support in the accumulated database and scores it by
the definition of average utility (analysis/verify_definitions.py, which shares no code with the
miner). The top-k result is every pattern whose average utility is at least the k-th largest one,
ties included; when fewer than k patterns exist, every pattern. The miner runs through
src/main/java/TopKProbe.java, cold (each batch starts from the single-item k-th value) and warm
(each batch also starts from the previous batch's k-th value), and must agree with the reference on
the k-th value and on the pattern set after every batch.

Databases: the ten boundary cases of verify_definitions plus seeded random databases, each split
into consecutive batches. The script prints how many (database, k, mode, batch) cells it compared
and how many of them had ties at the k-th value, since a tie rule checked on no tie is unchecked.

Mixed update model: every database is also fed with each sequence cut into one or two parts, the
second arriving in a later batch under the same key, i.e. as itemsets appended to the stored
sequence. The reference then scores the sequences joined from the parts that have arrived. These
runs cover top-k (cold and warm) and the threshold mode on four arms (the full algorithm, the
rebuild control and the two other places of the coupled test), and the script reports how many
batches really carried appended parts.

    python3 analysis/verify_topk.py            # exit 1 on any disagreement
"""
from __future__ import annotations

import importlib.util
import itertools
import random
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
_spec = importlib.util.spec_from_file_location("verify_definitions", ROOT / "analysis" / "verify_definitions.py")
_vd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vd)
KS = [1, 2, 3, 5, 8]


def reference_all(profits, sequences) -> dict[str, tuple[int, int]]:
    """Every pattern with support: rendered pattern -> (utility, length), by definition."""
    seqs = [[{i: profits[i] * q for i, q in iset} for iset in s] for s in sequences]
    items = sorted({i for s in seqs for iset in s for i in iset})
    max_sets = max(len(s) for s in seqs)
    max_width = max(len(iset) for s in seqs for iset in s)
    itemsets = [tuple(c) for r in range(1, max_width + 1) for c in itertools.combinations(items, r)]
    out, frontier = {}, [[i] for i in itemsets]
    for _ in range(max_sets):
        nxt = []
        for pat in frontier:
            tot = 0
            for s in seqs:
                us = _vd.occurrence_utilities(pat, s)
                if us:
                    tot += max(us)
            if tot == 0:
                continue
            out[_vd.render(pat)] = (tot, sum(len(x) for x in pat))
            nxt.append(pat)
        frontier = [p + [i] for p in nxt for i in itemsets]
    return out


def reference_topk(allp: dict, k: int):
    vals = sorted((Fraction(u, l) for u, l in allp.values()), reverse=True)
    if len(vals) < k:
        return Fraction(0), set(allp)
    kth = vals[k - 1]
    return kth, {p for p, (u, l) in allp.items() if Fraction(u, l) >= kth}


def random_db(rng: random.Random):
    n_items = rng.randint(3, 6)
    profits = {i: rng.randint(1, 9) for i in range(1, n_items + 1)}
    seqs = []
    for _ in range(rng.randint(4, 8)):
        s = []
        for _ in range(rng.randint(1, 4)):
            width = rng.randint(1, 3)
            iset = sorted(rng.sample(range(1, n_items + 1), min(width, n_items)))
            s.append([(i, rng.randint(1, 5)) for i in iset])
        seqs.append(s)
    return profits, seqs


def write_db(d: Path, profits, sequences):
    (d / "db_eui.txt").write_text("".join(f"{i}:{p}\n" for i, p in sorted(profits.items())))
    lines = []
    for s in sequences:
        toks = []
        for iset in s:
            toks += [f"{i}[{q}]" for i, q in sorted(iset)] + ["-1"]
        lines.append(" ".join(toks + ["-2"]))
    (d / "db_seq.txt").write_text("\n".join(lines) + "\n")
    (d / "db.properties").write_text(f"dataset.path={d / 'db_seq.txt'}\nminUtil.percentage=0.0\n")


def mixed(rng: random.Random, sequences):
    """Lines (key, itemsets) and batch sizes: each sequence in one or two parts, the second later."""
    first, later = [], []
    for key, s in enumerate(sequences):
        if len(s) >= 2 and rng.random() < 0.7:
            cut = rng.randint(1, len(s) - 1)
            first.append((key, s[:cut]))
            later.append((key, s[cut:]))
        else:
            (first if rng.random() < 0.6 else later).append((key, s))
    rng.shuffle(later)
    lines = first + later
    sizes = [len(first)] + ([len(later) // 2, len(later) - len(later) // 2] if len(later) >= 2 else [len(later)])
    return lines, [x for x in sizes if x > 0]


def joined(lines, upto: int):
    """The sequences after the first `upto` lines: parts of one key concatenated in arrival order."""
    seqs: dict = {}
    for key, part in lines[:upto]:
        seqs.setdefault(key, []).extend(part)
    return [seqs[k] for k in sorted(seqs)]


def run(d: Path, k: int, mode: str, sizes, keys=None, arm=None):
    argv = ["java", "-cp", str(JAR), "TopKProbe", str(d / "db_eui.txt"), str(d / "db_seq.txt"),
            str(d / "db.properties"), str(k), mode, ",".join(map(str, sizes))]
    if keys is not None or arm is not None:
        argv += [",".join(map(str, keys)) if keys is not None else "-", arm or "-"]
    out = subprocess.run(argv, capture_output=True, text=True, cwd=d)
    if out.returncode != 0:
        return None, out.stderr.strip()[:200]
    kth, sets, info = {}, {}, {}
    for line in out.stdout.splitlines():
        f = line.split()
        if f[0] == "batch":
            b = int(f[1])
            kth[b] = Fraction(int(f[3]), int(f[5]))
            sets.setdefault(b, set())
            info[b] = {"appended": int(f[13]), "sequences": int(f[15])}
        elif f[0] == "pattern":
            sets.setdefault(int(f[1]), set()).add(f[2])
    return (kth, sets, info), None


def split(n: int) -> list[int]:
    if n < 3:
        return [n]
    a = n // 3
    return [n - 2 * a, a, a]


def main() -> int:
    if not JAR.exists():
        raise SystemExit("build the JAR first: mvn -q package -DskipTests")
    rng = random.Random(20261016)
    dbs = [(name, p, s) for name, (p, s) in _vd.CASES.items()] + \
          [(f"random-{i}", *random_db(rng)) for i in range(40)]
    cells = ties = nonempty = 0
    mixed_cells = mixed_appended = 0
    problems = []
    with tempfile.TemporaryDirectory(prefix="verify-topk-") as tmp:
        d = Path(tmp)
        for name, profits, seqs in dbs:
            write_db(d, profits, seqs)
            sizes = split(len(seqs))
            bounds = list(itertools.accumulate(sizes))
            refs = [reference_all(profits, seqs[:b]) for b in bounds]
            for k in KS:
                for mode in ("cold", "warm"):
                    out = subprocess.run(["java", "-cp", str(JAR), "TopKProbe", str(d / "db_eui.txt"),
                                          str(d / "db_seq.txt"), str(d / "db.properties"), str(k), mode,
                                          ",".join(map(str, sizes))], capture_output=True, text=True, cwd=tmp)
                    if out.returncode != 0:
                        problems.append(f"{name} k={k} {mode}: exit {out.returncode}: {out.stderr.strip()[:200]}")
                        continue
                    got_kth, got_set = {}, {}
                    for line in out.stdout.splitlines():
                        f = line.split()
                        if f[0] == "batch":
                            got_kth[int(f[1])] = Fraction(int(f[3]), int(f[5]))
                            got_set.setdefault(int(f[1]), set())
                        elif f[0] == "pattern":
                            got_set.setdefault(int(f[1]), set()).add(f[2])
                    for b, ref in enumerate(refs):
                        kth, want = reference_topk(ref, k)
                        cells += 1
                        nonempty += bool(want)
                        ties += sum(1 for u, l in ref.values() if Fraction(u, l) == kth) > 1
                        if got_kth.get(b) != kth or got_set.get(b) != want:
                            problems.append(f"{name} k={k} {mode} batch {b}: k-th {got_kth.get(b)} vs {kth}; "
                                            f"missing {sorted(want - got_set.get(b, set()))[:4]}, "
                                            f"extra {sorted(got_set.get(b, set()) - want)[:4]}")
        # mixed update model
        mrng = random.Random(20261017)
        for name, profits, seqs in dbs:
            lines, sizes = mixed(mrng, seqs)
            keys = [k for k, _ in lines]
            write_db(d, profits, [part for _, part in lines])
            bounds = list(itertools.accumulate(sizes))
            refs = [reference_all(profits, joined(lines, b)) for b in bounds]
            n_keys = [len({k for k, _ in lines[:b]}) for b in bounds]
            runs = [(k, mode, None) for k in KS for mode in ("cold", "warm")] + \
                   [(0, "mu=0.05", arm) for arm in ("HAUSP-UB[noEUCS]", "HAUSP-UB[rebuild+noEUCS]",
                                                    "HAUSP-UB[noL2+noEUCS]", "HAUSP-UB[noL2+L3@node+noEUCS]")]
            for k, mode, arm in runs:
                got, err = run(d, max(k, 1), mode, sizes, keys, arm)
                if got is None:
                    problems.append(f"mixed {name} k={k} {mode} {arm}: {err}")
                    continue
                kth_got, sets_got, info = got
                for b, ref in enumerate(refs):
                    mixed_cells += 1
                    mixed_appended += info[b]["appended"] > 0
                    if info[b]["sequences"] != n_keys[b]:
                        problems.append(f"mixed {name} {mode} {arm} batch {b}: {info[b]['sequences']} sequences "
                                        f"stored, {n_keys[b]} keys seen")
                    if mode.startswith("mu="):
                        total = sum(profits[i] * q for s in joined(lines, bounds[b]) for iset in s for i, q in iset)
                        want = {p for p, (u, l) in ref.items() if Fraction(u, l) >= Fraction(5, 100) * total}
                        if sets_got.get(b) != want:
                            problems.append(f"mixed {name} {mode} {arm} batch {b}: missing "
                                            f"{sorted(want - sets_got.get(b, set()))[:4]}, extra "
                                            f"{sorted(sets_got.get(b, set()) - want)[:4]}")
                        continue
                    kth, want = reference_topk(ref, k)
                    if kth_got.get(b) != kth or sets_got.get(b) != want:
                        problems.append(f"mixed {name} k={k} {mode} batch {b}: k-th {kth_got.get(b)} vs {kth}; "
                                        f"missing {sorted(want - sets_got.get(b, set()))[:4]}, "
                                        f"extra {sorted(sets_got.get(b, set()) - want)[:4]}")
    print(f"verify_topk: {cells} (database, k, mode, batch) cells over {len(dbs)} databases; "
          f"{nonempty} with a nonempty result, {ties} with a tie at the k-th value")
    print(f"verify_topk: mixed model {mixed_cells} cells, {mixed_appended} of them in a batch that carried "
          f"appended parts")
    if problems:
        for p in problems[:20]:
            print("  MISMATCH  " + p)
        print(f"verify_topk: FAIL -- {len(problems)} cell(s) disagree")
        return 1
    print("verify_topk: PASS -- every k-th value and every top-k set matches the definition")
    return 0


if __name__ == "__main__":
    sys.exit(main())
