import numpy as np
import pytest
from fastapi.testclient import TestClient

from scripts.generate_data import generate
from services.api.app import create_app
from services.evidence_worker.handlers import persist_and_reconcile, record_duplicate
from services.evidence_worker.reconciler import reconcile
from services.ml_worker.features import FEATURE_NAMES, RiskFeatures, build_features, to_vector
from services.ml_worker.model import RiskModel
from services.ml_worker.training import (best_threshold, build_dataset, rule_points, split_indices, train)
from shared.database.session import make_session_factory
from shared.messaging.broker import InMemoryBroker
from shared.messaging.runner import run_worker

NORMAL = dict(weight_deviation=0.02, weight_missing=0, missing_logistics_count=0, duplicate_event_count=0,
              identifier_mismatch=0, identifier_unverified=0, condition_worse_by=0, condition_unknown=0,
              missing_item_reported=0, max_logistics_gap_hours=26.0, prior_returns_90d=1)
WRONG = {**NORMAL, "identifier_mismatch": 1, "weight_deviation": 0.4}


@pytest.fixture(scope="module")
def trained():
    return train(seed=7, n=2000)


def test_feature_schema_has_no_label_or_scenario_leakage():
    banned_substrings = ("scenario", "label", "anomal")
    assert not any(b in n for n in FEATURE_NAMES for b in banned_substrings)
    assert "y" not in FEATURE_NAMES
    assert set(RiskFeatures.model_fields) == set(FEATURE_NAMES)


def test_missing_values_become_explicit_indicators():
    f = build_features({"weight_deviation": None, "condition_delta": None, "identifier_verified": 0})
    assert (f.weight_missing, f.condition_unknown, f.identifier_unverified, f.weight_deviation) == (1, 1, 1, 0.0)
    assert build_features({"condition_delta": -3}).condition_worse_by == 0  # better than reported is not 'worse'


def test_features_come_from_reconciliation_metrics():
    events, metas = generate(42, 2)
    rid = next(m["return_id"] for m in metas if m["scenario"] == "wrong_product")
    f = build_features(reconcile(rid, [e for e in events if e.return_id == rid]).metrics)
    assert f.identifier_mismatch == 1 and len(to_vector(f)) == len(FEATURE_NAMES)


def test_splits_are_disjoint_complete_and_stratified():
    _, y, *_ = build_dataset(3, 1000)
    tr, va, te = split_indices(y, 3)
    assert not (set(tr) & set(va) or set(tr) & set(te) or set(va) & set(te))
    assert len(tr) + len(va) + len(te) == len(y)
    assert max(abs(y[i].mean() - y.mean()) for i in (tr, va, te)) < 0.02


def test_threshold_selection_uses_only_given_scores():
    y = np.array([0, 0, 1, 1]); p = np.array([0.1, 0.4, 0.6, 0.9])
    assert 0.4 < best_threshold(y, p) <= 0.6


def test_training_is_reproducible():
    _, a = train(seed=5, n=800)
    _, b = train(seed=5, n=800)
    assert a == b


def test_report_is_honest_about_synthetic_data(trained):
    _, r = trained
    assert r["data"]["synthetic"] is True
    for key in ("precision", "recall", "f1", "pr_auc", "confusion_matrix"):
        assert key in r["test_model"] and key in r["test_rule_baseline"]
    assert r["test_model"]["pr_auc"] > 2 * r["test_model"]["prevalence"]  # learned something


def test_shuffled_labels_destroy_performance(trained):
    _, honest = trained
    _, shuffled = train(seed=7, n=2000, shuffle_labels=True)
    assert shuffled["test_model"]["pr_auc"] < 0.5 * honest["test_model"]["pr_auc"]


def test_scoring_ranks_wrong_product_above_normal_with_contributions(trained):
    model, _ = trained
    n, w = model.score(RiskFeatures(**NORMAL)), model.score(RiskFeatures(**WRONG))
    assert 0 <= n.score <= 1 and w.score > n.score and w.flagged and not n.flagged
    assert w.contributions and w.contributions[0].feature in {"identifier_mismatch", "weight_deviation"}
    assert "SYNTHETIC" in w.disclaimer


def test_save_load_roundtrip(trained, tmp_path):
    model, _ = trained
    model.save(tmp_path / "m.joblib")
    assert RiskModel.load(tmp_path / "m.joblib").score(RiskFeatures(**WRONG)) == model.score(RiskFeatures(**WRONG))


def test_feature_schema_drift_is_rejected(trained):
    model, _ = trained
    with pytest.raises(AssertionError):
        RiskModel({**model.a, "feature_names": FEATURE_NAMES[:-1]})


def test_rule_baseline():
    assert rule_points(RiskFeatures(**NORMAL)) == 0 and rule_points(RiskFeatures(**WRONG)) >= 2


# ---- API -------------------------------------------------------------------------------------
def test_risk_api(trained):
    model, _ = trained
    events, metas = generate(42, 3)
    sf, b = make_session_factory("sqlite://"), InMemoryBroker(3)
    for e in events:
        b.publish("ev", e.return_id, e.model_dump_json().encode())
    run_worker(b.consumer("g", "ev"), sf, "g", persist_and_reconcile, b, "dlq", on_duplicate=record_duplicate,
               idle_exit=True, sleep=lambda s: None)
    c = TestClient(create_app(sf, model))
    r = c.post("/risk/score", json=WRONG)
    assert r.status_code == 200 and r.json()["flagged"] is True
    bad = c.post("/risk/score", json={**NORMAL, "weight_deviation": -1})
    assert bad.status_code == 422 and c.post("/risk/score", json={"weight_deviation": 0.1}).status_code == 422
    by = {m["scenario"]: m["return_id"] for m in metas}
    hi = c.get(f"/returns/{by['conflicting_condition']}/risk").json()
    lo = c.get(f"/returns/{by['normal']}/risk").json()
    assert hi["risk"]["flagged"] and not lo["risk"]["flagged"]
    assert c.get("/returns/NOPE/risk").status_code == 404


def test_risk_api_503_without_model():
    c = TestClient(create_app(make_session_factory("sqlite://")))
    assert c.post("/risk/score", json=NORMAL).status_code == 503
