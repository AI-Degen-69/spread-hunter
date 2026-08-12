"""Markout: the cost of being filled, measured in hours instead of years.

The fleet reports rent confidently and says nothing about the other half of

    EV/day = rent/day - expected loss from unpaired fills/day

These markets resolve in 2026-2027, so settlement P&L reads $0.00 for months
and cannot answer the question in any useful timeframe. Markout answers it
early: after we were filled, where did the price actually go? A maker who is
systematically filled just before the price moves against him is losing money
no matter how healthy the rent line looks.

THE CORRECTNESS CONSTRAINT. The reference mid must exclude our own resting
size. On the best markets we hold a majority of the book (63% measured on one),
so a mid that included our own orders would measure our own footprint and
report it back as edge -- a number that looks plausible and means nothing.

In paper mode this is automatic: our quotes live in QueueFillEngine and are
never sent to the venue, so the fetched book is already clean. A LIVE run has
no such guarantee and must record `ref_mid_source='contaminated'`, which
excludes those rows from every aggregate below rather than silently poisoning
them.
"""
from __future__ import annotations

from strategy import store
from strategy.config import load as load_cfg

# Horizon lengths in tuple order -- column i of the markouts table is written
# at horizon `_HORIZONS[i]`. Loaded once: the horizons never change mid-run.
_HORIZONS = load_cfg().markout_horizons


def markout_per_share(fill_price: float, mid_later: float, side: str) -> float:
    """Cost of one filled share, in price units.

    We only ever buy, so a mid that sits below our fill price means the fill
    was informed against us. `side` is part of the signature because each side
    is measured against its OWN token's mid -- buying DOWN at 0.38 is scored
    against the DOWN mid, not against 1 minus the UP mid. Once the caller has
    supplied the right mid the arithmetic is identical for both, which is why
    the parameter is not branched on.
    """
    return mid_later - fill_price


def _weight(row: dict) -> float:
    """Shares this row speaks for.

    A row with NO `size` key weighs 1.0 -- one row, one vote, which is exactly
    the unweighted mean this function used to compute. That branch never fires
    in production: every live row comes from `store.markout_rows()`, a
    `SELECT *` over a table that has carried `size` since it was created. It
    exists so a caller that supplies no sizes degrades to the old behaviour
    instead of silently weighing all its evidence at zero and reading
    `insufficient_sample` on a full sample.

    A size that IS present but null, zero or negative is different in kind: it
    is a defective row, not an unsized caller, and it weighs 0.0 so it can
    neither move the mean nor pad the effective sample.
    """
    if "size" not in row:
        return 1.0
    size = row.get("size")
    if not size or size <= 0:
        return 0.0
    return float(size)


def _stats_from_rows(rows: list[dict], min_sample: int) -> dict:
    """Aggregate one market's fills into a verdict, weighted by size.

    Returns `insufficient_sample` rather than a mean when the sample is thin.
    That is the load-bearing behaviour: a three-fill mean on a thin book is
    noise, and the gate consuming this would happily evict a sound market on
    it, forfeiting real rent for an imaginary reason.

    WHY WEIGHTED. An unweighted mean gives a 10-share print and a 200-share
    print the same vote. Measured on the 2026-08-04 sample: two prints carrying
    233 shares each counted once apiece against fifty ~50-share prints, and
    every one of the 23 live markets inherited the same pooled reading of
    -0.052375/share on n=52. The money does not experience a mean over fills,
    it experiences a mean over shares, so that is what the gate is handed.

    WHY AN EFFECTIVE SAMPLE. Once rows are weighted the row count stops
    describing how much evidence the mean rests on -- ten fills where one
    carries 90% of the size is roughly one observation. `n` is therefore Kish's
    `sum(w)^2 / sum(w^2)`, which equals the row count EXACTLY when the sizes
    are equal, so `markout_min_sample` and the doubling rule in
    `gate.next_state` keep the meaning they were tuned with and need no
    re-derivation. `n_rows` carries the raw count for logging.

    Contaminated rows are dropped BEFORE weighting: a live run that cannot
    subtract our own resting size would otherwise let one large measurement of
    our own footprint dominate the mean, which is strictly worse than the
    unweighted version of the same bug. Zero total weight -- every row unsized
    or defective -- is an absence of evidence, not a division by zero.
    """
    clean = [r for r in rows if r.get("ref_mid_source") != "contaminated"]
    weights = [_weight(r) for r in clean]
    total = sum(weights)
    if total <= 0:
        return {"n": 0.0, "n_rows": len(clean),
                "verdict": "insufficient_sample", "mean_per_share": None}
    n_eff = total * total / sum(w * w for w in weights)
    if n_eff < min_sample:
        return {"n": n_eff, "n_rows": len(clean),
                "verdict": "insufficient_sample", "mean_per_share": None}
    mean = sum(w * r["markout"] for w, r in zip(weights, clean)) / total
    return {"n": n_eff, "n_rows": len(clean), "mean_per_share": mean,
            "verdict": "losing" if mean < 0 else "earning"}


