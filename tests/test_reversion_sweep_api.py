"""The /api/reversion/sweep route serves the whole grid, read-only.

Journeys under test:
1. As the Owner, the route returns the grid the terminal report shows, so the
   page and the CLI can never disagree about what the tape answered.
2. As the Owner, the route refuses the production registry by name, because a
   third reversion entry point is a third way to open it.
3. As the Owner, the held-out cut survives the HTTP round trip, so the page
   can score games nobody has read yet without pooling them with the past.
4. As the Owner, the route is not cached, because a grid that answers with
   the tape's own age must not be re-served stale by the browser.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core_brain.reversion_watch import open_store


@pytest.fixture()
def client():
    from dashboard.server import app
    with TestClient(app) as test_client:
        yield test_client


def _tape(tmp_path, *, slug="lol-a-b", n=40):
    conn = open_store(tmp_path / "sweep.db")
    for i in range(n):
        mid = 0.50 if i < 30 else 0.55
        conn.execute("INSERT OR IGNORE INTO quotes VALUES (?,?,?,?)",
                     (slug, 1_700_000_000 + i * 60, mid - 0.005, mid + 0.005))
    conn.commit()
    conn.close()
    return tmp_path / "sweep.db"


def test_the_sweep_route_serves_the_grid(client, tmp_path):
    path = _tape(tmp_path)

    r = client.get("/api/reversion/sweep", params={"db": str(path)})

    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "READY"
    assert body["cells"]
    assert "pre_registered" in body["cells"][0]


def test_the_sweep_route_refuses_the_registry(client, tmp_path):
    registry = tmp_path / "orders.db"
    registry.write_bytes(b"")

    r = client.get("/api/reversion/sweep", params={"db": str(registry)})

    assert r.status_code == 400
    assert "registry" in r.json()["detail"] or "order" in r.json()["detail"]


def test_the_held_out_cut_survives_the_round_trip(client, tmp_path):
    path = _tape(tmp_path)

    r = client.get("/api/reversion/sweep",
                   params={"db": str(path), "games_from": 1_700_000_500})

    assert r.status_code == 200
    body = r.json()
    assert body["games_from"] == 1_700_000_500
    assert all(c["trades"] == 0 for c in body["cells"])


def test_the_sweep_route_is_not_cached(client, tmp_path):
    path = _tape(tmp_path)

    r = client.get("/api/reversion/sweep", params={"db": str(path)})

    assert "no-cache" in r.headers["Cache-Control"]
