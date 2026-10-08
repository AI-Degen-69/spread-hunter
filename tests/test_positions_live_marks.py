"""Open positions under live marks (#427): the node harness drives app.js.

A naked UP leg re-prices the moment a fresh venue mid arrives over the
existing SSE connection; matched pairs stay at $1, finished markets stay
out, and stale/invalid/reset frames never paint a wrong price. All offline:
mark frames are injected, never fetched.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
HARNESS = _ROOT / "tests" / "js" / "live_marks_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")

NOW_MS = 1_700_000_000_000
NOW_SEC = NOW_MS / 1000.0

CID_LIVE = "0xlive"
CID_PAIR = "0xpair"
CID_DONE = "0xdone"


def _quote(token, side, mid, ts, price=None):
    return {"token_id": token, "side": side, "mid": mid, "ts": ts,
            "price": mid - 0.02 if price is None else price,
            "order_id": None, "queue_ahead": None}


def _kpi() -> dict:
    return {"by_market": {
        CID_LIVE: {
            "condition_id": CID_LIVE, "title": "Live Market", "resolved": False,
            "days_to_resolve": 2.0, "up_sh": 10, "dn_sh": 0, "total_sh": 10,
            "up_cost": 4.70, "dn_cost": 0.0, "total_cost": 4.70,
            "pair_cost": None, "realized_pnl": 0.0,
            "quotes": [
                _quote("tok-live-up", "UP", 0.47, 200.0),
                _quote("tok-live-dn", "DN", 0.53, 201.0),
            ],
            "fills": [],
        },
        CID_PAIR: {
            "condition_id": CID_PAIR, "title": "Pair Market", "resolved": False,
            "days_to_resolve": 3.0, "up_sh": 5, "dn_sh": 5, "total_sh": 10,
            "up_cost": 2.45, "dn_cost": 2.45, "total_cost": 4.90,
            "pair_cost": 0.98, "realized_pnl": 0.0,
            "quotes": [
                _quote("tok-pair-up", "UP", 0.49, 200.0),
                _quote("tok-pair-dn", "DN", 0.51, 201.0),
            ],
            "fills": [],
        },
        CID_DONE: {
            "condition_id": CID_DONE, "title": "Done Market", "resolved": True,
            "days_to_resolve": -1.0, "up_sh": 5, "dn_sh": 0, "total_sh": 5,
            "up_cost": 2.35, "dn_cost": 0.0, "total_cost": 2.35,
            "pair_cost": None, "realized_pnl": 0.0,
            "quotes": [_quote("tok-done-up", "UP", 0.47, 100.0)],
            "fills": [],
        },
    }}


def _mark(token, mid, ts, seq, bid=None, ask=None):
    return {"token_id": token, "bid": mid if bid is None else bid,
            "ask": mid if ask is None else ask, "mid": mid, "ts": ts, "seq": seq}


def _run(*, emit=None, apply=None, clear=False, kpi=None) -> dict:
    payload = {"kpi": kpi if kpi is not None else _kpi(), "state": {},
               "nowMs": NOW_MS, "emit": emit or [], "apply": apply or [],
               "clear": clear}
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _cell(html: str, cid: str, cell: str) -> str | None:
    # The whole <td> tag: the class attribute precedes data-cid, so the
    # match must start at <td, not at data-cid.
    m = re.search(r'<td[^>]*data-cid="%s" data-cell="%s"[^>]*>(.*?)</td>' % (
        re.escape(cid), cell), html, re.S)
    return m.group(0) + "|||" + m.group(1) if m else None


def _tag(cell: str | None) -> str:
    return cell.split("|||")[0] if cell else ""


def _has_live(cell: str | None) -> bool:
    # The `live` CSS class on the tag — never a bare substring, the `0xlive`
    # condition id matches that too.
    return re.search(r'class="[^"]*\blive\b', _tag(cell)) is not None


@requires_node
def test_mark_listener_registered_on_shared_connection():
    res = _run()
    assert res["markListener"] is True


@requires_node
def test_live_mark_moves_value_and_unrealized_with_live_affordance():
    snap = {"seq": 3, "snapshot": True, "reset": False,
            "marks": [_mark("tok-live-up", 0.60, NOW_SEC - 1, 3)]}
    res = _run(emit=[snap])
    value = _cell(res["html"], CID_LIVE, "value")
    assert value is not None
    assert "$6.00" in value  # 10 naked UP shares at the live 0.60 mid
    assert _has_live(value)
    assert "Live mark" in _tag(value)
    unrealized = _cell(res["html"], CID_LIVE, "unrealized")
    assert unrealized is not None
    assert "+$1.30" in unrealized  # 6.00 - 4.70 cost


@requires_node
def test_quote_mids_render_without_live_class_before_any_mark():
    res = _run()
    value = _cell(res["html"], CID_LIVE, "value")
    assert value is not None
    assert "$4.70" in value  # 10 shares at the quote mid 0.47
    assert not _has_live(value)


@requires_node
def test_matched_pairs_stay_at_par_under_live_marks():
    snap = {"seq": 5, "snapshot": True, "reset": False, "marks": [
        _mark("tok-pair-up", 0.90, NOW_SEC - 1, 5),
        _mark("tok-pair-dn", 0.10, NOW_SEC - 1, 5)]}
    res = _run(emit=[snap])
    value = _cell(res["html"], CID_PAIR, "value")
    assert value is not None
    assert "$5.00" in value  # merged pairs redeem at par, never at the book


@requires_node
def test_finished_markets_stay_excluded():
    snap = {"seq": 7, "snapshot": True, "reset": False,
            "marks": [_mark("tok-done-up", 0.99, NOW_SEC - 1, 7)]}
    res = _run(emit=[snap])
    assert CID_DONE not in res["html"]


@requires_node
def test_stale_and_invalid_marks_never_paint():
    res = _run(apply=[
        {"payload": {"seq": 9, "snapshot": False, "reset": False, "marks": [
            _mark("tok-live-up", 0.60, NOW_SEC - 60, 9),      # 60s old: stale
            _mark("tok-live-up", 1.50, NOW_SEC - 1, 10),      # out of range
            {"token_id": None, "mid": 0.60, "ts": NOW_SEC - 1, "seq": 11},
            _mark("tok-live-up", "not-a-number", NOW_SEC - 1, 12)]},
         "nowMs": NOW_MS},
    ])
    value = _cell(res["html"], CID_LIVE, "value")
    assert value is not None
    assert "$4.70" in value  # quote mid stands; nothing live painted
    assert not _has_live(value)


@requires_node
def test_newer_mark_wins_older_delta_ignored():
    res = _run(apply=[
        {"payload": {"seq": 5, "snapshot": False, "reset": False,
                     "marks": [_mark("tok-live-up", 0.60, NOW_SEC - 1, 5)]},
         "nowMs": NOW_MS},
        {"payload": {"seq": 5, "snapshot": False, "reset": False,
                     "marks": [_mark("tok-live-up", 0.90, NOW_SEC - 1, 3)]},
         "nowMs": NOW_MS},
    ])
    assert res["applied"] == [True, False]
    by_tok = {e["token"]: e["mid"] for e in res["live"]}
    assert by_tok["tok-live-up"] == pytest.approx(0.60)


@requires_node
def test_reset_frame_clears_marks_back_to_quotes():
    res = _run(emit=[
        {"seq": 5, "snapshot": True, "reset": False,
         "marks": [_mark("tok-live-up", 0.60, NOW_SEC - 1, 5)]},
        {"seq": 6, "snapshot": False, "reset": True, "marks": []},
    ])
    assert res["liveSize"] == 0
    value = _cell(res["html"], CID_LIVE, "value")
    assert value is not None and "$4.70" in value


@requires_node
def test_store_switch_clear_drops_marks():
    res = _run(
        emit=[{"seq": 5, "snapshot": True, "reset": False,
               "marks": [_mark("tok-live-up", 0.60, NOW_SEC - 1, 5)]}],
        clear=True)
    assert res["liveSize"] == 0
    value = _cell(res["html"], CID_LIVE, "value")
    assert value is not None and not _has_live(value)
