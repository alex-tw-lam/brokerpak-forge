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
