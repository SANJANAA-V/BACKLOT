import json
from pathlib import Path

import httpx
import pytest

from scripts.generate_data import generate
from services.vision_worker.adapters import AnthropicVisionAdapter, SimulatedVisionAdapter, VisionUnavailable
from services.vision_worker.analysis import analyze_return
from shared.utilities.anthropic_client import AnthropicClient
from shared.utilities.cache import InMemoryCache

EVENTS, METAS = generate(42, 1)
BY = {m["scenario"]: m for m in METAS}
ANN = {k: v for m in METAS for k, v in m["image_annotations"].items()}
OK_JSON = {"visible_damage": "yes", "damage_description": "cracked corner", "packaging_condition": "intact",
           "product_category_seen": "smartphone", "model_confidence": "HIGH"}


def report(scenario, adapter=None, cache=None, category=None):
    rid = BY[scenario]["return_id"]
    return analyze_return([e for e in EVENTS if e.return_id == rid], adapter or SimulatedVisionAdapter(ANN), cache, category)


def kinds(r):
    return {d.kind for d in r.differences}


def test_simulated_findings_are_labelled_unverified_and_simulated():
    r = report("normal")
    assert r.differences == [] and all(f.provenance == "SIMULATED" and f.verified is False for f in r.findings)
    assert "UNVERIFIED" in r.notice


def test_conflicting_condition_shows_damage_visibility_difference():
    assert "DAMAGE_VISIBILITY" in kinds(report("conflicting_condition"))


def test_visible_damage_has_no_difference_when_both_photos_agree():
    assert "DAMAGE_VISIBILITY" not in kinds(report("visible_damage"))


def test_wrong_product_and_empty_package_category_differences():
    assert "CATEGORY_MISMATCH" in kinds(report("wrong_product"))
    r = report("empty_package", category="smartphone")
    assert "EXPECTED_CATEGORY_MISMATCH" in kinds(r)


def test_missing_annotation_is_unavailable_not_a_crash():
    r = analyze_return([e for e in EVENTS if e.return_id == BY["normal"]["return_id"]], SimulatedVisionAdapter({}))
    assert {f.status for f in r.findings} == {"UNAVAILABLE"} and r.differences == []


def test_adapter_crash_is_isolated_per_image():
    class Boom:
        name, provenance = "boom", "MODEL_GENERATED"
        def analyze(self, uri, by): raise RuntimeError("secret-token-123")
    r = report("normal", Boom())
    assert {f.status for f in r.findings} == {"ERROR"} and "secret-token-123" not in json.dumps(r.model_dump())


def test_cache_avoids_second_adapter_call():
    calls = []
    class Counting(SimulatedVisionAdapter):
        def analyze(self, uri, by):
            calls.append(uri); return super().analyze(uri, by)
    cache, ad = InMemoryCache(), Counting(ANN)
    report("normal", ad, cache); n = len(calls)
    report("normal", ad, cache)
    assert n == 2 and len(calls) == n


# ---- Anthropic adapter with a mocked transport (never contacts the real API) -------------------
def make_adapter(handler, tmp_path, sleeps=None):
    (tmp_path / "a.png").write_bytes(b"\x89PNG fake")
    c = AnthropicClient("sk-test-KEY", "m", client=httpx.Client(transport=httpx.MockTransport(handler)),
                        sleep=(sleeps.append if sleeps is not None else lambda s: None))
    return AnthropicVisionAdapter(c, image_root=tmp_path)


def resp(text, status=200, headers=None):
    return httpx.Response(status, json={"content": [{"type": "text", "text": text}]}, headers=headers)


def test_anthropic_success_parses_fenced_json_and_sends_key_header_not_in_body(tmp_path):
    seen = {}
    def h(req):
        seen["key"], seen["body"] = req.headers["x-api-key"], req.content.decode()
        return resp("```json\n" + json.dumps(OK_JSON) + "\n```")
    obs = make_adapter(h, tmp_path).analyze("a.png", "customer")
    assert obs.visible_damage == "yes" and seen["key"] == "sk-test-KEY" and "sk-test-KEY" not in seen["body"]
    assert "serial" in seen["body"].lower()  # prompt forbids serial-number guesses


def test_rate_limit_retries_with_retry_after_then_succeeds(tmp_path):
    n, sleeps = {"c": 0}, []
    def h(req):
        n["c"] += 1
        return resp("", 429, {"retry-after": "2"}) if n["c"] < 3 else resp(json.dumps(OK_JSON))
    make_adapter(h, tmp_path, sleeps).analyze("a.png", "customer")
    assert n["c"] == 3 and sleeps == [2.0, 2.0]


def test_persistent_rate_limit_becomes_unavailable(tmp_path):
    with pytest.raises(VisionUnavailable):
        make_adapter(lambda r: resp("", 429), tmp_path).analyze("a.png", "customer")


def test_auth_error_and_bad_output_are_errors_without_leaking_key(tmp_path):
    from services.vision_worker.adapters import VisionError
    with pytest.raises(VisionError) as e1:
        make_adapter(lambda r: resp("", 401), tmp_path).analyze("a.png", "customer")
    with pytest.raises(VisionError) as e2:
        make_adapter(lambda r: resp("I think it is damaged"), tmp_path).analyze("a.png", "customer")
    assert "sk-test-KEY" not in str(e1.value) + str(e2.value)


def test_path_traversal_and_remote_urls_are_refused(tmp_path):
    ad = make_adapter(lambda r: resp(json.dumps(OK_JSON)), tmp_path)
    for uri in ("../../etc/passwd", "/etc/passwd", "https://evil.example/x.png", "synthetic://R-1/customer.jpg"):
        with pytest.raises(VisionUnavailable):
            ad.analyze(uri, "customer")


def test_no_api_key_means_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(VisionUnavailable):
        AnthropicVisionAdapter.from_env().analyze("a.png", "customer")
