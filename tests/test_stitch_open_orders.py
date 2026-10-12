"""Tab 2 (Orders): the stitch terminal's resting-order table.

Seven columns, left to right: Market | Side & Resting Quote | Pair Target |
Committed Capital | Risk State | Age / Stamp | Actions. Queue Ahead is gone:
it described where the order stands in line, not what the operator risks by
leaving it there. The risk read -- committed dollars beside the UNPAIRED
badge and the counter-quote still needed -- is what this tab answers now.
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
const state = {
    orders: [
        // Unpaired: YES leg resting alone, no pair cap recorded -> default 0.99.
        { order_id: 'ord-u1', condition_id: '0xalp', token_id: 'tok-u',
          pair_id: 'pair-u1', side: 'BUY', price: 0.25, original_size: 10.0,
          size_matched: 0.0, size_remaining: 10.0, status: 'open',
          posted_ts: 1700000100, age_sec: 45.0 },
        // Paired: both legs resting, pair cap posted at 0.98.
        { order_id: 'ord-p-up', condition_id: '0xswi', token_id: 'tok-pu',
          pair_id: 'pair-p1', side: 'BUY', price: 0.42, original_size: 10.0,
          size_matched: 0.0, size_remaining: 10.0, status: 'open',
          posted_ts: 1700000200, age_sec: 120.0 },
        { order_id: 'ord-p-dn', condition_id: '0xswi', token_id: 'tok-pd',
          pair_id: 'pair-p1', side: 'BUY', price: 0.53, original_size: 10.0,
          size_matched: 0.0, size_remaining: 10.0, status: 'open',
          posted_ts: 1700000201, age_sec: 119.0 }
    ],
    pairs: [
        { pair_id: 'pair-p1', condition_id: '0xswi', max_pair_cost_at_post: 0.98,
          orders: [], tokens: [] }
    ]
};
"""


@requires_node
def test_stitch_orders_head_has_seven_columns_in_spec_order_without_queue():
    code = _FIXTURE + """
    const head = app.stitchOpenOrdersHeadHtml(null);
    const labels = [...head.matchAll(/data-ot-sort="(\\d+)"/g)].map(m => m[1]);
    console.log(JSON.stringify({ head, labels }));
    """
    res = json.loads(_eval_js(code))
    head = res["head"]
    for label in ["Market", "Side &amp; Resting Quote", "Pair Target",
                  "Committed Capital", "Risk State", "Age / Stamp", "Actions"]:
        assert label in head, f"missing header: {label}"
    assert "Queue" not in head
    # Left-to-right hierarchy: Market first, Actions last and unsortable.
    positions = ["Market", "Resting Quote", "Pair Target", "Committed Capital",
                 "Risk State", "Age / Stamp", "Actions"]
    assert [head.index(p) for p in positions] == sorted(head.index(p) for p in positions)
    assert 'data-ot-sort' not in head.split("Actions")[1]


@requires_node
def test_stitch_orders_unpaired_row_shows_side_target_capital_and_risk():
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, state, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    # Market anchor comes before every metric of its own row.
    row = html.split('data-pair="pair-u1"', 1)[1]
    assert "Alcaraz vs Sinner" in row
    assert row.index("Alcaraz vs Sinner") < row.index("BUY YES")
    # Side & resting quote: green BUY YES leg with size, price and line cost...
    assert "BUY YES" in html
    assert "10 @ $0.250" in html
    assert "($2.50)" in html
    # ...and the muted counter-leg beneath it.
    assert "[Awaiting NO Quote]" in html
    # Pair target off the default 0.99 cap: 0.99 - 0.25 = 0.74 on the missing DN leg.
    assert "≤ $0.740" in html
    assert "cap $0.990" in html
    # Committed capital in amber, risk badge beside it.
    assert "$2.50" in html
    assert "UNPAIRED" in html
    assert "Missing Leg" in html
    # Age right of the metrics, actions flush right with hook attributes.
    assert "00:45" in html
    assert 'data-act="force-match"' in html
    assert 'data-act="cancel"' in html
    # Queue Ahead is gone from the rows too.
    assert "Queue" not in html


@requires_node
def test_stitch_orders_paired_row_locks_target_against_posted_cap():
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, state, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    # 0.42 + 0.53 = 0.95 locked against the 0.98 cap posted with the pair.
    assert "Locked $0.950" in html
    assert "cap $0.980" in html
    assert "PAIRED" in html
    # $4.20 + $5.30 committed on the paired row.
    assert "$9.50" in html


@requires_node
def test_stitch_orders_empty_state_spans_all_seven_columns():
    code = """
    const html = app.stitchOpenOrdersRows({ by_market: {} }, { orders: [] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    assert 'colspan="7"' in res["html"]
    assert "No orders are resting on the book." in res["html"]


@requires_node
def test_stitch_orders_risk_sorts_riskiest_first():
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, state, { col: 6, dir: 'desc' });
    const first = html.indexOf('data-pair="pair-u1"') < html.indexOf('data-pair="pair-p1"');
    console.log(JSON.stringify({ riskiestFirst: first }));
    """
    res = json.loads(_eval_js(code))
    assert res["riskiestFirst"] is True
