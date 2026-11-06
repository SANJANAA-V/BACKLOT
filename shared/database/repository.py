from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datetime import timezone

from shared.database.models import AuditEntry, EventRecord, Investigation, ProcessedEvent, ReconciliationSnapshot
from shared.schemas.events import ReturnEvent


def store_event(session: Session, ev: ReturnEvent) -> bool:
    """Insert event. Returns True if new, False if a redelivery (counted, not re-stored)."""
    existing = session.get(EventRecord, ev.event_id)
    if existing is not None:
        existing.duplicate_deliveries += 1
        session.commit()
        return False
    session.add(EventRecord(**ev.model_dump(mode="python", exclude={"event_type"}),
                            event_type=ev.event_type.value))
    try:
        session.commit()
    except IntegrityError:  # concurrent insert of same event_id
        session.rollback()
        return False
    return True


def claim_event(session: Session, group: str, event_id: str) -> bool:
    """Idempotency gate for a consumer group: True only for the first claim."""
    session.add(ProcessedEvent(consumer_group=group, event_id=event_id, status="DONE"))
    try:
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


def events_for_return(session: Session, return_id: str) -> list[EventRecord]:
    q = select(EventRecord).where(EventRecord.return_id == return_id).order_by(EventRecord.event_timestamp)
    return list(session.scalars(q))


def get_ledger(session: Session, group: str, event_id: str) -> ProcessedEvent | None:
    q = select(ProcessedEvent).where(ProcessedEvent.consumer_group == group,
                                     ProcessedEvent.event_id == event_id)
    return session.scalars(q).first()


def set_ledger(session: Session, group: str, event_id: str, status: str, attempts: int) -> None:
    for _ in range(2):
        row = get_ledger(session, group, event_id)
        if row:
            row.status, row.attempts = status, attempts
        else:
            session.add(ProcessedEvent(consumer_group=group, event_id=event_id,
                                       status=status, attempts=attempts))
        try:
            session.commit()
            return
        except IntegrityError:  # concurrent insert; retry as update
            session.rollback()


def bump_duplicate(session: Session, event_id: str) -> None:
    row = session.get(EventRecord, event_id)
    if row is not None:
        row.duplicate_deliveries += 1
        session.commit()


def load_return_events(session: Session, return_id: str) -> tuple[list[ReturnEvent], dict[str, int]]:
    """Stored events as schema objects (tz-normalised: SQLite drops tzinfo) + redelivery counts."""
    rows = events_for_return(session, return_id)
    events = []
    for r in rows:
        ts = r.event_timestamp if r.event_timestamp.tzinfo else r.event_timestamp.replace(tzinfo=timezone.utc)
        events.append(ReturnEvent(event_id=r.event_id, return_id=r.return_id, order_id=r.order_id,
                                  product_id=r.product_id, event_type=r.event_type, event_timestamp=ts,
                                  source_service=r.source_service, payload=r.payload,
                                  schema_version=r.schema_version, synthetic=r.synthetic))
    return events, {r.event_id: r.duplicate_deliveries for r in rows if r.duplicate_deliveries}


def upsert_snapshot(session: Session, return_id: str, result: dict, event_count: int) -> None:
    row = session.get(ReconciliationSnapshot, return_id)
    if row:
        row.result, row.event_count = result, event_count
    else:
        session.add(ReconciliationSnapshot(return_id=return_id, result=result, event_count=event_count))
    session.commit()


def get_snapshot(session: Session, return_id: str) -> ReconciliationSnapshot | None:
    return session.get(ReconciliationSnapshot, return_id)


def add_audit(session: Session, return_id: str, actor: str, action: str, detail: dict) -> None:
    session.add(AuditEntry(return_id=return_id, actor=actor, action=action, detail=detail))
    session.commit()


def list_audit(session: Session, return_id: str) -> list[AuditEntry]:
    return list(session.scalars(select(AuditEntry).where(AuditEntry.return_id == return_id).order_by(AuditEntry.id)))


def save_investigation(session: Session, return_id: str, run_id: str, bundle: dict) -> None:
    session.add(Investigation(return_id=return_id, run_id=run_id, bundle=bundle))
    session.commit()


def latest_investigation(session: Session, return_id: str) -> Investigation | None:
    q = select(Investigation).where(Investigation.return_id == return_id).order_by(Investigation.id.desc())
    return session.scalars(q).first()


def list_return_ids(session: Session, limit: int = 200) -> list[str]:
    return list(session.scalars(select(EventRecord.return_id).distinct().order_by(EventRecord.return_id).limit(limit)))


def review_status(session: Session, return_id: str, required: bool) -> str:
    if not required:
        return "NOT_REQUIRED"
    reviews = [a for a in list_audit(session, return_id) if a.action == "HUMAN_REVIEW"]
    return reviews[-1].detail["outcome"] if reviews else "PENDING"
