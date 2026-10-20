"""Standalone stat-gate dry-calc CLI.

Zero network, zero database. Computes power tables and gate definitions from
pure math using only core_brain.kpi and core_brain.config.

Usage:
    python scripts/stat_gate.py --dry-calc [--sigma 0.05] [--bankroll 100] [--json]

Issue #51: Stat Validation #1.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Sequence

# Ensure repo root is importable when run as ``python scripts/stat_gate.py``.
_LIVE_ROOT = Path(__file__).resolve().parent.parent
if str(_LIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIVE_ROOT))

from core_brain.config import load as load_cfg
from core_brain.kpi import (
    Z_ALPHA_90_ONE_SIDED,
    Z_BETA_80_POWER,
    gate_definition,
    power_table,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Statistical power & gate calculator (offline, no network)."
    )
    parser.add_argument(
        "--dry-calc",
        action="store_true",
        help="Compute power table and gate from config defaults (no DB/network).",
    )
    parser.add_argument(
        "--sigma",
        type=float,
        default=None,
        help="Standard deviation of per-trade return (same units as delta). "
        "Defaults to config stat_gate_deltas midpoint.",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=None,
        help="Starting capital (USD). Defaults to config bot_quote_bank_baseline_usd.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON instead of ASCII text.",
    )
    args = parser.parse_args(argv)

    if not args.dry_calc:
        parser.print_help()
        return 1

    cfg = load_cfg()
    sigma = args.sigma if args.sigma is not None else cfg.bot_eval_stat_gate_deltas[1]
    bankroll = args.bankroll if args.bankroll is not None else cfg.bot_quote_bank_baseline_usd

    if not math.isfinite(sigma) or sigma <= 0:
        parser.error(f"sigma must be finite and strictly positive, got {sigma}")
    if not math.isfinite(bankroll) or bankroll <= 0:
        parser.error(f"bankroll must be finite and strictly positive, got {bankroll}")

    pt = power_table(
        sigma=sigma,
        deltas=cfg.bot_eval_stat_gate_deltas,
        z_alpha=Z_ALPHA_90_ONE_SIDED,
        z_beta=Z_BETA_80_POWER,
    )
    gdef = gate_definition(cfg)

    if args.json_output:
        payload = {
            "sigma": sigma,
            "bankroll": bankroll,
            "power_table": pt,
            "gate_definition": gdef,
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    # ASCII text output
    print("=" * 60)
    print("  STATISTICAL POWER & SAMPLE SIZE")
    print("=" * 60)
    print(f"  sigma = {sigma:.4f}   bankroll = ${bankroll:.2f}")
    print()
    print(f"  {'Delta':>10}  {'N required':>12}  {'Wilson HW':>12}")
    print(f"  {'-'*10}  {'-'*12}  {'-'*12}")
    for row in pt:
        n_str = str(row["n_required"]) if row["n_required"] is not None else "N/A"
        hw_str = f'{row["wilson_half_width"]:.4f}' if row["wilson_half_width"] is not None else "N/A"
        print(f"  {row['delta']:>10.4f}  {n_str:>12}  {hw_str:>12}")
    print()

    print("=" * 60)
    print("  GATE DEFINITION")
    print("=" * 60)
    print(f"  Primary: {gdef['primary_gate']}")
    print(f"  Threshold: {gdef['threshold_pct']}%")
    print(f"  Dollar twin: {gdef['dollar_twin_gate']}")
    print(f"  Bankroll fraction: {gdef['bankroll_fraction']}")
    print()

    print("=" * 60)
    print("  TAUTOLOGY DISCLAIMER")
    print("=" * 60)
    # Word-wrap the disclaimer at 56 chars
    disc = gdef["tautology_disclaimer"]
    words = disc.split()
    line = "  "
    for w in words:
        if len(line) + len(w) + 1 > 58:
            print(line)
            line = "  " + w
        else:
            line += (" " if len(line) > 2 else "") + w
    if line.strip():
        print(line)
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
