"""State capture (D5): file state stored as differences between captures,
listed in `state/manifest.json` with the hand-off each capture marks."""

import json

from pilot.state import capture, load_manifest


def test_first_capture_stores_everything_as_additions(tmp_path):
    entry = capture(tmp_path, "after-threat-model", {"data/md4c/INFO.md": b"# md4c\n"},
                    from_role="threat-model", to_role="matcher-generator", ts=100.0)
    diff = (tmp_path / entry["diff"]).read_text()
    assert "+++ b/data/md4c/INFO.md" in diff and "+# md4c" in diff
    assert entry["added"] == ["data/md4c/INFO.md"] and entry["changed"] == [] and entry["removed"] == []
    assert entry["seq"] == 1 and entry["label"] == "after-threat-model"
    assert (entry["from_role"], entry["to_role"], entry["ts"]) == ("threat-model", "matcher-generator", 100.0)


def test_later_capture_stores_only_what_changed(tmp_path):
    capture(tmp_path, "a", {"keep.json": b'{"x": 1}\n', "edit.json": b'{"n": 1}\n', "gone.txt": b"bye\n"})
    entry = capture(tmp_path, "b", {"keep.json": b'{"x": 1}\n', "edit.json": b'{"n": 2}\n', "new.txt": b"hi\n"})
    diff = (tmp_path / entry["diff"]).read_text()
    assert "keep.json" not in diff
    assert '-{"n": 1}' in diff and '+{"n": 2}' in diff
    assert entry["added"] == ["new.txt"] and entry["changed"] == ["edit.json"] and entry["removed"] == ["gone.txt"]
    assert [e["seq"] for e in load_manifest(tmp_path)] == [1, 2]


def test_latest_full_state_is_kept_for_the_output_adapter(tmp_path):
    capture(tmp_path, "a", {"d/f.json": b"1"})
    capture(tmp_path, "b", {"d/f.json": b"2"})
    assert (tmp_path / "state" / "latest" / "d" / "f.json").read_bytes() == b"2"


def test_binary_files_are_recorded_by_hash_not_inlined(tmp_path):
    entry = capture(tmp_path, "a", {"db.sqlite": b"\x00\x01\xff"})
    diff = (tmp_path / entry["diff"]).read_text()
    assert "Binary file db.sqlite" in diff and "sha256" in diff
    manifest = json.loads((tmp_path / "state" / "manifest.json").read_text())
    assert manifest[0]["added"] == ["db.sqlite"]
