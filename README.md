# incremental-hausp-mining

Reference Java implementation of HAUSP-UB, an algorithm for incremental
high-average-utility sequential pattern mining over batch-growing quantitative
sequence databases.

Repository contents:

- the proposed algorithm (`HAUSP_UB`); its ablation variants are the same class under an arm
  name, `HAUSP-UB-L1`, `HAUSP-UB-L1L3` or `HAUSP-UB[opt+opt]`, and any other name is refused;
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
│   ├── run_2026-09.sh            Re-entrant runbook of the 2026-09 supplementary campaign.
│   ├── fetch_datasets.sh         Download the datasets and verify them against datasets/MANIFEST.sha256.
│   ├── run.bat                   Windows cmd launcher.
│   └── run.ps1                   Windows PowerShell launcher.
├── analysis/                     Python scripts that rebuild every table and figure, and the
│                                 checks that can fail (see "Checks that can fail").
├── analysis_out/                 Derived tables and figures (regenerable).
├── results/                      Measurement CSVs of the original campaign (legacy schema), one directory per experiment.
├── results-2026-09/              Measurement CSVs of the 2026-09 campaign (new schema, see "Two CSV generations").
├── results-2026-09b, -09c, -09d/ Later generations; each replaces the arms it carries, newest first.
├── results-probe/                Feasibility and verification runs. Never a source for a number;
│                                 see results-probe/README.md.
├── datasets/                     MANIFEST.sha256 pins all sixteen files; the seven measured
│   │                             databases are fetched, not tracked (see "Datasets").
│   ├── MANIFEST.sha256           bible/, bms1_spmf/, fifa/, kosarak/, leviathan/, sign/, syn/
│   └── example/{example_seq.txt, example_eui.txt}  Toy data; tracked, so the smoke test
│                                 and verify_definitions.py run straight after a clone.
└── src/main/java/
    ├── ExperimentConfig.java     Datasets and per-experiment parameters.
    ├── ConfigBridge.java         Materialises temporary .properties files.
    ├── ExperimentLauncher.java   Entry point; dispatches the runners (--exp 1..8, opt-in 9, 10, 11).
    ├── RunMeta.java              Provenance stamp written into every CSV: run id, commit, tree
    │                             state, JVM, heap, host, and a digest of datasets/MANIFEST.sha256.
    ├── Experiment{1..8}Runner.java  Experiments 9-11 reuse runners 1, 3 and 7.
    ├── SPMF_Converter.java       Utility generator that produced datasets/ (see its header).
    ├── HAUSP_UB.java             Proposed algorithm; fromArmName() configures every ablation.
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
| Python    | 3.9     | 3.9.6  |

Python is needed only for the scripts under `analysis/`; the experiments
themselves need nothing beyond the JDK. Pin the versions with
`python3 -m pip install -r analysis/requirements.txt`, in a virtual environment
if the interpreter is managed by the system.

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

### Launcher options

```
--exp 1,3            experiments to run (1..8; opt-in studies: 9 attribution, 10 pre-large mu sweep, 11 warm-start K)
--repeats N          trials per configuration (default 3)
--repeats-min-seconds S   configurations whose first trial takes under S seconds get 15 (<1 s), 10 (<10 s) or 5 (<120 s) trials
--dataset a,b        dataset short names (bible, bms1_spmf, fifa, kosarak, leviathan, sign, syn_c8t1s5i8n5k, example)
--algo A,B           arm names exactly as written in the CSV (e.g. HAUSP-UB-L1,HAUSP-UB)
--k 10,100           batch counts for Experiments 7 and 11
--results-dir DIR    root of the result CSVs (default results)
--timeout MIN        per-batch time limit in minutes
--resume             skip configurations already present in the CSV
--dump-config json   print every declared parameter as JSON and exit
```

### Maven directly

```bash
mvn -q package
mvn -q exec:java -Dexec.args="--exp 1"
java -Xmx16g -jar build/incremental-hausp-mining-1.0.0.jar --exp all
```

Each experiment writes a single CSV under `results/expN/`.

## Default parameters

Everything below changes a recorded number, so a result is only comparable with
another result taken under the same values. The table is generated from the
sources by `analysis/default_parameters.py`; `--check` exits non-zero when it has
drifted, and is run before a release.

<!-- BEGIN default-parameters (generated by analysis/default_parameters.py) -->

