"""The registry's PnL, checked against the venue's own record (#123).

Every KPI number is computed from `data/orders.db`, so a systematic registry
error is invisible: every figure agrees with every other figure and all of them
are wrong. The check has to come from a path that never reads the registry —
the wallet's activity feed, replayed as cashflow.

What is tested here is mostly the refusals. A check that rounds "I could not
read that" up to "it agrees" is worse than no check.
"""
from __future__ import annotations

import urllib.error

import pytest

from core_brain.pnl_crosscheck import (
    ACTIVITY_PAGE_SIZE,
    ACTIVITY_TYPES,
    ASC_TYPES,
    CREDIT_TYPES,
    Replay,
    cross_check,
    format_report,
    replay_cashflow,
    replay_rows,
)

FUNDER = "0x00000000000000000000000000000000000000f1"


def _trade(side: str, usdc: float) -> dict:
    return {"type": "TRADE", "side": side, "usdcSize": usdc}


def _event(kind: str, usdc: float) -> dict:
    return {"type": kind, "usdcSize": usdc}


def test_buys_and_splits_spend_while_sells_merges_and_redeems_return():
    # Arrange — one of each, all $10.
    rows = [_trade("BUY", 10.0), _event("SPLIT", 10.0),
            _trade("SELL", 10.0), _event("MERGE", 10.0), _event("REDEEM", 10.0)]

    # Act
    trading, credits, seen, unreadable = replay_rows(rows)

    # Assert — two out, three in.
    assert trading == pytest.approx(10.0)
    assert credits == 0.0
    assert seen == 5
    assert unreadable == 0


def test_platform_credits_are_kept_out_of_trading_cashflow():
    # Arrange — the headline figure has to be reproducible from trades alone.
    rows = [_trade("SELL", 5.0), _event("REWARD", 2.0), _event("MAKER_REBATE", 1.0)]

    # Act
    trading, credits, _, _ = replay_rows(rows)

    # Assert
    assert trading == pytest.approx(5.0)
    assert credits == pytest.approx(3.0)


def test_bare_rebate_is_not_a_known_type():
    # Arrange — bare REBATE is deliberately absent: the documented /activity
    # type enum has no bare REBATE value, so the replay must not know it.
    rows = [_trade("SELL", 5.0), _event("REBATE", 1.0)]

    # Act
    trading, credits, seen, unreadable = replay_rows(rows)

    # Assert — the row is counted, not guessed, and never credited.
    assert trading == pytest.approx(5.0)
    assert credits == 0.0
    assert seen == 2
    assert unreadable == 1


def test_bare_rebate_with_surrounding_whitespace_is_not_a_known_type():
    # Arrange — the classifier strips and uppercases, so " rebate " takes
    # the same path as "REBATE" and must end up equally unknown.
    rows = [_event(" rebate ", 1.0)]

    # Act
    trading, credits, seen, unreadable = replay_rows(rows)

    # Assert
    assert trading == 0.0
    assert credits == 0.0
    assert seen == 1
    assert unreadable == 1


def test_bare_rebate_in_feed_makes_replay_incomplete(monkeypatch):
    # Arrange — a bare REBATE row smuggled inside another type's walk.
    from core_brain import pnl_crosscheck as pc

    def _get_json(path, params, timeout):
        return [{"type": "REBATE", "usdcSize": 1.0}]

    monkeypatch.setattr(pc, "_get_json", _get_json)

    # Act
    replay = replay_cashflow(FUNDER, open_value_fn=lambda: 0.0,
                             types=("REWARD",))

    # Assert — loud and incomplete, adding nothing to inclusive PnL.
    assert replay.credits == 0.0
    assert replay.unreadable_rows == 1
    assert replay.complete is False
    assert replay.pnl_inclusive == replay.pnl


def test_credit_types_are_all_requested():
    # Arrange — the subset invariant: every credit type must also be a
    # requested type, so a credit can never go missing from the cashflow.
    # Act / Assert
    assert "REBATE" not in CREDIT_TYPES
    assert set(CREDIT_TYPES) <= set(ACTIVITY_TYPES)


