"""Taker fees recovered from the venue's own rows (#125).

`kpi.taker_fees_paid` sums a `fee` column this process wrote, so it reports what
we believed at close time and nothing else. The venue states the fee implicitly
on every trade row: `usdcSize` is not `size × price`, and the residual is the
fee — added on a BUY, deducted on a SELL.

Nothing here models a rate. Fees did not exist before late June 2026 and stepped
0.03 → 0.05 → 0.07 afterwards, so a rate assumed over old activity would invent
charges that were never made. The residual is measured instead.
"""
from __future__ import annotations

import urllib.error

import pytest

from core_brain.venue_fees import (
    ACTIVITY_PAGE_SIZE,
    FeeTotal,
    fees_from_rows,
    format_report,
    lifetime_taker_fees,
    recover_fee,
)

FUNDER = "0x00000000000000000000000000000000000000f1"


def _row(side: str, size: float, price: float, fee: float) -> dict:
    """A venue trade row carrying `fee` implicitly, the way the API does."""
    notional = size * price
    usdc = notional + fee if side == "BUY" else notional - fee
    return {"side": side, "size": size, "price": price, "usdcSize": usdc}


def test_a_buy_adds_the_fee_to_the_notional():
    # Arrange — 100 shares at $0.42, $0.35 charged.
    row = _row("BUY", 100.0, 0.42, 0.35)

    # Act / Assert
    assert recover_fee(row) == pytest.approx(0.35)


def test_v2_snake_case_usdc_size_is_recovered():
    row = {"side": "BUY", "size": 100.0, "price": 0.42, "usdc_size": 42.35}
    assert recover_fee(row) == pytest.approx(0.35)


def test_a_sell_deducts_the_fee_from_the_notional():
    # Arrange
    row = _row("SELL", 100.0, 0.58, 0.21)

    # Act / Assert
    assert recover_fee(row) == pytest.approx(0.21)


def test_a_free_era_row_recovers_exactly_zero():
    # Arrange — before late June 2026 there were no fees at all. A rounding
    # artefact here would put a fabricated fraction of a cent on every one.
    row = _row("BUY", 250.0, 0.31, 0.0)

    # Act / Assert
    assert recover_fee(row) == 0.0


def test_sub_cent_rounding_is_not_reported_as_a_fee():
    # Arrange — the venue's own rounding, not a charge.
    row = {"side": "BUY", "size": 10.0, "price": 0.5, "usdcSize": 5.0001}

    # Act / Assert
    assert recover_fee(row) == 0.0


def test_a_row_without_a_side_is_unmeasurable_not_free():
    # Arrange — without the side the residual's sign is unreadable, and
    # guessing it turns a charge into a rebate.
    row = {"size": 10.0, "price": 0.5, "usdcSize": 5.35}

    # Act / Assert
    assert recover_fee(row) is None


def test_a_row_missing_a_field_is_unmeasurable():
    # Arrange / Act / Assert
    assert recover_fee({"side": "BUY", "size": 10.0}) is None
    assert recover_fee({"side": "BUY", "price": 0.5, "usdcSize": 5.0}) is None
    assert recover_fee("not-a-row") is None


def test_a_negative_residual_is_refused_rather_than_read_as_a_discount():
    # Arrange — the venue does not charge a negative taker fee, so this is a
    # row we are misreading.
    row = {"side": "BUY", "size": 10.0, "price": 0.5, "usdcSize": 4.0}

    # Act / Assert
    assert recover_fee(row) is None


def test_unmeasurable_rows_are_counted_but_not_summed():
    # Arrange
    rows = [_row("BUY", 100.0, 0.42, 0.35),
            {"size": 10.0, "price": 0.5, "usdcSize": 5.35},
            _row("SELL", 50.0, 0.60, 0.10)]

    # Act
    total, seen, priced = fees_from_rows(rows)

    # Assert
    assert total == pytest.approx(0.45)
    assert seen == 3
    assert priced == 2


def test_a_lifetime_walk_totals_every_page():
    # Arrange — two full pages and a short one, which ends the walk.
    pages = {
        0: [_row("BUY", 1.0, 0.50, 0.01)] * ACTIVITY_PAGE_SIZE,
        ACTIVITY_PAGE_SIZE: [_row("SELL", 1.0, 0.50, 0.02)] * ACTIVITY_PAGE_SIZE,
        2 * ACTIVITY_PAGE_SIZE: [_row("BUY", 1.0, 0.50, 0.03)],
    }

    # Act
    total = lifetime_taker_fees(FUNDER, fetch=lambda offset: pages.get(offset, []))

    # Assert
    assert total.complete is True
    assert total.rows == 2 * ACTIVITY_PAGE_SIZE + 1
    assert total.fees_usd == pytest.approx(
        ACTIVITY_PAGE_SIZE * 0.01 + ACTIVITY_PAGE_SIZE * 0.02 + 0.03)


def test_a_walk_stopped_at_the_row_cap_says_it_is_incomplete():
    # Arrange — the feed repeats rather than ending past the cap, so a total
    # taken from it is understated and must not read as a lifetime figure.
    page = [_row("BUY", 1.0, 0.50, 0.01)] * ACTIVITY_PAGE_SIZE

    # Act
    total = lifetime_taker_fees(FUNDER, row_cap=ACTIVITY_PAGE_SIZE,
                                fetch=lambda offset: page)

    # Assert
    assert total.complete is False
    assert total.rows == ACTIVITY_PAGE_SIZE


