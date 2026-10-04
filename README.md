# incremental-hausp-mining

Java implementation of HAUSP-UB, an algorithm for incremental high-average-utility sequential
pattern mining on quantitative sequence databases that grow in batches. The repository contains
the algorithm, three baselines, the measurement campaign behind the paper, and the scripts that
rebuild every table and figure from it.

## Contents

```
pom.xml                  Maven build; one dependency, fastutil 8.5.12
src/main/java/           Algorithms, experiment runners, configuration
scripts/                 campaign.py (measurement driver), fetch_datasets.py, build_tafeng.py, run.sh
analysis/                Python scripts for tables, figures and checks
datasets/                MANIFEST.sha256 and a toy database; the real datasets are fetched
results/                 The measurement campaign; live-heap runs are under results/mem/
results-invariant/       Machine-independent results (pattern-set comparisons)
results-probe/           Feasibility and verification runs; no number in the paper comes from here
analysis_out/            Generated tables and figures
MEASUREMENT_MACHINE.txt  The machine every timing comes from
COMMIT_MAP.tsv           Old commit identifiers mapped to current ones (see "Checks")
provenance_map.json      Commit recorded in each result file mapped to its current commit
```

| Class | Role |
|---|---|
| `HAUSP_UB` | The proposed algorithm. Its variants are the same class under an arm name (`HAUSP-UB`, `HAUSP-UB-L1`, `HAUSP-UB-L1L3`, or `HAUSP-UB[opt+opt]`); any other name is refused. |
| `EHAUSM_Remining` | Re-mining baseline and correctness oracle |
| `EHAUSM_Inc` | Incremental baseline that keeps a pattern tree across batches |
| `Pre_HUSPM_adapt` | Pre-large baseline |
| `ExperimentConfig` | Every dataset, threshold and batch schedule. No `.properties` file is read at run time. |
| `ExperimentLauncher` | Entry point |
| `Experiment1Runner` to `Experiment8Runner` | One runner per campaign experiment; experiments 9, 10 and 11 reuse runners 1, 3 and 7 |
| `RunMeta` | Writes the provenance line at the top of every CSV |
| `SPMF_Converter` | Generator of the synthetic utilities (see "Datasets") |
| `WorkedExampleProbe` | Runs the presented arm on a small two-batch database for `analysis/verify_worked_example.py` |

The result files use the arm labels below; the paper uses the names on the right.

| Label in the CSVs | Name in the paper |
|---|---|
| `HAUSP-UB[noEUCS]` | HAUSP-UB |
| `HAUSP-UB[noL2+noEUCS]` | HAUSP-UB<sup>L1L3</sup> |
| `HAUSP-UB[noL3+noEUCS]` | HAUSP-UB<sup>L1L2</sup> |
| `HAUSP-UB-L1` | HAUSP-UB<sup>L1</sup><sub>EUCS</sub> (keeps the EUCS co-occurrence pre-filter) |
| `HAUSP-UB` | HAUSP-UB<sub>EUCS</sub> (keeps the EUCS co-occurrence pre-filter) |
| `EHAUSM-R` | APEAU-R |
| `EHAUSM-I` | APEAU-I |
| `Pre-HAUSPM` | Pre-HAUSPM |

## Requirements

| Component | Minimum | Tested |
|---|---|---|
| JDK | 11 | 25 (measurement machine), 26 (development) |
| Maven | 3.6 | 3.9 |
| Python | 3.9 | 3.9.6 (development) |
| RAM | 32 GB for the 24 GB heap | 64 GB (measurement machine) |

`campaign.py` and `fetch_datasets.py` use only the Python standard library. The analysis scripts
need the packages pinned in `analysis/requirements.txt`.

## Datasets

`datasets/MANIFEST.sha256` lists 18 files: two for each of the eight datasets in the paper (BIBLE,
BMS1, FIFA, KOSARAK, LEVIATHAN, SIGN, SYN, Ta-Feng) and two for the toy database in
`datasets/example/`. Only the toy database is kept in the repository, so the smoke test and
`analysis/verify_definitions.py` run right after a clone.

```bash
python scripts/fetch_datasets.py --tafeng-source <ta_feng_all_months_merged.csv>   # fetch, build Ta-Feng, verify
python scripts/fetch_datasets.py --verify-only                                      # verify files already present
```

