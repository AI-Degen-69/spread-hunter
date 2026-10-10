"""Targeted tests for Stitch Terminal Closed Trades table layout and YES/NO pills."""
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


@requires_node
def test_stitch_closed_trades_pair_renders_single_row_with_both_legs_and_single_pnl():
    code = """
    const settlements = [{
        method: 'merge',
        shares: 10,
        up_cost_removed: 4.70,
        dn_cost_removed: 4.60,
        cost_basis: 9.30,
        proceeds: 10.00,
        realized_pnl: 0.70,
        ts: 1700000000
    }];
    const html = app.renderStitchClosedLedgerRows('0x123', {}, settlements, [], Date.now());
    console.log(JSON.stringify({
        html,
        hasYes: html.includes('<span class="stitch-leg-pill yes">YES</span>'),
        hasNo: html.includes('<span class="stitch-leg-pill no">NO</span>'),
        rowCount: (html.match(/<tr /g) || []).length,
        pnlCount: (html.match(/class="stitch-pnl-cell"/g) || []).length
    }));
    """
    res = json.loads(_eval_js(code))
    assert res["hasYes"] is True
    assert res["hasNo"] is True
    assert res["rowCount"] == 1
    assert res["pnlCount"] == 1
    assert "+$0.70" in res["html"]
    assert "YES" in res["html"]
    assert "NO" in res["html"]
    assert "MERGED" in res["html"]


@requires_node
def test_stitch_closed_trades_solos_render_each_in_own_row_with_own_pnl():
    code = """
    const settlements = [
        {
            method: 'stop_loss_exit',
            reason: 'lifecycle_hard_stop',
            shares: 5,
            up_cost_removed: 2.35,
            dn_cost_removed: 0,
            cost_basis: 2.35,
            proceeds: 1.00,
            realized_pnl: -1.35,
            ts: 1700000000
        },
        {
            method: 'aged_out_exit',
            reason: 'grace_expired',
            shares: 5,
            up_cost_removed: 0,
            dn_cost_removed: 2.30,
            cost_basis: 2.30,
            proceeds: 0.50,
            realized_pnl: -1.80,
            ts: 1700000010
        }
    ];
    const html = app.renderStitchClosedLedgerRows('0x123', {}, settlements, [], Date.now());
    console.log(JSON.stringify({
        html,
        hasYes: html.includes('<span class="stitch-leg-pill yes">YES</span>'),
        hasNo: html.includes('<span class="stitch-leg-pill no">NO</span>'),
        rowCount: (html.match(/<tr /g) || []).length,
        pnlCount: (html.match(/class="stitch-pnl-cell"/g) || []).length
    }));
    """
    res = json.loads(_eval_js(code))
    assert res["hasYes"] is True
    assert res["hasNo"] is True
    # Two separate rows because each is a solo!
    assert res["rowCount"] == 2
    # Two separate PnL cells!
    assert res["pnlCount"] == 2
    assert "STOP LOSS" in res["html"]
    assert "GRACE EXPIRED" in res["html"]


@requires_node
def test_stitch_open_orders_and_positions_headers_and_pills():
    code = """
    const ordersHead = app.stitchOpenOrdersHeadHtml(null);
    const posHead = app.stitchPositionsHeadHtml(null);
    const mktsHead = app.stitchActiveMarketsHeadHtml(null);
    console.log(JSON.stringify({
        ordersHead,
        posHead,
        mktsHead
    }));
    """
    res = json.loads(_eval_js(code))
    assert "Dual-Leg Quotes (YES / NO)" in res["ordersHead"]
    assert "YES HELD" in res["posHead"]
    assert "NO HELD" in res["posHead"]
    assert "YES QUOTE" in res["mktsHead"]
    assert "NO QUOTE" in res["mktsHead"]
