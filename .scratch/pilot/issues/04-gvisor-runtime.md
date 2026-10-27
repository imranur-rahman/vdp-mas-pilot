# 04: gVisor runtime on the host

**What to build:** `runsc` is installed and registered as a Docker runtime on the host, so that Mantis's gVisor tier and the Anthropic harness can run their sandboxes. Verified with a test container. No model spend.

**Blocked by:** None (can start immediately)

**Status:** done

- [ ] `runsc` installed at a pinned version and registered in Docker's daemon configuration
- [ ] A container started with `--runtime=runsc --network=none` runs and is visible in the container-event log
- [ ] The installation steps are recorded in `docs/ENVIRONMENT.md`

## Comments

2026-10-27: Done on integration branch `integration/pilot` (head bd76bb2). Results, spend and remaining gaps are in `docs/pilot-report.md`; code-review fixes landed in `review/fixes`.
