import json

from scripts.generate_data import generate
from services.evidence_worker.reconciler import reconcile
from services.investigation_worker.rag import Retriever, build_query, default_retriever, load_policies
from services.investigation_worker.report import AnthropicLLM, Context, deterministic_report, generate_report
from services.recovery_worker.engine import decide, inputs_from
from shared.schemas.investigation import InvestigationReport, PolicyRef
from shared.utilities.anthropic_client import AnthropicClient, ProviderUnavailable

EVENTS, METAS = generate(42, 1)
RET = default_retriever()


def ctx(scenario="wrong_product"):
    rid = next(m["return_id"] for m in METAS if m["scenario"] == scenario)
    res = reconcile(rid, [e for e in EVENTS if e.return_id == rid])
    dec = decide(inputs_from(res, None, None))
    refs = [PolicyRef(chunk_id=c.chunk_id, title=c.title, doc=c.doc, version=c.version, score=s, text=c.text)
            for c, s in RET.search(build_query(res, None), 4)]
    return Context(res, None, None, dec, refs)


class Fake:
    name = "llm:fake"
    def __init__(self, out=None, exc=None): self.out, self.exc = out, exc
    def complete_json(self, system, user):
        if self.exc: raise self.exc
        return self.out(user) if callable(self.out) else self.out


def good(c):
    r = deterministic_report(c).model_dump(mode="json")
    r["investigation_summary"] = "LLM-written summary of the evidence."
    return r


# ---- RAG -------------------------------------------------------------------------------------
def test_policies_are_versioned_and_chunked():
    ch = load_policies()
    assert len(ch) >= 12 and all(c.version == "1.0" and c.chunk_id for c in ch)
    assert len({c.chunk_id for c in ch}) == len(ch)


def test_retrieval_finds_relevant_sections_and_is_deterministic():
    top = lambda q: [c.chunk_id for c, _ in RET.search(q, 3)]
    assert {"RP-3", "WI-3"} & set(top("product identifier mismatch wrong product"))
    assert "WI-5" == top("missing hub scan chain of custody")[0]
    assert "RP-6" in top("opened headphones resale hygiene")
    assert top("weight lighter than expected") == top("weight lighter than expected")
    assert RET.search("   ") == [] and RET.search("zzzz qqqq") == []


def test_every_scenario_retrieves_at_least_one_policy():
    for m in METAS:
        assert ctx(m["scenario"]).policies or m["scenario"] == "normal"


# ---- LLM report validation -----------------------------------------------------------------------
def test_no_llm_uses_deterministic_report_that_validates_and_cites_real_ids():
    c = ctx(); rep, meta = generate_report(c, None)
    assert meta.generated_by == "deterministic_fallback" and isinstance(rep, InvestigationReport)
    assert set(rep.supporting_event_ids) <= {t.event_id for t in c.result.timeline}
    assert rep.recommended_action == c.decision.action and rep.human_review_required
    assert "not proof" in rep.investigation_summary


def test_valid_llm_output_is_accepted():
    c = ctx(); rep, meta = generate_report(c, Fake(json.dumps(good(c))))
    assert meta.generated_by == "llm:fake" and rep.investigation_summary.startswith("LLM-written")


def test_fenced_json_is_accepted():
    c = ctx(); _, meta = generate_report(c, Fake("```json\n" + json.dumps(good(c)) + "\n```"))
    assert meta.generated_by == "llm:fake"


def _rejected(c, mutate, expect):
    g = good(c); mutate(g)
    rep, meta = generate_report(c, Fake(json.dumps(g)))
    assert meta.generated_by == "deterministic_fallback" and expect in meta.fallback_reason
    assert rep.recommended_action == c.decision.action


def test_hallucinated_event_id_rejected():
    _rejected(ctx(), lambda g: g["supporting_event_ids"].append("evt-made-up"), "unknown event ids")


def test_hallucinated_policy_rejected():
    _rejected(ctx(), lambda g: g["relevant_policy_references"].append("RP-99"), "unknown policy references")


def test_hallucinated_finding_rejected():
    _rejected(ctx(), lambda g: g["evidence_findings"].append({"finding_id": "F-nope", "explanation": "x"}), "unknown finding ids")


def test_llm_cannot_change_the_decision():
    _rejected(ctx(), lambda g: g.update(recommended_action="RESALE"), "differs from the decision engine")
    _rejected(ctx(), lambda g: g.update(human_review_required=False), "human_review_required differs")


def test_invented_percentage_rejected_but_evidence_percentage_allowed():
    c = ctx("weight_discrepancy")
    _rejected(c, lambda g: g.update(investigation_summary="There is a 97% chance of fraud."), "percentages")
    real = next(f.summary for f in c.result.findings if "%" in f.summary)
    pct = real[real.index("(") + 1: real.index("%") + 1]
    g = good(c); g["investigation_summary"] = f"Weight was off by {pct}."
    assert generate_report(c, Fake(json.dumps(g)))[1].generated_by == "llm:fake"


def test_garbage_schema_violations_and_provider_failures_fall_back():
    c = ctx()
    for fake, why in [(Fake("not json at all"), "schema validation"), (Fake(json.dumps({"x": 1})), "schema validation"),
                      (Fake(exc=ProviderUnavailable("HTTP 429")), "provider failure"), (Fake(exc=RuntimeError("boom")), "LLM error")]:
        rep, meta = generate_report(c, fake)
        assert meta.generated_by == "deterministic_fallback" and why in meta.fallback_reason and rep.human_review_required


def test_prompt_contains_rules_and_only_context_ids():
    c = ctx(); seen = {}
    def out(user): seen["u"] = user; return json.dumps(good(c))
    generate_report(c, Fake(out))
    u = json.loads(seen["u"])
    assert {f["finding_id"] for f in u["findings"]} == {f.finding_id for f in c.result.findings}
    assert u["decision"]["action"] == "HUMAN_REVIEW"


def test_anthropic_llm_wrapper_with_mock_transport():
    import httpx
    c = ctx()
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(good(c))}]}))
    llm = AnthropicLLM(AnthropicClient("k", "m", client=httpx.Client(transport=t)))
    assert generate_report(c, llm)[1].generated_by == "llm:m"
