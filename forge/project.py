"""Project layout: forge.yaml holds the per-brokerpak defaults."""
import pathlib
import sys
from typing import Optional

import yaml
from pydantic import BaseModel, Field

ROOT = pathlib.Path.cwd()


class Project(BaseModel):
    provider: str = "hcs"                       # terraform local name of the main provider
    provider_source: str = "huaweicloud/hcs"    # registry address
    definition_prefix: str = "csb-hcs"          # service names become csb-hcs-<service>
    standard_inputs: list[str] = Field(
        default_factory=lambda: ["region", "cloud", "project_name"],
        description="config-defaulted inputs every service gets (computed per platform)")
    tofu_version: Optional[str] = None          # filled from the manifest when present
    env_config_mapping: dict[str, str] = Field(
        default_factory=lambda: {"HCS_CLOUD": "hcs.cloud", "HCS_REGION_NAME": "hcs.region",
                                 "HCS_PROJECT_NAME": "hcs.project_name"},
        description="emitted into manifest.yml env_config_mapping; empty to omit")


def load() -> Project:
    f = ROOT / "forge.yaml"
    if f.exists():
        return Project.model_validate(yaml.safe_load(f.read_text()) or {})
    return Project()


def save(p: Project) -> None:
    (ROOT / "forge.yaml").write_text(yaml.dump(p.model_dump(), sort_keys=False))


def die(msg: str) -> None:
    sys.exit(msg)
