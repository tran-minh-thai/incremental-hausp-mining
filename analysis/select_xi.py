#!/usr/bin/env python3
"""PROBE (pattern counts only): choose the thresholds xi of a dataset by the number of HAUSPs of the whole data.

Registered rule (measurement request of 2026-11-11): for each dataset, three ratios xi such that the
number of HAUSPs after the last L5 batch, i.e. of the whole data, is near 10^2, 10^3 and 10^4. "Near" is
taken in log scale: among the ratios tried, the one whose count c minimises |log10 c - target|.

Search: a descending geometric grid (factor 2) from --start until the count exceeds 3 * 10^4 or a run
exceeds the time limit; then, for each target, bisection in log scale between the two grid points that
bracket it, --refine steps. The count can jump across a target (real data has plateaus); the rule then
picks the closer side, and the registration records both neighbours.

Each run is one JVM of TopKProbe in threshold mode (mu=X) on the whole data as one batch. Everything goes
to results-probe/select-xi/<run id>/: the output line of every run, runs.json, registration.json,
run_meta.json (command, commit, tree state, JAR sha256, java version).

Rule "sqrt2" (--rule sqrt2; reply of the content side, 2026-11-11, for the long-sequence datasets): the grid
xi_k = start * 2^(-k/2), k = 0, 1, ...; a level is accepted when static mining of the whole data finishes within
--limit seconds with at least 10 patterns; the dataset uses at most three of the smallest accepted levels. The
grid stops at the first level that times out or fails. --reuse DIR... takes a finished (OK) run of an earlier
artifact for a grid level when its JAR sha256 equals this one's and its xi equals the level exactly; a run that
timed out is never reused, since earlier runs shared the machine with other JVMs.

Usage: python3 analysis/select_xi.py --data NAME=DIR [--start 0.02] [--limit 1800] [--refine 4]
       python3 analysis/select_xi.py --rule sqrt2 --data NAME=DIR [--reuse DIR ...] [--limit 1800]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
TARGETS = (2, 3, 4)


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 24), b""):
            h.update(blk)
    return h.hexdigest()


def count(name, d, xi, limit, heap, log_dir):
    seq, eui = d / f"{name}_seq.txt", d / f"{name}_eui.txt"
    n = sum(1 for _ in open(seq))
    with tempfile.TemporaryDirectory() as t:
        props = Path(t) / "db.properties"
        props.write_text(f"dataset.path={seq}\nminUtil.percentage=0.0\n")
        argv = ["java", f"-Xmx{heap}", "-cp", str(JAR), "TopKProbe", str(eui), str(seq), str(props), "1",
                f"mu={xi!r}", str(n)]
        t0 = time.time()
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired:
            return {"xi": xi, "status": "OT", "seconds": round(time.time() - t0, 1)}
    line = next((l for l in p.stdout.splitlines() if l.startswith("batch ")), "")
    (log_dir / f"{name}_mu{xi!r}.txt").write_text(line + "\n" + p.stderr[-2000:])
    m = re.search(r"result (\d+) lists (\d+) nodes (\d+)", line)
    if p.returncode or not m:
        return {"xi": xi, "status": f"FAIL rc={p.returncode}", "seconds": round(time.time() - t0, 1)}
    return {"xi": xi, "status": "OK", "patterns": int(m.group(1)), "lists": int(m.group(2)),
            "nodes": int(m.group(3)), "seconds": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", required=True)
    ap.add_argument("--start", type=float, default=0.02)
    ap.add_argument("--limit", type=int, default=1800)
    ap.add_argument("--refine", type=int, default=4)
    ap.add_argument("--heap", default="24g")
    ap.add_argument("--rule", choices=("log10", "sqrt2"), default="log10")
    ap.add_argument("--reuse", nargs="*", type=Path, default=[])
    ap.add_argument("--min-patterns", type=int, default=10)
    args = ap.parse_args()
    dirty = [l for l in git("status", "--porcelain", "--untracked-files=no").splitlines() if l]
    if dirty:
        sys.exit(f"refusing: the working tree at {ROOT} has {len(dirty)} modified tracked files")
    if not JAR.exists() or any(p.stat().st_mtime > JAR.stat().st_mtime for p in (ROOT / "src").rglob("*.java")):
        sys.exit(f"refusing: {JAR} is missing or older than src/; rebuild it first")
    out = ROOT / "results-probe" / "select-xi" / time.strftime("%Y%m%d-%H%M%S")
    (out / "raw").mkdir(parents=True)
    runs, reg = {}, {}
    jar_sha = sha256(JAR)
    prior = {}
    for rd in args.reuse:
        meta = json.loads((rd / "run_meta.json").read_text())
        if meta["jar_sha256"] != jar_sha:
            sys.exit(f"refusing: {rd} was made with another JAR")
        for nm, rs in json.loads((rd / "runs.json").read_text()).items():
            for r in rs:
                if r["status"] == "OK":
                    prior.setdefault(nm, []).append({**r, "reused_from": rd.name})
    for spec in args.data:
        name, d = spec.split("=", 1)
        d = Path(d)
        runs[name] = []
        if args.rule == "sqrt2":
            k = 0
            while True:
                xi = args.start * 2 ** (-k / 2)
                r = next((x for x in prior.get(name, []) if x["xi"] == xi), None) \
                    or count(name, d, xi, args.limit, args.heap, out / "raw")
                runs[name].append(r)
                print(name, json.dumps(r), flush=True)
                if r["status"] != "OK" or xi < 1e-7:
                    break
                k += 1
            acc = [x for x in runs[name] if x["status"] == "OK" and x["patterns"] >= args.min_patterns
                   and x["seconds"] <= args.limit]
            acc = sorted(acc, key=lambda x: x["xi"])[:3]
            reg[name] = {"rule": "sqrt2", "accepted": [{"xi": x["xi"], "patterns": x["patterns"]} for x in acc],
                         "role": "end-to-end" if acc else "maintenance only"}
            (out / "runs.json").write_text(json.dumps(runs, indent=1) + "\n")
            (out / "registration.json").write_text(json.dumps(reg, indent=2) + "\n")
            continue
        xi = args.start
        while True:
            r = count(name, d, xi, args.limit, args.heap, out / "raw")
            runs[name].append(r)
            print(name, json.dumps(r), flush=True)
            if r["status"] != "OK" or r["patterns"] > 3 * 10 ** 4 or xi < 1e-7:
                break
            xi /= 2
        for tgt in TARGETS:
            for _ in range(args.refine):
                ok = sorted((x for x in runs[name] if x["status"] == "OK"), key=lambda x: -x["xi"])
                lo = next((x for x in ok if x["patterns"] >= 10 ** tgt), None)      # smaller xi, more patterns
                hi = next((x for x in reversed(ok) if x["patterns"] < 10 ** tgt), None)
                if lo is None or hi is None or lo["xi"] >= hi["xi"]:
                    break
                mid = math.sqrt(lo["xi"] * hi["xi"])
                if any(abs(x["xi"] - mid) < 1e-15 for x in runs[name]):
                    break
                r = count(name, d, mid, args.limit, args.heap, out / "raw")
                runs[name].append(r)
                print(name, json.dumps(r), flush=True)
        ok = [x for x in runs[name] if x["status"] == "OK" and x["patterns"] > 0]
        reg[name] = {}
        for tgt in TARGETS:
            if not ok:
                reg[name][f"1e{tgt}"] = None
                continue
            best = min(ok, key=lambda x: (abs(math.log10(x["patterns"]) - tgt), -x["xi"]))
            reg[name][f"1e{tgt}"] = {"xi": best["xi"], "patterns": best["patterns"]}
        (out / "runs.json").write_text(json.dumps(runs, indent=1) + "\n")
        (out / "registration.json").write_text(json.dumps(reg, indent=2) + "\n")
    java_v = subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.strip().splitlines()[:2]
    meta = {"kind": "PROBE (pattern counts on the development machine; seconds are not measurements)",
            "command": "python3 analysis/select_xi.py " + " ".join(
                a.split("=")[0] + "=" + Path(a.split("=", 1)[1]).name if "=" in a and "/" in a else a
                for a in sys.argv[1:]),
            "git_commit": git("rev-parse", "HEAD"), "tree": "clean", "jar_sha256": jar_sha, "java": java_v,
            "heap": args.heap, "limit_seconds": args.limit,
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(reg, indent=2))
    print("wrote", out.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