| parameter | default | declared at | what it controls |
|---|---|---|---|
| `REPEATS` | `3` | `src/main/java/ExperimentConfig.java:53` | Trials per configuration; the CSV keeps every trial. `--repeats N`. |
| `REPEATS_MIN_SECONDS` | `0.0` | `src/main/java/ExperimentConfig.java:62` | 0 disables adaptive repeats. `--repeats-min-seconds S` raises the trial count for short configurations. |
| `RESULTS_DIR` | `results` | `src/main/java/ExperimentConfig.java:44` | Root of the result CSVs. `--results-dir DIR`. |
| `TIMEOUT_OVERRIDE_MIN` | `0` | `src/main/java/ExperimentConfig.java:86` | 0 keeps each experiment's own limit. `--timeout MIN` overrides it for every batch. |
| `MEM_MODE_LIVE` | `false` | `src/main/java/ExperimentConfig.java:94` | false records the used heap; `--mem-mode live` forces a collection before each sample and needs a separate results directory. |
| `MU_PRELARGE` | `0.20` | `src/main/java/ExperimentConfig.java:249` | Pre-large ratio used by every experiment that does not sweep it. |
| `MU_SWEEP` | `0.05, 0.10, 0.20, 0.40` | `src/main/java/ExperimentConfig.java:252` | Pre-large ratios swept by the opt-in study. |
| `EXP3_DELTAS` | `0.05, 0.10, 0.15, 0.20` | `src/main/java/ExperimentConfig.java:255` | Increment sizes, as a fraction of the database. |
| `EXP7_BATCH_COUNTS` | `10, 20, 50, 100` | `src/main/java/ExperimentConfig.java:258` | Numbers of batches the database is split into. |
| `EXP11_BATCH_COUNTS` | `100` | `src/main/java/ExperimentConfig.java:261` | Batch counts of the warm-start study. |
| `WARM_START_FIRST_RATIO` | `0.20` | `src/main/java/ExperimentConfig.java:264` | Share of the database in the first batch under the `warm20` schedule; the rest is split equally. |
| `MemorySampler.INTERVAL_MS` | `1000L` | `src/main/java/MemorySampler.java:31` | Sampling period of the peak-memory series. A longer period can miss a peak. |
| `RunIsolation.GC_DEADLINE_MS` | `2000` | `src/main/java/RunIsolation.java:41` | Time allowed for the heap to settle between arms, so one arm's garbage is not charged to the next. |
| `RunIsolation.TEARDOWN_WAIT_SEC` | `5` | `src/main/java/RunIsolation.java:40` | Time allowed for a timed-out arm to stop before the run is marked `OT`. |
| `HEAP` | `24g` | `scripts/run.sh:32` | JVM heap ceiling (`-Xmx`). Part of the identity of a measurement: numbers taken under different ceilings do not compare. |
| `ALGO_TIMEOUT_MIN` | `90` | `scripts/run.sh:33` | Per-batch time limit in minutes passed as `--timeout`. |
| garbage collector | `-XX:+UseG1GC` | `scripts/run.sh:60` | Collector selected on the command line; it changes both timing and the memory series. |

Every per-experiment value -- participating datasets, minimum-utility thresholds,
batch schedules, arm lists and per-experiment time limits -- is printed in full by
`java -jar build/incremental-hausp-mining-1.0.0.jar --dump-config json`, which reads
the same declarations the runs read.

<!-- END default-parameters -->

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

## Datasets

