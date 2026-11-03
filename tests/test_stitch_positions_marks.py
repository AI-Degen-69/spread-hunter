"""Per-leg current price and unrealized P&L on the stitch positions table.

Each market row carries its own CURRENT PRICE cell (YES line first, NO below
-- the same stacked structure as the YES/NO HELD cells) and per-leg
unrealized lines, all fed by the live-marks overlay so they keep updating
while the book moves.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html import unescape
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


def _kpi(up_sh=10.0, up_cost=8.0, up_mid=0.76, dn_sh=0.0, dn_cost=0.0, dn_mid=None,
         quotes_count=0):
    quotes = []
    if up_mid is not None:
        quotes.append({"side": "UP", "price": 0.80, "mid": up_mid,
                       "token_id": "tok-up", "ts": 1700000000000})
    if dn_mid is not None:
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
                "quotes_count": quotes_count,
                "quotes": quotes,
            }
        }
    }


@requires_node
def test_positions_head_is_six_columns_in_issue_order():
    res = json.loads(_eval_js(
        "console.log(JSON.stringify({head: app.stitchPositionsHeadHtml(null)}));"))
    head = res["head"]
    labels = [unescape(x) for x in re.findall(r"<span class=\"ot-sort-label\">([^<]+)</span>", head)]
    assert labels == [
        "MARKET & CLASSIFICATION",
        "CATEGORY",
        "HELD INVENTORY",
        "CURRENT PRICE",
        "UNREALISED P&L",
        "LAST UPDATED",
    ]
    assert head.count("<th") == 6
    assert "TOTAL COST" not in head
    assert "MARK VALUE" not in head
    assert "HEDGE STATUS" not in head


@requires_node
def test_positions_head_has_sortable_category_column_after_market():
    res = json.loads(_eval_js(
        "console.log(JSON.stringify({head: app.stitchPositionsHeadHtml(null)}));"))
    head = res["head"]
    assert 'data-ot-sort="2"' in head, "positions needs a sortable CATEGORY key 2"
    market = head.index('data-ot-sort="1"')
    category = head.index(">CATEGORY</span>")
    assert market < category
    between = head[market:category]
    assert between.count("data-ot-sort") == 2
    assert between.count("<th") == 1


@requires_node
def test_single_leg_row_shows_its_own_current_price_and_pnl():
    # 10 UP @ $0.80 avg against a $0.76 mid: a loss, priced live. The NO side
    # is unheld but quoted, so both prices render.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(dn_mid=0.24))};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    assert 'data-cell="cur-price"' in html
    assert "YES $0.76" in html
    assert "NO $0.24" in html
    assert 'data-leg="YES"' in html and 'data-leg="NO"' in html
    assert 'data-leg="YES"' in html and 'data-leg="NO"' in html
    assert "Cost: $8.00" in html
    assert "sh total" not in html
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
    assert "Total" not in html
    assert "stitch-leg-pnl" not in html
    assert "stitch-pnl-total" not in html
    assert html.count("Cost: $") == 2


@requires_node
def test_live_overlay_moves_the_pair_pnl():
    # Quote mid says $0.84 (the frozen case); a fresh venue mark at $0.77
    # must win on the next render.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(up_mid=0.84, dn_mid=0.16, quotes_count=2))};
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
    assert "stitch-status-badge" not in html
    assert "quoting-breathing" not in html
    assert "resting-breathing" not in html


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
    assert "+$1.50" not in html  # no per-leg YES line
    assert "+$0.50" not in html  # no per-leg NO line
    assert "-$1.00" in html
    assert "Total" not in html


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


# ── #656: the active-trading pill on a held market ──────────────────────────


def _order(cid, status):
    return {"order_id": f"ord-{cid}-{status}", "condition_id": cid,
            "status": status}


@requires_node
def test_market_activity_pill_prefers_resting_and_stays_empty_when_idle():
    # One rule, three answers: a market nobody is working gets no markup at
    # all, a quoted market gets QUOTING, and RESTING wins when both are true.
    res = json.loads(_eval_js("""
    console.log(JSON.stringify({
        idle: app.marketActivityPill({quotes_count: 0}, []),
        quoting: app.marketActivityPill({quotes_count: 2}, []),
        resting: app.marketActivityPill({quotes_count: 2}, [{status: 'open'}])
    }));
    """))
    assert res["idle"] == ""
    assert "quoting-breathing" in res["quoting"]
    assert "resting-breathing" in res["resting"]
    assert "quoting-breathing" not in res["resting"]


@requires_node
@pytest.mark.parametrize("status", ["open", "partial", "pending", "Open"])
def test_stitch_positions_resting_order_shows_no_activity_pill(status):
    # #785: a resting order on the held market no longer adds a RESTING pill —
    # the Market cell carries title + category chip only.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi())};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(
            kpi, {{orders: [{json.dumps(_order('0xabc', status))}]}}, null)
    }}));
    """))
    html = res["html"]
    assert "resting-breathing" not in html
    assert "quoting-breathing" not in html
    assert ">RESTING</span>" not in html
    assert "stitch-status-badge" not in html


