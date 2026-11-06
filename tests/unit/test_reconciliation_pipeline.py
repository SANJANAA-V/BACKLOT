import random

from fastapi.testclient import TestClient

from scripts.generate_data import generate
from services.api.app import create_app
from services.evidence_worker.handlers import persist_and_reconcile, record_duplicate
from services.evidence_worker.reconciler import reconcile
from shared.database.repository import load_return_events
from shared.database.session import make_session_factory
from shared.messaging.broker import InMemoryBroker
from shared.messaging.runner import run_worker


def _run_pipeline():
    events, metas = generate(42, 3)
    shuffled = events[:]; random.Random(9).shuffle(shuffled)  # arbitrary cross-return and in-return disorder
    b, sf = InMemoryBroker(3), make_session_factory("sqlite://")
    for e in shuffled:
        b.publish("ev", e.return_id, e.model_dump_json().encode())
    run_worker(b.consumer("evidence_worker", "ev"), sf, "evidence_worker", persist_and_reconcile, b, "dlq",
               on_duplicate=record_duplicate, idle_exit=True, sleep=lambda s: None)
    return sf, metas


def test_snapshots_converge_to_fresh_reconciliation_despite_disorder():
    sf, metas = _run_pipeline()
    client = TestClient(create_app(sf))
    for m in metas:
        rid = m["return_id"]
        with sf() as s:
            events, dups = load_return_events(s, rid)
        fresh = reconcile(rid, events, dups).model_dump(mode="json")
        body = client.get(f"/returns/{rid}/reconciliation").json()
        assert body["source"] == "snapshot" and body["result"] == fresh
        assert client.get(f"/returns/{rid}/reconciliation?live=true").json()["result"] == fresh


def test_duplicate_scenario_reports_redelivery_through_pipeline():
    sf, metas = _run_pipeline()
    rid = next(m["return_id"] for m in metas if m["scenario"] == "duplicate_logistics_event")
    res = TestClient(create_app(sf)).get(f"/returns/{rid}/reconciliation").json()["result"]
    assert any(f["type"] == "DUPLICATE_EVENT" for f in res["findings"])


def test_unknown_return_is_404():
    assert TestClient(create_app(make_session_factory("sqlite://"))).get("/returns/NOPE/reconciliation").status_code == 404
