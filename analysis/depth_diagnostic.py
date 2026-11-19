#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Depth diagnostic of one HAUSP search: where the nodes and the assembled lists of a long-sequence dataset sit.

Draws a seeded sample of the lines (customers) of a raw dataset, runs one batch of the whole sample through TopKProbe
with the per-depth counters of HAUSP_UB (property hausp.depthStats) and a deadline inside the search, and records per
depth: nodes entered, nodes cut on entry by Layer 3, nodes whose finished subtree holds no HAUSP ("barren"), HAUSPs,
child lists assembled, of them rejected by Layer 2, and the summed length (occurrences) and support (sequences) of the
rejected and of the kept lists. Occurrences per sequence of a list answer whether the cost comes from many occurrences
of a pattern in one sequence; the barren and rejected shares answer whether the bound is loose.

This is a probe on the development machine: the counts are properties of the visited part of the search tree, which a
deadline cuts in depth-first order. No time is recorded.

Before the real run the tool checks itself on a generated database where the search finishes:
  (a) the per-depth nodes add up to the search's own node count;
  (b) the per-depth HAUSPs add up to the result count, and depth d holds exactly the HAUSPs with d+1 items in the
      pattern dump (computed from the dump, independently of the counters);
  (c) at every depth d >= 1 the nodes entered equal the kept lists (every kept list is recursed into);
  (d) occurrences >= support in every row.
Each check is shown to be refutable by a fault applied to the parsed table (shift HAUSPs by one depth, shift lists by
one depth, drop one node, swap occurrences and support).

Usage:
  python3 analysis/depth_diagnostic.py --data DUNNHUMBY=DIR --fraction 0.1 --seed 0 --xi 0.01 --limit 600 \
      --sample-dir DIR --out results-probe/depth-diagnostic/DUNNHUMBY-S10-xi0.01
