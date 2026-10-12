"""`record_shadow_merges` must not let one run merge another run's pairs.

The merge query reads every order with a pair_id in the file with no run
filter (unlike `settle_market`, which got explicit run-scoping after the
08-26 incident). Today one-file-per-arm plus uuid pair_ids should contain
it -- this test locks that in with two run_ids sharing one file. If it ever
goes red, scope the merge query by run the way `settle_market` does.
"""
import time

from core_brain.order_registry import FillRecord, OrderRegistry, init_db
from core_brain.quotes import QuoteIntent


class FakeMarket:
    condition_id = "0xabc"
    up_token = "tok-up"
    down_token = "tok-dn"
    market_slug = "fake-market"
    tick_size = 0.01
    neg_risk = False


def _cfg():
    from core_brain.config import load
    return load()


def _intents():
    return [
        QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                    mid=0.5, edge_vs_mid=0.0),
        QuoteIntent(side="DOWN", token_id="tok-dn", price=0.51, size=20,
                    mid=0.5, edge_vs_mid=0.0),
    ]


def _fill_and_merge_pair(reg, db, shares=6.0):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit,
    )
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    by_token = {o.token_id: o for o in reg.get_active_orders()
                if o.run_id == reg._run_id()}
    reg.record_fill(FillRecord(trade_id=f"f-up-{reg._run_id()}",
                               order_uuid=by_token["tok-up"].id,
                               size=shares, price=0.252))
    reg.record_fill(FillRecord(trade_id=f"f-dn-{reg._run_id()}",
                               order_uuid=by_token["tok-dn"].id,
                               size=shares, price=0.618))
    now = int(time.time() * 1000)
    reg.update_order_status(by_token["tok-up"].id, status="filled",
                            last_polled_ts=now)
    reg.update_order_status(by_token["tok-dn"].id, status="filled",
                            last_polled_ts=now)
    return record_shadow_merges(reg, db)


def test_second_run_merges_only_its_own_pair(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(db)
    reg_a = OrderRegistry(db_path=db, run_id="run-A")
    reg_b = OrderRegistry(db_path=db, run_id="run-B")

    assert len(_fill_and_merge_pair(reg_a, db)) == 1
    assert len(_fill_and_merge_pair(reg_b, db)) == 1
    closes = reg_b.get_all_closes()
    merges = [c for c in closes if c["method"] == "shadow_merge"]

    # Exactly one merge per run: run B must not re-merge run A's pair, and
    # each close must carry the run that earned it.
    assert len(merges) == 2
    assert sorted(c["run_id"] for c in merges) == ["run-A", "run-B"]
