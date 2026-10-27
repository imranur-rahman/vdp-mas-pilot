# Run environment

Checked 2026-10-26 on this machine (`/home/irahman3`):

| Requirement | State |
|---|---|
| Linux x86_64 | yes (Linux 7.0, x86_64) |
| Default task image | base-builder: Ubuntu 24.04.3, glibc 2.39, Python 3.11 (pulled, 3.1 GB) |
| `/dev/kvm` | present, group `kvm` (user not a member) |
| Docker daemon | Docker CE 29.9.2; user in `docker` group (until re-login, use `sg docker -c`) |
| `vm.mmap_rnd_bits=28` | not set (needs `sudo sysctl -w vm.mmap_rnd_bits=28`) |
| Disk | ~69 GB free on `/`; OSS-Fuzz images are large, watch this |
| HF token for `sunblaze-ucb/cybergym-e2e` | not checked |
| git identity | not configured globally |
| Paper repo `~/Research/failure-modes-of-vuln-discovery-agents` | not on this machine |

## Setup checklist (for T1)

- [x] Install Docker Engine; add user to `docker` group
- [x] `vm.mmap_rnd_bits=28` persisted in `/etc/sysctl.d/60-cybergym-mmap.conf`
- [x] CyberGym-E2E pinned as submodule `vendor/cybergym-e2e` (d0ae0f0); `md4c/arvo_31332` data in `data/`
- [x] Paper repo cloned to `failure-mode-paper/` (git-ignored)
- [x] Proxy stack: `uv run pilot up` (LiteLLM, Postgres, Phoenix, gateway forwarder; Squid with empty allowlist)
- [x] Phoenix at http://127.0.0.1:6006
