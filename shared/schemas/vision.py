from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

NOTICE = ("Image observations are MODEL-GENERATED (or simulated) and UNVERIFIED. They do not establish product "
          "identity, serial numbers, or image authenticity, and never decide an outcome on their own.")
Level = Literal["LOW", "MEDIUM", "HIGH"]


class ImageObservation(BaseModel):
    visible_damage: Literal["yes", "no", "unclear"] = "unclear"
    damage_description: str | None = Field(default=None, max_length=300)
    packaging_condition: Literal["intact", "damaged", "opened", "unclear"] = "unclear"
    product_category_seen: str | None = Field(default=None, max_length=60)
    model_confidence: Level = "LOW"


class ImageFinding(ImageObservation):
    event_id: str
    image_uri: str
    uploaded_by: str
    adapter: str
    status: Literal["OK", "UNAVAILABLE", "ERROR"]
    error: str | None = None
    provenance: Literal["MODEL_GENERATED", "SIMULATED"]
    verified: Literal[False] = False  # observations are never verified facts


class ImageDifference(BaseModel):
    kind: Literal["DAMAGE_VISIBILITY", "CATEGORY_MISMATCH", "EXPECTED_CATEGORY_MISMATCH", "PACKAGING_DIFFERENCE"]
    detail: str
    confidence: Level
    event_ids: list[str]
    note: str = "model-generated observation, unverified"


class VisionReport(BaseModel):
    adapter: str
    findings: list[ImageFinding]
    differences: list[ImageDifference]
    notice: str = NOTICE
