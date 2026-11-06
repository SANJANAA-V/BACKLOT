from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[2] / "frontend" / "app.py")


def test_streamlit_app_runs_in_demo_mode_and_shows_investigation(monkeypatch):
    monkeypatch.delenv("BACKLOT_API_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert any("SYNTHETIC" in w.value for w in at.warning)  # demo banner
    at.selectbox[0].select("R-00002").run()                  # wrong_product sample
    next(b for b in at.button if b.label == "Run investigation").click().run()
    assert not at.exception, at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Recommended action"] == "HUMAN_REVIEW" and metrics["Human review"] == "PENDING"
    assert len(at.tabs) == 7
    assert any("IDENTIFIER_MISMATCH" in e.label for e in at.expander)


def test_unknown_return_shows_error_not_crash(monkeypatch):
    monkeypatch.delenv("BACKLOT_API_URL", raising=False)
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.text_input[0].set_value("R-NOPE").run()
    next(b for b in at.button if b.label == "Run investigation").click().run()
    assert not at.exception and at.error
