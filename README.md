# brokerpak-forge

A CLI for authoring [Cloud Service Broker](https://github.com/cloudfoundry/cloud-service-broker)
brokerpaks — start from provider binaries, end with a brokerpak.

## Install

Install as a standalone tool (from this public repo):

```bash
uv tool install git+https://github.com/alex-tw-lam/brokerpak-forge.git
# or: pipx install git+https://github.com/alex-tw-lam/brokerpak-forge.git
forge --help
```

For development, use [uv](https://docs.astral.sh/uv/): `uv sync` then `uv run forge …`.

## Usage

Run inside a brokerpak directory (`./bin`, `manifest.yml`, `services/`,
`terraform/`):

```bash
forge add ./bin/tofu_1.11.3_linux_amd64          # one binary at a time
forge add ./bin/terraform-provider-hcs_2.4.26_linux_amd64 --org hcs=huaweicloud
forge manifest --write          # or scan a whole ./bin directory
forge schema extract            # offline provider schema
forge schema list / show NAME   # discover resources and their arguments

forge service new csms          # wizard: resource -> parameters -> treatment
forge csms move <param> --to user|plan|computed|skipped
forge csms gen --write          # definition yml + variables.tf + bind example
forge csms scaffold             # stub the module logic files
forge check                     # schema + sync + drift gates

forge shell                     # REPL with TAB completion
forge tui                       # Textual cockpit (sidebar + scrolling log)
```

`forge service import hcs-csms.yml` adopts an existing hand-written definition.
Every service in `services/` gets its own command group; `forge check` is the
CI gate.
