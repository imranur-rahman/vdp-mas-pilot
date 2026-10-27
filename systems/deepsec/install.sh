#!/usr/bin/env bash
# Install deepsec in the CyberGym-E2E task container (install phase: the
# container is still on Docker's bridge network, like CyberGym's own agents).
#
# Inputs, copied in by pilot.deepsec_run:
#   /opt/deepsec-src.tar.gz   `git archive` of deepsec at the pinned commit
#   /opt/deepsec-patches/     our changes, applied in name order
# Env: DEEPSEC_TARGET (the project root, e.g. /src/md4c), DEEPSEC_ID,
#      DEEPSEC_MODEL, PILOT_BASE_URL (the LiteLLM proxy).
set -euo pipefail

# Node 22 (D1) and deepsec's own package manager (pnpm 8.15.9, package.json).
curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1
apt-get install -y -qq nodejs patch >/dev/null 2>&1
corepack enable
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
corepack prepare pnpm@8.15.9 --activate >/dev/null
node --version

# deepsec at the pinned commit, patched, bundled.
mkdir -p /opt/deepsec
tar xzf /opt/deepsec-src.tar.gz -C /opt/deepsec
cd /opt/deepsec
for p in /opt/deepsec-patches/*.patch; do
  [ -e "$p" ] || continue
  echo "applying $(basename "$p")"
  patch -p1 --forward < "$p"
done
pnpm install --frozen-lockfile --reporter=silent
(cd packages/deepsec && pnpm bundle >/dev/null)
test -f packages/deepsec/dist/cli.mjs

# The workspace (.deepsec/ next to the code, deepsec's default) with its
# dependencies installed now, while the network is open. A CLI run from a
# source checkout makes the workspace depend on that checkout (file:).
cd "$DEEPSEC_TARGET"
node /opt/deepsec/packages/deepsec/dist/cli.mjs init .deepsec . \
  --id "$DEEPSEC_ID" --through install --headless --no-tui --output jsonl \
  --package-manager pnpm \
  --agent claude --model "$DEEPSEC_MODEL" \
  --model-auth direct --ai-provider anthropic --ai-api-key-env PILOT_PROXY_KEY \
  --ai-base-url "$PILOT_BASE_URL"
test -f .deepsec/node_modules/deepsec/dist/cli.mjs
echo "deepsec installed"
