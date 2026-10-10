"""The Orders & Trades table: four views of the same run.

A market the Market Filter graduated, an order resting on the book, a filled
leg the account is holding and a trade that settled with a booked profit or
loss are four stages of one object, and the operator reads them in that
order. They are four views of one table rather than four panels because only
the middle stages have live PnL: an order on the book is not a position, and
a PnL column beside it invites the reading that it is.

The column sets encode that. ACTIVE MARKETS carries no share count -- nothing
is owned yet. Orders carries no PnL -- nothing is exposed yet. OPEN POSITIONS
carries both. CLOSED TRADES lists only trades where money actually moved -- a
market that settled flat booked nothing, and a row of zeros is not a trade --
and, because its row is a MARKET and the positions under it are one click
down, it carries no cost column at all: `Commit ($)` read $0.00 on a closed
market, beside a booked loss. What is left on the market row is what a market
can answer -- when it closed, what state the positions under it rolled up to,
what money it booked, how many times something executed here, and that it is
finished.
"""
from __future__ import annotations

import json
import re
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


def _render(view: str, kpi: dict | None = None, state: dict | None = None,
            *, sort: dict | None = None, expand: list[str] | None = None) -> dict:
    # `sort` is keyword-only on purpose: as a positional third argument it lands
    # in `state`, the render comes back unsorted, and the sort assertion passes
    # against output the sort never touched.
    payload = {"view": view, "kpi": kpi or {}, "state": state or {}, "sort": sort,
               "expand": expand or []}
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _cids(rendered: dict) -> list[str]:
    """The market order the rows render in (`<tr>` tags only: #427 puts a
    `data-cid` address on the live value cells too, which are not rows)."""
    return re.findall(r'<tr[^>]*data-cid="([^"]+)"', rendered["html"])


def _pairs(rendered: dict) -> list[str]:
    """The pair order the Open Orders rows render in."""
    return re.findall(r'data-pair="([^"]+)"', rendered["html"])


def _order_ids(rendered: dict) -> list[str]:
    return re.findall(r'data-order-id="([^"]+)"', rendered["html"])


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


CID_ESPORTS = "0xesports"
CID_POLITICS = "0xpolitics"
CID_GHOST = "0xghost"


def _category_kpi() -> dict:
    return {
        "by_market": {
            CID_ESPORTS: {
                "condition_id": CID_ESPORTS, "title": "Lol Kcb Wd 2026 09 26",
                "category": "E-Sports",
                "days_to_resolve": 6.9, "volume_24h": 165792.64, "resolved": False,
                "quotes_count": 2, "up_sh": 0, "dn_sh": 0, "total_sh": 0,
                "total_cost": 0, "pair_cost": None, "realized_pnl": 0,
                "quotes": [
                    _quote("tok-e-up", "UP", 0.49, 100.0, "ord-e-up", 5186.53, price=0.45),
                    _quote("tok-e-dn", "DN", 0.51, 101.0, "ord-e-dn", 1200.0, price=0.48),
                ],
            },
            CID_POLITICS: {
                "condition_id": CID_POLITICS,
                "title": "Will Luiz Incio Lula Da Silva Win The 2026 "
                         "Brazilian Presidential Election",
                "category": "Politics",
                "days_to_resolve": 30.0, "volume_24h": 42000.0, "resolved": False,
                "quotes_count": 2, "up_sh": 10, "dn_sh": 6, "total_sh": 16,
                "total_cost": 15.0, "pair_cost": 0.98, "realized_pnl": 0.25,
                "quotes": [
                    _quote("tok-p-up", "UP", 0.60, 200.0),
                    _quote("tok-p-dn", "DN", 0.42, 201.0),
                ],
            },
        }
    }