@requires_node
def test_stitch_positions_resting_and_quoting_show_no_activity_pill():
    # #785: neither RESTING nor QUOTING may appear in the Market cell.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(quotes_count=3))};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(
            kpi, {{orders: [{json.dumps(_order('0xabc', 'open'))}]}}, null)
    }}));
    """))
    html = res["html"]
    assert "resting-breathing" not in html
    assert "quoting-breathing" not in html
    assert "stitch-status-badge" not in html


@requires_node
def test_stitch_positions_quoting_only_shows_no_activity_pill():
    # #785: quote activity no longer adds a QUOTING pill to the held row.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(quotes_count=1))};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: [
            {json.dumps(_order('0xabc', 'filled'))},
            {json.dumps(_order('0xabc', 'cancelled'))}]}}, null)
    }}));
    """))
    html = res["html"]
    assert "quoting-breathing" not in html
    assert "resting-breathing" not in html
    assert ">QUOTING</span>" not in html
    assert "stitch-status-badge" not in html


@requires_node
def test_stitch_positions_idle_market_keeps_standard_tags():
    # No quotes and no resting orders: the row keeps the tags it already had,
    # and no new grey IDLE pill appears.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi())};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    assert "resting-breathing" not in html
    assert "quoting-breathing" not in html
    assert "pill stopped" not in html
    assert "stitch-status-badge" not in html


@requires_node
def test_stitch_positions_other_market_orders_do_not_mark_held_market():
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi())};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(
            kpi, {{orders: [{json.dumps(_order('0xdef', 'open'))}]}}, null)
    }}));
    """))
    html = res["html"]
    assert "resting-breathing" not in html
    assert "quoting-breathing" not in html


@requires_node
def test_stitch_positions_finished_market_has_no_activity_pill():
    # A settled market leaves the held table entirely, so an order still open
    # on it cannot put a pill on a row that is not rendered.
    kpi = _kpi(quotes_count=4)
    kpi["by_market"]["0xabc"]["resolved"] = True
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(kpi)};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(
            kpi, {{orders: [{json.dumps(_order('0xabc', 'open'))}]}}, null)
    }}));
    """))
    html = res["html"]
    assert "No legs have filled" in html
    assert "resting-breathing" not in html
    assert "quoting-breathing" not in html

@requires_node
def test_stitch_positions_sorted_render_has_no_activity_pill():
    # #785: sorting must not reintroduce the removed activity pill; the row
    # tint carries the lone-leg hedge signal.
    kpi = _two_market_kpi(0.40, 0.80)
    kpi["by_market"]["0xabc"]["quotes_count"] = 2
    kpi["by_market"]["0xdef"]["quotes_count"] = 1
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(kpi)};
    const html = app.stitchPositionsRows(
        kpi, {{orders: [{json.dumps(_order('0xdef', 'open'))}]}}, {{col: 9, dir: 'desc'}});
    console.log(JSON.stringify({{html}}));
    """))
    html = res["html"]

    # Still sorted by CURRENT PRICE descending: 0xdef at $0.80 first.
    assert html.index("0xdef") < html.index("0xabc")
    by_cid = {cid: body for cid, body in re.findall(
        r'<tr[^>]*data-cid="([^"]+)"[^>]*>(.*?)</tr>', html, re.S)}
    assert len(by_cid) == 2
    assert "resting-breathing" not in html
    assert "quoting-breathing" not in html
    for body in by_cid.values():
        assert "stitch-status-badge" not in body


# ── hedge-aware held inventory: a missing leg with a resting order ──────────


def _lakers_kpi():
    # Lakers shape: 5 NO @ $0.45 held, nothing held on YES, both tokens named.
    kpi = _kpi(up_sh=0.0, up_cost=0.0, up_mid=None, dn_sh=5.0, dn_cost=2.25, dn_mid=0.51)
    m = kpi["by_market"]["0xabc"]
    m["title"] = "Lakers vs Clippers"
    m["quotes"].append({"side": "UP", "price": 0.46, "mid": 0.52,
                          "token_id": "tok-up", "ts": 1700000000000})
    return kpi


def _resting_up(price=0.46, size=5.0, status="open", remaining=None):
    order = {"id": "loc-up", "order_id": "v-up", "condition_id": "0xabc",
             "token_id": "tok-up", "side": "BUY", "price": price,
             "original_size": size, "status": status,
             "posted_ts": 1700000000000, "age_sec": 300}
    if remaining is not None:
        order["size_remaining"] = remaining
    return order


@requires_node
def test_held_no_with_resting_yes_shows_resting_rung_not_no_held():
    # The Lakers case: 5 NO held + 5 YES resting @ $0.46. The YES rung names
    # the hedge instead of "No YES held", and the row carries the
    # hedge-pending tint rather than a badge.
    # original 8 vs remaining 5: the rung must read the remainder. If the
    # renderer used original_size, this would print "8 resting" and fail.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_lakers_kpi())};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(
            kpi, {{orders: [{json.dumps(_resting_up(size=8.0, remaining=5.0))}]}}, null)
    }}));
    """))
    html = res["html"]
    assert "5 resting @ $0.46" in html
    assert "8 resting" not in html
    assert "hedge working" not in html
    assert "No YES held" not in html
    assert "SINGLE FILL" not in html
    assert "UNPAIRED" not in html
    assert "stitch-status-badge" not in html
    assert "is-hedge-pending" in html
    assert "stitch-positions-held" in html
    assert "Cost: $2.25" in html
    assert "sh total" not in html
    assert 'data-leg="YES"' in html and 'data-leg="NO"' in html


