# Runner image for CyberGym's claude-code agent (ticket 01) placed in a runner
# container (ticket 03). Same Claude Code version CyberGym installs.
# Built with pilot.isolation.build_image (registry allowlist, logged).
FROM node:22-bookworm-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git ca-certificates curl >/dev/null \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code@2.1.91 >/dev/null 2>&1 && claude --version
RUN useradd -m agent && mkdir -p /src /output && chown agent /src /output
USER agent
