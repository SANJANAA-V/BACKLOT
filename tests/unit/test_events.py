import pytest
from pydantic import ValidationError

from shared.schemas.events import ReturnEvent

BASE = dict(event_id="e1", return_id="R1", order_id="O1", product_id="P1",
            event_timestamp="2026-01-01T10:00:00Z", source_service="logistics")


def test_valid_event():
    e = ReturnEvent(**BASE, event_type="HUB_RECEIVED", payload={})
    assert e.schema_version == "1.0"


def test_unknown_event_type_rejected():
    with pytest.raises(ValidationError):
        ReturnEvent(**BASE, event_type="NOPE")


def test_missing_payload_key_rejected():
    with pytest.raises(ValidationError):
        ReturnEvent(**BASE, event_type="PARCEL_WEIGHT_RECORDED", payload={"weight_g": 500})


def test_bad_schema_major_rejected():
    with pytest.raises(ValidationError):
        ReturnEvent(**BASE, event_type="HUB_RECEIVED", schema_version="2.0")


def test_missing_required_field_rejected():
    b = {k: v for k, v in BASE.items() if k != "return_id"}
    with pytest.raises(ValidationError):
        ReturnEvent(**b, event_type="HUB_RECEIVED")
