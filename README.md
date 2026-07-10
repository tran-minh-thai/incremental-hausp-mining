# incremental-hausp-mining

Reference Java implementation of HAUSP-UB, an algorithm for incremental
high-average-utility sequential pattern mining over batch-growing quantitative
sequence databases.

Repository contents:

- the proposed algorithm (`HAUSP_UB`) and its ablation variant
  (`HAUSP_UB_IAUUB`, Layer 3 disabled);
- three reimplemented baselines on a shared AU-DUL representation:
  `EHAUSM_Remining` (re-mining oracle), `EHAUSM_Inc` (incremental baseline) and
  `Pre_HUSPM_adapt` (pre-large buffer);
- eight experiment runners covering the tightness, ablation, scalability,
  memory, and exactness studies in the paper, together with the long-batch
  growth stress test (Experiment 7) and the low-threshold sensitivity sweep
  (Experiment 8).

All datasets, all minimum-utility thresholds and the entire batch schedule are
declared in a single Java source file (`ExperimentConfig.java`); no `.properties`
files are read at runtime.

## Repository layout

```
.
├── pom.xml                       Maven build; pulls fastutil 8.5.12.
├── README.md
├── LICENSE                       MIT.
├── scripts/
│   ├── run.sh                    macOS / Linux launcher.
│   ├── run_resume.sh             Resumable orchestrator for the long runs.
│   ├── run.bat                   Windows cmd launcher.
│   └── run.ps1                   Windows PowerShell launcher.
├── analysis/                     Python scripts that rebuild every table and figure.
├── analysis_out/                 Derived tables and figures (regenerable).
├── results/                      Measurement CSVs, one directory per experiment.
├── datasets/                     Six SPMF benchmarks and one synthetic corpus.
│   ├── bible/{BIBLE_seq.txt, BIBLE_eui.txt}
│   ├── bms1_spmf/{BMS1_SPMF_seq.txt, BMS1_SPMF_eui.txt}
│   ├── fifa/{FIFA_seq.txt, FIFA_eui.txt}
│   ├── kosarak/{KOSARAK_seq.txt, KOSARAK_eui.txt}
│   ├── leviathan/{LEVIATHAN_seq.txt, LEVIATHAN_eui.txt}
│   ├── sign/{SIGN_seq.txt, SIGN_eui.txt}
│   ├── syn/{C8T1S5I8N5K_seq.txt, C8T1S5I8N5K_eui.txt}   IBM Quest synthetic corpus.
│   └── example/{example_seq.txt, example_eui.txt}  Toy worked-example data.
└── src/main/java/
    ├── ExperimentConfig.java     Datasets and per-experiment parameters.
    ├── ConfigBridge.java         Materialises temporary .properties files.
    ├── ExperimentLauncher.java   Entry point; dispatches Experiment1..8Runner.
    ├── Experiment{1..8}Runner.java
    ├── HAUSP_UB.java             Proposed algorithm.
    ├── HAUSP_UB_IAUUB.java       Ablation (IAUUB only).
    ├── EHAUSM_Inc.java           Incremental baseline.
    ├── EHAUSM_Remining.java      Re-mining oracle.
    ├── Pre_HUSPM_adapt.java      Pre-large buffer baseline.
    ├── QSDB_Parser.java          QSDB reader.
    ├── CSVLogger.java            CSV output writer.
    ├── RunIsolation.java         GC + sandbox executor + CPU timing.
    ├── RunResult.java            Per-run result record.
    └── Sequence.java, Itemset.java, ItemQ.java
```

## Requirements

| Component | Minimum | Tested |
|-----------|---------|--------|
| JDK       | 11      | 17 LTS |
| Maven     | 3.6     | 3.9    |
| RAM       | 4 GB    | 24 GB  |

The only third-party dependency is `it.unimi.dsi:fastutil:8.5.12`, retrieved
automatically from Maven Central.

## Build and run

### macOS or Linux

```bash
chmod +x scripts/run.sh
./scripts/run.sh              # all eight experiments, three trials each
./scripts/run.sh 1            # only Experiment 1
./scripts/run.sh 1,3,5        # selected experiments
HEAP=24g ./scripts/run.sh 4   # custom -Xmx
./scripts/run.sh 1 --repeats 5  # five independent trials per configuration
```

### Windows (cmd.exe)

```cmd
scripts\run.bat
scripts\run.bat 1
scripts\run.bat 1,3,5
set HEAP=24g && scripts\run.bat 4
```

