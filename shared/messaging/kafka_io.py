"""Real Kafka/Redpanda implementations (confluent-kafka). Imported lazily by services."""
from __future__ import annotations

from confluent_kafka import Consumer as _KConsumer
from confluent_kafka import KafkaException, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

from shared.messaging.broker import Message


class KafkaPublisher:
    def __init__(self, bootstrap: str) -> None:
        # acks=all + idempotence: no loss on leader failover, no producer-retry duplicates.
        self._p = Producer({"bootstrap.servers": bootstrap, "acks": "all",
                            "enable.idempotence": True})

    def publish(self, topic: str, key: str, value: bytes) -> None:
        err: list = []
        self._p.produce(topic, key=key.encode(), value=value,
                        on_delivery=lambda e, m: err.append(e) if e else None)
        self._p.flush(10)
        if err:
            raise KafkaException(err[0])


class KafkaConsumer:
    def __init__(self, bootstrap: str, group: str, topic: str) -> None:
        self._c = _KConsumer({"bootstrap.servers": bootstrap, "group.id": group,
                              "enable.auto.commit": False, "auto.offset.reset": "earliest"})
        self._c.subscribe([topic])

    def poll(self, timeout: float = 1.0) -> Message | None:
        m = self._c.poll(timeout)
        if m is None:
            return None
        if m.error():
            raise KafkaException(m.error())
        return Message(m.topic(), m.partition(), m.offset(), (m.key() or b"").decode(), m.value())

    def commit(self, msg: Message) -> None:
        self._c.commit(offsets=[TopicPartition(msg.topic, msg.partition, msg.offset + 1)],
                       asynchronous=False)


def create_topics(bootstrap: str, topics: list[str], partitions: int) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap})
    for name, fut in admin.create_topics([NewTopic(t, partitions, 1) for t in topics]).items():
        try:
            fut.result()
        except KafkaException as e:  # already exists is fine
            if "TOPIC_ALREADY_EXISTS" not in str(e):
                raise
