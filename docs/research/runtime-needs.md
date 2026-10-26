# Runtime needs of the six systems (input to D1)

Read from source at the commits in `docs/DECISIONS.md` (harness and VVAH: shallow
clones of HEAD on 2026-10-26). Nothing was installed or run. Paths are relative
to each upstream repo.

| | Mantis | Anthropic harness | AutoCVE | OpenAnt | deepsec | Visa VVAH |
|---|---|---|---|---|---|---|
| Runtime | Python ≥3.12, pip `.venv` | Python ≥3.11 + Docker; agents are `claude` CLI in containers | Python 3.12 FastAPI app, docker compose | Go ≥1.26 CLI + Python ≥3.11 venv (pip at first run) | Node ≥22 | Python ≥3.11; glibc ≥2.34 (embedded JVM graph DB) |
| Services | none (SQLite) | Docker, runsc, `vp-internal` network, `vp-egress-proxy` | Postgres, Redis, arq workers, frontend; mounts docker.sock | Docker for dynamic test only | none | none |
| Container-free mode | `static-only` (default; executes nothing) | none for the pipeline | none (sandbox tools error without Docker) | `--skip-dynamic-test` | default local `process` | always |
| Builds/runs target | dynamic tiers only; guest image has no C toolchain | yes: host `docker build` of target Dockerfile → ASAN binary; finder and grader run it | sandbox image has clang/cmake, agent can compile and run | dynamic test only, in an LLM-written Docker image | no | no |
| Points at target via | `run.sh <path>` | `targets/<name>/` (Dockerfile + config.yaml) | DB project (zip, local dir, git) | `openant scan <path>` | repo root / config | `--repo <path>` |
| Outputs | `reference/knowledge.db`, SARIF | `results/<target>/<ts>/` | Postgres rows, workspaces dir | `~/.openant/projects/.../scans/` | `<repo>/.deepsec/data/<id>/` | `<repo>/security-scan/`, `~/.vvaharness/state/` |

## Per-system notes

- **Mantis.** `run.sh` uses `workflow.json` (`static-only`). It upgrades to
  `microsandbox` only if `/dev/kvm` is usable and an msb image is cached. gVisor
  needs `--sandbox gvisor`: `docker create --runtime=runsc --network=none`,
  4 GB / 2 CPU, image overridable (`gvisor_env.py:51`). Static-only cannot run
  PoCs, so the reproducer stage is inert. Default model is Vertex Gemini.
- **Anthropic harness.** It requires runsc installed through
  `scripts/setup_sandbox.sh` (sudo, edits `/etc/docker/daemon.json`). The agent
  image is gcc:14 + Claude Code 2.1.144 with only `/work` copied from the target
  image. Unverified: whether OSS-Fuzz binaries still run after that copy. The
  grader is itself a `claude -p` agent in a fresh container. A target needs
  `config.yaml` (`image_tag`, `github_url`, `commit`, `binary_path`, `source_root`).
- **AutoCVE.** No CLI entry point. Headless use needs custom glue: insert a
  Project (`local_directory`) and an AgentTask, then call `_execute_agent_task_impl`.
  The DB layer accepts SQLite. Unverified end to end. Sandbox defaults:
  network `none`, 512 MB, 60 s. Semgrep and npm audit switch to `bridge`.
- **OpenAnt.** C/C++ is supported via tree-sitter. With `--skip-dynamic-test`,
  agent tools are code-index only. The venv needs PyPI at first run, so pre-bake it.
- **deepsec.** The Claude route allows Bash under bubblewrap with no network
  restriction. `LANGUAGE_EXTENSIONS` has no C/C++. The default model route is
  the Vercel AI Gateway; use `--model-auth direct --ai-provider anthropic`.
- **VVAH.** Default backend is `deepagents` over the Anthropic API. Tools are
  read-only. S10 remediation edits source; stop with `--stop-after s9`.

## Fix-leak risks (outside information about the fix)

1. Harness `--novelty`: fetches upstream `git log <commit>..HEAD`. Keep it off.
2. `.git` history in the `/src` copy: Mantis `get_git_log`/`get_git_diff`, OpenAnt diff modes.
3. OpenAnt `patch --cve`: fetches NVD.
4. Egress outside the LLM allowlist: AutoCVE Semgrep, npm audit, GitHub API; deepsec Bash.

## Not confirmed

- OSS-Fuzz base-builder OS and glibc version. If it is Ubuntu 20.04 / glibc
  2.31, VVAH cannot run inside the task container, and Mantis needs its own Python 3.12.
- Harness `/work`-only copy with OSS-Fuzz binaries; Mantis gVisor with an
  OSS-Fuzz image; AutoCVE headless on SQLite; deepsec reviewing any C file.
- CyberGym-E2E runner and validator internals: that lookup was stopped by a
  safeguard before reporting.
