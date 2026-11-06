from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from services.evidence_worker.reconciler import reconcile
from services.investigation_worker.orchestrator import Pipeline, investigate
from shared.database.repository import (add_audit, get_snapshot, latest_investigation, list_audit, list_return_ids,
                                        load_return_events, review_status)
from shared.schemas.recovery import RecoveryAction
from services.ml_worker.features import RiskFeatures, build_features
from services.ml_worker.model import RiskModel, RiskScore
from shared.schemas.evidence import ReconciliationResult


class ReviewRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    outcome: Literal["CONFIRMED", "OVERRIDDEN", "NEEDS_MORE_INFO"]
    final_action: RecoveryAction | None = None
    note: str = Field(default="", max_length=1000)


def create_app(session_factory: sessionmaker[Session], risk_model: RiskModel | None = None,
               pipeline: Pipeline | None = None) -> FastAPI:
    app = FastAPI(title="BACKLOT API")
    pl = pipeline or Pipeline(risk_model=risk_model)
    risk_model = risk_model or pl.risk_model

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    def _result(return_id: str, live: bool) -> dict:
        with session_factory() as s:
            if not live and (snap := get_snapshot(s, return_id)):
                return {"source": "snapshot", "event_count": snap.event_count,
                        "result": ReconciliationResult.model_validate(snap.result).model_dump(mode="json")}
            events, dups = load_return_events(s, return_id)
        if not events:
            raise HTTPException(404, f"no events for return {return_id}")
        return {"source": "live", "event_count": len(events),
                "result": reconcile(return_id, events, dups).model_dump(mode="json")}

    @app.get("/returns/{return_id}/reconciliation")
    def get_reconciliation(return_id: str, live: bool = False) -> dict:
        """Snapshot written by the evidence worker (eventually consistent) unless live=true,
        which recomputes from stored events."""
        return _result(return_id, live)

    def _model() -> RiskModel:
        if risk_model is None:
            raise HTTPException(503, "risk model not available; run `make train`")
        return risk_model

    @app.post("/risk/score", response_model=RiskScore)
    def score_features(features: RiskFeatures) -> RiskScore:
        return _model().score(features)

    @app.get("/returns/{return_id}/risk")
    def get_risk(return_id: str, live: bool = False) -> dict:
        model = _model()
        r = _result(return_id, live)
        feats = build_features(r["result"]["metrics"])
        return {"return_id": return_id, "reconciliation_source": r["source"],
                "features": feats.model_dump(), "risk": model.score(feats).model_dump()}

    @app.get("/returns")
    def list_returns(limit: int = 200) -> dict:
        with session_factory() as s:
            return {"return_ids": list_return_ids(s, min(limit, 1000))}

    @app.post("/returns/{return_id}/investigate")
    def run_investigation(return_id: str) -> dict:
        """Runs reconciliation, risk, vision, decision and report; stores the run and audit trail."""
        with session_factory() as s:
            try:
                return investigate(s, return_id, pl).model_dump(mode="json")
            except LookupError:
                raise HTTPException(404, f"no events for return {return_id}") from None

    @app.get("/returns/{return_id}/investigation")
    def get_investigation(return_id: str) -> dict:
        with session_factory() as s:
            row = latest_investigation(s, return_id)
            if row is None:
                raise HTTPException(404, "no investigation yet; POST /returns/{id}/investigate")
            b = dict(row.bundle)
            b["review_status"] = review_status(s, return_id, b["decision"]["human_review_required"])
            return b

    @app.get("/returns/{return_id}/audit")
    def get_audit(return_id: str) -> dict:
        with session_factory() as s:
            return {"return_id": return_id, "entries": [
                {"id": a.id, "actor": a.actor, "action": a.action, "detail": a.detail,
                 "created_at": a.created_at.isoformat()} for a in list_audit(s, return_id)]}

    @app.post("/returns/{return_id}/review")
    def post_review(return_id: str, req: ReviewRequest) -> dict:
        """A human reviewer records the outcome. Nothing else in the system can finalise a decision."""
        if req.outcome == "OVERRIDDEN" and req.final_action is None:
            raise HTTPException(422, "final_action is required when outcome is OVERRIDDEN")
        with session_factory() as s:
            row = latest_investigation(s, return_id)
            if row is None:
                raise HTTPException(409, "run an investigation before recording a review")
            add_audit(s, return_id, f"human:{req.reviewer}", "HUMAN_REVIEW",
                      {"outcome": req.outcome, "final_action": req.final_action.value if req.final_action else None,
                       "note": req.note, "reviewed_run_id": row.run_id})
            return {"return_id": return_id,
                    "review_status": review_status(s, return_id, row.bundle["decision"]["human_review_required"])}

    return app


def create_default_app() -> FastAPI:
    """uvicorn services.api.app:create_default_app --factory --port 8001

    VISION_ADAPTER = none (default) | simulated (synthetic demo annotations) | anthropic (needs ANTHROPIC_API_KEY + local images).
    The LLM report is used only if ANTHROPIC_API_KEY is set; otherwise the deterministic report is served."""
    import json
    import os
    from pathlib import Path

    from services.investigation_worker.report import AnthropicLLM
    from services.vision_worker.adapters import AnthropicVisionAdapter, SimulatedVisionAdapter
    from shared.database.session import make_session_factory
    from shared.utilities.cache import cache_from_env
    from shared.utilities.config import get_settings
    st = get_settings()
    model = RiskModel.load(st.risk_model_path) if Path(st.risk_model_path).exists() else None
    mode, adapter = os.getenv("VISION_ADAPTER", "none"), None
    if mode == "simulated":
        ann = Path("data/samples/image_annotations.json")
        adapter = SimulatedVisionAdapter(json.loads(ann.read_text()) if ann.exists() else {})
    elif mode == "anthropic":
        adapter = AnthropicVisionAdapter.from_env()
    pl = Pipeline(risk_model=model, vision_adapter=adapter, llm=AnthropicLLM.from_env(), cache=cache_from_env())
    return create_app(make_session_factory(st.database_url), pipeline=pl)
