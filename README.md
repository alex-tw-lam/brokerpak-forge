# brokerpak-forge

A CLI for authoring [Cloud Service Broker](https://github.com/cloudfoundry/cloud-service-broker)
brokerpaks — start from provider binaries, end with a brokerpak.

## Install

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run forge --help
```

## Usage

Run inside a brokerpak directory (`./bin`, `manifest.yml`, `services/`,
`terraform/`):

```bash
forge manifest --write          # ./bin binaries -> manifest.yml
forge schema extract            # offline provider schema
forge schema list / show NAME   # discover resources and their arguments

forge service new csms          # wizard: resource -> parameters -> treatment
forge csms move <param> --to user|plan|computed|skipped
forge csms gen --write          # definition yml + variables.tf + bind example
forge csms scaffold             # stub the module logic files
forge check                     # schema + sync + drift gates

forge shell                     # REPL with TAB completion
```

`forge service import hcs-csms.yml` adopts an existing hand-written definition.
Every service in `services/` gets its own command group; `forge check` is the
CI gate.
