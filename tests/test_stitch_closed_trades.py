"""Targeted tests for Stitch Terminal Closed Trades table layout and YES/NO pills."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_APP_JS = _ROOT / "dashboard" / "static" / "app.js"
_PROTOTYPE_CSS = _ROOT / "dashboard" / "static" / "prototype.css"

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
    const kpi = {
        by_market: {
            '0x123': {
                condition_id: '0x123',
                title: 'Fed Rate Cut in June',
                category: 'Finance',
                settlements: settlements,
                realized_pnl: 0.70,
                total_cost: 9.30,
                total_sh: 10
            }
        }
    };
    const parentTableHtml = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    console.log(JSON.stringify({
        html,
        parentTableHtml,
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
    # EXEC TYPE replaces the old STATUS badge: same outcome, own column, and it
    # is a pill now (#831). "Merge" alone also matches the old "Pair Merge", so
    # the old wording is asserted gone rather than merely shadowed.
    assert 'stitch-exec-type is-merge">Merge</span>' in res["html"]
    assert "Pair Merge" not in res["html"]
    assert "stitch-exec-type" in res["html"]
    assert "Fed Rate Cut in June" in res["parentTableHtml"]
    assert "+$0.70" in res["parentTableHtml"]
    # The outcome column counts money now, not the method that closed it (#831).
    assert "1 Win" in res["parentTableHtml"]
    assert "Merged" not in res["parentTableHtml"]


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
    # EXEC TYPE replaces the old STATUS badge: same outcomes, own column.
    assert "Stop Loss" in res["html"]
    assert "Grace Expired" in res["html"]


@requires_node
def test_stitch_closed_trades_total_cost_uses_settlement_basis_not_residual_dust():
    """#579: a closed market's Total Cost is the basis of what closed, never the
    residual open-inventory dust KPI publishes in the same field."""
    code = """
    const settlements = [
        {
            method: 'shadow_merge',
            shares: 7,
            up_cost_removed: 2.87,
            dn_cost_removed: 4.06,
            cost_basis: 6.93,
            proceeds: 7.00,
            realized_pnl: 0.07,
            ts: 1700000000
        },
        {
            method: 'shadow_merge',
            shares: 6,
            up_cost_removed: 2.10,
            dn_cost_removed: 3.42,
            cost_basis: 5.52,
            proceeds: 6.00,
            realized_pnl: 0.48,
            ts: 1700000010
        }
    ];
    // The real-world shape from the #569 store: both pairs fully closed, so the
    // only thing left in KPI's residual field is dust.
    const kpi = {
        by_market: {
            '0x123': {
                condition_id: '0x123',
                title: 'Feyenoord vs Como',
                category: 'Sports',
                settlements: settlements,
                realized_pnl: 0.55,
                total_cost: 0.35,
                total_sh: 0
            }
        }
    };
    const html = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    console.log(JSON.stringify({
        html,
        hasDustCost: html.includes('$0.35'),
        hasTrueCost: html.includes('$12.45'),
        hasInflatedPct: html.includes('157.14%'),
        hasTruePct: html.includes('4.42%')
    }));
    """
    res = json.loads(_eval_js(code))
    assert res["hasTrueCost"] is True, "closed row must show the settlement basis total"
    assert res["hasTruePct"] is True, "PnL pct must divide by the settlement basis total"
    assert res["hasDustCost"] is False, "residual dust must not render as Total Cost"
    assert res["hasInflatedPct"] is False, "dust denominator must not inflate the pct"


@requires_node
def test_stitch_closed_trades_total_cost_sort_uses_settlement_basis():
    """#579 follow-up: the Total Cost column sort ranks by the settlement basis,
    not the residual dust — a $12.45-basis market must outrank a $5.00 one even
    when it holds only $0.35 of residual."""
    code = """
    const big = {
        condition_id: '0xbig', title: 'Big Basis Market', category: 'Sports',
        settlements: [{ method: 'shadow_merge', shares: 7, cost_basis: 12.45,
            proceeds: 13.00, realized_pnl: 0.55, ts: 1700000000 }],
        realized_pnl: 0.55, total_cost: 0.35, total_sh: 0
    };
    const small = {
        condition_id: '0xsmall', title: 'Small Basis Market', category: 'Sports',
        settlements: [{ method: 'shadow_merge', shares: 5, cost_basis: 5.00,
            proceeds: 5.20, realized_pnl: 0.20, ts: 1700000000 }],
        realized_pnl: 0.20, total_cost: 0.50, total_sh: 0
    };
    const kpi = { by_market: { '0xbig': big, '0xsmall': small } };
    const desc = app.stitchClosedTradesRows(kpi, { orders: [] }, { col: 3, dir: 'desc' });
    const asc = app.stitchClosedTradesRows(kpi, { orders: [] }, { col: 3, dir: 'asc' });
    console.log(JSON.stringify({
        descFirst: desc.indexOf('0xbig') < desc.indexOf('0xsmall'),
        ascFirst: asc.indexOf('0xsmall') < asc.indexOf('0xbig')
    }));
    """
    res = json.loads(_eval_js(code))
    assert res["descFirst"] is True, "desc sort must put the larger basis first"
    assert res["ascFirst"] is True, "asc sort must put the smaller basis first"


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
    # Positions uses the same shared dual-leg HELD INVENTORY cell as Orders
    # (single template); the split YES HELD / NO HELD columns are gone.
    assert "HELD INVENTORY" in res["posHead"]
    assert "LAST UPDATED" in res["posHead"]
    assert "YES PRICE" in res["mktsHead"]
    assert "NO PRICE" in res["mktsHead"]


@requires_node
def test_stitch_closed_trades_empty_and_ledger_row_span_all_seven_columns():
    code = """
    const kpi = { by_market: { '0x123': {
        condition_id: '0x123', title: 'M', category: 'C',
        settlements: [{ method: 'merge', shares: 5, cost_basis: 5.0,
            proceeds: 5.5, realized_pnl: 0.5, ts: 1700000000 }],
        realized_pnl: 0.5, total_cost: 0, total_sh: 0 } } };
    const empty = app.stitchClosedTradesRows({ by_market: {} }, { orders: [] }, null);
    app.expandedMarkets.add('0x123');
    const full = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    app.expandedMarkets.delete('0x123');
    console.log(JSON.stringify({ empty, full }));
    """
    res = json.loads(_eval_js(code))
    assert 'colspan="7"' in res["empty"]
    assert "No closed trades yet" in res["empty"]
    expand = res["full"].split("stitch-expand-row")[1].split("</td>")[0]
    assert 'colspan="7"' in expand, "the ledger detail row must span the new column count"

# --- #831: the outcome column counts money, and the type is a pill ----------

@requires_node
def test_stitch_closed_trades_outcome_counts_money_not_method():
    """#831: 2 profitable merges + 4 losing sales used to read "2 Merged" and
    say nothing about the four losses. The column counts the money now."""
    code = """
    const settlements = [
        { method: 'merge', shares: 10, cost_basis: 9.30, proceeds: 10.00, realized_pnl: 0.70, ts: 1700000000 },
        { method: 'merge', shares: 10, cost_basis: 9.40, proceeds: 10.00, realized_pnl: 0.60, ts: 1700000010 },
        { method: 'sell', shares: 5, cost_basis: 2.50, proceeds: 1.00, realized_pnl: -1.50, ts: 1700000020 },
        { method: 'sell', shares: 5, cost_basis: 2.50, proceeds: 1.00, realized_pnl: -1.50, ts: 1700000030 },
        { method: 'sell', shares: 5, cost_basis: 2.50, proceeds: 1.00, realized_pnl: -1.50, ts: 1700000040 },
        { method: 'sell', shares: 5, cost_basis: 2.50, proceeds: 1.00, realized_pnl: -1.50, ts: 1700000050 }
    ];
    const kpi = { by_market: { '0xmix': {
        condition_id: '0xmix', title: 'Mixed Market', category: 'Sports',
        settlements, realized_pnl: -5.40, total_cost: 0, total_sh: 0 } } };
    const html = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    console.log(JSON.stringify({ html }));
    """
    html = json.loads(_eval_js(code))["html"]
    assert "stitch-pill-count win" in html
    assert "\u25cf 2 Wins" in html, "two profitable merges are two wins"
    assert "stitch-pill-count loss" in html
    assert "\u25cf 4 Losses" in html, "four losing sales are four losses"
    # The method vocabulary is gone from this column: it counted mechanics.
    assert "Merged" not in html
    assert "Stop<" not in html
    assert "Expired<" not in html


@requires_node
def test_stitch_closed_trades_flat_execution_counts_as_neither():
    """An explicit realized_pnl of 0 is a booked zero: it must stay zero (never
    re-derived from prices) and must not tip either count."""
    code = """
    const settlements = [
        { method: 'merge', shares: 10, cost_basis: 9.30, proceeds: 10.00, realized_pnl: 0.70, ts: 1700000000 },
        { method: 'merge', shares: 10, cost_basis: 9.00, proceeds: 10.00, realized_pnl: 0, ts: 1700000010 }
    ];
    const kpi = { by_market: { '0xflat': {
        condition_id: '0xflat', title: 'Flat Market', category: 'Sports',
        settlements, realized_pnl: 0.70, total_cost: 0, total_sh: 0 } } };
    // The ledger rows only exist while the market is open, so open it.
    app.expandedMarkets.add('0xflat');
    const html = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    app.expandedMarkets.delete('0xflat');
    console.log(JSON.stringify({ html }));
    """
    html = json.loads(_eval_js(code))["html"]
    assert "\u25cf 1 Win" in html
    assert "1 Wins" not in html, "one win is singular"
    assert "stitch-pill-count loss" not in html, "a booked zero is not a loss"
    assert "stitch-pill-count neutral" not in html, "one win means the column is not empty"
    # Explicit zero survives: proceeds - cost_basis would have said +$1.00.
    assert "+$1.00" not in html
    assert "+$0.00" in html


@requires_node
def test_stitch_closed_trades_missing_realized_pnl_falls_back_to_proceeds_minus_cost():
    """An execution that never recorded realized_pnl is measured as proceeds
    minus cost basis — the same arithmetic its own row prints."""
    code = """
    const settlements = [
        { method: 'sell', shares: 5, cost_basis: 2.50, proceeds: 1.00, ts: 1700000000 },
        { method: 'sell', shares: 5, cost_basis: 2.50, proceeds: 5.00, realized_pnl: null, ts: 1700000010 }
    ];
    const kpi = { by_market: { '0xnull': {
        condition_id: '0xnull', title: 'Null Market', category: 'Sports',
        settlements, realized_pnl: 1.00, total_cost: 0, total_sh: 0 } } };
    const html = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    console.log(JSON.stringify({ html }));
    """
    html = json.loads(_eval_js(code))["html"]
    assert "\u25cf 1 Win" in html, "proceeds 5.00 over a 2.50 basis is a win"
    assert "\u25cf 1 Loss" in html, "proceeds 1.00 under a 2.50 basis is a loss"


@requires_node
def test_stitch_closed_trades_every_exec_type_is_a_labelled_pill():
    """One pill per row, one label per close state the code can write: Merge /
    Stop Loss / Grace Expired / Resolved / Single Buy Exit / Venue Sync. A close
    the dashboard cannot name reads Unclassified — there is no catch-all "Sale"
    left to hide a drifted method behind."""
    code = """
    const settlements = [
        { method: 'merge', shares: 5, cost_basis: 5.0, proceeds: 5.5, realized_pnl: 0.5, ts: 1700000000 },
        { method: 'shadow_merge', shares: 5, cost_basis: 5.0, proceeds: 5.5, realized_pnl: 0.5, ts: 1700000001 },
        { method: 'single_buy_exit', shares: 5, cost_basis: 5.0, proceeds: 3.0, realized_pnl: -2.0, ts: 1700000002 },
        { method: 'single_buy_exit', reason: 'stranded_unhedged_exit', shares: 5, cost_basis: 5.0, proceeds: 3.0, realized_pnl: -2.0, ts: 1700000003 },
        { method: 'stop_loss_exit', reason: 'lifecycle_hard_stop', shares: 5, cost_basis: 5.0, proceeds: 3.0, realized_pnl: -2.0, ts: 1700000004 },
        { method: 'single_buy_exit', reason: 'pair_cap_hedge_unreachable', shares: 5, cost_basis: 5.0, proceeds: 3.0, realized_pnl: -2.0, ts: 1700000005 },
        { method: 'aged_out_exit', reason: 'grace_expired', shares: 5, cost_basis: 5.0, proceeds: 3.0, realized_pnl: -2.0, ts: 1700000006 },
        { method: 'settlement', shares: 5, cost_basis: 5.0, proceeds: 0.0, realized_pnl: -5.0, ts: 1700000007 },
        { method: 'shadow_settlement', shares: 5, cost_basis: 5.0, proceeds: 0.0, realized_pnl: -5.0, ts: 1700000008 },
        { method: 'venue_sync', shares: 5, cost_basis: 5.0, proceeds: 5.0, realized_pnl: 0.0, ts: 1700000009 },
        { method: 'sell', shares: 5, cost_basis: 5.0, proceeds: 3.0, realized_pnl: -2.0, ts: 1700000010 }
    ];
    const html = app.renderStitchClosedLedgerRows('0x123', {}, settlements, [], Date.now());
    console.log(JSON.stringify({ html }));
    """
    html = json.loads(_eval_js(code))["html"]
    for modifier, label in (("is-merge", "Merge"),
                            ("is-stop-loss", "Stop Loss"),
                            ("is-grace-expired", "Grace Expired"),
                            ("is-resolved", "Resolved"),
                            ("is-single-buy-exit", "Single Buy Exit"),
                            ("is-venue-sync", "Venue Sync"),
                            ("is-unclassified", "Unclassified")):
        assert f'stitch-exec-type {modifier}">{label}</span>' in html, label
    assert ">Sale<" not in html, "no close is a vague sale any more"
    assert "Pair Merge" not in html
    assert ">Settlement<" not in html, "a resolved market reads Resolved"
    # The hard-stop family shares one label: the cap bail-out is a stop too.
    assert html.count('is-stop-loss">Stop Loss</span>') == 2
    # The rescue method wins over its route reason: a stranded leg is the
    # saver's lone-leg exit, not the Global Stop Loss.
    assert html.count('is-single-buy-exit">Single Buy Exit</span>') == 2


@requires_node
def test_stitch_closed_trades_order_fallback_names_the_order_state():
    """A market with orders but no settlements still gets pills, and each pill
    is the order's own state — merged pairs read Merge, a filled pair Filled, a
    cancelled order Cancelled. No row guesses "Sale" from a status it can read."""
    code = """
    const orders = [
        { pair_id: 'p1', condition_id: '0x1', token_side: 'UP', price: 0.50, size_matched: 5, original_size: 5, status: 'merged', age_sec: 60 },
        { pair_id: 'p1', condition_id: '0x1', token_side: 'DOWN', price: 0.48, size_matched: 5, original_size: 5, status: 'merged', age_sec: 60 },
        { pair_id: 'p2', condition_id: '0x1', token_side: 'UP', price: 0.50, size_matched: 5, original_size: 5, status: 'filled', age_sec: 60 },
        { pair_id: 'p2', condition_id: '0x1', token_side: 'DOWN', price: 0.48, size_matched: 5, original_size: 5, status: 'filled', age_sec: 60 },
        { pair_id: 'p3', condition_id: '0x1', token_side: 'UP', price: 0.50, size_matched: 5, original_size: 5, status: 'merged', age_sec: 60 },
        { pair_id: 'p4', condition_id: '0x1', token_side: 'DOWN', price: 0.48, size_matched: 5, original_size: 5, status: 'filled', age_sec: 60 },
        { pair_id: 'p5', condition_id: '0x1', token_side: 'UP', price: 0.50, size_matched: 0, original_size: 5, status: 'cancelled', age_sec: 60 }
    ];
    const html = app.renderStitchClosedLedgerRows('0x1', {}, [], orders, Date.now());
    console.log(JSON.stringify({ html }));
    """
    html = json.loads(_eval_js(code))["html"]
    assert html.count('<span class="stitch-exec-type is-merge">Merge</span>') == 2
    assert html.count('<span class="stitch-exec-type is-order-filled">Filled</span>') == 2
    assert html.count('<span class="stitch-exec-type is-order-cancelled">Cancelled</span>') == 1
    assert ">Sale<" not in html
    assert "Pair Merge" not in html


def test_every_exec_type_label_owns_a_distinct_css_tone():
    """Colour is how a ledger row's type is read down the column: every label
    in EXEC_TYPE_CLASSES has a `is-*` class, that class has its own rule in
    prototype.css, and no two types share the same text colour."""
    app_js = _APP_JS.read_text(encoding="utf-8-sig")
    labels = dict(re.findall(r"'([A-Za-z][A-Za-z ]+)': '(is-[a-z-]+)',", app_js))
    assert labels, "EXEC_TYPE_CLASSES was not found in app.js"

    css = _PROTOTYPE_CSS.read_text(encoding="utf-8-sig")
    tones: dict[str, str] = {}
    for label, modifier in labels.items():
        rule = re.search(
            r"\.stitch-exec-type\." + re.escape(modifier) + r"\s*\{([^}]*)\}", css)
        assert rule, f"{label}: no CSS rule for .stitch-exec-type.{modifier}"
        colour = re.search(r"color:\s*(#[0-9a-f]{6})", rule.group(1))
        assert colour, f"{label}: {modifier} declares no text colour"
        tones[label] = colour.group(1)

    duplicates = {c: [l for l, v in tones.items() if v == c]
                  for c in set(tones.values()) if list(tones.values()).count(c) > 1}
    assert not duplicates, f"two exec types share a tone: {duplicates}"


@requires_node
def test_stitch_closed_trades_expanded_row_drops_the_duplicate_summary_strip():
    """The strip repeated the parent row's Total Cost and Net Realized inside
    the ledger. The parent keeps both; the duplicate is gone."""
    code = """
    const settlements = [
        { method: 'merge', shares: 10, cost_basis: 9.30, proceeds: 10.00, realized_pnl: 0.70, ts: 1700000000 }
    ];
    const kpi = { by_market: { '0xdup': {
        condition_id: '0xdup', title: 'Dup Market', category: 'Sports',
        settlements, realized_pnl: 0.55, total_cost: 0.35, total_sh: 0 } } };
    app.expandedMarkets.add('0xdup');
    const html = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    app.expandedMarkets.delete('0xdup');
    const parts = html.split('class="stitch-expand-row"');
    console.log(JSON.stringify({ html, parent: parts[0], ledger: parts[1] }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "stitch-closed-ledger-strip" not in html
    assert "Net Realized" not in html
    assert "Orphan" not in html
    assert "Total Cost:" not in html
    # The parent row still carries the settlement-summed basis and the market's
    # OWN realized PnL — 0.55, not the 0.70 the settlements would re-sum to.
    assert "$9.30" in res["parent"]
    assert "+$0.55" in res["parent"]
    assert "+$0.70" not in res["parent"]
    assert "stitch-exec-type is-merge" in res["ledger"]
