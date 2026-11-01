"""forge — start from provider binaries, end with a brokerpak.

  forge manifest --bin-dir ./bin --write      # binaries -> manifest.yml
  forge schema extract                        # offline provider schema
  forge schema list / show                    # discover resources and arguments
  forge service new NAME                      # pick a resource, draft a spec
  forge service move SVC FIELD --to plan      # user <-> plan <-> computed <-> skipped
  forge service gen SVC --write               # spec -> definition + variables.tf
"""
import pathlib
from typing import Optional

import typer
import yaml

from . import generate, manifest as manifest_mod, project as project_mod, schema as schema_mod, shell as shell_mod, spec as spec_mod
from . import validate as validate_mod

app = typer.Typer(no_args_is_help=True,
                  help="brokerpak authoring: binaries in, brokerpak out. Try `forge shell`.")
schema_app = typer.Typer(add_completion=False, no_args_is_help=True, help="provider schema (S6)")
service_app = typer.Typer(add_completion=False, no_args_is_help=True, help="service specs (S1)")
app.add_typer(schema_app, name="schema")
app.add_typer(service_app, name="service")


# ------------------------------------------------------------------ manifest

@app.command()
def manifest(
    bin_dir: pathlib.Path = typer.Option(pathlib.Path("bin"), "--bin-dir"),
    name: str = typer.Option("hcs-services", "--name"),
    org: Optional[str] = typer.Option(None, "--org", help="github org of the providers, e.g. huaweicloud"),
    author: str = typer.Option("CHANGE_ME", "--author"),
    description: str = typer.Option("Cloud Service Broker pak", "--description"),
    write: bool = typer.Option(False, "--write"),
) -> None:
    """Fill in manifest.yml from a directory of staged release binaries."""
    manifest_mod.run(bin_dir, name, org, author, description, write)
    if write:
        from .validate import validate_file
        errors, _ = validate_file(pathlib.Path("manifest.yml"), "brokerpak-manifest.schema.json")
        for e in errors:
            typer.echo(f"FAIL {e}", err=True)


# -------------------------------------------------------------------- schema

@schema_app.command("extract")
def schema_extract(bin_dir: Optional[pathlib.Path] = typer.Option(None, "--bin-dir")) -> None:
    """Extract every provider schema offline (filesystem mirror, no network)."""
    for out in schema_mod.extract(bin_dir):
        typer.echo(f"wrote {out}")


@schema_app.command("list")
def schema_list(pattern: Optional[str] = typer.Argument(None),
                data: bool = typer.Option(False, "-d", "--data-sources")) -> None:
    s = schema_mod.Schema()
    for n in (s.data_sources(pattern) if data else s.resources(pattern)):
        typer.echo(n)


@schema_app.command("show")
def schema_show(name: str = typer.Argument(...),
                data: bool = typer.Option(False, "-d", "--data-source")) -> None:
    s = schema_mod.Schema()
    kind = "data_source" if data else "resource"
    typer.echo(f"{name} ({kind})")
    typer.echo(schema_mod.format_attributes(s.attributes(kind, name)))


# ------------------------------------------------------------------- service

@service_app.command("new")
def service_new(
    service: str = typer.Argument(..., help="short service name, e.g. csms"),
    resource: Optional[str] = typer.Option(None, "--resource", help="skip the interactive picker"),
    yes: bool = typer.Option(False, "--yes", help="accept all defaults, no prompts"),
) -> None:
    """Draft a service spec by picking a resource from the provider schema."""
    s = schema_mod.Schema()
    if resource is None:
        pattern = "" if yes else typer.prompt("Filter resources by substring (enter for all)", default="")
        candidates = s.resources(pattern or None)
        if not candidates:
            typer.echo("no resources match")
            raise typer.Exit(1)
        for i, n in enumerate(candidates, 1):
            typer.echo(f"{i:4d}  {n}")
        pick = typer.prompt("Pick a resource number", type=int)
        resource = candidates[pick - 1]

    proj = project_mod.load()
    spec = spec_mod.draft(service, resource, s.attributes("resource", resource),
                          proj.standard_inputs)
    path = spec_mod.save(spec)
    typer.echo(f"wrote {path}")
    typer.echo(f"inputs:      {', '.join(spec.inputs) or '(none)'}")
    typer.echo(f"skipped:     {', '.join(spec.skipped_inputs) or '(none)'}")
    typer.echo(f"outputs:     {', '.join(o.field_name for o in spec.outputs) or '(none)'}")
    typer.echo(f"\nnext: forge service move {service} <field> --to user|plan|computed")
    typer.echo(f"      forge service gen {service} --write")


@service_app.command("move")
def service_move(
    service: str = typer.Argument(...),
    field: str = typer.Argument(...),
    to: str = typer.Option(..., "--to", help="user | plan | computed | skipped"),
    default: Optional[str] = typer.Option(
        None, "--default", help='computed expression, e.g. \'${instance.details["id"]}\''),
) -> None:
    """Reclassify an input: user <-> plan <-> computed (platform) <-> skipped."""
    spec = spec_mod.load(service)
    try:
        spec = spec_mod.move(spec, field, to, default)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    spec_mod.save(spec)
    typer.echo(f"{service}.{field} -> {to}")
    typer.echo(f"inputs:   {', '.join(f'{k} (plan)' if v.plan_input else k for k, v in spec.inputs.items()) or '(none)'}")
    typer.echo(f"computed: {', '.join(c.name for c in spec.bind.computed) or '(none)'}")
    typer.echo(f"skipped:  {', '.join(spec.skipped_inputs) or '(none)'}")


@service_app.command("show")
def service_show(service: str = typer.Argument(...)) -> None:
    spec = spec_mod.load(service)
    typer.echo(f"resource: {spec.resource}   bind: {spec.bind.pattern}")
    for k, v in spec.inputs.items():
        kind = "plan" if v.plan_input else "user"
        typer.echo(f"  {kind:8s} {k}")
    for c in spec.bind.computed:
        typer.echo(f"  {'computed':8s} {c.name} = {c.default}")
    if spec.skipped_inputs:
        typer.echo(f"  skipped   {', '.join(spec.skipped_inputs)}")


@service_app.command("gen")
def service_gen(
    service: str = typer.Argument(...),
    write: bool = typer.Option(False, "--write"),
    check: bool = typer.Option(False, "--check"),
) -> None:
    """Generate the definition yml, both variables.tf and the bind example."""
    spec = spec_mod.load(service)
    if write or check:
        from .validate import validate_document
        from .project import load as load_project
        doc = yaml.safe_load(generate.gen_definition(spec, load_project()))
        errs = validate_document(doc, "service-definition.schema.json", f"{service} (generated)")
        errs += validate_mod.check_optional_defaults(doc, f"{service} (generated)")
        for e in errs:
            typer.echo(f"FAIL {e}", err=True)
            raise typer.Exit(1)
    raise typer.Exit(generate.run(spec, write=write, check=check))


@app.command()
def validate(paths: list[pathlib.Path] = typer.Argument(None, help="yml files; default: manifest + hcs-*.yml in cwd")) -> None:
    """Validate manifests and service definitions against the vendored JSON Schemas."""
    raise typer.Exit(validate_mod.run(paths or []))


@app.command()
def shell() -> None:
    """Interactive REPL with TAB completion (commands, services, fields, resources)."""
    shell_mod.run(lambda argv: app(argv, standalone_mode=False))


def main() -> None:
    app()
