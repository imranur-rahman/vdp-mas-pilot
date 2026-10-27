"""Coding kit: per-coder label files and Cohen's kappa between two coders.

One YAML file per coder per run, `coding/labels/<run_id>/<coder>.yaml`, kept
under version control (T-1). Coders work independently, each in their own
file, keyed by step ID and finding ID from the bundle:

    schema: pilot-labels/1
    run_id: <run id>
    coder: <name>
    bundle: {steps_sha256: ..., stage_map_version: ...}   # what was coded
    steps:
      s0001:
        failure_modes: null        # null = not coded yet; [] = coded, no failure; [codes]
        first_decisive_error: false
        stage: null                # only for a step whose role maps to several stages
        claim: null                # text that replaces the extracted claim
        note: null
    findings:
      f001:
        verdict: null              # tp_other | fp | unverified (tp_target comes from the check)
        note: null

`coded_steps` applies one coder's stage choices and claim corrections to the
bundle's steps. `kappa` compares two coders' files item by item.
"""

import hashlib
import json
from pathlib import Path

import yaml

from .stages import STAGES

REPO = Path(__file__).resolve().parents[2]
LABELS_DIR = REPO / "coding" / "labels"
SCHEMA = "pilot-labels/1"
CODER_VERDICTS = ("tp_other", "fp", "unverified", "tp_target")


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _one_line(text, limit=160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def init_labels(bundle: Path, coder: str, out: Path | None = None) -> Path:
    """Write an empty label file for `coder` with every step and finding of the bundle.

    Comments beside each key show what is being coded (step type, agent,
    stage options, extracted claim; finding location and check), so coders can
    work from the file next to `trajectory.md`. Never overwrites a file.
    """
    bundle = Path(bundle)
    run = json.loads((bundle / "run.json").read_text())
    steps = _jsonl(bundle / "steps.jsonl")
    findings = json.loads((bundle / "findings.json").read_text())["findings"] \
        if (bundle / "findings.json").exists() else []
    out = Path(out) if out else LABELS_DIR / run["run_id"] / f"{coder}.yaml"
    if out.exists():
        raise FileExistsError(f"{out} exists; a coder's labels are never overwritten")

    lines = [f"# Labels by {coder} for {run['run_id']}. See src/pilot/coding.py for the format.",
             f"schema: {SCHEMA}",
             f"run_id: {json.dumps(run['run_id'])}",
             f"coder: {json.dumps(coder)}",
             "bundle:",
             f"  steps_sha256: {hashlib.sha256((bundle / 'steps.jsonl').read_bytes()).hexdigest()}",
             f"  stage_map_version: {json.dumps((run.get('stage_map') or {}).get('version'))}",
             "steps:"]
    for s in steps:
        about = [s["step_type"], s.get("agent_instance_id") or ""]
        if s["step_type"] == "oracle_run":
            about.append(f"CyberGym stage {(s.get('oracle_ref') or {}).get('stage')}: {s.get('output')}")
        lines.append(f"  {s['step_id']}:  # " + _one_line(" ".join(a for a in about if a)))
        if s.get("claim") and s["claim"].get("text"):
            lines.append(f"    # extracted claim: {_one_line(s['claim']['text'], 300)}")
        lines += ["    failure_modes: null",
                  "    first_decisive_error: false"]
        if s.get("stage") is None and len(s.get("stage_options") or []) > 1:
            lines.append(f"    stage: null  # choose one of {', '.join(s['stage_options'])}")
        else:
            lines.append(f"    stage: null  # assigned: {s.get('stage')}")
        lines += ["    claim: null", "    note: null"]
    lines.append("findings:" if findings else "findings: {}")
    for f in findings:
        loc = f.get("location") or {}
        check = f.get("tp_target_check") or {}
        lines += [f"  {f['finding_id']}:  # " + _one_line(
                      f"{loc.get('file')} {loc.get('function') or ''} lines {loc.get('line_start')}-"
                      f"{loc.get('line_end')}; tp_target check {check.get('result')} ({check.get('method')})"),
                  "    verdict: null  # tp_other | fp | unverified" + (
                      "  (already tp_target by the check)" if f.get("verdict") == "tp_target" else ""),
                  "    note: null"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return out


def load_labels(path: Path) -> dict:
    """A coder's label file, checked against the format."""
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: schema is {data.get('schema')!r}, expected {SCHEMA}")
    data.setdefault("steps", {})
    data.setdefault("findings", {})
    for step_id, label in data["steps"].items():
        label = label or {}
        modes = label.get("failure_modes")
        if modes is not None and not (isinstance(modes, list) and all(isinstance(m, str) for m in modes)):
            raise ValueError(f"{path}: {step_id}.failure_modes must be null or a list of codes")
        if label.get("stage") not in (None, *STAGES):
            raise ValueError(f"{path}: {step_id}.stage {label['stage']!r} is not one of S1-S8")
        data["steps"][step_id] = label
    for finding_id, label in data["findings"].items():
        label = label or {}
        if label.get("verdict") not in (None, *CODER_VERDICTS):
            raise ValueError(f"{path}: {finding_id}.verdict {label['verdict']!r} is not one of {CODER_VERDICTS}")
        data["findings"][finding_id] = label
    return data


def coded_steps(bundle: Path, labels_path: Path) -> list[dict]:
    """The bundle's steps with one coder's stage choices and claim corrections applied."""
    labels = load_labels(labels_path)
    steps = _jsonl(Path(bundle) / "steps.jsonl")
    for s in steps:
        label = labels["steps"].get(s["step_id"]) or {}
        if label.get("stage"):
            if label["stage"] not in (s.get("stage_options") or []):
                raise ValueError(f"{labels_path}: {s['step_id']} stage {label['stage']} is not one of "
                                 f"its role's stages {s.get('stage_options')}")
            s["stage"] = label["stage"]
        if label.get("claim"):
            s["claim"] = {"text": label["claim"], "source": "coder", "coder": labels.get("coder")}
    return steps


# --- agreement --------------------------------------------------------------------

def cohen_kappa(pairs: list[tuple]) -> dict:
    """Cohen's kappa over (first coder, second coder) category pairs.

    `kappa` is None when chance agreement is total (both coders used one and
    the same category throughout), where kappa is undefined.
    """
    n = len(pairs)
    if n == 0:
        return {"n": 0, "observed_agreement": None, "kappa": None}
    observed = sum(a == b for a, b in pairs) / n
    categories = {c for pair in pairs for c in pair}
    expected = sum((sum(a == c for a, _ in pairs) / n) * (sum(b == c for _, b in pairs) / n)
                   for c in categories)
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return {"n": n, "observed_agreement": observed, "kappa": kappa}


def kappa(first: Path, second: Path) -> dict:
    """Agreement between two coders' files for one run, per labeled item kind.

    - `verdict`: findings both coders gave a verdict.
    - `failure_mode:<code>`: presence of each code, over steps both coders coded
      (failure_modes not null).
    - `first_decisive_error`: over the same steps.
    - `stage`: steps where both chose a stage.
    """
    a, b = load_labels(first), load_labels(second)
    if a.get("run_id") != b.get("run_id"):
        raise ValueError(f"label files are for different runs: {a.get('run_id')} vs {b.get('run_id')}")
    result = {}

    shared = [k for k in a["findings"] if k in b["findings"]]
    result["verdict"] = cohen_kappa([(a["findings"][k]["verdict"], b["findings"][k]["verdict"])
                                     for k in shared
                                     if a["findings"][k].get("verdict") and b["findings"][k].get("verdict")])

    coded = [k for k in a["steps"] if k in b["steps"]
             and a["steps"][k].get("failure_modes") is not None
             and b["steps"][k].get("failure_modes") is not None]
    codes = sorted({m for k in coded for side in (a, b) for m in side["steps"][k]["failure_modes"]})
    for code in codes:
        result[f"failure_mode:{code}"] = cohen_kappa(
            [(code in a["steps"][k]["failure_modes"], code in b["steps"][k]["failure_modes"]) for k in coded])
    result["first_decisive_error"] = cohen_kappa(
        [(bool(a["steps"][k].get("first_decisive_error")), bool(b["steps"][k].get("first_decisive_error")))
         for k in coded])
    result["stage"] = cohen_kappa([(a["steps"][k]["stage"], b["steps"][k]["stage"]) for k in a["steps"]
                                   if k in b["steps"] and a["steps"][k].get("stage") and b["steps"][k].get("stage")])
    return result
