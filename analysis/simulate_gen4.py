"""Simulated generation-4 directory under results-probe/, to rehearse analysis/verify_gen4.py before the
real campaign exists; --drop-arm, --bad-count and --stale-span inject the faults the verifier must refuse.

Probe tooling: its output never belongs under a results directory."""
import sys, argparse
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))
from verify_gen4 import PLAN, FILES, keys
from common import read_results
HDR = [c for c in read_results(ROOT / "results-2026-09b/exp1/experiment1_tightness.csv").columns]
ap = argparse.ArgumentParser(); ap.add_argument("out")
for fl in ("--stale-span", "--drop-arm", "--bad-count"): ap.add_argument(fl, action="store_true")
a = ap.parse_args(); out = Path(a.out)
for e, f in FILES.items():
    frames = []
    for gen in ("results-2026-09b", "results-2026-09", "results"):
        p = ROOT / gen / f
        if p.exists() and p.stat().st_size:
            frames.append(read_results(p))
    d = pd.concat(frames, ignore_index=True)
    d = d[d["Algorithm"].isin(PLAN[e]) & d["Status"].isin(("SUCCESS", "SUCCESS_MATCH"))].copy()
    d = d.assign(_k=keys(d) + "|" + d["RunIndex"].astype(str)).drop_duplicates("_k", keep="first").drop(columns="_k")
    d["RunID"] = "20260913-0800"
    if a.drop_arm: d = d[d["Algorithm"] != PLAN[e][0]]
    if a.bad_count and e == 1:
        i = d.index[d["RunIndex"] == 0][0]; d.loc[i, "Recursed"] = float(d.loc[i, "Recursed"] or 0) + 7
    prov = [f"# run_id=20260913-0800 git=788ce29 jvm=26.0.1 heap=24g host=sim tree=clean cmd=--exp {e} --results-dir results-2026-09c (SIMULATED)"]
    if a.stale_span and e == 1:
        prov.append("# run_id=20260901-0800 git=788ce29 jvm=26.0.1 heap=24g host=sim tree=clean cmd=--exp 1 (SIMULATED old)")
        d.iloc[:5, d.columns.get_loc("RunID")] = "20260901-0800"
    dst = out / f; dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w") as fh:
        fh.write("\n".join(prov) + "\n")
        d.reindex(columns=HDR).to_csv(fh, index=False)
    print(f"exp{e}: {len(d)} rows, arms {sorted(d['Algorithm'].unique())}")