def drift_per_share(ref_mid: float, mid_later: float) -> float:
    """How far the market moved AFTER it filled us -- the adverse-selection
    term on its own.

    This is the correction to the original design, and it matters. Total
    markout is `mid_later - fill_price`, which silently includes the ~2c we
    quote under mid. A market whose price never moved therefore reads +2.15c
    and looks like pure edge, and a quality gate built on it could only trip
    if drift exceeded -2.5c -- a catastrophe detector, not the erosion monitor
    it was meant to be.

    Measured live on 2026-07-29: +2.11c captured spread, +0.04c drift. Almost
    all of the apparent edge was our own entry discount handed back to us.
    """
    return mid_later - ref_mid


def _matured(row: dict) -> list[float]:
    """Drift at every horizon already sampled for this fill, LONGEST FIRST.

    Deliberately drift and not total: this feeds the gate, and the gate must
    react to the market moving against us, never to our own offset.

    Longest first by DURATION, not by column (Session 50): the 15m exit-window
    read is APPENDED to the schema as mid_h3, after the 6h column, so column
    order and horizon length diverge. A fill with both the 6h and the 15m
    reading recorded must be judged on the 6h one -- the 15m reading is the
    exit counterfactual, not the gate's evidence. Iteration stops at the
    columns the row actually carries (`SELECT *`), so a pre-migration row or
    an older fixture degrades to the horizons it has.
    """
    ref = row.get("ref_mid")
    if ref is None:
        return []
    out = []
    i = 0
    while f"mid_h{i}" in row and i < len(_HORIZONS):
        mid = row.get(f"mid_h{i}")
        if mid is not None:
            out.append((i, drift_per_share(ref, mid)))
        i += 1
    out.sort(key=lambda p: _HORIZONS[p[0]], reverse=True)
    return [d for _, d in out]


def per_market_stats(min_sample: int) -> dict[str, dict]:
    """Per-market verdicts, using each fill's LONGEST matured horizon.

    The longest horizon is the honest one: a 5-minute reading on a market that
    resolves in 2027 mostly measures microstructure noise, while the 6-hour
    reading is where genuine repricing on news would show up. Fills with no
    matured horizon yet contribute nothing.
    """
    by: dict[str, list[dict]] = {}
    for r in store.markout_rows():
        matured = _matured(r)
        if not matured:
            continue
        # `_matured` returns longest-first (Session 50: mid_h3, the 15m
        # exit-window read, sits AFTER the 6h column in the schema but is
        # SHORTER), so the longest matured horizon is the first element.
        by.setdefault(r["condition_id"], []).append(
            {"markout": matured[0], "size": r.get("size"),
             "ref_mid_source": r.get("ref_mid_source")})
    return {cid: _stats_from_rows(rows, min_sample) for cid, rows in by.items()}


def fleet_stats(min_sample: int) -> dict:
    """One verdict over EVERY market's fills, on the same longest-horizon rule.

    `per_market_stats` is the right instrument for evicting one market and the
    wrong one for noticing that the whole universe is toxic. Its sample is thin
    by construction -- markets here rotate daily, so a market can take money off
    us for its entire life without ever reaching `markout_min_sample` fills of
    its own. Measured 2026-08-02: 47 markout rows across 18 markets, best
    per-market sample 7, so every market read `insufficient_sample` on every
    cycle and the gate never moved off NORMAL.

    Pooled, the same run gives n=42 -- enough to read. The caller uses this as
    the FALLBACK verdict for a market that has none of its own, which is the
    only way a young market inherits anything other than NORMAL.

    Same `_stats_from_rows` contract as the per-market path, so the gate cannot
    tell the two apart and needs no new branch: contaminated rows are excluded,
    and a thin pool still returns `insufficient_sample` rather than a mean.
    """
    rows: list[dict] = []
    for r in store.markout_rows():
        matured = _matured(r)
        if not matured:
            continue
        # Longest matured horizon first -- see per_market_stats.
        rows.append({"markout": matured[0], "size": r.get("size"),
                     "ref_mid_source": r.get("ref_mid_source")})
    return _stats_from_rows(rows, min_sample)


def sample_due(mids_by_cid: dict, now: float, horizons) -> int:
    """Record the mid at every horizon that has just matured.

    `mids_by_cid` maps condition_id -> {"UP": mid, "DOWN": mid}. Returns how
    many rows were updated so the caller can log progress. A market we have no
    fresh book for is skipped and retried next cycle rather than recorded
    against a stale price.
    """
    n = 0
    for row in store.pending_markouts(now, horizons):
        mids = mids_by_cid.get(row["condition_id"])
        if not mids:
            continue
        mid = mids.get(row["side"])
        if mid is None:
            continue
        i = row["_due"]
        # `done` must mean "every horizon recorded", not "the last tuple
        # element was written". Horizons are APPENDED, never re-sorted
        # (Session 50): the 15m exit-window read matures BEFORE the 1h and 6h
        # ones while sitting after them in the tuple, so marking done at
        # len(horizons)-1 would seal the row while two readings were still
        # owed -- they would never be written. Only columns the row actually
        # carries are counted, so a pre-migration row keeps the old behaviour.
        still_open = any(
            f"mid_h{j}" in row and row[f"mid_h{j}"] is None
            for j in range(len(horizons)) if j != i)
        store.close_markout(row["id"], i, mid, last=not still_open)
        n += 1
    return n
