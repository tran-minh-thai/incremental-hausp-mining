"""Paired Wilcoxon signed-rank tests for the headline comparisons of the paper.

Following Demsar (JMLR 2006), each test pairs HAUSP-UB with one baseline over
the completed configurations of an experiment (two-sided, exact for n < 25):
  - Exp 1: total runtime over 5 batches, pairs = datasets (n = 7)
  - Exp 3: update runtime at Batch 1, pairs = dataset x delta (n = 28)
  - Exp 4: peak memory, pairs = datasets (n = 7)
  - Exp 7: total runtime, pairs = dataset x K fully completed by both (n = 20)
Values are means over the independent trials. Reproduces the p-values quoted
reported with the runtime comparison of the manuscript.
"""
from pathlib import Path
import glob

import pandas as pd
from scipy.stats import wilcoxon
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PAPER_UB, load_experiment, load_memory  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OK = {"SUCCESS", "SUCCESS_MATCH"}
OUT = ROOT / "analysis_out" / "paper" / "tables" / "wilcoxon_tests.md"


def load(exp: int) -> pd.DataFrame:
    """Merged legacy + 2026-09 rows (see common.load_experiment)."""
    df = load_experiment(exp)
    if df is None:
        raise SystemExit(f"no results for experiment {exp}")
    return df


def pivot(df: pd.DataFrame, keys, val: str, agg: str) -> pd.DataFrame:
    ok = df[df["Status"].isin(OK)]
    per_trial = ok.groupby(keys + ["Algorithm", "RunIndex"], as_index=False)[val].agg(agg)
    means = per_trial.groupby(keys + ["Algorithm"], as_index=False)[val].mean()
    return means.pivot_table(index=keys, columns="Algorithm", values=val)


def test(piv: pd.DataFrame, base: str):
    """Two-sided paired test plus the direction, so a table never implies the sign of the effect."""
    both = piv[[PAPER_UB, base]].dropna()
    x, y = both[base].values, both[PAPER_UB].values
    r = wilcoxon(x, y, alternative="two-sided",
                 method="exact" if len(x) < 25 else "auto")
    wins = int((y < x).sum())
    return len(x), r.pvalue, wins


def main() -> None:
    rows = []

    piv = pivot(load(1), ["Dataset"], "tTotal(ms)", "sum")
    for b in ("EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM"):
        n, p, w = test(piv, b)
        rows.append(("Exp1 runtime (5 batches)", b, n, p, w))

    df3 = load(3)
    piv = pivot(df3[df3["BatchID"] > 0], ["Dataset", "DeltaRatio"], "tTotal(ms)", "sum")
    for b in ("EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM"):
        n, p, w = test(piv, b)
        rows.append(("Exp3 update runtime (Batch 1)", b, n, p, w))

    # Memory comes from the dedicated live-heap runs, never from the MemPeak column of a timing run
    # (used heap under lazy GC, JVM-history dependent; see EXPERIMENT_CHANGELOG 2026-09-05).
    mem4 = load_memory(4)
    if mem4 is not None:
        piv = pivot(mem4, ["Dataset"], "MemLive(MB)", "max")
        for b in ("EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM"):
            n, p, w = test(piv, b)
            rows.append(("Exp4 peak live heap", b, n, p, w))

    df7 = load(7)
    df7["K"] = (1.0 / df7["DeltaRatio"]).round().astype(int)
    ok = df7[df7["Status"].isin(OK)]
    per = ok.groupby(["Dataset", "K", "Algorithm", "RunIndex"], as_index=False).agg(
        t=("tTotal(ms)", "sum"), nb=("BatchID", "count"))
    per = per[per["nb"] == per["K"]]  # only fully-completed schedules
    means = per.groupby(["Dataset", "K", "Algorithm"], as_index=False)["t"].mean()
    piv = means.pivot_table(index=["Dataset", "K"], columns="Algorithm", values="t")
    for b in ("EHAUSM-I", "Pre-HAUSPM"):
        n, p, w = test(piv, b)
        rows.append(("Exp7 total runtime (completed-by-both)", b, n, p, w))

    out = pd.DataFrame(rows, columns=["Comparison", "Baseline", "n pairs", "p (two-sided)",
                                      "pairs where HAUSP-UB is lower"])
    print(out.to_string(index=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("# Paired Wilcoxon signed-rank tests (Demsar 2006)\n\n"
                   + out.to_markdown(index=False) + "\n")
    print(f"\n[written] {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
