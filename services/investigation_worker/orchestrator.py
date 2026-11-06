"""Runs the full investigation for one return and records an audit entry per step."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from services.evidence_worker.reconciler import reconcile
from services.investigation_worker.rag import Retriever, build_query, default_retriever
from services.investigation_worker.report import Context, LLMProvider, generate_report
from services.ml_worker.features import build_features
from services.ml_worker.model import RiskModel
from services.recovery_worker.economics import ECONOMICS
from services.recovery_worker.engine import decide, inputs_from
from services.vision_worker.adapters import VisionAdapter
from services.vision_worker.analysis import analyze_return
from shared.database.repository import add_audit, load_return_events, review_status, save_investigation
from shared.schemas.investigation import InvestigationBundle, PolicyRef
from shared.utilities.cache import Cache


@dataclass
class Pipeline:
    risk_model: RiskModel | None = None
    vision_adapter: VisionAdapter | None = None
    llm: LLMProvider | None = None
    retriever: Retriever | None = None
    cache: Cache | None = None
    policy_k: int = 4

    def __post_init__(self) -> None:
        if self.retriever is None:
            self.retriever = default_retriever()


def investigate(session: Session, return_id: str, pl: Pipeline) -> InvestigationBundle:
    events, dups = load_return_events(session, return_id)
    if not events:
        raise LookupError(return_id)
    run = uuid.uuid4().hex[:8]

    def audit(actor: str, event: str, **detail) -> None:
        add_audit(session, return_id, actor, event, {"run_id": run, **detail})

    result = reconcile(return_id, events, dups)
    audit("evidence_engine", "RECONCILED", events=len(events), completeness=result.completeness.status,
          findings=[{"id": f.finding_id, "type": f.type.value, "severity": f.severity.value} for f in result.findings])

    risk = None
    if pl.risk_model:
        risk = pl.risk_model.score(build_features(result.metrics)).model_dump()
        audit("risk_model", "RISK_SCORED", score=risk["score"], flagged=risk["flagged"], model_version=risk["model_version"],
              top_factors=[c["feature"] for c in risk["contributions"]])
    else:
        audit("risk_model", "RISK_SKIPPED", reason="no model loaded")

    vision = None
    if pl.vision_adapter:
        eco = ECONOMICS.get(result.facts.get("product_id") or "")
        vision = analyze_return(events, pl.vision_adapter, pl.cache, eco.category if eco else None)
        audit("vision", "VISION_ANALYZED", adapter=vision.adapter, statuses=[f.status for f in vision.findings],
              differences=[d.kind for d in vision.differences])

    decision = decide(inputs_from(result, risk, vision))
    audit("decision_engine", "DECISION_MADE", action=decision.action.value, reasons=decision.reasons,
          rules_fired=[t.rule_id for t in decision.rule_trace if t.fired], inputs=decision.inputs)

    hits = pl.retriever.search(build_query(result, vision), k=pl.policy_k)
    refs = [PolicyRef(chunk_id=c.chunk_id, title=c.title, doc=c.doc, version=c.version, score=s, text=c.text) for c, s in hits]
    report, meta = generate_report(Context(result, risk, vision, decision, refs), pl.llm)
    audit("investigation_report", "REPORT_GENERATED", generated_by=meta.generated_by, fallback_reason=meta.fallback_reason,
          policy_refs=[r.chunk_id for r in refs])

    bundle = InvestigationBundle(run_id=run, return_id=return_id, reconciliation=result, risk=risk, vision=vision,
                                 decision=decision, policy_references=refs, report=report, report_meta=meta,
                                 review_status=review_status(session, return_id, decision.human_review_required))
    save_investigation(session, return_id, run, bundle.model_dump(mode="json"))
    return bundle
