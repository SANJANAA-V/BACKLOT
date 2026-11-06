"""Deterministic SYNTHETIC return generator. All output is fake and flagged synthetic=True.

Ground-truth labels (is_anomalous) are assigned by scenario design, not by any real
fraud outcome. Do not read model scores trained on this as real-world performance.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared.schemas.events import ReturnEvent

NS = uuid.UUID("12345678-1234-5678-1234-567812345678")
BASE_TIME = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)

# product_id -> (name, expected_weight_g incl. packaging, price)
CATALOG = {
    "P-PHONE-01": ("Smartphone", 520, 24000),
    "P-LAPTOP-01": ("Laptop", 2400, 55000),
    "P-HEADPH-01": ("Headphones", 450, 6000),
    "P-WATCH-01": ("Smartwatch", 300, 9000),
    "P-SHOES-01": ("Running shoes", 900, 3500),
}

CATEGORY = {"P-PHONE-01": "smartphone", "P-LAPTOP-01": "laptop", "P-HEADPH-01": "headphones",
            "P-WATCH-01": "smartwatch", "P-SHOES-01": "footwear"}

# scenario -> is_anomalous (by construction)
SCENARIOS = {
    "normal": 0,
    "wrong_product": 1,
    "empty_package": 1,
    "visible_damage": 0,
    "missing_logistics_scan": 0,
    "duplicate_logistics_event": 0,
    "conflicting_condition": 1,
    "legitimate_unusual": 0,
    "weight_discrepancy": 1,
    "incomplete_evidence": 0,
}


def _eid(rid: str, etype: str, n: int = 0) -> str:
    return str(uuid.uuid5(NS, f"{rid}:{etype}:{n}"))


def build_return(idx: int, scenario: str, rng: random.Random) -> tuple[list[ReturnEvent], dict]:
    rid, oid = f"R-{idx:05d}", f"O-{idx:05d}"
    pid = rng.choice(list(CATALOG))
    _, exp_w, price = CATALOG[pid]
    prior = rng.choice([0, 0, 1, 1, 2, 5])  # same distribution for every scenario (no label signal)
    t = BASE_TIME + timedelta(hours=idx)
    state = dict(weight=int(exp_w * rng.uniform(0.97, 1.03)), reported="like_new",
                 observed="like_new", observed_id=f"SN-{idx:06d}", expected_id=f"SN-{idx:06d}",
                 reason="changed_mind", customer_img=True, wh_img=True)

    if scenario == "wrong_product":
        state["observed_id"] = f"SN-X{rng.randint(100000, 999999)}"
        state["weight"] = int(exp_w * rng.uniform(0.6, 1.5))
    elif scenario == "empty_package":
        state["weight"] = rng.randint(80, 200)
        state["observed"] = "missing_item"
    elif scenario == "visible_damage":
        state["observed"] = "damaged"; state["reported"] = "damaged"; state["reason"] = "arrived_damaged"
    elif scenario == "conflicting_condition":
        state["reported"] = "unopened"; state["observed"] = "used_with_damage"
    elif scenario == "legitimate_unusual":
        state["weight"] = int(exp_w * rng.uniform(1.12, 1.2))  # customer included extra accessory
        state["reason"] = "exchange_with_accessories"
    elif scenario == "weight_discrepancy":
        state["weight"] = int(exp_w * rng.choice([rng.uniform(0.45, 0.7), rng.uniform(1.4, 1.9)]))
    elif scenario == "incomplete_evidence":
        state["wh_img"] = False

    def ev(etype, minutes, source, payload, n=0):
        return ReturnEvent(event_id=_eid(rid, etype, n), return_id=rid, order_id=oid, product_id=pid,
                           event_type=etype, event_timestamp=t + timedelta(minutes=minutes),
                           source_service=source, payload=payload, synthetic=True)

    events = [
        ev("RETURN_REQUESTED", 0, "customer_portal", {"reason": state["reason"], "reported_condition": state["reported"],
                                                                     "prior_returns_90d": prior}),
        ev("PICKUP_SCHEDULED", 30, "logistics", {"slot": "next_day"}),
        ev("PICKUP_COMPLETED", 24 * 60 + rng.randint(0, 240), "logistics", {"courier": "C-" + str(rng.randint(1, 9))}),
        ev("PARCEL_WEIGHT_RECORDED", 24 * 60 + 260, "logistics", {"weight_g": state["weight"], "expected_weight_g": exp_w}),
    ]
    if scenario != "missing_logistics_scan":
        events.append(ev("HUB_RECEIVED", 48 * 60 + rng.randint(0, 300), "logistics", {"hub": "H-" + str(rng.randint(1, 5))}))
    if scenario == "duplicate_logistics_event":  # same event_id re-delivered
        events.append(events[2].model_copy())
    if state["customer_img"]:
        events.append(ev("IMAGE_EVIDENCE_UPLOADED", 5, "customer_portal",
                         {"image_uri": f"synthetic://{rid}/customer.jpg", "uploaded_by": "customer"}, 0))
    if scenario != "incomplete_evidence":
        events.append(ev("WAREHOUSE_INSPECTED", 72 * 60, "warehouse", {"observed_condition": state["observed"]}))
        events.append(ev("PRODUCT_ID_VERIFIED", 72 * 60 + 5, "warehouse",
                         {"expected_identifier": state["expected_id"], "observed_identifier": state["observed_id"]}))
        events.append(ev("IMAGE_EVIDENCE_UPLOADED", 72 * 60 + 10, "warehouse",
                         {"image_uri": f"synthetic://{rid}/warehouse.jpg", "uploaded_by": "warehouse"}, 1))
    # Simulate out-of-order arrival for ~20% of returns
    if rng.random() < 0.2 and len(events) > 3:
        i = rng.randrange(len(events) - 1)
        events[i], events[i + 1] = events[i + 1], events[i]

    # SIMULATED image annotations (stand-in for vision output; no real images exist in the synthetic data)
    cat = CATEGORY[pid]
    other = rng.choice([c for c in CATEGORY.values() if c != cat])
    ann = {f"synthetic://{rid}/customer.jpg": {
        "visible_damage": "yes" if state["reported"] == "damaged" else "no", "packaging": "intact", "category": cat,
        "description": "damage visible" if state["reported"] == "damaged" else "no damage visible"}}
    if scenario != "incomplete_evidence":
        wh_cat = "none" if scenario == "empty_package" else other if scenario == "wrong_product" else cat
        wh_dmg = state["observed"] in ("damaged", "used_with_damage")
        ann[f"synthetic://{rid}/warehouse.jpg"] = {
            "visible_damage": "yes" if wh_dmg else "no", "packaging": "damaged" if scenario == "visible_damage" else "intact",
            "category": wh_cat, "description": "damage visible" if wh_dmg else "no damage visible"}
    meta = {"return_id": rid, "scenario": scenario, "is_anomalous": SCENARIOS[scenario],
            "product_id": pid, "price": price, "synthetic": True,
            "prior_returns_90d": prior, "image_annotations": ann}
    return events, meta


# Realistic-ish scenario mix for ML training (mostly normal returns). Arbitrary demo values.
DEFAULT_MIX = {"normal": 0.65, "wrong_product": 0.03, "empty_package": 0.02, "visible_damage": 0.06,
               "missing_logistics_scan": 0.05, "duplicate_logistics_event": 0.05, "conflicting_condition": 0.03,
               "legitimate_unusual": 0.03, "weight_discrepancy": 0.03, "incomplete_evidence": 0.05}


def iter_mixed(seed: int, n: int, mix: dict[str, float] | None = None) -> list[tuple[list[ReturnEvent], dict]]:
    """n returns with scenario proportions from `mix`; every listed scenario appears at least once."""
    mix = mix or DEFAULT_MIX
    rng = random.Random(seed)
    counts = {k: max(1, round(n * w)) for k, w in mix.items() if k != "normal"}
    counts["normal"] = max(1, n - sum(counts.values()))
    plan = [s for s, c in counts.items() for _ in range(c)]
    rng.shuffle(plan)
    return [build_return(i + 1, sc, rng) for i, sc in enumerate(plan)]


def generate(seed: int, per_scenario: int) -> tuple[list[ReturnEvent], list[dict]]:
    rng = random.Random(seed)
    all_events: list[ReturnEvent] = []
    metas: list[dict] = []
    idx = 0
    for scenario in SCENARIOS:
        for _ in range(per_scenario):
            idx += 1
            evs, meta = build_return(idx, scenario, rng)
            all_events += evs
            metas.append(meta)
    return all_events, metas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--per-scenario", type=int, default=20)
    ap.add_argument("--out", default="data/samples")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    events, metas = generate(a.seed, a.per_scenario)
    with open(out / "events.jsonl", "w") as f:
        for e in events:
            f.write(e.model_dump_json() + "\n")
    with open(out / "returns.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in metas[0] if k != "image_annotations"], extrasaction="ignore")
        w.writeheader(); w.writerows(metas)
    (out / "image_annotations.json").write_text(json.dumps({k: v for m in metas for k, v in m["image_annotations"].items()}, indent=1))
    print(f"[SYNTHETIC] wrote {len(events)} events for {len(metas)} returns to {out}")


if __name__ == "__main__":
    main()
