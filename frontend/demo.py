"""Standalone demo bootstrap: in-memory DB seeded with SYNTHETIC returns, simulated vision, deterministic report.
No Kafka, Postgres or API credentials needed. If ANTHROPIC_API_KEY is set, the LLM report is used."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from scripts.generate_data import generate
from services.api.app import create_app
from services.evidence_worker.handlers import persist_and_reconcile
from services.investigation_worker.orchestrator import Pipeline
from services.investigation_worker.report import AnthropicLLM
from services.ml_worker.model import RiskModel
from services.ml_worker.training import train
from services.vision_worker.adapters import SimulatedVisionAdapter
from shared.database.session import make_session_factory
from shared.utilities.cache import InMemoryCache


def build_demo(model_path: str = "models/risk_model.joblib") -> tuple[FastAPI, dict[str, str]]:
    events, metas = generate(42, 1)
    sf = make_session_factory("sqlite://")
    for e in events:
        with sf() as s:
            persist_and_reconcile(s, e)
    model = RiskModel.load(model_path) if Path(model_path).exists() else train(seed=42, n=1500)[0]
    ann = {k: v for m in metas for k, v in m["image_annotations"].items()}
    pl = Pipeline(risk_model=model, vision_adapter=SimulatedVisionAdapter(ann), llm=AnthropicLLM.from_env(),
                  cache=InMemoryCache())
    labels = {m["return_id"]: m["scenario"] for m in metas}
    return create_app(sf, pipeline=pl), labels
