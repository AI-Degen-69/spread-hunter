"""Generate a standalone demonstration snapshot of the redesigned filter pipeline for UI inspection.

Populates runtime/pipeline.json and runtime/market_universe.json with representative
markets so the operator can inspect the funnel and Kanban columns on the dashboard
(http://127.0.0.1:8799 -> Tab 3: Market Filter Pipeline) without live network calls.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import scripts.filter_markets as fm
from scripts.filter_markets import _cause


def generate_preview_snapshot(out_dir: Path | str = "runtime") -> Path:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    now_ts = time.time()

    # 1. Eligible Live eSports Market (prioritized in Passed/Eligible column)
    cs2_live = {
        "source": "spread",
        "spread": 0.015,
        "eligible": True,
        "reject_reason": "",
        "volume_24h": 48_000.0,
        "days_to_resolve": 1.2,
        "cid": "0xcs2_live_demo",
        "title": "IEM Cologne: NAVI vs. FaZe Clan",
        "slug": "esports-cs2-navi-faze-live",
        "category": "eSports",
        "venue_category": "eSports",
        "tags": ["eSports", "CS2"],
        "market_type": "moneyline",
        "market_group": "",
        "series_title": "CS2 IEM Cologne",
        "event_title": "NAVI vs. FaZe Clan",
        "sports_market_type": "moneyline",
        "volatility_exempt": True,
        "event_live": True,
        "movement_usd": 12_500.0,
        "trade_count": 85,
        "daily": 0.0,
        "their_score": 5200.0,
        "est_income": 3.8,
        "est_capital": 120.0,
        "return_pct_day": 3.16,
    }

    # 2. Eligible Standard Market
    eth_eligible = {
        "source": "spread",
        "spread": 0.018,
        "eligible": True,
        "reject_reason": "",
        "volume_24h": 320_000.0,
        "days_to_resolve": 4.5,
        "cid": "0xeth_elig_demo",
        "title": "Will Ethereum reach $10,000 by year end?",
        "slug": "crypto-eth-10k-year-end",
        "category": "Crypto",
        "venue_category": "Crypto",
        "tags": ["Crypto", "ETH"],
        "market_type": "",
        "market_group": "",
        "series_title": "Ethereum",
        "event_title": "Ethereum price",
        "volatility_exempt": False,
        "event_live": False,
        "movement_usd": 28_000.0,
        "trade_count": 140,
        "daily": 0.0,
        "their_score": 8400.0,
        "est_income": 2.9,
        "est_capital": 120.0,
        "return_pct_day": 2.42,
    }

    # 3. Rejected: Decided Mid outside [0.15, 0.85]
    mid_low = {
        "source": "spread",
        "spread": 0.02,
        "eligible": False,
        "reject_reason": "YES: decided mid 0.10 outside [0.15, 0.85]",
        "volume_24h": 210_000.0,
        "days_to_resolve": 3.0,
        "cid": "0xmid_low_demo",
        "title": "Will Solana flip Ethereum in market cap?",
        "slug": "solana-flip-ethereum",
        "category": "Crypto",
        "their_score": 4000.0,
    }

    mid_high = {
        "source": "spread",
        "spread": 0.02,
        "eligible": False,
        "reject_reason": "YES: decided mid 0.90 outside [0.15, 0.85]",
        "volume_24h": 180_000.0,
        "days_to_resolve": 2.5,
        "cid": "0xmid_high_demo",
        "title": "Will Bitcoin remain above $50,000 this month?",
        "slug": "btc-above-50k",
        "category": "Crypto",
        "their_score": 3500.0,
    }

    # 4. Rejected: Spread exceeding 0.02
    spread_wide = {
        "source": "spread",
        "spread": 0.035,
        "eligible": False,
        "reject_reason": "YES: spread 0.0350 > 0.0200",
        "volume_24h": 190_000.0,
        "days_to_resolve": 5.0,
        "cid": "0xspread_wide_demo",
        "title": "Who will win Best Picture at the Oscars?",
        "slug": "oscars-best-picture",
        "category": "Culture",
        "their_score": 6000.0,
    }

    # 5. Rejected: Depth below $500
    depth_thin = {
        "source": "spread",
        "spread": 0.015,
        "eligible": False,
        "reject_reason": "YES: top-3 bid depth $140.00 <= $500.00",
        "volume_24h": 140_000.0,
        "days_to_resolve": 6.0,
        "cid": "0xdepth_thin_demo",
        "title": "Will SpaceX launch Starship Flight 6 this quarter?",
        "slug": "spacex-starship-flight-6",
        "category": "Space",
        "their_score": 2500.0,
    }

    # 6. Rejected: Volume below $125,000
    volume_low = {
        "source": "spread",
        "spread": 0.015,
        "eligible": False,
        "reject_reason": "24h volume $45,000 < $125,000",
        "volume_24h": 45_000.0,
        "days_to_resolve": 3.0,
        "cid": "0xvolume_low_demo",
        "title": "Federal Reserve Rate Decision in November",
        "slug": "fed-rate-decision-nov",
        "category": "Macro",
        "their_score": 3200.0,
    }

    # 7. Rejected: Horizon exceeding 30 days
    horizon_far = {
        "source": "spread",
        "spread": 0.015,
        "eligible": False,
        "reject_reason": "horizon: 45.0 days > 30.0 days",
        "volume_24h": 250_000.0,
        "days_to_resolve": 45.0,
        "cid": "0xhorizon_far_demo",
        "title": "US Presidential Election 2028 Nominee",
        "slug": "us-election-2028-nominee",
        "category": "Politics",
        "their_score": 9000.0,
    }

    all_markets = [
        cs2_live, eth_eligible, mid_low, mid_high,
        spread_wide, depth_thin, volume_low, horizon_far,
    ]

    eligible = [cs2_live, eth_eligible]
    rejected = [m for m in all_markets if not m["eligible"]]

    causes = {}
    for m in rejected:
        c = _cause(m["reject_reason"])
        causes[c] = causes.get(c, 0) + 1

    fm._write_pipeline_snapshot(
        cands=[],
        spread_cands=all_markets,
        out=all_markets,
        eligible=eligible,
        picked=eligible,
        causes=causes,
        census={"scanned": len(all_markets), "sample_size": len(all_markets)},
        gates={
            "price_band": "[0.15, 0.85]",
            "max_spread": 0.02,
            "min_volume": 125_000.0,
            "min_depth": 500.0,
            "live_volume_floor": 10_000.0,
        },
        attempted=len(all_markets),
        rejected=len(rejected),
        depth_gate_usd=500.0,
        volume_gate_usd=125_000.0,
        spread_gate=0.02,
        out_dir=out_path,
    )

    fm._write_universe_file(
        universe_rows=all_markets,
        discovery_meta={"source": "preview_filter_pipeline", "count": len(all_markets)},
        out_dir=out_path,
    )

    return out_path / "pipeline.json"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate demo preview snapshots for UI inspection.")
    parser.add_argument(
        "--out-dir",
        default="runtime",
        help="Directory to write preview snapshots (default: runtime for dashboard viewing)",
    )
    args = parser.parse_args()

    target = generate_preview_snapshot(out_dir=args.out_dir)
    print("=" * 70)
    print("  [DEMO DATA] MARKET FILTER PIPELINE — PREVIEW SNAPSHOT GENERATED")
    print("  WARNING: Contains simulated demonstration data for visual UI inspection.")
    print("=" * 70)
    print(f"  Snapshot written to: {target.resolve()}")
    print()
    print("  To inspect visually in the dashboard UI:")
    print("  1. Ensure dashboard is running: python -m dashboard.server")
    print("  2. Open: http://127.0.0.1:8799")
    print("  3. Navigate to: Tab 3 (Market Filter Pipeline)")
    print("  4. Observe Kanban columns:")
    print("     - DECIDED MID column: mid 0.10 and 0.90 rejections")
    print("     - SPREAD column: spread > 0.02 rejections")
    print("     - DEPTH / VOLUME / HORIZON columns: appropriate gate cards")
    print("     - ELIGIBLE / PASSED column: prioritized Live eSports market (IEM Cologne)")
    print("=" * 70)
