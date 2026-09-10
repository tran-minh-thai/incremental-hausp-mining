"""Build a SIMULATED generation-3 directory under results-probe/ to rehearse verify_gen3.py and the
analysis pipeline (HAUSP_NEWER_RESULTS=results-probe/<dir>). Fault injections test that the verifier refuses.
Build a SIMULATED generation-3 directory from the generation-2 CSVs (probe tooling, not results).
HAUSP-UB rows of the planned arms are copied with tTotal scaled by --scale, tLayer2/3 zeroed and a
synthetic RunID; Exp 7 keeps only the cells step c7 runs (the five OT cells are excluded unless --with-ot).
Optional fault injections: --drop-cell (omit one Exp 1 cell), --perturb-count (change one Recursed value)."""
import argparse, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))
from common import PAPER_UB
PLAN = {1: [PAPER_UB], 3: [PAPER_UB], 11: [PAPER_UB], 7: [PAPER_UB],
        2: [PAPER_UB, "HAUSP-UB[noL2+noEUCS]", "HAUSP-UB[noL3+noEUCS]"],
        9: ["HAUSP-UB[noL2+L3@node+nopool+noEUCS]", "HAUSP-UB[noL2+nopool+noEUCS]", "HAUSP-UB[noL2+noEUCS]", PAPER_UB]}
# arms with no generation-2 rows are simulated by cloning the paper arm under their name
CLONE = {"HAUSP-UB[noL2+L3@node+nopool+noEUCS]": PAPER_UB, "HAUSP-UB[noL2+nopool+noEUCS]": PAPER_UB}
FILES = {1: "exp1/experiment1_tightness.csv", 2: "exp2/experiment2_pruning_power.csv", 3: "exp3/experiment3_scalability.csv",
         7: "exp7/experiment7_long_batch.csv", 9: "exp9/experiment9_attribution.csv", 11: "exp11/experiment11_warm_start.csv"}
OT7 = {("SIGN", 20), ("SIGN", 50), ("SIGN", 100), ("C8T1S5I8N5K", 50), ("C8T1S5I8N5K", 100)}
ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--scale", type=float, default=0.8)
ap.add_argument("--with-ot", action="store_true"); ap.add_argument("--drop-cell", action="store_true"); ap.add_argument("--perturb-count", action="store_true")
a = ap.parse_args(); out = Path(a.out)
for e, f in FILES.items():
    raw = (ROOT / "results-2026-09" / f).read_text().splitlines()
    hdr = next(l for l in raw if l.startswith("Timestamp,")).split(",")
    ix = {c: i for i, c in enumerate(hdr)}
    rows = []
    for l in raw:
        if not l or l.startswith("#") or l.startswith("Timestamp,"): continue
        r = l.split(",")
        clones = [c for c, src in CLONE.items() if e == 9 and src == r[ix["Algorithm"]]]
        if r[ix["Algorithm"]] not in PLAN[e] and not clones: continue
        for c in clones:
            rc = list(r); rc[ix["Algorithm"]] = c; rc[ix["tTotal(ms)"]] = str(int(float(rc[ix["tTotal(ms)"]]) * a.scale * 1.2))
            rc[ix["tLayer2(ms)"]] = "0"; rc[ix["tLayer3(ms)"]] = "0"; rc[ix["RunID"]] = "99990910-0001"; rows.append(rc)
        if r[ix["Algorithm"]] not in PLAN[e]: continue
        if e == 7:
            k = round(1.0 / float(r[ix["DeltaRatio"]]))
            if (r[ix["Dataset"]], k) in OT7 and not a.with_ot: continue
        if r[ix["Status"]] in ("OT", "OOM") and not a.with_ot: continue
        r[ix["tTotal(ms)"]] = str(int(float(r[ix["tTotal(ms)"]]) * a.scale))
        r[ix["tMining(ms)"]] = str(int(float(r[ix["tMining(ms)"]]) * a.scale))
        r[ix["tLayer2(ms)"]] = "0"; r[ix["tLayer3(ms)"]] = "0"; r[ix["RunID"]] = "99990910-0001"
        rows.append(r)
    if e == 1 and a.drop_cell:
        rows = [r for r in rows if not (r[ix["Dataset"]] == "SIGN" and r[ix["BatchID"]] == "4")]
    if e == 9 and a.perturb_count:
        r = next(r for r in rows if r[ix["Dataset"]] == "BIBLE" and r[ix["RunIndex"]] == "0"); r[ix["Recursed"]] = str(int(r[ix["Recursed"]]) + 1)
    dst = out / f; dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("# run_id=99990910-0001 git=a705348 jvm=26.0.1 heap=24g host=sim tree=clean cmd=--exp %d --results-dir results-2026-09b (SIMULATED)\n" % e
                   + ",".join(hdr) + "\n" + "\n".join(",".join(r) for r in rows) + "\n")
    print(f"exp{e}: {len(rows)} simulated rows")
