#!/usr/bin/env python3
"""PROBE, not a measurement: how many promotion candidates an update of the result set would examine.

An update that does not run the whole search again keeps the HAUSP set with its evalIutil, and at
each batch examines (a) the stored HAUSPs, with their evalIutil on the batch, and (b) the HAUSPs of
the batch alone at the same relative threshold, restricted to the items whose SWU over the
accumulated database reaches 2 theta: the only patterns of length at least 2 that can be promoted
(src/main/java/BatchCandidateProbe.java states the argument). Whether that is cheaper than the
search depends on how many (b)-candidates there are, which is a property of the data and the batch
schedule, not of the machine. This probe counts them at sampled batches and sets them beside the
pattern count and the lists the full search assembles at the same batch, read from the
generation-1 results (counts are the same in both generations).

Each run is one JVM with a time limit; a run that exceeds it is recorded as OT with the
transition it was on. Everything goes to results-probe/batch-candidates/<run id>/: the raw output
of every JVM, summary.csv, and run_meta.json (command, commit, tree state, JAR hash, java version,
host, limits).

    python3 analysis/probe_batch_candidates.py                 # every dataset, K = 5, 20, 100
    python3 analysis/probe_batch_candidates.py --datasets LEVIATHAN --ks 5 --verify
    python3 analysis/probe_batch_candidates.py --evaluate results-probe/batch-candidates/<run id>

--evaluate applies the decision rule registered before the first run (EXPERIMENT_CHANGELOG,
2026-10-16): over the batches b >= 10 of K = 20 and K = 100 on which the full search completed,
"worth implementing" needs every run within the time limit with lists_ratio <= 0.2 and
candidates_per_hausp <= 10 on at least 5 datasets; "not worth it" is lists_ratio >= 0.5 or a
time-out on more than half of those batches on at least 4 datasets; anything else is open.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import platform
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OK, PAPER_UB, ROOT, load_config, load_experiment  # noqa: E402

JAR = ROOT / "build" / "incremental-hausp-mining-1.0.0.jar"
OUT_ROOT = ROOT / "results-probe" / "batch-candidates"
FILES = {"BIBLE": "bible/BIBLE", "BMS1_SPMF": "bms1_spmf/BMS1_SPMF", "FIFA": "fifa/FIFA",
         "KOSARAK": "kosarak/KOSARAK", "LEVIATHAN": "leviathan/LEVIATHAN", "SIGN": "sign/SIGN",
         "TAFENG": "tafeng/TAFENG", "C8T1S5I8N5K": "syn/C8T1S5I8N5K"}
#: Batches sampled per K (b >= 1: the batch whose arrival is examined).
SAMPLE = {5: [1, 2, 3, 4], 20: [1, 2, 5, 10, 15, 19], 100: [1, 2, 5, 10, 25, 50, 75, 99]}
#: The schedule of each K and the experiment whose threshold and generation-1 counts it uses.
SOURCE = {5: 1, 20: 7, 100: 7}
LINE = re.compile(r"^(probe|verify) (.*)$")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def reference(exp: int) -> dict:
    """(dataset, K, b) -> (HAUSP, lists assembled) of the full algorithm's first trial."""
    df = load_experiment(exp)
    d = df[(df["Algorithm"] == PAPER_UB) & df["Status"].isin(OK) & (df["RunIndex"] == 0)].copy()
    d["K"] = (1.0 / d["DeltaRatio"]).round().astype(int)
    if exp == 7 and "Schedule" in d.columns:
        d = d[d["Schedule"].astype(str) == "equal"]
    out = {}
    for _, r in d.iterrows():
        out[(r["Dataset"], int(r["K"]), int(r["BatchID"]))] = (int(r["HAUSP"]), int(r["CandUnified"]))
    return out


