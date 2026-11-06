"""Real-broker test. Skipped unless BACKLOT_KAFKA_IT=1 and a broker is on KAFKA_BOOTSTRAP_SERVERS.
Start one with: docker compose up -d redpanda"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.getenv("BACKLOT_KAFKA_IT") != "1", reason="needs a Kafka/Redpanda broker")


def test_pipeline_over_real_kafka():
    from scripts.generate_data import generate
    from services.evidence_worker.handlers import persist_and_reconcile, record_duplicate
    from shared.database.models import EventRecord
    from shared.database.session import make_session_factory
    from shared.messaging.kafka_io import KafkaConsumer, KafkaPublisher, create_topics
    from shared.messaging.runner import run_worker

    bs = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    tag = uuid.uuid4().hex[:8]
    topic, dlq, group = f"it-events-{tag}", f"it-dlq-{tag}", f"it-group-{tag}"
    create_topics(bs, [topic, dlq], 3)
    events, _ = generate(42, 1)
    pub = KafkaPublisher(bs)
    for e in events:
        pub.publish(topic, e.return_id, e.model_dump_json().encode())
    sf = make_session_factory("sqlite://")
    seen = {"n": 0}
    def stop():  # consume until all published messages were handled
        with sf() as s:
            return s.query(EventRecord).count() >= len({e.event_id for e in events})
    run_worker(KafkaConsumer(bs, group, topic), sf, group, persist_and_reconcile, pub, dlq,
               on_duplicate=record_duplicate, stop=stop, poll_timeout=2.0)
    with sf() as s:
        assert s.query(EventRecord).count() == len({e.event_id for e in events})