def test_remaining_credit_types_still_count_as_credits():
    # Arrange — the survivors, positive and negative (absolute-value rule).
    # Act / Assert
    for kind in ("MAKER_REBATE", "REWARD", "REFERRAL_REWARD", "CONVERSION"):
        assert replay_rows([_event(kind, 2.0)]) == (0.0, 2.0, 1, 0)
        assert replay_rows([_event(kind, -2.0)]) == (0.0, 2.0, 1, 0)


def test_default_request_vocabulary(monkeypatch):
    # Arrange — the replay asks for exactly its documented vocabulary.
    from core_brain import pnl_crosscheck as pc

    requests: list[dict] = []

    def _get_json(path, params, timeout):
        requests.append(dict(params))
        return []

    monkeypatch.setattr(pc, "_get_json", _get_json)

    # Act
    replay_cashflow(FUNDER, open_value_fn=lambda: 0.0)

    # Assert — literal set, not the production constant, so the decision
    # is preserved independently of the code under test.
    requested = [r["type"] for r in requests]
    assert set(requested) == {"TRADE", "MERGE", "SPLIT", "REDEEM", "REWARD",
                              "MAKER_REBATE", "REFERRAL_REWARD", "CONVERSION"}
    assert len(requested) == len(set(requested))
    assert "REBATE" not in requested


def test_a_row_whose_direction_cannot_be_read_is_counted_not_guessed():
    # Arrange — a trade with no side moves cash in an unknown direction.
    rows = [{"type": "TRADE", "usdcSize": 10.0}, _trade("BUY", 4.0)]

    # Act
    trading, _, seen, unreadable = replay_rows(rows)

    # Assert
    assert trading == pytest.approx(-4.0)
    assert seen == 2
    assert unreadable == 1


def test_merge_and_split_are_requested_oldest_first(monkeypatch):
    # Arrange — under the default DESC ordering the venue drops rows of these
    # two types silently, which under-counts exactly the events this strategy
    # lives on. Driven through the real reader so the request itself is what
    # gets asserted, not a stand-in for it.
    from core_brain import pnl_crosscheck as pc

    requests: list[dict] = []

    def _get_json(path, params, timeout):
        requests.append(dict(params))
        return []

    monkeypatch.setattr(pc, "_get_json", _get_json)

    # Act
    replay_cashflow(FUNDER, open_value_fn=lambda: 0.0,
                    types=("MERGE", "SPLIT", "TRADE"))

    # Assert
    by_type = {r["type"]: r for r in requests}
    assert by_type["MERGE"]["sortDirection"] == "ASC"
    assert by_type["SPLIT"]["sortDirection"] == "ASC"
    assert "sortDirection" not in by_type["TRADE"]


def test_an_unvalued_book_yields_no_venue_pnl():
    # Arrange — an unvalued book is not an empty one. Treating it as zero would
    # report every open pair as a total loss.
    replay = Replay(trading_cashflow=-10.0, open_value=None)

    # Act / Assert
    assert replay.pnl is None
    assert replay.pnl_inclusive is None


def test_venue_pnl_is_cashflow_plus_what_is_still_open():
    # Arrange
    replay = Replay(trading_cashflow=-10.0, credits=0.5, open_value=12.0)

    # Act / Assert
    assert replay.pnl == pytest.approx(2.0)
    assert replay.pnl_inclusive == pytest.approx(2.5)


def test_a_feed_that_cannot_be_read_reports_nothing(monkeypatch):
    # Arrange
    def _boom(kind, offset):
        raise urllib.error.URLError("data api unreachable")

    # Act / Assert — not a wallet that never traded.
    assert replay_cashflow(FUNDER, fetch=_boom, open_value_fn=lambda: 0.0) is None


def test_a_truncated_type_is_named_and_the_walk_is_incomplete():
    # Arrange — the feed repeats rather than ending past the cap.
    page = [_trade("BUY", 1.0)] * ACTIVITY_PAGE_SIZE

    def _fetch(kind, offset):
        return page if kind == "TRADE" else []

    # Act
    replay = replay_cashflow(FUNDER, row_cap=ACTIVITY_PAGE_SIZE, fetch=_fetch,
                             open_value_fn=lambda: 0.0,
                             types=("TRADE", "MERGE"))

    # Assert
    assert replay.complete is False
    assert replay.incomplete_types == ("TRADE",)


