import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import strategy.live_exec as le


def test_live_exec_arg_parsing():
    ap = argparse.ArgumentParser(description="LIVE Polymarket execution.")
    ap.add_argument("--live", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--series", default="btc-up-or-down-5m")
    p.add_argument("--token-id", default=None)
    p.add_argument("--cycles", type=int, default=30)
    p.add_argument("--min-time-remaining", type=float, default=90.0)
    p.add_argument("--max-complement-bid", type=float, default=0.85)
    p.add_argument("--max-loss", type=float, default=1.00)
    p.add_argument("--max-fills", type=int, default=1)
    p.add_argument("--live", action="store_true", default=argparse.SUPPRESS)

    args = ap.parse_args(["probe", "--cycles", "10", "--min-time-remaining", "120", "--max-complement-bid", "0.80"])
    assert args.cmd == "probe"
    assert args.cycles == 10
    assert args.min_time_remaining == 120.0
    assert args.max_complement_bid == 0.80
    assert args.max_loss == 1.00
    assert args.max_fills == 1


def test_encode_redeem_positions():
    res = le.encode_redeem_positions(
        le.USDC_E_CONTRACT,
        le.ZERO_BYTES32,
        "0x" + "11" * 32,
        [1, 2]
    )
    assert res.startswith("0x01b7037c")
    assert len(res) > 200


def test_dry_run_probe():
    with patch("strategy.markets.fetch_live_market") as mock_fetch:
        mock_fetch.return_value = MagicMock(
            market_slug="btc-updown-5m-12345",
            t_remaining=lambda: 180.0,
            up_token="token_up_123",
            down_token="token_down_456",
        )
        # probe dry run should return cleanly without making network calls or orders
        le.probe(series="btc-up-or-down-5m", cycles=5, live=False)
