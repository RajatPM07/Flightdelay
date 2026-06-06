"""The /landing marketing page is served only in demo mode and routes to both demo pages."""
import app.config as cfg
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_landing_served_when_demo_on(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    r = client.get("/landing")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "TripSecure" in r.text


def test_landing_404_when_demo_off(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", False)
    assert client.get("/landing").status_code == 404


def test_landing_links_to_both_demo_pages_and_stays_monitoring_only(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    html = client.get("/landing").text
    # CTAs route into the wired demo pages (not the old relative file paths)
    assert 'href="/"' in html        # decision-brain simulator
    assert 'href="/live"' in html    # multi-vendor status
    assert "app/static/demo.html" not in html
    assert "prefers-reduced-motion" in html
    # monitoring-only product framing — no payout/claim language leaked in
    lowered = html.lower()
    for banned in ["payout", "claim", "compensation"]:
        assert banned not in lowered, f"forbidden customer-facing term: {banned}"
