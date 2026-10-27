"""VVAH state collector (D5). Runs inside the task container around `vvaharness scan`.

Standard library only: it is copied into the task container and run with the
image's own Python. It starts the wrapped command, keeps every console line with
its time, and captures state at each stage hand-off and at the end of the run.

VVAH hands a typed record from one stage to the next by saving a checkpoint row
in its SQLite state database and then printing `[ckpt] saved <step>`. Each such
line is a hand-off; the collector then copies the database (SQLite backup API,
safe while VVAH keeps writing) and stores the target tree's changes since the
previous capture as a unified diff.

Layout under --state-dir:
  console.jsonl              {"t", "line"} per console line (stdout and stderr merged)
  handoffs.jsonl             one record per capture: seq, step, t, db, files_diff, files_changed
  NN_<step>/vvaharness.db    the state database as it was at that hand-off
  NN_<step>/files.diff       the tree's changes since the previous capture
"""

import argparse
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

CKPT_LINE = re.compile(r"\[ckpt\] saved (\S+)")


class Collector:
    def __init__(self, state_dir: Path, repo: Path, db: Path):
        self.state, self.repo, self.db = state_dir, repo, db
        self.state.mkdir(parents=True, exist_ok=True)
        self.prev = self.state / ".prev"
        self._copy_tree(self.repo, self.prev)
        self.seq = 0

    def capture(self, step: str, **extra) -> dict:
        self.seq += 1
        t = time.time()
        name = f"{self.seq:02d}_{re.sub(r'[^A-Za-z0-9_.-]', '_', step)}"
        out = self.state / name
        out.mkdir()
        record = {"seq": self.seq, "step": step, "t": t, "db": None}
        if self.db.exists():
            src = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
            dst = sqlite3.connect(out / "vvaharness.db")
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            record["db"] = f"{name}/vvaharness.db"
        diff, changed = self._diff_since_previous()
        (out / "files.diff").write_text(diff)
        record.update(files_diff=f"{name}/files.diff", files_changed=changed, **extra)
        with (self.state / "handoffs.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")
        return record

    def _diff_since_previous(self) -> tuple[str, list[str]]:
        # Paths in the diff are relative to the target root (a/<path>, b/<path>).
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
        shutil.copytree(src, dst, symlinks=True)


def _signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vvah_collector")
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--db", type=Path, required=True, help="VVAH's state database ($VVAHARNESS_STATE_DIR/vvaharness.db)")
    p.add_argument("--timeout", type=float, default=None,
                   help="wall-clock limit in seconds; then SIGTERM, and SIGKILL 60 s later")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd

    collector = Collector(args.state_dir, args.repo, args.db)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env={**os.environ, "PYTHONUNBUFFERED": "1"},
                            text=True, errors="replace", bufsize=1, start_new_session=True)
    timed_out = threading.Event()
    timers = []
    if args.timeout:
        def stop():
            timed_out.set()
            _signal(proc, signal.SIGCONT)
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
            m = CKPT_LINE.search(line)
            if m:
                # Hold the scan while its hand-off is copied, so the next stage
                # cannot change state between the checkpoint and the capture.
                _signal(proc, signal.SIGSTOP)
                try:
                    collector.capture(m.group(1))
                finally:
                    _signal(proc, signal.SIGCONT)
    code = proc.wait()
    for t in timers:
        t.cancel()
    collector.capture("end", exit_code=code, timed_out=timed_out.is_set())
    shutil.rmtree(collector.prev, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
