"""Turn a directory of staged release binaries into a brokerpak manifest.yml.

Recognises the staging layout produced by fetch scripts:

    tofu_1.11.3_linux_amd64/tofu
    terraform-provider-hcs_2.4.26_linux_amd64/terraform-provider-hcs_v2.4.26

and flat zips/binaries of the same names.
"""
import pathlib
import re
from typing import Optional

import yaml

from .project import die

STAGED = re.compile(
    r"^(?P<name>(?:terraform-provider-)?[a-z0-9-]+?)_v?(?P<version>\d+\.\d+\.\d+)"
    r"_(?P<os>[a-z0-9]+)_(?P<arch>[a-z0-9]+)$")

ORG_GUESSES = {"hcs": "huaweicloud", "google": "hashicorp", "azurerm": "hashicorp",
               "aws": "hashicorp", "random": "hashicorp"}


def parse_bin_dir(bin_dir: pathlib.Path) -> list[dict]:
    """Return [{name, version, os, arch, file}] for every staged binary found."""
    found: dict[tuple[str, str, str, str], pathlib.Path] = {}
    for entry in sorted(bin_dir.iterdir()):
        stem = entry.name.removesuffix(".zip")
        if entry.is_dir():
            inner = [p for p in entry.iterdir() if p.is_file() and not p.suffix]
            if not inner:
                continue
            m = STAGED.match(entry.name)
            if not m:
                continue
            found[(m["name"], m["version"], m["os"], m["arch"])] = inner[0]
        else:
            m = STAGED.match(stem)
            if not m:
                continue
            found[(m["name"], m["version"], m["os"], m["arch"])] = entry
    out = []
    for (name, version, os_, arch), path in sorted(found.items()):
        out.append({"name": name, "version": version, "os": os_, "arch": arch, "file": path})
    return out


def _source_entry(name: str, org: Optional[str]) -> Optional[dict]:
    if name == "tofu":
        return None  # tofu has no provider address
    short = name.removeprefix("terraform-provider-")
    ns = org or ORG_GUESSES.get(short, short)
    return {"provider": f"{ns}/{short}"}


def build_manifest(binaries: list[dict], pak_name: str, org: Optional[str],
                   author: str, description: str) -> dict:
    platforms = sorted({(b["os"], b["arch"]) for b in binaries})
    entries = []
    tofu_seen = False
    for b in binaries:
        if b["name"] == "tofu":
            entry = {
                "name": "tofu",
                "version": b["version"],
                "source": f"https://github.com/opentofu/opentofu/archive/v{b['version']}.zip",
                "url_template": "./bin/${name}_${version}_${os}_${arch}/${name}",
            }
            if not tofu_seen:
                entry["default"] = True
                tofu_seen = True
        else:
            entry = {
                "name": b["name"],
                "version": b["version"],
                "source": f"https://github.com/{(org or ORG_GUESSES.get(b['name'].removeprefix('terraform-provider-'), 'CHANGE_ME'))}"
                          f"/{b['name']}/archive/v{b['version']}.zip",
                **(_source_entry(b["name"], org) or {}),
                "url_template": "./bin/${name}_${version}_${os}_${arch}/${name}_v${version}",
            }
        entries.append(entry)

    return {
        "packversion": 1,
        "name": pak_name,
        "version": "0.1.0",
        "metadata": {"author": author, "description": description},
        "platforms": [{"os": o, "arch": a} for o, a in platforms],
        "terraform_binaries": entries,
    }


def run(bin_dir: pathlib.Path, pak_name: str, org: Optional[str],
        author: str, description: str, write: bool) -> None:
    binaries = parse_bin_dir(bin_dir)
    if not binaries:
        die(f"no staged binaries recognised in {bin_dir}")
    doc = build_manifest(binaries, pak_name, org, author, description)
    text = yaml.dump(doc, sort_keys=False, default_flow_style=False, width=100)
    if write:
        pathlib.Path("manifest.yml").write_text(text)
        print("wrote manifest.yml")
    print(text)
