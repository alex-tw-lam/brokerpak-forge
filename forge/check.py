"""forge check — the one gate: yml schema validity, yml <-> module sync, gen drift."""
import pathlib
import re
import sys

import yaml

from . import generate
from .project import load as load_project
from .validate import validate_file, guess_kind

ROOT = pathlib.Path.cwd()
VAR_RE = re.compile(r'^variable\s+"([^"]+)"\s*\{(.*?)^\}', re.S | re.M)
OUTPUT_RE = re.compile(r'^output\s+"([^"]+)"\s*\{', re.M)
TYPE_RE = re.compile(r'^\s*type\s*=\s*([a-z]+)', re.M)

TYPE_MAP = {
    "string": {"string"}, "number": {"number"}, "integer": {"number"},
    "boolean": {"bool"}, "object": {"object", "map", "any"},
    "array": {"list", "set", "tuple", "any"},
}
INTERNAL = {"labels", "request_context_json", "originating_identity_json"}


def _parse_module(directory: pathlib.Path) -> tuple[dict[str, str], set[str]]:
    variables, outputs = {}, set()
    if not directory.exists():
        return variables, outputs
    for tf in sorted(directory.glob("*.tf")):
        text = tf.read_text()
        for m in VAR_RE.finditer(text):
            tm = TYPE_RE.search(m.group(2))
            variables[m.group(1)] = tm.group(1) if tm else "any"
        outputs.update(OUTPUT_RE.findall(text))
    return variables, outputs


def _check_side(svc: str, side: str, sec: dict, module_dir: pathlib.Path) -> list[str]:
    errors: list[str] = []
    variables, outputs = _parse_module(module_dir)

    yml_inputs: dict[str, str] = {}
    for entry in sec.get("plan_inputs", []) + sec.get("user_inputs", []):
        yml_inputs[entry["field_name"]] = entry.get("type", "string")
    for comp in sec.get("computed_inputs", []):
        yml_inputs[comp["name"]] = comp.get("type", "string")

    for fname, ytype in sorted(yml_inputs.items()):
        if fname not in variables:
            errors.append(f"{svc}/{side}: yml input {fname!r} has no terraform variable")
        elif variables[fname].split("(")[0] not in TYPE_MAP.get(ytype, {"any"}):
            errors.append(f"{svc}/{side}: {fname!r} type mismatch: yml {ytype} vs tf {variables[fname]}")

    internal = INTERNAL if side == "provision" else set()
    for var in sorted(set(variables) - set(yml_inputs) - internal):
        errors.append(f"{svc}/{side}: terraform variable {var!r} is not declared in the yml")

    yml_outputs = {o["field_name"] for o in sec.get("outputs", [])}
    for out in sorted(yml_outputs - outputs):
        errors.append(f"{svc}/{side}: yml output {out!r} has no terraform output")
    for out in sorted(outputs - yml_outputs):
        errors.append(f"{svc}/{side}: terraform output {out!r} is not declared in the yml")
    return errors


def run() -> int:
    proj = load_project()
    stem = proj.definition_prefix.removeprefix("csb-")
    definitions = sorted(ROOT.glob(f"{stem}-*.yml"))
    if not definitions:
        print("no service definitions found")
        return 1

    failures = 0

    # gate 1: JSON Schema + optional defaults (manifest included when present)
    from .validate import run as validate_run
    paths = list(definitions)
    manifest = ROOT / "manifest.yml"
    if manifest.exists():
        paths = [manifest] + paths
    if validate_run(paths):
        failures += 1

    # gate 2: yml <-> module interface sync (through the spec's module pointers;
    # unassembled adopted modules report pending instead of failing)
    for path in definitions:
        spec_doc = yaml.safe_load(path.read_text())
        svc = spec_doc["name"]
        short = svc.removeprefix(f"{proj.definition_prefix}-")
        from .spec import load as load_spec, provision_dir, bind_dir_of
        try:
            spec_obj = load_spec(short)
        except SystemExit:
            spec_obj = None
        if spec_obj is not None and spec_obj.module_dir:
            errors = []
            for side, directory in (("provision", provision_dir(spec_obj)),
                                    ("bind", bind_dir_of(spec_obj))):
                if not directory.is_dir():
                    print(f"pending {path.name}: {side} module not assembled yet "
                          f"({directory}) — forge assemble {short} when ready")
                    continue
                errors += _check_side(svc, side, spec_doc.get(side, {}), directory)
        else:
            module = ROOT / "terraform" / short
            errors = _check_side(svc, "provision", spec_doc.get("provision", {}), module / "provision")
            errors += _check_side(svc, "bind", spec_doc.get("bind", {}), module / "bind")
        for comp in spec_doc.get("bind", {}).get("computed_inputs", []):
            for key in re.findall(r'instance\.details\["([^"]+)"\]', str(comp.get("default", ""))):
                prov_outputs = {o["field_name"] for o in spec_doc.get("provision", {}).get("outputs", [])}
                if key not in prov_outputs:
                    errors.append(f"{svc}/bind: computed {comp['name']!r} references "
                                  f"instance.details[{key!r}], not a provision output")
        if errors:
            failures += 1
            for e in errors:
                print(f"SYNC {e}")
        else:
            print(f"sync  ok  {path.name}")

    # gate 3: generated artefacts match their specs
    for spec_file in sorted((ROOT / "services").glob("*.yaml")):
        from .spec import load
        rc = generate.run(load(spec_file.stem), write=False, check=True)
        if rc:
            failures += 1

    if failures:
        print(f"\n{failures} gate(s) failed")
        return 1
    print("\nall gates green: schema valid, modules in sync, no generation drift")
    return 0
