# Provenance note for run 20261017-062704

This run was made with `analysis/probe_price_grid.py` before the tool was committed. The `commit` field of
`run_meta.json` (b4f9c2c) describes the miner (the JAR was built from it and its hash is recorded), not the
probe tool, and `tree = clean` did not see the untracked tool.

The tool was committed right after this run, with three additions that do not touch any count:

- a `--mu DATASET=VALUE` option that overrides a threshold and is recorded in `run_meta.json`;
- a refusal to run while the tool itself is not tracked by git;
- a `tool_sha256` field in `run_meta.json`.

`git diff` between the committed tool and the version that ran has only these lines. The diff was made by
removing the three additions from the committed file. The functions that produce every number in this run
are identical in both versions: `shape`, `random_db`, `linear_occurrences`, `statuses`, `definition_check`,
`item_totals`, `mine` and `evaluate`. The diff does not prove that the reconstruction is identical byte for
byte to the file that ran, because no hash of that file was recorded.
