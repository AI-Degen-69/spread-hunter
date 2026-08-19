"""Milestone 5 - Controlled test proving fork equivalence between strategy/ and live/engine/.

Fed with identical frozen book snapshots, identical config parameters, and identical
inventory state, both `strategy.quotes.decide_quotes` and `engine.quotes.decide_quotes`
must produce equivalent quote intents.

Executed in isolated subprocess to prevent leaking root `strategy` onto live suite's sys.path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import pytest

LIVE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_ROOT.parent


def _run_comparison(payload_code: str) -> dict:
    runner = f"""
import sys, json
sys.path.insert(0, r"{REPO_ROOT}")
sys.path.insert(0, r"{LIVE_ROOT}")

import strategy.quotes as sim_quotes
import strategy.config as sim_config
import engine.quotes as live_quotes
import engine.config as live_config

{payload_code}
"""
    res = subprocess.run([sys.executable, "-c", runner], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Subprocess failed (exit {res.returncode}):\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
    return json.loads(res.stdout.strip())


def test_controlled_fork_equivalence_two_sided():
    """Frozen controlled test asserting strategy/ and live/engine/ produce identical intents."""
    script = """
sim_cfg = sim_config.MakerConfig(
    objective="rewards",
    quote_shares=120,
    min_quote_shares=50,
    reward_offset=0.02,
    max_spread_from_mid=0.045,
    price_band_low=0.10,
    price_band_high=0.90,
    price_tick=0.001,
    max_naked_usd=120.0,
)
live_cfg = live_config.MakerConfig(
    objective="rewards",
    # The trees are equal in the SHARES mode the simulation runs. Dollar
    # sizing is a live-only divergence and is characterised separately --
    # asserting equality with it on would compare two different rules and
    # call the difference a bug.
    size_mode="shares",
    quote_shares=120,
    min_quote_shares=50,
    reward_offset=0.02,
    max_spread_from_mid=0.045,
    price_band_low=0.10,
    price_band_high=0.90,
    price_tick=0.001,
    max_naked_usd=120.0,
)

up_book = {
    "token_id": "0xUP_TOKEN",
    "best_bid": 0.50,
    "best_ask": 0.52,
    "bids": {0.50: 1000.0},
    "asks": {0.52: 1000.0},
}
down_book = {
    "token_id": "0xDOWN_TOKEN",
    "best_bid": 0.48,
    "best_ask": 0.50,
    "bids": {0.48: 1000.0},
    "asks": {0.50: 1000.0},
}

sim_inv = sim_quotes.Inventory(up_shares=0.0, down_shares=0.0, up_cost=0.0, down_cost=0.0, fills=0)
live_inv = live_quotes.Inventory(up_shares=0.0, down_shares=0.0, up_cost=0.0, down_cost=0.0, fills=0)

t_remaining = 1e9

sim_intents, sim_why = sim_quotes.decide_quotes(sim_cfg, up_book, down_book, sim_inv, t_remaining, None)
live_intents, live_why = live_quotes.decide_quotes(live_cfg, up_book, down_book, live_inv, t_remaining, None)

out = {
    "sim_why": sim_why,
    "live_why": live_why,
    "sim_intents": [{"side": i.side, "token_id": i.token_id, "price": i.price, "size": i.size, "mid": i.mid, "edge": i.edge_vs_mid, "crossed": i.crossed} for i in sim_intents],
    "live_intents": [{"side": i.side, "token_id": i.token_id, "price": i.price, "size": i.size, "mid": i.mid, "edge": i.edge_vs_mid, "crossed": i.crossed} for i in live_intents],
}
print(json.dumps(out))
"""
    data = _run_comparison(script)
    assert data["sim_why"] == data["live_why"] == ""
    assert len(data["sim_intents"]) == len(data["live_intents"]) == 2
    for s, l in zip(data["sim_intents"], data["live_intents"]):
        assert s["side"] == l["side"]
        assert s["token_id"] == l["token_id"]
        assert s["price"] == pytest.approx(l["price"], abs=1e-6)
        assert s["size"] == l["size"]
        assert s["mid"] == pytest.approx(l["mid"], abs=1e-6)
        assert s["edge"] == pytest.approx(l["edge"], abs=1e-6)
        assert s["crossed"] == l["crossed"]


def test_controlled_fork_equivalence_with_inventory_skew():
    """Controlled test with non-zero inventory producing identical skew offsets and size taper."""
    script = """
sim_cfg = sim_config.MakerConfig(
    objective="rewards",
    quote_shares=120,
    min_quote_shares=50,
    reward_offset=0.02,
    max_spread_from_mid=0.045,
    max_skew=0.015,
    max_naked_usd=120.0,
)
live_cfg = live_config.MakerConfig(
    objective="rewards",
    # The trees are equal in the SHARES mode the simulation runs. Dollar
    # sizing is a live-only divergence and is characterised separately --
    # asserting equality with it on would compare two different rules and
    # call the difference a bug.
    size_mode="shares",
    quote_shares=120,
    min_quote_shares=50,
    reward_offset=0.02,
    max_spread_from_mid=0.045,
    max_skew=0.015,
    max_naked_usd=120.0,
)

up_book = {
    "token_id": "0xUP",
    "best_bid": 0.51,
    "best_ask": 0.53,
    "bids": {0.51: 5000.0},
    "asks": {0.53: 5000.0},
}
down_book = {
    "token_id": "0xDOWN",
    "best_bid": 0.47,
    "best_ask": 0.49,
    "bids": {0.47: 5000.0},
    "asks": {0.49: 5000.0},
}

# Long UP inventory: 60 shares at 0.50 ($30 cost)
sim_inv = sim_quotes.Inventory(up_shares=60.0, down_shares=0.0, up_cost=30.0, down_cost=0.0, fills=1)
live_inv = live_quotes.Inventory(up_shares=60.0, down_shares=0.0, up_cost=30.0, down_cost=0.0, fills=1)

t_remaining = 1e9

sim_intents, sim_why = sim_quotes.decide_quotes(sim_cfg, up_book, down_book, sim_inv, t_remaining, None)
live_intents, live_why = live_quotes.decide_quotes(live_cfg, up_book, down_book, live_inv, t_remaining, None)

out = {
    "sim_why": sim_why,
    "live_why": live_why,
    "sim_intents": [{"side": i.side, "price": i.price, "size": i.size, "edge": i.edge_vs_mid} for i in sim_intents],
    "live_intents": [{"side": i.side, "price": i.price, "size": i.size, "edge": i.edge_vs_mid} for i in live_intents],
}
print(json.dumps(out))
"""
    data = _run_comparison(script)
    assert data["sim_why"] == data["live_why"] == ""
    assert len(data["sim_intents"]) == len(data["live_intents"]) == 1
    for s, l in zip(data["sim_intents"], data["live_intents"]):
        assert s["side"] == l["side"]
        assert s["price"] == pytest.approx(l["price"], abs=1e-6)
        assert s["size"] == l["size"]
        assert s["edge"] == pytest.approx(l["edge"], abs=1e-6)
