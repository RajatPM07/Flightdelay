import pytest
from fastapi.testclient import TestClient
import app.config as cfg
from app.main import app

client = TestClient(app)


def test_page_served_when_demo_mode_on(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "TripSecure" in r.text


def test_page_404_when_demo_mode_off(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", False)
    r = client.get("/")
    assert r.status_code == 404


def test_page_has_key_elements(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    html = client.get("/").text
    for marker in ['id="issue-form"', 'id="dashboard"', 'id="stepper"',
                   'id="sim-controls"', 'id="timeline"', 'id="notifications"',
                   'id="payload-console"', 'Space Grotesk', '#B02A30',
                   'gsap.min.js', 'prefers-reduced-motion']:
        assert marker in html, f"missing {marker}"
