from scripts.generate_data import SCENARIOS, generate


def test_deterministic():
    a, ma = generate(42, 3)
    b, mb = generate(42, 3)
    assert [e.model_dump_json() for e in a] == [e.model_dump_json() for e in b]
    assert ma == mb


def test_different_seed_differs():
    a, _ = generate(1, 3)
    b, _ = generate(2, 3)
    assert [e.model_dump_json() for e in a] != [e.model_dump_json() for e in b]


def test_all_scenarios_and_synthetic_flag():
    events, metas = generate(42, 2)
    assert {m["scenario"] for m in metas} == set(SCENARIOS)
    assert all(e.synthetic for e in events)


def test_missing_scan_has_no_hub_event():
    events, metas = generate(42, 3)
    rids = {m["return_id"] for m in metas if m["scenario"] == "missing_logistics_scan"}
    assert not any(e.event_type.value == "HUB_RECEIVED" and e.return_id in rids for e in events)


def test_duplicate_scenario_redelivers_same_event_id():
    events, metas = generate(42, 3)
    rid = next(m["return_id"] for m in metas if m["scenario"] == "duplicate_logistics_event")
    ids = [e.event_id for e in events if e.return_id == rid]
    assert len(ids) != len(set(ids))
