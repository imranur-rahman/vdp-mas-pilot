"""Role-to-stage map (T-5): stages are joined after the run, never recorded at run time.

The map is a versioned TOML file (`coding/role-stage-map.toml`), agreed by two
authors and revised without re-running anything:

    version = "2026-10-27.1"        # bump on every change; bundles record it
    [systems.<system>]
    <role> = ["S3"]                  # one stage: assigned automatically
    <role> = ["S3", "S4"]            # two (or more) stages: the coder chooses per step
    "prompt:<prompt_id>" = ["S5"]    # agent known only by its system-prompt hash

A step's role key is its `role`, or `agent` for a system that labels no roles
(one unnamed agent); a `prompt:<prompt_id>` entry is tried next.
"""

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_MAP = REPO / "coding" / "role-stage-map.toml"
STAGES = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
UNNAMED_ROLE = "agent"


@dataclass
class StageMap:
    version: str
    sha256: str
    path: str
    systems: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def stages_for(self, system: str, role: str | None, prompt_id: str | None) -> list[str] | None:
        roles = self.systems.get(system) or {}
        for key in (role or UNNAMED_ROLE, f"prompt:{prompt_id}" if prompt_id else None):
            if key in roles:
                return list(roles[key])
        return None


def load_map(path: Path) -> StageMap:
    path = Path(path)
    raw = path.read_bytes()
    data = tomllib.loads(raw.decode())
    if not data.get("version"):
        raise ValueError(f"{path}: role-stage map has no version")
    systems = {}
    for system, roles in (data.get("systems") or {}).items():
        systems[system] = {}
        for role, stages in roles.items():
            if not isinstance(stages, list) or not stages:
                raise ValueError(f"{path}: {system}.{role} must list at least one stage")
            unknown = [s for s in stages if s not in STAGES]
            if unknown:
                raise ValueError(f"{path}: {system}.{role} names unknown stage(s) {unknown}")
            systems[system][role] = stages
    return StageMap(version=str(data["version"]), sha256=hashlib.sha256(raw).hexdigest(),
                    path=str(path), systems=systems)


def join_stages(steps: list[dict], stage_map: StageMap) -> dict:
    """Set `stage` and `stage_options` on every step; return the bundle's map record.

    Only agent steps (model calls and refusals) get stages. A dual-stage role
    leaves `stage` empty for the coder, who picks one of `stage_options`.
    """
    unmapped = set()
    for s in steps:
        s["stage"], s["stage_options"] = None, []
        if s["step_type"] not in ("llm_step", "refusal"):
            continue
        options = stage_map.stages_for(s["system"], s.get("role"), s.get("prompt_id"))
        if options is None:
            unmapped.add(s.get("role") or UNNAMED_ROLE)
            continue
        s["stage_options"] = options
        s["stage"] = options[0] if len(options) == 1 else None
    return {"version": stage_map.version, "sha256": stage_map.sha256,
            "unmapped_roles": sorted(unmapped)}
