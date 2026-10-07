"""Issue #196: shadow markout rows never mature, blocking the validation verdict.

Two defects, one outcome — every markout row a shadow run writes stays
`done=0` and excluded from `count_matured_markouts`, so the statistical
validation harness's `min_markouts` stop condition can never fire.

Defect 1 — ended markets. `sample_pending_markouts` resolves the mid through
`fetch_pinned_market`, which returns None for a closed market. A fill on a
market that resolved before its horizons came due therefore never samples any
horizon and the row is retried forever. Every row in the shadow-01 store
(`data/01_shadow_11-09_00-37.db`) hit exactly this: 12 rows, 0 matured.

Defect 2 — the permanent contamination stamp. Rows are written
`ref_mid_source='contaminated'` and the sampler's own docstring says the stamp
holds "until a clean reference mid is measured" — but nothing ever clears it:
`_record_reference` stores into `refs_json` and stops. The harness counter
(`statistical_validation_run.run.count_matured_markouts`) excludes
contaminated rows, so even a fully matured row counts as zero.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from core_brain.markout import sample_pending_markouts
from core_brain.order_registry import OrderRegistry


T0 = 1_800_000_000.0


@pytest.fixture
def registry(tmp_path) -> OrderRegistry:
    from core_brain.order_registry import init_db
    db = tmp_path / "shadow.db"
    init_db(str(db))
    return OrderRegistry(db)


def _seed_markout(registry: OrderRegistry, ts: float, token: str = "tok-up",
                  condition_id: str = "0xmarket") -> int:
    con = sqlite3.connect(str(registry.db_path))
    con.execute(
        "INSERT INTO markouts (ts, condition_id, side, token_id, fill_price,"
        " size, ref_mid, ref_mid_source, done, run_id)"
        " VALUES (?,?,?,?,?,?,?, 'contaminated', 0, 'shadow-test')",
        (ts, condition_id, "BUY", token, 0.40, 200.0, 0.40),
    )
    con.commit()
    markout_id = con.execute("SELECT MAX(id) FROM markouts").fetchone()[0]
    con.close()
    return markout_id


def _row(registry: OrderRegistry, markout_id: int) -> dict:
    con = sqlite3.connect(str(registry.db_path))
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM markouts WHERE id = ?",
                      (markout_id,)).fetchone()
    con.close()
    return dict(row) if row else {}


def _patch_venue(monkeypatch, *, market_closed: bool):
    """Point the sampler's venue reads at fakes.

    `market_closed=True` reproduces the ended-market case:
    `fetch_pinned_market` returns None exactly as it does for a resolved
    market, while the book endpoint still answers (the CLOB serves closed
    books).
    """
    import core_brain.markets as markets_mod

    class _Market:
        up_token = "tok-up"
        down_token = "tok-dn"

    def fake_fetch(condition_id, require_rewards=False):
        if market_closed:
            return None
        return _Market()

    monkeypatch.setattr(markets_mod, "fetch_pinned_market", fake_fetch)
    monkeypatch.setattr(
        markets_mod, "full_book",
        lambda host, token: {"best_bid": 0.55, "best_ask": 0.57})


def test_a_markout_on_a_closed_market_still_matures(registry, monkeypatch):
    """Defect 1. A fill on a market that resolved must not strand its row.

    The row is stuck done=0 today because `fetch_pinned_market` returns None
    for closed markets and the sampler refuses to write a horizon without a
    mid. The book endpoint is public and still answers for closed markets,
    so the mid is measurable without the pinned-market fetch.
    """
    markout_id = _seed_markout(registry, T0 - 4000)
    _patch_venue(monkeypatch, market_closed=True)

    updated = sample_pending_markouts(
        registry, now_sec=T0, trades_fn=lambda token, cid=None: [])

    row = _row(registry, markout_id)
    assert row.get("mid_h0") is not None, (
        "a closed market's book still answers; the horizon must be sampled")
    assert updated >= 1


def test_a_resolved_market_with_purged_book_matures_at_settlement(registry, monkeypatch):
    """The real-world case the backfill exposed: the market resolved, the
    venue purged its book (/book 404s), so the book fallback cannot answer.
    The store's own resolutions table knows the winner, and a resolved
    market's terminal price is exact: 1.0 on the winning token, 0.0 on the
    losing one. The horizon must be sampled from that settlement price.
    """
    markout_id = _seed_markout(registry, T0 - 4000, token="tok-up")
    con = sqlite3.connect(str(registry.db_path))
    con.execute(
        "INSERT INTO resolutions (condition_id, winning_token, winning_token_id,"
        " resolved_ts, run_id) VALUES ('0xmarket', 'Up', 'tok-up', ?, 'shadow-test')",
        (T0 - 100.0,),
    )
    con.commit()
    con.close()
    # Book endpoint purged -> raises, as it does live for resolved markets.
    _patch_venue(monkeypatch, market_closed=True)
    import core_brain.markets as markets_mod
    monkeypatch.setattr(
        markets_mod, "full_book",
        lambda host, token: (_ for _ in ()).throw(Exception("404 purged")))

    sample_pending_markouts(
        registry, now_sec=T0, trades_fn=lambda token, cid=None: [])

    row = _row(registry, markout_id)
    assert row["mid_h0"] == pytest.approx(1.0), (
        "the winning token settles at 1.00; the horizon must carry that")


def test_a_resolved_markets_losing_leg_settles_at_zero(registry, monkeypatch):
    markout_id = _seed_markout(registry, T0 - 4000, token="tok-dn")
    con = sqlite3.connect(str(registry.db_path))
    con.execute(
        "INSERT INTO resolutions (condition_id, winning_token, winning_token_id,"
        " resolved_ts, run_id) VALUES ('0xmarket', 'Up', 'tok-up', ?, 'shadow-test')",
        (T0 - 100.0,),
    )
    con.commit()
    con.close()
    _patch_venue(monkeypatch, market_closed=True)
    import core_brain.markets as markets_mod
    monkeypatch.setattr(
        markets_mod, "full_book",
        lambda host, token: (_ for _ in ()).throw(Exception("404 purged")))

    sample_pending_markouts(
        registry, now_sec=T0, trades_fn=lambda token, cid=None: [])

    assert _row(registry, markout_id)["mid_h0"] == pytest.approx(0.0)


def test_a_market_without_a_recorded_resolution_stays_pending(registry, monkeypatch):
    """Control: no book, no resolution -> the row is left for a later pass,
    never priced on a guess."""
    markout_id = _seed_markout(registry, T0 - 4000, token="tok-up")
    _patch_venue(monkeypatch, market_closed=True)
    import core_brain.markets as markets_mod
    monkeypatch.setattr(
        markets_mod, "full_book",
        lambda host, token: (_ for _ in ()).throw(Exception("404 purged")))

    sample_pending_markouts(
        registry, now_sec=T0, trades_fn=lambda token, cid=None: [])

    assert _row(registry, markout_id)["mid_h0"] is None


def test_both_legs_of_one_closed_market_mature(registry, monkeypatch):
    """Regression: the closed-market fallback must serve each token.

    A market's fill on each leg leaves two pending rows sharing one closed
    condition_id with different tokens. A fallback cached by condition_id
    from the first row's token resolves nothing for the second -- the second
    row stays stranded. Keyed by token, both mature.
    """
    up_id = _seed_markout(registry, T0 - 4000, token="tok-up")
    dn_id = _seed_markout(registry, T0 - 4000, token="tok-dn")
    _patch_venue(monkeypatch, market_closed=True)

    sample_pending_markouts(
        registry, now_sec=T0, trades_fn=lambda token, cid=None: [])

    assert _row(registry, up_id)["mid_h0"] is not None
    assert _row(registry, dn_id)["mid_h0"] is not None, (
        "the DOWN leg's row must sample its own token's book, not the UP "
        "leg's cached fallback")


def test_a_sampled_row_with_a_recorded_reference_is_counted_clean(registry, monkeypatch):
    """Defect 2. Once the tape supplies a windowed reference, the row must
    stop reading as contaminated — or the harness's matured-markout counter
    excludes it forever and `min_markouts` can never be met.
    """
    markout_id = _seed_markout(registry, T0 - 4000)
    _patch_venue(monkeypatch, market_closed=False)

    sample_pending_markouts(
        registry, now_sec=T0,
        trades_fn=lambda token, cid=None: [
            {"timestamp": T0 - 4000 + 300, "price": 0.50, "size": 100.0},
        ])

    row = _row(registry, markout_id)
    assert row["ref_mid_source"] != "contaminated", (
        "a row with a measured tape reference is no longer contaminated")
    # And the harness counter — the gate the shadow run waits on — sees it.
    from statistical_validation_run.run import count_matured_markouts
    assert count_matured_markouts(registry.db_path) >= 1


def test_a_row_without_tape_stays_contaminated(registry, monkeypatch):
    """The stamp means 'the reference was never measured', and that meaning
    must survive: a row with no tape answer keeps the stamp and stays out of
    the harness count."""
    markout_id = _seed_markout(registry, T0 - 4000)
    _patch_venue(monkeypatch, market_closed=False)

    # Tape answers with an empty window — no prints, no reference.
    sample_pending_markouts(
        registry, now_sec=T0, trades_fn=lambda token, cid=None: [])

    row = _row(registry, markout_id)
    assert row["ref_mid_source"] == "contaminated"
    from statistical_validation_run.run import count_matured_markouts
    assert count_matured_markouts(registry.db_path) == 0


def _seed_resolution(registry, winning_token_id, winning_token="Up"):
    con = sqlite3.connect(str(registry.db_path))
    con.execute(
        "INSERT INTO resolutions (condition_id, winning_token, winning_token_id,"
        " resolved_ts, run_id) VALUES ('0xmarket', ?, ?, ?, 'shadow-test')",
        (winning_token, winning_token_id, T0 - 100.0),
    )
    con.commit()
    con.close()


@pytest.mark.parametrize("first", ["tok-up", "tok-dn"])
def test_winner_and_loser_mature_without_any_book_read(registry, monkeypatch,
                                                       first):
    """#402 T4, terminal-first: settlement serves both legs with zero venue
    reads, in either row order (the cache key is condition + token)."""
    import core_brain.markets as markets_mod
    second = "tok-dn" if first == "tok-up" else "tok-up"
    up_id = _seed_markout(registry, T0 - 4000, token=first)
    dn_id = _seed_markout(registry, T0 - 4000, token=second)
    _seed_resolution(registry, "tok-up")

    venue_reads: list[str] = []

    def counting_book(host, token):
        venue_reads.append(f"book:{token}")
        raise Exception("404 purged")

    def forbidden_fetch(condition_id, require_rewards=False):
        raise AssertionError("terminal-first must not fetch the market")

    monkeypatch.setattr(markets_mod, "full_book", counting_book)
    monkeypatch.setattr(markets_mod, "fetch_pinned_market", forbidden_fetch)

    sample_pending_markouts(
        registry, now_sec=T0, trades_fn=lambda token, cid=None: [])

    rows = {first: _row(registry, up_id), second: _row(registry, dn_id)}
    assert rows["tok-up"]["mid_h0"] == pytest.approx(1.0)
    assert rows["tok-dn"]["mid_h0"] == pytest.approx(0.0)
    assert venue_reads == []


def test_unknown_winner_stays_pending_without_book_reads(registry, monkeypatch):
    """#402 T4: a resolution with no known winner leaves the horizon pending
    and buys no book request."""
    import core_brain.markets as markets_mod
    markout_id = _seed_markout(registry, T0 - 4000, token="tok-up")
    _seed_resolution(registry, None, winning_token=None)

    venue_reads: list[str] = []

    def counting_book(host, token):
        venue_reads.append(f"book:{token}")
        raise Exception("404 purged")

    monkeypatch.setattr(markets_mod, "full_book", counting_book)
    monkeypatch.setattr(
        markets_mod, "fetch_pinned_market", lambda *a, **k: None)

    sample_pending_markouts(
        registry, now_sec=T0, trades_fn=lambda token, cid=None: [])

    assert _row(registry, markout_id)["mid_h0"] is None
    assert venue_reads == []
