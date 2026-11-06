"""Event contract shared by every service. Bump SCHEMA_VERSION on breaking changes."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.0"


class EventType(str, Enum):
    RETURN_REQUESTED = "RETURN_REQUESTED"
    PICKUP_SCHEDULED = "PICKUP_SCHEDULED"
    PICKUP_COMPLETED = "PICKUP_COMPLETED"
    PARCEL_WEIGHT_RECORDED = "PARCEL_WEIGHT_RECORDED"
    HUB_RECEIVED = "HUB_RECEIVED"
    WAREHOUSE_INSPECTED = "WAREHOUSE_INSPECTED"
    IMAGE_EVIDENCE_UPLOADED = "IMAGE_EVIDENCE_UPLOADED"
    PRODUCT_ID_VERIFIED = "PRODUCT_ID_VERIFIED"
    REFUND_REVIEW_REQUESTED = "REFUND_REVIEW_REQUESTED"
    RECOVERY_DECISION_CREATED = "RECOVERY_DECISION_CREATED"


# Minimum payload keys per event type (checked at the gateway, before Kafka).
REQUIRED_PAYLOAD_KEYS: dict[EventType, set[str]] = {
    EventType.RETURN_REQUESTED: {"reason", "reported_condition"},
    EventType.PARCEL_WEIGHT_RECORDED: {"weight_g", "expected_weight_g"},
    EventType.WAREHOUSE_INSPECTED: {"observed_condition"},
    EventType.IMAGE_EVIDENCE_UPLOADED: {"image_uri", "uploaded_by"},
    EventType.PRODUCT_ID_VERIFIED: {"expected_identifier", "observed_identifier"},
}


class ReturnEvent(BaseModel):
    event_id: str = Field(min_length=1)
    return_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    event_type: EventType
    event_timestamp: datetime
    source_service: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    synthetic: bool = False  # True for every generated record

    @model_validator(mode="after")
    def _check_payload(self) -> "ReturnEvent":
        missing = REQUIRED_PAYLOAD_KEYS.get(self.event_type, set()) - self.payload.keys()
        if missing:
            raise ValueError(f"{self.event_type.value} payload missing keys: {sorted(missing)}")
        if self.schema_version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
            raise ValueError(f"unsupported schema major version {self.schema_version}")
        return self
