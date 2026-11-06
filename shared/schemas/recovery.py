from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RecoveryAction(str, Enum):
    RESALE = "RESALE"
    REFURBISH = "REFURBISH"
    REPAIR = "REPAIR"
    LIQUIDATE = "LIQUIDATE"
    RECYCLE = "RECYCLE"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class RuleTrace(BaseModel):
    rule_id: str
    description: str
    fired: bool
    detail: dict[str, Any] = Field(default_factory=dict)


class RecoveryDecision(BaseModel):
    action: RecoveryAction
    human_review_required: bool
    reasons: list[str]
    inputs: dict[str, Any]
    rule_trace: list[RuleTrace]
    note: str = "Deterministic business rules; demo economics are synthetic. Not produced by an LLM."