def test_the_gap_is_judged_against_fees_not_against_zero():
    # Arrange — the registry books pre-fee, the wallet nets post-fee, so a gap
    # of exactly the fees is agreement, not a discrepancy.
    replay = Replay(trading_cashflow=-8.0, open_value=10.0)  # venue PnL 2.00

    # Act
    check = cross_check(registry_pnl=2.35, replay=replay, taker_fees=0.35)

    # Assert
    assert check.gap == pytest.approx(0.35)
    assert check.residual == pytest.approx(0.0)
    assert check.explained is True


def test_a_gap_fees_do_not_explain_is_reported_unexplained():
    # Arrange — this is the registry error the check exists to catch.
    replay = Replay(trading_cashflow=-8.0, open_value=10.0)

    # Act
    check = cross_check(registry_pnl=9.00, replay=replay, taker_fees=0.35)

    # Assert
    assert check.explained is False
    assert check.residual == pytest.approx(6.65)


def test_without_a_fee_figure_the_gap_is_reported_but_not_judged():
    # Arrange
    replay = Replay(trading_cashflow=-8.0, open_value=10.0)

    # Act
    check = cross_check(registry_pnl=2.35, replay=replay, taker_fees=None)

    # Assert
    assert check.gap == pytest.approx(0.35)
    assert check.explained is None


def test_an_incomplete_walk_is_never_explained():
    # Arrange — a short venue total can match the registry by coincidence, and
    # calling that agreement is the failure this module exists to avoid.
    replay = Replay(trading_cashflow=-8.0, open_value=10.0,
                    incomplete_types=("MERGE",))

    # Act
    check = cross_check(registry_pnl=2.00, replay=replay, taker_fees=0.0)

    # Assert
    assert check.gap == pytest.approx(0.0)
    assert check.explained is None


def test_maker_rebates_reduce_the_expected_gap():
    # Arrange
    replay = Replay(trading_cashflow=-8.0, open_value=10.0)

    # Act
    check = cross_check(registry_pnl=2.20, replay=replay,
                        taker_fees=0.35, maker_rebates=0.15)

    # Assert
    assert check.expected_gap == pytest.approx(0.20)
    assert check.explained is True


def test_the_report_leads_with_what_was_not_measured():
    # Arrange
    replay = Replay(trading_cashflow=-8.0, open_value=10.0,
                    incomplete_types=("SPLIT",))
    check = cross_check(registry_pnl=2.35, replay=replay, taker_fees=0.35)

    # Act
    text = format_report(check, replay)

    # Assert
    assert text.splitlines()[0].startswith("venue replay: INCOMPLETE")
    assert "SPLIT" in text
    assert "UNJUDGED" in text


def test_the_report_says_nothing_was_measured_rather_than_zero():
    # Arrange
    check = cross_check(registry_pnl=2.35, replay=None, taker_fees=0.35)

    # Act
    text = format_report(check, None)

    # Assert
    assert "UNREAD" in text
    assert "not $0.00" in text
    assert check.explained is None


def test_a_row_that_could_not_be_read_makes_the_walk_incomplete():
    # Arrange — a row the replay could not price is money missing from the
    # cashflow, exactly like a truncated walk.
    replay = Replay(trading_cashflow=-8.0, open_value=10.0, rows=3,
                    unreadable_rows=1)

    # Act
    check = cross_check(registry_pnl=2.35, replay=replay, taker_fees=0.35)

    # Assert
    assert replay.complete is False
    assert check.explained is None


def test_a_truncated_fee_walk_cannot_produce_an_explained_verdict():
    # Arrange — the fee total is a floor, so the expected gap built on it is a
    # floor, and a verdict would be arithmetic on a number known to be short.
    replay = Replay(trading_cashflow=-8.0, open_value=10.0)

    # Act
    check = cross_check(registry_pnl=2.35, replay=replay, taker_fees=0.35,
                        fees_complete=False)

    # Assert
    assert check.gap == pytest.approx(0.35)
    assert check.explained is None
