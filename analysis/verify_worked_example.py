#!/usr/bin/env python3
"""Mine the worked example with the real implementation and compare it with the stated values.

analysis/worked_example.json holds a small database split into two batches, a threshold, and
values the presented arm must reproduce on it. This script writes the database to a scratch
directory, mines it batch by batch with WorkedExampleProbe (the presented arm, driven through the
same public calls the runners use), and checks:

  * after every batch, the reported pattern set equals the one an exhaustive enumeration from the
    definitions gives (the reference of analysis/verify_definitions.py, which shares no code with
    the miner);
  * after the last batch, the pattern set equals the expected one;
  * the accumulated total utility equals the expected value and the sum of quantity x profit;
  * for every listed root, evalIutil equals the expected value and the sum, over sequences, of
    the largest utility of an occurrence of that item; evalMFUUB equals the expected value.

The Layer-2 estimates of the child candidates are not checked here: they live in local arrays of
the timed search loop, and this project does not instrument that loop.

    python3 analysis/verify_worked_example.py      # exit 1 on any disagreement

Needs the jar (mvn -q package) and a JDK on the PATH.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "analysis" / "worked_example.json"
JAR = next(iter(sorted(ROOT.glob("build/incremental-hausp-mining-*.jar"))), None)

_spec = importlib.util.spec_from_file_location("verify_definitions", ROOT / "analysis" / "verify_definitions.py")
_vd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vd)


def parse_sequence(text: str, ids: dict) -> list:
    """'a[2] | b[1] c[2]' -> [[(1, 2)], [(2, 1), (3, 2)]]"""
    out = []
    for itemset in text.split("|"):
        pairs = []
        for tok in itemset.split():
            letter, qty = tok.rstrip("]").split("[")
            pairs.append((ids[letter], int(qty)))
        out.append(sorted(pairs))
    return out


def parse_pattern(text: str, ids: dict) -> str:
    """'m | v' -> '<(7)(8)>', the form the miner writes."""
    return _vd.render([[ids[x] for x in itemset.split()] for itemset in text.split("|")])


def main() -> int:
    if JAR is None:
        print("verify_worked_example: no jar under build/; run mvn -q package")
        return 1
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ids = fx["items"]
    profits = {ids[k]: v for k, v in fx["profits"].items()}
    seqs = [parse_sequence(s, ids) for s in fx["sequences"]]
    sizes = fx["batch_sizes"]
    min_util = fx["min_util"]
    exp = fx["expected"]
    roots = exp.get("roots_after_last_batch", {})

    with tempfile.TemporaryDirectory(prefix="worked-example-") as tmp:
        t = Path(tmp)
        (t / "worked_example_eui.txt").write_text("".join(f"{i}:{p}\n" for i, p in sorted(profits.items())))
        (t / "worked_example_seq.txt").write_text("\n".join(
            " ".join([tok for its in s for tok in [f"{i}[{q}]" for i, q in its] + ["-1"]] + ["-2"]) for s in seqs) + "\n")
        (t / "probe.properties").write_text("dataset.path=worked_example_seq.txt\n")
        r = subprocess.run(["java", "-cp", str(JAR), "WorkedExampleProbe",
                            "worked_example_eui.txt", "worked_example_seq.txt", "probe.properties",
                            repr(min_util), ",".join(map(str, sizes)), ",".join(str(ids[k]) for k in roots)],
                           cwd=t, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            print("verify_worked_example: the probe failed, exit %d\n%s" % (r.returncode, (r.stdout + r.stderr)[-800:]))
            return 1
        batches, root_vals = {}, {}
        for ln in r.stdout.splitlines():
            f = ln.split()
            if f[:1] == ["batch"]:
                batches[int(f[1])] = {"total": int(f[3]), "hausp": int(f[5])}
            elif f[:1] == ["root"] and len(f) == 6:
                root_vals[int(f[1])] = {"evalIutil": int(f[3]), "evalMFUUB": int(f[5])}
        mined = {}
        for b in range(len(sizes)):
            files = list((t / "out").glob(f"HAUSP_worked_example_B{b}_mu*.txt"))
            mined[b] = ({ln.split("\t")[0] for ln in files[0].read_text().splitlines() if ln.strip()}
                        if len(files) == 1 else None)

    bad, checked = [], 0

    def check(label, got, want):
        nonlocal checked
        checked += 1
        status = "ok " if got == want else "BAD"
        print(f"  {status} {label}: got {got!r}, want {want!r}")
        if got != want:
            bad.append(label)

    end = 0
    for b, size in enumerate(sizes):
        end += size
        prefix = seqs[:end]
        want = _vd.reference_hausp(profits, prefix, min_util)
        check(f"batch {b}: pattern set equals the enumeration from the definitions ({end} sequences)",
              sorted(mined[b]) if mined[b] is not None else None, sorted(want))
        check(f"batch {b}: pattern count in the run result", batches.get(b, {}).get("hausp"), len(want))
    last = len(sizes) - 1
    check("last batch: pattern set equals the expected set",
          sorted(mined[last]) if mined[last] is not None else None,
          sorted(parse_pattern(p, ids) for p in exp["hausp_after_last_batch"]))
    total = sum(q * profits[i] for s in seqs for its in s for i, q in its)
    check("last batch: total utility equals the expected value", batches.get(last, {}).get("total"), exp["total_utility"])
    check("last batch: total utility equals the sum of quantity x profit", batches.get(last, {}).get("total"), total)
    for letter, want_vals in roots.items():
        iid = ids[letter]
        got = root_vals.get(iid, {})
        by_definition = sum(max(us) for s in seqs
                            for us in [_vd.occurrence_utilities([[iid]], [{i: q * profits[i] for i, q in its} for its in s])]
                            if us)
        check(f"root {letter}: evalIutil equals the expected value", got.get("evalIutil"), want_vals["evalIutil"])
        check(f"root {letter}: evalIutil equals the definition", got.get("evalIutil"), by_definition)
        check(f"root {letter}: evalMFUUB equals the expected value", got.get("evalMFUUB"), want_vals["evalMFUUB"])

    print("verify_worked_example: %d comparisons; the Layer-2 estimates of the child candidates are not "
          "checked here (they live in the timed search loop)" % checked)
    if bad:
        print("verify_worked_example: FAIL -- %d of %d disagree" % (len(bad), checked))
        return 1
    print("verify_worked_example: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
