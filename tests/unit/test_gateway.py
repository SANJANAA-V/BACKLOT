import json

from fastapi.testclient import TestClient

from scripts.generate_data import generate
from services.event_gateway.app import create_app
from shared.messaging.broker import InMemoryBroker

EV = generate(42, 1)[0][0]


def test_valid_event_accepted_and_keyed_by_return_id():
    b = InMemoryBroker(3)
    r = TestClient(create_app(b)).post("/events", json=EV.model_dump(mode="json"))
    assert r.status_code == 202
    (m,) = b.messages("return-events")
    assert m.key == EV.return_id and json.loads(m.value)["event_id"] == EV.event_id


def test_invalid_event_rejected_not_published():
    b = InMemoryBroker(3)
    bad = EV.model_dump(mode="json"); bad["event_type"] = "NOPE"
    r = TestClient(create_app(b)).post("/events", json=bad)
    assert r.status_code == 422 and b.messages("return-events") == []


def test_bus_failure_returns_503():
    class Down:
        def publish(self, *a, **k): raise RuntimeError("broker down")
    r = TestClient(create_app(Down())).post("/events", json=EV.model_dump(mode="json"))
    assert r.status_code == 503
