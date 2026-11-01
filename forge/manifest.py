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
    if not bin_dir.is_dir():
        die(f"no binary directory at {bin_dir} — stage the release binaries there "
            f"(bin/tofu_<ver>_<os>_<arch>/, bin/terraform-provider-<name>_<ver>_<os>_<arch>/) "
            "or pass --bin-dir")
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


def resolve_address(short: str, pairs: dict[str, str],
                    blanket: Optional[str], proj) -> str:
    """Provider registry address: --org name=ns pairs win, then forge.yaml
    provider_orgs, then built-in guesses, then a blanket --org. Unknown
    providers are rejected with guidance instead of a silently wrong address."""
    value = (pairs.get(short) or proj.provider_orgs.get(short)
             or ORG_GUESSES.get(short) or blanket)
    if value is None:
        die(f"cannot determine the registry namespace for provider {short!r}.\n"
            f"  forge manifest --org {short}=<namespace> ...        # one-off\n"
            f"  or forge.yaml: provider_orgs: {{{short}: <namespace>}}   # persistent")
    return value if "/" in value else f"{value}/{short}"


def parse_org_flags(flags: list[str]) -> tuple[dict[str, str], Optional[str]]:
    """--org accepts 'ns' (blanket default) or 'provider=ns' (per provider),
    repeatable: --org huaweicloud --org csbpg=cloudfoundry.org/cloud-service-broker"""
    pairs: dict[str, str] = {}
    blanket: Optional[str] = None
    for f in flags or []:
        if "=" in f:
            k, v = f.split("=", 1)
            pairs[k.strip()] = v.strip()
        elif blanket is None:
            blanket = f.strip()
        else:
            die(f"--org {f!r}: a blanket namespace was already given ({blanket!r}); "
                "use provider=namespace pairs for anything else")
    return pairs, blanket


def build_manifest(binaries: list[dict], pak_name: str, orgs: list[str],
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
            from .project import load as load_project
            pairs, blanket = parse_org_flags(orgs)
            short = b["name"].removeprefix("terraform-provider-")
            addr = resolve_address(short, pairs, blanket, load_project())
            entry = {
                "name": b["name"],
                "version": b["version"],
                "source": f"https://github.com/{addr.split('/')[0]}"
                          f"/{b['name']}/archive/v{b['version']}.zip",
                "provider": addr,
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
                   orgs: list[str], author: Optional[str],
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

    fresh = build_manifest(binaries, doc.get("name", "hcs-services"), orgs,
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


BARE = re.compile(r"^(?P<name>terraform-provider-[a-z0-9-]+)_v(?P<version>\d+\.\d+\.\d+)_(?P<os>[a-z0-9]+)_(?P<arch>[a-z0-9]+)$")


def parse_one(path: pathlib.Path) -> dict:
    """One binary at a time: a staged dir, a release zip, or a bare binary."""
    path = path.expanduser().resolve()
    if path.is_dir():
        for inner in sorted(path.iterdir()):
            if inner.is_dir():
                continue
            m = STAGED.match(path.name.removesuffix(".zip")) or STAGED.match(inner.name.removesuffix(".zip"))
            if m and (inner.name.startswith(m["name"]) or m["name"].startswith("tofu")):
                binary = _binary_in(path, m["name"])
                if binary:
                    return {"name": m["name"], "version": m["version"],
                            "os": m["os"], "arch": m["arch"], "file": binary}
        die(f"cannot parse a release identity from directory {path.name} "
            "(expected name_version_os_arch)")
    if path.is_file():
        stem = path.name.removesuffix(".zip")
        m = STAGED.match(stem)
        if m:
            return {"name": m["name"], "version": m["version"],
                    "os": m["os"], "arch": m["arch"], "file": path}
        m = BARE.match(stem) or re.match(
            r"^(?P<name>tofu)_(?P<version>\d+\.\d+\.\d+)_(?P<os>[a-z0-9]+)_(?P<arch>[a-z0-9]+)$", stem)
        if m:
            print(f"note: {path.name} carries no os/arch in the name — assuming linux/amd64")
            return {"name": m["name"], "version": m["version"],
                    "os": "linux", "arch": "amd64", "file": path}
        m = re.match(r"^(?P<name>terraform-provider-[a-z0-9-]+)_v(?P<version>\d+\.\d+\.\d+)$", stem)
        if m:
            print(f"note: {path.name} carries no os/arch in the name — assuming linux/amd64")
            return {"name": m["name"], "version": m["version"],
                    "os": "linux", "arch": "amd64", "file": path}
        die(f"cannot parse a release identity from {path.name} "
            "(expected name_version_os_arch[.zip] or a terraform-provider-..._vX.Y.Z binary)")
    die(f"no such file or directory: {path}")


def run_add(paths: list[pathlib.Path], name: Optional[str], orgs: list[str],
            author: Optional[str], description: Optional[str]) -> None:
    binaries = [parse_one(p) for p in paths]
    manifest_path = pathlib.Path("manifest.yml")
    if manifest_path.exists():
        doc, notes = merge_manifest(yaml.safe_load(manifest_path.read_text()) or {},
                                    binaries, name, orgs, author, description)
    else:
        doc = build_manifest(binaries, name or "hcs-services", orgs,
                             author or "CHANGE_ME", description or "Cloud Service Broker pak")
        notes = [f"created manifest.yml"]
    for n in notes:
        print(n)
    manifest_path.write_text(yaml.dump(doc, sort_keys=False, default_flow_style=False, width=100))
    print(f"wrote manifest.yml ({len(doc['terraform_binaries'])} binaries)")


def run(bin_dir: pathlib.Path, pak_name: Optional[str], orgs: list[str],
        author: Optional[str], description: Optional[str], write: bool) -> None:
    binaries = parse_bin_dir(bin_dir)
    if not binaries:
        die(f"no staged binaries recognised in {bin_dir}")

    manifest_path = pathlib.Path("manifest.yml")
    if manifest_path.exists():
        existing = yaml.safe_load(manifest_path.read_text()) or {}
        doc, notes = merge_manifest(existing, binaries, pak_name, orgs, author, description)
        for n in notes:
            print(n)
    else:
        doc = build_manifest(binaries, pak_name or "hcs-services", orgs,
                             author or "CHANGE_ME", description or "Cloud Service Broker pak")
    text = yaml.dump(doc, sort_keys=False, default_flow_style=False, width=100)
    if write:
        manifest_path.write_text(text)
        print("wrote manifest.yml")
    print(text)