def evaluate(run_dir: Path) -> int:
    """The pre-registered verdict, per dataset and overall, with the batches each one rests on."""
    with (run_dir / "summary.csv").open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if int(r["K"]) in (20, 100) and int(r["b"]) >= 10
                and r.get("full_hausp", "") != ""]
    per: dict = {}
    for r in rows:
        per.setdefault(r["dataset"], []).append(r)
    good = bad = 0
    for ds, rs in sorted(per.items()):
        ok = [r for r in rs if r["status"] == "OK"]
        n_bad = sum(1 for r in rs if r["status"] != "OK" or float(r.get("lists_ratio") or 0) >= 0.5)
        is_good = len(ok) == len(rs) and all(float(r["lists_ratio"]) <= 0.2 and float(r["candidates_per_hausp"]) <= 10
                                              for r in ok)
        is_bad = n_bad > len(rs) / 2
        good += is_good
        bad += is_bad
        print(f"  {ds:12s} batches {len(rs):2d}: time-outs {len(rs) - len(ok)}, lists_ratio >= 0.5 or time-out "
              f"{n_bad}, -> {'worth' if is_good else 'not worth' if is_bad else 'open'}")
    verdict = ("WORTH IMPLEMENTING" if good >= 5 else "NOT WORTH IT" if bad >= 4 else "OPEN")
    print(f"evaluate: {len(per)} datasets with comparable batches; worth on {good}, not worth on {bad} -> {verdict}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--datasets", nargs="*", default=list(FILES))
    ap.add_argument("--ks", nargs="*", type=int, default=[5, 20, 100])
    ap.add_argument("--timeout", type=int, default=300, help="seconds per JVM")
    ap.add_argument("--verify", action="store_true", help="also check the promotion property on pattern dumps")
    ap.add_argument("--evaluate", type=Path, metavar="RUN_DIR", help="apply the registered decision rule to a finished run")
    a = ap.parse_args()
    if a.evaluate:
        return evaluate(a.evaluate if a.evaluate.is_absolute() else ROOT / a.evaluate)

    if not JAR.exists() or any(p.stat().st_mtime > JAR.stat().st_mtime for p in (ROOT / "src").rglob("*.java")):
        raise SystemExit("the JAR is missing or older than the sources; run: mvn -q package -DskipTests")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    cfg = {e["id"]: e for e in load_config()["experiments"]}
    mu = {(r["csv_name"], k): r["min_util"] for k, e in SOURCE.items() for r in cfg[e]["runs"]}
    refs = {e: reference(e) for e in set(SOURCE.values())}

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT_ROOT / run_id
    (out / "raw").mkdir(parents=True)
    java_v = subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.strip().splitlines()
    meta = {"kind": "PROBE (feasibility, counts only; no timing here is a measurement)",
            "command": " ".join([Path(sys.executable).name, *sys.argv]), "commit": git("rev-parse", "HEAD"),
            "tree": "clean" if not dirty else f"MODIFIED ({len(dirty.splitlines())} files)",
            "jar_sha256": sha256(JAR), "java": java_v[:2], "host": socket.gethostname(),
            "python": platform.python_version(), "timeout_s": a.timeout, "sample": SAMPLE,
            "thresholds": {f"{d}@K={k}": mu.get((d, k)) for d in a.datasets for k in a.ks},
            "reference": "generation-1 results: experiment 1 for K = 5, experiment 7 (equal schedule) for K = 20 and 100"}
    (out / "run_meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"[probe] PROBE run {run_id} -> {out.relative_to(ROOT)}; tree {meta['tree']}")

    rows = []
    with tempfile.TemporaryDirectory(prefix="batch-candidates-") as scratch:
        for ds in a.datasets:
            for k in a.ks:
                m = mu[(ds, k)]
                props = Path(scratch) / f"{ds}.properties"
                props.write_text(f"dataset.path={ROOT / 'datasets' / FILES[ds]}_seq.txt\nminUtil.percentage={m}\n",
                                 encoding="utf-8")
                for b in SAMPLE[k]:
                    argv = ["java", "-Xmx24g", "-XX:+UseG1GC", "-cp", str(JAR), "BatchCandidateProbe",
                            str(ROOT / "datasets" / f"{FILES[ds]}_eui.txt"), str(ROOT / "datasets" / f"{FILES[ds]}_seq.txt"),
                            str(props), str(m), str(k), str(b)] + (["verify"] if a.verify else [])
                    try:
                        p = subprocess.run(argv, cwd=scratch, capture_output=True, text=True, timeout=a.timeout)
                        text, status = p.stdout + p.stderr, ("OK" if p.returncode == 0 else f"EXIT{p.returncode}")
                    except subprocess.TimeoutExpired as e:
                        text = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
                        status = "OT"
                    (out / "raw" / f"{ds}-K{k}-b{b}.txt").write_text(" ".join(argv[5:]) + "\n" + text, encoding="utf-8")
                    row = {"dataset": ds, "K": k, "b": b, "min_util": m, "status": status}
                    for line in text.splitlines():
                        mm = LINE.match(line.strip())
                        if mm:
                            prefix = "" if mm.group(1) == "probe" else "verify_"
                            for kv in mm.group(2).split():
                                key, _, val = kv.partition("=")
                                row[prefix + key] = val
                    full = refs[SOURCE[k]].get((ds, k, b))
                    row["full_hausp"], row["full_lists"] = (full if full else ("", ""))
                    if full and row.get("candidates", "") != "" and full[0]:
                        row["candidates_per_hausp"] = round(int(row["candidates"]) / full[0], 3)
                    if full and row.get("cand_lists", "") != "" and full[1]:
                        row["lists_ratio"] = round(int(row["cand_lists"]) / full[1], 5)
                    rows.append(row)
                    print(f"[probe] {ds} K={k} b={b}: {status} candidates={row.get('candidates', '-')} "
                          f"full_hausp={row['full_hausp']} lists_ratio={row.get('lists_ratio', '-')}"
                          + (f" verify: promoted={row.get('verify_promoted_len2plus')} missing={row.get('verify_missing')}"
                             if a.verify else ""), flush=True)
    cols = sorted({c for r in rows for c in r}, key=lambda c: (c not in ("dataset", "K", "b", "min_util", "status"), c))
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"[probe] wrote {len(rows)} rows to {(out / 'summary.csv').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
