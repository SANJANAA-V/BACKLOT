"""Feature engineering. Inputs are ONLY reconciliation metrics derived from events (the same
code path serves training and inference). Scenario names and labels never reach this module."""
from __future__ import annotations

from pydantic import BaseModel, Field

FEATURE_NAMES = [
    "weight_deviation",          # |observed-expected|/expected parcel weight (0 if not recorded)
    "weight_missing",            # 1 if no usable weight record
    "missing_logistics_count",   # of pickup scheduled/completed, weight, hub scan
    "duplicate_event_count",     # redeliveries + same-content re-emissions
    "identifier_mismatch",       # expected vs observed product identifier differ
    "identifier_unverified",     # no PRODUCT_ID_VERIFIED event at all
    "condition_worse_by",        # how many grade steps worse the warehouse found it vs customer report
    "condition_unknown",         # no comparable condition pair (e.g. not inspected)
    "missing_item_reported",     # warehouse says item absent from package
    "max_logistics_gap_hours",   # largest gap between consecutive logistics events (0 if n/a)
    "prior_returns_90d",         # customer return history indicator
]


class RiskFeatures(BaseModel):
    weight_deviation: float = Field(ge=0)
    weight_missing: int = Field(ge=0, le=1)
    missing_logistics_count: int = Field(ge=0, le=4)
    duplicate_event_count: int = Field(ge=0)
    identifier_mismatch: int = Field(ge=0, le=1)
    identifier_unverified: int = Field(ge=0, le=1)
    condition_worse_by: int = Field(ge=0)
    condition_unknown: int = Field(ge=0, le=1)
    missing_item_reported: int = Field(ge=0, le=1)
    max_logistics_gap_hours: float = Field(ge=0)
    prior_returns_90d: int = Field(ge=0)


def build_features(metrics: dict) -> RiskFeatures:
    wd, cd = metrics.get("weight_deviation"), metrics.get("condition_delta")
    return RiskFeatures(
        weight_deviation=wd or 0.0, weight_missing=int(wd is None),
        missing_logistics_count=int(metrics.get("missing_logistics_count") or 0),
        duplicate_event_count=int(metrics.get("duplicate_event_count") or 0),
        identifier_mismatch=int(metrics.get("identifier_mismatch") or 0),
        identifier_unverified=1 - int(metrics.get("identifier_verified") or 0),
        condition_worse_by=max(int(cd), 0) if cd is not None else 0, condition_unknown=int(cd is None),
        missing_item_reported=int(metrics.get("missing_item_reported") or 0),
        max_logistics_gap_hours=float(metrics.get("max_logistics_gap_hours") or 0.0),
        prior_returns_90d=int(metrics.get("prior_returns_90d") or 0))


def to_vector(f: RiskFeatures) -> list[float]:
    d = f.model_dump()
    return [float(d[n]) for n in FEATURE_NAMES]
