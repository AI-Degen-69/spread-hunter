"""Visit ordering by filled exposure: naked -> balanced -> flat (#505).

`order_by_exposure` must be pure -- readable with no database, no loop and no
venue -- because it decides only the ORDER of the rotation's visits. Each visit
still reads fresh inventory; ranking never becomes a source of truth about
positions.

These tests pin the rank table (dust tolerance 1e-9, a negative leg is naked),
stability within each rank, full coverage of the input, and that nothing is
mutated.
"""
from __future__ import annotations

from core_brain.quotes import Inventory
from core_brain.rotation_priority import order_by_exposure
from core_brain.trader_loop import _cid


def _spec(cid: str):
    return {"cid": cid}


def _obj(cid: str):
    class _Market:
        condition_id = cid

    return _Market()


def _cids(specs):
    return [_cid(s) for s in specs]


class TestOrderByExposure:
    def test_naked_before_balanced_before_flat(self):
        snap = {
            "0xa": Inventory(),                                    # flat
            "0xb": Inventory(up_shares=5, down_shares=5),          # balanced
            "0xc": Inventory(up_shares=7, down_shares=0),          # naked
            "0xd": Inventory(),                                    # flat
            "0xe": Inventory(up_shares=2, down_shares=1),          # naked
        }
        feed = [_spec(c) for c in ("0xa", "0xb", "0xc", "0xd", "0xe")]
        out = order_by_exposure(feed, snap, _cid)
        assert _cids(out) == ["0xc", "0xe", "0xb", "0xa", "0xd"]

    def test_feed_order_kept_within_each_rank(self):
        snap = {
            "0x1": Inventory(up_shares=3, down_shares=0),          # naked
            "0x2": Inventory(up_shares=1, down_shares=4),          # naked
            "0x3": Inventory(up_shares=2, down_shares=2),          # balanced
            "0x4": Inventory(),                                    # flat
            "0x5": Inventory(),                                    # flat
            "0x6": Inventory(up_shares=9, down_shares=1),          # naked
        }
        feed = [_spec(c) for c in ("0x1", "0x2", "0x3", "0x4", "0x5", "0x6")]
        out = order_by_exposure(feed, snap, _cid)
        assert _cids(out) == ["0x1", "0x2", "0x6", "0x3", "0x4", "0x5"]

    def test_same_input_gives_same_output(self):
        snap = {
            "0xa": Inventory(up_shares=7, down_shares=0),
            "0xb": Inventory(up_shares=5, down_shares=5),
        }
        feed = [_spec("0xa"), _spec("0xb")]
        before = list(feed)
        out1 = order_by_exposure(feed, snap, _cid)
        out2 = order_by_exposure(feed, snap, _cid)
        assert out1 == out2
        assert feed == before

    def test_every_spec_returned_exactly_once(self):
        snap = {
            "0xa": Inventory(up_shares=7, down_shares=0),
            "0xb": Inventory(up_shares=5, down_shares=5),
            "0xc": Inventory(),
            "0xd": Inventory(up_shares=1, down_shares=0),
        }
        feed = [_spec(c) for c in ("0xa", "0xb", "0xc", "0xd")]
        out = order_by_exposure(feed, snap, _cid)
        assert len(out) == len(feed)
        for spec in feed:
            assert sum(1 for o in out if o is spec) == 1

    def test_all_flat_or_empty_snapshot_keeps_feed_order(self):
        feed = [_spec(c) for c in ("0xa", "0xb", "0xc")]
        all_flat = {c: Inventory() for c in ("0xa", "0xb", "0xc")}
        assert order_by_exposure(feed, all_flat, _cid) == feed
        assert order_by_exposure(feed, {}, _cid) == feed
        held_only_elsewhere = {"0xzz": Inventory(up_shares=9, down_shares=0)}
        assert order_by_exposure(feed, held_only_elsewhere, _cid) == feed
        assert order_by_exposure([], all_flat, _cid) == []

    def test_dust_imbalance_is_not_naked(self):
        snap = {
            "0xnaked": Inventory(up_shares=3, down_shares=0),
            "0xdust": Inventory(up_shares=1e-10, down_shares=0),        # flat
            "0xbal": Inventory(up_shares=5, down_shares=5 + 1e-10),     # balanced
        }
        feed = [_spec("0xdust"), _spec("0xbal"), _spec("0xnaked")]
        out = order_by_exposure(feed, snap, _cid)
        assert _cids(out) == ["0xnaked", "0xbal", "0xdust"]

    def test_missing_from_snapshot_is_flat(self):
        snap = {"0xheld": Inventory(up_shares=4, down_shares=0)}
        feed = [_spec("0xflat"), _spec("0xheld"), _spec("0xmissing")]
        out = order_by_exposure(feed, snap, _cid)
        assert _cids(out) == ["0xheld", "0xflat", "0xmissing"]

    def test_dict_and_object_specs_and_case(self):
        # Lowercase snapshot keys must match a checksummed dict cid and an
        # object carrying `condition_id` instead of `cid`.
        snap = {
            "0xab": Inventory(up_shares=4, down_shares=0),   # naked
            "0xcd": Inventory(up_shares=2, down_shares=2),   # balanced
        }
        feed = [_obj("0xCD"), _spec("0xAB")]
        out = order_by_exposure(feed, snap, _cid)
        assert out[0] is feed[1]   # naked first despite later feed position
        assert out[1] is feed[0]

    def test_empty_cid_is_flat_and_kept(self):
        snap = {"0xa": Inventory(up_shares=4, down_shares=0)}
        no_key = {}
        feed = [no_key, _spec("0xa")]
        out = order_by_exposure(feed, snap, _cid)
        assert out[0] is feed[1]
        assert out[1] is no_key

    def test_negative_leg_is_naked(self):
        snap = {
            "0xneg": Inventory(up_shares=-1, down_shares=0),
            "0xbal": Inventory(up_shares=2, down_shares=2),
        }
        feed = [_spec("0xbal"), _spec("0xneg")]
        out = order_by_exposure(feed, snap, _cid)
        assert _cids(out) == ["0xneg", "0xbal"]
