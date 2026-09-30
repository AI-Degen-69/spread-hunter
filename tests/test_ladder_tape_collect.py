"""The tape collector records both legs of a series, read-only vs the venue."""
from __future__ import annotations

import json
import sqlite3

import pytest

from scripts import ladder_tape_collect
from scripts.ladder_tape_collect import (
    _usable_market,
    collect,
)

OPEN = 1790790300


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Session:
    def __init__(self, events, histories):
        self._events = events
        self._histories = histories
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url.endswith("/events"):
            return _Resp(self._events)
        return _Resp({"history": self._histories.get(
            params["market"], [])})


def _market_row(**over):
    base = {"conditionId": "0xabc", "question": "Bitcoin Up or Down",
            "slug": f"btc-updown-5m-{OPEN}",
            "clobTokenIds": json.dumps(["tok-up", "tok-down"]),
            "outcomePrices": json.dumps(["1", "0"])}
    base.update(over)
    return base


def _session():
    events = [{"markets": [_market_row()]}]
    histories = {
        "tok-up": [{"t": OPEN + 16, "p": 0.60}, {"t": OPEN + 200, "p": 0.99}],
        "tok-down": [{"t": OPEN + 17, "p": 0.40}, {"t": OPEN + 200, "p": 0.01}],
    }
    return _Session(events, histories)


# --- usability gate ------------------------------------------------------------

def test_needs_two_tokens_start_and_clean_resolution():
    assert _usable_market(_market_row())["up_wins"] is True
    assert _usable_market(_market_row(clobTokenIds=json.dumps(["only"]))) is None
    assert _usable_market(_market_row(slug="no-epoch-here")) is None
    assert _usable_market(_market_row(outcomePrices=json.dumps(["0.5", "0.5"]))) is None


# --- collection ------------------------------------------------------------------

def test_collects_both_legs_with_ticks_and_resolution(tmp_path):
    db = tmp_path / "ladder_tape.db"
    stats = collect(series_slug="btc-up-or-down-5m", out_db=db,
                    max_markets=10, session=_session())
    assert stats["markets"] == 1
    assert stats["ticks"] == 4
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT token_id, up_wins, first_seen FROM markets").fetchall()
    assert sorted(r[0] for r in rows) == ["tok-down", "tok-up"]
    assert {r[1] for r in rows} == {1}
    assert {r[2] for r in rows} == {OPEN}
    assert con.execute("SELECT COUNT(*) FROM ticks").fetchone()[0] == 4
    con.close()


def test_production_store_is_refused(tmp_path):
    with pytest.raises(Exception):
        collect(series_slug="x", out_db=tmp_path / "orders.db",
                max_markets=1, session=_session())


def test_probe_reads_collected_tape(tmp_path):
    from scripts.ladder_probe import run
    db = tmp_path / "ladder_tape.db"
    collect(series_slug="btc-up-or-down-5m", out_db=db,
            max_markets=10, session=_session())
    out = tmp_path / "report.json"
    report = run(str(db), str(out), slug_like="btc-updown-%", window_sec=300)
    assert report["markets"] == 1
    assert report["by_shape"]["2"]["policies"]["hold"]["mean_pnl"] is not None
