"""At-least-once consumption made effectively-once via a per-group idempotency ledger,
bounded retries with backoff, and a dead-letter queue."""
from __future__ import annotations

import json
import logging
import time
from typing import Callable

from pydantic import ValidationError
from sqlalchemy.orm import Session

from shared.database.repository import get_ledger, set_ledger
from shared.messaging.broker import Message, Publisher
from shared.schemas.events import ReturnEvent

log = logging.getLogger("backlot.processor")

PROCESSED, DUPLICATE_SKIPPED, DEAD_LETTERED = "PROCESSED", "DUPLICATE_SKIPPED", "DEAD_LETTERED"
Handler = Callable[[Session, ReturnEvent], None]


def _dead_letter(dlq: Publisher, dlq_topic: str, group: str, msg: Message, error: str, attempts: int) -> None:
    body = {"group": group, "error": error, "attempts": attempts,
            "source": {"topic": msg.topic, "partition": msg.partition, "offset": msg.offset},
            "value": msg.value.decode(errors="replace")}
    dlq.publish(dlq_topic, msg.key, json.dumps(body).encode())


def process_message(session: Session, group: str, msg: Message, handler: Handler,
                    dlq: Publisher, dlq_topic: str, max_attempts: int = 3, base_delay: float = 0.5,
                    sleep: Callable[[float], None] = time.sleep,
                    on_duplicate: Callable[[Session, ReturnEvent], None] | None = None) -> str:
    try:
        event = ReturnEvent.model_validate_json(msg.value)
    except (ValidationError, ValueError) as exc:  # poison pill: retrying cannot help
        ledger_id = f"invalid:{msg.topic}:{msg.partition}:{msg.offset}"
        _dead_letter(dlq, dlq_topic, group, msg, f"invalid event: {exc}", 0)
        set_ledger(session, group, ledger_id, "DEAD_LETTER", 0)
        log.warning("dead-lettered invalid message", extra={"group": group, "ledger_id": ledger_id})
        return DEAD_LETTERED

    row = get_ledger(session, group, event.event_id)
    if row is not None and row.status in ("DONE", "DEAD_LETTER"):
        if on_duplicate:
            on_duplicate(session, event)
        return DUPLICATE_SKIPPED

    last_err = ""
    for attempt in range(1, max_attempts + 1):
        try:
            handler(session, event)
            set_ledger(session, group, event.event_id, "DONE", attempt)
            return PROCESSED
        except Exception as exc:  # noqa: BLE001 - any handler failure is retried
            session.rollback()
            last_err = f"{type(exc).__name__}: {exc}"
            set_ledger(session, group, event.event_id, "FAILED", attempt)
            log.warning("handler failed", extra={"group": group, "event_id": event.event_id, "attempt": attempt})
            if attempt < max_attempts:
                sleep(base_delay * 2 ** (attempt - 1))
    _dead_letter(dlq, dlq_topic, group, msg, last_err, max_attempts)
    set_ledger(session, group, event.event_id, "DEAD_LETTER", max_attempts)
    return DEAD_LETTERED
