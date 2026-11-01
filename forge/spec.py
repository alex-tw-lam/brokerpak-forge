"""The service spec: single source of truth for one brokerpak service."""
import pathlib
import sys
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field

BindPattern = Literal["passthrough", "mint", "grant", "register", "read-back"]
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


def draft(service: str, resource: str, attrs: dict[str, dict],
          standard_inputs: list[str]) -> ServiceSpec:
    """Classify resource arguments: required -> user inputs, others -> skipped."""
    spec = ServiceSpec(
        service=service, resource=resource, id=_uuid_placeholder(),
        display_name=f"CSB {service}", description=f"CSB {service}",
        tags=["hcs", service], plans=[PlanSpec(
            name="default", id=_uuid_placeholder(), description=f"Default {service} plan",
            display_name="Default")],
        skipped_inputs=list(standard_inputs),
    )
    for arg, meta in sorted(attrs.items()):
        if arg in ("id", "region") or meta.get("computed") and not meta.get("optional"):
            if arg == "id":
                spec.outputs.append(OutputSpec(field_name="id", details=f"ID of the {resource}"))
            continue
        if meta.get("required"):
            spec.inputs[arg] = InputSpec(
                maps_to=arg, details=f"{arg} of {resource}.",
                description=f"{arg} of {resource}.",
                rationale="required argument of the resource")
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

