"""forge <svc> scaffold — stub the hand-written logic files from the schema.

Creates only what is missing (never overwrites): main.tf with the managed
resource mapping every exposed input, the tagging locals, provider.tf and
versions.tf from the manifest pins, and output skeletons. The TODOs mark the
places only a human can decide.
"""
import pathlib
import re
import shutil
import subprocess
import sys

import yaml

from .project import load as load_project
from .spec import ServiceSpec, load as load_spec

ROOT = pathlib.Path.cwd()


def _rel(p: pathlib.Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _versions_tf() -> str:
    man_path = ROOT / "manifest.yml"
    if not man_path.exists():
        sys.exit("no manifest.yml — run: forge manifest --write")
    man = yaml.safe_load(man_path.read_text())
    requires = []
    for b in man["terraform_binaries"]:
        if b["name"] == "tofu" or not b.get("provider"):
            continue
        requires.append(f'    {b["provider"].split("/")[1]} = '
                        f'{{ source = "registry.terraform.io/{b["provider"]}", version = "{b["version"]}" }}')
    return ("terraform {\n  required_providers {\n" + "\n".join(requires) + "\n  }\n}\n")


def _provider_tf(proj) -> str:
    args = "\n".join(f"  {n:<13} = var.{n}" for n in proj.standard_inputs)
    return f'provider "{proj.provider}" {{\n{args}\n}}\n'


def _data_tf() -> str:
    return '''locals {
  request_context = var.request_context_json != "" ? try(jsondecode(var.request_context_json), {}) : {}

  context_tags = {
    for key in ["namespace", "instance_name"] :
    key => try(local.request_context[key], null)
    if try(local.request_context[key], null) != null
  }

  originating_identity = var.originating_identity_json != "" ? try(jsondecode(var.originating_identity_json), {}) : {}

  identity_tags = {
    for key, expr in { created_by = try(local.originating_identity["username"], null) } :
    key => expr if expr != null
  }

  tags = merge(var.labels, local.context_tags, local.identity_tags)
}
'''


def _main_tf(spec: ServiceSpec) -> str:
    lines = [f'# TODO: the service logic. Every exposed input is mapped below;',
             '# fill in defaults, data sources and any extra resources.',
             f'resource "{spec.resource or "TODO_RESOURCE"}" "this" {{']
    for key, inp in spec.inputs.items():
        arg = inp.maps_to or key
        lines.append(f"  {arg:<28} = var.{key}")
    if spec.skipped_inputs:
        lines.append("")
        lines.append("  # module-internal defaults (skipped inputs) — set them here:")
        for arg in spec.skipped_inputs:
            lines.append(f"  # {arg:<26} = TODO")
    lines.append("")
    lines.append("  tags = local.tags")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _outputs_tf(spec: ServiceSpec) -> str:
    lines = ["# TODO: values for the declared outputs."]
    for o in spec.outputs:
        lines.append(f'output "{o.field_name}" {{')
        lines.append(f'  description = "{o.details or o.field_name}"')
        lines.append(f'  value       = TODO  # e.g. {spec.resource or "..."}.this.{o.field_name}')
        lines.append("}")
        lines.append("")
    for o in spec.bind_outputs:
        lines.append(f'# bind output "{o.field_name}" is produced in the bind module')
    return "\n".join(lines) + "\n"


def ensure_vars(service: str) -> int:
    """Append any variables the definition declares but the (adopted) module
    does not have — the platform inputs and the tagging trio."""
    import re
    from .generate import _tf_var_body
    from .spec import load as load_spec, provision_dir as prov_dir_of
    prov_dir = prov_dir_of(load_spec(service))
    var_file = prov_dir / "variables.tf"
    if not var_file.is_file():
        print(f"no {var_file} — nothing to ensure")
        return 0
    have = set(re.findall(r'^variable\s+"([^"]+)"', var_file.read_text(), re.M))
    want: list[tuple[str, str, str, str | None]] = [
        ("region", "string", "The platform region.", None),
        ("cloud", "string", "The platform cloud.", None),
        ("project_name", "string", "The platform project_name.", None),
        ("labels", "map(any)", "Labels to apply as tags.", None),
        ("request_context_json", "string", "JSON of the OSB request context.", '""'),
        ("originating_identity_json", "string", "JSON of the OSB originating identity.", '""'),
    ]
    missing = [w for w in want if w[0] not in have]
    if missing:
        with var_file.open("a") as f:
            f.write("\n# --- platform inputs (forge ensure-vars) ---\n")
            for name, typ, desc, default in missing:
                f.write(_tf_var_body(name, typ, desc, default) + "\n")
        print(f"appended to {_rel(var_file)}: {', '.join(m[0] for m in missing)}")
    else:
        print("all platform variables already declared")

    # echo outputs: the bind computed inputs pull the platform values from
    # instance.details, so an adopted module must output them
    from .project import load as load_project
    out_file = prov_dir / "outputs.tf"
    have_out = set(re.findall(r'^output\s+"([^"]+)"', out_file.read_text(), re.M)) if out_file.is_file() else set()
    missing_out = [n for n in load_project().standard_inputs if n not in have_out]
    if missing_out:
        with out_file.open("a") as f:
            f.write("\n# --- platform echoes (forge ensure-vars) ---\n")
            for n in missing_out:
                f.write(f'output "{n}" {{\n  value = var.{n}\n}}\n\n')
        print(f"appended to {_rel(out_file)}: {', '.join(missing_out)} outputs")
    return 0


def assemble(service: str) -> int:
    """Copy an adopted module into the brokerpak layout, patch it, forget the
    external pointer — after this the pak copy is canonical."""
    import shutil
    from .spec import _tilde, load as load_spec, provision_dir, bind_dir_of
    spec = load_spec(service)
    if not spec.module_dir:
        die(f"{service}: no module_dir in the spec — nothing to assemble")
    src_prov = provision_dir(spec)
    src_bind = bind_dir_of(spec)
    dst_prov = ROOT / "terraform" / service / "provision"
    dst_bind = ROOT / "terraform" / service / "bind"
    if not src_prov.is_dir():
        die(f"module not found at {src_prov} — re-adopt or fix module_dir")
    shutil.rmtree(dst_prov, ignore_errors=True)
    shutil.copytree(src_prov, dst_prov)
    print(f"copied {src_prov} -> {dst_prov.relative_to(ROOT)}")
    if src_bind.is_dir():
        shutil.rmtree(dst_bind, ignore_errors=True)
        shutil.copytree(src_bind, dst_bind)
        print(f"copied {src_bind} -> {dst_bind.relative_to(ROOT)}")
    spec.module_dir = None
    spec.bind_dir = None
    from .spec import save
    save(spec)
    print("scrubbed module_dir/bind_dir — the pak copy is canonical now")
    ensure_vars(service)
    from .generate import run as gen_run
    gen_run(load_spec(service), write=True, check=False)
    print("\nnext: forge add <binaries> (if not yet) -> forge check -> pak build")
    return 0


def run(service: str, force: bool) -> int:
    proj = load_project()
    spec = load_spec(service)
    base = ROOT / "terraform" / service
    files = {
        base / "provision" / "main.tf": _main_tf(spec),
        base / "provision" / "data.tf": _data_tf(),
        base / "provision" / "provider.tf": _provider_tf(proj),
        base / "provision" / "versions.tf": _versions_tf(),
        base / "provision" / "outputs.tf": _outputs_tf(spec),
        base / "bind" / "main.tf": f"# TODO: the bind logic (pattern: {spec.bind.pattern})\n",
        base / "bind" / "provider.tf": _provider_tf(proj),
        base / "bind" / "versions.tf": _versions_tf(),
    }
    for path, content in files.items():
        rel = path.relative_to(ROOT)
        if path.exists() and not force:
            print(f"keep   {rel}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"wrote  {rel}")
    _tofu_fmt(base)

    print("\nnext: fill the TODOs, then: forge check")
    return 0


def _tofu_fmt(base: pathlib.Path) -> None:
    man = yaml.safe_load((ROOT / "manifest.yml").read_text())
    ver = next((b["version"] for b in man["terraform_binaries"] if b["name"] == "tofu"), None)
    tofu = ROOT / "bin" / f"tofu_{ver}_linux_amd64" / "tofu"
    if not tofu.is_file():
        print("hint: tofu not staged — run `tofu fmt -recursive terraform` yourself")
        return
    for d in (base / "provision", base / "bind"):
        if d.is_dir():
            subprocess.run([str(tofu), "fmt"], cwd=d, check=False,
                           stdout=subprocess.DEVNULL)
