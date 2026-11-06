import random
from datetime import datetime, timedelta, timezone

import pytest

from scripts.generate_data import generate
from services.evidence_worker.reconciler import reconcile
from shared.schemas.events import ReturnEvent
from shared.schemas.evidence import Confidence, FindingType as FT, Severity as S

EVENTS, METAS = generate(42, 5)
SCEN = {}
for m in METAS:
    SCEN.setdefault(m["scenario"], []).append(m["return_id"])


def run(rid, events=None):
    evs = [e for e in (events or EVENTS) if e.return_id == rid]
    return reconcile(rid, evs)


def types(res):
    return {f.type for f in res.findings}


def get(res, t):
    return [f for f in res.findings if f.type == t]


def each(scenario):
    return [run(r) for r in SCEN[scenario]]


def test_normal_returns_have_no_findings_and_are_complete():
    for r in each("normal"):
        assert r.findings == [] and r.completeness.status == "COMPLETE"


def test_wrong_product_flags_identifier_mismatch_with_limited_confidence():
    for r in each("wrong_product"):
        (f,) = get(r, FT.IDENTIFIER_MISMATCH)
        assert f.severity == S.HIGH and f.confidence == Confidence.MEDIUM and f.benign_explanations


def test_empty_package_flags_missing_item_and_weight():
    for r in each("empty_package"):
        assert FT.MISSING_ITEM_REPORTED in types(r)
        assert get(r, FT.WEIGHT_DEVIATION)[0].severity.rank >= S.MEDIUM.rank


def test_visible_damage_is_uncertain_origin_not_conflict():
    for r in each("visible_damage"):
        assert FT.CONDITION_CONFLICT not in types(r)
        assert get(r, FT.DAMAGE_ORIGIN_UNCERTAIN)[0].severity == S.LOW


def test_missing_scan():
    for r in each("missing_logistics_scan"):
        assert r.completeness.status == "PARTIAL" and "HUB_RECEIVED" in r.completeness.missing
        assert r.metrics["missing_logistics_count"] == 1


def test_duplicate_event_detected_from_repeated_delivery():
    for r in each("duplicate_logistics_event"):
        f = get(r, FT.DUPLICATE_EVENT)[0]
        assert f.severity == S.LOW and f.details["kind"] == "redelivery"
        assert r.metrics["duplicate_event_count"] == 1


def test_conflicting_condition():
    for r in each("conflicting_condition"):
        f = get(r, FT.CONDITION_CONFLICT)[0]
        assert f.severity == S.HIGH and len(f.conflicting_event_ids) == 1
        assert any(e.relation == "conflicts_with" for e in r.graph.edges)


def test_legitimate_unusual_return_has_only_low_severity_findings():
    for r in each("legitimate_unusual"):
        assert all(f.severity.rank <= S.LOW.rank for f in r.findings)
        assert FT.WEIGHT_DEVIATION in types(r)


def test_weight_discrepancy():
    for r in each("weight_discrepancy"):
        assert get(r, FT.WEIGHT_DEVIATION)[0].severity.rank >= S.MEDIUM.rank


def test_incomplete_evidence_is_insufficient():
    for r in each("incomplete_evidence"):
        assert r.completeness.status == "INSUFFICIENT"
        assert {"WAREHOUSE_INSPECTED", "PRODUCT_ID_VERIFIED", "IMAGE_EVIDENCE:warehouse"} <= set(r.completeness.missing)


def test_every_finding_cites_real_events_and_graph_is_consistent():
    for m in METAS:
        r = run(m["return_id"])
        ids = {t.event_id for t in r.timeline}
        node_ids = {n.id for n in r.graph.nodes}
        for f in r.findings:
            assert f.supporting_event_ids and set(f.supporting_event_ids + f.conflicting_event_ids) <= ids
            assert f.finding_id in node_ids
        assert all(e.source in node_ids and e.target in node_ids for e in r.graph.edges)


def test_result_independent_of_arrival_order():
    for m in METAS:
        evs = [e for e in EVENTS if e.return_id == m["return_id"]]
        shuffled = evs[:]; random.Random(1).shuffle(shuffled)
        assert reconcile(m["return_id"], evs).model_dump() == reconcile(m["return_id"], shuffled).model_dump()


# ---- hand-built edge cases -------------------------------------------------------------
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ev(etype, minutes, payload=None, eid=None, pid="P1"):
    return ReturnEvent(event_id=eid or f"{etype}-{minutes}", return_id="R", order_id="O", product_id=pid,
                       event_type=etype, event_timestamp=T0 + timedelta(minutes=minutes),
                       source_service="t", payload=payload or {})


REQ = ev("RETURN_REQUESTED", 0, {"reason": "x", "reported_condition": "like_new"})


def test_no_events_raises():
    with pytest.raises(ValueError):
        reconcile("R", [])


def test_sequence_violation():
    r = reconcile("R", [REQ, ev("PICKUP_SCHEDULED", 100), ev("PICKUP_COMPLETED", 50)])
    assert FT.SEQUENCE_VIOLATION in types(r)


def test_delayed_event():
    r = reconcile("R", [REQ, ev("PICKUP_SCHEDULED", 10), ev("PICKUP_COMPLETED", 10 + 80 * 60)])
    assert get(r, FT.DELAYED_EVENT)[0].details["gap_hours"] == 80.0
    assert r.metrics["max_logistics_gap_hours"] == 80.0


def test_inconsistent_product_reference():
    r = reconcile("R", [REQ, ev("PICKUP_SCHEDULED", 10, pid="P2")])
    assert FT.INCONSISTENT_PRODUCT_REFERENCE in types(r)


def test_conflicting_weights():
    w = lambda m, g: ev("PARCEL_WEIGHT_RECORDED", m, {"weight_g": g, "expected_weight_g": 500})
    r = reconcile("R", [REQ, w(10, 500), w(20, 700)])
    assert FT.CONFLICTING_WEIGHTS in types(r)


def test_semantic_duplicate_with_new_event_id():
    a, b = (ev("HUB_RECEIVED", m, {"hub": "H1"}, eid=f"e{m}") for m in (10, 12))
    f = get(reconcile("R", [REQ, a, b]), FT.DUPLICATE_EVENT)[0]
    assert f.details["kind"] == "semantic"


def test_unknown_condition_label_not_treated_as_conflict():
    r = reconcile("R", [REQ, ev("WAREHOUSE_INSPECTED", 100, {"observed_condition": "weird"})])
    assert FT.UNKNOWN_CONDITION_LABEL in types(r) and FT.CONDITION_CONFLICT not in types(r)


def test_condition_confidence_drops_without_warehouse_image():
    r = reconcile("R", [ev("RETURN_REQUESTED", 0, {"reason": "x", "reported_condition": "unopened"}),
                        ev("WAREHOUSE_INSPECTED", 100, {"observed_condition": "damaged"})])
    assert get(r, FT.CONDITION_CONFLICT)[0].confidence == Confidence.LOW


def test_non_numeric_weight_does_not_crash():
    r = reconcile("R", [REQ, ev("PARCEL_WEIGHT_RECORDED", 10, {"weight_g": "heavy", "expected_weight_g": 500})])
    assert r.metrics["weight_deviation"] is None
