# VVAH patches (P3)

Every change to VVAH's code is a `*.patch` file here, applied in name order with
`git apply` to the clone at the pinned commit before install
(`src/pilot/adapters/vvah.py`, `install_script`).

None so far: VVAH 1.5.1 runs unmodified. The adapter changes only configuration:

- `config.local.yaml` overlay beside a copy of the packaged `default.yaml`: every
  model role set to the study model on the profile's own route (P3(d), P4).
- `--remediate`: S10 on, with the default profile's S10 settings.
