#!/usr/bin/env bash
# Writes .env (git-ignored) with the pilot's secrets. Prompts without echo;
# generates the proxy's own master key and database password.
set -euo pipefail
cd "$(dirname "$0")/.."

read -rsp "OpenRouter API key: " openrouter; echo
read -rsp "Hugging Face token: " hf; echo

umask 077
cat > .env <<EOF
OPENROUTER_API_KEY=${openrouter}
HF_TOKEN=${hf}
LITELLM_MASTER_KEY=sk-pilot-$(openssl rand -hex 24)
POSTGRES_PASSWORD=$(openssl rand -hex 24)
EOF
echo "Wrote $(pwd)/.env (mode 600)."