The annotated datasets used by every experiment are pinned by
`datasets/MANIFEST.sha256`. Item quantities and unit profits were generated by
`src/main/java/SPMF_Converter.java` (seed 42) from the public SPMF files.
Three of them (BIBLE, BMS1, C8T1S5I8N5K) are value-identical to release
`v1.1-seed42-lognormal` of the shared repository
[huspm-datasets](https://github.com/tran-minh-thai/huspm-datasets); FIFA,
KOSARAK, LEVIATHAN and SIGN come from an earlier batch conversion whose
generator was shared across the batch, so they cannot be regenerated with a
per-file seed and are distributed verbatim.

The seven measured databases are **not tracked here**. They live in that shared
repository under their own tag, `hausp-ub-v1-exact`, which exists because the
repository's other releases carry different conversions of the same source
sequences: only two of the sixteen files match them byte for byte. Fetch them
before the first run:

```bash
./scripts/fetch_datasets.sh                # download + verify into datasets/
./scripts/fetch_datasets.sh --verify-only  # check files already present
```

Both forms end by checking every file against `datasets/MANIFEST.sha256` and fail
loudly on a mismatch. A `FAILED` line means the copy on disk is not the one the
recorded numbers were taken on, and nothing measured against it is comparable.

Only `datasets/example/` is kept in the repository: it is two small files, and the
smoke test and `analysis/verify_definitions.py` have to run straight after a clone,
before anything has been downloaded.

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

Every runner appends rows to a CSV. Files written since 2026-09-04 start with a
provenance comment (readers must skip lines beginning with `#`):

```
# run_id=20260904-0748 git=e0ec34f jvm=26.0.1 heap=24g host=<machine> tree=clean cmd=--exp 1 ...
Timestamp, Algorithm, Dataset, BatchID, RunIndex, MinUtil, mu, DeltaRatio,
TotalDBUtil, CumulativeDBSize,
tScan(ms), tMining(ms), tTotal(ms), tLayer1(ms), tLayer2(ms), tLayer3(ms),
Cand, PrunedL1(SWU), PrunedL2(IAUUB), PrunedL3(MFUUB),
TightnessPEAU, TightnessIAUUB, TightnessMFUUB,
HAUSP, SHAUS, MemPeak(MB),
PoolBorrows, PoolReuses, PoolPeakLive, AudulActive, Status,
Recursed, ArmOrder, Schedule, PoolBytes, FlatBytes, EucsBytes, AudulRootBytes,
RescanTriggered, BufferUtil, BufferTested, SafetyBound, PrunedL3Node, PrunedL1Root, RunID
```

`Cand` is the number of utility lists assembled, counted the same way for every
algorithm: every root list of an item with SWU at or above the threshold and
every child projection list built, whether or not a later test rejects it;
`Recursed` is the number of children recursed into. `PrunedL1Root` counts the
root lists HAUSP-UB rejects by its root test (the baselines reject the same
roots inside their DFS by PEAU). `ArmOrder` is
the position of the arm in the executed arm list (measurement order),
`Schedule` the batch schedule label of Experiments 7/11 (`equal`, `warm20`),
`RunID` the run identifier of the provenance line. `PoolBytes`, `FlatBytes`,
`EucsBytes` and `AudulRootBytes` are the array payloads of the persistent
structures of HAUSP-UB sampled when the batch's heap peak was recorded;
`RescanTriggered`, `BufferUtil`, `BufferTested` and `SafetyBound` describe the
pre-large buffer of Pre-HAUSPM; `PrunedL3Node` is the share of `PrunedL3(MFUUB)`
applied on node entry; `PrunedL1Root` the root lists rejected by the root test. Cells that an algorithm does not measure are empty.

### Two CSV generations

`results/` holds the original campaign in the legacy schema (columns up to
`Status`; for the HAUSP-UB arms `Cand` there counted only children that
survived Layers 2 and 3, and the `HAUSP-UB-L1` rows were produced with the
Layer-3 test still active). `results-2026-09/` holds the supplementary
campaign in the schema above. `analysis/common.py` merges the two: a
measurement condition is replaced by its re-run only when both runs measured
the same arm set, legacy `HAUSP-UB-L1` rows are dropped, and legacy HAUSP-UB
candidate counts are taken from the counts re-run under
`results-2026-09/counts/` (they cannot be recovered from the legacy columns;
`analysis/verify_count_identity.py` documents why).

The `tLayer1/2/3(ms)` and pool columns are populated by HAUSP-UB and its
ablation variants only; the baselines log zero. `RunIndex` is zero-based and
identifies the trial within the `--repeats N` sweep. `PoolBorrows` / `PoolReuses`
quantify how many AU-DUL allocations were avoided by the shared pool;
`AudulActive` is the number of accumulated 1-itemset AU-DULs that remain live
at the end of the batch, and is the metric used in Experiment 7 to verify that
the long-run memory stays bounded.

Experiment 6 uses a narrower schema dedicated to multi-batch agreement counts.

### Result trees are never overwritten

A generation, once written, stays as it is. A re-measurement opens a **new**
directory rather than editing an old one, named after when it ran and the commit it
ran from, and `analysis/common.py` decides which generation supplies each arm --
newest first, and only when the newer run measured the same arm set. This is what
lets an old number be traced to the run that produced it instead of being quietly
replaced.

Two consequences worth stating, because both were paid for:

* A run may not append rows under a different column header; `CSVLogger` refuses,
  rather than leaving one file with two schemas in it.
* When a version of the manuscript is submitted, the trees it was built from are
  copied once into `results-submitted-<yyyymmdd>-<commit>/` and nothing writes there
  again. Later campaigns keep opening their own generations beside it.

## Reproducing the paper's analysis

The measurement CSVs behind every number in the paper are committed under
`results/`. The scripts in `analysis/` rebuild all derived artifacts from
them:

```bash
python3 -m pip install -r analysis/requirements.txt
python3 analysis/dataset_stats.py              # dataset characteristics measured from the files
python3 analysis/verify_count_identity.py \
    --old results/exp1/experiment1_tightness.csv \
    --new results-2026-09/exp1_probe/exp1/experiment1_tightness.csv   # legacy/new counting identities
python3 analysis/build_latex_tables.py         # every numeric table of the manuscript (+ copy to ../paper/tables)
python3 analysis/check_inputs.py               # every generated table is \input by the manuscript
python3 analysis/build_report.py               # Markdown summary tables + figure PDFs
python3 analysis/audit_results.py              # consistency checks over the collected CSVs
python3 analysis/wilcoxon_tests.py             # paired Wilcoxon significance tests
python3 analysis/memprobe_report.py            # memory attribution of the FIFA K=100 probe
python3 analysis/identifier_space.py           # what the identifier-indexed structures cost per dataset
python3 analysis/default_parameters.py         # rewrite the default-parameter table of this README
```

The last step -- carrying a value into the manuscript's own prose -- is not here. It
belongs with the manuscript, together with the vocabulary the manuscript uses for each
quantity, so this repository stops where a number is still data: a CSV, a generated
table, a JSON file. That is also why nothing under `analysis/` has to be filtered before
publication.

### Checks that can fail

These are not summaries; each was shown to reject an injected fault, and each prints the denominator
of what it compared.

```bash
python3 analysis/verify_definitions.py         # the miner against the paper's definitions, on boundary cases
python3 analysis/verify_counts_probe.py --probe <probe csv>   # no deterministic count moved
python3 analysis/verify_gen3.py                # generation-3 completeness, counts, provenance
python3 analysis/verify_gen4.py                # generation-4 single-campaign checks
python3 analysis/default_parameters.py --check # the README table still matches the sources
python3 analysis/check_language.py             # no non-English text, no manuscript numbering
```

`verify_definitions.py` builds ten small databases, each aimed at one boundary the description could
get wrong, mines each with the real implementation and compares the reported patterns, as sets, with
an exhaustive reference that applies the average-utility and HAUSP definitions and reuses nothing of
the algorithm. These databases exist to test the code; the manuscript illustrates its definitions
with one example database only.

`simulate_gen3.py` and `simulate_gen4.py` build a simulated generation under `results-probe/` and
inject the faults `verify_gen3.py` and `verify_gen4.py` must refuse. They are how those two
verifiers are shown not to be nodding machines; point `HAUSP_NEWER_RESULTS` or
`HAUSP_NEWEST_RESULTS` at the simulated directory and `common.py` prints a REHEARSAL line so the
run cannot be mistaken for a measurement.

On macOS the system interpreter (`/usr/bin/python3`, 3.9) carries the pinned
pandas; a Homebrew `python3` without pandas fails at import.

Everything is written to `analysis_out/paper/` and is deterministic: the same
CSVs yield the same tables, figures, and p-values. Every generated `.tex`
table starts with a `% source:` line naming the CSV files and run ids behind
it, and the best value of each comparison group is set in bold by the
generator.

For long unattended runs, `scripts/run_resume.sh` executes the heavy
experiments under the uniform protocol (identical batch schedules, 90-minute
per-batch limit) and can be interrupted and restarted at any time; completed
work is skipped by reading the results CSVs.

## Citation

If you use this code or the parameters declared in `ExperimentConfig.java` in
your research, please cite the HAUSP-UB paper.

## License

Released under the MIT License; see [LICENSE](LICENSE) for the full text.
