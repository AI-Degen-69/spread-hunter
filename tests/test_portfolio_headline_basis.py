"""On the live registry the headline is the wallet; on a shadow store it is not.

#97 put the whole card on ONE basis -- registry equity, `starting_capital +
total_pnl` -- because under a shadow run the simulated gain never reaches the
wallet, and a headline reading the wallet beside a chart reading the registry
gave three numbers no one could reconcile.

That reasoning is about the shadow case, and it does not carry to the live
registry, where the wallet is the answer to the question the card asks. On the
production store the page printed $169.88 while Polymarket showed $81.28 in the
same browser; even with the account-wide sync rows removed it printed $78.43,
because registry equity cannot see venue-side activity it never recorded.

So the basis follows the store: LIVE reads the venue's account value, SHADOW
keeps registry equity, and `portfolio.total_value_basis` says which was used.

The third defect here is a stale float mark. A mark was called current when it
was newer than the last CLOSE -- true of a mark from 28-08 whose positions had
been gone for nine days, because no close had been written since. The venue
re-measures the whole book on every account mark, so a float mark older than
the newest account mark describes a book the venue has already replaced.

Journeys under test:
1. As the Owner, the headline on the live page equals the balance Polymarket
   shows me in the next tab.
2. As the Owner, a shadow run still reads its own simulated equity.
3. As the Owner, an open float nobody has measured since the venue last spoke
   reads as unmeasured, not as a nine-day-old number.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from core_brain import account as acct_mod
from core_brain import kpi as kpi_mod
from core_brain.kpi import report
from core_brain.order_registry import SCHEMA, CloseRecord, OrderRegistry

RUN = "run-basis"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


@pytest.fixture
def live_db(temp_db, monkeypatch):
    """The same store, but resolving as the production registry."""
    monkeypatch.setattr(kpi_mod, "DEFAULT_DB_PATH", temp_db)
    return temp_db


def _mark(reg: OrderRegistry, ts: float, account_value: float,
          positions_value: float = 0.0) -> None:
    reg.log_account_mark(
        acct_mod.compose_account_mark(
            collateral_usd=account_value - positions_value,
            positions_value_usd=positions_value,
            open_positions=[], closed_positions=[], user_pnl_usd=0.0,
        ),
        ts=ts, run_id=RUN,
    )


def _seed(reg: OrderRegistry, t0: float) -> None:
    """A wallet mark before the bot traded, then one losing close."""
    _mark(reg, t0, 81.37)
    reg.log_close(CloseRecord(
        ts=t0 + 60, condition_id="0xbot", market_slug="bot-market",
        method="single_buy_exit", shares=5.0, cost_basis=2.75, proceeds=2.20,
        realized_pnl=-0.55, tx_hash="0xbot1", run_id=RUN,
    ))


# --------------------------------------------------------------------------
# Journey 1: the live headline is the wallet
# --------------------------------------------------------------------------

def test_live_headline_is_the_venue_account_value(live_db):
    reg = OrderRegistry(live_db)
    t0 = time.time() - 3600
    _seed(reg, t0)
    _mark(reg, t0 + 120, 81.28)

    p = report(db_path=live_db, run_id="all")["portfolio"]
    assert p["total_value"] == pytest.approx(81.28)
    assert p["total_value_basis"] == "venue"
    # The bot's own result is untouched by the change of basis.
    assert p["realized_pnl"] == pytest.approx(-0.55)


def test_live_headline_falls_back_when_the_venue_never_answered(live_db):
    """No account mark means no wallet reading, not a wallet worth $0.00."""
    reg = OrderRegistry(live_db)
    reg.log_close(CloseRecord(
        ts=time.time() - 60, condition_id="0xbot", market_slug="bot-market",
        method="merge", shares=5.0, cost_basis=4.70, proceeds=5.00,
        realized_pnl=0.30, tx_hash="0xbot1", run_id=RUN,
    ))

    p = report(db_path=live_db, run_id="all")["portfolio"]
    assert p["total_value_basis"] == "registry"
    assert p["total_value"] == pytest.approx(p["starting_capital"] + 0.30)


# --------------------------------------------------------------------------
# Journey 2: a shadow store keeps #97's basis
# --------------------------------------------------------------------------

def test_shadow_headline_stays_on_registry_equity(temp_db):
    """The simulated gain never reaches the wallet, so the wallet is not it."""
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 3600
    _seed(reg, t0)
    _mark(reg, t0 + 120, 81.28)

    p = report(db_path=temp_db, run_id="all")["portfolio"]
    assert p["total_value_basis"] == "registry"
    assert p["total_value"] == pytest.approx(p["starting_capital"] - 0.55)


# --------------------------------------------------------------------------
# Journey 4: a live run on its OWN store still reads the wallet
# --------------------------------------------------------------------------

def _pilot_store(tmp_path) -> tuple:
    """A live pilot's store: its own filename, wallet marks only the sweep writes."""
    db_file = tmp_path / "live_pilot_251029.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    reg = OrderRegistry(db_file)
    t0 = time.time() - 3600
    _seed(reg, t0)
    _mark(reg, t0 + 120, 81.28)
    return db_file, reg


