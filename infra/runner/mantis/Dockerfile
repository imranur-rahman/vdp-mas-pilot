# Runner image for Google Mantis (ticket 07), placed in a runner container on
# cybergym-internal (P7, D1). Mantis is pinned at f3162c0; its source is COPYd
# from the build context (the host clone the adapter prepares, with the pilot
# patches applied). Built with pilot.isolation.build_image (registry allowlist,
# logged). The container needs the host Docker socket at run time so Mantis's
# gVisor tier can start sibling `--runtime=runsc --network=none` sandboxes on
# the task image.
FROM python:3.12-slim

RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      git ca-certificates curl docker.io >/dev/null \
 && rm -rf /var/lib/apt/lists/*

COPY reference /opt/mantis/reference
COPY skills /opt/mantis/skills

RUN pip install --no-cache-dir -r /opt/mantis/reference/requirements.txt >/dev/null \
 && python3 -c "import google.adk, litellm; print('adk+litellm ok')"

WORKDIR /opt/mantis/reference
ENV PYTHONUNBUFFERED=1
