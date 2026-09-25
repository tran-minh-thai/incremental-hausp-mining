#!/usr/bin/env python3
"""Run a measurement campaign from a plan file, one JVM per command, on Windows or macOS.

The measurement machine is Windows, so this driver uses the Python standard library only. It is
the one entry point for measurements, and it applies itself every condition a measurement
depends on, instead of leaving them to a command line:

  * the JVM invocation of every recorded run: -Xmx24g -XX:+UseG1GC -jar <jar> ... --timeout 90;
  * a JAR built from this working tree (rebuilt with Maven when older than any source file);
  * a clean working tree at a commit that origin also has, so the commit each row records is
    code anyone can fetch;
  * datasets byte-identical to datasets/MANIFEST.sha256;
  * the measurement machine named in MEASUREMENT_MACHINE.txt (probe plans are exempt);
  * the machine kept awake while the campaign runs (SetThreadExecutionState on Windows,
    caffeinate on macOS);
  * every command atomic. Before a command starts, the size of every file under its results
    directory is written to the ledger. A command that did not finish -- a stop, a crash, a power
    cut, a restart forced by Windows Update -- is rolled back to those sizes on the next start and
    run again from its beginning. This is exact because result files are only ever appended to
    (CSVLogger opens them with append=true), and the driver checks that after every command.
    It also means no runner's own --resume is relied on: four runners have none.

Usage, from the repository root:
    python scripts/campaign.py scripts/plans/<plan>.json             run, or continue after a stop
    python scripts/campaign.py scripts/plans/<plan>.json --dry-run   print the commands only
To stop cleanly, create the stop file printed at start: the running command finishes first.
When every command is done the driver commits the results directory and pushes it.

Exit codes: 0 all done, 1 refused before running, 2 a command failed (rolled back),
3 measurements disabled here, 4 stopped on request, 5 a result file was rewritten, not appended,
6 a command exited 0 without writing a result row.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEAP = "24g"            # the -Xmx of every recorded run; part of what an OOM verdict means
TIMEOUT_MIN = 90        # per-batch time limit the manuscript states
JVM_FLAGS = ["-XX:+UseG1GC"]
STOP_FILE = Path(tempfile.gettempdir()) / "hausp-stop"
TAIL = 4096             # bytes whose hash proves a file's old content was left alone
DRIVER_FLAGS = {"--timeout", "--resume", "--results-dir", "--exp", "--dataset", "--algo"}
CURRENT: list = []      # the running JVM, so an interruption of the driver never leaves it writing
OPEN_LINE = [False]     # the JVM's last output line had no newline (it was killed mid-line)


class Refused(Exception):
    """A precondition does not hold; nothing has been measured."""


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def say(msg: str, log=None) -> None:
    line = f"[campaign {dt.datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}"
    if OPEN_LINE[0]:
        line = "\n" + line
        OPEN_LINE[0] = False
    print(line, flush=True)
    if log:
        log.write(line + "\n")
        log.flush()


def run_quiet(argv: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace")
    except OSError as e:
        return 127, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tail_hash(path: Path, size: int) -> str:
    with path.open("rb") as fh:
        fh.seek(max(0, size - TAIL))
        return hashlib.sha256(fh.read(size - max(0, size - TAIL))).hexdigest()


# ------------------------------------------------------------------------------- plan

def load_plan(path: Path) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    for key in ("name", "ledger_dir", "commands"):
        if key not in plan:
            raise Refused(f"plan {path} has no '{key}'")
    seen = set()
    for c in plan["commands"]:
        for key in ("id", "exp", "dataset", "algo", "args", "results_dir"):
            if key not in c:
                raise Refused(f"plan command {c.get('id')} has no '{key}'")
        if c["id"] in seen:
            raise Refused(f"plan command id {c['id']} appears twice")
        seen.add(c["id"])
        clash = DRIVER_FLAGS.intersection(c["args"])
        if clash:
            raise Refused(f"plan command {c['id']} passes {sorted(clash)} itself; the driver sets those")
        rd = Path(c["results_dir"])
        if rd.is_absolute() or ".." in rd.parts:
            raise Refused(f"plan command {c['id']}: results_dir {rd} must be relative, inside the repository")
        if rd != Path(plan["ledger_dir"]) and Path(plan["ledger_dir"]) not in rd.parents:
            raise Refused(f"plan command {c['id']}: results_dir {rd} is outside the plan's directory "
                          f"{plan['ledger_dir']}, where its ledger, checks and commit are confined")
        if "--mem-mode" in c["args"] and "mem" not in c["results_dir"]:
            raise Refused(f"plan command {c['id']}: a live-heap run needs a results_dir containing 'mem'")
    return plan


def argv_of(c: dict, java: str, jar: Path) -> list[str]:
    return ([java, f"-Xmx{HEAP}", *JVM_FLAGS, "-jar", str(jar),
             "--exp", str(c["exp"]), "--dataset", c["dataset"], "--algo", c["algo"], *c["args"],
             "--results-dir", c["results_dir"], "--timeout", str(TIMEOUT_MIN)])


def command_key(c: dict) -> str:
    """Identity of a command, independent of where java and the JAR live."""
    body = json.dumps([c["exp"], c["dataset"], c["algo"], c["args"], c["results_dir"], HEAP, TIMEOUT_MIN])
    return hashlib.sha256(body.encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------- preconditions

def is_probe(plan: dict) -> bool:
    return all(Path(c["results_dir"]).parts[0].startswith("results-probe") for c in plan["commands"])


def check_gate(plan: dict) -> None:
    """Mirror of the launcher's own guard, so a refusal comes before the build, with the reason."""
    if not os.environ.get("HAUSP_NO_MEASURE"):
        return
    toy = all(c["dataset"] == "example" for c in plan["commands"])
    if toy or is_probe(plan):
        return
    print("[campaign] REFUSED: HAUSP_NO_MEASURE is set, so this machine may not produce measurements.\n"
          "[campaign] Allowed here: plans on the example dataset, or plans writing under results-probe*.",
          file=sys.stderr)
    sys.exit(3)