@requires_node
def test_held_no_without_resting_yes_stays_unpaired():
    # The Kansas case: a lone held leg with no hedge resting keeps the honest
    # plain "No YES held" rung and the unpaired row tint — still no badge.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_lakers_kpi())};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    assert "No YES held" in html
    assert "hedge working" not in html
    assert "stitch-status-badge" not in html
    assert "UNPAIRED" not in html
    assert "SINGLE FILL" not in html
    assert "is-unpaired" in html
    assert "is-hedge-pending" not in html


@requires_node
@pytest.mark.parametrize("removed_key", [4, 5, 6, 8])
def test_positions_removed_sort_keys_fall_back_to_default(removed_key):
    # A leftover sort key for a removed column must not reach the rows, the
    # header, or aria-sort; the default order is total cost desc, then latest fill.
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_two_market_kpi(0.40, 0.80))};
    const html = app.stitchPositionsRows(kpi, {{orders: []}}, {{col: {removed_key}, dir: 'desc'}});
    const head = app.stitchPositionsHeadHtml({{col: {removed_key}, dir: 'desc'}});
    console.log(JSON.stringify({{html, head}}));
    """))
    assert "aria-sort" not in res["head"]
    # Default order: 0xabc (100 sh, $40 cost) outranks 0xdef (10 sh, $8 cost).
    assert res["html"].index("0xabc") < res["html"].index("0xdef")


@requires_node
def test_positions_unquoted_side_prints_dash_without_dot():
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(up_sh=10.0, up_cost=8.0, up_mid=0.76, dn_mid=None))};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    no_line = html[html.index('data-leg="NO"'):html.index("</div>", html.index('data-leg="NO"')) + 1]
    assert "NO --" in no_line
    assert "stitch-cur-live" not in no_line


@requires_node
def test_positions_zero_cost_basis_omits_percentage():
    res = json.loads(_eval_js(f"""
    const kpi = {{ by_market: {{ "0xabc": {{
        condition_id: "0xabc", title: "Free shares", slug: "free-shares",
        category: "E-Sports", up_sh: 10.0, up_cost: 0.0, dn_sh: 0.0, dn_cost: 0.0,
        total_cost: 0.0, total_sh: 10.0, realized_pnl: 0.0, quotes_count: 1,
        quotes: [{{side: "UP", price: 0.5, mid: 0.76, token_id: "tok-up", ts: 1700000000000}}]
    }} }} }};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))
    html = res["html"]
    assert "+$7.60" in html
    assert "%" not in html.split('data-cell="unrealized"')[1].split("</td>")[0]


@requires_node
def test_positions_flat_and_positive_pnl_classes():
    flat = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(up_sh=10.0, up_cost=8.0, up_mid=0.80, dn_mid=0.20))};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))["html"]
    assert 'class="stitch-pnl-val flat"' in flat or "stitch-pnl-val flat" in flat
    positive = json.loads(_eval_js(f"""
    const kpi = {json.dumps(_kpi(up_sh=10.0, up_cost=8.0, up_mid=0.90, dn_mid=0.10))};
    console.log(JSON.stringify({{
        html: app.stitchPositionsRows(kpi, {{orders: []}}, null)
    }}));
    """))["html"]
    assert "pos" in positive.split('data-cell="unrealized"')[1].split("</td>")[0]


@requires_node
def test_positions_unrealized_pnl_sorts_by_key_7():
    # A fresh mark moves 0xabc's P&L above 0xdef's: key 7 must reorder the rows.
    base_a = _kpi(up_sh=10.0, up_cost=8.0, up_mid=0.50)["by_market"]["0xabc"]
    base_b = {**base_a, "condition_id": "0xdef", "title": "Other market",
              "slug": "other", "total_cost": 8.0, "up_cost": 8.0,
              "quotes": [{"side": "UP", "price": 0.80, "mid": 0.95,
                          "token_id": "tok-up-b", "ts": 1700000000000}]}
    kpi = {"by_market": {"0xabc": base_a, "0xdef": base_b}}
    res = json.loads(_eval_js(f"""
    const kpi = {json.dumps(kpi)};
    const html = app.stitchPositionsRows(kpi, {{orders: []}}, {{col: 7, dir: 'desc'}});
    console.log(JSON.stringify({{html}}));
    """))
    html = res["html"]
    assert html.index("0xdef") < html.index("0xabc")
