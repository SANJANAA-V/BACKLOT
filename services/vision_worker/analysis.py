from __future__ import annotations

from shared.schemas.events import ReturnEvent
from shared.schemas.vision import ImageDifference, ImageFinding, ImageObservation, VisionReport
from shared.utilities.cache import Cache
from services.vision_worker.adapters import VisionAdapter, VisionError, VisionUnavailable

LEVEL = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _finding(ev: ReturnEvent, adapter: VisionAdapter, cache: Cache | None) -> ImageFinding:
    uri, by = ev.payload["image_uri"], str(ev.payload.get("uploaded_by"))
    base = dict(event_id=ev.event_id, image_uri=uri, uploaded_by=by, adapter=adapter.name, provenance=adapter.provenance)
    key = f"vision:{adapter.name}:{uri}"
    if cache and (hit := cache.get(key)):
        return ImageFinding(**base, status="OK", **ImageObservation.model_validate_json(hit).model_dump())
    try:
        obs = adapter.analyze(uri, by)
    except VisionUnavailable as exc:
        return ImageFinding(**base, status="UNAVAILABLE", error=str(exc))
    except VisionError as exc:
        return ImageFinding(**base, status="ERROR", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - one bad image must not break the investigation
        return ImageFinding(**base, status="ERROR", error=f"unexpected {type(exc).__name__}")
    if cache:
        cache.set(key, obs.model_dump_json())
    return ImageFinding(**base, status="OK", **obs.model_dump())


def _conf(a: ImageFinding, b: ImageFinding) -> str:
    return ["LOW", "MEDIUM", "HIGH"][min(LEVEL[a.model_confidence], LEVEL[b.model_confidence])]


def analyze_return(events: list[ReturnEvent], adapter: VisionAdapter, cache: Cache | None = None,
                   expected_category: str | None = None) -> VisionReport:
    imgs = sorted({e.event_id: e for e in events if e.event_type.value == "IMAGE_EVIDENCE_UPLOADED"}.values(),
                  key=lambda e: (e.event_timestamp, e.event_id))
    findings = [_finding(e, adapter, cache) for e in imgs]
    ok = [f for f in findings if f.status == "OK"]
    cust = next((f for f in reversed(ok) if f.uploaded_by == "customer"), None)
    wh = next((f for f in reversed(ok) if f.uploaded_by == "warehouse"), None)
    diffs: list[ImageDifference] = []
    if cust and wh:
        ids = [cust.event_id, wh.event_id]
        c = _conf(cust, wh)
        if {cust.visible_damage, wh.visible_damage} == {"yes", "no"}:
            side = "warehouse" if wh.visible_damage == "yes" else "customer"
            other = "customer" if side == "warehouse" else "warehouse"
            diffs.append(ImageDifference(kind="DAMAGE_VISIBILITY", confidence=c, event_ids=ids,
                                         detail=f"Damage is visible in the {side} image but not in the {other} image"))
        if {cust.packaging_condition, wh.packaging_condition} == {"intact", "damaged"}:
            diffs.append(ImageDifference(kind="PACKAGING_DIFFERENCE", confidence=c, event_ids=ids,
                                         detail=f"Packaging looks {cust.packaging_condition} (customer) vs {wh.packaging_condition} (warehouse)"))
        cc, wc = cust.product_category_seen, wh.product_category_seen
        if cc and wc and cc != wc:
            diffs.append(ImageDifference(kind="CATEGORY_MISMATCH", confidence=c, event_ids=ids,
                                         detail=f"Customer image shows '{cc}', warehouse image shows '{wc}'"))
    if wh and expected_category and wh.product_category_seen and wh.product_category_seen != expected_category:
        diffs.append(ImageDifference(kind="EXPECTED_CATEGORY_MISMATCH", confidence=wh.model_confidence, event_ids=[wh.event_id],
                                     detail=f"Warehouse image shows '{wh.product_category_seen}' but the order was for '{expected_category}'"))
    return VisionReport(adapter=adapter.name, findings=findings, differences=diffs)
