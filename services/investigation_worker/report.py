"""Grounded investigation report. The LLM only EXPLAINS the decision engine's output; everything it returns is
validated against the retrieved context and rejected (-> deterministic fallback) if it invents ids, policies,
actions or percentages."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from shared.schemas.evidence import ReconciliationResult
from shared.schemas.investigation import FindingExplanation, InvestigationReport, PolicyRef, ReportMeta
from shared.schemas.recovery import RecoveryDecision
from shared.schemas.vision import VisionReport
from shared.utilities.anthropic_client import AnthropicClient, ProviderError, ProviderUnavailable
from shared.utilities.jsonutil import extract_json


class LLMProvider(Protocol):
    name: str

    def complete_json(self, system: str, user: str) -> str: ...


class AnthropicLLM:
    def __init__(self, client: AnthropicClient) -> None:
        self.client, self.name = client, f"llm:{client.model}"

    @classmethod
    def from_env(cls, **kw) -> "AnthropicLLM | None":
        c = AnthropicClient.from_env("LLM_MODEL", **kw)
        return cls(c) if c else None

    def complete_json(self, system: str, user: str) -> str:
        return self.client.complete(system, user, max_tokens=1500)


@dataclass
class Context:
    result: ReconciliationResult
    risk: dict | None
    vision: VisionReport | None
    decision: RecoveryDecision
    policies: list[PolicyRef]


SYSTEM = """You write investigation reports for a human reviewer of an e-commerce return.
Rules:
- Use ONLY the facts, event ids, finding ids and policy section ids in the provided JSON. Never invent any.
- Findings are inconsistencies, not proof of wrongdoing. Never accuse anyone of fraud. Mention benign explanations.
- recommended_action and human_review_required MUST equal decision.action and decision.human_review_required; you explain, you do not decide.
- Do not state any percentage or confidence number that is not in the input.
- Put missing or uncertain information in "uncertainty".
- Reply with ONE JSON object with keys: investigation_summary, evidence_findings (list of {finding_id, explanation}),
  supporting_event_ids, relevant_policy_references (policy section ids), uncertainty (list of strings),
  recommended_action, recommendation_reason, human_review_required."""


def context_json(c: Context) -> dict:
    r = c.result
    return {
        "return_id": r.return_id,
        "timeline": [{"event_id": t.event_id, "type": t.event_type, "time": t.event_timestamp.isoformat(),
                      "source": t.source_service} for t in r.timeline],
        "completeness": r.completeness.model_dump(),
        "findings": [{"finding_id": f.finding_id, "type": f.type.value, "severity": f.severity.value,
                      "confidence": f.confidence.value, "summary": f.summary,
                      "supporting_event_ids": f.supporting_event_ids, "benign_explanations": f.benign_explanations}
                     for f in r.findings],
        "risk": c.risk and {"score": c.risk["score"], "flagged": c.risk["flagged"], "note": "synthetic-trained ranking score"},
        "image_observations_unverified": c.vision and [d.model_dump() for d in c.vision.differences],
        "decision": {"action": c.decision.action.value, "human_review_required": c.decision.human_review_required,
                     "reasons": c.decision.reasons},
        "policy_sections": [{"id": p.chunk_id, "title": p.title, "text": p.text} for p in c.policies],
    }


def deterministic_report(c: Context) -> InvestigationReport:
    r, d = c.result, c.decision
    span = (r.timeline[-1].event_timestamp - r.timeline[0].event_timestamp).days
    srcs = sorted({t.source_service for t in r.timeline})
    summary = (f"Return {r.return_id}: {len(r.timeline)} events from {', '.join(srcs)} over {span} days. "
               f"Evidence completeness is {r.completeness.status}. {len(r.findings)} finding(s) were raised; "
               f"these are inconsistencies in the evidence, not proof of wrongdoing.")
    unc = [f"Missing evidence: {m}" for m in r.completeness.missing]
    unc += [f"Low-confidence finding: {f.type.value}" for f in r.findings if f.confidence.value == "LOW"]
    unc.append("Risk score unavailable." if not c.risk else "Risk score comes from a model trained on synthetic data.")
    if c.vision is None:
        unc.append("No image analysis was run.")
    else:
        unc += [f"Image analysis {f.status.lower()} for {f.uploaded_by} image: {f.error}" for f in c.vision.findings if f.status != "OK"]
    ids = list(dict.fromkeys(i for f in r.findings for i in f.supporting_event_ids)) or [r.timeline[0].event_id]
    return InvestigationReport(
        investigation_summary=summary,
        evidence_findings=[FindingExplanation(finding_id=f.finding_id, explanation=(
            f"{f.summary} ({f.severity.value} severity, {f.confidence.value} confidence). Possible benign causes: "
            + ("; ".join(f.benign_explanations) or "none recorded") + ".")[:800]) for f in r.findings],
        supporting_event_ids=ids, relevant_policy_references=[p.chunk_id for p in c.policies],
        uncertainty=unc, recommended_action=d.action,
        recommendation_reason=" ".join(d.reasons)[:1500], human_review_required=d.human_review_required)


def grounding_problems(rep: InvestigationReport, c: Context, ctx_text: str) -> list[str]:
    probs = []
    ev_ids = {t.event_id for t in c.result.timeline}
    f_ids = {f.finding_id for f in c.result.findings}
    p_ids = {p.chunk_id for p in c.policies}
    if bad := set(rep.supporting_event_ids) - ev_ids:
        probs.append(f"unknown event ids {sorted(bad)}")
    if bad := {e.finding_id for e in rep.evidence_findings} - f_ids:
        probs.append(f"unknown finding ids {sorted(bad)}")
    if bad := set(rep.relevant_policy_references) - p_ids:
        probs.append(f"unknown policy references {sorted(bad)}")
    if rep.recommended_action != c.decision.action:
        probs.append("recommended_action differs from the decision engine")
    if rep.human_review_required != c.decision.human_review_required:
        probs.append("human_review_required differs from the decision engine")
    text = " ".join([rep.investigation_summary, rep.recommendation_reason, *rep.uncertainty,
                     *(e.explanation for e in rep.evidence_findings)])
    allowed = set(re.findall(r"\d+(?:\.\d+)?\s*%", ctx_text))
    if bad := {m.replace(" ", "") for m in re.findall(r"\d+(?:\.\d+)?\s*%", text)} - {a.replace(" ", "") for a in allowed}:
        probs.append(f"percentages not present in the evidence: {sorted(bad)}")
    return probs


def generate_report(c: Context, llm: LLMProvider | None) -> tuple[InvestigationReport, ReportMeta]:
    fallback = deterministic_report(c)
    if llm is None:
        return fallback, ReportMeta(generated_by="deterministic_fallback", fallback_reason="no LLM provider configured")
    ctx_text = json.dumps(context_json(c))
    try:
        raw = llm.complete_json(SYSTEM, ctx_text)
        rep = InvestigationReport.model_validate(extract_json(raw))
    except (ProviderUnavailable, ProviderError) as exc:
        return fallback, ReportMeta(generated_by="deterministic_fallback", fallback_reason=f"LLM provider failure: {exc}")
    except (ValueError, ValidationError) as exc:
        return fallback, ReportMeta(generated_by="deterministic_fallback",
                                    fallback_reason=f"LLM output failed schema validation: {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001
        return fallback, ReportMeta(generated_by="deterministic_fallback", fallback_reason=f"LLM error: {type(exc).__name__}")
    if probs := grounding_problems(rep, c, ctx_text):
        return fallback, ReportMeta(generated_by="deterministic_fallback",
                                    fallback_reason="LLM output failed grounding checks: " + "; ".join(probs))
    return rep, ReportMeta(generated_by=llm.name)
