"""Evidence reconciliation engine: pure, deterministic, order-independent.

Same events in any arrival order (and any duplication) -> identical result.
Every finding cites the event ids it rests on. Thresholds are module constants, documented
in docs/reconciliation.md.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import timedelta
from itertools import combinations

from shared.schemas.events import ReturnEvent
from shared.schemas.evidence import (DISCLAIMER, Completeness, Confidence, EvidenceGraph, Finding,
                                     FindingType as FT, GraphEdge, GraphNode, ReconciliationResult,
                                     Severity as S, TimelineEntry)

LIFECYCLE = ["RETURN_REQUESTED", "PICKUP_SCHEDULED", "PICKUP_COMPLETED", "PARCEL_WEIGHT_RECORDED",
             "HUB_RECEIVED", "WAREHOUSE_INSPECTED"]
LOGISTICS_CHAIN = ["PICKUP_SCHEDULED", "PICKUP_COMPLETED", "HUB_RECEIVED", "WAREHOUSE_INSPECTED"]
LOGISTICS_REQUIRED = ["PICKUP_SCHEDULED", "PICKUP_COMPLETED", "PARCEL_WEIGHT_RECORDED", "HUB_RECEIVED"]
EXPECTED = ["RETURN_REQUESTED", *LOGISTICS_REQUIRED, "WAREHOUSE_INSPECTED", "PRODUCT_ID_VERIFIED",
            "IMAGE_EVIDENCE:customer", "IMAGE_EVIDENCE:warehouse"]
CRITICAL = {"RETURN_REQUESTED", "PARCEL_WEIGHT_RECORDED", "WAREHOUSE_INSPECTED", "PRODUCT_ID_VERIFIED"}
MISSING_SEVERITY = {"HUB_RECEIVED": S.MEDIUM, "PARCEL_WEIGHT_RECORDED": S.MEDIUM, "WAREHOUSE_INSPECTED": S.MEDIUM,
                    "PRODUCT_ID_VERIFIED": S.MEDIUM, "RETURN_REQUESTED": S.MEDIUM}

WEIGHT_TOLERANCE = 0.05   # <=5% deviation: normal scale/packaging variance, no finding
WEIGHT_MEDIUM = 0.25      # >25% medium, >50% high
WEIGHT_HIGH = 0.50
DELAY_HOURS = 72.0
SEMANTIC_DUP_WINDOW = timedelta(minutes=10)

# Lower = better condition.
CONDITION_RANK = {"unopened": 0, "like_new": 1, "good": 2, "used": 3, "used_with_damage": 4, "damaged": 5}
DAMAGED = {"used_with_damage", "damaged"}


def _fid(return_id: str, ftype: FT, ids: list[str], extra: str = "") -> str:
    h = hashlib.sha1(f"{return_id}|{ftype.value}|{','.join(sorted(ids))}|{extra}".encode()).hexdigest()[:12]
    return f"F-{h}"


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def reconcile(return_id: str, events: list[ReturnEvent],
              duplicate_counts: dict[str, int] | None = None) -> ReconciliationResult:
    if not events:
        raise ValueError("no events for return")
    dup_counts: dict[str, int] = defaultdict(int, duplicate_counts or {})
    unique: dict[str, ReturnEvent] = {}
    for e in events:
        if e.event_id in unique:
            dup_counts[e.event_id] += 1  # same event_id seen again in the input
        else:
            unique[e.event_id] = e
    timeline_events = sorted(unique.values(), key=lambda e: (e.event_timestamp, e.event_id))
    by_type: dict[str, list[ReturnEvent]] = defaultdict(list)
    for e in timeline_events:
        by_type[e.event_type.value].append(e)

    findings: list[Finding] = []

    def add(ftype, sev, conf, summary, support, conflicts=None, details=None, benign=None, extra=""):
        findings.append(Finding(finding_id=_fid(return_id, ftype, support + (conflicts or []), extra),
                                type=ftype, severity=sev, confidence=conf, summary=summary,
                                supporting_event_ids=support, conflicting_event_ids=conflicts or [],
                                details=details or {}, benign_explanations=benign or []))

    anchor = by_type["RETURN_REQUESTED"][0] if by_type["RETURN_REQUESTED"] else timeline_events[0]
    latest = timeline_events[-1]
    absence_refs = list(dict.fromkeys([anchor.event_id, latest.event_id]))
    images = {"customer": [], "warehouse": []}
    for e in by_type["IMAGE_EVIDENCE_UPLOADED"]:
        images.setdefault(str(e.payload.get("uploaded_by")), []).append(e)

    # ---- completeness / missing events -------------------------------------------------
    present, missing = [], []
    for item in EXPECTED:
        if item.startswith("IMAGE_EVIDENCE:"):
            ok = bool(images.get(item.split(":")[1]))
        else:
            ok = bool(by_type.get(item))
        (present if ok else missing).append(item)
    for item in missing:
        add(FT.MISSING_EVENT, MISSING_SEVERITY.get(item, S.LOW), Confidence.HIGH,
            f"Expected evidence not present: {item}", absence_refs, details={"missing": item, "as_of_event": latest.event_id},
            benign=["Event may be delayed and not yet delivered", "Source service may not have emitted it"], extra=item)
    if not missing:
        status = "COMPLETE"
    elif CRITICAL & set(missing):
        status = "INSUFFICIENT"
    else:
        status = "PARTIAL"
    completeness = Completeness(status=status, ratio=round(len(present) / len(EXPECTED), 3),
                                present=present, missing=missing)

    # ---- duplicates --------------------------------------------------------------------
    dup_total = 0
    for eid, n in sorted(dup_counts.items()):
        if eid in unique and n > 0:
            dup_total += n
            add(FT.DUPLICATE_EVENT, S.LOW, Confidence.HIGH,
                f"{unique[eid].event_type.value} event delivered {n + 1} times", [eid],
                details={"kind": "redelivery", "extra_deliveries": n},
                benign=["At-least-once delivery or producer retry"], extra="redelivery")
    seen: dict[tuple, ReturnEvent] = {}
    for e in timeline_events:
        key = (e.event_type.value, json.dumps(e.payload, sort_keys=True), e.source_service)
        prev = seen.get(key)
        if prev and e.event_timestamp - prev.event_timestamp <= SEMANTIC_DUP_WINDOW:
            dup_total += 1
            add(FT.DUPLICATE_EVENT, S.LOW, Confidence.MEDIUM,
                f"{e.event_type.value} appears twice with identical content within 10 minutes",
                [prev.event_id], conflicts=[e.event_id], details={"kind": "semantic"},
                benign=["Source re-emitted the event with a new id"], extra="semantic")
        seen.setdefault(key, e)

    # ---- sequence violations and delays --------------------------------------------------
    first = {t: by_type[t][0] for t in LIFECYCLE if by_type.get(t)}
    present_stages = [t for t in LIFECYCLE if t in first]
    for a, b in zip(present_stages, present_stages[1:]):
        if first[b].event_timestamp < first[a].event_timestamp:
            add(FT.SEQUENCE_VIOLATION, S.LOW, Confidence.LOW,
                f"{b} is timestamped before {a}", [first[a].event_id, first[b].event_id],
                details={"earlier_expected": a, "later_expected": b},
                benign=["Clock skew between services", "Late or backfilled event"], extra=f"{a}>{b}")
    chain = [t for t in LOGISTICS_CHAIN if t in first]
    max_gap: float | None = None
    for a, b in zip(chain, chain[1:]):
        gap = (first[b].event_timestamp - first[a].event_timestamp).total_seconds() / 3600
        if gap < 0:
            continue
        max_gap = gap if max_gap is None else max(max_gap, gap)
        if gap > DELAY_HOURS:
            add(FT.DELAYED_EVENT, S.LOW, Confidence.LOW, f"{gap:.0f}h between {a} and {b}",
                [first[a].event_id, first[b].event_id], details={"gap_hours": round(gap, 1)},
                benign=["Carrier delay", "Weekend or holiday handling"], extra=f"{a}>{b}")

    # ---- product references and identifiers ---------------------------------------------
    pids = {e.product_id for e in timeline_events}
    if len(pids) > 1:
        add(FT.INCONSISTENT_PRODUCT_REFERENCE, S.MEDIUM, Confidence.MEDIUM,
            "Events for this return reference different product_ids",
            [e.event_id for e in timeline_events], details={"product_ids": sorted(pids)},
            benign=["Upstream data-mapping error"])
    id_mismatch = 0
    for e in by_type["PRODUCT_ID_VERIFIED"]:
        exp, obs = e.payload.get("expected_identifier"), e.payload.get("observed_identifier")
        if exp != obs:
            id_mismatch = 1
            add(FT.IDENTIFIER_MISMATCH, S.HIGH, Confidence.MEDIUM,
                "Observed product identifier differs from the one shipped",
                [e.event_id], details={"expected": exp, "observed": obs},
                benign=["Scanner or manual data-entry error", "Identifier format change", "Warehouse mix-up"])

    # ---- weights ---------------------------------------------------------------------------
    weight_dev: float | None = None
    weights = by_type["PARCEL_WEIGHT_RECORDED"]
    if weights:
        w0 = weights[0]
        w, exp_w = _num(w0.payload.get("weight_g")), _num(w0.payload.get("expected_weight_g"))
        if w is not None and exp_w and exp_w > 0:
            weight_dev = round(abs(w - exp_w) / exp_w, 4)
            if weight_dev > WEIGHT_TOLERANCE:
                sev = S.HIGH if weight_dev > WEIGHT_HIGH else S.MEDIUM if weight_dev > WEIGHT_MEDIUM else S.LOW
                conf = Confidence.HIGH if weight_dev > WEIGHT_HIGH else Confidence.MEDIUM if weight_dev > WEIGHT_MEDIUM else Confidence.LOW
                add(FT.WEIGHT_DEVIATION, sev, conf,
                    f"Parcel weight {w:.0f}g vs expected {exp_w:.0f}g ({weight_dev:.0%} {'under' if w < exp_w else 'over'})",
                    [w0.event_id], details={"weight_g": w, "expected_weight_g": exp_w, "deviation": weight_dev},
                    benign=["Accessories or extra packaging included", "Scale calibration", "Packaging variant"])
        for other in weights[1:]:
            w2 = _num(other.payload.get("weight_g"))
            if w is not None and w2 is not None and w > 0 and abs(w2 - w) / w > WEIGHT_TOLERANCE:
                add(FT.CONFLICTING_WEIGHTS, S.MEDIUM, Confidence.MEDIUM,
                    "Two weight records for this parcel disagree", [w0.event_id], conflicts=[other.event_id],
                    details={"weights_g": [w, w2]}, benign=["Re-weighed at a different hub", "Scale error"])

    # ---- condition ----------------------------------------------------------------------------
    cond_delta: int | None = None
    missing_item = 0
    req = by_type["RETURN_REQUESTED"][0] if by_type["RETURN_REQUESTED"] else None
    insp = by_type["WAREHOUSE_INSPECTED"][-1] if by_type["WAREHOUSE_INSPECTED"] else None
    facts = {"product_id": timeline_events[0].product_id,
             "reported_condition": req.payload.get("reported_condition") if req else None,
             "observed_condition": insp.payload.get("observed_condition") if insp else None}
    if req and insp:
        rep, obs = req.payload.get("reported_condition"), insp.payload.get("observed_condition")
        has_wh_img = bool(images.get("warehouse"))
        corroboration = Confidence.MEDIUM if has_wh_img else Confidence.LOW
        if obs == "missing_item":
            missing_item = 1
            add(FT.MISSING_ITEM_REPORTED, S.HIGH, corroboration,
                "Warehouse inspection reports the item is missing from the package",
                [insp.event_id], conflicts=[req.event_id], details={"reported": rep},
                benign=["Item removed or lost in transit", "Inspection error", "Seller packing error"])
        elif rep in CONDITION_RANK and obs in CONDITION_RANK:
            cond_delta = CONDITION_RANK[obs] - CONDITION_RANK[rep]
            if cond_delta >= 2:
                add(FT.CONDITION_CONFLICT, S.HIGH if cond_delta >= 4 else S.MEDIUM, corroboration,
                    f"Customer reported '{rep}' but warehouse observed '{obs}'",
                    [insp.event_id], conflicts=[req.event_id],
                    details={"reported": rep, "observed": obs, "rank_delta": cond_delta},
                    benign=["Damage in transit", "Differing grading standards", "Customer unaware of defect"])
            elif cond_delta <= -2:
                add(FT.CONDITION_CONFLICT, S.LOW, corroboration,
                    f"Customer reported '{rep}' but warehouse observed better condition '{obs}'",
                    [insp.event_id], conflicts=[req.event_id],
                    details={"reported": rep, "observed": obs, "rank_delta": cond_delta},
                    benign=["Customer over-described the problem"])
        else:
            add(FT.UNKNOWN_CONDITION_LABEL, S.INFO, Confidence.HIGH, "Condition label not recognised; cannot compare",
                [insp.event_id, req.event_id], details={"reported": rep, "observed": obs})
        if obs in DAMAGED:
            support = [insp.event_id] + [e.event_id for t in ("PICKUP_COMPLETED", "HUB_RECEIVED") for e in by_type.get(t, [])[:1]]
            disclosed = rep in DAMAGED
            add(FT.DAMAGE_ORIGIN_UNCERTAIN, S.LOW if disclosed else S.MEDIUM, Confidence.LOW,
                "Damage observed at warehouse; pre-pickup vs in-transit origin cannot be determined from events alone",
                support, details={"customer_disclosed_damage": disclosed, "custody_gap": not by_type.get("HUB_RECEIVED")},
                benign=["Damage during carrier handling"])

    metrics = {"weight_deviation": weight_dev,
               "missing_logistics_count": sum(1 for t in LOGISTICS_REQUIRED if not by_type.get(t)),
               "duplicate_event_count": dup_total, "identifier_mismatch": id_mismatch,
               "identifier_verified": int(bool(by_type["PRODUCT_ID_VERIFIED"])), "condition_delta": cond_delta,
               "max_logistics_gap_hours": None if max_gap is None else round(max_gap, 2),
               "missing_evidence_count": len(missing), "missing_item_reported": missing_item,
               "prior_returns_90d": _num(req.payload.get("prior_returns_90d")) if req else None, "completeness_ratio": completeness.ratio}

    findings.sort(key=lambda f: (-f.severity.rank, f.type.value, f.finding_id))
    timeline = [TimelineEntry(event_id=e.event_id, event_type=e.event_type.value, event_timestamp=e.event_timestamp,
                              source_service=e.source_service, duplicate_deliveries=dup_counts.get(e.event_id, 0))
                for e in timeline_events]
    return ReconciliationResult(return_id=return_id, timeline=timeline, completeness=completeness,
                                findings=findings, metrics=metrics, facts=facts, graph=_graph(return_id, timeline_events, findings),
                                disclaimer=DISCLAIMER)


def _graph(return_id: str, events: list[ReturnEvent], findings: list[Finding]) -> EvidenceGraph:
    nodes = [GraphNode(id=return_id, kind="return", label=f"Return {return_id}")]
    edges: list[GraphEdge] = []
    for i, e in enumerate(events):
        nodes.append(GraphNode(id=e.event_id, kind="event", label=e.event_type.value,
                               attrs={"timestamp": e.event_timestamp.isoformat(), "source": e.source_service}))
        edges.append(GraphEdge(source=e.event_id, target=return_id, relation="belongs_to"))
        if i:
            edges.append(GraphEdge(source=events[i - 1].event_id, target=e.event_id, relation="precedes"))
    for f in findings:
        nodes.append(GraphNode(id=f.finding_id, kind="finding", label=f.type.value,
                               attrs={"severity": f.severity.value, "confidence": f.confidence.value}))
        edges.append(GraphEdge(source=f.finding_id, target=return_id, relation="about"))
        edges += [GraphEdge(source=f.finding_id, target=i, relation="supported_by") for i in f.supporting_event_ids]
        pair = [] if f.type.value == "DUPLICATE_EVENT" else dict.fromkeys(f.supporting_event_ids[:1] + f.conflicting_event_ids)
        for a, b in combinations(pair, 2):
            edges.append(GraphEdge(source=a, target=b, relation="conflicts_with"))
    return EvidenceGraph(nodes=nodes, edges=edges)
