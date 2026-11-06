"""Transparent recovery decision engine. Pure and deterministic; no LLM involvement.

Review gate (any fired rule -> HUMAN_REVIEW), then an economics path. Every rule is recorded in rule_trace.
"""
from __future__ import annotations

from pydantic import BaseModel

from services.recovery_worker.economics import (ECONOMICS, GRADE, HANDLING_COST, MIN_REFURB_MARGIN,
                                                REPAIR_GAIN_FACTOR, VALUE_FACTOR)
from shared.schemas.evidence import ReconciliationResult, Severity
from shared.schemas.recovery import RecoveryAction as A, RecoveryDecision, RuleTrace
from shared.schemas.vision import VisionReport


# Photo differences that contradict the claimed product/damage. PACKAGING_DIFFERENCE is advisory only
# (it may indicate transit damage, which does not contradict the customer's account).
CONTRADICTING = {"DAMAGE_VISIBILITY", "CATEGORY_MISMATCH", "EXPECTED_CATEGORY_MISMATCH"}


class RecoveryInputs(BaseModel):
    product_id: str | None
    reported_condition: str | None
    observed_condition: str | None
    completeness_status: str
    risk_score: float | None
    risk_flagged: bool | None
    high_findings: int
    medium_findings: int
    vision_difference_count: int


def inputs_from(result: ReconciliationResult, risk: dict | None, vision: VisionReport | None) -> RecoveryInputs:
    sev = [f.severity for f in result.findings]
    return RecoveryInputs(
        product_id=result.facts.get("product_id"), reported_condition=result.facts.get("reported_condition"),
        observed_condition=result.facts.get("observed_condition"), completeness_status=result.completeness.status,
        risk_score=risk["score"] if risk else None, risk_flagged=risk["flagged"] if risk else None,
        high_findings=sum(s == Severity.HIGH for s in sev), medium_findings=sum(s == Severity.MEDIUM for s in sev),
        vision_difference_count=sum(d.kind in CONTRADICTING for d in vision.differences) if vision else 0)


def decide(i: RecoveryInputs) -> RecoveryDecision:
    trace: list[RuleTrace] = []
    reasons: list[str] = []

    def rule(rid: str, desc: str, fired: bool, **detail) -> bool:
        trace.append(RuleTrace(rule_id=rid, description=desc, fired=fired, detail=detail))
        return fired

    eco = ECONOMICS.get(i.product_id or "")
    cond = i.observed_condition
    review = [
        (rule("R1", "Evidence completeness is INSUFFICIENT", i.completeness_status == "INSUFFICIENT",
              status=i.completeness_status), "Critical evidence is missing."),
        (rule("R2", "Risk model flagged the return or a HIGH-severity finding exists",
              bool(i.risk_flagged) or i.high_findings > 0, risk_flagged=i.risk_flagged, risk_score=i.risk_score,
              high_findings=i.high_findings), "The risk model or a high-severity evidence finding calls for review."),
        (rule("R3", "Evidence is PARTIAL and a MEDIUM+ finding exists",
              i.completeness_status == "PARTIAL" and i.medium_findings > 0, medium_findings=i.medium_findings),
         "Some expected evidence is missing alongside a medium-severity finding."),
        (rule("R4", "Image analysis found contradicting differences (advisory, model-generated)", i.vision_difference_count > 0,
              differences=i.vision_difference_count), "Image analysis reported differences between photos."),
        (rule("R5", "Product economics or a recognised inspected condition is unavailable",
              eco is None or cond not in GRADE, product_id=i.product_id, observed_condition=cond),
         "Cannot compute a recovery value for this product/condition."),
    ]
    reasons = [msg for fired, msg in review if fired]
    if i.risk_flagged is None:
        reasons.append("Note: risk score was not available and was not used.")
    inputs = i.model_dump()
    if any(f for f, _ in review):
        return RecoveryDecision(action=A.HUMAN_REVIEW, human_review_required=True, reasons=reasons,
                                inputs=inputs, rule_trace=trace)

    grade = GRADE[cond]
    price = eco.price
    liq_net = price * VALUE_FACTOR["liquidation"] - eco.liquidation_cost
    inputs.update(grade=grade, price=price, liquidation_net=liq_net)
    resale_blocked = eco.hygiene_restricted and grade != "A"
    rule("R6", "Policy constraint: opened hygiene-restricted items cannot be resold as new", resale_blocked,
         hygiene_restricted=eco.hygiene_restricted, grade=grade)
    refurb_net = price * VALUE_FACTOR["refurbished"] - eco.refurb_cost - HANDLING_COST
    repair_net = price * VALUE_FACTOR["repaired"] - eco.repair_cost - HANDLING_COST
    inputs.update(refurbish_net=refurb_net, repair_net=repair_net)

    if grade in ("A", "B") and not resale_blocked:
        rule("R7", "Grade A/B and resale allowed -> RESALE", True, grade=grade)
        return _done(A.RESALE, [f"Condition grade {grade} and policy allows resale."], inputs, trace)
    if grade in ("A", "B", "C"):
        ok = rule("R8", f"Refurbish net value >= {MIN_REFURB_MARGIN}", refurb_net >= MIN_REFURB_MARGIN, refurbish_net=refurb_net)
        why = ("Resale is not allowed for this opened item" if resale_blocked else f"Condition grade {grade}")
        if ok:
            return _done(A.REFURBISH, [f"{why}; refurbishing nets {refurb_net:.0f}."], inputs, trace)
        rule("R9", "Fallback: liquidate if net value positive else recycle", True, liquidation_net=liq_net)
        return _liq_or_recycle(liq_net, f"{why}, but refurbishing is not worthwhile", inputs, trace)
    ok = rule("R10", f"Repair net >= {REPAIR_GAIN_FACTOR}x liquidation net (and positive)",
              repair_net > 0 and repair_net >= REPAIR_GAIN_FACTOR * liq_net, repair_net=repair_net, liquidation_net=liq_net)
    if ok:
        return _done(A.REPAIR, [f"Damaged (grade D); repair nets {repair_net:.0f} vs liquidation {liq_net:.0f}."], inputs, trace)
    rule("R9", "Fallback: liquidate if net value positive else recycle", True, liquidation_net=liq_net)
    return _liq_or_recycle(liq_net, "Damaged and repair is not worthwhile", inputs, trace)


def _liq_or_recycle(liq_net, why, inputs, trace):
    if liq_net > 0:
        return _done(A.LIQUIDATE, [f"{why}; liquidation nets {liq_net:.0f}."], inputs, trace)
    return _done(A.RECYCLE, [f"{why}; liquidation would lose money ({liq_net:.0f})."], inputs, trace)


def _done(action, reasons, inputs, trace):
    return RecoveryDecision(action=action, human_review_required=False, reasons=reasons, inputs=inputs, rule_trace=trace)
