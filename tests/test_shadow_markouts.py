"""Adverse selection must be measurable in a rehearsal, not only live.

Both completed shadow runs (`data/04_shadow_31-08_20-43.db` and
`data/06_shadow_01-09_trial88.db`) hold fills and an empty `markouts` table.
The cause is structural rather than a horizon that never matured: shadow fills
are credited by `shadow_exec`, which wrote a `fills` row and nothing else, and
the only sampler that matures a horizon is started by `core_brain.order_manager`
on the live poll path. A rehearsal therefore never created a markout row and
never sampled one.

That gap blocks the interpretation of any experiment that changes the fill rate:
more fills at worse selection is not an improvement, and without these rows no
run can tell the two apart. The tests below pin both halves -- the write on
fill, and the sampling pass inside the shadow session's own sweep.
"""
from __future__ import annotations

import sqlite3

import pytest

from core_brain.order_registry import OrderRegistry, init_db


class FakeMarket:
    condition_id = "0xabc"
    slug = "fake-market"
    up_token = "tok-up"
    down_token = "tok-dn"
    tick_size = 0.001


def _cfg():
    from core_brain.config import load
    return load()


def _intents():
    from core_brain.trader_loop import QuoteIntent
    return [
        QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                    mid=0.5, edge_vs_mid=0.0),
        QuoteIntent(side="DOWN", token_id="tok-dn", price=0.51, size=20,
                    mid=0.5, edge_vs_mid=0.0),
    ]


@pytest.fixture()
def registry(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(str(db))
    return OrderRegistry(str(db)), str(db)


def _markout_rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM markouts")]
    finally:
        conn.close()


def test_a_credited_shadow_fill_writes_a_markout_row(registry):
    """The write half. Without it `markouts` stays empty however long a run is."""
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.46: 20.0}})

    rows = _markout_rows(db)
    assert len(rows) == 1, "one credited fill must leave one markout row"
    row = rows[0]
    assert row["condition_id"] == "0xabc"
    assert row["token_id"] == "tok-up"
    assert row["fill_price"] == pytest.approx(0.47)
    assert row["size"] == pytest.approx(20.0)
    assert row["done"] == 0, "a fresh markout has no matured horizon yet"
    assert row["mid_h0"] is None


def test_the_markout_row_carries_the_market_slug(registry):
    """The first sweep's rows came back with a NULL slug.

    The market specs the loop rotates carry the slug as `market_slug` -- the
    name every other writer in this module reads -- while the pinned market
    objects call it `slug`. Reading only `slug` left the markout rows
    unlabelled next to fully labelled quote rows on the same fill.
    """
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    class SpecMarket(FakeMarket):
        market_slug = "atp-borges-tien-2026-08-30"
        slug = None

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, SpecMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    settle_market(reg, SpecMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.46: 20.0}})

    assert _markout_rows(db)[0]["market_slug"] == "atp-borges-tien-2026-08-30"


def test_the_markout_row_carries_the_run_that_filled(registry):
    """A store is reused across rehearsals; an unattributed row is unusable."""
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.46: 20.0}})

    row = _markout_rows(db)[0]
    assert row["run_id"] == reg._run_id()


def test_a_duplicate_trade_does_not_write_a_second_markout(registry):
    """Fill credit is idempotent per trade; the markout must be too."""
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    seen: set = set()
    settle_market(reg, FakeMarket(), db_path=db, seen=seen,
                  traded_fn=lambda cid, s: {"tok-up": {0.46: 20.0}})
    # A second rotation that sees no new tape must not re-credit anything.
    settle_market(reg, FakeMarket(), db_path=db, seen=seen,
                  traded_fn=lambda cid, s: {})

    assert len(_markout_rows(db)) == 1


def test_a_matured_horizon_is_sampled_into_the_row(registry):
    """The read half, end to end: a due horizon is filled from the book."""
    from core_brain.markout import sample_pending_markouts
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.46: 20.0}})

    row_ts = float(_markout_rows(db)[0]["ts"])

    import core_brain.markets as markets_mod

    class PinnedMarket:
        up_token = "tok-up"
        down_token = "tok-dn"

    def fake_full_book(host, token_id):
        # UP drifted up after our buy; DOWN is its mirror.
        if token_id == "tok-up":
            return {"best_bid": 0.49, "best_ask": 0.51}
        return {"best_bid": 0.49, "best_ask": 0.51}

    orig_book = markets_mod.full_book
    orig_pinned = markets_mod.fetch_pinned_market
    markets_mod.full_book = fake_full_book
    markets_mod.fetch_pinned_market = lambda cid, require_rewards=False: PinnedMarket()
    try:
        updated = sample_pending_markouts(
            reg, now_sec=row_ts + 301.0, trades_fn=lambda token, cid=None: [],
        )
    finally:
        markets_mod.full_book = orig_book
        markets_mod.fetch_pinned_market = orig_pinned

    assert updated == 1
    row = _markout_rows(db)[0]
    assert row["mid_h0"] == pytest.approx(0.50), (
        "the 5-minute horizon must carry the sampled mid")


def test_a_rehearsal_rotation_samples_pending_markouts(monkeypatch, tmp_path):
    """The wiring half, driven through `run_shadow` rather than the wrapper.

    `MarkoutWorker` is started only by `core_brain.order_manager` on the live
    poll path, so a shadow session that never calls the sampler leaves every
    horizon NULL no matter how many fills it books. Calling
    `sample_shadow_markouts` directly here would still pass with the call site
    in `shadow_run.shadow_sweep` deleted -- so the session is run instead, one
    rotation, with the sampler stubbed underneath it.
    """
    import core_brain.markout as markout_mod
    from core_brain.shadow_run import run_shadow

    db = tmp_path / "shadow.db"
    init_db(str(db))

    calls: list[str] = []

    def fake_sample(registry, clob_host="https://clob.polymarket.com", **kwargs):
        calls.append(clob_host)
        return 0

    monkeypatch.setattr(markout_mod, "sample_pending_markouts", fake_sample)

    # An empty market list starves the quoting loop, so the sweep is the only
    # thing in the rotation that could reach the sampler.
    run_shadow(
        minutes=0.0, db_path=db,
        markets_fn=lambda max_markets=None: [],
        client_fn=lambda: object(),
        fetch_books=lambda host, token_id: {"bids": {}, "asks": {}},
    )

    assert len(calls) == 1, (
        "one rehearsal rotation must run exactly one sampler pass")


def test_a_failing_sampler_never_stops_the_sweep(monkeypatch, tmp_path):
    """Telemetry may not take a rehearsal down. Degrade, log, continue."""
    import core_brain.markout as markout_mod
    import core_brain.shadow_run as shadow_run

    db = tmp_path / "shadow.db"
    init_db(str(db))
    reg = OrderRegistry(str(db))

    def boom(registry, clob_host="https://clob.polymarket.com", **kwargs):
        raise RuntimeError("tape unreachable")

    monkeypatch.setattr(markout_mod, "sample_pending_markouts", boom)

    # No exception: the caller is the per-rotation sweep.
    assert shadow_run.sample_shadow_markouts(reg) == 0
