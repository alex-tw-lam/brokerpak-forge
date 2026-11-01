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


def check_optional_defaults(doc: dict, label: str) -> list[str]:
    """Rule: an exposed optional input always carries a default (null counts).

    Without one, "unset" reaches the module ambiguously — a non-required input
    with no yml default relies on a tf default nobody can see from the catalog.
    """
    errors = []
    for side in ("provision", "bind"):
        for group in ("plan_inputs", "user_inputs"):
            for i, entry in enumerate(doc.get(side, {}).get(group, [])):
                if not entry.get("required") and "default" not in entry:
                    errors.append(
                        f"{label}: {side}/{group}/{i} {entry.get('field_name')!r}: "
                        "optional input has no default (add default: null or a value)")
    return errors


def validate_file(path: pathlib.Path, schema_name: str) -> tuple[list[str], list[str]]:
    """Return (errors, warnings)."""
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        return [f"{path.name}: not a yml mapping"], []
    errors = validate_document(doc, schema_name, path.name)
    warnings = []
    if schema_name.startswith("service-definition"):
        # Discipline, not a CSB requirement: official paks rely on tf defaults
        # in 16 places, so this only warns for foreign files. forge-generated
        # definitions always satisfy it (gen enforces the rule as an error).
        warnings = check_optional_defaults(doc, path.name)
    return errors, warnings


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
        errors, warnings = validate_file(p, kind)
        for w in warnings:
            print(f"WARN  {w}")
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