def test_a_live_run_on_its_own_store_reads_the_wallet_too(tmp_path, monkeypatch):
    """The contained pilot writes its marks to its own store, not orders.db.

    The basis followed the production FILENAME, so a live run on
    `data/live_pilot_<stamp>.db` -- a real-money account the venue is read from
    every second -- printed `starting_capital + total_pnl`: the venue's
    unrealised cashPnl subtracted from a starting capital that is itself an
    already-traded wallet mark. That modelled figure went NEGATIVE while
    Polymarket, in the next tab, showed the account funded.
    """
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    db_file, _reg = _pilot_store(tmp_path)

    p = report(db_path=db_file, run_id="all", live_account=True)["portfolio"]
    assert p["total_value_basis"] == "venue"
    assert p["total_value"] == pytest.approx(81.28)


def test_the_live_account_opt_in_is_not_the_default(tmp_path, monkeypatch):
    """Every caller that does not claim a live account keeps #97's basis.

    Widening the rule inside `report()` would have moved the basis for every
    rehearsal store and the 541 audit goldens with it, so the proof lives at
    the call site instead of in the filename test.
    """
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    db_file, _reg = _pilot_store(tmp_path)

    p = report(db_path=db_file, run_id="all")["portfolio"]
    assert p["total_value_basis"] == "registry"


# --------------------------------------------------------------------------
# Journey 3: a float mark the venue has already superseded
# --------------------------------------------------------------------------

def test_float_mark_older_than_the_newest_account_mark_is_not_current(live_db):
    """The venue re-measures the whole book; the older mark describes a
    book it has since replaced.

    Nine days separated these two rows on the production registry, and the
    card was still printing the float from the older one.
    """
    reg = OrderRegistry(live_db)
    t0 = time.time() - 86400 * 9
    _seed(reg, t0)
    reg.log_float_mark(unrealized_usd=-0.54, committed_open_usd=2.76,
                       naked_usd=0.0, ts=t0 + 120, run_id=RUN)
    _mark(reg, time.time() - 60, 81.28)

    p = report(db_path=live_db, run_id="all")["portfolio"]
    assert p["unrealized_measured"] is False
    assert p["unrealized_usd"] is None
    assert p["open_committed_usd"] is None


def test_a_float_mark_stamped_at_the_account_mark_is_not_current(live_db):
    """An equal stamp does not prove the float was written second.

    `time.time()` gives the two writes distinct stamps in any real sweep, so a
    tie means the ordering is unknown -- and unknown reads as unmeasured, not
    as a float the venue may already have replaced.
    """
    reg = OrderRegistry(live_db)
    t0 = time.time() - 3600
    _seed(reg, t0)
    _mark(reg, t0 + 120, 81.28)
    reg.log_float_mark(unrealized_usd=-0.54, committed_open_usd=2.76,
                       naked_usd=0.0, ts=t0 + 120, run_id=RUN)

    p = report(db_path=live_db, run_id="all")["portfolio"]
    assert p["unrealized_measured"] is False
    assert p["unrealized_usd"] is None


def test_a_float_mark_from_the_current_sweep_still_counts(live_db):
    """Each live sweep writes the account mark first, the float mark second.

    The rule must not reject that ordering, or a healthy run reports its open
    exposure as unmeasured on every cycle.
    """
    reg = OrderRegistry(live_db)
    t0 = time.time() - 3600
    _seed(reg, t0)
    _mark(reg, t0 + 120, 81.28)
    reg.log_float_mark(unrealized_usd=1.25, committed_open_usd=9.60,
                       naked_usd=0.0, ts=t0 + 120.5, run_id=RUN)

    p = report(db_path=live_db, run_id="all")["portfolio"]
    assert p["unrealized_measured"] is True
    assert p["unrealized_usd"] == pytest.approx(1.25)
    assert p["open_committed_usd"] == pytest.approx(9.60)
