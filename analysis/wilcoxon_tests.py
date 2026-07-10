"""Paired Wilcoxon signed-rank tests for the headline comparisons of the paper.

Following Demsar (JMLR 2006), each test pairs HAUSP-UB with one baseline over
the completed configurations of an experiment (two-sided, exact for n < 25):
  - Exp 1: total runtime over 5 batches, pairs = datasets (n = 7)
  - Exp 3: update runtime at Batch 1, pairs = dataset x delta (n = 28)
  - Exp 4: peak memory, pairs = datasets (n = 7)
  - Exp 7: total runtime, pairs = dataset x K fully completed by both (n = 20)
Values are means over the independent trials. Reproduces the p-values quoted
in Section 5.1 of the manuscript.
"""
from pathlib import Path
import glob

import pandas as pd
from scipy.stats import wilcoxon

ROOT = Path(__file__).resolve().parent.parent
OK = {"SUCCESS", "SUCCESS_MATCH"}
OUT = ROOT / "analysis_out" / "paper" / "tables" / "wilcoxon_tests.md"


def load(exp: int) -> pd.DataFrame:
    fs = glob.glob(str(ROOT / "results" / f"exp{exp}" / "*.csv"))
    return pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)


def pivot(df: pd.DataFrame, keys, val: str, agg: str) -> pd.DataFrame:
    ok = df[df["Status"].isin(OK)]
    per_trial = ok.groupby(keys + ["Algorithm", "RunIndex"], as_index=False)[val].agg(agg)
    means = per_trial.groupby(keys + ["Algorithm"], as_index=False)[val].mean()
    return means.pivot_table(index=keys, columns="Algorithm", values=val)


def test(piv: pd.DataFrame, base: str):
    both = piv[["HAUSP-UB", base]].dropna()
    x, y = both[base].values, both["HAUSP-UB"].values
    r = wilcoxon(x, y, alternative="two-sided",
                 method="exact" if len(x) < 25 else "auto")
    return len(x), r.pvalue


def main() -> None:
    rows = []

    piv = pivot(load(1), ["Dataset"], "tTotal(ms)", "sum")
    for b in ("EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM"):
        n, p = test(piv, b)
        rows.append(("Exp1 runtime (5 batches)", b, n, p))

    df3 = load(3)
    piv = pivot(df3[df3["BatchID"] > 0], ["Dataset", "DeltaRatio"], "tTotal(ms)", "sum")
    for b in ("EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM"):
        n, p = test(piv, b)
        rows.append(("Exp3 update runtime (Batch 1)", b, n, p))

    piv = pivot(load(4), ["Dataset"], "MemPeak(MB)", "max")
    for b in ("EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM"):
        n, p = test(piv, b)
        rows.append(("Exp4 peak memory", b, n, p))

    df7 = load(7)
    df7["K"] = (1.0 / df7["DeltaRatio"]).round().astype(int)
    ok = df7[df7["Status"].isin(OK)]
    per = ok.groupby(["Dataset", "K", "Algorithm", "RunIndex"], as_index=False).agg(
        t=("tTotal(ms)", "sum"), nb=("BatchID", "count"))
    per = per[per["nb"] == per["K"]]  # only fully-completed schedules
    means = per.groupby(["Dataset", "K", "Algorithm"], as_index=False)["t"].mean()
    piv = means.pivot_table(index=["Dataset", "K"], columns="Algorithm", values="t")
    for b in ("EHAUSM-I", "Pre-HAUSPM"):
        n, p = test(piv, b)
        rows.append(("Exp7 total runtime (completed-by-both)", b, n, p))

    out = pd.DataFrame(rows, columns=["Comparison", "Baseline", "n pairs", "p (two-sided)"])
    print(out.to_string(index=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("# Paired Wilcoxon signed-rank tests (Demsar 2006)\n\n"
                   + out.to_markdown(index=False) + "\n")
    print(f"\n[written] {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
