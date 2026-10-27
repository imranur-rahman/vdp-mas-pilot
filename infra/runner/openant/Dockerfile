# Runner image for OpenAnt (ticket 09), placed in a runner container (ticket 03).
# OpenAnt is a Go CLI (go 1.26) driving a Python 3.11+ core; the managed venv is
# an editable install of libs/openant-core (tree-sitter-c for the C parser).
#
# This image is the analysis tool itself, built once as setup (git clone + Go
# modules + PyPI). OpenAnt's own per-finding dynamic-test image builds happen
# DURING a run and are routed through the logged registry allowlist
# (pilot.isolation.build_image); on a C target they are skipped (no template),
# so this image needs no Docker daemon of its own.
ARG OPENANT_COMMIT=9143061

FROM golang:1.26-bookworm AS build
ARG OPENANT_COMMIT
RUN git clone -q https://github.com/knostic/OpenAnt /src/openant \
 && git -C /src/openant checkout -q ${OPENANT_COMMIT}
# Pilot patches (P3), kept in systems/openant/patches/ and copied here by the adapter.
COPY patches/ /tmp/patches/
RUN for p in /tmp/patches/*.patch; do git -C /src/openant apply "$p"; done
WORKDIR /src/openant/apps/openant-cli
RUN make build && test -x bin/openant

FROM python:3.11-slim-bookworm
ARG OPENANT_COMMIT
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends git ca-certificates curl diffutils build-essential >/dev/null \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/openant /opt/openant
# Symlinked, as the README installs it: the CLI finds config/languages.json by
# walking up from its resolved path.
RUN ln -s /opt/openant/apps/openant-cli/bin/openant /usr/local/bin/openant
# Pre-build the managed venv OpenAnt expects at ~/.openant/venv the way its
# CLI does (pinned requirements.txt, then an editable install of the core) so no network is needed at run time.
ENV OPENANT_PYTHON=/root/.openant/venv/bin/python
RUN python3.11 -m venv /root/.openant/venv \
 && /root/.openant/venv/bin/pip install -q --upgrade pip \
 && /root/.openant/venv/bin/pip install -q -r /opt/openant/libs/openant-core/requirements.txt \
 && /root/.openant/venv/bin/pip install -q -e /opt/openant/libs/openant-core \
 && /root/.openant/venv/bin/python -c "import tree_sitter_c, anthropic; print('core ok')"
# The CLI's staleness key (internal/python/runtime.go depsHash): sha256 of
# corePath NUL pyproject.toml requirements.txt. Writing it marks this venv as
# current, so no pip install runs inside the (network-less) run.
RUN cd /opt/openant/libs/openant-core && python3 -c "import hashlib,pathlib; c='/opt/openant/libs/openant-core'; h=hashlib.sha256(c.encode()+b'\\0'+pathlib.Path('pyproject.toml').read_bytes()+pathlib.Path('requirements.txt').read_bytes()).hexdigest(); pathlib.Path('/root/.openant/venv/.deps-hash').write_text(h+'\\n')"
RUN openant version
RUN mkdir -p /src /output /root/.config/openant
WORKDIR /src