def check_git() -> dict:
    code, top = run_quiet(["git", "rev-parse", "--show-toplevel"])
    if code != 0:
        raise Refused(f"git does not see a repository at {ROOT}: {top}")
    if Path(top).resolve() != ROOT:
        raise Refused(f"git top level is {top}, not {ROOT}")
    _, dirty = run_quiet(["git", "status", "--porcelain", "--untracked-files=no"])
    if dirty:
        raise Refused("tracked files have uncommitted changes, so the commit a result records would not be "
                      "the code that ran:\n  " + dirty.replace("\n", "\n  ")
                      + "\n  Commit or discard them (git stash), then start again.")
    _, head = run_quiet(["git", "rev-parse", "HEAD"])
    code, out = run_quiet(["git", "fetch", "--quiet", "origin"])
    fetched = code == 0
    _, containing = run_quiet(["git", "branch", "-r", "--contains", head])
    if not containing.strip():
        raise Refused(f"commit {head[:7]} is not on origin, so nobody else can fetch the code a result "
                      f"would name. Push it, or pull origin's version." + ("" if fetched else f"\n  (git fetch failed: {out})"))
    _, branch = run_quiet(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    _, behind = run_quiet(["git", "rev-list", "--count", f"HEAD..origin/{branch}"])
    if behind.isdigit() and int(behind) > 0:
        raise Refused(f"origin/{branch} is {behind} commit(s) ahead of this checkout. Run: git pull --ff-only")
    return {"commit": head, "branch": branch, "fetched": fetched}


def check_datasets() -> dict:
    manifest = ROOT / "datasets" / "MANIFEST.sha256"
    if not manifest.exists():
        raise Refused(f"{manifest} is missing")
    bad = []
    n = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        want, name = line.split(None, 1)
        name = name.lstrip("*").strip()
        p = ROOT / "datasets" / name
        n += 1
        if not p.exists():
            bad.append(f"{p}: missing")
        elif sha256_file(p) != want:
            bad.append(f"{p}: sha256 differs from the manifest")
    if bad:
        raise Refused("datasets do not match datasets/MANIFEST.sha256 (run: python scripts/fetch_datasets.py):\n  "
                      + "\n  ".join(bad))
    return {"files_verified": n, "manifest_sha256": sha256_file(manifest)}


def declared_host() -> str | None:
    p = ROOT / "MEASUREMENT_MACHINE.txt"
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "host":
            words = value.split()
            return words[0] if words else None
    return None


def same_host(a: str, b: str) -> bool:
    return a.split(".")[0].lower() == b.split(".")[0].lower()


def check_machine(plan: dict, log) -> str:
    here = socket.gethostname()
    declared = declared_host()
    if is_probe(plan):
        say(f"host {here}; probe plan, so the measurement-machine declaration is not required", log)
        return here
    if not declared:
        raise Refused(f"{ROOT / 'MEASUREMENT_MACHINE.txt'} declares no host, so no machine is allowed to "
                      f"produce the paper's numbers yet. This machine is '{here}'.")
    if not same_host(here, declared):
        raise Refused(f"this machine is '{here}', but {ROOT / 'MEASUREMENT_MACHINE.txt'} declares "
                      f"'{declared}' as the measurement machine.")
    return here


def find_jar() -> Path | None:
    jars = sorted(p for p in (ROOT / "build").glob("incremental-hausp-mining-*.jar")
                  if not p.name.startswith("original-"))
    return jars[-1] if jars else None


def newer_sources(jar: Path) -> list[Path]:
    t = jar.stat().st_mtime
    out = [p for p in (ROOT / "src").rglob("*") if p.is_file() and p.stat().st_mtime > t]
    pom = ROOT / "pom.xml"
    if pom.stat().st_mtime > t:
        out.append(pom)
    return out


def ensure_jar(log) -> Path:
    jar = find_jar()
    stale = newer_sources(jar) if jar else []
    if jar and not stale:
        return jar
    mvn = shutil.which("mvn")
    why = "no JAR under build/" if not jar else f"{len(stale)} source file(s) newer than {jar.name}, e.g. {stale[0]}"
    if not mvn:
        raise Refused(f"{why}, and Maven (mvn) is not on PATH to rebuild it")
    say(f"building the JAR ({why}): {mvn} -q package -DskipTests", log)
    code, out = run_quiet([mvn, "-q", "package", "-DskipTests"])
    if code != 0:
        raise Refused(f"Maven build failed:\n{out[-3000:]}")
    jar = find_jar()
    if not jar or newer_sources(jar):
        raise Refused("the build finished but build/ holds no JAR newer than the sources")
    return jar


# ------------------------------------------------------------------------------- keeping awake

class Awake:
    """Holds off system sleep while the campaign runs."""

    def __init__(self, required: bool, log):
        self.proc = None
        self.how = None
        system = platform.system()
        if system == "Windows":
            import ctypes
            es_continuous, es_system_required = 0x80000000, 0x00000001
            prev = ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
            if prev == 0:
                self._fail(required, "SetThreadExecutionState refused", log)
            else:
                self.how = "SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)"
        elif system == "Darwin" and shutil.which("caffeinate"):
            self.proc = subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())])
            self.how = "caffeinate -ims"
        else:
            self._fail(required, f"no sleep inhibitor known for {system}", log)
        if self.how:
            say(f"sleep held off for the whole campaign by {self.how}", log)

    @staticmethod
    def _fail(required: bool, why: str, log) -> None:
        if required:
            raise Refused(f"cannot keep the machine awake ({why}); a batch timed across a sleep "
                          f"would include the time asleep and nothing in the result would show it")
        say(f"WARNING: {why}; acceptable only because this is a probe plan", log)

    def release(self) -> None:
        if platform.system() == "Windows" and self.how:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        if self.proc:
            self.proc.terminate()


