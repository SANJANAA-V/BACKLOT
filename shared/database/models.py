from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class EventRecord(Base):
    """Raw event log. event_id is the dedup key at the storage layer."""
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    return_id: Mapped[str] = mapped_column(String, index=True)
    order_id: Mapped[str] = mapped_column(String)
    product_id: Mapped[str] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_service: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    schema_version: Mapped[str] = mapped_column(String)
    synthetic: Mapped[bool] = mapped_column(default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    duplicate_deliveries: Mapped[int] = mapped_column(Integer, default=0)


class ProcessedEvent(Base):
    """Per-consumer-group idempotency ledger and processing status."""
    __tablename__ = "processed_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    consumer_group: Mapped[str] = mapped_column(String)
    event_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="DONE")  # DONE | FAILED | DEAD_LETTER
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    __table_args__ = (
        UniqueConstraint("consumer_group", "event_id", name="uq_group_event"),
        Index("ix_processed_group", "consumer_group"),
    )


class AuditEntry(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    return_id: Mapped[str] = mapped_column(String, index=True)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReconciliationSnapshot(Base):
    """Latest reconciliation result per return, written by the evidence worker."""
    __tablename__ = "reconciliation_snapshots"
    return_id: Mapped[str] = mapped_column(String, primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
    event_count: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Investigation(Base):
    """Every investigation run is kept (append-only) so decisions stay traceable."""
    __tablename__ = "investigations"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    return_id: Mapped[str] = mapped_column(String, index=True)
    run_id: Mapped[str] = mapped_column(String)
    bundle: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
