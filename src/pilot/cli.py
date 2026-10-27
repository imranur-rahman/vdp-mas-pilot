"""`pilot` command line. Needs Docker access: run as `sg docker -c "uv run pilot ..."`
until your login session picks up the docker group."""

import argparse
import json
from pathlib import Path

from . import claims, coding, deepsec_run, exporter, infra, isolation, runner
from .adapters import harness_run


RUNS = {"claude-code": runner.run_claude_code, "claude-code-runner": runner.run_claude_code_in_runner,
        "deepsec": deepsec_run.run_deepsec, "anthropic-harness": harness_run.run}


def _run_vvah(*args):
    from .adapters import vvah
    return vvah.run(*args)


RUNS["visa-vvah"] = _run_vvah


def _autocve(*a):
    from .adapters import autocve
    return autocve.run(*a)


RUNS["autocve"] = _autocve


def main():
    p = argparse.ArgumentParser(prog="pilot")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="start the firewall and the proxy stack")
    up.set_defaults(fn=lambda a: print("proxy:", infra.ensure_proxy(infra.ensure_firewall())))

    probe = sub.add_parser("probe", help="check what a runner container can reach (expected: only LiteLLM)")
    probe.add_argument("--image", default="pilot-runner-claude-code:2.1.91")
    probe.set_defaults(fn=lambda a: print(json.dumps(
        isolation.probe_runner_network(infra.ensure_firewall(), a.image), indent=2)))

    run = sub.add_parser("run", help="run one system on one task")
    run.add_argument("system", choices=sorted(RUNS),
                     help="claude-code / claude-code-runner / visa-vvah / deepsec in/near the task container;"
                          " anthropic-harness: on the host, agents in gVisor on cybergym-internal;"
                          " autocve: runner container on cybergym-internal")
    run.add_argument("task", help="CyberGym-E2E task, e.g. md4c/arvo_31332")
    run.add_argument("--kind", choices=["dev", "data"], required=True)
    run.add_argument("--budget", type=float, help="hard cap in USD (default: dev 2, data 20)")
    run.add_argument("--timeout", type=int, default=5400, help="agent timeout in seconds")
    run.set_defaults(fn=lambda a: print("run folder:", RUNS[a.system](a.task, a.kind, a.budget, a.timeout)))

    exp = sub.add_parser("export", help="build the trajectory bundle for a run folder (no Docker needed)")
    exp.add_argument("run_dir", type=Path)
    exp.set_defaults(fn=lambda a: print("bundle:", exporter.export(a.run_dir)))

    cl = sub.add_parser("claims", help="extract step claims (Haiku 5.5 through the proxy, T-3) and re-export")
    cl.add_argument("run_dir", type=Path)
    cl.add_argument("--budget", type=float, default=1.0, help="hard cap in USD for the extraction key")
    cl.set_defaults(fn=lambda a: print("bundle:", _export_with_claims(a.run_dir, a.budget)))

    lab = sub.add_parser("labels", help="write a coder's label file for a bundle (coding kit)")
    lab.add_argument("bundle", type=Path)
    lab.add_argument("--coder", required=True)
    lab.add_argument("--out", type=Path, help=f"default: {coding.LABELS_DIR}/<run_id>/<coder>.yaml")
    lab.set_defaults(fn=lambda a: print("label file:", coding.init_labels(a.bundle, a.coder, a.out)))

    kap = sub.add_parser("kappa", help="Cohen's kappa between two coders' label files")
    kap.add_argument("first", type=Path)
    kap.add_argument("second", type=Path)
    kap.set_defaults(fn=lambda a: print(json.dumps(coding.kappa(a.first, a.second), indent=2)))

    args = p.parse_args()
    args.fn(args)


def _export_with_claims(run_dir: Path, budget: float) -> Path:
    """Live claim extraction on a `tooling` key. Its proxy log goes to
    `<run folder>/claim-extraction/proxy.jsonl`, apart from the run's own log."""
    run_id = json.loads((run_dir / "run.json").read_text())["run_id"]
    key = infra.mint_key(run_id=f"{run_id}/claim-extraction", kind="tooling", system="pilot-claims",
                         budget_usd=budget, role="claim-extractor")
    extractor = claims.proxy_extractor(f"http://127.0.0.1:{infra.PROXY_PORT}", key)
    return exporter.export(run_dir, extractor=extractor)
