from __future__ import annotations

from collections import Counter
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from shared.messaging.broker import Consumer, Publisher
from shared.messaging.processor import Handler, process_message


def run_worker(consumer: Consumer, session_factory: sessionmaker[Session], group: str, handler: Handler,
               dlq: Publisher, dlq_topic: str, on_duplicate=None,
               stop: Callable[[], bool] = lambda: False, idle_exit: bool = False,
               poll_timeout: float = 1.0, **kwargs) -> Counter:
    """Poll -> process -> commit. Offset is committed only after processing (or dead-lettering)."""
    counts: Counter = Counter()
    while not stop():
        msg = consumer.poll(poll_timeout)
        if msg is None:
            if idle_exit:
                break
            continue
        with session_factory() as session:
            counts[process_message(session, group, msg, handler, dlq, dlq_topic,
                                   on_duplicate=on_duplicate, **kwargs)] += 1
        consumer.commit(msg)
    return counts
