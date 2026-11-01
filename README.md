# brokerpak-forge

A CLI for authoring [Cloud Service Broker](https://github.com/cloudfoundry/cloud-service-broker)
brokerpaks — **start from provider binaries, end with a brokerpak**.

It removes the three pains of brokerpak work:

- **Not knowing what the provider offers** → offline schema queries with
  required/optional/computed/sensitive flags per argument.
- **Hand-writing parallel descriptions of one interface** → one small
  `services/<svc>.yaml` spec generates the definition yml, both `variables.tf`
  and the bind example.
- **Inputs landing in the wrong category** → `service move` reclassifies any
  input between `user`, `plan`, `computed` (platform input) and `skipped`
  freely, then regenerates.

## The flow

```
 ./bin (staged release binaries)
   │
   ▼
 forge manifest --write            → manifest.yml
   │
   ▼
 forge schema extract              → schema/*.json (offline mirror, no network)
 forge schema list / show          → pick a resource, read its arguments
   │
   ▼
 forge service new csms            → services/csms.yaml (draft, classified)
 forge service move csms name --to plan
 forge service move csms name --to computed --default '${instance.details["name"]}'
   │
   ▼
 forge service gen csms --write    → hcs-csms.yml + variables.tf ×2 + bind example
```

## Shell mode & completion

```bash
forge shell
```

A REPL with **TAB completion that knows the context**: after `service move ` it
completes your service names, after `service move csms ` the fields of that
spec (user, plan, computed and skipped alike), after `--to ` the four
categories, and after `schema show ` every resource in the extracted schema.
History persists in `~/.forge_history`.

For OS-level completion of the CLI itself, typer ships it:

```bash
forge --install-completion   # bash / zsh / fish; restart the shell
```

## Install & use

```bash
pip install typer pydantic pyyaml          # or: pip install -e .
cd your-brokerpak-dir
forge manifest --bin-dir ./bin --name my-services --org huaweicloud --write
forge schema extract
forge service new myservice
forge service gen myservice --write
```

Every generated file is idempotent — `forge service gen <svc> --check` is a CI
drift gate. Still hand-written (service logic): `main.tf`, `data.tf`,
`outputs.tf`, `provider.tf`, `versions.tf`.

## Input categories

| Category | Where it lands | Set by |
|---|---|---|
| `user` | provision `user_inputs` — per ServiceInstance parameter | the instance creator |
| `plan` | provision `plan_inputs` — fixed by the chosen plan | the operator's plan definitions |
| `computed` (platform) | `computed_inputs` — HIL expression, e.g. `${instance.details["id"]}` or `${config("hcs.region")}` | the broker |
| `skipped` | not exposed; a module-internal default in `main.tf` | nobody — that's the point |

`forge service move <svc> <field> --to user|plan|computed|skipped` moves any
field between categories; `--default` supplies the HIL expression when moving to
`computed`.

## Conventions

- Working directory is the brokerpak repo root (`manifest.yml`, `services/`,
  `terraform/`, `examples/` live there).
- `forge.yaml` (optional) overrides the defaults: `provider`, `provider_source`,
  `definition_prefix`, `standard_inputs`.
- The five bind patterns (passthrough / mint / grant / register / read-back) are
  a property of the spec — see the companion docs in the brokerpak repos.

## Validation

CSB enforces the yml formats via Go structs at pak build time — late and terse.
forge vendors equivalent JSON Schemas (derived from those structs, hardened
against the official AWS/Azure/GCP brokerpak definitions):

    forge validate                      # manifest + service definitions in cwd
    forge validate path/to/file.yml     # explicit files

Validation also runs automatically when generating (`manifest --write`,
`service gen --write`), so invalid files cannot be produced.
