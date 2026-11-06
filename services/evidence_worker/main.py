import logging

from services.evidence_worker.handlers import persist_and_reconcile, record_duplicate
from shared.database.session import make_session_factory
from shared.messaging.kafka_io import KafkaConsumer, KafkaPublisher
from shared.messaging.runner import run_worker
from shared.utilities.config import get_settings

GROUP = "evidence_worker"

if __name__ == "__main__":
    s = get_settings()
    logging.basicConfig(level=s.log_level)
    run_worker(KafkaConsumer(s.kafka_bootstrap_servers, GROUP, s.topic_events),
               make_session_factory(s.database_url), GROUP, persist_and_reconcile,
               KafkaPublisher(s.kafka_bootstrap_servers), s.topic_dlq, on_duplicate=record_duplicate)
