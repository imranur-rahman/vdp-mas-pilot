"""VVAH role windows and hand-off records, from the collector's console log and captures.

The console lines are copied from a real VVAH 1.5.1 development run.
"""

from pilot.adapters.vvah import handoff_records, role_windows

CONSOLE = [
    (100.0, "  05:15:29.659 WARN  v.b.l.cli.discovery : 'claude' not found on PATH; using bare name"),
    (109.6, "  ✓ [probe] all model backends reachable"),
    (109.7, "  [auto-step1] surveying /src/md4c (27 files, prompt 5238 chars)"),
    (111.0, "  [auto-step1] applied overlay  (exclude_dirs=7 exts=20 globs=2)"),
    (111.1, "[progress] stage-start s0    rules"),
    (112.6, "  [ckpt] saved s0"),
    (112.7, "[progress] stage-done  s0   outcome=completed  1.6s  entry_points=0 sinks=0"),
    (125.1, "[progress] stage-start s2    claude-haiku-5-5 [deepagents]"),
    (142.7, "  [ckpt] saved s2"),
    (142.8, "  [ckpt] saved s2_prov"),
    (142.9, "[progress] stage-done  s2   outcome=completed  17.7s  assets=4 boundaries=4 threats=10"),
    (171.3, "[progress] stage-start s5    enrich"),
    (171.4, "  [ckpt] saved s5"),
    (171.5, "[progress] stage-done  s5   outcome=completed  0.2s  kept=1 dropped=0"),
    (174.5, "[progress] stage-start s6    claude-haiku-5-5 [deepagents]"),
    (177.5, "[progress] stage-done  s6   outcome=completed  3.0s  verified=1 dropped=0"),
    (178.0, "[progress] stage-start s10   claude-haiku-5-5 [deepagents]"),
    (178.1, "[progress] stage-done  s10  outcome=completed  0.1s  attempted=0 fixed=0 not_fixed=0"),
    (178.2, "Done in 68s. 1 verified findings (0 dropped), 0 chains."),
]
CONSOLE = [{"t": t, "line": line} for t, line in CONSOLE]

HANDOFFS = [
    {"seq": 1, "step": "s0", "t": 112.65, "db": "01_s0/vvaharness.db", "files_diff": "01_s0/files.diff", "files_changed": []},
    {"seq": 2, "step": "s2", "t": 142.75, "db": "02_s2/vvaharness.db", "files_diff": "02_s2/files.diff", "files_changed": []},
    {"seq": 3, "step": "s2_prov", "t": 142.85, "db": "03_s2_prov/vvaharness.db", "files_diff": "03_s2_prov/files.diff", "files_changed": []},
    {"seq": 4, "step": "s5", "t": 171.45, "db": "04_s5/vvaharness.db", "files_diff": "04_s5/files.diff", "files_changed": []},
    {"seq": 5, "step": "end", "t": 178.3, "db": "05_end/vvaharness.db", "files_diff": "05_end/files.diff",
     "files_changed": ["security-scan/findings.json"], "exit_code": 0},
]


def test_each_model_stage_is_one_roles_window():
    windows = {w["role"]: w for w in role_windows(CONSOLE)}
    assert (windows["threatmodel"]["start"], windows["threatmodel"]["end"]) == (125.1, 142.9)
    assert (windows["verify"]["start"], windows["verify"]["end"]) == (174.5, 177.5)
    assert windows["remediate"]["stage"] == "s10"


def test_calls_before_the_first_stage_are_preflight_probes_then_the_auto_exclude_survey():
    windows = {w["role"]: w for w in role_windows(CONSOLE)}
    assert (windows["preflight"]["start"], windows["preflight"]["end"]) == (100.0, 109.6)
    assert (windows["autoexclude"]["start"], windows["autoexclude"]["end"]) == (109.7, 111.0)


def test_stages_without_model_calls_get_no_window():
    assert {w["stage"] for w in role_windows(CONSOLE)} == {"preflight", "auto-step1", "s2", "s6", "s10"}


def test_a_deterministic_stage_hands_off_through_a_code_decision():
    recs = handoff_records(HANDOFFS, CONSOLE)
    s5 = next(r for r in recs if r["stage"] == "s5")
    assert s5["step_type"] == "code_decision"
    assert s5["summary"] == "outcome=completed  0.2s  kept=1 dropped=0"
    assert s5["state_ref"] == {"db": "state/04_s5/vvaharness.db", "files_diff": "state/04_s5/files.diff",
                               "files_changed": [], "checkpoint": "s5"}


def test_a_model_stage_hands_off_its_record_from_its_role():
    s2 = next(r for r in handoff_records(HANDOFFS, CONSOLE) if r["stage"] == "s2")
    assert s2["step_type"] == "handoff" and s2["role"] == "threatmodel"
    assert s2["t"] == 142.75
    assert s2["summary"] == "outcome=completed  17.7s  assets=4 boundaries=4 threats=10"


def test_provenance_rows_are_not_hand_offs_and_the_end_of_run_capture_is_kept():
    recs = handoff_records(HANDOFFS, CONSOLE)
    assert [r["stage"] for r in recs] == ["s0", "s2", "s5", "end"]
    end = recs[-1]
    assert end["step_type"] == "handoff" and end["role"] is None
    assert end["state_ref"]["files_changed"] == ["security-scan/findings.json"]