### Windows (PowerShell)

```powershell
.\scripts\run.ps1
.\scripts\run.ps1 1
.\scripts\run.ps1 1,3,5
$env:HEAP="24g"; .\scripts\run.ps1 4
```

### Maven directly

```bash
mvn -q package
mvn -q exec:java -Dexec.args="--exp 1"
java -Xmx16g -jar build/incremental-hausp-mining-1.0.0.jar --exp all
```

Each experiment writes a single CSV under `results/expN/`.

## Modifying parameters

Open `src/main/java/ExperimentConfig.java`. Each of `EXP1`..`EXP6` lists its
participating datasets through `DatasetRun` factories:

```java
DatasetRun.simple(BIBLE,     0.0005, MU_PRELARGE, FIVE_BATCH_20)   // single minUtil
DatasetRun.withMinUtils(BIBLE, new double[]{0.001, 0.0009, 0.0008, 0.0007, 0.0006}, MU_PRELARGE) // sweep
DatasetRun.withThresholds(BIBLE, new double[]{0.0005, 0.0004, 0.00025}, MU_PRELARGE)             // discrete list
```

Same dataset, different experiment, different minimum-utility threshold — that
is precisely the case handled in the paper (for example BIBLE uses 0.10% in
Experiment 1 but 0.05% in Experiments 3, 4 and 6).

Recompile and rerun; no other file needs to change.

## Data format

Each dataset has two files in `datasets/<name>/`.

`<NAME>_seq.txt` lists one quantitative sequence per line:

```
1[2] -1 2[1] 3[2] -1 -2
4[5] 6[3] -1 -2
```

`itemID[quantity]` is a single item with its quantity; `-1` closes an itemset;
`-2` closes a sequence.

`<NAME>_eui.txt` lists the external utility of every item:

```
1:5
2:3
3:2
```

Both `:` and `,` separators are accepted. Internal utility is computed as
`quantity × externalUtility`.

## Output format

Every runner appends rows to a CSV with the following schema:

```
Timestamp, Algorithm, Dataset, BatchID, RunIndex, MinUtil, mu, DeltaRatio,
TotalDBUtil, CumulativeDBSize,
tScan(ms), tMining(ms), tTotal(ms), tLayer1(ms), tLayer2(ms), tLayer3(ms),
Cand, PrunedL1(SWU), PrunedL2(IAUUB), PrunedL3(MFUUB),
TightnessPEAU, TightnessIAUUB, TightnessMFUUB,
HAUSP, SHAUS, MemPeak(MB),
PoolBorrows, PoolReuses, PoolPeakLive, AudulActive, Status
```

The `tLayer1/2/3(ms)` and pool columns are populated by HAUSP-UB and its
ablation variants only; the baselines log zero. `RunIndex` is zero-based and
identifies the trial within the `--repeats N` sweep. `PoolBorrows` / `PoolReuses`
quantify how many AU-DUL allocations were avoided by the shared pool;
`AudulActive` is the number of accumulated 1-itemset AU-DULs that remain live
at the end of the batch, and is the metric used in Experiment 7 to verify that
the long-run memory stays bounded.

Experiment 6 uses a narrower schema dedicated to multi-batch agreement counts.

## Reproducing the paper's analysis

The measurement CSVs behind every number in the paper are committed under
`results/`. The scripts in `analysis/` rebuild all derived artifacts from
them:

```bash
python3 -m pip install -r analysis/requirements.txt
python3 analysis/build_report.py        # Markdown summary tables + figure PDFs
python3 analysis/build_latex_tables.py  # LaTeX table bodies used in the manuscript
python3 analysis/audit_results.py       # consistency checks over the collected CSVs
python3 analysis/wilcoxon_tests.py      # paired Wilcoxon significance tests
```

Everything is written to `analysis_out/paper/` and is deterministic: the same
CSVs yield the same tables, figures, and p-values. In the manuscript the best
value of each comparison group is additionally set in bold; that typographic
step lives in the LaTeX sources, not in these scripts.

For long unattended runs, `scripts/run_resume.sh` executes the heavy
experiments under the uniform protocol (identical batch schedules, 90-minute
per-batch limit) and can be interrupted and restarted at any time; completed
work is skipped by reading the results CSVs.

## Citation

If you use this code or the parameters declared in `ExperimentConfig.java` in
your research, please cite the HAUSP-UB paper.

## License

Released under the MIT License; see [LICENSE](LICENSE) for the full text.
