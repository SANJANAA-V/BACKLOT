import pytest

from scripts.generate_data import generate
from shared.database.repository import claim_event, events_for_return, store_event
from shared.database.session import make_session_factory


@pytest.fixture
def session():
    with make_session_factory("sqlite://")() as s:
        yield s


def test_duplicate_event_stored_once_and_counted(session):
    events, metas = generate(42, 3)
    rid = next(m["return_id"] for m in metas if m["scenario"] == "duplicate_logistics_event")
    for e in [e for e in events if e.return_id == rid]:
        store_event(session, e)
    rows = events_for_return(session, rid)
    assert len({r.event_id for r in rows}) == len(rows)
    assert sum(r.duplicate_deliveries for r in rows) == 1


def test_claim_is_idempotent_per_group(session):
    assert claim_event(session, "evidence_worker", "e1") is True
    assert claim_event(session, "evidence_worker", "e1") is False
    assert claim_event(session, "ml_worker", "e1") is True  # other group still processes


def test_out_of_order_arrival_returns_timestamp_order(session):
    events, _ = generate(7, 5)
    rid = events[0].return_id
    mine = [e for e in events if e.return_id == rid]
    for e in reversed(mine):
        store_event(session, e)
    ts = [r.event_timestamp for r in events_for_return(session, rid)]
    assert ts == sorted(ts)
