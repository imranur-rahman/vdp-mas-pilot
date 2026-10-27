"""OpenAnt scan-state collector (D5). Runs inside the runner container around
`openant scan`.

Standard library only: it is copied into the runner container and run with the
image's own Python. OpenAnt writes `<step>.report.json` into its scan directory
as each pipeline step finishes (`core/step_report.py`). The collector polls the
scan directory, and at each new `<step>.report.json` (a hand-off) it snapshots
the scan directory and records the target tree's changes since the previous
capture, then captures once more at the end.

Layout under --state-dir:
  console.jsonl              {"t", "line"} per console line (stdout and stderr merged)
  handoffs.jsonl             one record per capture: seq, step, t, report, state_ref, files_changed
  NN_<step>/                 the scan directory as it was at that hand-off
  NN_<step>/files.diff       the target tree's changes since the previous capture
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

REPORT_SUFFIX = ".report.json"
# scan.report.json is the whole-run aggregate, not a per-step hand-off.
NON_HANDOFF_REPORTS = {"scan"}


def phase_of_report(name: str) -> str:
    """`analyze.report.json` -> `analyze`."""
    return Path(name).name[: -len(REPORT_SUFFIX)] if name.endswith(REPORT_SUFFIX) else Path(name).stem


def is_handoff_report(name: str) -> bool:
    """A `<step>.report.json` that marks a per-step hand-off (not the aggregate)."""
    return name.endswith(REPORT_SUFFIX) and phase_of_report(name) not in NON_HANDOFF_REPORTS


class Collector:
    """Polls `scan_dir` for new step reports and captures scan state at each."""

    def __init__(self, state_dir: Path, repo: Path, scan_dir: Path):
        self.state = Path(state_dir)
        self.repo = Path(repo)
        self.scan_dir = Path(scan_dir)
        self.state.mkdir(parents=True, exist_ok=True)
        self.prev = self.state / ".prev"
        self._copy_tree(self.repo, self.prev)
        self.seq = 0
        self.seen: set[str] = set()

    def poll(self) -> None:
        """Capture any step reports that have appeared since the last poll."""
        if not self.scan_dir.exists():
            return
        for report in sorted(self.scan_dir.rglob(f"*{REPORT_SUFFIX}"), key=lambda p: p.stat().st_mtime):
            name = report.name
            if name in self.seen or not is_handoff_report(name):
                continue
            self.seen.add(name)
            self.capture(phase_of_report(name), report)

    def capture(self, step: str, report: Path | None = None) -> dict:
        self.seq += 1
        t = time.time()
        name = f"{self.seq:02d}_{re.sub(r'[^A-Za-z0-9_.-]', '_', step)}"
        out = self.state / name
        out.mkdir(parents=True, exist_ok=True)
        if self.scan_dir.exists():
            shutil.copytree(self.scan_dir, out / "scan", symlinks=True, dirs_exist_ok=True)
        diff, changed = self._diff_since_previous()
        (out / "files.diff").write_text(diff)
        record = {"seq": self.seq, "step": step, "t": t,
                  "report": f"{name}/scan/{report.name}" if report else None,
                  "state_ref": name, "files_diff": f"{name}/files.diff", "files_changed": changed}
        with (self.state / "handoffs.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")
        return record

    def _diff_since_previous(self) -> tuple[str, list[str]]:
        stage = self.state / ".cur"
        self._copy_tree(self.repo, stage)
        r = subprocess.run(["diff", "-ruN", "-a", ".prev", ".cur"], capture_output=True, text=True,
                           errors="replace", cwd=self.state, env={**os.environ, "LC_ALL": "C"})
        diff = re.sub(r"^(---|\+\+\+) \.prev/", r"\1 a/", r.stdout, flags=re.M)
        diff = re.sub(r"^(---|\+\+\+) \.cur/", r"\1 b/", diff, flags=re.M)
        diff = re.sub(r"^diff -ruN -a \.prev/(\S+) \.cur/\S+$", r"diff -ruN a/\1 b/\1", diff, flags=re.M)
        changed = sorted(set(re.findall(r"^diff -ruN a/(\S+) ", diff, flags=re.M)))
        shutil.rmtree(self.prev)
        stage.rename(self.prev)
        return diff, changed

    @staticmethod
    def _copy_tree(src: Path, dst: Path) -> None:
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst, symlinks=True)
        else:
            dst.mkdir(parents=True)


def _signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="openant_collector")
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--repo", type=Path, required=True, help="the target source tree OpenAnt analyzes")
    p.add_argument("--scan-dir", type=Path, required=True, help="OpenAnt's scan output directory")
    p.add_argument("--poll", type=float, default=5.0, help="seconds between scan-dir polls")
    p.add_argument("--timeout", type=float, default=None,
                   help="wall-clock limit in seconds; then SIGTERM, and SIGKILL 60 s later")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd

    collector = Collector(args.state_dir, args.repo, args.scan_dir)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env={**os.environ, "PYTHONUNBUFFERED": "1"},
                            text=True, errors="replace", bufsize=1, start_new_session=True)
    timed_out = threading.Event()
    stop_poll = threading.Event()

    def poller():
        while not stop_poll.wait(args.poll):
            try:
                collector.poll()
            except Exception:
                pass
    poll_thread = threading.Thread(target=poller, daemon=True)
    poll_thread.start()

    timers = []
    if args.timeout:
        def stop():
            timed_out.set()
            _signal(proc, signal.SIGTERM)
            kill = threading.Timer(60, _signal, (proc, signal.SIGKILL))
            kill.daemon = True
            kill.start()
            timers.append(kill)
        timer = threading.Timer(args.timeout, stop)
        timer.daemon = True
        timer.start()
        timers.append(timer)

    with (args.state_dir / "console.jsonl").open("a") as console:
        for line in proc.stdout:
            line = line.rstrip("\n")
            console.write(json.dumps({"t": time.time(), "line": line}) + "\n")
            console.flush()
    code = proc.wait()
    stop_poll.set()
    poll_thread.join(timeout=10)
    for t in timers:
        t.cancel()
    collector.poll()  # catch any reports written just before exit
    collector.capture("end")
    shutil.rmtree(collector.prev, ignore_errors=True)
    (args.state_dir / "exit.json").write_text(json.dumps({"exit_code": code, "timed_out": timed_out.is_set()}))
    return code


if __name__ == "__main__":
    sys.exit(main())
