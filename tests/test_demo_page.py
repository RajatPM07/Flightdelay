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
