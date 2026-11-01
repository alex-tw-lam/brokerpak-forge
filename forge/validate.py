"""Validate brokerpak yml files against the vendored JSON Schemas.

CSB enforces the yml formats via Go structs at pak build time — late and with
terse messages. forge vendors equivalent JSON Schemas (derived from those
structs and the official AWS/Azure/GCP brokerpaks) so definitions and manifests
can be validated the moment they are generated.
"""
import json
import pathlib
import sys

import yaml
from jsonschema import Draft7Validator

SCHEMA_DIR = pathlib.Path(__file__).parent / "schemas"
ROOT = pathlib.Path.cwd()


def _validator(name: str) -> Draft7Validator:
    schema = json.loads((SCHEMA_DIR / name).read_text())
    return Draft7Validator(schema)


def validate_document(doc: dict, schema_name: str, label: str) -> list[str]:
    errors = []
    for e in _validator(schema_name).iter_errors(doc):
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        errors.append(f"{label}: {loc}: {e.message}")
    return errors


def validate_file(path: pathlib.Path, schema_name: str) -> list[str]:
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        return [f"{path.name}: not a yml mapping"]
    return validate_document(doc, schema_name, path.name)


def guess_kind(path: pathlib.Path) -> str | None:
    name = path.name
    if name.startswith("manifest"):
        return "brokerpak-manifest.schema.json"
    doc = yaml.safe_load(path.read_text()) or {}
    if {"provision", "bind"} & set(doc) and "terraform_binaries" not in doc:
        return "service-definition.schema.json"
    return None


def run(paths: list[pathlib.Path]) -> int:
    if not paths:
        candidates = []
        for pattern in ("manifest.yml", "manifest.yml.example", "hcs-*.yml"):
            candidates.extend(sorted(ROOT.glob(pattern)))
        paths = candidates
    if not paths:
        print("nothing to validate (no manifest.yml or hcs-*.yml here)")
        return 1

    failures = 0
    for p in paths:
        kind = guess_kind(p)
        if kind is None:
            print(f"skip  {p.name} (neither manifest nor service definition)")
            continue
        errors = validate_file(p, kind)
        if errors:
            failures += 1
            for e in errors:
                print(f"FAIL  {e}")
        else:
            print(f"ok    {p.name} ({kind.removesuffix('.schema.json')})")
    if failures:
        print(f"\n{failures} file(s) failed schema validation")
        return 1
    return 0
