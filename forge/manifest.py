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


def _binary_in(entry: pathlib.Path, stem: str) -> pathlib.Path | None:
    """The actual executable inside a staged dir — name wins, never LICENSE.

    Version-bearing names like terraform-provider-random_v3.9.0 end in `.0`,
    which pathlib counts as a suffix, so suffix filtering cannot be used.
    """
    candidates = [p for p in entry.iterdir()
                  if p.is_file() and p.name.startswith(stem)]
    return candidates[0] if candidates else None


def parse_bin_dir(bin_dir: pathlib.Path) -> list[dict]:
    """Return [{name, version, os, arch, file}] for every staged binary found."""
    found: dict[tuple[str, str, str, str], pathlib.Path] = {}
    for entry in sorted(bin_dir.iterdir()):
        stem = entry.name.removesuffix(".zip")
        m = STAGED.match(stem)
        if not m:
            continue
        if entry.is_dir():
            binary = _binary_in(entry, m["name"])
            if binary is None:
                continue
            found[(m["name"], m["version"], m["os"], m["arch"])] = binary
        else:
            found[(m["name"], m["version"], m["os"], m["arch"])] = entry
    out = []
    for (name, version, os_, arch), path in sorted(found.items()):
        out.append({"name": name, "version": version, "os": os_, "arch": arch, "file": path})
    return out


def _namespace(short: str, org: Optional[str]) -> str:
    """Built-in guesses win (hashicorp/random stays hashicorp even under
    --org huaweicloud); --org fills in providers we do not know."""
    return ORG_GUESSES.get(short) or org or short


def _source_entry(name: str, org: Optional[str]) -> Optional[dict]:
    if name == "tofu":
        return None  # tofu has no provider address
    short = name.removeprefix("terraform-provider-")
    return {"provider": f"{_namespace(short, org)}/{short}"}


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
                "source": f"https://github.com/{_namespace(b['name'].removeprefix('terraform-provider-'), org)}"
                          f"/{b['name']}/archive/v{b['version']}.zip",
                **(_source_entry(b["name"], org) or {}),
                "url_template": "./bin/${name}_${version}_${os}_${arch}/${name}_v${version}",
            }
        entries.append(entry)

    from .project import load as load_project
    proj = load_project()
    stem = proj.definition_prefix.removeprefix("csb-")
    definitions = sorted(p.name for p in pathlib.Path.cwd().glob(f"{stem}-*.yml"))

    doc = {
        "packversion": 1,
        "name": pak_name,
        "version": "0.1.0",
        "metadata": {"author": author, "description": description},
        "platforms": [{"os": o, "arch": a} for o, a in platforms],
        "terraform_binaries": entries,
        "service_definitions": definitions,
    }
    if proj.env_config_mapping:
        doc["env_config_mapping"] = proj.env_config_mapping
    return doc


def merge_manifest(existing: dict, binaries: list[dict], pak_name: Optional[str],
                   org: Optional[str], author: Optional[str],
                   description: Optional[str]) -> tuple[dict, list[str]]:
    """Update an existing manifest in place: refresh/add binary entries for what
    is staged, keep everything the operator hand-edited (extra platforms, upgrade
    paths, env mappings, unknown binary entries, metadata tweaks)."""
    notes: list[str] = []
    doc = dict(existing)
    if pak_name:
        doc["name"] = pak_name
        notes.append(f"name -> {pak_name}")
    if author or description:
        meta = dict(doc.get("metadata", {}))
        if author:
            meta["author"] = author
        if description:
            meta["description"] = description
        doc["metadata"] = meta

    fresh = build_manifest(binaries, doc.get("name", "hcs-services"), org,
                           author or "", description or "")
    old_keys = {(b["name"], b["version"]) for b in doc.get("terraform_binaries", [])}
    new_keys = {(b["name"], b["version"]) for b in fresh["terraform_binaries"]}

    binaries_out = list(doc.get("terraform_binaries", []))
    by_key = {(b["name"], b["version"]): b for b in binaries_out}
    for entry in fresh["terraform_binaries"]:
        key = (entry["name"], entry["version"])
        if key in by_key:
            # keep any operator additions on the old entry, refresh the rest
            merged = {**entry, **{k: v for k, v in by_key[key].items() if k not in entry}}
            notes.append(f"updated   {key[0]} {key[1]}")
            binaries_out[binaries_out.index(by_key[key])] = merged
        else:
            binaries_out.append(entry)
            notes.append(f"added     {key[0]} {key[1]}")
    for key in sorted(old_keys - new_keys):
        notes.append(f"kept      {key[0]} {key[1]} (not staged; untouched)")
    doc["terraform_binaries"] = binaries_out

    # keep exactly one default tofu when none is marked
    tofus = [b for b in doc["terraform_binaries"] if b["name"] == "tofu"]
    if tofus and not any(b.get("default") for b in tofus):
        tofus[0]["default"] = True
        notes.append("marked    default tofu")
    return doc, notes


def run(bin_dir: pathlib.Path, pak_name: Optional[str], org: Optional[str],
        author: Optional[str], description: Optional[str], write: bool) -> None:
    binaries = parse_bin_dir(bin_dir)
    if not binaries:
        die(f"no staged binaries recognised in {bin_dir}")

    manifest_path = pathlib.Path("manifest.yml")
    if manifest_path.exists():
        existing = yaml.safe_load(manifest_path.read_text()) or {}
        doc, notes = merge_manifest(existing, binaries, pak_name, org, author, description)
        for n in notes:
            print(n)
    else:
        doc = build_manifest(binaries, pak_name or "hcs-services", org,
                             author or "CHANGE_ME", description or "Cloud Service Broker pak")
    text = yaml.dump(doc, sort_keys=False, default_flow_style=False, width=100)
    if write:
        manifest_path.write_text(text)
        print("wrote manifest.yml")
    print(text)
