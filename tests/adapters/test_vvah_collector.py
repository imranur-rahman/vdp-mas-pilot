"""VVAH state collector: a wrapped command goes in, state captures come out.

The collector runs inside the task container around `vvaharness scan`. Here a
fake scan stands in for VVAH: it writes checkpoint rows to a SQLite state
database the way VVAH does and prints VVAH's `[ckpt] saved <step>` line.
"""

import json
import sqlite3
import sys
import textwrap
from pathlib import Path

from pilot.adapters import vvah_collector

FAKE_SCAN = textwrap.dedent("""
    import sqlite3, sys, time
    from pathlib import Path
    db, repo = sys.argv[1], Path(sys.argv[2])
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS checkpoints (run_id, step, payload)")
    def save(step, payload):
        with con:
            con.execute("INSERT INTO checkpoints VALUES ('r', ?, ?)", (step, payload))
        print(f"  [ckpt] saved {step}", flush=True)
        time.sleep(0.5)  # the next stage's model calls take at least this long
    print("starting scan", flush=True)
    save("s2", '{"threats": []}')
    (repo / "md4c.c").write_text("int fixed;\\n")
    save("s10.f1", '{"kind": "fixed"}')
    print("done", file=sys.stderr, flush=True)
    sys.exit(3)
""")


def run_fake_scan(tmp_path: Path) -> tuple[int, Path]:
    repo = tmp_path / "md4c"
    repo.mkdir()
    (repo / "md4c.c").write_text("int vulnerable;\n")
    db_dir = tmp_path / "vvah-state"
    db_dir.mkdir()
    script = tmp_path / "fake_scan.py"
    script.write_text(FAKE_SCAN)
    state = tmp_path / "state"
    code = vvah_collector.main([
        "--state-dir", str(state), "--repo", str(repo), "--db", str(db_dir / "vvaharness.db"),
        "--", sys.executable, str(script), str(db_dir / "vvaharness.db"), str(repo),
    ])
    return code, state


def handoffs(state: Path) -> list[dict]:
    return [json.loads(l) for l in (state / "handoffs.jsonl").read_text().splitlines()]


def test_returns_the_scan_exit_code(tmp_path):
    code, _ = run_fake_scan(tmp_path)
    assert code == 3


def test_captures_state_at_each_checkpoint_and_at_the_end(tmp_path):
    _, state = run_fake_scan(tmp_path)
    hs = handoffs(state)
    assert [h["step"] for h in hs] == ["s2", "s10.f1", "end"]
    assert [h["seq"] for h in hs] == [1, 2, 3]
    assert all(h["t"] for h in hs)
    assert hs[-1]["exit_code"] == 3


def test_each_capture_holds_a_copy_of_the_state_database_as_it_was_then(tmp_path):
    _, state = run_fake_scan(tmp_path)
    steps_in_copy = []
    for h in handoffs(state):
        con = sqlite3.connect(state / h["db"])
        steps_in_copy.append(sorted(r[0] for r in con.execute("SELECT step FROM checkpoints")))
    assert steps_in_copy == [["s2"], ["s10.f1", "s2"], ["s10.f1", "s2"]]


def test_file_state_is_stored_as_differences_between_captures(tmp_path):
    _, state = run_fake_scan(tmp_path)
    hs = handoffs(state)
    diffs = [(state / h["files_diff"]).read_text() for h in hs]
    assert diffs[0] == ""   # nothing changed before the first hand-off
    assert "-int vulnerable;" in diffs[1] and "+int fixed;" in diffs[1]
    assert diffs[2] == ""   # nothing changed after the last checkpoint
    assert hs[1]["files_changed"] == ["md4c.c"]


def test_console_lines_are_kept_with_their_times(tmp_path):
    _, state = run_fake_scan(tmp_path)
    lines = [json.loads(l) for l in (state / "console.jsonl").read_text().splitlines()]
    text = [l["line"] for l in lines]
    assert text[0] == "starting scan"
    assert "  [ckpt] saved s2" in text and "done" in text
    assert all(isinstance(l["t"], float) for l in lines)