The first command downloads 16 files from release `hausp-ub-v1-exact` of
[huspm-datasets](https://github.com/tran-minh-thai/huspm-datasets), builds Ta-Feng, and checks all
18 files against the manifest. A `MISSING` or `BAD` line means the copy on disk differs from the one
behind the results.

**Generated utilities.** For the seven datasets other than Ta-Feng, item quantities and unit
profits come from `SPMF_Converter.java` with seed 42. BIBLE, BMS1 and SYN are value-identical to
release `v1.1-seed42-lognormal` of huspm-datasets. FIFA, KOSARAK, LEVIATHAN and SIGN cannot be
regenerated with a per-file seed and are distributed as the exact bytes used.

**SYN.** The SPMF release of a database produced by the IBM Quest generator with parameters slen 8,
tlen 1, seq.patlen 5, lit.patlen 8 and nitems 5000. Its label in the CSVs (`C8T1S5I8N5K`) and on the
command line (`syn_c8t1s5i8n5k`) is built from those parameters and does not describe the data:
measured on the file, SYN has 2.36 itemsets per sequence, 7.97 items per itemset, and 68,240
distinct items with identifiers up to 4,999,999.

**Ta-Feng.** Its utilities are measured, not generated: a sequence is a customer, an itemset is
everything that customer bought on one day, the quantity is `AMOUNT`, and the profit of an item is
the median of its unit prices. The data is described in Hsu, Chung and Huang (Machine Learning 57,
2004). Ta-Feng is not redistributed here or in huspm-datasets, because the public copy carries no
licence from its rights holder. Download `ta_feng_all_months_merged.csv` (Version 1 of the Kaggle
dataset "Ta Feng Grocery Dataset", 63,642,758 bytes, SHA-256
`1d575e5d0b7207d7706d22ca56c7535886fff8175ca5537a310333a4ab7a7b67`) from
<https://www.kaggle.com/datasets/chiranjivdas09/ta-feng-grocery-dataset>; `build_tafeng.py` rebuilds
both files byte for byte. Besides SYN it is the only dataset with multi-item itemsets: 6.84 items
per itemset on average, and 85.1% of its 119,578 baskets hold more than one item.

### Data format

`<NAME>_seq.txt` holds one sequence per line. `itemID[quantity]` is an item with its quantity, `-1`
closes an itemset and `-2` closes the sequence:

```
1[2] -1 2[1] 3[2] -1 -2
4[5] 6[3] -1 -2
```

`<NAME>_eui.txt` gives the external utility (unit profit) of each item, as `itemID:profit`; a
comma or whitespace also works as the separator. The utility of an item occurrence is
quantity × profit.

## Running

### Measurement campaign

Timings are taken only on the machine declared in `MEASUREMENT_MACHINE.txt` (Windows, AMD Ryzen 9
9950X, 64 GB, JDK 25). It needs Git, a JDK, Maven and Python 3 on the PATH.

```
git pull --ff-only
python scripts/fetch_datasets.py --tafeng-source <ta_feng_all_months_merged.csv>
python scripts/campaign.py scripts/plans/validation.json
python scripts/campaign.py scripts/plans/full.json
```

- `validation.json` runs one command of each kind on its cheapest case, into
  `results-probe/windows-validation/`, and kills one command mid-run to test recovery. Run it first
  on any new machine. `full.json` is refused until `analysis/check_validation.py` passes on that run
  and the host name is entered in `MEASUREMENT_MACHINE.txt`.
- Before measuring, `campaign.py` checks that the tree is clean and pushed, the datasets match the
  manifest, the host is the declared one, and the jar is newer than the sources. Every command runs
  as `java -Xmx24g -XX:+UseG1GC -jar ... --timeout 90`, and the machine is kept awake.
- Each command is atomic. The size of every result file is recorded before it starts; an
  interrupted command is cut back to those sizes and run again, so starting the same plan again
  resumes it. To stop cleanly, create the stop file printed at start. When the plan finishes, the
  results are committed and pushed.
- Do not pull while a campaign is running. Every row records its commit, and a commit that changes
  `src/`, `pom.xml`, the driver, the plans or the manifest makes the driver stop.

The heap is 24 GB because from 32 GB on the JVM no longer compresses object references, which
inflates the memory of the object-heavy baselines more than that of HAUSP-UB
(`results-probe/oops-test`). Only one campaign may use the machine at a time.

### Development runs

```bash
./scripts/run.sh 1 --dataset example --results-dir results-probe/smoke   # smoke test on the toy data
mvn -q package
java -Xmx16g -jar build/incremental-hausp-mining-1.0.0.jar --exp all
```

On a machine that must not produce measurements, set `HAUSP_NO_MEASURE`; the launcher then accepts
only the toy dataset or a `results-probe*` directory.

### Launcher options

```
--exp 1,3                 campaign experiments (1..8; opt-in: 9 attribution, 10 pre-large margin sweep,
                          11 warm-start schedule); "all" runs 1..8
--dataset a,b             bible, bms1_spmf, fifa, kosarak, leviathan, sign, tafeng, syn_c8t1s5i8n5k, example
--algo A,B                arm labels exactly as in the CSVs
--repeats N               trials per configuration (default 3)
--repeats-min-seconds S   configurations whose first trial is shorter get 15 (<1 s), 10 (<10 s) or 5 (<120 s) trials
--k 10,100                batch counts for experiments 7 and 11
--results-dir DIR         output root; by default a new results-<run id>-<commit>/
--timeout MIN             per-batch time limit in minutes
--mem-mode live           force a collection before each heap sample (needs its own results directory)
--resume                  skip configurations already present in the CSV
--profile-phases          per-node phase timers; runtimes of such a run are not comparable across arms
--dump-config json        print every declared parameter and exit
--print-header            print the CSV header and exit
```

Each campaign experiment writes one CSV under `<results dir>/expN/`. A run never writes into a
directory that already holds results unless it is given one with `--results-dir`.

## Parameters

Each value below changes recorded numbers, so results compare only when taken under the same
values. The table is generated from the sources by `analysis/default_parameters.py`.

<!-- BEGIN default-parameters (generated by analysis/default_parameters.py) -->

| parameter | default | declared at | what it controls |
|---|---|---|---|
| `REPEATS` | `3` | `src/main/java/ExperimentConfig.java:53` | Trials per configuration; the CSV keeps every trial. `--repeats N`. |
| `REPEATS_MIN_SECONDS` | `0.0` | `src/main/java/ExperimentConfig.java:62` | 0 disables adaptive repeats. `--repeats-min-seconds S` raises the trial count for short configurations. |
| `RESULTS_DIR` | `results` | `src/main/java/ExperimentConfig.java:44` | Fallback only. The launcher, given no `--results-dir DIR`, opens `results-<run id>-<commit>` instead, so a run never writes into a tree that already holds results. |
| `TIMEOUT_OVERRIDE_MIN` | `0` | `src/main/java/ExperimentConfig.java:86` | 0 keeps each experiment's own limit. `--timeout MIN` overrides it for every batch. |
| `MEM_MODE_LIVE` | `false` | `src/main/java/ExperimentConfig.java:94` | false records the used heap; `--mem-mode live` forces a collection before each sample and needs a separate results directory. |
| `MU_PRELARGE` | `0.20` | `src/main/java/ExperimentConfig.java:260` | Pre-large ratio used by every experiment that does not sweep it. |
| `MU_SWEEP` | `0.05, 0.10, 0.20, 0.40` | `src/main/java/ExperimentConfig.java:263` | Pre-large ratios swept by the opt-in study. |
| `EXP3_DELTAS` | `0.05, 0.10, 0.15, 0.20` | `src/main/java/ExperimentConfig.java:266` | Increment sizes, as a fraction of the database. |
| `EXP7_BATCH_COUNTS` | `10, 20, 50, 100` | `src/main/java/ExperimentConfig.java:269` | Numbers of batches the database is split into. |
| `EXP11_BATCH_COUNTS` | `10, 20, 50, 100` | `src/main/java/ExperimentConfig.java:272` | Batch counts of the warm-start study. |
| `WARM_START_FIRST_RATIO` | `0.20` | `src/main/java/ExperimentConfig.java:275` | Share of the database in the first batch under the `warm20` schedule; the rest is split equally. |
| `MemorySampler.INTERVAL_MS` | `1000L` | `src/main/java/MemorySampler.java:31` | Sampling period of the peak-memory series. A longer period can miss a peak. |
| `RunIsolation.GC_DEADLINE_MS` | `2000` | `src/main/java/RunIsolation.java:41` | Time allowed for the heap to settle between arms, so one arm's garbage is not charged to the next. |
| `RunIsolation.TEARDOWN_WAIT_SEC` | `5` | `src/main/java/RunIsolation.java:40` | Time allowed for a timed-out arm to stop before the run is marked `OT`. |
| `HEAP` | `24g` | `scripts/run.sh:32` | JVM heap ceiling (`-Xmx`). Part of the identity of a measurement: numbers taken under different ceilings do not compare. |
| `ALGO_TIMEOUT_MIN` | `90` | `scripts/run.sh:33` | Per-batch time limit in minutes passed as `--timeout`. |
| `HEAP` (measurement campaign) | `24g` | `scripts/campaign.py:56` | Ceiling of every measurement, set by the campaign driver on the measurement machine. Below 32g so the JVM keeps compressing object references for every arm; a larger ceiling inflates the object-heavy baselines more than the proposed algorithm (`results-probe/oops-test`). A measurement under a different ceiling is not comparable, and B14 of `audit_results.py` refuses a tree that mixes them. |
| `TIMEOUT_MIN` (measurement campaign) | `90` | `scripts/campaign.py:57` | Per-batch time limit the campaign driver passes as `--timeout`; the limit the paper states. |
| garbage collector (measurement campaign) | `-XX:+UseG1GC` | `scripts/campaign.py:58` | Collector the campaign driver selects; it has to equal the development launcher's. |
| garbage collector | `-XX:+UseG1GC` | `scripts/run.sh:97` | Collector selected on the command line; it changes both timing and the memory series. |

Every per-experiment value (participating datasets, minimum-utility thresholds,
batch schedules, arm lists and per-experiment time limits) is printed in full by
`java -jar build/incremental-hausp-mining-1.0.0.jar --dump-config json`, which reads
the same declarations the runs read.

<!-- END default-parameters -->

To change datasets, thresholds or schedules, edit `src/main/java/ExperimentConfig.java`, where each
experiment lists its runs:

```java
DatasetRun.simple(BIBLE, 0.0005, MU_PRELARGE, FIVE_BATCH_20)                                      // one threshold
DatasetRun.withMinUtils(BIBLE, new double[]{0.001, 0.0009, 0.0008, 0.0007, 0.0006}, MU_PRELARGE)  // threshold sweep
DatasetRun.withThresholds(BIBLE, new double[]{0.0005, 0.0004, 0.00025}, MU_PRELARGE)              // listed thresholds
```

Then rebuild and rerun; no other file needs to change.

## Experiment numbers

Results and logs are named by the campaign experiment number (`results/expN`, `--exp N`), which
never changes. The paper numbers its experiments in the order it presents them.
`analysis/paper_experiment_numbers.json` maps one to the other, and the table generators read it.

<!-- BEGIN experiment-numbering (generated by analysis/experiment_numbering.py) -->

| Campaign (`results/expN`, `--exp N`) | Manuscript | Measures |
|---|---|---|
| 1 | Experiment 2 | Runtime and candidates on the five-batch schedule |
| 2 | Experiment 5 | Pruning-layer ablation over a minUtil sweep, single pass |
| 3 | Experiment 3 | Update cost versus batch size |
| 4 | Experiment 4 | Live heap on the five-batch schedule |
| 5 | Experiment 1 | Exactness against the re-mining oracle, single pass |
| 6 | Experiment 1 | Exactness against the re-mining oracle, five batches |
| 7 | Experiment 7 | Number of batches K, equal schedule |
| 8 | Experiment 8 | Sensitivity to low thresholds |
| 9 | Experiment 6 | Attribution of the runtime and memory gap |
| 10 | Experiment 3 (safety-margin sweep of Pre-HAUSPM, no number of its own) | Pre-HAUSPM update time across safety margins |
| 11 | Experiment 7 (warm-start schedule, no number of its own) | Number of batches K, warm-start schedule |

<!-- END experiment-numbering -->

## Output format

Every CSV starts with a provenance line (readers skip lines beginning with `#`), followed by the
header:

```
# run_id=20260904-0748 git=e0ec34f jvm=26.0.1 heap=24g host=<machine> tree=clean cmd=--exp 1 ...
Timestamp, Algorithm, Dataset, BatchID, RunIndex, MinUtil, mu, DeltaRatio, TotalDBUtil,
CumulativeDBSize, tScan(ms), tMining(ms), tTotal(ms), tLayer1(ms), tLayer2(ms), tLayer3(ms),
Cand, PrunedL1(SWU), PrunedL2(IAUUB), PrunedL3(MFUUB), TightnessPEAU, TightnessIAUUB,
TightnessMFUUB, HAUSP, SHAUS, MemPeak(MB), PoolBorrows, PoolReuses, PoolPeakLive, AudulActive,
Status, Recursed, ArmOrder, Schedule, PoolBytes, FlatBytes, EucsBytes, AudulRootBytes,
RescanTriggered, BufferUtil, BufferTested, SafetyBound, PrunedL3Node, PrunedL1Root, MemMode,
MemLive(MB), MemRetained(MB), GcForced, RunID
```

- `Cand` counts the utility lists assembled, the same way for every algorithm; `Recursed` counts
  the children recursed into.
- `tLayer1/2/3(ms)` and the `Pool*` columns are filled by HAUSP-UB and its variants only.
- `RunIndex` numbers the trials from 0; `ArmOrder` is the position of the arm in the run;
  `Schedule` is `equal` or `warm20` (experiments 7 and 11).
- `RescanTriggered`, `BufferUtil`, `BufferTested` and `SafetyBound` describe the pre-large buffer.
- Cells an algorithm does not measure are empty.

Campaign experiment 6 writes a narrower file with one agreement count per batch. `CSVLogger`
refuses to append rows under a different header.

## Rebuilding the tables and figures

The CSVs behind every number in the paper are committed under `results/`.

```bash
python3 -m pip install -r analysis/requirements.txt
python3 analysis/dataset_stats.py         # dataset characteristics, measured from the files
python3 analysis/build_latex_tables.py    # every numeric table of the paper
python3 analysis/build_report.py          # summary tables and figures
python3 analysis/wilcoxon_tests.py        # paired Wilcoxon tests
python3 analysis/identifier_space.py      # memory of the identifier-indexed structures
python3 analysis/export_quantities.py     # every quantity the paper quotes, as data
```

Everything is written to `analysis_out/paper/` and is deterministic. Each generated table begins
with a `% source:` line naming its CSV files and run identifiers. Figures are named after the
campaign experiment that draws them (`exp3_time_vs_delta.pdf`); the paper assigns its own figure
numbers. `export_quantities.py` writes `analysis_out/paper/quantities.json`, which holds numbers
only, with no wording. A quantity that cannot be computed appears as `null` under `_missing` with
the reason, and `_stamp` records the commit and whether the tree was clean.

## Checks

Each check exits non-zero on failure and prints how many cases it compared.

```bash
python3 analysis/check_measurement_machine.py  # every timing comes from the declared machine
python3 analysis/check_validation.py results-probe/windows-validation \
    --reference results-probe/mac-validation  # a new machine's validation run, before it measures
python3 analysis/verify_definitions.py        # the miner against the paper's definitions, on boundary cases
python3 analysis/verify_worked_example.py     # the miner on the worked example (analysis/worked_example.json)
python3 analysis/verify_pattern_sets.py       # arms return the same pattern sets, not only the same counts
python3 analysis/verify_tables.py             # published cells recomputed from the CSVs without common.py
python3 analysis/audit_results.py             # consistency of the collected CSVs
python3 analysis/check_coverage.py            # every declared (experiment, dataset) has rows
python3 analysis/stale_cells.py               # every printed cell comes from the current code
python3 analysis/check_arms.py                # each experiment runs the arms the paper reports
python3 analysis/check_provenance.py          # every recorded commit can still be found
python3 analysis/check_language.py            # English only, no manuscript numbering in the code
python3 analysis/default_parameters.py --check   # the parameter table above matches the sources
python3 analysis/experiment_numbering.py --check # the experiment-number table above matches its source
```

`check_inputs.py` also exists. It compares the generated tables with the manuscript, which is not
in this repository, so from a clone it exits non-zero by design.

`verify_tables.py` reads the CSVs with its own code, because all other checks and the table
generators share the loaders in `common.py`, and a fault there would move tables and checks
together.

`verify_definitions.py` mines small databases built around boundary cases and compares the result,
as sets, with an exhaustive reference written from the paper's definitions.

Each result file records the commit it ran from. `provenance_map.json` maps every recorded commit
to the current commit that carries the same code, `COMMIT_MAP.tsv` lists old and new identifiers,
and `check_provenance.py --table` prints the mapping.

## Citation

If you use this code or the parameters in `ExperimentConfig.java`, please cite the HAUSP-UB paper.

## License

MIT; see [LICENSE](LICENSE).
