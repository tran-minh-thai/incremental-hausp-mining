#!/usr/bin/env python3
"""Every result file must name a commit that can still be found.

Each result file opens with a provenance line naming the commit the run was started from.
That identifier is the only thing tying a recorded number to the code that produced it, and
it is fragile in a way nothing else notices: rewriting history changes every identifier
after the point it touches, while the files keep quoting the old ones. The compiled tables
stay correct, the CSVs stay correct, and the link between them quietly stops resolving.

This check reads the provenance line of every result file and resolves its commit, directly
or through `provenance_map.json`, which records what the rewrites of 2026-09-17 did. It
prints how many files it read, because "no broken reference" over zero files is not a pass.

    python3 analysis/check_provenance.py          # exit 1 if any stamp cannot be resolved
    python3 analysis/check_provenance.py --table  # print the mapping as a table
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "provenance_map.json"
STAMP = re.compile(r"\bgit=([0-9a-f]{7,40})")
def _trees() -> tuple[str, ...]:
    """Every directory that can hold a stamped artifact, found by looking.

    A tuple of names was here, and it stopped at the fifth result generation
    while eleven existed: the six it did not name held every artifact of the
    newest database, so the count this check prints -- the thing that makes it
    more than a shrug -- was a true statement over the wrong denominator.
    """
    names = [d.name for d in sorted(ROOT.iterdir())
             if d.is_dir() and d.name.startswith("results")]
    return tuple(names + ["analysis_out"])


TREES = _trees()


def exists(commit: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", commit + "^{commit}"],
                          capture_output=True).returncode == 0


def stamps() -> dict[str, list[str]]:
    """Commit identifier -> the result files quoting it."""
    found: dict[str, list[str]] = {}
    for tree in TREES:
        base = ROOT / tree
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.csv")):
            try:
                head = p.open(encoding="utf-8", errors="ignore").readline()
            except OSError:
                continue
            m = STAMP.search(head)
            if m:
                found.setdefault(m.group(1), []).append(str(p.relative_to(ROOT)))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", action="store_true", help="print the mapping and exit 0")
    a = ap.parse_args()

    mapping = json.loads(MAP.read_text())["commits"] if MAP.exists() else {}
    found = stamps()
    files = sum(len(v) for v in found.values())

    direct, mapped, broken = [], [], []
    for stamp in sorted(found):
        if exists(stamp):
            direct.append(stamp)
        elif stamp in mapping and exists(mapping[stamp]["current"]):
            mapped.append(stamp)
        else:
            broken.append(stamp)

    if a.table:
        print("%-9s %-9s %-6s %s" % ("recorded", "now", "files", "subject"))
        for stamp in sorted(found):
            cur = stamp if stamp in direct else mapping.get(stamp, {}).get("current", "-")
            subj = mapping.get(stamp, {}).get("subject", "")
            if stamp in direct and not subj:
                subj = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%s", stamp],
                                      capture_output=True, text=True).stdout.strip()
            print("%-9s %-9s %-6d %s" % (stamp, cur[:9], len(found[stamp]), subj[:72]))
        return 0

    print("check_provenance: %d result files carry a provenance line, naming %d commits"
          % (files, len(found)))
    print("  %d resolve directly, %d through provenance_map.json, %d broken"
          % (len(direct), len(mapped), len(broken)))
    if not found:
        print("check_provenance: FAIL -- no result file carries a provenance line at all")
        return 1
    if broken:
        for stamp in broken:
            print("  BROKEN  %s is quoted by %d file(s) and names no commit in this history; "
                  "add it to provenance_map.json" % (stamp, len(found[stamp])))
            for f in found[stamp][:3]:
                print("            %s" % f)
        print("check_provenance: FAIL -- %d unresolvable identifier(s)" % len(broken))
        return 1
    print("check_provenance: PASS -- every recorded commit can still be found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
