"""Per-leg current price and unrealized P&L on the stitch positions table.

Each market row carries its own CURRENT PRICE cell (YES line first, NO below
-- the same stacked structure as the YES/NO HELD cells) and per-leg
unrealized lines, all fed by the live-marks overlay so they keep updating
while the book moves.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_APP_JS = _ROOT / "dashboard" / "static" / "app.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _eval_js(code: str) -> str:
    runner = f"""
const fs = require('fs');
const source = fs.readFileSync({json.dumps(str(_APP_JS))}, 'utf8');
const mod = {{ exports: {{}} }};
const noop = () => {{}};
const stub = () => ({{
    textContent: '', innerHTML: '', style: {{}}, dataset: {{}},
    classList: {{ add: noop, remove: noop, toggle: noop, contains: () => true }},
    addEventListener: noop, querySelector: () => null, querySelectorAll: () => [],
    setAttribute: noop, getAttribute: () => null
}});
const doc = {{
    getElementById: stub, querySelector: () => null, querySelectorAll: () => [],
    addEventListener: noop, body: {{ classList: {{ add: noop, remove: noop, toggle: noop, contains: () => true }} }}
}};
const win = {{ addEventListener: noop, location: {{ href: '' }} }};
const storage = {{ getItem: () => null, setItem: noop, removeItem: noop }};
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
    mod, mod.exports, doc, win, storage, function() {{ return {{ addEventListener: noop }}; }}
);
const app = mod.exports;
{code}
"""
    out = subprocess.run([shutil.which("node"), "-e", runner],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return out.stdout.strip()


def _kpi(up_sh=10.0, up_cost=8.0, up_mid=0.76, dn_sh=0.0, dn_cost=0.0, dn_mid=None):
    quotes = []
    if up_sh > 0:
        quotes.append({"side": "UP", "price": 0.80, "mid": up_mid,
                       "token_id": "tok-up", "ts": 1700000000000})
    if dn_sh > 0:
        quotes.append({"side": "DOWN", "price": 0.14, "mid": dn_mid,
                       "token_id": "tok-dn", "ts": 1700000000000})
    return {
        "by_market": {
            "0xabc": {
                "condition_id": "0xabc",
                "title": "LoL: FOX vs JDG",
                "slug": "lol-fox-jdg",
                "category": "E-Sports",
                "up_sh": up_sh,
                "up_cost": up_cost,
                "dn_sh": dn_sh,
                "dn_cost": dn_cost,
                "total_cost": up_cost + dn_cost,
                "total_sh": up_sh + dn_sh,
                "realized_pnl": 0.0,
                "quotes": quotes,
            }
        }
    }


@requires_node
def test_positions_head_has_current_price():
    res = json.loads(_eval_js(
        "console.log(JSON.stringify({head: app.stitchPositionsHeadHtml(null)}));"))
    assert "CURRENT PRICE" in res["head"]


@requires_node
def test_single_leg_row_shows_its_own_current_price_and_pnl():
    # 10 UP @ $0.80 avg against a $0.76 mid: a loss, priced live.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi())};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    assert 'data-cell="cur-price"' in html
    assert "YES $0.76<" in html or "YES $0.76 " in html
    assert "NO $0.24" not in html  # unheld leg prints no line
    assert ">YES <span" in html
    assert "-$0.40" in html
    assert "(-5.0%)" in html


@requires_node
def test_paired_row_stacks_no_below_yes_with_a_total():
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(dn_sh=10.0, dn_cost=1.40, dn_mid=0.24))};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    yes_at = html.index("YES $0.76")
    no_at = html.index("NO $0.24")
    assert yes_at < no_at  # NO below YES in the same cell
    assert "Total" in html


@requires_node
def test_live_overlay_moves_the_per_leg_lines():
    # Quote mid says $0.84 (the frozen case); a fresh venue mark at $0.77
    # must win on the next render.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(up_mid=0.84))};
    const nowMs = Date.now();
    app.applyLiveMarks(
        {{marks: [{{token_id: 'tok-up', bid: 0.76, ask: 0.78, mid: 0.77,
                   ts: nowMs, seq: 2}}], snapshot: true}}, nowMs);
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    assert "YES $0.77" in html
    assert "-$0.30" in html
    assert "live mark" in html


@requires_node
def test_live_dot_sits_only_on_the_fresh_leg():
    # Only the YES token has a fresh mark: its line carries the dot, the NO
    # quote-mid fallback line must not claim to be live.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(dn_sh=10.0, dn_cost=1.40, dn_mid=0.24))};
    const nowMs = Date.now();
    app.applyLiveMarks(
        {{marks: [{{token_id: 'tok-up', bid: 0.76, ask: 0.77, mid: 0.765,
                   ts: nowMs, seq: 2}}], snapshot: true}}, nowMs);
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    yes_line = html[html.index('data-leg="YES"'):html.index('data-leg="NO"')]
    no_line = html[html.index('data-leg="NO"'):]
    assert "stitch-cur-live" in yes_line
    assert "stitch-cur-live" not in no_line.split("</div>")[0]


@requires_node
def test_paired_total_uses_par_valuation_like_mark_value():
    # Leg mids 0.70/0.60 sum past $1.00: the leg lines may read profit while
    # the pair at par ($10.00 on $11.00 cost) is down $1.00. The total must
    # agree with MARK VALUE, never with the summed mids.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(up_sh=10.0, up_cost=5.50, up_mid=0.70,
                                 dn_sh=10.0, dn_cost=5.50, dn_mid=0.60))};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    assert "+$1.50" in html  # YES leg line stays mid-based
    assert "+$0.50" in html  # NO leg line stays mid-based
    total_at = html.index("Total")
    assert "-$1.00" in html[total_at:]


def _two_market_kpi(mid_a, mid_b):
    # 0xabc holds 100 sh (big value, low price); 0xdef holds 10 sh.
    # Descending by price puts 0xdef first; descending by position value
    # would put 0xabc first -- the test tells the two sort keys apart.
    base_a = _kpi(up_sh=100.0, up_cost=40.0, up_mid=mid_a)["by_market"]["0xabc"]
    base_b = {**base_a, "condition_id": "0xdef", "title": "Other market",
              "slug": "other", "up_sh": 10.0, "up_cost": 8.0,
              "total_cost": 8.0, "total_sh": 10.0,
              "quotes": [{"side": "UP", "price": 0.80, "mid": mid_b,
                          "token_id": "tok-up-b", "ts": 1700000000000}]}
    return {"by_market": {"0xabc": base_a, "0xdef": base_b}}


@requires_node
def test_current_price_sorts_by_price_not_position_value():
    # 100 sh @ $0.40 (value $40) vs 10 sh @ $0.80 (value $8): descending
    # price puts the $0.80 row first even though its position is smaller.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_two_market_kpi(0.40, 0.80))};
    const html = app.stitchPositionsRows(kpi, {{orders: []}},
                                         {{col: 9, dir: 'desc'}});
    console.log(JSON.stringify({{html}}));
    """))
    html = res["html"]
    assert html.index("0xdef") < html.index("0xabc")


@requires_node
def test_current_price_sort_follows_a_mark_update():
    # A fresh mark moves 0xabc from $0.40 to $0.90: the next sorted render
    # must flip the row order without any other change.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_two_market_kpi(0.40, 0.80))};
    const before = app.stitchPositionsRows(kpi, {{orders: []}},
                                           {{col: 9, dir: 'desc'}});
    const nowMs = Date.now();
    app.applyLiveMarks(
        {{marks: [{{token_id: 'tok-up', bid: 0.89, ask: 0.91, mid: 0.90,
                   ts: nowMs, seq: 2}}], snapshot: true}}, nowMs);
    const after = app.stitchPositionsRows(kpi, {{orders: []}},
                                          {{col: 9, dir: 'desc'}});
    console.log(JSON.stringify({{before, after}}));
    """))
    assert res["before"].index("0xdef") < res["before"].index("0xabc")
    assert res["after"].index("0xabc") < res["after"].index("0xdef")
