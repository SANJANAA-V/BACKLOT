"""Output models of the evidence reconciliation engine."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

DISCLAIMER = ("Findings are inconsistencies in the available evidence, not proof of wrongdoing. "
              "Each lists benign explanations; a human reviewer decides.")


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def rank(self) -> int:
        return ["INFO", "LOW", "MEDIUM", "HIGH"].index(self.value)


class Confidence(str, Enum):
    """Confidence that the finding reflects a real-world difference rather than a data error."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FindingType(str, Enum):
    MISSING_EVENT = "MISSING_EVENT"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    DELAYED_EVENT = "DELAYED_EVENT"
    SEQUENCE_VIOLATION = "SEQUENCE_VIOLATION"
    WEIGHT_DEVIATION = "WEIGHT_DEVIATION"
    CONFLICTING_WEIGHTS = "CONFLICTING_WEIGHTS"
    IDENTIFIER_MISMATCH = "IDENTIFIER_MISMATCH"
    INCONSISTENT_PRODUCT_REFERENCE = "INCONSISTENT_PRODUCT_REFERENCE"
    CONDITION_CONFLICT = "CONDITION_CONFLICT"
    MISSING_ITEM_REPORTED = "MISSING_ITEM_REPORTED"
    DAMAGE_ORIGIN_UNCERTAIN = "DAMAGE_ORIGIN_UNCERTAIN"
    UNKNOWN_CONDITION_LABEL = "UNKNOWN_CONDITION_LABEL"


class Finding(BaseModel):
    finding_id: str
    type: FindingType
    severity: Severity
    confidence: Confidence
    summary: str
    supporting_event_ids: list[str] = Field(min_length=1)
    conflicting_event_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    benign_explanations: list[str] = Field(default_factory=list)


class TimelineEntry(BaseModel):
    event_id: str
    event_type: str
    event_timestamp: datetime
    source_service: str
    duplicate_deliveries: int = 0


class Completeness(BaseModel):
    status: Literal["COMPLETE", "PARTIAL", "INSUFFICIENT"]
    ratio: float
    present: list[str]
    missing: list[str]


class GraphNode(BaseModel):
    id: str
    kind: Literal["return", "event", "finding"]
    label: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: Literal["belongs_to", "precedes", "supported_by", "conflicts_with", "about"]


class EvidenceGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ReconciliationResult(BaseModel):
    return_id: str
    timeline: list[TimelineEntry]
    completeness: Completeness
    findings: list[Finding]
    metrics: dict[str, float | int | None]
    facts: dict[str, str | None] = Field(default_factory=dict)
    graph: EvidenceGraph
    disclaimer: str = DISCLAIMER
