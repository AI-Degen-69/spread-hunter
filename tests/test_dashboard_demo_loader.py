"""The dashboard can load the demo registry from a running page.

The demo registry is the bogus store `scripts/build_demo_db.py` writes, so
every table and metric card can be eyeballed without a live run. Loading it
from a running page is the same read-only re-point the run switcher performs
(`POST /api/system/db`), aimed at one well-known path instead of a run the
operator picks from a list.

The safety property this file exists to pin: a page reading the demo is a
SHADOW page. `resolve_db_identity` reports `is_production=False` for any
store that is not `DEFAULT_DB_PATH`, and START is gated on that flag -- so a
button that loads bogus data next to the controls that spend money cannot
hand the operator a page that would start the live stack.

The toggle is pinned too: pressing the control again returns the page to its
launch store, and when the page was launched ON the demo itself the env var
that still points at it is cleared, so the button can never trap itself on
bogus data.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core_brain.order_registry import DEFAULT_DB_PATH, SCHEMA
from dashboard import server as srv
from dashboard.server import app, set_db_override


def _make_db(tmp_path: Path, name: str) -> Path:
    db = tmp_path / name
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db


def _token() -> dict:
    return {"X-Control-Token": srv.CONTROL_TOKEN}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A client whose demo path and live fallback live under tmp_path."""
    demo = _make_db(tmp_path, "demo_orders.db")
    monkeypatch.setattr(srv, "demo_db_path", lambda: demo)
    live = _make_db(tmp_path, "live.db")
    monkeypatch.setenv("LIVE_DB_PATH", str(live))
    set_db_override(None)
    yield TestClient(app)
    set_db_override(None)


def test_loading_the_demo_points_the_page_at_it(client):
    """One press re-points the page at the demo registry."""
    res = client.post("/api/system/demo", headers=_token())

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["demo"] is True
    assert Path(body["status"]["db_path"]).name == "demo_orders.db"


def test_a_page_reading_the_demo_cannot_start_the_live_stack(client):
    """The safety property: the demo store is SHADOW, so START stays gated."""
    client.post("/api/system/demo", headers=_token())

    status = client.get("/api/system/status").json()

    assert status["db_is_production"] is False
    assert status["db_mode"] != "LIVE"


def test_pressing_it_again_returns_the_page_to_the_launch_store(client):
    """The toggle: back to the store the page was launched on."""
    client.post("/api/system/demo", headers=_token())

    res = client.post("/api/system/demo", headers=_token())

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["demo"] is False
    assert Path(body["status"]["db_path"]).name == "live.db"


def test_a_missing_demo_registry_is_refused_with_the_build_command(client, tmp_path, monkeypatch):
    """No demo file yet: refuse, name the command, leave the page where it was."""
    monkeypatch.setattr(srv, "demo_db_path", lambda: tmp_path / "not-built.db")
    live = _make_db(tmp_path, "live.db")
    monkeypatch.setenv("LIVE_DB_PATH", str(live))
    set_db_override(live)

    res = client.post("/api/system/demo", headers=_token())

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "build_demo_db.py" in body["message"]
    # The page did not move.
    assert Path(body["status"]["db_path"]).name == "live.db"


def test_a_page_launched_on_the_demo_returns_to_production(client, monkeypatch):
    """The trap case: launched ON the demo, the env var still points at it.

    `--db <demo>` sets both the override and LIVE_DB_PATH, so returning to
    the launch store by dropping the override would resolve straight back to
    the demo. The endpoint clears that env var, so the fallback is the
    production registry and the button can never trap itself on bogus data.
    """
    demo = Path(srv.demo_db_path())
    monkeypatch.setenv("LIVE_DB_PATH", str(demo))
    set_db_override(demo)  # what `--db <demo>` does at startup

    res = client.post("/api/system/demo", headers=_token())

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["demo"] is False
    assert Path(body["status"]["db_path"]).resolve() == DEFAULT_DB_PATH.resolve()


def test_toggling_without_the_control_token_is_refused(client):
    """The control surface stays behind the control token."""
    res = client.post("/api/system/demo")

    assert res.status_code == 403