def test_a_first_read_that_fails_reports_nothing_rather_than_zero():
    # Arrange — an unreachable endpoint must not read as "paid no fees".
    def _boom(offset):
        raise urllib.error.URLError("data api unreachable")

    # Act / Assert
    assert lifetime_taker_fees(FUNDER, fetch=_boom) is None


def test_a_page_that_fails_mid_walk_truncates_rather_than_lying():
    # Arrange
    page = [_row("BUY", 1.0, 0.50, 0.01)] * ACTIVITY_PAGE_SIZE

    def _fetch(offset):
        if offset == 0:
            return page
        raise OSError("connection reset")

    # Act
    total = lifetime_taker_fees(FUNDER, fetch=_fetch)

    # Assert
    assert total.complete is False
    assert total.fees_usd == pytest.approx(ACTIVITY_PAGE_SIZE * 0.01)


def test_an_unset_funder_is_not_a_wallet_with_no_fees():
    # Arrange / Act / Assert
    assert lifetime_taker_fees("") is None


def test_the_result_serialises_for_a_report():
    # Arrange
    total = FeeTotal(fees_usd=1.23456789, rows=10, priced_rows=9, complete=True)

    # Act
    payload = total.as_dict()

    # Assert
    assert payload == {"fees_usd": 1.234568, "rows": 10,
                       "priced_rows": 9, "complete": True}


def test_the_report_never_prints_an_unread_total_as_zero():
    # Arrange / Act
    text = format_report(None, FUNDER)

    # Assert — the difference between "measured zero" and "did not measure" is
    # the whole point, so the text says which one this is.
    assert "UNREAD" in text
    assert "not $0.00" in text


def test_the_report_flags_a_truncated_walk():
    # Arrange
    total = FeeTotal(fees_usd=12.5, rows=4000, priced_rows=3990, complete=False)

    # Act
    text = format_report(total, FUNDER)

    # Assert
    assert "TRUNCATED" in text
    assert "10 (counted, never summed)" in text


def test_the_row_cap_is_applied_before_the_page_is_totalled():
    # Arrange — a 750-row allowance against 500-row pages. Counting the second
    # page whole and then noticing the cap would total 1,000 rows the cap says
    # not to trust.
    page = [_row("BUY", 1.0, 0.50, 0.01)] * ACTIVITY_PAGE_SIZE

    # Act
    total = lifetime_taker_fees(FUNDER, row_cap=750, fetch=lambda offset: page)

    # Assert
    assert total.rows == 750
    assert total.fees_usd == pytest.approx(750 * 0.01)
    assert total.complete is False


def test_a_response_that_is_not_a_list_is_not_the_end_of_the_feed():
    # Arrange — an error object read as "no more rows" would end the walk
    # early and report the short total as complete.
    page = [_row("BUY", 1.0, 0.50, 0.01)] * ACTIVITY_PAGE_SIZE

    def _fetch(offset):
        return page if offset == 0 else {"error": "rate limited"}

    # Act
    total = lifetime_taker_fees(FUNDER, fetch=_fetch)

    # Assert
    assert total.complete is False
    assert total.rows == ACTIVITY_PAGE_SIZE


def test_a_first_response_that_is_not_a_list_reports_nothing():
    # Arrange / Act / Assert
    assert lifetime_taker_fees(FUNDER, fetch=lambda offset: {"error": "nope"}) is None


def test_v2_activity_envelope_in_lifetime_taker_fees():
    # Arrange — Data API v2 envelope with data array and pagination
    rows = [{"side": "BUY", "size": 10.0, "price": 0.50, "usdc_size": 5.05}]
    envelope = {"data": rows, "pagination": {"next_cursor": None}}

    # Act
    total = lifetime_taker_fees(FUNDER, fetch=lambda offset: envelope)

    # Assert
    assert total is not None
    assert total.complete is True
    assert total.fees_usd == pytest.approx(0.05)


def test_v2_activity_two_pages_linked_by_cursor():
    pages = [
        {"data": [{"side": "BUY", "size": 10.0, "price": 0.50, "usdc_size": 5.05}], "pagination": {"next_cursor": "c2"}},
        {"data": [{"side": "BUY", "size": 10.0, "price": 0.50, "usdc_size": 5.05}], "pagination": {"next_cursor": None}},
    ]
    calls = []

    def _fetch(offset):
        calls.append(offset)
        return pages[len(calls) - 1]

    total = lifetime_taker_fees(FUNDER, fetch=_fetch)
    assert total is not None
    assert total.complete is True
    assert total.rows == 2
    assert total.fees_usd == pytest.approx(0.10)


def test_v2_activity_repeated_cursor_marks_incomplete():
    pages = [
        {"data": [{"side": "BUY", "size": 10.0, "price": 0.50, "usdc_size": 5.05}], "pagination": {"next_cursor": "loop"}},
        {"data": [{"side": "BUY", "size": 10.0, "price": 0.50, "usdc_size": 5.05}], "pagination": {"next_cursor": "loop"}},
    ]
    calls = []

    def _fetch(offset):
        calls.append(offset)
        return pages[min(len(calls) - 1, len(pages) - 1)]

    total = lifetime_taker_fees(FUNDER, fetch=_fetch)
    assert total is not None
    assert total.complete is False
    assert total.rows == 1


def test_float_noise_below_zero_normalises_rather_than_refusing():
    # Arrange — the arithmetic can land a hair under zero on an exact-fee row.
    row = {"side": "BUY", "size": 3.0, "price": 0.1, "usdcSize": 0.3 - 1e-12}

    # Act / Assert — that is a zero fee, not an unreadable row.
    assert recover_fee(row) == 0.0
