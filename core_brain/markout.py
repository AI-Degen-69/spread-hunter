"""core_brain/markout.py - Out-of-band adverse selection sampler for live trading.

Measures mid-price movement post-fill at the standard markout horizons:
  mid_h0: 300s (5m)
  mid_h1: 3600s (1h)
  mid_h2: 21600s (6h)
  mid_h3: 900s (15m, exit window counterfactual)

Amendment 3 Constraint:
- Must run out-of-band and NEVER block the reconcile or poll loop.
- Network timeouts or book errors leave NULLs rather than delaying registry operations.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from core_brain.order_registry import OrderRegistry
from core_brain.config import load as load_cfg
from core_brain.market_lifecycle import resolved_condition_ids

log = logging.getLogger("markout")

_CFG = load_cfg()
MARKOUT_HORIZONS: tuple[float, ...] = getattr(
    _CFG, "markout_horizons", (300.0, 3600.0, 21600.0, 900.0)
)


# THE REFERENCE PRICE, AND WHAT IT IS MEASURED AGAINST.
#
# A single mid sampled at exactly T+300s is one draw of a bouncing quantity: on
# a two-tick book the bid-ask bounce alone makes every sell look favourable and
# every buy look poor. A size-weighted VWAP over a window centred on the horizon
# is the same measurement with the bounce averaged out.
#
# The second half matters more. A raw markout says "the price moved this far
# after we filled" and attributes all of it to us -- so a market that drifted
# for reasons that have nothing to do with this bot reads as our own adverse
# selection. Our fills are overwhelmingly passive, which makes that the common
# case rather than the corner. The peer baseline is what everyone ELSE who
# printed in the same window got over the same span; the difference between our
# outcome and theirs is the part that is about our execution.
REFERENCE_WINDOW_SEC: float = 60.0

# Our own prints sit in the same public tape as everyone else's, and the feed
# does not label them. A print is treated as ours when its price and size match
# the fill within these tolerances inside the fill's own window -- approximate
# by construction, and it under-excludes rather than over-excludes: dropping a
# stranger's print would bias the baseline toward us, which is the direction
# that flatters the number.
_PEER_PRICE_EPSILON = 1e-9
_PEER_SIZE_EPSILON = 1e-9


def vwap(trades, start_ts: float, end_ts: float) -> Optional[float]:
    """Size-weighted mean price of the prints inside [start_ts, end_ts].

    None when nothing traded in the window. A window with no prints has no
    reference price, and substituting a mid here would quietly mix two
    different measurements in one column.
    """
    total_notional = 0.0
    total_size = 0.0
    for trade in trades or []:
        if not isinstance(trade, dict):
            continue
        try:
            ts = float(trade.get("timestamp") or 0.0)
            price = float(trade.get("price") or 0.0)
            size = float(trade.get("size") or 0.0)
        except (TypeError, ValueError):
            continue
        if size <= 0 or price <= 0:
            continue
        if ts < start_ts or ts > end_ts:
            continue
        total_notional += price * size
        total_size += size
    if total_size <= 0:
        return None
    return total_notional / total_size


def windowed_reference(trades, center_ts: float,
                       window_sec: float = REFERENCE_WINDOW_SEC) -> Optional[float]:
    """VWAP over a window centred on `center_ts`."""
    half = max(0.0, window_sec) / 2.0
    return vwap(trades, center_ts - half, center_ts + half)


def _is_our_print(trade: dict, fill_price: Optional[float],
                  fill_size: Optional[float]) -> bool:
    if fill_price is None or fill_size is None:
        return False
    try:
        price = float(trade.get("price"))
        size = float(trade.get("size"))
    except (TypeError, ValueError):
        return False
    return (abs(price - fill_price) <= _PEER_PRICE_EPSILON
            and abs(size - fill_size) <= _PEER_SIZE_EPSILON)


def peer_markout(trades, fill_ts: float, reference: Optional[float],
                 direction: float, fill_price: Optional[float] = None,
                 fill_size: Optional[float] = None,
                 window_sec: float = REFERENCE_WINDOW_SEC) -> Optional[float]:
    """What everyone else who printed in the fill's window got over the span.

    Same arithmetic as our own markout -- `(reference - price) * direction` --
    applied to the other prints in the window, size-weighted. None when the
    window holds nobody but us: with no peers there is no baseline, and a zero
    would claim the market stood still.
    """
    if reference is None:
        return None
    half = max(0.0, window_sec) / 2.0
    start, end = fill_ts - half, fill_ts + half

    total = 0.0
    total_size = 0.0
    for trade in trades or []:
        if not isinstance(trade, dict):
            continue
        try:
            ts = float(trade.get("timestamp") or 0.0)
            price = float(trade.get("price") or 0.0)
            size = float(trade.get("size") or 0.0)
        except (TypeError, ValueError):
            continue
        if size <= 0 or price <= 0 or ts < start or ts > end:
            continue
        if _is_our_print(trade, fill_price, fill_size):
            continue
        total += (reference - price) * direction * size
        total_size += size
    if total_size <= 0:
        return None
    return total / total_size


def excess_markout(raw: Optional[float], peer: Optional[float]) -> Optional[float]:
    """Our markout net of what the market handed everyone else.

    None when either side is missing: an excess computed against an absent
    baseline is just the raw number wearing a different name.
    """
    if raw is None or peer is None:
        return None
    return raw - peer


def _resolve_leg(
    token_id: Optional[str], mids: dict, side_fallback: str
) -> Optional[str]:
    """Map a fill's token to the UP or DOWN leg of its market.

    Returns None when the token belongs to neither leg -- better to leave the
    markout unsampled than to attribute it to the wrong side of the book.
    """
    if token_id:
        if token_id == mids.get("_up_token"):
            return "UP"
        if token_id == mids.get("_down_token"):
            return "DOWN"
        return None
    return side_fallback if side_fallback in ("UP", "DOWN") else None


def _settlement_mid(registry, condition_id: str, token_id: str) -> Optional[float]:
    """The terminal mid for a resolved market's token: 1.0 winner, 0.0 loser.

    Reads the registry's own `resolutions` table -- written by the resolution
    sweeper (`market_resolution.sweep_market_resolutions`) from the public
    gamma feed, so no network read happens here and this can never fail on a
    credential. `None` when the condition has no recorded resolution (market
    still open, or the sweeper never reached it): the caller then leaves the
    row for a later pass, exactly as before this fallback existed.
    """
    try:
        winner_id = registry.get_winning_token_id(condition_id)
    except Exception:  # noqa: BLE001 - telemetry degrades, never blocks
        return None
    if not winner_id:
        return None
    return 1.0 if str(token_id) == str(winner_id) else 0.0


def _record_reference(registry, row: dict, markout_id: int, horizon_idx: int,
                      mid: float, token_id: Optional[str], trades_fn,
                      now: float, horizons: tuple[float, ...]) -> None:
    """Store the windowed reference and peer baseline for one horizon.

    The reference window is CENTRED on the horizon, so half of it is still in
    the future at the moment the horizon comes due. Measuring then would build
    the VWAP from the first half only and store it as though it covered the
    whole window. The row is left for a later pass instead; a horizon whose
    window has not closed by the time the row completes simply has no
    reference, which the excess aggregation already skips.
    """
    if not token_id:
        return

    fill_ts = float(row.get("ts") or 0.0)
    horizon_sec = horizons[horizon_idx] if horizon_idx < len(horizons) else 0.0
    window_closes_at = fill_ts + horizon_sec + REFERENCE_WINDOW_SEC / 2.0
    if now < window_closes_at:
        return

    trades = trades_fn(token_id, row.get("condition_id"))
    if not trades:
        return

    reference = windowed_reference(trades, fill_ts + horizon_sec)
    # No prints around the horizon means no VWAP. The mid stands in only for the
    # peer arithmetic, and the stored `ref` says plainly that it was unmeasured.
    peer_ref = reference if reference is not None else mid

    fill_price = row.get("fill_price")
    fill_size = row.get("size")
    # UNSIGNED, deliberately. `kpi.report` computes the raw drift as
    # `reference - fill_price` with no side factor, and the excess subtracts
    # the peer figure from it. Signing one side and not the other would flip
    # the excess on every SELL.
    peer = peer_markout(trades, fill_ts, peer_ref, direction=1.0,
                        fill_price=fill_price, fill_size=fill_size)
    registry.update_markout_reference(markout_id, horizon_idx, reference, peer)

    # The reference exists in `refs_json`, but the row's headline `ref_mid`
    # still reads as the fill price stamped 'contaminated' -- the hold the
    # row was born under. Retire it: the measured VWAP becomes `ref_mid` and
    # the source names the tape, so the harness's matured-markout counter and
    # the KPI pooling stop excluding this row. A None reference (window with
    # no prints) leaves the hold in place -- unmeasured is not clean.
    if reference is not None:
        registry.mark_markout_clean(markout_id, reference)


def sample_pending_markouts(
    registry: OrderRegistry,
    clob_host: str = "https://clob.polymarket.com",
    now_sec: Optional[float] = None,
    horizons: tuple[float, ...] = MARKOUT_HORIZONS,
    trades_fn=None,
) -> int:
    """Sample one pass of due markouts out-of-band. Never raises to caller.

    `trades_fn(token_id) -> list[dict]` supplies the tape the windowed
    reference and the peer baseline are built from. Left as None it reads the
    public trades feed; a caller that cannot reach it still gets the raw
    mid-based markout, unchanged.
    """
    from core_brain.markets import full_book, fetch_pinned_market

    if trades_fn is None:
        trades_fn = _default_trades_fn(horizons)

    now = now_sec if now_sec is not None else time.time()
    try:
        pending = registry.get_pending_markouts(now, horizons)
    except Exception:
        return 0

    if not pending:
        return 0

    updated_count = 0
    mids_cache: dict[str, dict[str, float]] = {}
    closed_book_mids: dict[str, Optional[float]] = {}
    settled_mids: dict[tuple[str, str], Optional[float]] = {}
    # Terminal first (#402): resolved conditions are valued from the
    # persisted settlement before any book attempt. One read per pass.
    resolved = resolved_condition_ids(registry)

    for row in pending:
        cid = row.get("condition_id")
        # `side` on a markout row is the order book's BUY/SELL, never UP/DOWN, so
        # it cannot pick a reference mid on its own. Resolve the leg from the
        # token the fill was on; fall back to the side string only for rows
        # written before token_id existed.
        row_token = row.get("token_id")
        side = str(row.get("side") or "UP").upper()
        h_idx = row.get("_due")
        m_id = row.get("id")
        if cid is None or h_idx is None or m_id is None:
            continue

        mid: Optional[float] = None
        if row_token and str(cid).lower() in resolved:
            # The market is over: 1.0 on the winning token, 0.0 on the
            # losing one, with no book request at all. An unknown winner
            # leaves the horizon pending, also with no book request.
            term_key = (str(cid), str(row_token))
            if term_key not in settled_mids:
                settled_mids[term_key] = _settlement_mid(
                    registry, str(cid), str(row_token))
            mid = settled_mids[term_key]
            if mid is None:
                continue
        else:
            # Fetch market mid if not cached
            if cid not in mids_cache:
                try:
                    m = fetch_pinned_market(cid, require_rewards=False)
                    if m:
                        up_book = full_book(clob_host, m.up_token)
                        dn_book = full_book(clob_host, m.down_token)
                        bb_up, ba_up = up_book.get("best_bid"), up_book.get("best_ask")
                        bb_dn, ba_dn = dn_book.get("best_bid"), dn_book.get("best_ask")
                        # A legitimate price of 0.0 is falsy; test for absence.
                        mid_up = (
                            (bb_up + ba_up) / 2.0
                            if (bb_up is not None and ba_up is not None)
                            else None
                        )
                        mid_dn = (
                            (bb_dn + ba_dn) / 2.0
                            if (bb_dn is not None and ba_dn is not None)
                            else None
                        )
                        mids_cache[cid] = {
                            "UP": mid_up,
                            "DOWN": mid_dn,
                            "_up_token": m.up_token,
                            "_down_token": m.down_token,
                        }
                except Exception:
                    mids_cache[cid] = {}

            mids = mids_cache.get(cid, {})
            leg = _resolve_leg(row_token, mids, side)
            mid = mids.get(leg) if leg else None

            if mid is None and row_token:
                # The pinned-market fetch refused (closed / unfunded /
                # unreachable) and left nothing to resolve a leg from. The fill's
                # own token is still known, and the book endpoint is public and
                # answers for closed markets -- read that token's book directly.
                # Keyed by TOKEN, not condition_id: both legs of one closed market
                # land here as separate rows with separate tokens, and a
                # cid-keyed entry built from the first row's token would resolve
                # nothing for the second -- the row would stay stranded exactly
                # as before the fallback existed. None is cached too: a book that
                # will not answer is retried never, not every pass.
                token_key = str(row_token)
                if token_key not in closed_book_mids:
                    try:
                        b = full_book(clob_host, token_key)
                        bb, ba = b.get("best_bid"), b.get("best_ask")
                        closed_book_mids[token_key] = (
                            (bb + ba) / 2.0
                            if (bb is not None and ba is not None)
                            else None
                        )
                    except Exception:
                        closed_book_mids[token_key] = None
                mid = closed_book_mids[token_key]

            if mid is None and row_token and row.get("condition_id"):
                # Last link in the chain: the market RESOLVED and the venue has
                # purged its book (the /book endpoint 404s for it -- confirmed on
                # the shadow-01 store, where every closed token 404s). The store's
                # own `resolutions` table holds the winner per condition, and a
                # resolved market's terminal price is not an estimate: 1.0 on the
                # winning token, 0.0 on the losing one. Sampling a horizon from
                # settlement is the truest mid a purged market can offer -- the
                # drift it produces is the outcome, not noise around it. Cached
                # per (condition, token): the resolution is market-wide but the
                # price is per leg, and a cid-keyed entry built from the first
                # row's token would misprice the second. None cached too, so a
                # market with no recorded resolution is not re-read every pass.
                last_key = (str(row["condition_id"]), str(row_token))
                if last_key not in settled_mids:
                    settled_mids[last_key] = _settlement_mid(
                        registry, str(row["condition_id"]), str(row_token))
                mid = settled_mids[last_key]

        # The windowed reference and the peer baseline, when the tape can be
        # read. Best-effort by design: a tape that will not answer leaves these
        # NULL and the raw mid-based markout is unaffected, which is the same
        # contract the rest of this sampler runs under.
        if mid is not None and trades_fn is not None:
            try:
                _record_reference(registry, row, m_id, h_idx, mid, row_token,
                                  trades_fn, now, horizons)
            except Exception as exc:  # noqa: BLE001 - degrade, never block
                # Swallowed on purpose -- the raw markout must still be
                # recorded -- but a reference that silently never appears is
                # indistinguishable from a market that never printed.
                log.debug("markout reference skipped for token %s: %r",
                          row_token, exc)

        if mid is not None:
            # Check if all other horizons are filled
            still_open = any(
                f"mid_h{j}" in row and row[f"mid_h{j}"] is None
                for j in range(len(horizons))
                if j != h_idx
            )
            try:
                registry.update_markout_horizon(m_id, h_idx, mid, last=not still_open)
                updated_count += 1
            except Exception:
                pass

    return updated_count


# One page of the tape covers minutes on a busy token, and the 6h horizon needs
# prints from six hours ago. Walk back until the page runs past the oldest
# window we could need -- bounded, because this runs per token per pass and an
# unbounded walk on a hot market would spend the sampler's whole budget on one
# row.
TAPE_PAGE_SIZE = 500
TAPE_MAX_PAGES = 6


def _default_trades_fn(horizons: tuple[float, ...] = MARKOUT_HORIZONS):
    """The public tape for one market, filtered to one token.

    The trades endpoint is queried by `market` (condition id) -- the same
    parameter `markets.recent_trades` uses -- and the rows are then filtered to
    the token being measured. A market's two legs move inversely, so mixing
    them into one VWAP would price the reference at something neither leg ever
    traded at.

    Read-only, no credentials, no signer.
    """
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    base = "https://data-api.polymarket.com/trades"
    headers = {"User-Agent": "spread-hunter"}
    span = max(horizons) if horizons else 0.0

    def fetch(token_id: str, condition_id: Optional[str] = None) -> list:
        # Everything at or after this instant can matter to a pending markout;
        # anything older cannot, so the walk stops there.
        oldest_needed = time.time() - span - REFERENCE_WINDOW_SEC
        rows: list = []
        if not condition_id:
            return rows
        for page_index in range(TAPE_MAX_PAGES):
            params = {"market": condition_id, "limit": TAPE_PAGE_SIZE,
                      "offset": page_index * TAPE_PAGE_SIZE}
            url = f"{base}?{urllib.parse.urlencode(params)}"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    page = _json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.debug("tape page %d failed for %s: %r", page_index, token_id, exc)
                break
            if not isinstance(page, list) or not page:
                break
            # Only this token's prints. The complement leg trades in the same
            # market and at the mirror price; averaging the two would produce a
            # reference neither leg ever printed at.
            rows.extend(t for t in page
                        if isinstance(t, dict) and str(t.get("asset")) == str(token_id))
            if len(page) < TAPE_PAGE_SIZE:
                break

            oldest = None
            for trade in page:
                if not isinstance(trade, dict):
                    continue
                try:
                    ts = float(trade.get("timestamp") or 0.0)
                except (TypeError, ValueError):
                    continue
                if oldest is None or ts < oldest:
                    oldest = ts
            # The page already reaches past everything a pending markout could
            # need; another page would be venue work for rows nobody reads.
            if oldest is not None and oldest <= oldest_needed:
                break
        return rows

    return fetch


class MarkoutWorker(threading.Thread):
    """Background daemon thread to sample markouts periodically without blocking main loops."""

    def __init__(
        self,
        registry: Optional[OrderRegistry] = None,
        clob_host: str = "https://clob.polymarket.com",
        interval_sec: float = 10.0,
    ) -> None:
        super().__init__(daemon=True, name="MarkoutSamplerWorker")
        self.registry = registry or OrderRegistry()
        self.clob_host = clob_host
        self.interval_sec = interval_sec
        self.stop_requested = threading.Event()

    def run(self) -> None:
        while not self.stop_requested.is_set():
            try:
                sample_pending_markouts(self.registry, self.clob_host)
            except Exception:
                pass
            self.stop_requested.wait(self.interval_sec)

    def stop(self) -> None:
        self.stop_requested.set()


def _markout_weight(row: dict) -> float:
    """Shares a row speaks for. Mirrors the paper-run markout weighting.

    A row with NO `size` key weighs 1.0 -- one row, one vote. A size that is
    present but null, zero or negative is a defective row and weighs 0.0 so it
    can neither move the mean nor pad the effective sample.
    """
    if "size" not in row:
        return 1.0
    size = row.get("size")
    if not size or size <= 0:
        return 0.0
    return float(size)


def _pool_stats(rows: list[dict], min_sample: int) -> dict:
    """Size-weighted pooled verdict. Mirrors the paper-run pooled markout statistics.

    Returns `insufficient_sample` rather than a mean when the sample is thin.
    `n` is Kish's `sum(w)^2 / sum(w^2)` -- the effective sample the size
    weighting leaves behind -- so `min_sample` keeps the meaning it was tuned
    with. Contaminated rows are dropped BEFORE weighting: a live run that
    cannot subtract our own resting size must not let one large measurement of
    our own footprint dominate the mean.
    """
    clean = [r for r in rows if r.get("ref_mid_source") != "contaminated"]
    weights = [_markout_weight(r) for r in clean]
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


def _matured_drift(row: dict, horizons: tuple[float, ...]) -> list[float]:
    """Drift at every horizon sampled, LONGEST FIRST.

    Drift, not total markout: `mid_later - ref_mid` is the adverse-selection
    term alone, and the gate must react to the market moving against us, never
    to our own entry offset -- the 15m
    exit-window read sits AFTER the 6h column in the schema but is SHORTER, so
    sorting is by duration, never by column index.
    """
    ref = row.get("ref_mid")
    if ref is None:
        return []
    out = []
    for i in range(len(horizons)):
        col = f"mid_h{i}"
        if col not in row:
            continue
        mid = row.get(col)
        if mid is not None:
            out.append((i, float(mid) - float(ref)))
    out.sort(key=lambda p: horizons[p[0]], reverse=True)
    return [d for _, d in out]


def fleet_stats(
    registry: OrderRegistry,
    min_sample: int,
    horizons: tuple[float, ...] = MARKOUT_HORIZONS,
) -> dict:
    """One verdict over EVERY market's fills, on the longest-horizon drift rule.

    Reads the registry's
    `markouts` table instead of the paper run's store. Pooled is the only
    reading that exists when no individual market matures a sample of its own
    -- the case `gate.fleet_posture` exists for -- so the fleet runner feeds
    this to that gate once per cycle.

    Every live markout row is written `ref_mid_source='contaminated'` until a
    clean reference mid is measured, and `_pool_stats` excludes those rows, so
    a run with no clean reference reads `insufficient_sample` and the posture
    stays NORMAL -- never acting on a poisoned footprint.
    """
    rows = []
    for r in registry.get_all_markouts():
        matured = _matured_drift(r, horizons)
        if not matured:
            continue
        # Longest matured horizon first -- see _matured_drift.
        rows.append({"markout": matured[0], "size": r.get("size"),
                     "ref_mid_source": r.get("ref_mid_source")})
    return _pool_stats(rows, min_sample)
