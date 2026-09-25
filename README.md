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
│   ├── campaign.py               The one entry point for measurements (standard-library Python,
│   │                             Windows or macOS): runs a plan, one JVM per command, atomically.
│   ├── plans/                    Generated plans (full.json, validation.json) and the cost table
│   │                             that orders them; written by analysis/campaign_plan.py.
│   ├── fetch_datasets.py         Download the datasets and verify them against datasets/MANIFEST.sha256.
│   ├── build_tafeng.py           Rebuild Ta-Feng from the public transaction log.
│   └── run.sh                    Development launcher (macOS / Linux) for probes and smoke tests.
├── analysis/                     Python scripts that rebuild every table and figure, and the
│                                 checks that can fail (see "Checks that can fail").
├── analysis_out/                 Derived tables and figures (regenerable).
├── results/                      The measurement campaign on the declared machine, written by
│                                 scripts/campaign.py (live-heap runs under results/mem/).
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

## The measurement machine

Every runtime and peak-heap figure in the paper comes from one machine, declared in
`MEASUREMENT_MACHINE.txt` at the repository root: its host name, CPU, memory, operating system,
JVM and the heap ceiling. Since 2026-09-25 that is a Windows machine (AMD Ryzen 9 9950X, 64 GB,
JDK 25); every result measured earlier, on the development machine, was removed from the working
tree that day and survives only in the git history. `scripts/campaign.py` refuses to run a
measurement plan on any other host. The file is a declaration by the author, not a
detection — nothing reads the current hostname and writes it down, because a wrong guess would
quietly license timings from a machine that was never meant to produce them. Its host line stays
empty until the validation plan has run on that machine, and is then copied from the provenance
line of that run, so the declaration and the JVM name the machine the same way.

The heap ceiling is 24g, below the 32g at which the JVM stops compressing object references: a larger ceiling would have widened every memory gap in the proposed algorithm's favour through the JVM alone (Experiment 4 on Ta-Feng, references uncompressed: persistent-tree baseline 1.27x, re-mining 1.16x, proposed algorithm 1.09x; results-probe/oops-test). The machine runs one campaign at a time, of this
project or any other: two campaigns sharing it would slow each other and neither result would
show it.

`analysis/check_measurement_machine.py` is what gives the file force: it refuses any artifact
under `results*/` whose provenance line names a different host. Counts are exempt by design — they are fixed
by commit, data and seed, which is why `results-probe*/` and `results-invariant/` are not checked.

## Requirements

| Component | Minimum | Tested |
|-----------|---------|--------|
| JDK       | 11      | 25 (measurement machine), 26 (development) |
| Maven     | 3.6     | 3.9    |
| RAM       | 32 GB for the 24g heap | 64 GB (measurement machine) |
| Python    | 3.9     | 3.9.6 (development) |

On the measurement machine Python runs only `scripts/campaign.py` and `scripts/fetch_datasets.py`,
which use the standard library alone; the scripts under `analysis/` need the packages below. Pin the versions with
`python3 -m pip install -r analysis/requirements.txt`, in a virtual environment
if the interpreter is managed by the system.

The only third-party dependency is `it.unimi.dsi:fastutil:8.5.12`, retrieved
automatically from Maven Central.

## Build and run

### Measurement campaign (the declared machine)

Needs Git, a JDK, Maven and Python 3 (standard library only) on the PATH. From a clone:

```
git pull --ff-only
python scripts/fetch_datasets.py
python scripts/campaign.py scripts/plans/validation.json
python scripts/campaign.py scripts/plans/full.json
```

