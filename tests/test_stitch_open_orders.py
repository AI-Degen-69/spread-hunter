"""Tab 2 (Orders): the stitch terminal's resting-order table.

Six columns, left to right: Market & Arbitrage Status | CATEGORY | Dual-Leg
Quotes (YES / NO) | Total Committed | LAST UPDATED | Actions. An absent leg
renders its counterpart fate: a filled counterpart holds a position (green),
a cancelled one is requoting, a genuinely missing one awaits a quote.
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
def test_stitch_orders_head_has_eight_columns_in_spec_order():
    code = _FIXTURE + """
    const head = app.stitchOpenOrdersHeadHtml(null);
    console.log(JSON.stringify({ head }));
    """
    res = json.loads(_eval_js(code))
    head = res["head"]
    positions = ["Market", "CATEGORY", "STATUS", "Dual-Leg Quotes (YES / NO)",
                 "PAIR COST", "Total Committed", "LAST UPDATED", "Actions"]
    for label in positions:
        assert label in head, f"missing header: {label}"
    assert [head.index(p) for p in positions] == sorted(head.index(p) for p in positions)
    # CATEGORY is a real sort button on key 8, STATUS on key 9, PAIR COST on key 10.
    assert 'data-ot-sort="8"' in head
    assert 'data-ot-sort="9"' in head
    assert 'data-ot-sort="10"' in head
    assert 'data-ot-sort' not in head.split("Actions")[1]
    assert head.count("<th") == 8


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
    assert "MISSING PAIR" in html
    # The unpaired pill carries the explanation in its hover title.
    assert 'title="No Position"' in html
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
    assert "SINGLE FILL" in html
    assert "Position Held" in html
    assert "5 @ $0.70" in html
    assert "is-hedge-pending" in html
    # Hedge-pending: one side filled, the other resting — the commit cell
    # shows the break-even max for the other side (cap minus resting price).
    # UP rests at $0.25 against the default $0.99 cap: max $0.74, green.
    commit_sub = html.split('stitch-commit-sub"')[1].split("</span>")[0]
    assert "0.74" in commit_sub
    assert "color:#4ade80" in commit_sub


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
    assert "SINGLE FILL" not in html
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
    assert "SINGLE FILL" in html
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
def test_stitch_orders_empty_state_spans_all_eight_columns():
    code = """
    const html = app.stitchOpenOrdersRows({ by_market: {} }, { orders: [] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    assert 'colspan="8"' in res["html"]
    assert "No orders are resting on the book." in res["html"]


@requires_node
def test_stitch_orders_category_key_groups_by_category_and_defaults_ascending():
    code = _FIXTURE + """
    const cats = {
        '0xalp': { ...kpi.by_market['0xalp'], category: 'Tennis' },
        '0xswi': { ...kpi.by_market['0xswi'], category: 'Golf' },
    };
    const catKpi = { by_market: cats };
    const html = app.stitchOpenOrdersRows(
        catKpi, { orders: [restingUP, pairedUP, pairedDN] }, { col: 8, dir: 'asc' });
    const golfFirst = html.indexOf('data-pair="pair-p1"') < html.indexOf('data-pair="pair-u1"');
    console.log(JSON.stringify({
        golfFirst, defaultDir: app.otDefaultDir('open-orders', 8)
    }));
    """
    res = json.loads(_eval_js(code))
    assert res["golfFirst"] is True, "key 8 must group rows by category"
    assert res["defaultDir"] == "asc", "category is a word column: first click sorts A-Z"


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


@requires_node
def test_stitch_orders_unpaired_row_shows_break_even_max_for_missing_side():
    # UP rests at $0.25 against the default $0.99 cap: the missing DN leg may
    # cost at most $0.74 before the pair stops breaking even. Green while
    # completable; the cap itself is never stated.
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, { orders: [restingUP] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "0.74" in html
    assert "color:#4ade80" in html
    assert "shares total" not in html
    assert "Unhedged Risk" not in html
    assert "0.99" not in html


@requires_node
def test_stitch_orders_threshold_follows_which_side_rests():
    # DN rests at $0.70: the missing UP leg may cost at most $0.29.
    code = _FIXTURE + """
    const restingDN = Object.assign({}, restingUP,
        { order_id: 'ord-d1', token_id: 'tok-d', price: 0.70 });
    const html = app.stitchOpenOrdersRows(kpi, { orders: [restingDN] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    assert "0.29" in res["html"]


@requires_node
def test_stitch_orders_posted_cap_overrides_the_default():
    # Pair posted its cap at $0.98: $0.98 - $0.25 = $0.73, not $0.74.
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, {
        orders: [restingUP],
        pairs: [{ pair_id: 'pair-u1', condition_id: '0xalp',
                  max_pair_cost_at_post: 0.98 }],
    }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    assert "0.73" in res["html"]


@requires_node
def test_stitch_orders_no_room_threshold_turns_red():
    # Resting at the full cap: any fill books over-cap, so the max shows red.
    code = _FIXTURE + """
    const overRester = Object.assign({}, restingUP, { price: 1.00 });
    const html = app.stitchOpenOrdersRows(kpi, { orders: [overRester] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "color:#f87171" in html


@requires_node
def test_stitch_orders_fractional_price_threshold_floors_to_cents():
    # Ladder prices can carry fractional cents: $0.99 - $0.244 = $0.746
    # must show $0.74, never the rounded-up $0.75.
    code = _FIXTURE + """
    const fracUP = Object.assign({}, restingUP, { price: 0.244 });
    const html = app.stitchOpenOrdersRows(kpi, { orders: [fracUP] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    sub = res["html"].split('stitch-commit-sub"')[1].split("</span>")[0]
    assert "0.74" in sub
    assert "0.75" not in sub


@requires_node
def test_stitch_orders_paired_row_carries_no_threshold():
    # Nothing missing and nothing filled: no break-even line, and the old
    # committed total is gone — the column shows the max only.
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(
        kpi, { orders: [pairedUP, pairedDN] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "$9.50" not in html
    row = html.split('data-pair="pair-p1"')[1]
    assert "stitch-commit-sub" not in row
    assert "color:#4ade80" not in row and "color:#f87171" not in row


@requires_node
def test_stitch_orders_room_sits_on_the_resting_leg_rung():
    # Two-rung cell mirroring Dual-Leg heights: the value shares the rung
    # of the resting (unfilled) leg, the other rung stays empty.
    # NO rests -> value on the second rung; YES rests -> first rung.
    code = _FIXTURE + """
    const restingNO = Object.assign({}, restingUP,
        { order_id: 'ord-n1', token_id: 'tok-d', price: 0.40 });
    const htmlNo = app.stitchOpenOrdersRows(kpi, { orders: [restingNO] }, null);
    const htmlYes = app.stitchOpenOrdersRows(kpi, { orders: [restingUP] }, null);
    console.log(JSON.stringify({ htmlNo, htmlYes }));
    """
    res = json.loads(_eval_js(code))
    for key, resting_leg in (("htmlNo", "NO"), ("htmlYes", "YES")):
        row = res[key].split('data-pair="pair-u1"')[1]
        rungs = row.split('stitch-quote-rung">')[1:]
        # Col 2 contributes the first two rungs, Col 3 the next two.
        room_rungs = rungs[2:4]
        assert len(room_rungs) == 2
        filled_rung = room_rungs[1] if resting_leg == "NO" else room_rungs[0]
        empty_rung = room_rungs[0] if resting_leg == "NO" else room_rungs[1]
        assert "0.59" in filled_rung if resting_leg == "NO" else "0.74" in filled_rung
        assert "stitch-commit-sub" in filled_rung
        assert "stitch-commit-sub" not in empty_rung
        assert "font-size:11.5px" in filled_rung


@requires_node
def test_stitch_orders_filled_leg_shows_room_on_resting_side():
    # Operator example: YES filled 5 @ $0.55, NO resting 5 @ $0.40 — the
    # row shows the max the other side may cost: $0.99 - $0.40 = $0.59.
    # Mirrored (NO filled @ $0.40, YES resting @ $0.55) it shows $0.44.
    code = _FIXTURE + """
    const filledYES = Object.assign({}, filledDN,
        { order_id: 'ord-y1', token_id: 'tok-u', price: 0.55 });
    const restingNO = Object.assign({}, restingUP,
        { order_id: 'ord-n1', token_id: 'tok-d', price: 0.40 });
    const html1 = app.stitchOpenOrdersRows(
        kpi, { orders: [filledYES, restingNO] }, null);
    const restingYES = Object.assign({}, restingUP, { price: 0.55 });
    const filledNO = Object.assign({}, filledDN, { price: 0.40 });
    const html2 = app.stitchOpenOrdersRows(
        kpi, { orders: [restingYES, filledNO] }, null);
    console.log(JSON.stringify({ html1, html2 }));
    """
    res = json.loads(_eval_js(code))
    sub1 = res["html1"].split('stitch-commit-sub"')[1].split("</span>")[0]
    sub2 = res["html2"].split('stitch-commit-sub"')[1].split("</span>")[0]
    assert "0.59" in sub1
    assert "0.44" in sub2


@requires_node
def test_stitch_orders_paired_row_shows_pair_cost_cell():
    # Paired row (0.42 + 0.53) renders $0.95 in the PAIR COST cell.
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(
        kpi, { orders: [pairedUP, pairedDN] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "$0.95" in html
    assert "PAIRED" in html
    assert "is-paired" in html
    # The pill is word-only: no Pair Cost / % Edge text inside it.
    assert "Pair Cost:" not in html
    assert "% Edge" not in html


@requires_node
def test_stitch_orders_unpaired_row_shows_pair_cost_dash():
    # A lone resting leg has no cost: PAIR COST shows --.
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(kpi, { orders: [restingUP] }, null);
    console.log(JSON.stringify({ html }));
    """
    res = json.loads(_eval_js(code))
    html = res["html"]
    assert "MISSING PAIR" in html
    assert "$0.25" in html  # the resting leg cost still renders
    assert "--" in html


@requires_node
def test_stitch_orders_status_sorts_alphabetically():
    # STATUS (key 9) is a text column: ascending puts MISSING PAIR first.
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(
        kpi, { orders: [restingUP, pairedUP, pairedDN] }, { col: 9, dir: 'asc' });
    const missingFirst = html.indexOf('data-pair="pair-u1"') < html.indexOf('data-pair="pair-p1"');
    console.log(JSON.stringify({ missingFirst, defaultDir: app.otDefaultDir('open-orders', 9) }));
    """
    res = json.loads(_eval_js(code))
    assert res["missingFirst"] is True, "key 9 must sort MISSING PAIR before PAIRED"
    assert res["defaultDir"] == "asc", "status is a word column: first click sorts A-Z"


@requires_node
def test_stitch_orders_pair_cost_sorts_numerically():
    # PAIR COST (key 10) is a money column: descending puts the larger pair first.
    code = _FIXTURE + """
    const html = app.stitchOpenOrdersRows(
        kpi, { orders: [restingUP, pairedUP, pairedDN] }, { col: 10, dir: 'desc' });
    const pairedFirst = html.indexOf('data-pair="pair-p1"') < html.indexOf('data-pair="pair-u1"');
    console.log(JSON.stringify({ pairedFirst, defaultDir: app.otDefaultDir('open-orders', 10) }));
    """
    res = json.loads(_eval_js(code))
    assert res["pairedFirst"] is True, "key 10 must sort the costed pair first"
    assert res["defaultDir"] == "desc", "pair cost is a money column: first click sorts high-low"
