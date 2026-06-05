"""The /live page is served only in demo mode and carries its key elements."""
import app.config as cfg
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_live_page_served_when_demo_on(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    r = client.get("/live")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    for marker in ['id="live-form"', 'id="live-result"', 'id="live-edgecases"',
                   'id="live-vendor"', 'id="live-flight"', 'id="live-date"']:
        assert marker in r.text, f"missing {marker}"


def test_live_page_404_when_demo_off(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", False)
    assert client.get("/live").status_code == 404