"""
import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
COLS = ("nodes", "cut_l3", "barren", "hausp", "lists", "drop_l2", "occ_drop", "sup_drop", "occ_kept", "sup_kept")
DEFAULT_ARM = "HAUSP-UB[noEUCS]"  # the arm TopKProbe uses by default, as in the threshold selection


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 24), b""):
            h.update(blk)
    return h.hexdigest()


def git(*a):
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()


def anon(arg: str) -> str:
    """A command-line argument with the home directory replaced by ~ (in NAME=PATH arguments too)."""
    home = Path.home().resolve().as_posix()
    return arg.replace(home, "~")


def probe(eui: Path, seq: Path, xi: float, limit: float, heap: str, arm: str):
    n = sum(1 for _ in open(seq))
    with tempfile.TemporaryDirectory() as t:
        (Path(t) / "out").mkdir()
        props = Path(t) / "db.properties"
        props.write_text(f"dataset.path={seq.as_posix()}\nminUtil.percentage=0.0\n")
        argv = ["java", f"-Xmx{heap}", "-Dhausp.depthStats=true", f"-Dhausp.deadlineSeconds={limit}", "-cp", str(JAR),
                "TopKProbe", str(eui), str(seq), str(props), "1", f"mu={xi!r}", str(n), "-", arm]
        t0 = time.time()
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=limit + 600, cwd=t)
        except subprocess.TimeoutExpired:
            return {"status": "KILLED", "wall_note": "outer timeout"}, None, []
        _ = time.time() - t0
    out = p.stdout.splitlines()
    st = next((l for l in out if l.startswith("depth-stats batch")), "")
    m = re.search(r"status (\w+)", st)
    if p.returncode or not m:
        return {"status": f"FAIL rc={p.returncode}", "stderr": p.stderr[-1500:]}, None, []
    i = out.index(st)
    table = {}
    for line in out[i + 2:]:
        f = line.split()
        if len(f) != len(COLS) + 1 or not f[0].isdigit():
            break
        table[int(f[0])] = dict(zip(COLS, map(int, f[1:])))
    batch = next((l for l in out if l.startswith("batch ")), "")
    bm = re.search(r"result (\d+) lists (\d+) nodes (\d+)", batch)
    head = {"status": m.group(1), "sequences": n}
    if bm:
        head.update(result=int(bm.group(1)), lists=int(bm.group(2)), nodes=int(bm.group(3)))
    patterns = [l.split()[2] for l in out if l.startswith("pattern ")]
    return head, table, patterns


def items_of(pattern: str) -> int:
    return len(re.findall(r"\d+", pattern))


def checks(head, table, patterns):
    """Return {name: (compared, wrong)} for the four checks of the module docstring."""
    res = {}
    res["nodes_sum"] = (1, int(sum(r["nodes"] for r in table.values()) != head.get("nodes")))
    by_len = {}
    for p in patterns:
        by_len[items_of(p) - 1] = by_len.get(items_of(p) - 1, 0) + 1
    depths = set(by_len) | {d for d, r in table.items() if r["hausp"]}
    wrong = sum(1 for d in depths if table.get(d, {}).get("hausp", 0) != by_len.get(d, 0))
    wrong += int(sum(r["hausp"] for r in table.values()) != head.get("result"))
    res["hausp_by_depth"] = (len(depths) + 1, wrong)
    deep = [d for d in table if d >= 1]
    res["nodes_eq_kept"] = (len(deep), sum(1 for d in deep
                                           if table[d]["nodes"] != table[d]["lists"] - table[d]["drop_l2"]))
    res["occ_ge_sup"] = (len(table), sum(1 for r in table.values()
                                         if r["occ_drop"] < r["sup_drop"] or r["occ_kept"] < r["sup_kept"]))
    return res


def fault(table, name):
    t = {d: dict(r) for d, r in table.items()}
    ds = sorted(t)
    if name == "shift_hausp":
        vals = [t[d]["hausp"] for d in ds]
        for d, v in zip(ds, [0] + vals[:-1]):
            t[d]["hausp"] = v
    elif name == "shift_lists":
        vals = [(t[d]["lists"], t[d]["drop_l2"]) for d in ds]
        for d, v in zip(ds, vals[1:] + [(0, 0)]):
            t[d]["lists"], t[d]["drop_l2"] = v
    elif name == "drop_node":
        d = max(ds, key=lambda x: t[x]["nodes"])
        t[d]["nodes"] -= 1
    elif name == "swap_occ_sup":
        for d in ds:
            t[d]["occ_kept"], t[d]["sup_kept"] = t[d]["sup_kept"], t[d]["occ_kept"]
    return t


def self_test(heap, arm):
    rng = random.Random(7)
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        profits = {i: rng.randint(1, 5) for i in range(1, 13)}
        lines = []
        for _ in range(120):
            isets = [sorted(rng.sample(range(1, 13), rng.randint(1, 3))) for _ in range(rng.randint(2, 9))]
            lines.append(" ".join(" ".join(f"{i}[{rng.randint(1, 4)}]" for i in s) + " -1" for s in isets) + " -2")
        (d / "db_eui.txt").write_text("".join(f"{i}:{p}\n" for i, p in sorted(profits.items())))
        (d / "db_seq.txt").write_text("\n".join(lines) + "\n")
        head, table, patterns = probe(d / "db_eui.txt", d / "db_seq.txt", 0.004, 300, heap, arm)
    if head.get("status") != "OK" or not table:
        return {"ok": False, "head": head}
    base = checks(head, table, patterns)
    ok = all(c > 0 and w == 0 for c, w in base.values()) and len(patterns) > 0
    expect = {"shift_hausp": "hausp_by_depth", "shift_lists": "nodes_eq_kept", "drop_node": "nodes_sum",
              "swap_occ_sup": "occ_ge_sup"}
    caught = {}
    for f, target in expect.items():
        r = checks(head, fault(table, f), patterns)
        caught[f] = {k: w for k, (c, w) in r.items()}
        ok &= r[target][1] > 0
    return {"ok": ok, "patterns": len(patterns), "depths": len(table), "checks": base, "faults": caught}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--fraction", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--xi", type=float, required=True)
    ap.add_argument("--limit", type=float, default=600)
    ap.add_argument("--heap", default="12g")
    ap.add_argument("--arm", default=DEFAULT_ARM)
    ap.add_argument("--sample-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if [l for l in git("status", "--porcelain", "--untracked-files=no").splitlines() if l]:
        sys.exit(f"refusing: the working tree at {ROOT} has modified tracked files")
    if not JAR.exists() or any(p.stat().st_mtime > JAR.stat().st_mtime for p in (ROOT / "src").rglob("*.java")):
        sys.exit(f"refusing: {JAR} is missing or older than src/; rebuild it first")
    if args.out.exists():
        sys.exit(f"refusing: {args.out} exists")
    st = self_test(args.heap, args.arm)
    print("self-test", json.dumps(st))
    if not st["ok"]:
        sys.exit("self-test failed")

    name, dd = args.data.split("=", 1)
    src_seq, src_eui = Path(dd) / f"{name}_seq.txt", Path(dd) / f"{name}_eui.txt"
    lines = src_seq.read_text().splitlines()
    k = round(len(lines) * args.fraction)
    pick = sorted(random.Random(args.seed).sample(range(len(lines)), k))
    args.sample_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{name}_S{round(args.fraction * 100)}_seed{args.seed}"
    seq, eui = args.sample_dir / f"{tag}_seq.txt", args.sample_dir / f"{tag}_eui.txt"
    body = "\n".join(lines[i] for i in pick) + "\n"
    if seq.exists() and seq.read_text() != body:
        sys.exit(f"refusing: {seq} exists with other content")
    seq.write_text(body)
    eui.write_bytes(src_eui.read_bytes())

    head, table, patterns = probe(eui, seq, args.xi, args.limit, args.heap, args.arm)
    print("run", json.dumps(head))
    args.out.mkdir(parents=True)
    rows = ["depth\t" + "\t".join(COLS)] + [f"{d}\t" + "\t".join(str(table[d][c]) for c in COLS)
                                            for d in sorted(table or {})]
    (args.out / "depth_table.tsv").write_text("\n".join(rows) + "\n")
    meta = {
        "kind": "PROBE (search-tree counts on the development machine; no time recorded)",
        "command": "python3 analysis/depth_diagnostic.py " + " ".join(anon(a) for a in sys.argv[1:]),
        "git_commit": git("rev-parse", "HEAD"), "tree": "clean", "jar_sha256": sha(JAR),
        "code_sha256": sha(Path(__file__)), "arm": args.arm, "xi": args.xi, "deadline_seconds": args.limit,
        "heap": args.heap, "source_seq_sha256": sha(src_seq), "source_eui_sha256": sha(src_eui),
        "sample": {"fraction": args.fraction, "seed": args.seed, "lines": k, "of": len(lines),
                   "seq_sha256": sha(seq), "path": anon(seq)},
        "java": subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.strip().splitlines()[:2],
        "python": sys.version.split()[0], "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run": head, "self_test": st,
        "note": "status OT means the deadline stopped the search; the table then covers the depth-first prefix visited",
    }
    (args.out / "run_meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"wrote {args.out.as_posix()}")


if __name__ == "__main__":
    main()
