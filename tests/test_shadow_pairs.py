"""Shadow pair-manager + stable pair identity (operator directive 2026-10-12).

Covers:
  * plan_pairs: adopt completing legs under one pair_id; cancel hopeless
    orphans; leave a completable leg for the pairs pass (no cancel).
  * stable pair identity: record_submit reuses a resting complement's pair_id
    across rotations instead of minting a fresh one every call.
"""
from __future__ import annotations

from types import SimpleNamespace

from core_brain.shadow_pairs import plan_pairs


def _order(local_id, token, price, pair_id=None, status="open", size=10.0):
    return SimpleNamespace(
        id=local_id, token_id=token, price=price, pair_id=pair_id,
        status=status, original_size=size, condition_id="0xcond",
    )


def _book(ask):
    # normalized book dict shape plan_pairs expects (asks as dict price->size)
    return {"asks": {round(ask, 4): 100.0}} if ask is not None else {"asks": {}}


# --- plan_pairs: adopt ------------------------------------------------------

def test_adopt_welds_two_detached_legs_under_one_pair_id():
    a = _order("idA", "tokUP", 0.30, pair_id="pair-aaa")
    b = _order("idB", "tokDN", 0.60, pair_id=None)
    plan = plan_pairs("0xcond", [a, b], {}, {"0xcond": ("tokUP", "tokDN")},
                      max_pair_cost=0.98, mint_pair_id=lambda: "pair-fresh")
    assert len(plan.adopt) == 1
    l1, l2, shared = plan.adopt[0]
    assert {l1, l2} == {"idA", "idB"}
    # reuses the existing pair- id rather than orphaning a new one
    assert shared == "pair-aaa"
    assert not plan.cancel


def test_adopt_mints_when_neither_leg_has_a_pair_id():
    a = _order("idA", "tokUP", 0.40, pair_id=None)
    b = _order("idB", "tokDN", 0.50, pair_id=None)
    plan = plan_pairs("0xcond", [a, b], {}, {"0xcond": ("tokUP", "tokDN")},
                      max_pair_cost=0.98, mint_pair_id=lambda: "pair-fresh")
    assert plan.adopt[0][2] == "pair-fresh"


def test_no_adopt_when_combined_exceeds_cap():
    a = _order("idA", "tokUP", 0.50)
    b = _order("idB", "tokDN", 0.55)  # 1.05 > cap
    plan = plan_pairs("0xcond", [a, b], {}, {"0xcond": ("tokUP", "tokDN")},
                      max_pair_cost=0.98, mint_pair_id=lambda: "pair-x")
    assert not plan.adopt


# --- plan_pairs: cancel hopeless orphans ------------------------------------

def test_cancel_orphan_when_combined_with_ask_exceeds_cap():
    # Only UP rests (no partner). Opposing DOWN ask makes it uncompletable.
    a = _order("idA", "tokUP", 0.60)
    books = {"tokDN": _book(0.50)}  # 0.60 + 0.50 = 1.10 >= cap
    plan = plan_pairs("0xcond", [a], books, {"0xcond": ("tokUP", "tokDN")},
                      max_pair_cost=0.98, mint_pair_id=lambda: "pair-x")
    assert "idA" in plan.cancel


def test_cancel_orphan_when_no_opposing_book():
    a = _order("idA", "tokUP", 0.60)
    plan = plan_pairs("0xcond", [a], {}, {"0xcond": ("tokUP", "tokDN")},
                      max_pair_cost=0.98, mint_pair_id=lambda: "pair-x")
    assert "idA" in plan.cancel


def test_completable_orphan_is_left_for_the_pairs_pass():
    # UP rests alone but opposing ask is cheap enough to complete -> do NOT
    # cancel; the pairs pass crosses to complete.
    a = _order("idA", "tokUP", 0.40)
    books = {"tokDN": _book(0.50)}  # 0.40 + 0.50 = 0.90 < cap
    plan = plan_pairs("0xcond", [a], books, {"0xcond": ("tokUP", "tokDN")},
                      max_pair_cost=0.98, mint_pair_id=lambda: "pair-x")
    assert "idA" not in plan.cancel
    assert not plan.adopt


def test_partner_resting_is_not_treated_as_orphan():
    # Both tokens present but combined over cap: not adoptable, but each has a
    # partner resting, so neither is a hopeless orphan -> no cancel.
    a = _order("idA", "tokUP", 0.60)
    b = _order("idB", "tokDN", 0.60)
    plan = plan_pairs("0xcond", [a, b], {}, {"0xcond": ("tokUP", "tokDN")},
                      max_pair_cost=0.98, mint_pair_id=lambda: "pair-x")
    assert not plan.cancel
    assert not plan.adopt


def test_empty_orders_is_a_noop():
    plan = plan_pairs("0xcond", [], {}, {"0xcond": ("tokUP", "tokDN")},
                      max_pair_cost=0.98, mint_pair_id=lambda: "pair-x")
    assert not plan.adopt and not plan.cancel


# --- stable pair identity via record_submit ---------------------------------

def test_record_submit_reuses_resting_complement_pair_id(tmp_path):
    """A re-quote joins the resting complement's pair_id, not a fresh one.

    This is the fix for split pairs / no merge: UP and DOWN of one economic
    pair converge on a single id across rotations.
    """
    import uuid

    from core_brain.order_registry import DEFAULT_DB_PATH, OrderRegistry, init_db
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    db = tmp_path / "shadow.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)
    ensure_shadow_tables(db)

    market = SimpleNamespace(condition_id="0xcond")
    cfg = SimpleNamespace(max_pair_cost=0.99)

    class QI:
        def __init__(self, side, token, price, size, pair_id=None):
            self.side, self.token_id, self.price, self.size = side, token, price, size
            self.pair_id = pair_id

    # Rotation 1: place only the UP leg.
    up = QI("UP", "tokUP", 0.30, 10.0)
    record_submit(object(), reg, market, [up], cfg,
                  db_path=db, book_fn=lambda h, t: {"bids": {0.29: 100.0}, "asks": {0.31: 100.0}})
    up_pair = {o.pair_id for o in reg.get_active_orders() if o.token_id == "tokUP"}
    assert len(up_pair) == 1
    up_pair_id = up_pair.pop()

    # Rotation 2: the DOWN leg re-quotes while UP still rests. It must JOIN
    # the UP pair_id, not mint a new one.
    dn = QI("DOWN", "tokDN", 0.60, 10.0)
    record_submit(object(), reg, market, [dn], cfg,
                  db_path=db, book_fn=lambda h, t: {"bids": {0.59: 100.0}, "asks": {0.61: 100.0}})
    dn_pair = {o.pair_id for o in reg.get_active_orders() if o.token_id == "tokDN"}
    assert len(dn_pair) == 1
    assert dn_pair.pop() == up_pair_id, (
        "DOWN leg did not join the resting UP pair_id -- pairs would split")
