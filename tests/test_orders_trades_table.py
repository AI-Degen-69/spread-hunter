"""The Orders & Trades table: four views of the same run.

A market the Market Filter graduated, an order resting on the book, a filled
leg the account is holding and a trade that settled with a booked profit or
loss are four stages of one object, and the operator reads them in that
order. They are four views of one table rather than four panels because only
the middle stages have live PnL: an order on the book is not a position, and
a PnL column beside it invites the reading that it is.

The column sets encode that. ACTIVE MARKETS carries no share count -- nothing
is owned yet. Orders carries no PnL -- nothing is exposed yet. OPEN POSITIONS
carries both. CLOSED TRADES reuses the Data & Markets table shape -- Commit,
Hedge, Realized P&L, Fills, Status -- so a closed trade reads identically in
both places, and lists only trades where money actually moved: a market that
settled flat booked nothing, and a row of zeros is not a trade.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "dashboard" / "static"
HARNESS = Path(__file__).resolve().parent / "js" / "orders_trades_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")

CID_QUOTED = "0xquoted"
CID_HELD = "0xheld"
CID_DONE = "0xresolved"
CID_CLOSED = "0xclosed"
CID_SETTLED = "0xsettled"


def _render(view: str, kpi: dict | None = None, state: dict | None = None) -> dict:
    payload = {"view": view, "kpi": kpi or {}, "state": state or {}}
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _quote(token: str, side: str, mid: float, ts: float, order_id: str | None = None,
           queue: float | None = None, price: float | None = None) -> dict:
    # The bot quotes under the mid; that gap is the edge the pair is chasing.
    return {"token_id": token, "side": side, "mid": mid, "ts": ts,
            "price": mid - 0.025 if price is None else price,
            "order_id": order_id, "queue_ahead": queue}


def _kpi() -> dict:
    return {
        "by_market": {
            CID_QUOTED: {
                "condition_id": CID_QUOTED, "title": "Quoted Market", "category": "MLB",
                "days_to_resolve": 6.9, "volume_24h": 165792.64, "resolved": False,
                "quotes_count": 2, "up_sh": 0, "dn_sh": 0, "total_sh": 0,
                "total_cost": 0, "pair_cost": None, "realized_pnl": 0,
                # Mids sum to $1.00 -- they always do, the legs are two sides
                # of one binary. The quotes sum to $0.93, and that gap is the
                # only edge there is.
                "quotes": [
                    _quote("tok-up", "UP", 0.49, 100.0, "ord-up", 5186.53, price=0.45),
                    _quote("tok-dn", "DN", 0.51, 101.0, "ord-dn", 1200.0, price=0.48),
                ],
            },
            CID_HELD: {
                "condition_id": CID_HELD, "title": "Held Market", "category": "NBA",
                "days_to_resolve": 2.0, "volume_24h": 42000.0, "resolved": False,
                "quotes_count": 2, "up_sh": 10, "dn_sh": 6, "total_sh": 16,
                "total_cost": 15.0, "pair_cost": 0.98, "realized_pnl": 0.25,
                "quotes": [
                    _quote("tok-h-up", "UP", 0.60, 200.0),
                    _quote("tok-h-dn", "DN", 0.42, 201.0),
                ],
            },
            CID_DONE: {
                "condition_id": CID_DONE, "title": "Resolved Market", "category": "MLB",
                "days_to_resolve": -1.0, "volume_24h": 9000.0, "resolved": True,
                "quotes_count": 4, "up_sh": 0, "dn_sh": 0, "total_sh": 0,
                "total_cost": 0, "pair_cost": None, "realized_pnl": 1.5,
                "quotes": [],
            },
            # The case the RESOLVED view existed for: the market settled and the
            # winner is known, but the legs are still on the books because
            # nothing has merged or redeemed them yet. It carries a settlement
            # and non-zero P&L, so it is a closed trade.
            CID_SETTLED: {
                "condition_id": CID_SETTLED, "title": "Settled Market", "category": "LOL",
                "days_to_resolve": None, "volume_24h": 51000.0, "resolved": True,
                "resolution": {"winner": "Dplus KIA", "resolved_ts": 1788526463.068991},
                "quotes_count": 4, "up_sh": 23, "dn_sh": 23, "total_sh": 46,
                "up_cost": 15.62, "dn_cost": 6.52, "total_cost": 22.14,
                "pair_cost": 0.963,                "realized_pnl": 1.21,
                "quotes": [],
                "settlements": [{"method": "single_buy_exit", "pnl": 1.21}],
            },
            # Settled with a booked profit, so it is a closed trade even
            # though nothing is held on it any more.
            CID_CLOSED: {
                "condition_id": CID_CLOSED, "title": "Closed Market", "category": "MLB",
                "days_to_resolve": None, "volume_24h": 77000.0, "resolved": True,
                "resolution": {"winner": "Away", "resolved_ts": 1788526400.0},
                "quotes_count": 2, "up_sh": 0, "dn_sh": 0, "total_sh": 0,
                "total_cost": 0, "pair_cost": None, "realized_pnl": -0.35,
                "quotes": [],
                "settlements": [{"method": "venue_sync", "pnl": -0.35}],
            },
        }
    }


def _state() -> dict:
    return {
        "orders": [
            # Posted DOWN-leg-first on purpose: the table has to put UP above
            # DOWN whatever order the registry hands them over in.
            {"order_id": "ord-dn", "condition_id": CID_QUOTED, "token_id": "tok-dn",
             "pair_id": "pair-a", "side": "BUY", "price": 0.51, "original_size": 5.0,
             "size_matched": 2.0, "size_remaining": 3.0, "status": "partial",
             "posted_ts": 900, "age_sec": 120.0},
            {"order_id": "ord-up", "condition_id": CID_QUOTED, "token_id": "tok-up",
             "pair_id": "pair-a", "side": "BUY", "price": 0.47, "original_size": 5.0,
             "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
             "posted_ts": 1000, "age_sec": 90.0},
            {"order_id": "ord-filled", "condition_id": CID_HELD, "token_id": "tok-h-up",
             "side": "BUY", "price": 0.60, "original_size": 10.0, "size_matched": 10.0,
             "size_remaining": 0.0, "status": "filled", "posted_ts": 800, "age_sec": 300.0},
            {"order_id": "ord-gone", "condition_id": CID_HELD, "token_id": "tok-h-dn",
             "side": "BUY", "price": 0.40, "original_size": 4.0, "size_matched": 0.0,
             "size_remaining": 4.0, "status": "cancelled", "posted_ts": 700, "age_sec": 400.0},
        ]
    }


# ── The shape of the three views ────────────────────────────────────────────

@requires_node
def test_the_four_views_are_the_four_stages_of_a_trade():
    # Arrange / Act
    rendered = _render("active-markets")

    # Assert
    assert rendered["views"] == ["active-markets", "open-orders", "positions",
                                 "closed-trades"]


@requires_node
def test_active_markets_carries_no_share_count():
    # Arrange — nothing is owned at this stage, so a size column would be
    # describing a position the account has not taken.
    rendered = _render("active-markets", _kpi(), _state())

    # Act / Assert
    joined = " ".join(rendered["columns"]).lower()
    assert "shares" not in joined
    assert "size" not in joined
    assert rendered["columns"] == ["Market", "Category", "UP Quote", "DOWN Quote",
                                   "Pair Cost", "Edge", "24h Volume", "Resolves",
                                   "Status"]


@requires_node
def test_open_orders_carries_no_pnl():
    # Arrange — an order resting in the book is not a position. A PnL column
    # beside it invites exactly the reading the strategy cannot afford.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert
    joined = " ".join(rendered["columns"]).lower()
    assert "pnl" not in joined
    assert "unrealized" not in joined
    assert rendered["columns"] == ["Market", "Leg", "Price", "Size", "Filled",
                                   "Remaining", "Total Cost", "Queue Ahead",
                                   "Age", "Order Status"]


@requires_node
def test_positions_carries_the_pnl_columns():
    # Arrange — this is the only stage where the account is exposed.
    rendered = _render("positions", _kpi(), _state())

    # Act / Assert
    assert "Unrealized" in rendered["columns"]
    assert "Realized" in rendered["columns"]


@requires_node
def test_positions_reads_per_leg_like_the_open_book():
    # Arrange — both tables describe pairs of legs, so both list one leg per
    # row under one Size column. A UP Shares / DOWN Shares pair of columns
    # made Positions the odd one out and left half of every row empty.
    rendered = _render("positions", _kpi(), _state())

    # Act / Assert
    assert rendered["columns"] == ["Market", "Leg", "Size", "Avg Price", "Cost",
                                   "Mark Value", "Unrealized", "Realized"]
    assert "Hedge" not in rendered["columns"]


# ── Active markets ──────────────────────────────────────────────────────────

@requires_node
def test_active_markets_lists_the_markets_being_quoted():
    # Arrange / Act
    rendered = _render("active-markets", _kpi(), _state())

    # Assert — the resolved market is done, not active.
    assert "Quoted Market" in rendered["html"]
    assert "Held Market" in rendered["html"]
    assert "Resolved Market" not in rendered["html"]
    assert rendered["rows"] == 2


@requires_node
def test_active_markets_prices_the_pair_off_the_bot_s_own_quotes():
    # Arrange — the pair is only worth quoting while UP + DOWN is under $1.00,
    # so the row shows what the bot is bidding on each leg and what the pair
    # would cost if both filled.
    rendered = _render("active-markets", _kpi(), _state())

    # Act / Assert — 0.45 + 0.48 = 0.93, so 7.0 cents of edge.
    assert "$0.450" in rendered["html"]
    assert "$0.480" in rendered["html"]
    assert "$0.930" in rendered["html"]
    assert "7.0¢" in rendered["html"]


@requires_node
def test_active_markets_edge_is_not_computed_from_mids():
    # Arrange — UP mid and DOWN mid sum to $1.00 by construction: they are two
    # sides of one binary. An edge computed from them reads 0.0¢ on every row
    # of every real database, which is what it did before this.
    kpi = _kpi()
    mids = kpi["by_market"][CID_QUOTED]["quotes"]
    assert round(mids[0]["mid"] + mids[1]["mid"], 6) == 1.0

    # Act
    rendered = _render("active-markets", kpi, _state())

    # Assert
    assert "0.0¢" not in rendered["html"]
    assert "$1.000" not in rendered["html"]


@requires_node
def test_active_markets_reads_the_down_leg_however_it_is_spelled():
    # Arrange — the quote log writes the down leg as `DOWN`; orders and the
    # pair summary call it `DN`. Accepting only one spelling left every real
    # database showing a blank DOWN mid, pair cost and edge.
    kpi = _kpi()
    kpi["by_market"][CID_QUOTED]["quotes"] = [
        _quote("tok-up", "UP", 0.49, 100.0, price=0.45),
        _quote("tok-dn", "DOWN", 0.51, 101.0, price=0.48),
    ]

    # Act
    rendered = _render("active-markets", kpi, _state())

    # Assert
    assert "$0.480" in rendered["html"]
    assert "$0.930" in rendered["html"]
    assert "7.0¢" in rendered["html"]


@requires_node
def test_active_markets_says_so_when_nothing_is_quoted():
    # Arrange / Act
    rendered = _render("active-markets", {"by_market": {}}, {"orders": []})

    # Assert
    assert "No markets are being quoted." in rendered["html"]


# ── Open orders ─────────────────────────────────────────────────────────────

@requires_node
def test_open_orders_shows_only_orders_still_resting():
    # Arrange — a filled or cancelled order is not open.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert
    assert rendered["rows"] == 2
    assert "ord-up" in rendered["html"]
    assert "ord-dn" in rendered["html"]
    assert "ord-filled" not in rendered["html"]
    assert "ord-gone" not in rendered["html"]


@requires_node
def test_open_orders_prices_the_order_and_its_cost():
    # Arrange — size, price and what the order would cost if it all filled,
    # which is the number that has to clear the per-order cap.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert — 5 shares at $0.47 is $2.35.
    assert "$0.470" in rendered["html"]
    assert "$2.35" in rendered["html"]


@requires_node
def test_open_orders_names_the_leg_each_order_is_on():
    # Arrange — orders carry a token id; only the quote log knows which side
    # of the market that token is. A single buy is the failure mode this
    # column exists to make visible.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert
    assert ">UP<" in rendered["html"]
    assert ">DOWN<" in rendered["html"]


@requires_node
def test_open_orders_names_the_leg_that_never_got_a_quote_logged():
    # Arrange — a leg with no quote in the log leaves its token unnamed, and
    # that is exactly the order most worth labelling: a lone filled leg is the
    # single buy the strategy exists to avoid. The market is binary, so a
    # token that is not the named leg is the other one.
    kpi = _kpi()
    kpi["by_market"][CID_QUOTED]["quotes"] = [
        _quote("tok-up", "UP", 0.47, 100.0, "ord-up", 5186.53),
    ]

    # Act
    rendered = _render("open-orders", kpi, _state())

    # Assert
    assert ">UP<" in rendered["html"]
    assert ">DOWN<" in rendered["html"]


@requires_node
def test_open_orders_leaves_the_leg_blank_when_neither_side_was_quoted():
    # Arrange — with nothing named, guessing a side would be inventing it.
    kpi = _kpi()
    kpi["by_market"][CID_QUOTED]["quotes"] = []

    # Act
    rendered = _render("open-orders", kpi, _state())

    # Assert
    assert ">UP<" not in rendered["html"]
    assert ">DOWN<" not in rendered["html"]
    assert ">--<" in rendered["html"]


@requires_node
def test_open_orders_falls_back_to_the_condition_id_for_an_unreported_market():
    # Arrange — an order outlives its market's entry in the KPI report once
    # that market leaves the graduated universe. A truncated condition id is
    # still something the operator can search the registry for; `--` is not.
    state = _state()
    for order in state["orders"]:
        if order.get("pair_id") == "pair-a":
            order["condition_id"] = "0xabc123def456"

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert
    assert "0xabc123d" in rendered["html"]


@requires_node
def test_open_orders_lists_a_pair_as_two_rows_up_first():
    # Arrange — both legs have to fill for the pair to merge back into $1.00,
    # so the two orders that belong together are read together, and the leg
    # that decides the direction is read first.
    rendered = _render("open-orders", _kpi(), _state())

    # Act
    html = rendered["html"]

    # Assert — UP above DOWN, whatever order the registry handed them over in.
    assert html.index(">UP<") < html.index(">DOWN<")
    assert html.count('data-pair="pair-a"') == 2


@requires_node
def test_open_orders_names_the_market_once_across_both_legs():
    # Arrange — one market, one name: a name repeated on both rows reads as
    # two unrelated orders that happen to share a title.
    rendered = _render("open-orders", _kpi(), _state())

    # Act
    html = rendered["html"]

    # Assert — the merged cell spans the pair, and the second row has no
    # market cell of its own.
    assert 'rowspan="2"' in html
    assert html.count("Quoted Market") == 1


@requires_node
def test_open_orders_prices_the_pair_the_two_legs_would_make():
    # Arrange — under $1.00 the merge books a profit; over it books a loss.
    # That is the number the pair exists to hit, so it belongs on the pair.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert — 0.47 + 0.51 = $0.980.
    assert "Pair Cost" in rendered["html"]
    assert "$0.980" in rendered["html"]
    assert "ot-cost is-good" in rendered["html"]
    # Plain text beside the status pill, not a second pill: a pill reads as a
    # state, and two of them stacked look like two verdicts on one pair.
    assert 'ot-tag is-good">Pair Cost' not in rendered["html"]


@requires_node
def test_open_orders_does_not_call_a_break_even_pair_a_loss():
    # Arrange — a pair costing exactly $1.00 merges back into $1.00. That is
    # zero profit, not a loss, and colouring it red reads as money lost.
    state = _state()
    for order in state["orders"]:
        order["price"] = 0.50

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert — a warning that the capital earns nothing, not a loss in red.
    assert "$1.000" in rendered["html"]
    assert "ot-cost is-warn" in rendered["html"]
    assert "ot-cost is-alert" not in rendered["html"]


@requires_node
def test_open_orders_labels_a_half_built_pair_unpaired():
    # Arrange — an order whose partner leg is not on the book is Unpaired.
    # If it fills alone it becomes a single buy: a directional bet nobody
    # decided to take. The glossary keeps those two names apart, so the tag
    # names the order’s state, not the position it has not become yet.
    state = _state()
    state["orders"] = [o for o in state["orders"] if o["order_id"] != "ord-dn"]

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert
    assert "Unpaired" in rendered["html"]
    assert "ot-tag is-alert" in rendered["html"]
    assert "Pair Cost" not in rendered["html"]
    assert "single buy" not in rendered["html"].lower()
    assert "Inferred" not in rendered["html"]
    assert "ot-tag is-info" not in rendered["html"]


@requires_node
def test_open_orders_consolidates_detached_complementary_legs_with_inferred_tag():
    # Arrange — two resting orders on the same market missing pair_id (or with
    # mismatched/null pair_id) are complementary (one UP, one DOWN).
    # They must consolidate into one pair group with rowspan=2, computed pair cost,
    # and the secondary 'Inferred' tag.
    state = _state()
    for o in state["orders"]:
        if o.get("condition_id") == CID_QUOTED:
            o["pair_id"] = None  # Detach both orders

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert
    assert f'data-pair="inferred:{CID_QUOTED}"' in rendered["html"]
    assert 'rowspan="2"' in rendered["html"]
    assert "ot-tag is-info" in rendered["html"]
    assert "Inferred" in rendered["html"]
    # Combined cost: 0.47 (UP) + 0.51 (DOWN) = 0.98
    assert "$0.980" in rendered["html"]
    assert "Partial" in rendered["html"]  # Sizes: 5.0 vs 3.0 remaining



@requires_node
def test_open_orders_bands_alternate_pairs():
    # Arrange — two pairs back to back are four rows; without the banding the
    # boundary between them is invisible.
    state = _state()
    state["orders"] = state["orders"] + [
        {"order_id": "ord-b-up", "condition_id": CID_HELD, "token_id": "tok-h-up",
         "pair_id": "pair-b", "side": "BUY", "price": 0.55, "original_size": 5.0,
         "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
         "posted_ts": 500, "age_sec": 60.0},
        {"order_id": "ord-b-dn", "condition_id": CID_HELD, "token_id": "tok-h-dn",
         "pair_id": "pair-b", "side": "BUY", "price": 0.40, "original_size": 5.0,
         "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
         "posted_ts": 499, "age_sec": 61.0},
    ]

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert — every pair opens with a rule, and every second pair is shaded.
    assert rendered["html"].count("ot-pair-start") == 2
    assert rendered["html"].count("ot-pair-alt") == 2
    assert rendered["rows"] == 4


@requires_node
def test_a_pair_that_cannot_merge_at_a_profit_reads_as_an_alert():
    # Arrange — a pair assembled at or over $1.00 is a booked loss, not a
    # warning about one. It must not wear the same colour as a healthy pair.
    state = _state()
    for order in state["orders"]:
        if order.get("pair_id") == "pair-a":
            order["price"] = 0.55

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert — 0.55 + 0.55 = $1.100.
    assert "ot-cost is-alert" in rendered["html"]
    assert "ot-cost is-good" not in rendered["html"]


def test_the_panel_copy_uses_the_glossary_names():
    # Arrange — `docs/agents/glossary.md` retired "screener" for "Market
    # Filter", and operator-facing copy is where the old name survives longest.
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    index = (_STATIC / "index.html").read_text(encoding="utf-8")

    # Act — the three view notes, which is the copy this panel owns.
    notes = app.split("const OT_NOTES = {")[1].split("};")[0]

    # Assert
    assert "Market Filter" in notes
    assert "screener" not in notes.lower()
    assert "Market Filter" in index.split('id="orders-trades-note"')[1][:300]


def test_the_glossary_separates_an_unpaired_order_from_a_single_buy():
    # Arrange — `Unpaired` is the state of the order; `single buy` is the state
    # of the position it turns into if it fills alone. Collapsing the two
    # names loses the distinction between a risk and a booked one.
    glossary = (_ROOT / "docs" / "agents" / "glossary.md").read_text(encoding="utf-8")

    # Act / Assert
    assert "**Unpaired**" in glossary
    assert "**Pair Cost**" in glossary
    assert "single buy" in glossary
    assert "one leg resting" in glossary  # named as the wording to avoid


def test_the_market_column_has_a_width_floor():
    # Arrange — the market name is the only cell that wraps, and a three-word
    # title folded onto three lines squeezes every number column beside it.
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")

    # Act
    block = css.split("#orders-trades-table td.ot-market")[1].split("}")[0]

    # Assert
    assert "min-width" in block


def test_the_width_floor_does_not_target_the_first_cell_by_position():
    # Arrange — in a paired row the market cell uses `rowspan`, so the second
    # row's first cell is the Leg. Keyed off position, the 200px floor would
    # land on the Leg column instead of the market name.
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")

    # Act / Assert
    assert "#orders-trades-table td:first-child" not in css


@requires_node
@pytest.mark.parametrize("view", ["active-markets", "open-orders", "positions"])
def test_every_view_tags_its_market_cell(view):
    # Arrange — the width floor is keyed off the class now, so a view that
    # forgets it loses the floor and folds the title onto three lines.
    rendered = _render(view, _kpi(), _state())

    # Act / Assert
    assert 'class="ot-market"' in rendered["html"]


@requires_node
def test_closed_trades_renders_the_data_and_markets_row_class():
    # Arrange — the closed-trades view reuses the Data & Markets row shape,
    # which carries the market name inside `marketLink` on a `market-row`,
    # not an `ot-market` cell: the same row class, so the same expand wiring.
    rendered = _render("closed-trades", _kpi(), _state())

    # Act / Assert
    assert 'class="market-row' in rendered["html"]
    assert 'tabindex="0"' in rendered["html"]


@requires_node
def test_open_orders_says_so_when_the_book_is_empty():
    # Arrange / Act
    rendered = _render("open-orders", _kpi(), {"orders": []})

    # Assert
    assert "No orders are resting on the book." in rendered["html"]


# ── Positions ───────────────────────────────────────────────────────────────

@requires_node
def test_positions_lists_only_markets_with_filled_shares():
    # Arrange / Act
    rendered = _render("positions", _kpi(), _state())

    # Assert — one row per held leg, the market named once across both.
    assert rendered["rows"] == 2
    assert rendered["html"].count("Held Market") == 1
    assert "Quoted Market" not in rendered["html"]


@requires_node
def test_positions_marks_the_merged_pair_at_par_and_the_rest_at_mid():
    # Arrange — 10 UP and 6 DOWN: six pairs merge back into $1.00 each, and
    # the four naked UP shares are worth whatever UP is trading at.
    #   6 * 1.00 + 4 * 0.60 = $8.40, against a $15.00 cost basis.
    rendered = _render("positions", _kpi(), _state())

    # Act / Assert
    assert "$8.40" in rendered["html"]
    assert "-$6.60" in rendered["html"]


@requires_node
def test_positions_leaves_the_mark_blank_when_the_naked_leg_has_no_mid():
    # Arrange — an unhedged leg with no observed mid cannot be valued, and a
    # made-up number here is worse than an empty cell.
    kpi = _kpi()
    kpi["by_market"][CID_HELD]["quotes"] = []

    # Act
    rendered = _render("positions", kpi, _state())

    # Assert -- read the Mark Value cell itself. A bare `"--" in html` passes
    # on any row, so it would stay green if the naked shares were valued at 0.
    cells = rendered["html"].split("</td>")
    mark_cell = cells[rendered["columns"].index("Mark Value")]
    assert ">--" in mark_cell
    assert "$8.40" not in rendered["html"]


@requires_node
def test_positions_calls_a_lone_filled_leg_unpaired():
    # Arrange — one leg filled and the other not is a directional bet nobody
    # decided to take. It wears the same name and the same tone here as an
    # order with no partner does on the book.
    kpi = _kpi()
    kpi["by_market"][CID_HELD]["dn_sh"] = 0
    kpi["by_market"][CID_HELD]["total_sh"] = 10

    # Act
    rendered = _render("positions", kpi, _state())

    # Assert
    assert "Unpaired" in rendered["html"]
    assert "ot-tag is-alert" in rendered["html"]
    assert rendered["rows"] == 1


@requires_node
def test_positions_shows_inferred_tag_when_fills_lack_shared_pair_id():
    # Arrange — both legs are held in a market, but fills do not share a common pair_id.
    state = _state()
    state["fills"] = [
        {"fill_id": "f-1", "order_id": "o-1", "condition_id": CID_HELD, "token_id": "tok-h-up",
         "pair_id": "pair-detached-up", "side": "BUY", "price": 0.60, "size": 10.0},
        {"fill_id": "f-2", "order_id": "o-2", "condition_id": CID_HELD, "token_id": "tok-h-dn",
         "pair_id": "pair-detached-dn", "side": "BUY", "price": 0.40, "size": 6.0},
    ]

    # Act
    rendered = _render("positions", _kpi(), state)

    # Assert
    assert "ot-tag is-info" in rendered["html"]
    assert "Inferred" in rendered["html"]
    assert "Partial" in rendered["html"]  # 10 UP vs 6 DN


@requires_node
def test_positions_omits_inferred_tag_when_fills_share_pair_id():
    # Arrange — both legs are held and fills share a genuine pair_id.
    state = _state()
    state["fills"] = [
        {"fill_id": "f-1", "order_id": "o-1", "condition_id": CID_HELD, "token_id": "tok-h-up",
         "pair_id": "pair-native-held", "side": "BUY", "price": 0.60, "size": 10.0},
        {"fill_id": "f-2", "order_id": "o-2", "condition_id": CID_HELD, "token_id": "tok-h-dn",
         "pair_id": "pair-native-held", "side": "BUY", "price": 0.40, "size": 6.0},
    ]

    # Act
    rendered = _render("positions", _kpi(), state)

    # Assert
    assert "ot-tag is-info" not in rendered["html"]
    assert "Inferred" not in rendered["html"]
    assert "Partial" in rendered["html"]


@requires_node
def test_positions_says_so_when_nothing_is_held():
    # Arrange / Act
    rendered = _render("positions", {"by_market": {}}, {"orders": []})

    # Assert
    assert "No legs have filled, so nothing is held." in rendered["html"]


# ── Closed trades ──────────────────────────────────────────────────────────

@requires_node
def test_closed_trades_lists_markets_that_booked_a_profit_or_loss():
    # Arrange — a closed trade is a settlement with money actually booked,
    # whatever the account holds now. CID_SETTLED still shows held legs; CID
    # _CLOSED settled flat but booked a loss.
    rendered = _render("closed-trades", _kpi(), _state())

    # Act / Assert — one row per closed trade, largest P&L first: the profit
    # on the settled pair (+$1.21) outranks the booked loss (-$0.35).
    assert rendered["rows"] == 2
    assert "Settled Market" in rendered["html"]
    assert "Closed Market" in rendered["html"]
    assert rendered["html"].index("Settled Market") < rendered["html"].index("Closed Market")


@requires_node
def test_closed_trades_uses_the_data_and_markets_table_shape():
    # Arrange — the tab exists so a closed trade reads like the same market
    # in the Data & Markets table; two shapes for one fact is two reads.
    rendered = _render("closed-trades", _kpi(), _state())

    # Act / Assert
    assert rendered["columns"] == ["Market", "Commit ($)", "Hedge",
                                   "Realized P&L", "Fills", "Status"]
    assert 'class="market-row"' in rendered["html"]


@requires_node
def test_closed_trades_excludes_a_settled_market_with_no_booked_pnl():
    # Arrange — CID_DONE settled but booked nothing: no shares, no settlement,
    # zero P&L. A row of zeros is not a trade.
    rendered = _render("closed-trades", _kpi(), _state())

    # Act / Assert
    assert "Resolved Market" not in rendered["html"]


@requires_node
def test_closed_trades_excludes_a_zero_pnl_market_even_with_a_settlement():
    # Arrange — the settlement arrived, but the trade merged flat and booked
    # zero. There is no profit or loss to read.
    kpi = _kpi()
    kpi["by_market"][CID_SETTLED]["realized_pnl"] = 0

    # Act
    rendered = _render("closed-trades", kpi, _state())

    # Assert
    assert "Settled Market" not in rendered["html"]
    assert rendered["rows"] == 1


@requires_node
def test_closed_trades_excludes_a_market_with_no_settlement():
    # Arrange — a finished market the settlement sweeper has not written yet
    # is not a closed trade; the Data & Markets table already covers it.
    kpi = _kpi()
    kpi["by_market"][CID_SETTLED].pop("settlements")

    # Act
    rendered = _render("closed-trades", kpi, _state())

    # Assert
    assert "Settled Market" not in rendered["html"]


@requires_node
def test_closed_trades_says_so_when_nothing_has_closed():
    # Arrange / Act
    rendered = _render("closed-trades", {"by_market": {}}, {"orders": []})

    # Assert
    assert "No closed trades yet" in rendered["html"]


@requires_node
def test_closed_trades_excludes_a_settled_market_with_a_resting_order():
    # Arrange — CID_SETTLED is settled with booked P&L, but has a working (open) order
    state = _state()
    state["orders"].append({
        "condition_id": CID_SETTLED,
        "status": "open",
        "side": "BUY",
        "price": 0.45,
        "original_size": 10,
        "size_matched": 0,
    })

    # Act
    rendered = _render("closed-trades", _kpi(), state)

    # Assert — the market with a working order is not closed; count drops matching the exclusion
    assert "Settled Market" not in rendered["html"]
    assert "Closed Market" in rendered["html"]
    assert rendered["rows"] == 1
    assert rendered["counts"]["closed-trades"] == 1


@requires_node
def test_closed_trades_always_renders_finished_status_pill():
    # Arrange / Act — every row rendered in CLOSED TRADES must display as FINISHED
    rendered = _render("closed-trades", _kpi(), _state())

    # Assert
    assert "FINISHED" in rendered["html"]
    assert "QUOTING" not in rendered["html"]
    assert "IDLE" not in rendered["html"]


@requires_node
def test_active_markets_retains_live_quoting_status_pill():
    # Arrange / Act — active-markets table retains live state (QUOTING / IDLE)
    rendered = _render("active-markets", _kpi(), _state())

    # Assert
    assert "QUOTING" in rendered["html"]



# ── Tab counts ──────────────────────────────────────────────────────────────

@requires_node
def test_each_tab_counts_its_own_rows():
    # Arrange — the count on the tab is what tells the operator there is
    # something on a view they are not looking at.
    rendered = _render("active-markets", _kpi(), _state())

    # Act / Assert
    assert rendered["counts"] == {
        "active-markets": 2,
        "open-orders": 2,
        "positions": 1,
        "closed-trades": 2,
    }


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_the_panel_is_on_the_served_page():
    # Arrange / Act
    index = (_STATIC / "index.html").read_text(encoding="utf-8")

    # Assert
    assert 'id="orders-trades-card"' in index
    assert 'id="orders-trades-head"' in index
    assert 'id="orders-trades-body"' in index
    for view in ("active-markets", "open-orders", "positions", "closed-trades"):
        assert f'data-ot-view="{view}"' in index


def test_the_panel_redraws_on_visible_poll_and_switch():
    # Arrange — the table is worthless if it only fills in on a tab click.
    # Since #264 it repaints on every poll while its tab is visible, plus
    # once synchronously on switch.
    app = (_STATIC / "app.js").read_text(encoding="utf-8")

    # Act / Assert
    assert "renderOrdersTrades(currentKpi, lastState)" in app
    assert "initOrdersTradesTabs();" in app


def test_the_panel_lands_on_the_dashboard_page():
    # Arrange — the sidebar layout has to claim it, or it stays behind on the
    # tabbed page when the layout is switched over.
    prototype = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act
    home = prototype.split("page: 'home'")[1].split("page: 'data-markets'")[0]

    # Assert
    assert "#orders-trades-card" in home
    assert "#broker-portfolio-overview" in home
    assert "label: 'Dashboard'" in home
