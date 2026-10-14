"""Visit ordering by filled exposure: naked -> balanced -> flat (#505).

`order_by_exposure` is pure: it reads only its arguments, mutates nothing and
returns the same output for the same input. Ranking changes ONLY the order in
which `trader_loop.run` visits markets -- every visit still reads fresh
inventory, so no quoting or risk decision ever reads a position from here.

The snapshot maps a lowercased condition id to the `Inventory` the previous
visits already read (no extra database or venue call). A market with no entry
yet -- a new market, or any market in the first rotation -- ranks flat in its
feed position.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from core_brain.quotes import Inventory

#: Share-count dust tolerance. |up - down| at or below this is not a naked leg.
DUST = 1e-9

_RANK_NAKED = 0
_RANK_BALANCED = 1
_RANK_FLAT = 2


def _rank_of(inv: "Inventory | None") -> int:
    if inv is None:
        return _RANK_FLAT
    up = float(inv.up_shares)
    down = float(inv.down_shares)
    # A negative leg is a sell anomaly: risk first, never quiet.
    if up < 0 or down < 0 or abs(up - down) > DUST:
        return _RANK_NAKED
    if up > DUST and down > DUST:
        return _RANK_BALANCED
    return _RANK_FLAT


def order_by_exposure(
    specs: Sequence[Any],
    snapshot: Mapping[str, Inventory],
    cid_of: Callable[[Any], str],
) -> list:
    """Return the specs reordered by exposure: naked, then balanced, then flat.

    Stable: within each rank the feed order is kept. Total: every input spec
    comes back exactly once (a new list, the same objects) and the input list
    is never changed. A spec with no snapshot entry or an empty id ranks flat;
    snapshot entries with no matching spec are ignored.

    Ranking only orders the rotation's visits; each visit still reads fresh
    inventory.
    """

    def _key(spec: Any) -> int:
        cid = cid_of(spec)
        if not cid:
            return _RANK_FLAT
        return _rank_of(snapshot.get(str(cid).lower()))

    return sorted(specs, key=_key)