`validation.json` runs one command of every shape the full plan uses, on its cheapest cell, into
`results-probe/windows-validation/`, and kills one of them mid-run to test the rollback on that
machine; run it first on any new machine. `full.json` measures everything the paper prints, and
is refused until the machine's host is declared: after the validation run,
`analysis/check_validation.py results-probe/windows-validation --reference results-probe/mac-validation`
must pass (one host and JVM, heap 24g, a clean tree, `--timeout 90`, a self-test kill that hit
written rows, and every count equal to the development machine's run of the same plan), and only
then is the host copied into `MEASUREMENT_MACHINE.txt`.

Do not pull while a campaign is unfinished: every row records the commit it ran from. Commits that
leave the measured paths alone (`src/`, `pom.xml`, the driver, the plans, the dataset manifest)
may reach origin meanwhile and do not stop it; one that changes them makes the driver refuse to
continue.

`campaign.py` checks, before measuring anything, that the tracked tree is clean and on origin,
that every dataset matches `datasets/MANIFEST.sha256`, that this host is the declared one, and
that the JAR is newer than every source (it rebuilds with Maven otherwise). It runs every command
as `java -Xmx24g -XX:+UseG1GC -jar ... --timeout 90`, keeps the machine awake for the whole
campaign, and makes every command atomic: the size of every result file is written to a ledger
before the command starts, and a command that did not finish -- a stop, a crash, a power cut, a
forced restart -- is cut back to those sizes on the next start and run again. Starting the same
command again therefore continues a campaign after any interruption. To stop cleanly, create the
stop file it prints at start; the running command finishes first. When the plan is done it
commits the results and pushes them.

### Development runs (any machine)

```bash
./scripts/run.sh 1 --dataset example --results-dir results-probe/smoke   # smoke test on the toy data
```

A machine that must not produce measurements sets `HAUSP_NO_MEASURE`; the launcher then accepts
only the toy dataset or a `results-probe*` directory.

### Launcher options

```
--exp 1,3            experiments to run (1..8; opt-in studies: 9 attribution, 10 pre-large mu sweep, 11 warm-start K)
--repeats N          trials per configuration (default 3)
--repeats-min-seconds S   configurations whose first trial takes under S seconds get 15 (<1 s), 10 (<10 s) or 5 (<120 s) trials
--dataset a,b        dataset short names (bible, bms1_spmf, fifa, kosarak, leviathan, sign, syn_c8t1s5i8n5k, example)
--algo A,B           arm names exactly as written in the CSV (e.g. HAUSP-UB-L1,HAUSP-UB)
--k 10,100           batch counts for Experiments 7 and 11
--results-dir DIR    root of the result CSVs; without it, results-<run id>-<commit>
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

Each experiment writes a single CSV under `<results dir>/expN/`. Given no
`--results-dir`, a run opens `results-<run id>-<commit>/` of its own rather than
writing into a tree that already holds results -- see "Result trees are never
overwritten".

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
| `HEAP` (measurement campaign) | `24g` | `scripts/campaign.py:56` | Ceiling of every measurement, set by the campaign driver on the measurement machine. Below 32g so the JVM keeps compressing object references for every arm; a larger ceiling inflated the object-heavy baselines more than the proposed algorithm (`results-probe/oops-test`). A measurement under a different ceiling is not comparable, and B14 of `audit_results.py` refuses a tree that mixes them. |
| `TIMEOUT_MIN` (measurement campaign) | `90` | `scripts/campaign.py:57` | Per-batch time limit the campaign driver passes as `--timeout`; the limit the paper states. |
| garbage collector (measurement campaign) | `-XX:+UseG1GC` | `scripts/campaign.py:58` | Collector the campaign driver selects; it has to equal the development launcher's. |
| garbage collector | `-XX:+UseG1GC` | `scripts/run.sh:97` | Collector selected on the command line; it changes both timing and the memory series. |

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

Ta-Feng is the exception and the only database here whose utilities are
**measured** rather than generated: a sequence is a customer, an itemset is one
shopping trip, and an item's profit is its unit price read from the source
transaction log, so nothing about it is seeded. Rebuild it with

```bash
python3 scripts/build_tafeng.py --source <ta_feng_all_months_merged.csv>
```

which reproduces both of its files byte for byte. It is also the only database
here besides the synthetic one on which an I-extension is legal: 6.84 items per
itemset, and 85.1% of its 119,578 baskets hold more than one item.

The measured databases are **not tracked here**. They live in that shared
repository under their own tag, `hausp-ub-v1-exact`, which exists because the
repository's other releases carry different conversions of the same source
sequences: only two of the eighteen files match them byte for byte. Fetch them
before the first run:

```bash
python scripts/fetch_datasets.py                # download + verify into datasets/
python scripts/fetch_datasets.py --verify-only  # check files already present
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

Until 2026-09-25 the results came in several generations -- a legacy campaign whose schema
stopped at `Status`, and later re-runs layered over it arm by arm -- and `analysis/common.py`
merged them. All of them were removed that day for one campaign on the declared machine, in the
schema above, written into `results/`. The merge code has not been retired yet; until it is,
`analysis/common.py` refuses to read that campaign rather than apply the legacy rules to it (one
of them drops every `HAUSP-UB-L1` row).

The `tLayer1/2/3(ms)` and pool columns are populated by HAUSP-UB and its
ablation variants only; the baselines log zero. `RunIndex` is zero-based and
identifies the trial within the `--repeats N` sweep. `PoolBorrows` / `PoolReuses`
quantify how many AU-DUL allocations were avoided by the shared pool;
`AudulActive` is the number of accumulated 1-itemset AU-DULs that remain live
at the end of the batch, and is the metric used in Experiment 7 to verify that
the long-run memory stays bounded.

Experiment 6 uses a narrower schema dedicated to multi-batch agreement counts.

### Result trees are never overwritten

The tables are built from one campaign, in `results/`. `scripts/campaign.py` writes it one
command at a time and never edits what an earlier command wrote: the ledger beside the results
records the size of every file before each command, and an interrupted command is cut back to
exactly that and run again. A command therefore either leaves its complete rows or none, and the
readers in `analysis/common.py` refuse a memory row written twice or a row in an older schema
instead of choosing one quietly.

Two further rules worth stating, because both were paid for:

* A run may not append rows under a different column header; `CSVLogger` refuses,
  rather than leaving one file with two schemas in it.
* When a version of the manuscript is submitted, the trees it was built from are
  copied once into `results-submitted-<yyyymmdd>-<commit>/` and nothing writes there
  again. Later campaigns keep opening their own generations beside it.

## Reproducing the paper's analysis

The measurement CSVs behind every number in the paper are committed under
`results/`. The scripts in `analysis/` rebuild all derived artifacts from
them:

Run these with the interpreter that has the dependencies, and check first -- on macOS a
Homebrew `python3` takes precedence on the PATH and fails at import with
`No module named 'pandas'`, while the system interpreter at `/usr/bin/python3` (3.9) is
the one carrying them:

```bash
python3 -c "import pandas, numpy, scipy, matplotlib, tabulate" || echo "try /usr/bin/python3"
```

```bash
python3 -m pip install -r analysis/requirements.txt
python3 analysis/dataset_stats.py              # dataset characteristics measured from the files
python3 analysis/build_latex_tables.py         # every numeric table of the manuscript (+ copy to ../paper/tables)
python3 analysis/check_inputs.py               # every generated table is \input by the manuscript (needs it)
python3 analysis/build_report.py               # Markdown summary tables + figure PDFs (see below)
python3 analysis/audit_results.py              # consistency checks over the collected CSVs
python3 analysis/wilcoxon_tests.py             # paired Wilcoxon significance tests
python3 analysis/identifier_space.py           # what the identifier-indexed structures cost per dataset
python3 analysis/default_parameters.py         # rewrite the default-parameter table of this README
python3 analysis/export_quantities.py          # every measured quantity, as data (the handover file)
```

### Where this repository stops

Figures are written to `analysis_out/paper/figures/` under names that say which experiment
drew them -- `exp3_time_vs_delta.pdf`, not `Figure5_...`. Which of them a manuscript
prints, and what number each one gets, follows the order the images appear in that
manuscript, so it is decided there. This used to be decided here, and the numbers went
stale the moment a figure stopped being cited: LaTeX numbers its own captions and never
reads a file name, so the mismatch was invisible in the compiled PDF and would have
surfaced only in the package sent to a journal.


`export_quantities.py` writes `analysis_out/paper/quantities.json`, and that file is the
whole of the handover to whoever writes the prose. It holds numbers, series and counts
under names taken from the experiments and the CSV columns -- no wording, no markup, no
display names. Turning one of them into a sentence, choosing which end of a range to
quote and in which direction, is not done here.

Two rules keep the line where it is:

* **Aggregation over trials belongs here**, because it is a statement about the
  measurement: a mean over repeats, a max over samples of a memory series.
* **Reduction across datasets does not**, because which end of a range gets said, and in
  which direction, is a property of the sentence. Series come out per dataset, under the
  names the CSVs use, and the reader reduces them.

A quantity that cannot be computed is written as `null` under `_missing` with the reason
beside it, never left out: a key that is absent looks the same as a key nobody wanted.
`_stamp` records when the export ran, from which commit, and whether the tree was clean,
because a stale data file is read just as quietly as a current one and nothing downstream
can tell the difference. `--check` exits non-zero when the file no longer matches the
artifacts; it ignores `_stamp`, which differs from itself on every run.

This is also why nothing under `analysis/` has to be filtered before publication.

### Checks that can fail

These are not summaries; each was shown to reject an injected fault, and each prints the denominator
of what it compared.

```bash
python3 analysis/check_measurement_machine.py   # every timing artifact names the declared machine
python3 analysis/verify_definitions.py         # the miner against the paper's definitions, on boundary cases
python3 analysis/check_validation.py results-probe/windows-validation \
    --reference results-probe/mac-validation   # a new machine's validation run, before it measures
python3 analysis/stale_cells.py                # every printed cell measured by the current code
python3 analysis/default_parameters.py --check # the README table still matches the sources
python3 analysis/check_language.py             # no non-English text, no manuscript numbering
python3 analysis/check_provenance.py           # every recorded commit can still be found
python3 analysis/check_arms.py                 # no experiment runs the EUCS-carrying arm by default
python3 analysis/verify_tables.py              # published cells recomputed from the CSVs, without common.py
```

One of these needs something this repository does not contain. `check_inputs.py` compares
the generated tables with the manuscript that reads them, and the manuscript is kept out of
here on purpose, so from a bare clone it says so and exits non-zero -- a check that cannot
run must not report success. Every other check runs from a clone with nothing else present.

`verify_tables.py` exists because every other check here reads its data through
`common.load_experiment`, and so does every table generator: a fault on that shared path
would move the tables and the checks together and they would agree all the way down. It
opens the CSV files itself, applies the same rules in its own code, and compares cell by
cell, printing how many it compared. It was shown to catch a value edited in a published table.

`check_provenance.py` guards the one link nothing else notices when it breaks. Every result
file opens with the commit its run was started from, and that identifier is what ties a
number to the code behind it. The history was rewritten twice on 2026-09-17 -- once to take
an environment variable name out of the commits that mentioned it, once to drop the measured
databases, which are now fetched from a release instead. Both preserved every commit and
their order, and neither altered a file any run had read, but both changed the identifiers.
`provenance_map.json` records what moved where, and the check refuses when a recorded commit
resolves to nothing. Run `--table` to see the mapping.

`verify_definitions.py` builds ten small databases, each aimed at one boundary the description could
get wrong, mines each with the real implementation and compares the reported patterns, as sets, with
an exhaustive reference that applies the average-utility and HAUSP definitions and reuses nothing of
the algorithm. These databases exist to test the code; the manuscript illustrates its definitions
with one example database only.

`check_validation.py` is the gate between a new machine and the paper: every command of the
validation plan done, a self-test kill that hit written rows, one host and JVM, the stated heap
and time limit, a clean tree, and every deterministic column equal to another machine's run of
the same plan. It was shown to pass a run compared with its own copy, to catch one altered
pattern count, and to fail rather than pass against an empty reference.

Everything is written to `analysis_out/paper/` and is deterministic: the same
CSVs yield the same tables, figures, and p-values. Every generated `.tex`
table starts with a `% source:` line naming the CSV files and run ids behind
it, and the best value of each comparison group is set in bold by the
generator.

## Citation

If you use this code or the parameters declared in `ExperimentConfig.java` in
your research, please cite the HAUSP-UB paper.

## License

Released under the MIT License; see [LICENSE](LICENSE) for the full text.
