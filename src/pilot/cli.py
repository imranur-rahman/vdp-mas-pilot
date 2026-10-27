"""`pilot` command line. Needs Docker access: run as `sg docker -c "uv run pilot ..."`
until your login session picks up the docker group."""

import argparse

from pathlib import Path

from . import exporter, infra, runner


def main():
    p = argparse.ArgumentParser(prog="pilot")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="start the firewall and the proxy stack")
    up.set_defaults(fn=lambda a: print("proxy:", infra.ensure_proxy(infra.ensure_firewall())))

    run = sub.add_parser("run", help="run one system on one task")
    run.add_argument("system", choices=["claude-code"])
    run.add_argument("task", help="CyberGym-E2E task, e.g. md4c/arvo_31332")
    run.add_argument("--kind", choices=["dev", "data"], required=True)
    run.add_argument("--budget", type=float, help="hard cap in USD (default: dev 2, data 20)")
    run.add_argument("--timeout", type=int, default=5400, help="agent timeout in seconds")
    run.set_defaults(fn=lambda a: print("run folder:", runner.run_claude_code(a.task, a.kind, a.budget, a.timeout)))

    exp = sub.add_parser("export", help="build the trajectory bundle for a run folder (no Docker needed)")
    exp.add_argument("run_dir", type=Path)
    exp.set_defaults(fn=lambda a: print("bundle:", exporter.export(a.run_dir)))

    args = p.parse_args()
    args.fn(args)
