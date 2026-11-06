from sqlalchemy.orm import Session

from services.evidence_worker.reconciler import reconcile
from shared.database.repository import bump_duplicate, load_return_events, store_event, upsert_snapshot
from shared.schemas.events import ReturnEvent


def refresh_snapshot(session: Session, return_id: str) -> None:
    """Recompute from ALL stored events for the return, so arrival order never matters."""
    events, dups = load_return_events(session, return_id)
    if events:
        upsert_snapshot(session, return_id, reconcile(return_id, events, dups).model_dump(mode="json"), len(events))


def persist_and_reconcile(session: Session, event: ReturnEvent) -> None:
    """Idempotent: storage is keyed by event_id and reconciliation is a pure recompute."""
    store_event(session, event)
    refresh_snapshot(session, event.return_id)


def record_duplicate(session: Session, event: ReturnEvent) -> None:
    bump_duplicate(session, event.event_id)
    refresh_snapshot(session, event.return_id)
