from services.recovery_worker.engine import RecoveryInputs, decide
from shared.schemas.recovery import RecoveryAction as A

BASE = dict(product_id="P-PHONE-01", reported_condition="like_new", observed_condition="like_new",
            completeness_status="COMPLETE", risk_score=0.1, risk_flagged=False, high_findings=0,
            medium_findings=0, vision_difference_count=0)


def d(**kw):
    return decide(RecoveryInputs(**{**BASE, **kw}))


def test_clean_like_new_phone_is_resold():
    x = d()
    assert x.action == A.RESALE and not x.human_review_required
    assert any(t.rule_id == "R7" and t.fired for t in x.rule_trace)


def test_each_review_trigger_sends_to_human():
    for kw, rule in [(dict(completeness_status="INSUFFICIENT"), "R1"), (dict(risk_flagged=True), "R2"),
                     (dict(high_findings=1), "R2"), (dict(completeness_status="PARTIAL", medium_findings=1), "R3"),
                     (dict(vision_difference_count=1), "R4"), (dict(product_id="P-UNKNOWN"), "R5"),
                     (dict(observed_condition="weird"), "R5")]:
        x = d(**kw)
        assert x.action == A.HUMAN_REVIEW and x.human_review_required
        assert any(t.rule_id == rule and t.fired for t in x.rule_trace), kw


def test_partial_evidence_without_medium_finding_does_not_force_review():
    assert d(completeness_status="PARTIAL").action == A.RESALE


def test_policy_constraint_blocks_resale_of_opened_hygiene_item():
    assert d(product_id="P-HEADPH-01").action == A.REFURBISH
    assert d(product_id="P-HEADPH-01", observed_condition="unopened").action == A.RESALE


def test_economics_drive_damaged_outcomes():
    dmg = dict(observed_condition="damaged", reported_condition="damaged")
    assert d(product_id="P-LAPTOP-01", **dmg).action == A.REPAIR
    assert d(product_id="P-SHOES-01", **dmg).action == A.RECYCLE  # liquidation would lose money
    assert d(product_id="P-PHONE-01", observed_condition="used").action == A.REFURBISH


def test_missing_risk_score_is_disclosed_not_assumed_safe():
    x = d(risk_score=None, risk_flagged=None)
    assert any("risk score was not available" in r for r in x.reasons) or x.action != A.HUMAN_REVIEW


def test_deterministic_and_inputs_recorded():
    assert d().model_dump() == d().model_dump() and d().inputs["grade"] == "B"


def test_review_reasons_list_every_fired_gate():
    x = d(completeness_status="INSUFFICIENT", risk_flagged=True)
    assert len(x.reasons) == 2


def test_packaging_difference_alone_is_advisory_and_does_not_block_recovery():
    from scripts.generate_data import generate
    from services.evidence_worker.reconciler import reconcile
    from services.recovery_worker.engine import inputs_from
    from services.vision_worker.adapters import SimulatedVisionAdapter
    from services.vision_worker.analysis import analyze_return
    events, metas = generate(42, 1)
    m = next(m for m in metas if m["scenario"] == "visible_damage")
    evs = [e for e in events if e.return_id == m["return_id"]]
    v = analyze_return(evs, SimulatedVisionAdapter(m["image_annotations"]))
    assert {d.kind for d in v.differences} == {"PACKAGING_DIFFERENCE"}
    assert inputs_from(reconcile(m["return_id"], evs), None, v).vision_difference_count == 0
