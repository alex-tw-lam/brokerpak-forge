"""The service spec: single source of truth for one brokerpak service."""
import pathlib
import sys
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field

BindPattern = Literal["passthrough", "mint", "grant", "register", "read-back", "imported"]
SERVICES_DIR = pathlib.Path("services")


class InputSpec(BaseModel):
    field_name: Optional[str] = None
    maps_to: Optional[str] = None          # resource argument when different
    type: str = "string"
    details: str = ""
    description: str = ""                  # terraform variable description
    default: Optional[Any] = None
    nullable: bool = False
    required: bool = False
    prohibit_update: bool = False
    plan_updateable: bool = False
    plan_input: bool = False               # plan_inputs instead of user_inputs
    enum: Optional[dict[str, str]] = None
    constraints: Optional[dict[str, Any]] = None
    tf_type: Optional[str] = None
    rationale: str = ""                    # S5: why this input exists


class ComputedSpec(BaseModel):
    name: str
    default: str                           # HIL expression, e.g. ${instance.details["id"]}
    type: str = "string"


class BindSpec(BaseModel):
    pattern: BindPattern = "passthrough"
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    computed: list[ComputedSpec] = Field(default_factory=list)


class OutputSpec(BaseModel):
    field_name: str
    type: str = "string"
    details: str = ""


class PlanSpec(BaseModel):
    name: str
    id: str
    description: str = ""
    display_name: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)


class ServiceSpec(BaseModel):
    service: str
    id: str = ""
    display_name: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    provider_display_name: str = "Huawei Cloud Stack"
    image_url: str = ""
    documentation_url: str = ""
    support_url: str = ""
    plan_updateable: bool = False
    resource: str = ""
    plans: list[PlanSpec] = Field(default_factory=list)
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    skipped_inputs: list[str] = Field(default_factory=list)   # resource args left as module-internal defaults
    outputs: list[OutputSpec] = Field(default_factory=list)
    bind: BindSpec = BindSpec()
    bind_outputs: list[OutputSpec] = Field(default_factory=list)


def path_for(service: str) -> pathlib.Path:
    return SERVICES_DIR / f"{service}.yaml"


def load(service: str) -> ServiceSpec:
    p = path_for(service)
    if not p.exists():
        sys.exit(f"no spec at {p} — run: forge service new {service}")
    return ServiceSpec.model_validate(yaml.safe_load(p.read_text()))


def save(spec: ServiceSpec) -> pathlib.Path:
    SERVICES_DIR.mkdir(exist_ok=True)
    p = path_for(spec.service)
    p.write_text(yaml.dump(spec.model_dump(exclude_none=True), sort_keys=False,
                           default_flow_style=False, width=100, allow_unicode=True))
    return p


def default_treatment(arg: str, meta: dict) -> str:
    """Suggested classification for one resource argument."""
    if meta.get("required"):
        return "user"
    return "skipped"


def draft(service: str, resource: str, attrs: dict[str, dict],
          standard_inputs: list[str],
          treatments: dict[str, str] | None = None) -> ServiceSpec:
    """Classify resource arguments: required -> user inputs, others -> skipped."""
    spec = ServiceSpec(
        service=service, resource=resource, id=_uuid_placeholder(),
        display_name=f"CSB {service}", description=f"CSB {service}",
        tags=["hcs", service], plans=[PlanSpec(
            name="default", id=_uuid_placeholder(), description=f"Default {service} plan",
            display_name="Default")],
        skipped_inputs=list(standard_inputs),
    )
    treatments = treatments or {}
    for arg, meta in sorted(attrs.items()):
        if arg in standard_inputs:
            continue  # platform inputs are generated, never skipped entries
        if arg == "id" or (meta.get("computed") and not meta.get("optional")):
            if arg == "id":
                spec.outputs.append(OutputSpec(field_name="id", details=f"ID of the {resource}"))
            continue
        treatment = treatments.get(arg) or default_treatment(arg, meta)
        if treatment == "user":
            spec.inputs[arg] = InputSpec(
                maps_to=arg, details=f"{arg} of {resource}.",
                description=f"{arg} of {resource}.",
                rationale="required argument of the resource")
        elif treatment == "plan":
            spec.inputs[arg] = InputSpec(
                maps_to=arg, details=f"{arg} of {resource}.",
                description=f"{arg} of {resource}.", plan_input=True,
                rationale="fixed by the plan")
        elif treatment == "computed":
            spec.bind.computed.append(
                ComputedSpec(name=arg, default=f'${{instance.details["{arg}"]}}'))
        else:
            spec.skipped_inputs.append(arg)
    return spec