def _category_state() -> dict:
    return {
        "orders": [
            {"order_id": "ord-e-up", "condition_id": CID_ESPORTS, "token_id": "tok-e-up",
             "pair_id": "pair-e", "side": "BUY", "price": 0.45, "original_size": 5.0,
             "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
             "posted_ts": 1000, "age_sec": 90.0},
            {"order_id": "ord-e-dn", "condition_id": CID_ESPORTS, "token_id": "tok-e-dn",
             "pair_id": "pair-e", "side": "BUY", "price": 0.48, "original_size": 5.0,
             "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
             "posted_ts": 1001, "age_sec": 89.0},
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
    assert rendered["columns"] == ["Timestamp", "Market", "Category", "UP Quote", "DOWN Quote",
                                   "Mid Price", "$ Traded 30m", "24h Volume", "Top-3 Bid Depth",
                                   "Horizon", "Status"]


@requires_node
def test_open_orders_carries_no_pnl():
    # Arrange — an order resting in the book is not a position. A PnL column
    # beside it invites exactly the reading the strategy cannot afford.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert
    joined = " ".join(rendered["columns"]).lower()
    assert "pnl" not in joined
    assert "unrealized" not in joined
    # Orders that are still resting have neither fills to split out nor a
    # non-open status to explain: Filled, Remaining and Order Status only
    # described closed work this tab never shows.
    assert rendered["columns"] == ["Timestamp", "Market", "Leg", "Price", "Size",
                                   "Total Cost", "Queue Ahead", "Age"]


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
    assert rendered["columns"] == ["Timestamp", "Market", "Leg", "Size", "Avg Price", "Cost",
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
    # Arrange — the row shows what the bot is bidding on each leg and the mid
    # price from screening, while pair cost and edge are removed.
    rendered = _render("active-markets", _kpi(), _state())

    # Act / Assert — 0.45 UP quote, 0.48 DOWN quote, 0.490 mid price; no pair cost or edge.
    assert "$0.450" in rendered["html"]
    assert "$0.480" in rendered["html"]
    assert "$0.490" in rendered["html"]
    assert "$0.930" not in rendered["html"]
    assert "7.0¢" not in rendered["html"]


@requires_node
def test_active_markets_edge_and_pair_cost_are_removed():
    # Arrange / Act — Pair Cost and Edge columns are removed from Active Markets.
    rendered = _render("active-markets", _kpi(), _state())

    # Assert
    assert "Pair Cost" not in rendered["columns"]
    assert "Edge" not in rendered["columns"]
    assert "$0.930" not in rendered["html"]
    assert "7.0¢" not in rendered["html"]


@requires_node
def test_active_markets_reads_the_down_leg_however_it_is_spelled():
    # Arrange — the quote log writes the down leg as `DOWN`; orders and the
    # pair summary call it `DN`. Accepting both spellings ensures quotes and mids render.
    kpi = _kpi()
    kpi["by_market"][CID_QUOTED]["quotes"] = [
        _quote("tok-up", "UP", 0.49, 100.0, price=0.45),
        _quote("tok-dn", "DOWN", 0.51, 101.0, price=0.48),
    ]

    # Act
    rendered = _render("active-markets", kpi, _state())

    # Assert
    assert "$0.480" in rendered["html"]
    assert "$0.490" in rendered["html"]
    assert "$0.930" not in rendered["html"]


@requires_node
def test_active_markets_says_so_when_nothing_is_quoted():
    # Arrange / Act
    rendered = _render("active-markets", {"by_market": {}}, {"orders": []})

    # Assert
    assert "No markets are being quoted." in rendered["html"]


@requires_node
def test_active_markets_displays_screening_parameters():
    # Arrange — Active Markets must show the screening filter parameters:
    # Mid Price, $ traded in last 30m, 24h Volume, Top-3 Bid Depth, Horizon.
    kpi = _kpi()
    kpi["by_market"][CID_QUOTED]["movement_usd"] = 14192.85
    kpi["by_market"][CID_QUOTED]["movement_window_sec"] = 1800.0
    kpi["by_market"][CID_QUOTED]["yes_depth_usd"] = 2015.95
    kpi["by_market"][CID_QUOTED]["no_depth_usd"] = 6642.05

    # Act
    rendered = _render("active-markets", kpi, _state())
    html = rendered["html"]

    # Assert — each screening parameter renders in the row:
    # Mid Price ($0.490), 30m traded ($14k), 24h volume ($166k), top-3 bid depth ($2k), horizon (6.9d)
    assert "$0.490" in html
    assert "$14k" in html
    assert "$166k" in html
    assert "$2k" in html
    assert "6.9d" in html


@requires_node
def test_active_markets_displays_feed_top3_bid_depth_without_side_depths():
    kpi = _kpi()
    market = kpi["by_market"][CID_QUOTED]
    market["top3_bid_depth"] = 3123.45
    market.pop("yes_depth_usd", None)
    market.pop("no_depth_usd", None)

    rendered = _render("active-markets", kpi, _state())

    assert "$3k" in rendered["html"]


@requires_node
def test_active_markets_handles_mixed_depths():
    # Arrange — one nonnumeric depth and one numeric depth should fall back to finite depth
    kpi = _kpi()
    market = kpi["by_market"][CID_QUOTED]
    market.pop("top3_bid_depth", None)
    market["yes_depth_usd"] = "invalid"
    market["no_depth_usd"] = 4500.0

    rendered = _render("active-markets", kpi, _state())

    assert "$5k" in rendered["html"]


@requires_node
def test_active_markets_status_pills_distinguish_resting_and_quoting():
    # Arrange — CID_QUOTED has resting orders (RESTING); CID_HELD has quotes but no resting orders (QUOTING).
    rendered = _render("active-markets", _kpi(), _state())
    html = rendered["html"]

    # Assert — RESTING uses resting-breathing (bright green in css) on CID_QUOTED row,
    # QUOTING uses quoting-breathing (less bright green in css) on CID_HELD row.
    quoted_row = html.split(f'data-cid="{CID_QUOTED}"')[1].split("</tr>")[0]
    held_row = html.split(f'data-cid="{CID_HELD}"')[1].split("</tr>")[0]
    assert 'class="pill resting-breathing"' in quoted_row
    assert 'class="pill quoting-breathing"' in held_row


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
def test_closed_trades_carries_no_market_level_cost():
    # Arrange — a CLOSED TRADES row is a market, and the positions are the rows
    # inside it. `Commit ($)` is the cost of what is HELD, so on a closed market
    # it read $0.00 next to a booked loss: a number that contradicts the row it
    # sits on. The cost still exists one click down, per position.
    rendered = _render("closed-trades", _kpi(), _state())

    # Act / Assert
    assert rendered["columns"] == ["Timestamp", "Market", "Hedge",
                                   "Realized P&L", "Fill events", "Status"]
    assert 'class="market-row"' in rendered["html"]
    # The settled market holds 23 + 23 shares at a $22.14 basis; none of that
    # belongs on the market row.
    assert "$22.14" not in rendered["html"]
    assert rendered["html"].count('colspan="6"') == rendered["html"].count('class="orders-expand-row"')


@requires_node
def test_the_hedge_header_says_it_is_a_roll_up_of_the_rows_inside():
    # Arrange — the column is the positions under the market, summed. Read as a
    # fact about the market it contradicted the trades it sat on.
    head = _render("closed-trades", _kpi(), _state())["head"]

    # Act
    hedge_th = next(th for th in head.split("<th")[1:] if ">Hedge<" in th)
    tag = hedge_th.split(">")[0]

    # Assert — the vocabulary rides on the <th>, not in the cell text.
    assert "Every position under this market, rolled up" in tag
    assert "Hedge: " not in _render("closed-trades", _kpi(), _state())["html"]


# ── The Hedge roll-up on a market row ─────────────────────────────────────

CID_PARTIAL = "0xpartial"


def _hedged_kpi() -> dict:
    """Four closed markets, one per state of the positions under them: both legs
    matched, both legs at different sizes, one leg alone, and nothing at all."""
    kpi = _kpi()
    kpi["by_market"][CID_SETTLED].update({"up_sh": 23, "dn_sh": 23})
    kpi["by_market"][CID_CLOSED].update({"up_sh": 0, "dn_sh": 0})
    kpi["by_market"][CID_DONE] = {
        **kpi["by_market"][CID_SETTLED],
        "condition_id": CID_DONE, "title": "Naked Market", "category": "MLB",
        "resolved": False, "days_to_resolve": 3.0, "resolution": None,
        # Still a closed trade here: it carries the settled fixture's
        # settlement and booked P&L (an aged-out single-buy exit), but the
        # market itself is not finished -- days_to_resolve is forward.
        "up_sh": 10.0, "dn_sh": 0.0, "realized_pnl": -1.4,
    }
    kpi["by_market"][CID_PARTIAL] = {
        **kpi["by_market"][CID_SETTLED],
        "condition_id": CID_PARTIAL, "title": "Partly Paired Market", "category": "MLB",
        "up_sh": 10.0, "dn_sh": 6.0, "realized_pnl": -0.5,
    }
    return kpi


def _hedge_cell(html: str, marker: str) -> str:
    """The Hedge cell of the row that names `marker`, on its own.

    The row's cell order is Timestamp, Market, Hedge, ... and this reads the
    third cell by position. Searching the row for the word instead would let a
    tooltip or a legend anywhere in it satisfy the assertion.
    """
    idx = html.index(marker)
    row_start = html.rindex('<tr class="market-row', 0, idx)
    row = html[row_start:html.index("</tr>", idx)]
    return row.split("<td")[3]


@requires_node
def test_a_settled_market_with_time_on_its_hands_is_unsettled_not_unpaired():
    # Arrange -- CID_SETTLED settled with a booked profit and holds 10 shares
    # on only one leg. `Unpaired` is the pair-assembly word: it claims the
    # engine is still trying to complete a pair, and a settled market is
    # not. The word here has to be one a settled market can say truthfully.
    kpi = _kpi()
    kpi["by_market"][CID_SETTLED].update({"up_sh": 0, "dn_sh": 10})

    # Act / Assert
    cell = _hedge_cell(_render("closed-trades", kpi, _state())["html"],
                       "Settled Market")
    assert ">Unpaired<" not in cell
    assert ">Unsettled<" in cell


@requires_node
def test_the_unsettled_single_buy_says_what_it_is():
    # Arrange -- the naked shares on a settled market are not a wait for a
    # partner; they are a wanton directional bet the market's own resolution
    # decided alone. The hover has to say that, not promise a merge.
    kpi = _kpi()
    kpi["by_market"][CID_SETTLED].update({"up_sh": 0, "dn_sh": 10})

    # Act / Assert
    cell = _hedge_cell(_render("closed-trades", kpi, _state())["html"],
                       "Settled Market")
    assert "settled without its counter-leg" in cell


@requires_node
@requires_node
def test_an_unfinished_market_still_says_unpaired():
    # Arrange -- the four states of the positions under a market. Paired,
    # Partial and Unpaired are the three words the OPEN POSITIONS table already
    # uses on the positions themselves, and Flat is the fourth case that
    # vocabulary cannot name: a market holding nothing at all.
    rendered = _render("closed-trades", _hedged_kpi(), _state())

    # Act
    html = rendered["html"]

    # Assert -- `Unpaired` belongs to the market that can still assemble a
    # pair (CID_DONE is unfinished here, up 10 dn 0); the settled market says
    # `Unsettled` instead, which is what time ran out on it.
    assert ">Paired<" in _hedge_cell(html, "Settled Market")
    assert ">Partial<" in _hedge_cell(html, "Partly Paired Market")
    assert ">Unpaired<" in _hedge_cell(html, "Naked Market")
    assert ">Flat<" in _hedge_cell(html, "Closed Market")
    assert ">Unpaired<" not in _hedge_cell(html, "Settled Market")
    # The words the glossary retired from a state name.
    assert "One-Sided" not in html
    assert ">Hedged<" not in html


@requires_node
def test_a_market_holding_nothing_says_flat_instead_of_unpaired():
    # Arrange -- the case that made the column unreadable: a closed market holds
    # nothing, and "nothing held" used to fall into the same branch as "held
    # and unbalanced". On shadow-01 all 82 closed markets read One-Sided, and
    # `Unpaired` would claim a single buy that is not there.
    rendered = _render("closed-trades", _hedged_kpi(), _state())

    # Assert
    cell = _hedge_cell(rendered["html"], "Closed Market")
    assert "Flat" in cell
    assert "Unpaired" not in cell
    assert "nothing held" in cell


@requires_node
def test_the_four_roll_up_states_use_the_pair_status_tones():
    # Arrange -- Paired, Partial and Unpaired take the tones the OPEN POSITIONS
    # table already gives them (good, warn, alert); Flat is quieter still,
    # because "nothing here" is not a verdict.
    rendered = _render("closed-trades", _hedged_kpi(), _state())

    # Act
    html = rendered["html"]

    # Assert
    assert 'class="ot-tag is-good"' in _hedge_cell(html, "Settled Market")
    assert 'class="ot-tag is-warn"' in _hedge_cell(html, "Partly Paired Market")
    assert 'class="ot-tag is-alert"' in _hedge_cell(html, "Naked Market")
    assert 'class="ot-tag is-quiet"' in _hedge_cell(html, "Closed Market")


@requires_node
def test_the_lone_leg_says_which_leg_and_how_much():
    # Arrange -- 10 UP against nothing: the risk the column exists to show.
    rendered = _render("closed-trades", _hedged_kpi(), _state())

    # Assert
    cell = _hedge_cell(rendered["html"], "Naked Market")
    assert "10.0000 UP held with no DOWN partner: a single buy" in cell


# ── The expanded rows: leg, units, status ─────────────────────────────────

def _expanded(rendered: dict) -> str:
    """The sub-table the click path renders under the opened market row.

    Sliced out of the real render rather than rebuilt from the same helpers: an
    assertion against the whole row could be satisfied by the outer row's own
    badges, which is the confusion this round is about.
    """
    html = rendered["html"]
    start = html.index('class="orders-expand-row"')
    return html[start:html.index("</table>", start)]


def _render_expanded(kpi: dict, state: dict) -> str:
    """CLOSED TRADES with the settled market opened, as a click opens it."""
    return _expanded(_render("closed-trades", kpi, state, expand=[CID_SETTLED]))


@requires_node
def test_the_expanded_row_names_the_leg_it_belongs_to():
    # Arrange -- the "Outcome / Leg" column asked the payload for `token_side`
    # and `outcome`, which the server never sends, so it rendered BUY for every
    # row and the UP/DOWN colouring could never fire. The leg is resolvable from
    # the market's own quotes, which is how the Orders and Positions tables
    # already do it.
    kpi = _kpi()
    kpi["by_market"][CID_SETTLED]["quotes"] = [
        {"token_id": "tok-up", "side": "UP"},
        {"token_id": "tok-dn", "side": "DN"},
    ]
    state = _state()
    state["orders"] = [
        _settled_order("ord-up", "tok-up", pair_id="pair-s", price=0.95),
        _settled_order("ord-dn", "tok-dn", pair_id="pair-s", price=0.40),
    ]

    # Act
    rendered = _render("closed-trades", kpi, state, expand=[CID_SETTLED])
    expanded = _expanded(rendered)

    # Assert -- both legs named, and each pill coloured by its own leg.
    assert "UP · BUY" in expanded
    assert "DOWN · BUY" in expanded
    assert "badge-down" in expanded


def _settled_order(order_id: str, token: str, **over) -> dict:
    """One filled leg on the settled market, which is what the opened row
    shows. The market is a closed trade only while it has orders to expand."""
    order = {"id": order_id, "order_id": order_id, "condition_id": CID_SETTLED,
             "token_id": token, "side": "BUY", "price": 0.50,
             "original_size": 10.0, "size_matched": 10.0, "status": "filled",
             "display_status": "filled", "is_merged": False,
             "posted_ts": 900, "age_sec": 60.0}
    order.update(over)
    return order


@requires_node
def test_the_inner_table_says_shares_and_the_outer_says_events():
    # Arrange -- the outer row counted fill EVENTS while the single row inside
    # it counted SHARES, and both were captioned "Fills": the same word for two
    # units. Now each says its own unit.
    state = _state()
    state["orders"] = [_settled_order("ord-up", "tok-up", pair_id="pair-s")]

    # Act
    rendered = _render("closed-trades", _kpi(), state, expand=[CID_SETTLED])
    expanded = _expanded(rendered)

    # Assert -- the market row counts events, the legs count shares.
    assert "Fill events" in rendered["head"]
    assert "Size (sh)" in expanded
    assert "Filled (sh)" in expanded


@requires_node
def test_the_inner_status_says_what_it_means():
    # Arrange -- FILLED and MERGED sit in one column and are not alternatives:
    # FILLED is the order executing, MERGED is what happened to the shares next.
    state = _state()
    state["orders"] = [
        _settled_order("ord-f", "tok-up", pair_id="pair-f"),
        _settled_order("ord-m", "tok-dn", pair_id="pair-m",
                       display_status="merged", is_merged=True),
    ]

    # Act
    expanded = _render_expanded(_kpi(), state)

    # Assert
    assert ">FILLED<" in expanded
    assert ">MERGED<" in expanded
    assert "the shares are on the books" in expanded
    assert "merged back into $1.00 a share" in expanded


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


# ── Relative age beside the Timestamp ──────────────────────────────────

def _now_ms() -> float:
    """The JS-side wall clock the relative age reads, in milliseconds."""
    return __import__("time").time() * 1000


def _first_cell(html: str, marker: str) -> str:
    """The first <td> of the row whose markup follows `marker`.

    The Timestamp cell is the row's first cell in every view, so asserting
    against it alone cannot be satisfied by another caption or another `--`
    elsewhere in the row.
    """
    i = html.index(marker)
    row_start = html.rindex("<tr", 0, i)
    row = html[row_start:html.index("</tr>", i)]
    cell_start = row.index("<td") + 1
    cell = row[cell_start:row.index("</td>", cell_start)]
    return cell



@requires_node
def test_every_timestamp_cell_carries_a_relative_age_caption():
    # Arrange — the Timestamp column answers "when exactly"; the muted caption
    # under it answers "how long ago". `fmtRelAgo` reads the wall clock, so the
    # exact word is not assertable for real timestamps — but a PRESENT caption
    # inside the FIRST cell of a row with a measured time is.
    kpi, state = _kpi(), _state()
    # The base held-market fixture carries quotes but no fill rows; a Position
    # timestamps its FILL, so give it one (recent, in milliseconds) to assert
    # against. Quotes-at-ts-200 still date from 1970 and carry their own caption.
    kpi["by_market"][CID_HELD]["fills"] = [
        {"token_id": "tok-h-up", "venue_ts": _now_ms() - 120000},
    ]

    # Act / Assert — one caption inside the first cell of a market row.
    active = _render("active-markets", kpi, state)["html"]
    assert '<div class="caption-muted">' in _first_cell(active, CID_HELD)

    orders = _render("open-orders", kpi, state)["html"]
    assert '<div class="caption-muted">' in _first_cell(orders, "data-pair=")

    positions = _render("positions", kpi, state)["html"]
    assert '<div class="caption-muted">' in _first_cell(positions, CID_HELD)

    closed = _render("closed-trades", kpi, state)["html"]
    assert '<div class="caption-muted">' in _first_cell(closed, CID_SETTLED)


@requires_node
def test_a_row_with_no_timestamp_renders_no_age_caption():
    # Arrange — a market whose timestamps are all absent shows `--`; an age
    # caption under `--` would invent freshness nothing measured.
    kpi = _kpi()
    quiet = kpi["by_market"][CID_QUOTED]
    quiet["quotes"] = []
    quiet["fills"] = []
    quiet["settlements"] = []
    quiet["resolution"] = None

    # Act
    rendered = _render("active-markets", kpi, _state())

    # Assert — the Quoted Market row's FIRST cell is exactly the `--`
    # placeholder: no date, no age caption.
    html = rendered["html"]
    assert "Quoted Market" in html
    cell = _first_cell(html, CID_QUOTED)
    assert "caption-muted" not in cell
    assert "--" in cell


@requires_node
def test_the_age_caption_uses_the_table_muted_token():
    # Arrange — the caption must read as secondary text, styled from the same
    # token the rest of the table uses, not a hard-coded colour.
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")

    # Act
    block = css.split("#orders-trades-table td .caption-muted {")[1].split("}")[0]

    # Assert
    assert "var(--text-muted)" in block
    assert "font-size" in block


@requires_node
def test_a_position_with_quotes_but_no_fills_shows_no_timestamp():
    # Arrange — a held market whose quotes carry timestamps but whose fills
    # list is empty: the position's Timestamp is the FILL time, and no fill
    # time is measured, so the cell must read `--` — a quote time would dress
    # a quoting event up as an ownership event.
    kpi = _kpi()
    held = kpi["by_market"][CID_HELD]
    held["fills"] = []

    # Act
    rendered = _render("positions", kpi, None)

    # Assert — the held market's first cell has no date and no caption.
    cell = _first_cell(rendered["html"], CID_HELD)
    assert "caption-muted" not in cell
    assert "--" in cell


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


@requires_node
def test_active_markets_does_not_call_a_quoted_market_without_resting_orders_idle():
    # Arrange — issue #272: a market that is being quoted but whose orders have
    # not rested (or were cancelled/filled) is NOT idle. CID_HELD is quoted
    # (quotes_count=2) and holds only a filled and a cancelled order, so
    # nothing is resting on the book.
    rendered = _render("active-markets", _kpi(), _state())

    # Act
    held_row = rendered["html"].split("Held Market")[1].split("</tr>")[0]

    # Assert — active quoting, no resting order: QUOTING, never IDLE.
    assert "QUOTING" in held_row
    assert "IDLE" not in held_row


@requires_node
def test_active_markets_labels_a_market_with_resting_orders_resting():
    # Arrange — CID_QUOTED has both legs resting on the book; the more
    # specific state outranks plain QUOTING.
    rendered = _render("active-markets", _kpi(), _state())

    # Act
    quoted_row = rendered["html"].split("Quoted Market")[1].split("</tr>")[0]

    # Assert
    assert "RESTING" in quoted_row


@requires_node
def test_active_markets_excludes_a_zero_quote_market_with_only_a_filled_order():
    # Arrange — issue #272 (CodeRabbit round): a market with no quote activity
    # and only a filled order passes `isQuotedMarket` via its alive order, but
    # it has nothing active to show and would render IDLE — the exact state
    # the tab must not display.
    kpi = _kpi()
    kpi["by_market"][CID_HELD]["quotes_count"] = 0

    # Act
    rendered = _render("active-markets", kpi, _state())

    # Assert
    assert "Held Market" not in rendered["html"]
    assert rendered["rows"] == 1


@requires_node
def test_active_markets_status_header_explains_the_vocabulary():
    # Arrange — issue #272: the operator must be able to read what each
    # status means without leaving the table.
    # Act
    head = _render("active-markets", _kpi(), _state())["head"]

    # Assert
    head = _render("active-markets", _kpi(), _state())["head"]

    # Assert — the vocabulary must ride as a title attribute on the Status
    # <th>, not leak into the cell text as a literal `title="..."` string.
    status_th = next(th for th in head.split("<th")[1:] if ">Status<" in th)
    tag = status_th.split(">")[0]
    assert 'title="RESTING' in tag
    assert 'IDLE: no quote activity observed."' in tag
    assert "title=" not in status_th.split(">")[-1]



# ── Venue categories (issue #295) ────────────────────────────────────────────

@requires_node
def test_active_markets_shows_venue_categories_not_dashes():
    # Arrange — the two operator markets with their resolved categories.
    rendered = _render("active-markets", _category_kpi(), _category_state())

    # Act / Assert — the Category cell carries the label, never `--`.
    assert '<td class="mono">E-Sports</td>' in rendered["html"]
    assert '<td class="mono">Politics</td>' in rendered["html"]


@requires_node
def test_open_orders_and_positions_carry_category_captions():
    # Arrange
    orders = _render("open-orders", _category_kpi(), _category_state())
    positions = _render("positions", _category_kpi(), _category_state())

    # Act / Assert — the caption rides inside the row-spanning Market cell,
    # so the pair rows stay adjacent and the rowspan is untouched.
    assert '<div class="caption-muted">E-Sports</div>' in orders["html"]
    assert 'rowspan="2"' in orders["html"]
    assert '<div class="caption-muted">Politics</div>' in positions["html"]
    assert 'rowspan="2"' in positions["html"]


@requires_node
def test_closed_trades_carries_the_category_caption():
    # Arrange — a settled politics market with a booked profit is a trade.
    kpi = _category_kpi()
    kpi["by_market"][CID_POLITICS].update({
        "resolved": True, "total_sh": 0, "up_sh": 0, "dn_sh": 0,
        "realized_pnl": 0.5,
        "settlements": [{"method": "merge", "pnl": 0.5}],
    })
    rendered = _render("closed-trades", kpi, _category_state())

    # Act / Assert
    assert '<div class="caption-muted">Politics</div>' in rendered["html"]


# ── The close reason on a closed trade ──────────────────────────────────
# A closes row already carries `reason` -- the store writes it -- and the
# dashboard dropped it, so an aged-out rescue and a grace-expiry exit both read
# as a bare One-Sided row with a loss. Telling them apart meant opening the
# forensic report. The reason is provenance, not state, so it renders quietly.

def _with_settlements(*closes: dict, cid: str = CID_SETTLED) -> dict:
    kpi = _kpi()
    kpi["by_market"][cid]["settlements"] = list(closes)
    return kpi


@requires_node
def test_a_closed_trade_names_the_reason_it_closed_with():
    # Arrange -- the rescue exit from #311: a leg past the 900s window sold
    # before its market ended.
    kpi = _with_settlements({"method": "single_buy_exit", "pnl": -2.6,
                             "reason": "aged_out_rescue", "ts": 1788526463.0})

    # Act
    rendered = _render("closed-trades", kpi, _state())

    # Assert -- the label is what the operator reads, and the raw token stays
    # reachable beside it so the row can be matched to the registry and to
    # `scripts/rescue_exit_report.py` without a translation table.
    assert "close-reason-pill" in rendered["html"]
    assert "Aged-out rescue" in rendered["html"]
    assert "aged_out_rescue" in rendered["html"]


@requires_node
def test_a_close_that_carried_no_reason_shows_no_reason_chip():
    # Arrange -- a merge and a settlement book their close without one.
    kpi = _with_settlements({"method": "merge", "pnl": 1.21, "ts": 1788526463.0},
                            {"method": "shadow_settlement", "pnl": 0.4,
                             "ts": 1788526470.0})

    # Act
    rendered = _render("closed-trades", kpi, _state())

    # Assert
    assert "close-reason-pill" not in rendered["html"]


@requires_node
def test_a_later_close_without_a_reason_does_not_hide_the_named_one():
    # Arrange -- the rescue exit, then the settlement that followed it. The
    # settlement is newer but says nothing, and the row's PnL is the exit's.
    kpi = _with_settlements({"method": "single_buy_exit", "pnl": -2.6,
                             "reason": "aged_out_rescue", "ts": 1788526463.0},
                            {"method": "shadow_settlement", "pnl": -0.2,
                             "ts": 1788526499.0})

    # Act
    rendered = _render("closed-trades", kpi, _state())

    # Assert
    assert "Aged-out rescue" in rendered["html"]


@requires_node
def test_the_newest_named_close_wins_when_a_market_names_two():
    # Arrange -- an adverse-drift stop, then the grace expiry that finished it.
    kpi = _with_settlements({"method": "single_buy_exit", "pnl": -1.0,
                             "reason": "adverse_drift", "ts": 1788526400.0},
                            {"method": "single_buy_exit", "pnl": -0.4,
                             "reason": "grace_expired", "ts": 1788526500.0})

    # Act
    rendered = _render("closed-trades", kpi, _state())

    # Assert -- one chip, the newest reason, no pile-up of every close.
    assert "Grace expired" in rendered["html"]
    assert "Adverse drift" not in rendered["html"]
    assert rendered["html"].count("close-reason-pill") == 1


@requires_node
def test_a_reason_the_page_has_never_seen_still_renders_its_own_words():
    # Arrange -- a reason added on the Python side without touching this page.
    kpi = _with_settlements({"method": "single_buy_exit", "pnl": -1.0,
                             "reason": "collateral_sweep", "ts": 1788526463.0})

    # Act
    rendered = _render("closed-trades", kpi, _state())

    # Assert -- unreadable-in-practice beats invisible: the operator sees a
    # reason the page does not know rather than a row with no reason at all.
    assert "Collateral sweep" in rendered["html"]


@requires_node
@pytest.mark.parametrize("view", ["active-markets", "open-orders", "positions"])
def test_the_close_reason_stays_on_closed_trades(view):
    # Arrange -- the same market with a named close, read in the other views.
    kpi = _with_settlements({"method": "single_buy_exit", "pnl": -2.6,
                             "reason": "aged_out_rescue", "ts": 1788526463.0})

    # Act
    rendered = _render(view, kpi, _state())

    # Assert -- how a trade closed is not a property of an open position.
    assert "close-reason-pill" not in rendered["html"]


def test_the_close_reason_pill_stays_out_of_the_state_colour_vocabulary():
    # DESIGN.md: a hue may never appear without a state meaning behind it. How a
    # trade closed is provenance, so the chip lives in the quiet gray band.
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")
    start = css.index(".close-reason-pill {")
    rule = css[start:css.index("}", start)]

    assert "var(--text-muted)" in rule
    for hue in ("--signal", "--warn", "--loss", "--open",
                "--green-", "--amber-", "--red-", "--blue-"):
        assert hue not in rule


@requires_node
def test_unmatched_open_order_falls_back_to_the_pair_identity():
    # Arrange — resting order whose market left the KPI feed; the registry
    # pair identity still names it.
    state = {
        "orders": [
            {"order_id": "ord-ghost", "condition_id": CID_GHOST, "token_id": "tok-g",
             "pair_id": "pair-ghost", "side": "BUY", "price": 0.45,
             "original_size": 5.0, "size_matched": 0.0, "size_remaining": 5.0,
             "status": "open", "posted_ts": 1000, "age_sec": 90.0},
        ],
        "pairs": [
            {"pair_id": "pair-ghost", "condition_id": CID_GHOST,
             "market": {"condition_id": CID_GHOST, "title": "Ghost Market",
                        "slug": "ghost-market", "category": "Politics"}},
        ],
    }
    rendered = _render("open-orders", {"by_market": {}}, state)

    # Act / Assert — the identity title shows with its category caption.
    assert "Ghost Market" in rendered["html"]
    assert '<div class="caption-muted">Politics</div>' in rendered["html"]


def test_kpi_report_resolves_operator_slugs_without_a_feed(tmp_path, monkeypatch):
    """Python-level feed miss: closes name the market, keywords label it."""
    import sqlite3
    import time

    from core_brain import kpi as kpi_mod
    from core_brain.kpi import report
    from core_brain.order_registry import (
        SCHEMA, CloseRecord, OrderRegistry,
    )

    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "markets.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)

    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    reg = OrderRegistry(db_file)
    t0 = time.time() - 600
    run = "run-operator-slugs"
    reg.log_close(CloseRecord(
        ts=t0 + 60, condition_id="0xlol", market_slug="lol-kcb-wd-2026-09-26",
        method="merge", shares=5.0, cost_basis=4.70, proceeds=5.00,
        realized_pnl=0.30, tx_hash="0xaaa", run_id=run,
    ))
    reg.log_close(CloseRecord(
        ts=t0 + 180, condition_id="0xlula",
        market_slug=("will-luiz-incio-lula-da-silva-win-the-2026-"
                     "brazilian-presidential-election"),
        method="merge", shares=5.0, cost_basis=4.90, proceeds=5.00,
        realized_pnl=0.10, tx_hash="0xbbb", run_id=run,
    ))

    data = report(db_path=db_file, run_id=run)

    assert data["by_market"]["0xlol"]["category"] == "E-Sports"
    assert data["by_market"]["0xlula"]["category"] == "Politics"


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


def test_the_panel_is_wired_to_visible_poll_and_switch():
    # Arrange — this is a wiring check only: the panel must be reachable from
    # the visible-tab poll path and from the synchronous switch repaint. The
    # real paint behaviour lives in tests/test_dashboard_input_lag.py.
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


# ── Click-to-sort (issue #294) ───────────────────────────────────────────────
#
# The operator opens Orders & Trades to answer "which one" — the deepest
# queue, the costliest pair, the oldest order, the biggest loss — and the
# builders answer "what just happened" instead. Clicking a header has to
# answer the other question without breaking the row shape: Orders and OPEN
# POSITIONS render one row per leg with the pair's numbers in rowspan cells,
# and CLOSED TRADES renders a main row plus an optional expanded sub-row, so
# the sort has to re-order the backing groups BEFORE rows are paired into HTML.

CID_DEEP = "0xdeep"
CID_SHALLOW = "0xshallow"
CID_QUIET = "0xquiet"


def _sort_kpi() -> dict:
    """Three quoted markets whose numeric columns disagree with their names.

    Deliberately inverted: alphabetically `0xdeep` would sort last, by volume
    it is the largest, and its pair cost is the highest of the three. A sort
    that reads the rendered string instead of the underlying value cannot
    produce the expected order for any of these columns. The quote timestamps
    are inverted against the titles too — `Zeta Deep` quoted first, `Alpha
    Quiet` (no quotes) unmeasured — so a Timestamp sort is assertable as a
    real value order, not a constant.
    """
    return {
        "by_market": {
            CID_DEEP: {
                "condition_id": CID_DEEP, "title": "Zeta Deep", "category": "MLB",
                "days_to_resolve": 1.5, "volume_24h": 1000.0, "resolved": False,
                "quotes_count": 1, "up_sh": 0, "dn_sh": 0, "total_sh": 0,
                "total_cost": 0, "pair_cost": None, "realized_pnl": 0,
                "quotes": [
                    _quote("tk-d-up", "UP", 0.50, 100.0, "o-deep-up", 5000.0, price=0.60),
                    _quote("tk-d-dn", "DN", 0.50, 101.0, "o-deep-dn", 4800.0, price=0.50),
                ],
            },
            CID_SHALLOW: {
                "condition_id": CID_SHALLOW, "title": "Mid Shallow", "category": "NBA",
                "days_to_resolve": 10.0, "volume_24h": 95.0, "resolved": False,
                "quotes_count": 7, "up_sh": 0, "dn_sh": 0, "total_sh": 0,
                "total_cost": 0, "pair_cost": None, "realized_pnl": 0,
                "quotes": [
                    _quote("tk-s-up", "UP", 0.50, 500.0, "o-sh-up", 10.0, price=0.45),
                    _quote("tk-s-dn", "DN", 0.50, 501.0, "o-sh-dn", 20.0, price=0.48),
                ],
            },
            # No quotes at all: every price column is `--`. A column of
            # unmeasured cells must never outrank a measured one.
            CID_QUIET: {
                "condition_id": CID_QUIET, "title": "Alpha Quiet", "category": "LOL",
                "days_to_resolve": None, "volume_24h": 0.0, "resolved": False,
                "quotes_count": 1, "up_sh": 0, "dn_sh": 0, "total_sh": 0,
                "total_cost": 0, "pair_cost": None, "realized_pnl": 0,
                "quotes": [],
            },
        }
    }


def _sort_state() -> dict:
    return {
        "orders": [
            # CID_DEEP: the deepest queue on the book, but posted EARLIER than
            # the shallow pair. The default order (newest first) therefore puts
            # p-shallow ahead of it, so only a real sort on Queue Ahead can
            # produce p-deep first.
            {"order_id": "o-deep-up", "condition_id": CID_DEEP, "token_id": "tk-d-up",
             "pair_id": "p-deep", "side": "BUY", "price": 0.60, "original_size": 10.0,
             "size_matched": 0.0, "size_remaining": 10.0, "status": "open",
             "posted_ts": 100, "age_sec": 5.0},
            {"order_id": "o-deep-dn", "condition_id": CID_DEEP, "token_id": "tk-d-dn",
             "pair_id": "p-deep", "side": "BUY", "price": 0.50, "original_size": 10.0,
             "size_matched": 0.0, "size_remaining": 10.0, "status": "open",
             "posted_ts": 100, "age_sec": 5.0},
            # CID_SHALLOW: the newest pair on the book, with almost nothing
            # queued ahead of it.
            {"order_id": "o-sh-up", "condition_id": CID_SHALLOW, "token_id": "tk-s-up",
             "pair_id": "p-shallow", "side": "BUY", "price": 0.45, "original_size": 5.0,
             "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
             "posted_ts": 900, "age_sec": 900.0},
            {"order_id": "o-sh-dn", "condition_id": CID_SHALLOW, "token_id": "tk-s-dn",
             "pair_id": "p-shallow", "side": "BUY", "price": 0.48, "original_size": 5.0,
             "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
             "posted_ts": 900, "age_sec": 900.0},
        ]
    }


@requires_node
def test_no_sort_leaves_every_view_in_the_order_it_renders_today():
    # Arrange — the baseline each sorted view has to be distinguishable from.
    kpi, state = _kpi(), _state()

    # Act / Assert
    assert _cids(_render("active-markets", kpi, state)) == [CID_HELD, CID_QUOTED]
    assert _cids(_render("positions", kpi, state)) == [CID_HELD, CID_HELD]
    assert _cids(_render("closed-trades", kpi, state)) == [CID_SETTLED, CID_CLOSED]
    assert _pairs(_render("open-orders", kpi, state)) == ["pair-a", "pair-a"]


@requires_node
def test_sorting_a_numeric_column_ranks_by_the_value_not_the_rendered_text():
    # Arrange — 1000.00 must outrank 95.00 even though "1" < "9" as text.
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    rendered = _render("active-markets", kpi, state, sort={"col": 7, "dir": "desc"})

    # Assert — column 7 is 24h Volume.
    assert _cids(rendered) == [CID_DEEP, CID_SHALLOW, CID_QUIET]


@requires_node
def test_sorting_by_timestamp_ranks_by_the_quote_time():
    # Arrange — column 0 is Timestamp: the latest quote time per market. The
    # titles disagree with the quote times, so a constant or name-based sort
    # cannot produce this order. The unmeasured market ranks last in BOTH
    # directions, like every other unmeasured column.
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    desc = _render("active-markets", kpi, state, sort={"col": 0, "dir": "desc"})
    asc = _render("active-markets", kpi, state, sort={"col": 0, "dir": "asc"})

    # Assert — SHALLOW quoted at ts 500/501, DEEP at 100/101, QUIET never.
    assert _cids(desc) == [CID_SHALLOW, CID_DEEP, CID_QUIET]
    assert _cids(asc) == [CID_DEEP, CID_SHALLOW, CID_QUIET]


@requires_node
def test_a_text_column_sorts_ascending_on_the_market_name():
    # Arrange
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    rendered = _render("active-markets", kpi, state, sort={"col": 1, "dir": "asc"})

    # Assert — by name, not by markup and not by condition_id.
    assert _cids(rendered) == [CID_QUIET, CID_SHALLOW, CID_DEEP]


@requires_node
def test_an_unmeasured_cell_ranks_last_in_both_directions():
    # Arrange — CID_QUIET has no quotes, so every price column is `--`.
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    desc = _render("active-markets", kpi, state, sort={"col": 5, "dir": "desc"})
    asc = _render("active-markets", kpi, state, sort={"col": 5, "dir": "asc"})

    # Assert — pair cost: 1.10 and 0.93 are measured, `--` is not, and the
    # unmeasured row must not lead in either direction.
    assert _cids(desc)[-1] == CID_QUIET
    assert _cids(asc)[-1] == CID_QUIET



def _two_held_markets() -> dict:
    """Two held markets whose default order is the reverse of the sorted one.

    `heldMarketEntries` sorts by total_cost descending, so the costlier market is
    listed FIRST. Here the costlier market is also the one further underwater, so
    a sort that quietly did nothing would still look plausible unless the test
    names the whole sequence.

    BIG is a full pair, so both legs merge and its mark is exactly $1.00/share.
    SMALL holds a naked UP leg, marked at the UP mid. That is what separates the
    two Unrealized values -- a paired position cannot be underwater.
    """
    def held(cid, title, up_sh, dn_sh, up_cost, dn_cost):
        return {
            "condition_id": cid, "title": title, "category": "MLB",
            "days_to_resolve": 2.0, "volume_24h": 1000.0, "resolved": False,
            "quotes_count": 2, "up_sh": up_sh, "dn_sh": dn_sh,
            "total_sh": up_sh + dn_sh, "total_cost": up_cost + dn_cost,
            "pair_cost": 0.98, "realized_pnl": 0.0,
            "quotes": [_quote(cid + "-up", "UP", 0.60, 200.0),
                       _quote(cid + "-dn", "DN", 0.42, 201.0)],
        }
    # BIG: 10 UP + 10 DN, all paired -> mark 10*1.00 = 10.00, cost 12.00 -> -2.00
    # SMALL: 10 UP only, naked -> mark 10*0.60 = 6.00, cost 5.20 -> +0.80
    return {"by_market": {
        "0xposbig": held("0xposbig", "Alpha Big", 10, 10, 7.20, 4.80),
        "0xpossmall": held("0xpossmall", "Zulu Small", 10, 0, 5.20, 0.0),
    }}


@requires_node
def test_sorting_positions_reorders_two_held_markets_and_keeps_legs_adjacent():
    # Arrange - OPEN POSITIONS renders one row per held leg with the market name
    # and all three pair-level numbers spanning them, so a sort that re-ordered
    # the ROWS rather than the held markets would strand the rowspan cells.
    kpi = _two_held_markets()

    # Act - column 7 is Unrealized, in both directions. `sort` is the FOURTH
    # argument: passing it third hands it to `state` and silently renders the
    # unsorted view, which would make every assertion below vacuous.
    default = _render("positions", kpi)
    asc = _render("positions", kpi, None, sort={"col": 7, "dir": "asc"})
    desc = _render("positions", kpi, None, sort={"col": 7, "dir": "desc"})

    # Assert - BIG is the loser (-2.00, a full pair marked at $1.00/share) and
    # SMALL is in the black (+0.80, a naked UP leg marked at the 0.60 mid), so
    # ascending puts the loser first and descending puts the winner first. Both
    # differ from the default cost-descending order, and the legs of each market
    # stay adjacent with one market cell and three pair-level numbers on the
    # first row of the pair.
    assert _cids(default) == ["0xposbig", "0xposbig", "0xpossmall"]
    assert _cids(asc) == ["0xposbig", "0xposbig", "0xpossmall"]
    assert _cids(desc) == ["0xpossmall", "0xposbig", "0xposbig"]
    assert desc["html"].count('class="ot-pair-row ot-pair-start') == 2
    assert desc["html"].count('class="ot-market" rowspan=') == 2
    assert desc["html"].count('ot-pair-value" rowspan=') == 6


@requires_node
def test_sorting_positions_keeps_the_pair_banding_alternating_in_rendered_order():
    # Arrange - the banding is what makes two rows read as ONE pair. Computed
    # from the pre-sort index it desynchronises from the order on screen, so
    # adjacent pairs share a background and the pair boundary is lost.
    kpi = _two_held_markets()

    # Act - descending, which is the direction that actually reorders these two.
    rendered = _render("positions", kpi, None, sort={"col": 7, "dir": "desc"})

    # Assert - each market's first row alternates, in the order rendered: the
    # market that moved to first is the one that now carries the band.
    banded = re.findall(r'class="([^"]*ot-pair-start[^"]*)"', rendered["html"])
    assert len(banded) == 2
    assert ("ot-pair-alt" in banded[0]) != ("ot-pair-alt" in banded[1])


@requires_node
def test_sorting_closed_trades_reorders_trades_against_the_default_order():
    # Arrange — CLOSED TRADES reuses the Data & Markets row shape, so a trade
    # renders as a main row plus an optional expanded sub-row as ONE string.
    # Sorting the entries keeps each trade whole; sorting the rows would not.
    kpi, state = _kpi(), _state()

    # Act — column 3 is Realized P&L; ascending puts the losing trade first,
    # which is the opposite of the order the view renders by default.
    default = _render("closed-trades", kpi, state)
    rendered = _render("closed-trades", kpi, state, sort={"col": 3, "dir": "asc"})

    # Assert
    assert _cids(default) == [CID_SETTLED, CID_CLOSED]
    assert _cids(rendered) == [CID_CLOSED, CID_SETTLED]


@requires_node
def test_every_column_header_is_a_real_button_the_keyboard_can_reach():
    # Arrange
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    head = _render("active-markets", kpi, state)["head"]

    # Assert — one button per column, and a native button turns Enter and
    # Space into a click without any key handling of its own.
    assert head.count('<button type="button"') == 11


@requires_node
def test_exactly_one_header_carries_the_direction_indicator():
    # Arrange
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    head = _render("active-markets", kpi, state, sort={"col": 0, "dir": "desc"})["head"]

    # Assert — aria-sort on the sorted <th> only, and it names the direction.
    assert head.count("aria-sort=") == 1
    assert 'aria-sort="descending"' in head
    assert "aria-sort=" not in head.replace('aria-sort="descending"', "")


@requires_node
def test_the_sorted_column_says_its_direction_in_text_and_not_only_in_an_arrow():
    # Arrange — the direction must not be carried by the arrow glyph alone.
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    head = _render("active-markets", kpi, state, sort={"col": 0, "dir": "desc"})["head"]

    # Assert — the button carries a visually-hidden direction word.
    assert "descending" in head
    assert "aria-hidden" in head

@requires_node
def test_the_sortable_header_has_hover_focus_and_a_pointer_without_new_colours():
    # Arrange — issue #294: the header has to read as a control before it is
    # clicked, and every affordance has to come from the existing tokens.
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")
    block = css.split(".ot-sort-btn {")[1].split("}")[0]
    hover = css.split(".ot-sort-btn:hover {")[1].split("}")[0]
    focus = css.split(".ot-sort-btn:focus-visible {")[1].split("}")[0]

    # Assert
    assert "cursor: pointer" in block
    assert "var(--border-strong)" in hover
    assert "var(--text-primary)" in hover
    assert ":focus-visible" in css
    assert "outline: 2px solid var(--open)" in focus

    # No hard-coded colour in any spelling: `#abc`, `#aabbcc`, `rgb()`,
    # `rgba()`, `hsl()`. Checking only `#` hexes would have passed while the
    # hover rule shipped a literal `rgba(255, 255, 255, 0.05)`. Comments are
    # stripped first: an issue reference like `#277` is not a colour.
    hard_coded = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    for name, rule in (("base", block), ("hover", hover), ("focus", focus)):
        declarations = re.sub(r"/\*.*?\*/", "", rule, flags=re.S)
        found = hard_coded.search(declarations)
        assert found is None, f"hard-coded colour in the sortable header ({name}): {found.group(0)}"


@requires_node
def test_the_direction_is_announced_once_not_twice():
    # Arrange — `aria-sort` on the <th> already carries the direction. A second
    # copy inside the button makes a screen reader say it twice per column.
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    head = _render("active-markets", kpi, state, sort={"col": 0, "dir": "desc"})["head"]

    # Assert — the direction word rides on the <th> only, and the arrow stays
    # aria-hidden decoration that no assistive tech reads.
    body = head.split(">")[2]
    assert "descending" not in body
    assert 'aria-sort="descending"' in head
    assert head.count("descending") == 1


@requires_node
def test_a_garbage_column_index_does_not_silently_disable_sorting():
    # Arrange — a NaN column passes `typeof x === 'number'`, matches no
    # accessor case, and degrades every sort into a no-op with no indicator.
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    rendered = _render("active-markets", kpi, state, sort={"col": "nonsense", "dir": "desc"})

    # Assert — the guard rejects it, so no header claims to be sorted.
    assert "aria-sort=" not in rendered["head"]


@requires_node
def test_sorting_open_orders_reorders_pairs_without_splitting_the_legs():
    # Arrange — the pair is the unit. Sorting its rows instead of its groups
    # would strand the rowspan market cell and the pair tags on the wrong row.
    kpi, state = _sort_kpi(), _sort_state()

    # Act — column 6 is Queue Ahead, the operator's "which order is stuck" question.
    rendered = _render("open-orders", kpi, state, sort={"col": 6, "dir": "desc"})

    # Assert — the deepest queue first, even though that pair is the older one
    # and therefore second in the view's default newest-first order.
    assert _pairs(_render("open-orders", kpi, state)) == ["p-shallow", "p-shallow",
                                                          "p-deep", "p-deep"]
    assert _pairs(rendered) == ["p-deep", "p-deep", "p-shallow", "p-shallow"]
    # UP still above DN inside each pair, and the first row of each pair is the
    # one carrying the rowspan market cell.
    assert _order_ids(rendered) == ["o-deep-up", "o-deep-dn", "o-sh-up", "o-sh-dn"]
    assert rendered["html"].count('class="ot-pair-row ot-pair-start') == 2
    # The rowspan cell rides on the first row of each pair, once per pair.
    assert rendered["html"].count('class="ot-market" rowspan=') == 2

@requires_node
def test_sorting_open_orders_by_total_cost_ranks_pairs_by_the_sum_of_their_legs():
    # Arrange — Total Cost is the pair's build cost, so it is the SUM of the
    # legs. CID_DEEP is 10*0.60 + 10*0.50 = 11.00; CID_SHALLOW is
    # 5*0.45 + 5*0.48 = 4.65, and it is the default order too, so descending
    # has to be checked against a genuinely different order than the baseline.
    kpi, state = _sort_kpi(), _sort_state()

    # Act
    rendered = _render("open-orders", kpi, state, sort={"col": 5, "dir": "asc"})

    # Assert — ascending puts the cheaper pair first, and every row still renders.
    assert _pairs(rendered) == ["p-shallow", "p-shallow", "p-deep", "p-deep"]
    assert rendered["html"].count('class="ot-market" rowspan=') == 2


@requires_node
def test_every_open_orders_column_sorts_without_throwing():
    # Arrange — a column whose accessor references a name it does not have
    # throws on every render, and the view silently stops updating. This walks
    # every column of the view so a broken accessor cannot hide.
    kpi, state = _sort_kpi(), _sort_state()

    for col in range(8):
        for direction in ("asc", "desc"):
            rendered = _render("open-orders", kpi, state,
                               sort={"col": col, "dir": direction})
            # Two pairs, two legs each, every one of them present.
            assert _pairs(rendered) == ["p-deep", "p-deep", "p-shallow", "p-shallow"] \
                or _pairs(rendered) == ["p-shallow", "p-shallow", "p-deep", "p-deep"]





def _clicks(steps: list[dict]) -> dict:
    """Drive the click path itself: each step is one header click."""
    out = subprocess.run([shutil.which("node"), str(HARNESS),
                          json.dumps({"kpi": _sort_kpi(), "state": _sort_state(),
                                      "toggle": steps})],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


@requires_node
def test_the_first_click_on_a_money_column_sorts_descending():
    # Arrange — the operator's question is "which is biggest", not "which comes
    # first alphabetically", so every non-text column starts descending.
    # Act
    result = _clicks([{"view": "active-markets", "col": 7}])

    # Assert — column 7 is 24h Volume, and the head shows the direction.
    assert result["toggles"][0] == {"col": 7, "dir": "desc"}
    assert 'aria-sort="descending"' in result["heads"][0]


@requires_node
def test_the_first_click_on_a_text_column_sorts_ascending():
    # Arrange / Act
    result = _clicks([{"view": "active-markets", "col": 1}])

    # Assert — the Market column is the exception: its question is "which name
    # comes first", so it starts ascending.
    assert result["toggles"][0] == {"col": 1, "dir": "asc"}
    assert 'aria-sort="ascending"' in result["heads"][0]


@requires_node
def test_clicking_the_sorted_column_again_flips_the_direction():
    # Act — the same header twice.
    result = _clicks([{"view": "active-markets", "col": 7},
                      {"view": "active-markets", "col": 7}])

    # Assert
    assert result["toggles"] == [{"col": 7, "dir": "desc"}, {"col": 7, "dir": "asc"}]
    assert 'aria-sort="ascending"' in result["heads"][1]


@requires_node
def test_clicking_a_different_column_starts_it_fresh():
    # Act — switch columns rather than flipping the previous one.
    result = _clicks([{"view": "active-markets", "col": 7},
                      {"view": "active-markets", "col": 1}])

    # Assert — the new column starts on its own natural direction, it does not
    # inherit the direction the operator last used elsewhere.
    assert result["toggles"][1] == {"col": 1, "dir": "asc"}


@requires_node
def test_sorting_one_view_does_not_disturb_another_views_sort():
    # Arrange — the operator sorts Orders, goes to look at Positions, and comes
    # back. Each view has to still be sorted the way they left it.
    # Act
    result = _clicks([{"view": "open-orders", "col": 6},
                      {"view": "positions", "col": 7},
                      {"view": "open-orders", "col": 6}])

    # Assert — Orders is still on Queue Ahead descending, Positions kept its own
    # Unrealized sort, and the last Orders click merely flipped it.
    assert result["toggles"] == [{"col": 6, "dir": "desc"},
                                 {"col": 7, "dir": "desc"},
                                 {"col": 6, "dir": "asc"}]
    assert 'aria-sort="ascending"' in result["heads"][2]



