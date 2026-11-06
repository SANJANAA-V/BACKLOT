import json

import pytest

from scripts.generate_data import generate
from services.evidence_worker.handlers import persist_and_reconcile, record_duplicate
from shared.database.models import EventRecord, ProcessedEvent
from shared.database.repository import get_ledger
from shared.database.session import make_session_factory
from shared.messaging.broker import InMemoryBroker
from shared.messaging.processor import DEAD_LETTERED, DUPLICATE_SKIPPED, PROCESSED, process_message
from shared.messaging.runner import run_worker

NOSLEEP = lambda s: None  # noqa: E731
G = "evidence_worker"


@pytest.fixture
def sf():
    return make_session_factory("sqlite://")


def _msg(b, payload: bytes, key="k"):
    b.publish("ev", key, payload)
    return b.consumer("probe", "ev").poll()


def test_end_to_end_pipeline_dedups_and_counts_redeliveries(sf):
    events, metas = generate(42, 3)
    b = InMemoryBroker(3)
    for e in events:
        b.publish("ev", e.return_id, e.model_dump_json().encode())
    counts = run_worker(b.consumer(G, "ev"), sf, G, persist_and_reconcile, b, "dlq",
                        on_duplicate=record_duplicate, idle_exit=True, sleep=NOSLEEP)
    unique = {e.event_id for e in events}
    with sf() as s:
        assert s.query(EventRecord).count() == len(unique)
        assert sum(r.duplicate_deliveries for r in s.query(EventRecord)) == len(events) - len(unique)
        assert s.query(ProcessedEvent).filter_by(status="DONE").count() == len(unique)
    assert counts[PROCESSED] == len(unique) and counts[DUPLICATE_SKIPPED] == len(events) - len(unique)
    assert b.messages("dlq") == []


def test_retry_then_success(sf):
    e = generate(1, 1)[0][0]; b = InMemoryBroker(1)
    calls = {"n": 0}
    def flaky(session, ev):
        calls["n"] += 1
        if calls["n"] < 3: raise RuntimeError("db hiccup")
        persist_and_reconcile(session, ev)
    with sf() as s:
        out = process_message(s, G, _msg(b, e.model_dump_json().encode()), flaky, b, "dlq", sleep=NOSLEEP)
        assert out == PROCESSED
        row = get_ledger(s, G, e.event_id)
        assert (row.status, row.attempts) == ("DONE", 3)


def test_exhausted_retries_go_to_dlq_and_are_not_reprocessed(sf):
    e = generate(1, 1)[0][0]; b = InMemoryBroker(1); delays = []
    def boom(session, ev): raise RuntimeError("always")
    with sf() as s:
        m = _msg(b, e.model_dump_json().encode())
        assert process_message(s, G, m, boom, b, "dlq", max_attempts=3, base_delay=1, sleep=delays.append) == DEAD_LETTERED
        assert delays == [1, 2]  # exponential backoff between attempts
        (d,) = b.messages("dlq")
        body = json.loads(d.value)
        assert body["attempts"] == 3 and "RuntimeError" in body["error"] and body["source"]["offset"] == m.offset
        assert process_message(s, G, m, boom, b, "dlq", sleep=NOSLEEP) == DUPLICATE_SKIPPED
        assert len(b.messages("dlq")) == 1


def test_poison_pill_dead_lettered_without_retry(sf):
    b = InMemoryBroker(1); calls = []
    with sf() as s:
        out = process_message(s, G, _msg(b, b"{not json"), lambda s_, e: calls.append(e), b, "dlq", sleep=NOSLEEP)
    assert out == DEAD_LETTERED and calls == [] and len(b.messages("dlq")) == 1


def test_crash_before_commit_redelivers_and_is_idempotent(sf):
    e = generate(1, 1)[0][0]; b = InMemoryBroker(1)
    b.publish("ev", e.return_id, e.model_dump_json().encode())
    c = b.consumer(G, "ev")
    with sf() as s:  # first delivery processed, but worker "crashes" before commit
        process_message(s, G, c.poll(), persist_and_reconcile, b, "dlq", sleep=NOSLEEP)
    run_worker(c, sf, G, persist_and_reconcile, b, "dlq", on_duplicate=record_duplicate, idle_exit=True, sleep=NOSLEEP)
    with sf() as s:
        assert s.query(EventRecord).count() == 1
        assert s.query(EventRecord).one().duplicate_deliveries == 1
