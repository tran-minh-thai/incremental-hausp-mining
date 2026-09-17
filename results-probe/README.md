# results-probe: feasibility and verification runs, never a source for the paper

This tree holds runs whose purpose is to decide something or to check something, not to measure
anything. It is versioned because the checks it records are evidence: several of them are cited in
`EXPERIMENT_CHANGELOG.md`, and a claim backed by a run that only ever existed on one machine is a
claim nobody else can audit.

**No number in the manuscript may come from here.** Two things enforce that rather than asking
anyone to remember it:

- `analysis/common.py` names every source tree explicitly and never globs `results*`, so nothing
  under this directory can be swept into a table by accident.
- When `HAUSP_NEWER_RESULTS` or `HAUSP_NEWEST_RESULTS` deliberately points a generation at a probe
  directory, which is how `simulate_gen3.py` and `simulate_gen4.py` rehearse the analysis pipeline,
  `common.py` prints a REHEARSAL line to stderr naming the tree.

Be aware of one thing when reading these files: they carry the **same wide schema as a real run**,
timing and memory columns included, because the same launcher writes them. A number in a column
here is not a measurement. The session guard in `ExperimentLauncher` permits a restricted environment to
write into this tree precisely because nothing written here can become a result.

## What is currently here

| directory | what it records |
|---|---|
| `definitions/` | the boundary-case suite of `analysis/verify_definitions.py`: ten small databases mined by the real implementation and compared, as pattern sets, with a reference built from the definitions |
| `itemset-order/` | four runs on the toy dataset showing that the parser's sort is what makes the search independent of the input item order, including the two with the sort removed |
| `prefix-check/` | the counts pre-flight the runbook's memory steps run before spending hours: every deterministic counter compared with the recorded artifacts |
| `exp*/`, `mem/`, `breakdown*/`, `fix2/`, `schema/` | earlier feasibility runs kept for the trail |
