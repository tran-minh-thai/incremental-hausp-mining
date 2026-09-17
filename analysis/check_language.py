#!/usr/bin/env python3
"""Refuse two things the repository must not carry, and say how many files were read.

1. Text in the working language of the notes rather than in English. Source files are
   published; notes are not, and translating at publication time is a bulk edit at the
   busiest moment, with the highest risk of changing meaning.
2. Numbering that belongs to the manuscript -- "Section 3.2", "Theorem 7" -- which dies
   the moment the manuscript renumbers, and drags paper content into the code.
   References to a *published* algorithm are fine and are named as such in prose; what
   this rejects is a bare number pointing at our own draft.

    python3 analysis/check_language.py          # exit 1 on any finding

The alphabet is built from Unicode decompositions rather than written out, so this file
does not contain the characters it looks for.
"""
import re
import subprocess
import sys
import unicodedata

# Latin letters carrying the marks used by the working language, derived rather than typed.
_EXTRA = {"ă", "â", "đ", "ê", "ô", "ơ", "ư",
          "Ă", "Â", "Đ", "Ê", "Ô", "Ơ", "Ư"}
_VOWELS = set("aeiouyAEIOUY")


def _is_marked(ch: str) -> bool:
    if ch in _EXTRA:
        return True
    if ord(ch) < 128:
        return False
    d = unicodedata.decomposition(ch)
    if not d:
        return False
    try:
        base = chr(int(d.split()[0], 16))
    except ValueError:
        return False
    return base in _VOWELS or base in _EXTRA or unicodedata.decomposition(base) != ""


# "Theorem 4", "Section 3.2", "Eq. (7)", the paper's own numbering.
_PAPER_NUM = re.compile(
    r"\b(Theorem|Lemma|Definition|Proposition|Corollary|Section|Subsection)\s*\.?\s*\d"
    r"|§\s*\d")

# Paths whose content is data, not prose: measured artifacts and the manifest.
_SKIP_PREFIX = ("results/", "results-2026-09", "results-probe/", "results-invariant/",
                "datasets/")


def main() -> int:
    files = [f for f in subprocess.run(["git", "ls-files", "-z"], capture_output=True,
                                       check=True).stdout.decode().split("\0") if f]
    read = 0
    findings = []
    for path in files:
        if path.startswith(_SKIP_PREFIX):
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        read += 1
        for n, line in enumerate(text.splitlines(), 1):
            marked = sorted({c for c in line if _is_marked(c)})
            if marked:
                findings.append((path, n, "non-English text (%s)" % "".join(marked), line.strip()[:100]))
            m = _PAPER_NUM.search(line)
            if m:
                findings.append((path, n, "manuscript numbering (%r)" % m.group(0), line.strip()[:100]))

    print("check_language: read %d of %d tracked files (artifacts and binaries skipped)"
          % (read, len(files)))
    if not findings:
        print("check_language: PASS -- 0 findings")
        return 0
    print("check_language: FAIL -- %d finding(s)" % len(findings))
    for path, n, what, line in findings:
        print("  %s:%d  %s\n      %s" % (path, n, what, line))
    return 1


if __name__ == "__main__":
    sys.exit(main())
