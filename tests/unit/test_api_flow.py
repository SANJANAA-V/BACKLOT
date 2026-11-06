import json

import pytest
from fastapi.testclient import TestClient

from frontend.demo import build_demo
from shared.schemas.investigation import InvestigationBundle


@pytest.fixture(scope="module")
def env():
    app, labels = build_demo()
    return TestClient(app), {v: k for k, v in labels.items()}


def run(env, scenario):
    c, ids = env
    r = c.post(f"/returns/{ids[scenario]}/investigate")
    assert r.status_code == 200
    return InvestigationBundle.model_validate(r.json())  # full schema validation of the API output


def test_list_returns(env):
    assert len(env[0].get("/returns").json()["return_ids"]) == 10




@pytest.mark.parametrize("scenario,expect", [
    ("wrong_product", {"HUMAN_REVIEW"}), ("empty_package", {"HUMAN_REVIEW"}),
    ("conflicting_condition", {"HUMAN_REVIEW"}), ("weight_discrepancy", {"HUMAN_REVIEW"}),
    ("incomplete_evidence", {"HUMAN_REVIEW"}), ("missing_logistics_scan", {"HUMAN_REVIEW"}),
    ("normal", {"RESALE", "REFURBISH"}), ("legitimate_unusual", {"RESALE", "REFURBISH"}),
    ("duplicate_logistics_event", {"RESALE", "REFURBISH"}), ("visible_damage", {"REPAIR", "LIQUIDATE", "RECYCLE"})])
def test_each_sample_scenario_gets_a_sensible_recommendation(env, scenario, expect):
    b = run(env, scenario)
    assert b.decision.action.value in expect, (scenario, b.decision.reasons)
    assert b.report.recommended_action == b.decision.action
    assert b.report_meta.generated_by == "deterministic_fallback"  # no credentials in tests
    assert b.decision.human_review_required == (b.decision.action.value == "HUMAN_REVIEW")
    assert b.reconciliation.disclaimer and b.vision.notice


def test_bundle_is_fully_grounded(env):
    b = run(env, "conflicting_condition")
    ev = {t.event_id for t in b.reconciliation.timeline}
    assert set(b.report.supporting_event_ids) <= ev
    assert {p.chunk_id for p in b.policy_references} >= set(b.report.relevant_policy_references)
    assert b.risk and "SYNTHETIC" in b.risk["disclaimer"]


def test_audit_trail_records_each_step_in_order_with_inputs(env):
    c, ids = env
    rid = ids["wrong_product"]
    before = len(c.get(f"/returns/{rid}/audit").json()["entries"])
    b = run(env, "wrong_product")
    entries = c.get(f"/returns/{rid}/audit").json()["entries"][before:]
    assert [e["action"] for e in entries] == ["RECONCILED", "RISK_SCORED", "VISION_ANALYZED", "DECISION_MADE", "REPORT_GENERATED"]
    assert all(e["detail"]["run_id"] == b.run_id for e in entries)
    dm = next(e for e in entries if e["action"] == "DECISION_MADE")["detail"]
    assert dm["action"] == "HUMAN_REVIEW" and dm["rules_fired"] and "inputs" in dm


def test_review_workflow_changes_status_and_is_audited(env):
    c, ids = env
    rid = ids["empty_package"]
    assert c.post(f"/returns/{rid}/review", json={"reviewer": "a", "outcome": "CONFIRMED"}).status_code in (200, 409)
    run(env, "empty_package")
    assert c.get(f"/returns/{rid}/investigation").json()["review_status"] in ("PENDING", "CONFIRMED")
    assert c.post(f"/returns/{rid}/review", json={"reviewer": "", "outcome": "CONFIRMED"}).status_code == 422
    assert c.post(f"/returns/{rid}/review", json={"reviewer": "r", "outcome": "OVERRIDDEN"}).status_code == 422
    ok = c.post(f"/returns/{rid}/review", json={"reviewer": "priya", "outcome": "OVERRIDDEN", "final_action": "REFURBISH", "note": "n"})
    assert ok.json()["review_status"] == "OVERRIDDEN"
    last = c.get(f"/returns/{rid}/audit").json()["entries"][-1]
    assert last["actor"] == "human:priya" and last["detail"]["final_action"] == "REFURBISH"
    assert c.get(f"/returns/{rid}/investigation").json()["review_status"] == "OVERRIDDEN"


def test_review_not_required_status_and_errors(env):
    c, ids = env
    assert run(env, "normal").review_status == "NOT_REQUIRED"
    assert c.post("/returns/NOPE/investigate").status_code == 404
    assert c.get("/returns/NOPE/investigation").status_code == 404
    assert c.post("/returns/NOPE/review", json={"reviewer": "x", "outcome": "CONFIRMED"}).status_code == 409


def test_rerun_appends_history_instead_of_overwriting(env):
    c, ids = env
    rid = ids["visible_damage"]
    run(env, "visible_damage"); n1 = len(c.get(f"/returns/{rid}/audit").json()["entries"])
    run(env, "visible_damage"); n2 = len(c.get(f"/returns/{rid}/audit").json()["entries"])
    assert n2 == n1 + 5
