# SOP — zero to a validated brokerpak service

The operator's journey, start to finish. Each step names the exact command;
every command exists. Phases 0–2 happen once per pak/provider; phase 3 is the
repeatable loop; phase 4 hands off to CSB.

## Phase 0 — one-off setup

```
0.1  ./bin staged with the release binaries:
       bin/tofu_<ver>_<os>_<arch>/tofu
       bin/terraform-provider-<name>_<ver>_<os>_<arch>/terraform-provider-<name>_v<ver>
0.2   (optional) forge.yaml — provider, provider_source, definition_prefix,
       standard_inputs, env_config_mapping defaults
```

## Phase 1 — the pak manifest (once per pak)

```
1.1  forge manifest --write          # bin/ -> manifest.yml (+ validation)
1.2  forge validate manifest.yml     # JSON Schema gate (or `forge check` later)
```

Later re-runs merge: staged binaries refresh/add, hand edits survive
(`required_env_vars`, upgrade paths, extra platforms, metadata…).

## Phase 2 — provider knowledge (once per provider version)

```
2.1  forge schema extract            # offline, every provider in the manifest
2.2  forge schema list <pattern>     # what resources exist
     forge schema list <pattern> -d  # what data sources exist
2.3  forge schema show <name> [-d]   # every argument with R/O/C/S flags
```

## Phase 3 — author a service (the repeatable loop)

```
3.1  forge service new <name>        # wizard: resource -> parameter table
                                     #         -> classify each (Enter = suggested)
3.2  forge <name>                    # where am I? resource + every parameter's treatment
3.3  forge <name> move <param> --to user|plan|computed|skipped
3.4  forge <name> gen --write        # hcs-<name>.yml + variables.tf x2 + bind example
3.5  forge <name> scaffold           # main/data/provider/versions/outputs stubs
                                     # (never overwrites; tofu fmt applied)
3.6  fill the TODOs in main.tf / outputs.tf   # the human part: logic + values
3.7  forge check                     # THE gate (see below)
```

`forge check` runs three gates at once:

| Gate | Catches |
|---|---|
| JSON Schema + rules | malformed yml; exposed optional without a default |
| yml ↔ module sync | input with no variable (or reverse), type mismatches, outputs declared on one side only, computed `instance.details[...]` refs that are not provision outputs |
| generation drift | generated files hand-edited instead of the spec |

## Phase 4 — hand-off to CSB

```
4.1  pak build && pak validate       # inside the brokerpak repo (Go toolchain)
4.2  iterate: edit services/<name>.yaml or `move` -> `gen --write` -> `forge check`
```

## Adopting existing hand-written services

```
forge service import hcs-<name>.yml --name <name>   # yml -> spec
forge <name> gen --write                            # now in the loop
```

## Troubleshooting quick map

- `not an argument of <resource>` WARN → rename; set `maps_to` in the spec
- SYNC `computed … not a provision output` → declare the output, or move the input back
- DRIFT on gen → never hand-edit generated files; edit the spec and regenerate
- optional without default WARN → add `default: null` (or a value) in the spec
