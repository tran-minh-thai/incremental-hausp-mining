# Note on the last line of evaluation.txt

The line "significance: ... -> NOT SIGNIFICANT" applies the six-dataset rule registered for the first run
(at least 3 of 6 datasets) to this one-dataset run, where it has no meaning. The rule registered for this run
(EXPERIMENT_CHANGELOG, 2026-10-17, "H-nhóm") is: at least 1% of the patterns in the union of the HAUSP sets
change status over x0.8..x1.2, at one of the two thresholds 0.0025 and 0.0012. Read the line above it for
the count. The tool was corrected right after this run; it now prints no verdict for a run outside the
registered setting. evaluation.txt is left as the run wrote it.

The `command` field of run_meta.json was edited after the run: the path of the source file was
replaced by its file name, so that no machine path is published. The file is identified by
`source_sha256` in the same record.
