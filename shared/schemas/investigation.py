from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from shared.schemas.evidence import ReconciliationResult
from shared.schemas.recovery import RecoveryAction, RecoveryDecision
from shared.schemas.vision import VisionReport


class FindingExplanation(BaseModel):
    finding_id: str
    explanation: str = Field(max_length=800)


class InvestigationReport(BaseModel):
    """Structured investigation output (LLM or deterministic fallback)."""
    investigation_summary: str = Field(max_length=2500)
    evidence_findings: list[FindingExplanation]
    supporting_event_ids: list[str]
    relevant_policy_references: list[str]
    uncertainty: list[str]
    recommended_action: RecoveryAction
    recommendation_reason: str = Field(max_length=1500)
    human_review_required: bool


class PolicyRef(BaseModel):
    chunk_id: str
    title: str
    doc: str
    version: str
    score: float
    text: str


class ReportMeta(BaseModel):
    generated_by: str  # "llm:<model>" or "deterministic_fallback"
    fallback_reason: str | None = None


class InvestigationBundle(BaseModel):
    run_id: str
    return_id: str
    reconciliation: ReconciliationResult
    risk: dict | None
    vision: VisionReport | None
    decision: RecoveryDecision
    policy_references: list[PolicyRef]
    report: InvestigationReport
    report_meta: ReportMeta
    review_status: Literal["NOT_REQUIRED", "PENDING", "CONFIRMED", "OVERRIDDEN", "NEEDS_MORE_INFO"]
