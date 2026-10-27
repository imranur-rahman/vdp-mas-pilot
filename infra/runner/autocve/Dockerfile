# Runner image for AutoCVE (ticket 10): its backend at the pinned commit with
# the pilot's patches applied (systems/autocve/patches), run headless on SQLite
# by autocve_start.py. The build context is assembled by
# pilot.adapters.autocve.runner_context (AutoCVE's backend/, tiktoken's
# cl100k_base file, the start script) and built with
# pilot.isolation.build_image (registry allowlist, logged).
#
# Follows AutoCVE's own backend/Dockerfile (Python 3.12, requirements-lock.txt,
# editable install) minus the Postgres client and the CJK fonts, plus aiosqlite
# (SQLite driver, P3(c)). No web UI, Redis, or Postgres.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DEFAULT_TIMEOUT=120
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      gcc libffi-dev git ripgrep curl ca-certificates \
      libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
      libglib2.0-0 shared-mime-info >/dev/null \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY autocve/backend/pyproject.toml autocve/backend/requirements-lock.txt autocve/backend/README.md ./
RUN python -m venv /app/.venv \
 && /app/.venv/bin/pip install -q --upgrade pip setuptools wheel \
 && /app/.venv/bin/pip install -q --retries 5 -r requirements-lock.txt \
 && /app/.venv/bin/pip install -q --retries 5 "aiosqlite==0.21.0"
COPY autocve/backend/ /app/
RUN /app/.venv/bin/pip install -q --no-deps -e . && /app/.venv/bin/python -c "import app.main"

# tiktoken would download its encoding at first use; the runner has no internet.
COPY tiktoken/ /opt/tiktoken/
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken LITELLM_LOCAL_MODEL_COST_MAP=True
COPY autocve_start.py /opt/pilot/autocve_start.py
