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
WORKDIR /src/openant/apps/openant-cli
RUN make build && test -x bin/openant

FROM python:3.11-slim-bookworm
ARG OPENANT_COMMIT
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends git ca-certificates curl diffutils build-essential >/dev/null \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/openant /opt/openant
COPY --from=build /src/openant/apps/openant-cli/bin/openant /usr/local/bin/openant
# Pre-build the managed venv OpenAnt expects at ~/.openant/venv: an editable
# install of the core library so no network is needed at run time.
ENV OPENANT_PYTHON=/root/.openant/venv/bin/python
RUN python3.11 -m venv /root/.openant/venv \
 && /root/.openant/venv/bin/pip install -q --upgrade pip \
 && /root/.openant/venv/bin/pip install -q -e /opt/openant/libs/openant-core \
 && /root/.openant/venv/bin/python -c "import tree_sitter_c, anthropic; print('core ok')"
RUN openant version || true
RUN mkdir -p /src /output /root/.config/openant
WORKDIR /src
