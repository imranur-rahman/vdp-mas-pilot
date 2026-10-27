"""State capture at hand-offs and at the end of a run (D5).

Each capture writes `state/<NN>-<label>.diff`, a unified diff against the
previous capture (the first one against nothing), and appends an entry to
`state/manifest.json`. The most recent full state is kept in `state/latest/`
so the next capture can be diffed and the output adapter can read it.
Databases are binary: a diff records their hash, and `latest/` holds the copy.
"""

import difflib
import hashlib
import json
import re
import shutil
import time
from pathlib import Path


def load_manifest(run_dir: Path) -> list[dict]:
    path = Path(run_dir) / "state" / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else []


def _read_tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _text(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode()
    except UnicodeDecodeError:
        return None


def _file_diff(path: str, old: bytes | None, new: bytes | None) -> str:
    old_t, new_t = (_text(old) if old is not None else ""), (_text(new) if new is not None else "")
    if old_t is None or new_t is None:
        h = lambda b: hashlib.sha256(b).hexdigest() if b is not None else "none"
        return f"Binary file {path} changed: sha256 {h(old)} -> {h(new)}\n"
    lines = difflib.unified_diff(old_t.splitlines(keepends=True), new_t.splitlines(keepends=True),
                                 "/dev/null" if old is None else f"a/{path}",
                                 "/dev/null" if new is None else f"b/{path}")
    out = "".join(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n" for l in lines)
    if old is None and not out:  # empty new file
        out = f"--- /dev/null\n+++ b/{path}\n"
    return out


def capture(run_dir: Path, label: str, files: dict[str, bytes], *, from_role: str | None = None,
            to_role: str | None = None, ts: float | None = None, note: str | None = None) -> dict:
    state = Path(run_dir) / "state"
    state.mkdir(parents=True, exist_ok=True)
    latest = state / "latest"
    before = _read_tree(latest)
    manifest = load_manifest(run_dir)
    seq = len(manifest) + 1

    added = sorted(set(files) - set(before))
    removed = sorted(set(before) - set(files))
    changed = sorted(p for p in set(files) & set(before) if files[p] != before[p])
    diff = "".join(_file_diff(p, before.get(p), files.get(p)) for p in sorted(added + changed + removed))
    name = f"{seq:02d}-{re.sub(r'[^A-Za-z0-9._-]+', '-', label)}.diff"
    (state / name).write_text(diff)

    if latest.exists():
        shutil.rmtree(latest)
    for rel, data in files.items():
        dest = latest / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    entry = {"seq": seq, "label": label, "ts": ts if ts is not None else time.time(),
             "from_role": from_role, "to_role": to_role, "diff": f"state/{name}",
             "files": len(files), "added": added, "changed": changed, "removed": removed, "note": note}
    manifest.append(entry)
    (state / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return entry
