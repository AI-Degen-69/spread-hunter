"""Tab 2 (Orders): the stitch terminal's resting-order table.

Five columns, left to right: Market & Arbitrage Status | Dual-Leg Quotes
(YES / NO) | Total Committed | LAST UPDATED | Actions. An absent leg renders
its counterpart fate: a filled counterpart holds a position (green), a
cancelled one is requoting, a genuinely missing one awaits a quote.
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


_FIXTURE = """
const kpi = {
    by_market: {
        '0xalp': {
            condition_id: '0xalp', title: 'Alcaraz vs Sinner', category: 'Tennis',
            days_to_resolve: 6.9, volume_24h: 152000, resolved: false,
            quotes_count: 2, up_sh: 0, dn_sh: 0, total_sh: 0,
            total_cost: 0, pair_cost: null, realized_pnl: 0,
            quotes: [
                { token_id: 'tok-u', side: 'UP', mid: 0.275, ts: 1700000000,
                  price: 0.25, order_id: 'ord-u1', queue_ahead: null },
                { token_id: 'tok-d', side: 'DN', mid: 0.725, ts: 1700000001,
                  price: 0.70, order_id: null, queue_ahead: null }
            ]
        },
        '0xswi': {
            condition_id: '0xswi', title: 'Swiatek vs Sabalenka', category: 'Tennis',
            days_to_resolve: 2.3, volume_24h: 98400, resolved: false,
            quotes_count: 2, up_sh: 0, dn_sh: 0, total_sh: 0,
            total_cost: 0, pair_cost: null, realized_pnl: 0,
            quotes: [
                { token_id: 'tok-pu', side: 'UP', mid: 0.445, ts: 1700000000,
                  price: 0.42, order_id: 'ord-p-up', queue_ahead: null },
                { token_id: 'tok-pd', side: 'DN', mid: 0.555, ts: 1700000001,
                  price: 0.53, order_id: 'ord-p-dn', queue_ahead: null }
            ]
        }
    }
};
const restingUP = { order_id: 'ord-u1', condition_id: '0xalp', token_id: 'tok-u',
  pair_id: 'pair-u1', side: 'BUY', price: 0.25, original_size: 10.0,
  size_matched: 0.0, size_remaining: 10.0, status: 'open',
  posted_ts: 1700000100, age_sec: 45.0 };
const filledDN = { order_id: 'ord-d0', condition_id: '0xalp', token_id: 'tok-d',
  pair_id: 'pair-u1', side: 'BUY', price: 0.70, original_size: 5.0,
  filled_size: 5.0, size_matched: 5.0, size_remaining: 0.0, status: 'filled',
  posted_ts: 1700000050, age_sec: 90.0 };
const cancelledDN = { order_id: 'ord-d9', condition_id: '0xalp', token_id: 'tok-d',
  pair_id: 'pair-u1', side: 'BUY', price: 0.70, original_size: 5.0,
  size_matched: 0.0, size_remaining: 5.0, status: 'cancelled',
  posted_ts: 1700000050, age_sec: 90.0 };
const pairedUP = { order_id: 'ord-p-up', condition_id: '0xswi', token_id: 'tok-pu',
  pair_id: 'pair-p1', side: 'BUY', price: 0.42, original_size: 10.0,
  size_matched: 0.0, size_remaining: 10.0, status: 'open',
  posted_ts: 1700000200, age_sec: 120.0 };
const pairedDN = { order_id: 'ord-p-dn', condition_id: '0xswi', token_id: 'tok-pd',
  pair_id: 'pair-p1', side: 'BUY', price: 0.53, original_size: 10.0,
  size_matched: 0.0, size_remaining: 10.0, status: 'open',
  posted_ts: 1700000201, age_sec: 119.0 };
// A fill from an EARLIER trade in the same market: same condition, DN leg,
// but no pair_id (it settled before pairs were tracked). It must never
// classify the current pair as hedge-pending.
const stalePairFillDN = { order_id: 'ord-old', condition_id: '0xalp', token_id: 'tok-d',
  pair_id: null, side: 'BUY', price: 0.65, original_size: 5.0,
  filled_size: 5.0, size_matched: 5.0, size_remaining: 0.0, status: 'filled',
  posted_ts: 1700000000, age_sec: 200.0 };
