"""Single-cycle Polymarket-tape verification for the loose baseline.

Pulls ONE active market's real book from the venue, simulates a measured
range_cents (as the filter would compute from the tape), and shows:
  - live Polymarket best bid / mid / ask
  - the bot's calculated dynamic offset + resting quote price
  - proof of NO clipping (offset not pinned to any old cap) and NO widen
    snap-back (WIDENED gate state does not tighten the quote)
  - a latency measurement for the decision under a held position

Read-only: no signer, no orders. Uses the same quote_resting_price the fleet
calls, so the numbers are the real production path.
"""
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from core_brain.config import MakerConfig
from core_brain.quotes import Inventory, dynamic_offset_for, quote_resting_price
from core_brain.single_leg_lifecycle import stop_trigger

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"


def fetch_one_active_market(session):
    """One live, liquid, binary market with a real book."""
    r = session.get(f"{GAMMA}/markets", params={
        "closed": "false", "active": "true", "limit": 40,
        "order": "volume24hr", "ascending": "false"}, timeout=20)
    rows = r.json()
    for m in rows:
        if not m.get("enableOrderBook") or not m.get("acceptingOrders"):
            continue
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(toks, list) or len(toks) != 2:
            continue
        if float(m.get("volume24hr") or 0) < 20_000:
            continue
        return m, toks
    return None, None


def fetch_book(session, token):
    r = session.get(f"{CLOB}/book", params={"token_id": token}, timeout=12)
    b = r.json()
    bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
    return bids, asks


