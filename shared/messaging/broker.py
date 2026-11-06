"""Broker abstraction: Publisher/Consumer protocols + an in-memory broker for tests/demos.

The in-memory broker mimics the parts of Kafka the pipeline relies on: partitioning by key,
per-partition ordering, consumer groups with committed offsets, and redelivery of
uncommitted messages. It does NOT model rebalancing, replication or broker failure.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    topic: str
    partition: int
    offset: int
    key: str
    value: bytes


class Publisher(Protocol):
    def publish(self, topic: str, key: str, value: bytes) -> None: ...


class Consumer(Protocol):
    def poll(self, timeout: float = 1.0) -> Message | None: ...
    def commit(self, msg: Message) -> None: ...


def partition_for(key: str, n: int) -> int:
    return zlib.crc32(key.encode()) % n


class InMemoryBroker:
    def __init__(self, partitions: int = 3) -> None:
        self.partitions = partitions
        self._logs: dict[tuple[str, int], list[Message]] = {}
        self._committed: dict[tuple[str, str, int], int] = {}  # (group, topic, partition) -> next offset

    def publish(self, topic: str, key: str, value: bytes) -> None:
        p = partition_for(key, self.partitions)
        log = self._logs.setdefault((topic, p), [])
        log.append(Message(topic, p, len(log), key, value))

    def messages(self, topic: str) -> list[Message]:
        return [m for p in range(self.partitions) for m in self._logs.get((topic, p), [])]

    def consumer(self, group: str, topic: str) -> "InMemoryConsumer":
        return InMemoryConsumer(self, group, topic)


class InMemoryConsumer:
    def __init__(self, broker: InMemoryBroker, group: str, topic: str) -> None:
        self.b, self.group, self.topic = broker, group, topic

    def poll(self, timeout: float = 1.0) -> Message | None:
        for p in range(self.b.partitions):
            log = self.b._logs.get((self.topic, p), [])
            nxt = self.b._committed.get((self.group, self.topic, p), 0)
            if nxt < len(log):
                return log[nxt]  # uncommitted messages are redelivered
        return None

    def commit(self, msg: Message) -> None:
        k = (self.group, msg.topic, msg.partition)
        self.b._committed[k] = max(self.b._committed.get(k, 0), msg.offset + 1)
