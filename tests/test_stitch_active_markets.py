"""Tab 1 (Active Markets): the stitch terminal's quoted-market table.

Seven columns, left to right: MARKET (title only) | CATEGORY | YES PRICE |
NO PRICE | 24H VOLUME (volume only) | STATUS | LAST UPDATED. The EDGE column
is gone; the category badge lives in its own cell.
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
const nowMs = Date.now();
const kpi = {
    by_market: {
        '0xalp': {
            condition_id: '0xalp', title: 'Alcaraz vs Sinner', category: 'ATP Tour',
            days_to_resolve: 6.9, volume_24h: 152000, resolved: false,
            quotes_count: 4, up_sh: 0, dn_sh: 0, total_sh: 0,
            total_cost: 0, pair_cost: null, realized_pnl: 0,
            quotes: [
                { token_id: 'tok-u', side: 'UP', mid: 0.275, ts: nowMs - 41000,
                  price: 0.25, order_id: null, queue_ahead: null },
                { token_id: 'tok-d', side: 'DN', mid: 0.725, ts: nowMs - 41000,
                  price: 0.70, order_id: null, queue_ahead: null }
            ]
        }
    }
};
const state = { orders: [
    // A resting leg on this market: its status pill reads RESTING.
    { order_id: 'ord-u1', condition_id: '0xalp', token_id: 'tok-u',
      pair_id: 'pair-u1', side: 'BUY', price: 0.25, original_size: 10.0,
      size_matched: 0.0, size_remaining: 10.0, status: 'open',
      posted_ts: 1700000100, age_sec: 45.0 }
] };
"""


@requires_node
def test_stitch_markets_head_has_seven_columns_in_spec_order():
    code = _FIXTURE + """
    const head = app.stitchActiveMarketsHeadHtml(null);
    console.log(JSON.stringify({ head }));
    """
    res = json.loads(_eval_js(code))
    head = res["head"]
    ordered = ["MARKET", "CATEGORY", "YES PRICE", "NO PRICE",
               "24H VOLUME", "STATUS", "LAST UPDATED"]
    for label in ordered:
        assert label in head, f"missing header: {label}"
    assert [head.index(p) for p in ordered] == sorted(head.index(p) for p in ordered)
    # CATEGORY is a real sort button on the category key, not a label.
    assert 'data-ot-sort="2"' in head
    assert "EDGE" not in head
    assert head.count("<th") == 7


@requires_node
def test_stitch_markets_row_stacks_each_metric_per_spec():
    code = _FIXTURE + """
    const html = app.stitchActiveMarketsRows(kpi, state, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    # Market anchors the row; the category badge lives in its own cell now.
    row = html.split('data-cid="0xalp"')[1]
    assert "Alcaraz vs Sinner" in row
    market_cell = row.split('class="stitch-market-cell"')[1].split("</td>")[0]
    assert "stitch-category-pill" not in market_cell
    category_cell = row.split('class="stitch-category-cell"')[1].split("</td>")[0]
    assert "ATP Tour" in category_cell
    # Prices fall back to the quote mids here (no live-mark worker in the
    # harness): 0.275/0.725 render as $0.28/$0.72. No edge, no horizon.
    assert "$0.28" in row
    assert "$0.72" in row
    assert "stitch-edge" not in row
    assert "+0.0c" not in row
    # Volume stands alone in its column.
    assert "$152k" in row
    assert "6.9d" not in row
    # Status pill last for a market with a resting leg.
    assert "RESTING" in row
    assert "stitch-age-stopwatch" in row


@requires_node
def test_stitch_markets_empty_state_spans_all_seven_columns():
    code = """
    const html = app.stitchActiveMarketsRows({ by_market: {} }, { orders: [] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    assert 'colspan="7"' in res["html"]
    assert "No markets are being quoted." in res["html"]
