"""Generic offline provider schema extraction and queries."""
import json
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Optional

import yaml

ROOT = pathlib.Path.cwd()
SCHEMA_DIR = ROOT / "schema"


def _manifest() -> dict:
    f = ROOT / "manifest.yml"
    if not f.exists():
        sys.exit("no manifest.yml — run: forge manifest --write")
    return yaml.safe_load(f.read_text())


def _tofu_path(man: dict, bindir: pathlib.Path) -> pathlib.Path:
    ver = next((b["version"] for b in man["terraform_binaries"] if b["name"] == "tofu"), None)
    if ver is None:
        sys.exit("no tofu in manifest.yml")
    p = bindir / f"tofu_{ver}_linux_amd64" / "tofu"
    if not p.is_file():
        sys.exit(f"tofu {ver} not staged at {p}")
    return p


def extract(bin_dir: Optional[pathlib.Path]) -> list[pathlib.Path]:
    """`tofu providers schema -json` offline for every provider in the manifest."""
    man = _manifest()
    bindir = bin_dir or (ROOT / "bin")
    work = ROOT / ".forge-cache" / "schema"
    if work.exists():
        shutil.rmtree(work)
    module, mirror = work / "module", work / "mirror"
    module.mkdir(parents=True)

    requires, outputs, seen = [], [], {}
    for b in man["terraform_binaries"]:
        staged = bindir / f"{b['name']}_{b['version']}_linux_amd64"
        if not staged.is_dir():
            sys.exit(f"{b['name']} {b['version']} not staged at {staged}")
        if b["name"] == "tofu":
            continue
        addr = b.get("provider") or sys.exit(f"{b['name']}: no provider address in manifest.yml")
        short = addr.split("/")[1]
        seen[addr] = max(seen.get(addr, "0"), b["version"])
        binary = next(p for p in staged.iterdir()
                      if p.is_file() and p.name.startswith("terraform-provider-"))
        dest = mirror / "registry.terraform.io" / addr / b["version"] / "linux_amd64"
        dest.mkdir(parents=True)
        shutil.copy(binary, dest / binary.name)
        seen[addr] = max(seen.get(addr, "0"), b["version"])
    outputs = sorted(seen.items())

    requires = [f'    {addr.split("/")[1]} = {{ source = "registry.terraform.io/{addr}", version = "{ver}" }}'
                for addr, ver in sorted(seen.items())]
    (module / "versions.tf").write_text(
        "terraform {\n  required_providers {\n" + "\n".join(requires) + "\n  }\n}\n")
    tofurc = work / "tofurc"
    tofurc.write_text(
        "provider_installation {\n  filesystem_mirror {\n"
        f'    path    = "{mirror}"\n'
        '    include = ["registry.terraform.io/*/*"]\n  }\n}\n')
    env = {"TF_CLI_CONFIG_FILE": str(tofurc), "PATH": "/usr/bin:/bin"}
    tofu = _tofu_path(man, bindir)

    def run(*args, capture=False):
        return subprocess.run([str(tofu), *args], cwd=module, env=env, check=True,
                              stdout=subprocess.PIPE if capture else subprocess.DEVNULL)

    run("init", "-backend=false", "-input=false")
    proc = run("providers", "schema", "-json", capture=True)
    full = json.loads(proc.stdout)

    SCHEMA_DIR.mkdir(exist_ok=True)
    written = []
    for addr, version in outputs:
        key = f"registry.terraform.io/{addr}"
        body = {"provider_schemas": {addr: full["provider_schemas"][key]}}
        out = SCHEMA_DIR / f"{addr.replace('/', '-')}-{version}.json"
        out.write_text(json.dumps(body))
        written.append(out)
    return written


class Schema:
    def __init__(self):
        self.files = sorted(SCHEMA_DIR.glob("*.json")) if SCHEMA_DIR.exists() else []
        if not self.files:
            sys.exit("no schema extracted — run: forge schema extract")

    def _provider_block(self, kind: str, name: str) -> tuple[str, dict]:
        key = "resource_schemas" if kind == "resource" else "data_source_schemas"
        for f in self.files:
            data = json.loads(f.read_text())
            for addr, prov in data["provider_schemas"].items():
                if name in prov.get(key, {}):
                    return addr, prov[key][name]["block"]
        sys.exit(f"{name} not found in any extracted {key}; run: forge schema list")

    def resources(self, pattern: Optional[str]) -> list[str]:
        return self._names("resource_schemas", pattern)

    def data_sources(self, pattern: Optional[str]) -> list[str]:
        return self._names("data_source_schemas", pattern)

    def _names(self, key: str, pattern: Optional[str]) -> list[str]:
        names = []
        for f in self.files:
            for prov in json.loads(f.read_text())["provider_schemas"].values():
                names.extend(prov.get(key, {}))
        return sorted(n for n in set(names) if pattern is None or pattern in n)

    def attributes(self, kind: str, name: str) -> dict[str, dict]:
        return self._provider_block(kind, name)[1].get("attributes", {})


def format_attributes(attrs: dict[str, dict]) -> str:
    lines = []
    for arg, meta in sorted(attrs.items()):
        flags = [f for f, k in [("R", "required"), ("O", "optional"),
                                ("C", "computed"), ("S", "sensitive")] if meta.get(k)]
        t = meta.get("type", "?")
        if isinstance(t, list):
            t = "".join(part for part in t if isinstance(part, str))
        lines.append(f"  {arg:42s} {str(t):24s} {'/'.join(flags) or '-'}")
    return "\n".join(lines)


TF_TYPE_TO_SPEC = {"string": "string", "number": "number", "bool": "boolean"}


def spec_type(attr: dict) -> str:
    t = attr.get("type")
    base = t[0] if isinstance(t, list) else t
    return TF_TYPE_TO_SPEC.get(base if isinstance(base, str) else "?", "string")