// A cancellation posted AFTER the pair's fill: the held position survives it.
const lateCancelDN = { order_id: 'ord-d8', condition_id: '0xalp', token_id: 'tok-d',
  pair_id: 'pair-u1', side: 'BUY', price: 0.71, original_size: 5.0,
  size_matched: 0.0, size_remaining: 5.0, status: 'cancelled',
  posted_ts: 1700000300, age_sec: 10.0 };
"""


@requires_node
def test_stitch_orders_head_has_five_columns_in_spec_order():
    code = _FIXTURE + """
    const head = app.stitchOpenOrdersHeadHtml(null);
    console.log(JSON.stringify({ head }));
    """
    res = json.loads(_eval_js(code))
    head = res["head"]
    positions = ["Market", "Dual-Leg Quotes (YES / NO)", "Total Committed",
                 "LAST UPDATED", "Actions"]
    for label in positions:
        assert label in head, f"missing header: {label}"
    assert [head.index(p) for p in positions] == sorted(head.index(p) for p in positions)
    assert 'data-ot-sort' not in head.split("Actions")[1]
    assert head.count("<th") == 5


@requires_node
def test_stitch_orders_unpaired_row_shows_resting_leg_and_missing_counterpart():
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, { orders: [restingUP] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    row = html.split('data-pair="pair-u1"')[1]
    assert "Alcaraz vs Sinner" in row
    assert "10 @ $0.25" in html
    assert "cost $2.50" in html
    assert "[Awaiting NO Quote]" in html
    assert "UNPAIRED" in html
    assert "Missing Leg" in html
    assert "00:45" in html
    assert "Cancel" in html


@requires_node
def test_stitch_orders_filled_counterpart_holds_position():
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, { orders: [restingUP, filledDN] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "HEDGE PENDING" in html
    assert "Position Held" in html
    assert "5 @ $0.70" in html
    assert "Single-Fill (Hedge Active)" in html
    assert "is-hedge-pending" in html


@requires_node
def test_stitch_orders_cancelled_counterpart_requotes():
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, { orders: [restingUP, cancelledDN] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "NO" in html and "[Cancelled / Requoting]" in html
    assert "Position Held" not in html


@requires_node
def test_stitch_orders_earlier_pair_fill_does_not_classify_current_row():
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, { orders: [restingUP, stalePairFillDN] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "HEDGE PENDING" not in html
    assert "Position Held" not in html
    assert "[Awaiting NO Quote]" in html


@requires_node
def test_stitch_orders_late_cancel_does_not_hide_earlier_fill():
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, { orders: [restingUP, filledDN, lateCancelDN] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "HEDGE PENDING" in html
    assert "Position Held" in html


@requires_node
def test_stitch_orders_partially_matched_leg_never_claims_no_position():
    code = _FIXTURE + """
    const partial = Object.assign({}, restingUP, { size_matched: 4.0, size_remaining: 6.0 });
    const html = app.stitchOpenOrdersRows(kpi, { orders: [partial] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "No Position" not in html
    assert "Partial Fill Held" in html


@requires_node
def test_stitch_orders_empty_state_spans_all_five_columns():
    code = """
    const html = app.stitchOpenOrdersRows({ by_market: {} }, { orders: [] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    assert 'colspan="5"' in res["html"]
    assert "No orders are resting on the book." in res["html"]


@requires_node
def test_stitch_orders_sorts_biggest_commit_first():
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(
        kpi, { orders: [restingUP, pairedUP, pairedDN] }, { col: 5, dir: 'desc' });
    const pairedFirst = html.indexOf('data-pair="pair-p1"') < html.indexOf('data-pair="pair-u1"');
    console.log(JSON.stringify({ pairedFirst }));
    """
    res = json.loads(_eval_js(code))
    assert res["pairedFirst"] is True
