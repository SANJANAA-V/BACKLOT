from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException

from shared.messaging.broker import Publisher
from shared.schemas.events import ReturnEvent

log = logging.getLogger("backlot.gateway")


def create_app(publisher: Publisher, topic: str = "return-events") -> FastAPI:
    app = FastAPI(title="BACKLOT Event Gateway")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/events", status_code=202)
    def ingest(event: ReturnEvent) -> dict:
        """Validate, then publish keyed by return_id (keeps one return in one partition).
        202 = accepted for asynchronous processing, not yet processed."""
        try:
            publisher.publish(topic, event.return_id, event.model_dump_json().encode())
        except Exception as exc:  # noqa: BLE001
            log.error("publish failed: %s", exc)
            raise HTTPException(status_code=503, detail="event bus unavailable, retry later") from exc
        return {"event_id": event.event_id, "return_id": event.return_id, "accepted": True}

    return app


def create_default_app() -> FastAPI:
    """uvicorn services.event_gateway.app:create_default_app --factory"""
    from shared.messaging.kafka_io import KafkaPublisher
    from shared.utilities.config import get_settings
    s = get_settings()
    return create_app(KafkaPublisher(s.kafka_bootstrap_servers), s.topic_events)