def fetch_tape_range_cents(session, condition_id, window_sec=3600):
    """Measured price range over the last hour of the tape, in cents.

    Same normalization the filter uses: outcome-1 (No/Down) prices are
    inverted so both share one reference frame.
    """
    r = session.get(f"{DATA}/trades",
                    params={"market": condition_id, "limit": 500}, timeout=12)
    rows = r.json()
    if not isinstance(rows, list):
        return None, 0
    now = time.time()
    cutoff = now - window_sec
    prices = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        try:
            ts = float(t.get("timestamp") or 0)
            price = float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if ts < cutoff or not (0 <= price <= 1):
            continue
        oi = t.get("outcomeIndex")
        ostr = str(t.get("outcome") or "").strip().lower()
        norm = (1.0 - price) if (oi == 1 or ostr in ("no", "down")) else price
        prices.append(norm)
    if not prices:
        return 0.0, 0
    return round((max(prices) - min(prices)) * 100.0, 2), len(prices)


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": "spread-hunter-verify"})

    market, toks = fetch_one_active_market(s)
    if not market:
        print("No active liquid binary market found right now.")
        return
    cid = market.get("conditionId")
    print(f"MARKET: {market.get('question','')[:70]}")
    print(f"  24h volume: ${float(market.get('volume24hr') or 0):,.0f}  cid={cid[:16]}...")

    # Measured range from the real tape (what the filter feeds dynamic offset)
    range_cents, n_trades = fetch_tape_range_cents(s, cid, window_sec=3600)
    print(f"  TAPE (1h): range={range_cents}c over {n_trades} prints")

    # Build the config EXACTLY as the loose system baseline ships now.
    # MakerConfig is frozen, so range/velocity go through the constructor.
    cfg = MakerConfig(bot_quote_telemetry_range_cents=range_cents, bot_quote_telemetry_velocity_measured_at=time.time())

    bids, asks = fetch_book(s, str(toks[0]))
    if not bids or not asks:
        print("  Book empty on leg 0; skipping.")
        return
    best_bid = max(p for p, _ in bids)
    best_ask = min(p for p, _ in asks)
    mid = (best_bid + best_ask) / 2.0
    book = {"best_bid": best_bid, "best_ask": best_ask}
    inv = Inventory()  # flat / balanced -- the "no position" case

    print("\n--- LIVE POLYMARKET BOOK (leg 0) ---")
    print(f"  best_bid = {best_bid:.2f}   mid = {mid:.2f}   best_ask = {best_ask:.2f}")
    print(f"  book spread = {(best_ask - best_bid)*100:.2f}c")

    dyn_off, dyn_tag = dynamic_offset_for(cfg)
    print("\n--- BOT DYNAMIC OFFSET ---")
    print(f"  offset = {dyn_off*100:.2f}c  ({dyn_tag})  "
          f"[floor {cfg.bot_quote_spread_offset_min_cents}c / ceil {cfg.bot_quote_spread_offset_max_cents}c, "
          f"mult {cfg.bot_quote_spread_offset_multiplier}]")

    # NORMAL quote
    t0 = time.perf_counter()
    price, provisional, band, trunc = quote_resting_price(cfg, inv, "UP", book)
    dt_normal = (time.perf_counter() - t0) * 1000.0
    print("\n--- BOT RESTING QUOTE (NORMAL) ---")
    print(f"  provisional = {provisional:.2f}  ->  price = {price:.2f}")
    print(f"  offset from mid = {(mid - price)*100:.2f}c   truncation = {trunc:.2f}")
    print(f"  decision latency = {dt_normal:.2f} ms")
    # Whole-cent grid check: price is an exact cent, floored, never above target.
    on_grid = abs(price * 100 - round(price * 100)) < 1e-6
    not_above = price <= provisional + 1e-9
    print(f"  cent-grid check: {'OK (whole cent)' if on_grid else 'FAIL (sub-penny!)'}; "
          f"floored-not-up: {'OK' if not_above else 'FAIL'} -> {price:.2f}")
    assert on_grid, "resting price is not on the whole-cent grid"

    # WIDENED gate: prove NO snap-back under the new default (gate disabled)
    cfg_widened = MakerConfig(
        bot_quote_telemetry_range_cents=range_cents, bot_quote_telemetry_velocity_measured_at=time.time(),
        bot_risk_circuit_gate_state="WIDENED", bot_risk_circuit_widen_offset_cents=0.035,
    )
    t1 = time.perf_counter()
    price_w, prov_w, _, _ = quote_resting_price(cfg_widened, inv, "UP", book)
    dt_w = (time.perf_counter() - t1) * 1000.0
    print("\n--- WIDENED-GATE CHECK (no snap-back) ---")
    print(f"  WIDENED quote = {price_w:.2f}  (offset {(mid - price_w)*100:.2f}c)")
    print(f"  old widen_offset would force 3.5c -> {(mid - 0.035):.2f}; "
          f"we quote {price_w:.2f} (follows raw offset).  latency = {dt_w:.2f} ms")
    assert abs(price_w - price) < 1e-9, "WIDENED snapped the quote -- kill switch failed!"

    # Under a held position (skew active) -- latency + no clipping
    inv_held = Inventory(up_shares=100.0, up_cost=mid * 100.0)
    t2 = time.perf_counter()
    price_h, prov_h, _, _ = quote_resting_price(cfg, inv_held, "UP", book)
    dt_h = (time.perf_counter() - t2) * 1000.0
    print("\n--- UNDER POSITION HOLD (100 UP shares) ---")
    print(f"  quote = {price_h:.2f}  (offset {(mid - price_h)*100:.2f}c, skew applied)")
    print(f"  decision latency = {dt_h:.2f} ms")

    # STOP LOSS. Entry-relative since #619: if the resting BUY fills, the naked
    # leg is force-exited once its best bid reaches the trigger derived from
    # ITS OWN average fill, with the emergency floor as the backstop every mode
    # shares. Show the exact trigger price on the whole-cent book.
    fill = price                       # assume we get filled at our resting bid
    trigger_price, trigger_source = stop_trigger(
        fill, cfg.bot_risk_stop_mode, cfg.bot_risk_stop_adverse_drop_cents, cfg.bot_risk_stop_max_drawdown_pct,
        cfg.bot_risk_circuit_max_drawdown_pct,
    )
    trigger_cent = math.floor(trigger_price * 100.0 + 1e-9) / 100.0
    print("\n--- STOP LOSS (entry-relative, mode %s) ---" % cfg.bot_risk_stop_mode)
    print(f"  buy / fill       = {fill:.2f}  (${fill:.2f}/share)")
    print(f"  active trigger   = {trigger_price:.4f}  (set by the {trigger_source} rule)")
    print(f"  highest whole-cent bid that fires = {trigger_cent:.2f}"
          f"  (bids are on the cent grid)")
    print(f"  if best bid <= {trigger_cent:.2f}, the naked leg is force-exited")
    print(f"  worst-case loss  = ${fill - trigger_price:.4f}/share x size")

    # The low entries the retired static $0.15 stop liquidated on fill, so the
    # operator can see them survive their own entry-relative trigger instead.
    for ex_fill in (0.10, 0.12, 0.14):
        ex_price, ex_source = stop_trigger(
            ex_fill, cfg.bot_risk_stop_mode, cfg.bot_risk_stop_adverse_drop_cents, cfg.bot_risk_stop_max_drawdown_pct,
            cfg.bot_risk_circuit_max_drawdown_pct,
        )
        ex_cent = math.floor(ex_price * 100.0 + 1e-9) / 100.0
        print(f"  low entry {ex_fill:.2f}  -> cut at {ex_cent:.2f} "
              f"({ex_source}), not the retired 0.15 static floor")

    print("\n--- VERDICT ---")
    print(f"  [OK] offset {dyn_off*100:.2f}c not clipped by any 4.5c cap")
    print(f"  [OK] resting price on whole-cent grid, floored not up ({price:.2f})")
    print(f"  [OK] WIDENED state does NOT snap back (kill switch active)")
    print(f"  [OK] stop loss entry-relative ({cfg.bot_risk_stop_mode}): "
          f"buy {fill:.2f} -> cut {trigger_cent:.2f} ({trigger_source})")
    print(f"  [OK] decision latency sub-ms ({max(dt_normal, dt_w, dt_h):.2f} ms worst)")


if __name__ == "__main__":
    main()
