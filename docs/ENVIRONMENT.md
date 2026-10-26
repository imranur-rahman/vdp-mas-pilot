# Run environment

Checked 2026-10-26 on this machine (`/home/irahman3`):

| Requirement | State |
|---|---|
| Linux x86_64 | yes (Linux 7.0, x86_64) |
| Docker daemon | Docker CE 29.9.2 installed 2026-10-26 (official repo); user in `docker` group (new login needed) |
| `vm.mmap_rnd_bits=28` | not set (needs `sudo sysctl -w vm.mmap_rnd_bits=28`) |
| Disk | ~69 GB free on `/`; OSS-Fuzz images are large, watch this |
| HF token for `sunblaze-ucb/cybergym-e2e` | not checked |
| git identity | not configured globally |
| Paper repo `~/Research/failure-modes-of-vuln-discovery-agents` | not on this machine |

## Setup checklist (for T1)

- [x] Install Docker Engine; add user to `docker` group
- [ ] `sudo sysctl -w vm.mmap_rnd_bits=28` (persist in `/etc/sysctl.d/`)
- [ ] Clone `sunblaze-ucb/cybergym-e2e`; set `HF_TOKEN`; fetch `curl/arvo_66012`
- [ ] Clone the paper repo (read-only reference for `sections/method.tex`)
- [ ] LiteLLM proxy on the host, reachable from `cybergym-internal` via the host gateway
- [ ] Trace store (per D3)
