"""Tests for the interactive strategy explainer and simulator endpoint."""
import pytest
from fastapi.testclient import TestClient
from server.spread_dash import app


@pytest.fixture
def client():
    return TestClient(app)


def test_explainer_page_loads(client):
    res = client.get("/explainer")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    text = res.text
    
    # Verify core sections exist
    assert "Strategy Visualizer &amp; Live Playable Simulator" in text or "Strategy Visualizer" in text
    assert "THE CORE EDGE" in text
    assert "Interactive Dual Order-Book &amp; Merge Simulator" in text or "Interactive Dual Order-Book" in text
    assert "ctf.mergePositions()" in text
    assert "Interactive Capital Velocity &amp; Compounding Calculator" in text or "Compounding Calculator" in text
    assert "Hedged / Matched Pairs" in text
    assert "+$0.70" in text
    assert "-$0.95" in text


def test_navbar_includes_explainer_link(client):
    # Check dashboard, bankroll, landing, and explainer all include the explainer link
    for path in ["/", "/bankroll", "/explainer", "/landing"]:
        res = client.get(path)
        assert res.status_code == 200
        assert 'href="/explainer"' in res.text