def environment(log) -> dict:
    env = {"host": socket.gethostname(), "os": platform.platform(), "machine": platform.machine(),
           "processor": platform.processor(), "python": sys.version.split()[0],
           "cpu_count": os.cpu_count()}
    java = shutil.which("java")
    env["java_path"] = java
    if java:
        _, env["java_version"] = run_quiet([java, "-version"])
    mvn = shutil.which("mvn")
    if mvn:
        _, v = run_quiet([mvn, "-v"])
        env["maven_version"] = v.splitlines()[0] if v else ""
    if platform.system() == "Windows":
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = MemStatus()
        ms.dwLength = ctypes.sizeof(MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            env["ram_gb"] = round(ms.ullTotalPhys / 2 ** 30, 1)
        _, env["power_scheme"] = run_quiet(["powercfg", "/getactivescheme"])
    elif platform.system() == "Darwin":
        _, mem = run_quiet(["sysctl", "-n", "hw.memsize"])
        if mem.isdigit():
            env["ram_gb"] = round(int(mem) / 2 ** 30, 1)
        _, env["processor"] = run_quiet(["sysctl", "-n", "machdep.cpu.brand_string"])
    return env


# ------------------------------------------------------------------------------- ledger

class Ledger:
    """Append-only record of what the campaign did, next to the results it describes."""

    def __init__(self, plan: dict):
        self.dir = ROOT / plan["ledger_dir"]
        self.path = self.dir / f"campaign-{plan['name']}.ledger.jsonl"
        self.events = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.events.append(json.loads(line))

    def add(self, event: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        event = {"time": now(), **event}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self.events.append(event)

    def done_keys(self) -> dict:
        return {e["id"]: e["key"] for e in self.events if e["event"] == "done"}

    def unfinished(self) -> dict | None:
        """The last command started and never closed (done, rolled back or failed)."""
        open_ = {}
        for e in self.events:
            if e["event"] == "start":
                open_[e["id"]] = e
            elif e["event"] in ("done", "rolled_back", "failed"):
                open_.pop(e["id"], None)
        if len(open_) > 1:
            raise Refused(f"ledger {self.path} has {len(open_)} commands open at once; it was edited by hand")
        return next(iter(open_.values()), None)


def snapshot(results_dir: Path) -> dict:
    files = {}
    if results_dir.is_dir():
        for p in sorted(results_dir.rglob("*")):
            if p.is_file() and not p.name.endswith(".ledger.jsonl"):
                size = p.stat().st_size
                files[p.relative_to(ROOT).as_posix()] = [size, tail_hash(p, size)]
    return files


def verify_appended(before: dict, results_dir: Path) -> list[str]:
    """Files present before must have kept their old bytes (same tail at the old size)."""
    problems = []
    for rel, (size, th) in before.items():
        p = ROOT / rel
        if not p.exists():
            problems.append(f"{rel}: deleted")
        elif p.stat().st_size < size:
            problems.append(f"{rel}: shrank from {size} to {p.stat().st_size} bytes")
        elif tail_hash(p, size) != th:
            problems.append(f"{rel}: its first {size} bytes changed")
    return problems


def rollback(start: dict, log) -> None:
    """Return every file under the command's results directory to its recorded size."""
    before = start["snapshot"]
    results_dir = ROOT / start["results_dir"]
    problems = verify_appended(before, results_dir)
    if problems:
        raise Refused("cannot roll back: the files were changed other than by appending:\n  " + "\n  ".join(problems))
    cut, removed = 0, 0
    for rel, (size, _) in before.items():
        p = ROOT / rel
        if p.stat().st_size > size:
            with p.open("r+b") as fh:
                fh.truncate(size)
            cut += 1
    for rel in snapshot(results_dir):
        if rel not in before:
            (ROOT / rel).unlink()
            removed += 1
    after = snapshot(results_dir)
    if {k: v[0] for k, v in after.items()} != {k: v[0] for k, v in before.items()}:
        raise Refused("rollback did not restore the recorded sizes")
    say(f"rolled back command {start['id']}: {cut} file(s) cut to their recorded size, {removed} new file(s) removed", log)


# ------------------------------------------------------------------------------- running

def run_command(c: dict, argv: list[str], ledger: Ledger, log, interrupt_after: float | None = None) -> int:
    results_dir = ROOT / c["results_dir"]
    before = snapshot(results_dir)
    start = {"event": "start", "id": c["id"], "key": command_key(c), "results_dir": c["results_dir"],
             "snapshot": before, "argv": argv[1:]}
    ledger.add(start)
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1)
    CURRENT[:] = [proc]

    def pump():
        for line in proc.stdout:
            log.write(line)
            sys.stdout.write(line)
            OPEN_LINE[0] = not line.endswith("\n")
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    if interrupt_after is not None:
        # Self-test of the rollback, run on the measurement machine itself: kill the JVM once it
        # has written something, as a power cut would, then undo it the way a restart does. A
        # kill before any row was written would test nothing, so that case is reported as such.
        deadline = t0 + max(interrupt_after, 1.0)
        grown = 0
        while proc.poll() is None:
            now_ = snapshot(results_dir)
            grown = sum(1 for rel, v in now_.items() if rel not in before or v[0] != before[rel][0])
            if grown and time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        finished = proc.poll() is not None
        if not finished:
            proc.kill()
        proc.wait()
        reader.join()
        grown = sum(1 for rel, v in snapshot(results_dir).items() if rel not in before or v[0] != before[rel][0])
        verdict = ("INCONCLUSIVE: nothing had been written, so the rollback had nothing to undo" if not grown
                   else f"{grown} file(s) had grown")
        say(f"self-test: command {c['id']} {'finished before it could be killed' if finished else 'killed'}"
            f" after {time.monotonic() - t0:.0f} s; {verdict}", log)
        rollback(start, log)
        ledger.add({"event": "rolled_back", "id": c["id"], "reason": "self-test",
                    "killed": not finished, "files_grown": grown})
        return run_command(c, argv, ledger, log)

    rc = proc.wait()
    reader.join()
    CURRENT.clear()
    wall = time.monotonic() - t0
    log.flush()
    problems = verify_appended(before, results_dir)
    if problems:
        ledger.add({"event": "failed", "id": c["id"], "rc": rc, "wall_s": round(wall, 1), "problems": problems})
        say("a result file was rewritten, not appended -- the rollback guarantee no longer holds:\n  "
            + "\n  ".join(problems), log)
        sys.exit(5)
    if rc != 0:
        rollback(start, log)
        ledger.add({"event": "failed", "id": c["id"], "rc": rc, "wall_s": round(wall, 1)})
        return rc
    grown = [rel for rel, v in snapshot(results_dir).items() if rel not in before or v[0] != before[rel][0]]
    if not grown:
        # The launcher exits 0 when a dataset or arm name matches nothing; recording that as done
        # would leave a hole in the campaign that only the tables would reveal, days later.
        ledger.add({"event": "failed", "id": c["id"], "rc": 0, "wall_s": round(wall, 1),
                    "problems": ["exited 0 but wrote no result row"]})
        say(f"command {c['id']} exited 0 but wrote no result row: the dataset or arm name probably "
            f"matched nothing", log)
        return 6
    ledger.add({"event": "done", "id": c["id"], "key": command_key(c), "rc": 0, "wall_s": round(wall, 1)})
    return 0


def duplicated_keys(plan: dict) -> list[str]:
    """Rows that share (arm, dataset, threshold, increment, schedule, batch, trial) in one file.

    Experiment 11 is skipped: its four schedules share one head batch under one key by design.
    """
    found = []
    for rd in [plan["ledger_dir"]]:
        for f in sorted((ROOT / rd).rglob("*.csv")):
            if "exp11" in f.parts:
                continue
            with f.open(encoding="utf-8", errors="replace", newline="") as fh:
                rows = [r for r in csv.reader(line for line in fh if not line.startswith("#"))]
            if not rows:
                continue
            head, body = rows[0], rows[1:]
            cols = [head.index(k) for k in ("Algorithm", "UBArm", "Dataset", "MinUtil", "mu", "DeltaRatio",
                                            "Schedule", "BatchID", "RunIndex", "MemMode") if k in head]
            seen, dup = set(), 0
            for r in body:
                k = tuple(r[i] if i < len(r) else "" for i in cols)
                dup += k in seen
                seen.add(k)
            if dup:
                found.append(f"{f.relative_to(ROOT).as_posix()}: {dup} row(s) repeat a key")
    return found


def finish(plan: dict, ledger: Ledger, commit: bool, push: bool, log) -> int:
    dups = duplicated_keys(plan)
    for d in dups:
        say(f"DUPLICATED {d}", log)
    if dups:
        say("rows written twice: results not committed; report this before anything else", log)
        return 2
    if not commit:
        say("results left uncommitted (--no-commit)", log)
        return 0
    dirs = [plan["ledger_dir"]]
    code, out = run_quiet(["git", "add", "--", *dirs])
    if code != 0:
        say(f"git add failed: {out}", log)
        return 2
    code, out = run_quiet(["git", "commit", "-m", f"Measurement campaign '{plan['name']}' on {socket.gethostname()}",
                           "--", *dirs])
    if code != 0 and "nothing to commit" not in out:
        say(f"git commit failed: {out}\n  Commit and push {', '.join(dirs)} by hand.", log)
        return 2
    say(f"committed {', '.join(dirs)}", log)
    if push:
        code, out = run_quiet(["git", "push"])
        say("pushed to origin" if code == 0 else f"git push failed: {out}\n  Run: git push", log)
        return 0 if code == 0 else 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("plan", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="print the commands and stop")
    ap.add_argument("--no-push", action="store_true", help="commit the results at the end but do not push")
    ap.add_argument("--no-commit", action="store_true", help="leave the results uncommitted (development runs)")
    a = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    plan_path = a.plan if a.plan.is_absolute() else (Path.cwd() / a.plan)
    try:
        plan = load_plan(plan_path)
    except Refused as e:
        print(f"[campaign] REFUSED: {e}", file=sys.stderr)
        return 1
    java = shutil.which("java") or "java"
    if a.dry_run:
        for c in plan["commands"]:
            print(f"{c['id']:>4}  " + " ".join(argv_of(c, "java", Path("build/<jar>"))[1:]))
        print(f"{len(plan['commands'])} command(s)")
        return 0
    check_gate(plan)

    (ROOT / "logs").mkdir(exist_ok=True)
    log = (ROOT / "logs" / f"campaign-{plan['name']}.log").open("a", encoding="utf-8")
    ledger = Ledger(plan)
    awake = None
    try:
        say(f"plan {plan['name']}: {len(plan['commands'])} command(s); stop file: {STOP_FILE}", log)
        if STOP_FILE.exists():
            STOP_FILE.unlink()
        git = check_git()
        data = check_datasets()
        host = check_machine(plan, log)
        jar = ensure_jar(log)
        awake = Awake(required=not is_probe(plan), log=log)
        env = environment(log)
        ledger.add({"event": "session", "git": git, "datasets": data, "jar": jar.name,
                    "jar_sha256": sha256_file(jar), "plan_sha256": sha256_file(plan_path),
                    "heap": HEAP, "timeout_min": TIMEOUT_MIN, "jvm_flags": JVM_FLAGS, "environment": env})
        say(f"commit {git['commit'][:7]}, {data['files_verified']} dataset files verified, host {host}, "
            f"java {env.get('java_version', '?').splitlines()[0] if env.get('java_version') else '?'}", log)
        say("before a long campaign, also make sure: on mains power; Windows Update paused for the whole "
            "campaign; power plan set to high performance; the repository folder excluded from real-time "
            "virus scanning; no other heavy program running", log)

        open_ = ledger.unfinished()
        if open_:
            say(f"command {open_['id']} was interrupted last time; rolling it back before running it again", log)
            rollback(open_, log)
            ledger.add({"event": "rolled_back", "id": open_["id"], "reason": "interrupted"})

        done = ledger.done_keys()
        for c in plan["commands"]:
            if c["id"] in done and done[c["id"]] != command_key(c):
                raise Refused(f"command {c['id']} was done with different arguments than the plan now gives; "
                              f"the plan changed mid-campaign. Start a new plan name instead.")
        todo = [c for c in plan["commands"] if c["id"] not in done]
        est_left = sum(c.get("est_min", 0) for c in todo) / 60
        say(f"{len(plan['commands']) - len(todo)} done, {len(todo)} to run "
            f"(estimate {est_left:.1f} h, extrapolated from earlier runs on another machine)", log)

        for c in todo:
            if STOP_FILE.exists():
                STOP_FILE.unlink()
                say("stop requested; run the same command again to continue", log)
                return 4
            say(f"--- {c['id']}/{len(plan['commands'])} {c.get('label', '')}", log)
            rc = run_command(c, argv_of(c, java, jar), ledger, log, c.get("selftest_interrupt_after_s"))
            if rc != 0:
                say(f"command {c['id']} failed ({rc}); its partial rows were rolled back. Campaign stopped.", log)
                return 6 if rc == 6 else 2
        say("every command of the plan is done", log)
        return finish(plan, ledger, not a.no_commit, not (a.no_push or a.no_commit), log)
    except Refused as e:
        say(f"REFUSED: {e}", log)
        return 1
    except KeyboardInterrupt:
        say("interrupted; the running command is rolled back at the next start", log)
        return 130
    finally:
        for proc in CURRENT:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if awake:
            awake.release()
        log.close()


if __name__ == "__main__":
    sys.exit(main())
