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
from . import check as check_mod
from . import scaffold as scaffold_mod
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
    name: Optional[str] = typer.Option(None, "--name", help="only applied when set (existing value kept otherwise)"),
    org: Optional[str] = typer.Option(None, "--org", help="github org of the providers, e.g. huaweicloud"),
    author: Optional[str] = typer.Option(None, "--author"),
    description: Optional[str] = typer.Option(None, "--description"),
    write: bool = typer.Option(False, "--write"),
) -> None:
    """Create manifest.yml from staged binaries, or merge into the existing one
    (hand-edited fields and unknown binary entries are preserved)."""
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
    attrs = s.attributes("resource", resource)

    # the parameters first, R/O/C/S and the suggested treatment
    typer.echo(f"\n{resource} parameters:")
    typer.echo(f"  {'argument':42s} {'type':14s} {'flags':8s} suggested")
    for arg, meta in sorted(attrs.items()):
        flags = "".join(f for f, k in [("R", "required"), ("O", "optional"),
                                       ("C", "computed"), ("S", "sensitive")] if meta.get(k))
        t = meta.get("type", "?")
        t = "".join(x for x in t if isinstance(x, str)) if isinstance(t, list) else t
        if arg == "id" or (meta.get("computed") and not meta.get("optional")):
            suggested = "output"
        elif arg in proj.standard_inputs:
            suggested = "platform"
        else:
            suggested = spec_mod.default_treatment(arg, meta)
        typer.echo(f"  {arg:42s} {str(t):14s} {flags or '-':8s} {suggested}")

    treatments: dict[str, str] = {}
    if not yes:
        for arg in sorted(attrs):
            meta = attrs[arg]
            if arg == "id" or (meta.get("computed") and not meta.get("optional")):
                continue
            if arg in proj.standard_inputs:
                continue
            treatments[arg] = typer.prompt(
                f"  {arg} -> user/plan/computed/skipped",
                default=spec_mod.default_treatment(arg, meta))

    spec = spec_mod.draft(service, resource, attrs, proj.standard_inputs,
                          treatments=treatments)
    path = spec_mod.save(spec)
    typer.echo(f"wrote {path}")
    typer.echo(f"inputs:      {', '.join(f'{k} (plan)' if v.plan_input else k for k, v in spec.inputs.items()) or '(none)'}")
    typer.echo(f"computed:    {', '.join(c.name for c in spec.bind.computed) or '(none)'}")
    typer.echo(f"skipped:     {', '.join(spec.skipped_inputs) or '(none)'}")
    typer.echo(f"outputs:     {', '.join(o.field_name for o in spec.outputs) or '(none)'}")
    typer.echo(f"\nnext: forge {service} move <param> --to user|plan|computed|skipped")
    typer.echo(f"      forge {service} gen --write")


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


@service_app.command("import")
def service_import(
    path: pathlib.Path = typer.Argument(..., help="existing definition yml, e.g. hcs-csms.yml"),
    name: Optional[str] = typer.Option(None, "--name", help="service name override (default: derived from the definition)"),
) -> None:
    """Adopt an existing hand-written definition as a spec, then keep editing
    via move/gen instead of the yml itself."""
    import yaml as _yaml
    from .project import load as load_project
    from .spec import import_definition
    spec = import_definition(_yaml.safe_load(path.read_text()) or {}, load_project())
    if name:
        spec.service = name
    out = spec_mod.save(spec)
    typer.echo(f"wrote {out} (resource unknown — set it, or add maps_to for renames)")
    typer.echo(f"inputs:   {', '.join(f'{k} (plan)' if v.plan_input else k for k, v in spec.inputs.items()) or '(none)'}")
    typer.echo(f"computed: {', '.join(c.name for c in spec.bind.computed) or '(none)'}")
    typer.echo(f"next: forge service gen {spec.service} --write")


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
def check() -> None:
    """One gate: yml schema validity + yml/module sync + generation drift."""
    raise typer.Exit(check_mod.run())


@app.command()
def shell() -> None:
    """Interactive REPL with TAB completion (commands, services, fields, resources)."""
    shell_mod.run(lambda argv: app(argv, standalone_mode=False))


# --------------------------------------------------------------------------
# service-first hierarchy: forge <service> <action> — tool → service →
# parameter → treatment, mirroring how the work is thought about.
RESERVED = {"manifest", "schema", "service", "validate", "shell", "help", "exit", "quit"}


def _svc_show(service: str) -> None:
    spec = spec_mod.load(service)
    typer.echo(f"resource: {spec.resource or '(unset)'}   bind: {spec.bind.pattern}")
    for k, v in spec.inputs.items():
        kind = "plan" if v.plan_input else "user"
        typer.echo(f"  {kind:8s} {k}")
    for c in spec.bind.computed:
        typer.echo(f"  {'computed':8s} {c.name} = {c.default}")
    if spec.skipped_inputs:
        typer.echo(f"  skipped   {', '.join(spec.skipped_inputs)}")


def _svc_move(service: str, field: str, to: str, default: Optional[str]) -> None:
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


def register_service_apps() -> None:
    services_dir = pathlib.Path("services")
    if not services_dir.exists():
        return
    for f in sorted(services_dir.glob("*.yaml")):
        name = f.stem
        if name in RESERVED:
            continue

        svc = typer.Typer(add_completion=False, no_args_is_help=True,
                          help=f"the {name} service (spec: services/{name}.yaml)")

        def build(nm: str):
            @svc.command()
            def show() -> None:
                """Resource, parameters and their current treatment."""
                _svc_show(nm)

            @svc.command()
            def move(field: str = typer.Argument(..., help="a parameter of the service"),
                     to: str = typer.Option(..., "--to", help="user | plan | computed | skipped"),
                     default: Optional[str] = typer.Option(
                         None, "--default", help="computed expression, e.g. ${instance.details[id]}")) -> None:
                """Reclassify one parameter."""
                _svc_move(nm, field, to, default)

            @svc.command()
            def gen(write: bool = typer.Option(False, "--write"),
                    check: bool = typer.Option(False, "--check")) -> None:
                """Regenerate the definition and interfaces."""
                raise typer.Exit(generate.run(spec_mod.load(nm), write=write, check=check))

            @svc.command()
            def scaffold(force: bool = typer.Option(False, "--force", help="overwrite existing files")) -> None:
                """Stub the hand-written logic files (main/provider/versions/data/outputs)."""
                raise typer.Exit(scaffold_mod.run(nm, force))

        build(name)
        app.add_typer(svc, name=name)


register_service_apps()


def main() -> None:
    app()