def _uuid_placeholder() -> str:
    import uuid
    return str(uuid.uuid4())


def move(spec: ServiceSpec, field: str, to: str, default: Optional[str]) -> ServiceSpec:
    """Reclassify one input between user / plan / computed (platform) / skipped."""
    if to not in ("user", "plan", "computed", "skipped"):
        raise ValueError("--to must be user, plan, computed or skipped")

    # locate the field: inputs (user or plan) / skipped / already computed
    if field in spec.inputs:
        inp = spec.inputs.pop(field)
    elif field in spec.skipped_inputs:
        spec.skipped_inputs.remove(field)
        inp = InputSpec(maps_to=field, details=f"{field}.", rationale="promoted from skipped")
    elif field in [c.name for c in spec.bind.computed]:
        spec.bind.computed = [c for c in spec.bind.computed if c.name != field]
        inp = InputSpec(maps_to=field, details=f"{field}.", rationale="demoted from computed")
    else:
        raise ValueError(f"{field!r} is not an input of {spec.service}")

    if to in ("user", "plan"):
        inp.plan_input = (to == "plan")
        spec.inputs[field] = inp
    elif to == "skipped":
        spec.skipped_inputs.append(field)
    else:  # computed (platform input)
        expr = default or f'${{instance.details["{field}"]}}'
        if "instance.details" not in expr and "${" not in expr:
            expr = f"${{{expr}}}"
        spec.bind.computed.append(ComputedSpec(name=field, default=expr))
    return spec



def _input_from_entry(e: dict, plan_input: bool) -> "InputSpec":
    return InputSpec(
        field_name=e.get("field_name"),
        type=e.get("type", "string"),
        details=e.get("details", ""),
        description=e.get("details", ""),
        default=e.get("default") if "default" in e else None,
        nullable=bool(e.get("nullable")),
        required=bool(e.get("required")),
        prohibit_update=bool(e.get("prohibit_update")),
        plan_updateable=bool(e.get("plan_updateable")),
        plan_input=plan_input,
        enum=e.get("enum"),
        constraints=e.get("constraints"),
    )


def import_definition(doc: dict, proj) -> "ServiceSpec":
    """Adopt an existing hand-written definition yml as a spec, so the service
    can be regenerated (and continuously edited) from services/<svc>.yaml."""
    name = doc.get("name", "")
    prefix = proj.definition_prefix
    service = name[len(prefix) + 1:] if prefix and name.startswith(prefix + "-") else name

    inputs: dict[str, InputSpec] = {}
    prov = doc.get("provision", {})
    for e in prov.get("plan_inputs", []) + prov.get("user_inputs", []):
        fname = e["field_name"]
        if fname in proj.standard_inputs:
            continue  # regenerated automatically
        inputs[fname] = _input_from_entry(e, plan_input=e in prov.get("plan_inputs", []))

    bind_sec = doc.get("bind", {})
    bind_inputs = {e["field_name"]: _input_from_entry(e, plan_input=False)
                   for e in bind_sec.get("user_inputs", []) + bind_sec.get("plan_inputs", [])
                   if e["field_name"] not in proj.standard_inputs}
    computed = [ComputedSpec(name=c["name"], default=c.get("default", ""),
                             type=c.get("type", "string"))
                for c in bind_sec.get("computed_inputs", [])
                if c["name"] not in proj.standard_inputs]

    return ServiceSpec(
        service=service,
        id=doc.get("id", ""),
        display_name=doc.get("display_name", ""),
        description=doc.get("description", ""),
        tags=doc.get("tags", []),
        provider_display_name=doc.get("provider_display_name", ""),
        image_url=doc.get("image_url", ""),
        documentation_url=doc.get("documentation_url", ""),
        support_url=doc.get("support_url", ""),
        plan_updateable=bool(doc.get("plan_updateable")),
        plans=[PlanSpec(name=p["name"], id=p["id"], description=p.get("description", ""),
                        display_name=p.get("display_name"), properties=p.get("properties", {}))
               for p in doc.get("plans", [])],
        inputs=inputs,
        outputs=[OutputSpec(field_name=o["field_name"], type=o.get("type", "string"),
                            details=o.get("details", ""))
                 for o in prov.get("outputs", [])],
        bind=BindSpec(pattern="imported", inputs=bind_inputs, computed=computed),
        bind_outputs=[OutputSpec(field_name=o["field_name"], type=o.get("type", "string"),
                                 details=o.get("details", ""))
                      for o in bind_sec.get("outputs", [])],
    )
