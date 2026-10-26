"""Tournament Analytics & Decision Cockpit Server.

Interactive real-time visualization and statistical analysis cockpit for
Spread Hunter multi-arm shadow tournaments. One page is served: the current
setup (#487). The past-run pages (#484, #492, #608) were purged on 2026-10-21 --
their routes 404 now, and their static snapshots are no longer written. Their
arm records and stores stay: they are launch/compare data, not pages.
Computes risk, expectancy (EV), standard deviation, max drawdown,
Monte Carlo projections, and Live Readiness Scoring.

Usage:
    python -m scripts.tournament_cockpit_server --port 8800
"""
from __future__ import annotations

import argparse
import contextlib
import html
import http.server
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import socket
import socketserver
import sqlite3
import sys
import threading
import time
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_brain.close_methods import (  # noqa: E402
    MERGE_METHODS,
    RESCUE_METHODS,
    SETTLEMENT,
    SHADOW_MERGE_NOTE,
)
from core_brain.shadow_run import (  # noqa: E402
    latest_tournament_record,
    parse_tournament_run_dir_name,
)

ARMS_CONFIG_484 = [
    # Issue #484: Strategy Profiles
    {
        "issue": 484,
        "name": "control-1",
        "label": "Control (#1)",
        "port": 8801,
        "db": "data/i484_tournament_control-1.db",
        "run_id": "i484_tournament_control-1",
        "color": "#00e676",
        "desc": "Baseline fixed 2c offset, 4.5c single-buy loss stop",
        "worst_catalyst": "stop_loss_trigger_usd_cents (4.5c static)",
        "worst_catalyst_desc": "Fixed stop dumped 56 trades on temporary 4.5c noise (-$15.16 in rescue/stop haircuts)",
        "hypothesized_tweak": "stop_loss_trigger_usd_cents -> 6.0c",
        "tweak_rationale": "Widen stop from 4.5c to 6.0c to absorb noise and prevent premature panic exits while keeping adverse limit intact",
    },
    {
        "issue": 484,
        "name": "conservative-1",
        "label": "Conservative (#1)",
        "port": 8802,
        "db": "data/i484_tournament_conservative-1.db",
        "run_id": "i484_tournament_conservative-1",
        "color": "#00b0ff",
        "desc": "Dynamic offset 0.75x (2-5c), tighter 3.0c single-buy stop",
        "worst_catalyst": "stop_loss_trigger_usd_cents (3.0c tight)",
        "worst_catalyst_desc": "Hair-trigger 3c stop panic-sold 28 trades via aged_out_rescue (-$15.43, -$0.55/tr)",
        "hypothesized_tweak": "stop_loss_trigger_usd_cents -> 4.5c",
        "tweak_rationale": "Relax hair-trigger 3c stop to standard 4.5c so normal maker variance does not force emergency bailouts",
    },
    {
        "issue": 484,
        "name": "balanced-1",
        "label": "Balanced (#1)",
        "port": 8803,
        "db": "data/i484_tournament_balanced-1.db",
        "run_id": "i484_tournament_balanced-1",
        "color": "#e040fb",
        "desc": "Dynamic offset 0.50x (2-4c), standard 4.5c single-buy stop",
        "worst_catalyst": "dynamic_offset_multiplier (0.50x / 2-4c)",
        "worst_catalyst_desc": "No-man's land offset missed tight volume; lowest merge revenue in tournament (+$28.69)",
        "hypothesized_tweak": "dynamic_offset_multiplier -> 0.25x (2-3c)",
        "tweak_rationale": "Tighten multiplier from 0.50x to 0.25x to sit closer to market mid and capture high-volume flow",
    },
    {
        "issue": 484,
        "name": "aggressive-1",
        "label": "Aggressive (#1)",
        "port": 8804,
        "db": "data/i484_tournament_aggressive-1.db",
        "run_id": "i484_tournament_aggressive-1",
        "color": "#ffd600",
        "desc": "Dynamic offset 0.25x (2-3c), wider 6.0c single-buy stop",
        "worst_catalyst": "stop_loss_trigger_usd_cents (6.5c wide)",
        "worst_catalyst_desc": "Wide 6.5c leash allowed adverse single buys to bleed deeper (-$0.32 avg loss per stop)",
        "hypothesized_tweak": "stop_loss_trigger_usd_cents -> 4.5c",
        "tweak_rationale": "Reign in excessive 6.5c stop to standard 4.5c to cap catastrophic single-leg drawdown",
    },
    {
        "issue": 484,
        "name": "prudent-1",
        "label": "Prudent (#1)",
        "port": 8805,
        "db": "data/i484_tournament_prudent-1.db",
        "run_id": "i484_tournament_prudent-1",
        "color": "#ff6d00",
        "desc": "Fixed 3c offset, conservative 3.5c single-buy stop",
        "worst_catalyst": "dynamic_offset_multiplier (0.60x / 2-4c)",
        "worst_catalyst_desc": "Wide resting distance caused asymmetric fills; leg 2 failed to fill before settlement (-$15.28)",
        "hypothesized_tweak": "dynamic_offset_multiplier -> 0.30x (2-3c)",
        "tweak_rationale": "Narrow offset multiplier from 0.60x to 0.30x so leg 2 remains reachable before game close",
    },
    # Issue #485: Queue & Execution Mechanics
    {
        "issue": 485,
        "name": "stickhold-1",
        "label": "Stick-Hold (#1)",
        "port": 8806,
        "db": "data/i485_tournament_stickhold-1.db",
        "run_id": "i485_tournament_stickhold-1",
        "color": "#69f0ae",
        "desc": "Dynamic offset 0.25x, 45s single grace, 6.5c loss cap",
        "worst_catalyst": "requote_hold_queue_shares (0 / no hold)",
        "worst_catalyst_desc": "Zero queue hold surrendered priority on every tick; forced 57 single-buy exits (-$20.56)",
        "hypothesized_tweak": "requote_hold_queue_shares -> 500 (retired by #387)",
        "tweak_rationale": "Enable 500 share queue hold to defend priority against micro-price flicker and avoid queue demotions",
    },
    {
        "issue": 485,
        "name": "queuehug-1",
        "label": "Queue-Hug (#1)",
        "port": 8807,
        "db": "data/i485_tournament_queuehug-1.db",
        "run_id": "i485_tournament_queuehug-1",
        "color": "#ff5252",
        "desc": "Requote hold below 100 shares, queue hold 150 shares (retired by #387)",
        "worst_catalyst": "requote_hold_below_target (1.0 / unconstrained)",
        "worst_catalyst_desc": "Held bids while market collapsed to zero, eating toxic fills into settlement (-$31.65)",
        "hypothesized_tweak": "requote_hold_below_target -> 0.08 (retired by #387)",
        "tweak_rationale": "Constrain drop tolerance to 8c so collapsing books trigger immediate bid cancellations instead of toxic fills",
    },
    {
        "issue": 485,
        "name": "deadband-tight-1",
        "label": "Deadband-Tight (#1)",
        "port": 8808,
        "db": "data/i485_tournament_deadband-tight-1.db",
        "run_id": "i485_tournament_deadband-tight-1",
        "color": "#ff4081",
        "desc": "Strict 1c requote deadband, frequent requoting",
        "worst_catalyst": "requote_dead_band (0.01 / 1c strict)",
        "worst_catalyst_desc": "1c tick flutter churned hedging orders to back of queue; leg 2 never filled (-$38.03 settlement)",
        "hypothesized_tweak": "requote_dead_band -> 0.05 (5c buffer)",
        "tweak_rationale": "Widen deadband to 5c to prevent 1c jitter from resetting queue rank on complementary hedge legs",
    },
    {
        "issue": 485,
        "name": "deadband-wide-1",
        "label": "Deadband-Wide (#1)",
        "port": 8809,
        "db": "data/i485_tournament_deadband-wide-1.db",
        "run_id": "i485_tournament_deadband-wide-1",
        "color": "#76ff03",
        "desc": "Wide 2c requote deadband, reduces unnecessary churn",
        "worst_catalyst": "dynamic_offset_multiplier (0.50x / 2-4c)",
        "worst_catalyst_desc": "Inherited 0.50x offset backed off on volatility, capping merges to +$35.14 vs control's +$39.91",
        "hypothesized_tweak": "dynamic_offset_multiplier -> 0.25x (2-3c)",
        "tweak_rationale": "Lower multiplier to 0.25x so wide-deadband stability pairs with tighter maker pricing to boost merges",
    },
    {
        "issue": 485,
        "name": "snapstop-1",
        "label": "Snap-Stop (#1)",
        "port": 8810,
        "db": "data/i485_tournament_snapstop-1.db",
        "run_id": "i485_tournament_snapstop-1",
        "color": "#b388ff",
        "desc": "Fast 30s single buy grace, tight 4.0c loss cap",
        "worst_catalyst": "single_buy_grace_sec (0s / zero patience)",
        "worst_catalyst_desc": "0s grace gave leg 2 zero time as maker; rushed taker hedge failed, wiping out -$14.82 at settlement",
        "hypothesized_tweak": "single_buy_grace_sec -> 45s",
        "tweak_rationale": "Provide 45s of maker patience so leg 2 can fill organically before triggering forced market cross",
    },
]

ARMS_CONFIG_487 = [
    # Issue #487: the current-setup ten-arm tournament. One control running
    # today's configuration untouched, plus nine arms that each move exactly one
    # LIVE BOT_* setting. Worst-catalyst columns stay EMPTY: there is no
    # forensic run behind this set, and inventing numbers is the failure this
    # issue removes. Static db/run_id values are placeholders -- the launcher
    # writes data/tournaments/i487-current-setup-10arm_<stamp>/plan.json at
    # launch and resolve_tournament_arms() reads the real db/run_id/port from it. Before
    # launch a missing store reports NO_DATA, which is the honest fresh state.
    {
        "issue": 487,
        "name": "control",
        "label": "Control",
        "port": 8801,
        "db": "data/487_tournament_01_control_current_setup_10arm.db",
        "run_id": "shadow-487-t01-control-current_setup_10arm",
        "color": "#00e676",
        "desc": "Today's shipped configuration plus the operator's .env, untouched",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "none - the current setup",
        "tweak_rationale": "The baseline every variation is measured against; this arm changes nothing on purpose",
    },
    {
        "issue": 487,
        "name": "offset-mult-025",
        "label": "Offset Mult 0.25x",
        "port": 8802,
        "db": "data/487_tournament_02_offset-mult-025_current_setup_10arm.db",
        "run_id": "shadow-487-t02-offset-mult-025-current_setup_10arm",
        "color": "#00b0ff",
        "desc": "Dynamic offset multiplier halved, 0.50x -> 0.25x (the preset aggressive value)",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "quote_spread_offset_multiplier = 0.25",
        "tweak_rationale": "A lower multiplier tracks a quiet book more closely and rests nearer mid, which should raise merge volume",
    },
    {
        "issue": 487,
        "name": "offset-min-2c",
        "label": "Offset Min 2c",
        "port": 8803,
        "db": "data/487_tournament_03_offset-min-2c_current_setup_10arm.db",
        "run_id": "shadow-487-t03-offset-min-2c-current_setup_10arm",
        "color": "#e040fb",
        "desc": "Dynamic offset floor raised 1c -> 2c (the preset conservative value)",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "quote_spread_offset_min_cents = 2",
        "tweak_rationale": "A 2c floor stops the offset collapsing onto mid in calm markets, where a 1c edge pays for nothing",
    },
    {
        "issue": 487,
        "name": "offset-max-3c",
        "label": "Offset Max 3c",
        "port": 8804,
        "db": "data/487_tournament_04_offset-max-3c_current_setup_10arm.db",
        "run_id": "shadow-487-t04-offset-max-3c-current_setup_10arm",
        "color": "#ffd600",
        "desc": "Dynamic offset ceiling cut 20c -> 3c (the preset aggressive value)",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "quote_spread_offset_max_cents = 3",
        "tweak_rationale": "A hard 3c ceiling stops the offset running away in volatile books and keeps both legs reachable before settlement",
    },
    {
        "issue": 487,
        "name": "offset-static",
        "label": "Offset Static",
        "port": 8805,
        "db": "data/487_tournament_05_offset-static_current_setup_10arm.db",
        "run_id": "shadow-487-t05-offset-static-current_setup_10arm",
        "color": "#ff6d00",
        "desc": "Dynamic offset switched off: the shipped 2c static offset quotes instead",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "quote_gate_dynamic_offset_on = 0",
        "tweak_rationale": "A fixed 2c offset should beat volatility tracking if the dynamic offset is mostly noise",
    },
    {
        "issue": 487,
        "name": "deadband-6c",
        "label": "Deadband 6c",
        "port": 8806,
        "db": "data/487_tournament_06_deadband-6c_current_setup_10arm.db",
        "run_id": "shadow-487-t06-deadband-6c-current_setup_10arm",
        "color": "#69f0ae",
        "desc": "Requote dead band widened 0.03 -> 0.06 (the preset hybrid value)",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "order_requote_dead_band_cents = 0.06",
        "tweak_rationale": "A wider dead band should stop small price jitter from churning orders to the back of the queue",
    },
    {
        "issue": 487,
        "name": "widen-on",
        "label": "Widen Gate On",
        "port": 8807,
        "db": "data/487_tournament_07_widen-on_current_setup_10arm.db",
        "run_id": "shadow-487-t07-widen-on-current_setup_10arm",
        "color": "#ff5252",
        "desc": "WIDENED-gate snap-back switched on: a market flagged WIDENED quotes the 3.5c widen offset instead of the raw dynamic offset. No preset ships a different value for this knob, so the arm is not preset-sourced.",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "quote_gate_widen_on = 1",
        "tweak_rationale": "Tests whether the WIDENED-gate snap-back helps or hurts: it can quote TIGHTER than the raw dynamic offset in exactly the markets that are losing",
    },
    {
        "issue": 487,
        "name": "price-risk-030",
        "label": "Price Risk 3c",
        "port": 8808,
        "db": "data/487_tournament_08_price-risk-030_current_setup_10arm.db",
        "run_id": "shadow-487-t08-price-risk-030-current_setup_10arm",
        "color": "#ff4081",
        "desc": "Price-risk offset weight tripled, 0.010 -> 0.030: every resting quote sits up to ~3c deeper under mid. No preset ships this setting, so the arm is not preset-sourced.",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "quote_price_risk_widen = 0.030",
        "tweak_rationale": "Demanding more offset where a share costs more should price a naked leg's downside properly; it deepens every quote, not just the risky ones",
    },
    {
        "issue": 487,
        "name": "stop-adverse-5c",
        "label": "Stop Adverse 5c",
        "port": 8809,
        "db": "data/487_tournament_09_stop-adverse-5c_current_setup_10arm.db",
        "run_id": "shadow-487-t09-stop-adverse-5c-current_setup_10arm",
        "color": "#b388ff",
        "desc": "Single-leg stop switched on, NONE -> ABSOLUTE_DROP, with the shipped 0.05 adverse-drop threshold travelling with the mode: one variable, two behaviours. No preset ships this setting, so the arm is not preset-sourced.",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "risk_stop_mode = ABSOLUTE_DROP",
        "tweak_rationale": "An entry-relative stop should cap single-leg losses instead of letting a naked leg run to settlement",
    },
    {
        "issue": 487,
        "name": "book-window-4c",
        "label": "Book Window 4c",
        "port": 8810,
        "db": "data/487_tournament_10_book-window-4c_current_setup_10arm.db",
        "run_id": "shadow-487-t10-book-window-4c-current_setup_10arm",
        "color": "#18ffff",
        "desc": "Per-market quoting window tightened 15c -> 4c. The one arm that changes which markets get quoted at all. The baseline preset ships 15c, the current setup's own value, so the arm is not preset-sourced.",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "quote_book_max_spread_from_mid = 4",
        "tweak_rationale": "A narrower window refuses the wide, unreadable books and keeps quoting only the tightest ones",
    },
]

ARMS_CONFIG_492 = [
    # Issue #492: fresh Round 1 baseline re-run on fixed code (no #487 tweaks).
    # Worst-catalyst columns stay EMPTY until this run produces results -- the
    # old #484/#485 hypotheses described buggy-code artifacts, not real
    # behavior, and must not anchor the new read. Static db/run_id values are
    # placeholders (real paths land via data/tournaments/i492-r<run>_<stamp>/plan.json
    # once the run launches); missing files report as NO_DATA, which is the
    # honest fresh state.
    {
        "issue": 492,
        "name": "control-1",
        "label": "Control (#1)",
        "port": 8801,
        "db": "data/tournament_i492-r1_control-1.db",
        "run_id": "shadow-492-tournament-1-control-1",
        "color": "#00e676",
        "desc": "Baseline fixed 2c offset, 4.5c single-buy loss stop",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 492,
        "name": "conservative-1",
        "label": "Conservative (#1)",
        "port": 8802,
        "db": "data/tournament_i492-r1_conservative-1.db",
        "run_id": "shadow-492-tournament-1-conservative-1",
        "color": "#00b0ff",
        "desc": "Dynamic offset 0.75x (2-5c), tighter 3.0c single-buy stop",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 492,
        "name": "balanced-1",
        "label": "Balanced (#1)",
        "port": 8803,
        "db": "data/tournament_i492-r1_balanced-1.db",
        "run_id": "shadow-492-tournament-1-balanced-1",
        "color": "#e040fb",
        "desc": "Dynamic offset 0.50x (2-4c), standard 4.5c single-buy stop",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 492,
        "name": "aggressive-1",
        "label": "Aggressive (#1)",
        "port": 8804,
        "db": "data/tournament_i492-r1_aggressive-1.db",
        "run_id": "shadow-492-tournament-1-aggressive-1",
        "color": "#ffd600",
        "desc": "Dynamic offset 0.25x (2-3c), wider 6.0c single-buy stop",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 492,
        "name": "prudent-1",
        "label": "Prudent (#1)",
        "port": 8805,
        "db": "data/tournament_i492-r1_prudent-1.db",
        "run_id": "shadow-492-tournament-1-prudent-1",
        "color": "#ff6d00",
        "desc": "Fixed 3c offset, conservative 3.5c single-buy stop",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 492,
        "name": "stickhold-1",
        "label": "Stick-Hold (#1)",
        "port": 8806,
        "db": "data/tournament_i492-r1_stickhold-1.db",
        "run_id": "shadow-492-tournament-1-stickhold-1",
        "color": "#69f0ae",
        "desc": "Dynamic offset 0.25x, 45s single grace, 6.5c loss cap",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 492,
        "name": "queuehug-1",
        "label": "Queue-Hug (#1)",
        "port": 8807,
        "db": "data/tournament_i492-r1_queuehug-1.db",
        "run_id": "shadow-492-tournament-1-queuehug-1",
        "color": "#ff4081",
        "desc": "Requote hold below 100 shares, queue hold 150 shares (retired by #387)",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 492,
        "name": "deadband-tight-1",
        "label": "Deadband-Tight (#1)",
        "port": 8808,
        "db": "data/tournament_i492-r1_deadband-tight-1.db",
        "run_id": "shadow-492-tournament-1-deadband-tight-1",
        "color": "#ff5252",
        "desc": "Strict 1c requote deadband, frequent requoting",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 492,
        "name": "deadband-wide-1",
        "label": "Deadband-Wide (#1)",
        "port": 8809,
        "db": "data/tournament_i492-r1_deadband-wide-1.db",
        "run_id": "shadow-492-tournament-1-deadband-wide-1",
        "color": "#76ff03",
        "desc": "Wide 2c requote deadband, reduces unnecessary churn",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 492,
        "name": "snapstop-1",
        "label": "Snap-Stop (#1)",
        "port": 8810,
        "db": "data/tournament_i492-r1_snapstop-1.db",
        "run_id": "shadow-492-tournament-1-snapstop-1",
        "color": "#b388ff",
        "desc": "Fast 30s single buy grace, tight 4.0c loss cap",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
]

ARMS_CONFIG_608 = [
    # Issue #608: 10-character shadow tournament on shipped defaults.
    # Worst-catalyst columns stay EMPTY until this run produces results --
    # the run's own verdict fills them, never hardcode (per #492 precedent).
    {
        "issue": 608,
        "name": "the-accountant-1",
        "label": "The Accountant (#1)",
        "port": 8801,
        "db": "data/i608_tournament-1_the-accountant-1.db",
        "run_id": "shadow-608-tournament-1-the-accountant-1",
        "color": "#00e676",
        "desc": "Tight 2.0c stop (half shipped 4.0c): exits orphan legs early",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 608,
        "name": "the-patient-one-1",
        "label": "The Patient One (#1)",
        "port": 8802,
        "db": "data/i608_tournament-1_the-patient-one-1.db",
        "run_id": "shadow-608-tournament-1-the-patient-one-1",
        "color": "#00b0ff",
        "desc": "Loose 8.0c stop: lets legs breathe through noise",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 608,
        "name": "the-surgeon-1",
        "label": "The Surgeon (#1)",
        "port": 8803,
        "db": "data/i608_tournament-1_the-surgeon-1.db",
        "run_id": "shadow-608-tournament-1-the-surgeon-1",
        "color": "#e040fb",
        "desc": "Tight 0.25x dynamic offset: quotes close to the book",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 608,
        "name": "the-daydreamer-1",
        "label": "The Daydreamer (#1)",
        "port": 8804,
        "db": "data/i608_tournament-1_the-daydreamer-1.db",
        "run_id": "shadow-608-tournament-1-the-daydreamer-1",
        "color": "#ffd600",
        "desc": "Wide 1.0x dynamic offset: rests deep for edge",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 608,
        "name": "the-regular-1",
        "label": "The Regular (#1)",
        "port": 8805,
        "db": "data/i608_tournament-1_the-regular-1.db",
        "run_id": "shadow-608-tournament-1-the-regular-1",
        "color": "#ff6d00",
        "desc": "Offset excursion capped at 3c (down from 20c)",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 608,
        "name": "the-metronome-1",
        "label": "The Metronome (#1)",
        "port": 8806,
        "db": "data/i608_tournament-1_the-metronome-1.db",
        "run_id": "shadow-608-tournament-1-the-metronome-1",
        "color": "#69f0ae",
        "desc": "Calm 0.06 requote deadband: reprices half as often",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 608,
        "name": "the-gambler-1",
        "label": "The Gambler (#1)",
        "port": 8807,
        "db": "data/i608_tournament-1_the-gambler-1.db",
        "run_id": "shadow-608-tournament-1-the-gambler-1",
        "color": "#ff4081",
        "desc": "Hold direction (0.0) + 800-queue conviction through wobbles",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 608,
        "name": "the-chaperone-1",
        "label": "The Chaperone (#1)",
        "port": 8808,
        "db": "data/i608_tournament-1_the-chaperone-1.db",
        "run_id": "shadow-608-tournament-1-the-chaperone-1",
        "color": "#ff5252",
        "desc": "Defends queue position (500) on every tick",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 608,
        "name": "the-graceful-1",
        "label": "The Graceful (#1)",
        "port": 8809,
        "db": "data/i608_tournament-1_the-graceful-1.db",
        "run_id": "shadow-608-tournament-1-the-graceful-1",
        "color": "#76ff03",
        "desc": "45s maker grace on calm legs the stop did not take",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
    {
        "issue": 608,
        "name": "the-strict-parent-1",
        "label": "The Strict Parent (#1)",
        "port": 8810,
        "db": "data/i608_tournament-1_the-strict-parent-1.db",
        "run_id": "shadow-608-tournament-1-the-strict-parent-1",
        "color": "#b388ff",
        "desc": "Downside anchor: 2.0c stop + 0s grace (documents the floor)",
        "worst_catalyst": "",
        "worst_catalyst_desc": "",
        "hypothesized_tweak": "",
        "tweak_rationale": "",
    },
]

# Legacy alias kept for older imports. The tournament the cockpit serves by
# default is CURRENT_SETUP_KEY (#487); this constant no longer selects a page.
ARMS_CONFIG = ARMS_CONFIG_492

TOURNAMENTS = {
    "492": {
        "id": "492",
        "title": "Issue #492 Baseline Re-run (Fixed Code)",
        "subtitle": "12-Hour Multi-Arm Live Shadow Evaluation | Original 10 arms, byte-identical env, no #487 tweaks",
        "description": "Fresh Round 1 baseline on fixed code. Catalyst columns stay empty until this run reports; the current-setup tournament (#487) is a separate, later run.",
        "arms": ARMS_CONFIG_492,
        "issue_primary": 492,
        "issue_secondary": 492,
    },
    "608": {
        "id": "608",
        "title": "Issue #608 Character Tournament (Shipped Defaults)",
        "subtitle": "12-Hour Multi-Arm Live Shadow Evaluation | 10 one-knob characters, shared Data Analysis verdict",
        "description": "Ten characters, one knob each, on loose shipped defaults. Catalyst columns stay empty until this run reports.",
        "arms": ARMS_CONFIG_608,
        "issue_primary": 608,
        "issue_secondary": 608,
    },
    "484": {
        "id": "484",
        "title": "Issue #484 / #485 Baseline Tournament (Archive)",
        "subtitle": "12-Hour Multi-Arm Live Shadow Evaluation | Issue #484 (Strategy Profiles) & Issue #485 (Execution Mechanics)",
        "description": "Archived baseline built on buggy code (see #492); kept for history, not for decisions.",
        "arms": ARMS_CONFIG_484,
        "issue_primary": 484,
        "issue_secondary": 485,
    },
    "487": {
        "id": "487",
        "title": "Issue #487 Current-Setup Tournament",
        "subtitle": "12-Hour Multi-Arm Shadow Evaluation | One control at today's settings + 9 arms varying a single live parameter",
        "description": "The first ten-arm run on the post-2026-10-20 code: every arm starts from the configuration that ships today and changes exactly one live setting. Comparative reading happens after the 720-minute run; no arm carries a catalyst score yet.",
        "arms": ARMS_CONFIG_487,
        "issue_primary": 487,
        "issue_secondary": 487,
    },
}


# The cockpit serves one page: the current setup. Everything past-run is a
# launch/compare record only. These paths are the only ones that render, and the
# current setup is the only tournament key the JSON API will serve.
CURRENT_SETUP_KEY = "487"
PAGE_PATHS: dict[str, str] = {
    "": CURRENT_SETUP_KEY,
    "/index.html": CURRENT_SETUP_KEY,
    "/487": CURRENT_SETUP_KEY,
    "/487.html": CURRENT_SETUP_KEY,
    "/cockpit_487": CURRENT_SETUP_KEY,
}
# The keys the JSON API may serve: the served pages' own tournaments, derived so
# a new page cannot forget to widen this.
SERVED_TOURNAMENT_KEYS = frozenset(PAGE_PATHS.values())


def tourney_key_for_path(path: str) -> Optional[str]:
    """Return the tournament key whose page serves this path, or None for a 404.

    None is deliberate for `/492`, `/484`, `/484.html`, `/608` and `/608.html`:
    the purged past-run pages. Issue #698 asked for that purge; PR #703 ticked
    the acceptance boxes without delivering the route bullet, so it lands here.
    """
    return PAGE_PATHS.get(path.split("?")[0].rstrip("/"))


def _strip_round_suffix(name: str) -> str:
    """Drop a trailing -<digit> round suffix so plan files match presets."""
    return re.sub(r"-\d+$", "", str(name or ""))


log = logging.getLogger(__name__)


class TournamentRecordError(Exception):
    """An arm record exists but cannot be trusted to describe this run.

    A record that is absent means the run has not started -- a normal state. A
    record that is present and unreadable means the page is about to show the
    static presets while looking exactly like that normal state. The two must not
    be confused, so the second one has a name.
    """


# The last broken-record message already logged. `get_cached_stats` re-collects
# every 3s, so a warning per call would print the same line hundreds of times. A
# successful read clears it, which lets a record that breaks again warn again.
_WARNED_RECORD: Optional[str] = None


def _read_arm_record(path: Path) -> list[dict[str, Any]]:
    """Read one arm record and check its shape; raise TournamentRecordError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        # JSONDecodeError and UnicodeDecodeError are ValueError subclasses, so
        # truncation, bad bytes and bad JSON all land here.
        raise TournamentRecordError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TournamentRecordError(f"{path}: top level is not a JSON object")
    if "arms" not in data:
        raise TournamentRecordError(f"{path}: 'arms' is missing")
    record_arms = data["arms"]
    if not isinstance(record_arms, list):
        raise TournamentRecordError(f"{path}: 'arms' is not a list")
    for i, arm in enumerate(record_arms):
        if not isinstance(arm, dict):
            raise TournamentRecordError(f"{path}: arm {i} is not a JSON object")
        if not isinstance(arm.get("name"), str) or not arm["name"]:
            raise TournamentRecordError(f"{path}: arm {i} has no string 'name'")
        # An ABSENT field is fine -- the merge keeps the static placeholder. A
        # PRESENT-but-unusable one is not: `db_path: null` would be copied into
        # the arm and reach `ROOT / cfg["db"]` in analyze_arm_database, where it
        # raises TypeError and takes the page down with a traceback instead of
        # showing the warning this module exists to show.
        for field in ("db_path", "run_id"):
            if field in arm and (not isinstance(arm[field], str) or not arm[field]):
                raise TournamentRecordError(
                    f"{path}: arm {i} ({arm['name']}) has a non-string '{field}'"
                )
        if "dash_port" in arm:
            port = arm["dash_port"]
            if isinstance(port, bool) or not isinstance(port, int):
                raise TournamentRecordError(
                    f"{path}: arm {i} ({arm['name']}) has a non-integer 'dash_port'"
                )
    return record_arms


# What a run's own record says about the page: the arms it launched, the prose
# from its arms file, and how long it planned to run.
DEFAULT_TOURNAMENT_MINUTES = 720.0

# Colours for plan arms whose arms file names none. Ten entries, so a ten-arm
# run that carries no colour still reads as ten distinct lines on the charts.
_PLAN_ARM_COLORS = (
    "#00e676", "#00b0ff", "#e040fb", "#ffd600", "#ff6d00",
    "#76ff03", "#b388ff", "#ff5252", "#18ffff", "#ffab40",
)


def _tournament_root() -> Path:
    """The run-folder root, read through this module's ROOT so it is patchable."""
    return ROOT / "data" / "tournaments"


def _newest_plan_record() -> Optional[Path]:
    """The most recently launched `plan.json`, whatever issue it belongs to.

    Ranked on the folder's parsed launch stamp: the run an operator just started
    is the one the page must follow, and an issue number says nothing about
    launch order (a #799 run on top of a #608 one is newer, not "lower").
    """
    directory = _tournament_root()
    if not directory.is_dir():
        return None
    best: Optional[tuple[tuple[str, str], Path]] = None
    for candidate in directory.iterdir():
        parsed = parse_tournament_run_dir_name(candidate)
        plan = candidate / "plan.json"
        if parsed is None or not plan.is_file():
            continue
        rank = (parsed["stamp"], candidate.name)
        if best is None or rank > best[0]:
            best = (rank, plan)
    return best[1] if best else None


def _plan_identity(plan_path: Path) -> tuple[Optional[int], Optional[float]]:
    """`(issue, planned minutes)` for a run folder, from its folder or record."""
    parsed = parse_tournament_run_dir_name(plan_path.parent)
    issue = parsed["issue"] if parsed else None
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return issue, None
    if not isinstance(plan, dict):
        return issue, None
    if issue is None and isinstance(plan.get("issue"), int):
        issue = plan["issue"]
    minutes = plan.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        return issue, None
    return issue, float(minutes)


def _plan_arm_source(plan_path: Path) -> dict[str, dict[str, Any]]:
    """The plan's own arms file (`arms_source`), keyed by arm name.

    That file is where a run keeps the prose for its arms: the cockpit's #487
    presets describe a different ten arms, so a newer table has to read its
    hypotheses from the file the launcher actually executed. Best-effort -- a
    missing or broken file leaves names only, which is still true about the run.
    """
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(plan, dict):
        return {}
    source = plan.get("arms_source")
    if not isinstance(source, str) or not source:
        return {}
    path = Path(source)
    if not path.is_absolute():
        path = ROOT / path
    try:
        with open(path, "r", encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(spec, list):
        return {}
    return {
        entry["name"]: entry
        for entry in spec
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }


def _tweak_text(env: Any) -> str:
    """`key = value` pairs for an arm's env, in the cockpit's own prose form."""
    if not isinstance(env, dict):
        return ""
    lines = []
    for key, value in env.items():
        lines.append(f"{str(key).removeprefix('BOT_').lower()} = {value}")
    return " \u00b7 ".join(lines)


def build_plan_arms(plan_path: Path) -> list[dict[str, Any]]:
    """The arms a plan actually launched, shaped like the static preset rows."""
    try:
        record_arms = _read_arm_record(plan_path)
    except TournamentRecordError:
        return []
    issue, _ = _plan_identity(plan_path)
    source = _plan_arm_source(plan_path)
    arms = []
    for i, record_arm in enumerate(record_arms):
        name = record_arm["name"]
        entry = source.get(name) or {}
        arms.append({
            "issue": issue if issue is not None else 0,
            "name": name,
            "label": entry.get("label") or name.replace("-", " ").title(),
            "port": record_arm.get("dash_port", 0),
            "db": record_arm.get("db_path", ""),
            "run_id": record_arm.get("run_id", ""),
            "color": entry.get("color") or _PLAN_ARM_COLORS[i % len(_PLAN_ARM_COLORS)],
            "desc": entry.get("desc") or entry.get("profile") or "",
            "worst_catalyst": entry.get("worst_catalyst") or "",
            "worst_catalyst_desc": entry.get("worst_catalyst_desc") or "",
            "hypothesized_tweak": _tweak_text(entry.get("env") or record_arm.get("env")),
            "tweak_rationale": entry.get("thesis") or "",
        })
    return arms


def _arms_from_newest_plan(meta: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """The newest run's own arms when it is not the run this page describes.

    A run of the page's own issue is merged over the static presets instead, so
    a partial record still reads as the ten-arm table it was launched from.
    """
    newest = _newest_plan_record()
    if newest is None:
        return None
    issue, _ = _plan_identity(newest)
    if issue is None or issue == int(meta.get("issue_primary") or 0):
        return None
    return build_plan_arms(newest) or None


def resolve_tournament_meta(
    tourney_key: str = CURRENT_SETUP_KEY,
) -> tuple[dict[str, Any], float]:
    """The page's branding and planned length, following the newest run.

    A table that follows a newer run of another issue needs a header that says
    so: "#487" over ten #799 arms is the same lie as ten #487 presets over a
    #799 run. Planned minutes come from that record, so progress is measured
    against the length the run was launched with, not a fixed 12 hours.
    """
    meta = TOURNAMENTS.get(tourney_key, TOURNAMENTS[CURRENT_SETUP_KEY])
    if tourney_key != CURRENT_SETUP_KEY:
        return meta, DEFAULT_TOURNAMENT_MINUTES
    newest = _newest_plan_record()
    if newest is None:
        return meta, DEFAULT_TOURNAMENT_MINUTES
    issue, minutes = _plan_identity(newest)
    target_minutes = minutes or DEFAULT_TOURNAMENT_MINUTES
    if issue is None or issue == int(meta.get("issue_primary") or 0):
        return meta, target_minutes
    # A newest run whose arms cannot be read cannot own the header either: the
    # arm table falls back to the older record, and a header naming the failed
    # run would sit above another run's arms.
    plan_arms = build_plan_arms(newest)
    if not plan_arms:
        return meta, DEFAULT_TOURNAMENT_MINUTES
    arms_label = f"{len(plan_arms)} Arms"
    run_label = (parse_tournament_run_dir_name(newest.parent) or {}).get("name")
    run_label = (run_label or "run").replace("-", " ").title()
    # The #487 preset table rides along inside its meta; leaving it there would
    # ship those ten arms to the browser as this run's arms, under a #799 title.
    branding = {key: value for key, value in meta.items() if key != "arms"}
    return {
        **branding,
        "id": str(issue),
        "title": f"Issue #{issue} Tournament \u2014 Live Run",
        "subtitle": f"{target_minutes / 60.0:g}-Hour Multi-Arm Shadow Evaluation | {run_label} | {arms_label}",
        "description": (
            f"Following the newest tournament record on disk: issue #{issue}, {arms_label.lower()} "
            "launched from its own arms file. Comparative reading happens after the planned run "
            "length; catalyst columns stay empty until this run reports."
        ),
        "issue_primary": issue,
        "issue_secondary": issue,
        "nav_label": f"#{issue} {run_label} ({arms_label})",
    }, target_minutes


def resolve_tournament_arms_with_status(
    tourney_key: str = CURRENT_SETUP_KEY,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Resolve this key's arms, and say so when its record cannot be read.

    Returns `(arms, record_error)`. `record_error` is None when there is nothing
    to report: a key with no record (the run has not started), the static archive
    key, and a record that parsed. A broken record falls back to the static
    presets so the page stays usable, and the caller puts the error next to that
    table instead of letting it pass for "not started".
    """
    global _WARNED_RECORD
    meta = TOURNAMENTS.get(tourney_key, TOURNAMENTS[CURRENT_SETUP_KEY])
    default_arms = meta["arms"]
    # #484 stays fully static (archive); live keys resolve their plan file.
    if tourney_key not in ("487", "492", "608"):
        return default_arms, None

    # The served page follows the newest run on disk, whatever issue launched it:
    # an operator who starts a ten-arm #799 run must see ten #799 arms, not the
    # #487 presets that happen to share the key. Only when an older run of this
    # key is the newest thing on disk does the key's own record apply. Archive
    # keys keep their own records -- #492 and #608 are frozen contracts, and the
    # newest run on disk says nothing about what they evaluated.
    if tourney_key == CURRENT_SETUP_KEY:
        page_arms = _arms_from_newest_plan(meta)
        if page_arms:
            return page_arms, None

    # One resolver for every key: `latest_tournament_record` ranks by parsed
    # (run number, stamp), so a named run (#487's `--stamp current_setup_10arm`)
    # no longer wins or loses on the accident of `c` sorting after a digit.
    latest_plan = latest_tournament_record(tourney_key, ROOT / "data" / "tournaments")
    if tourney_key == CURRENT_SETUP_KEY:
        # A newer run of the page's own issue outranks the highest-numbered one:
        # run number is not launch order, so a named run started today would
        # otherwise merge last week's r1 db/port into today's rows.
        newest = _newest_plan_record()
        newest_issue = _plan_identity(newest)[0] if newest is not None else None
        if newest is not None and newest_issue == int(meta.get("issue_primary") or 0):
            latest_plan = newest
    if latest_plan is None:
        return default_arms, None
    try:
        record_arms = _read_arm_record(latest_plan)
    except TournamentRecordError as exc:
        message = str(exc)
        if message != _WARNED_RECORD:
            log.warning("Could not read tournament record %s", message)
            _WARNED_RECORD = message
        return default_arms, message
    _WARNED_RECORD = None
    plan_arms: dict[str, Any] = {}
    for a in record_arms:
        plan_arms[a["name"]] = a
        plan_arms[_strip_round_suffix(a["name"])] = a
    merged = []
    for base in default_arms:
        name = base["name"]
        pa = plan_arms.get(name, plan_arms.get(_strip_round_suffix(name)))
        if pa is not None:
            merged.append({
                **base,
                "db": pa.get("db_path", base["db"]),
                "run_id": pa.get("run_id", base["run_id"]),
                "port": pa.get("dash_port", base["port"]),
            })
        else:
            merged.append(base)
    return merged, None


def resolve_tournament_arms(tourney_key: str = CURRENT_SETUP_KEY) -> list[dict[str, Any]]:
    """Dynamically resolve tournament arms from active plan file or fall back to static presets.

    The arms only: `resolve_tournament_arms_with_status` carries the reason when a
    record exists but could not be read.
    """
    return resolve_tournament_arms_with_status(tourney_key)[0]


def analyze_arm_database(cfg: dict[str, Any], target_minutes: float = 720.0) -> dict[str, Any]:
    """Extract comprehensive statistical, risk, and time series metrics for one arm."""
    db_path = ROOT / cfg["db"]
    if not db_path.exists():
        return {
            **cfg,
            "status": "missing_db",
            "trades_count": 0,
            "total_pnl": 0.0,
            "score": 0,
            "readiness": "NO_DATA"
        }

    try:
        with contextlib.closing(
            sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        ) as conn:
            c = conn.cursor()

            # Check time window from queue_marks or quotes
            first_ts = 0.0
            last_ts = 0.0
            ts_row = c.execute(
                "SELECT min(ts), max(ts) FROM queue_marks WHERE run_id = ?",
                (cfg["run_id"],),
            ).fetchone()
            if ts_row and ts_row[0]:
                first_ts = ts_row[0]
                last_ts = ts_row[1] or first_ts
            else:
                q_row = c.execute(
                    "SELECT min(ts), max(ts) FROM quotes WHERE run_id = ?",
                    (cfg["run_id"],),
                ).fetchone()
                if q_row and q_row[0]:
                    first_ts = q_row[0]
                    last_ts = q_row[1] or first_ts

            elapsed_min = (last_ts - first_ts) / 60.0 if first_ts else 0.0
            rem_min = max(0.0, target_minutes - elapsed_min)

            # Fills and Orders
            fills_cnt = c.execute(
                "SELECT count(*) FROM fills WHERE run_id = ?", (cfg["run_id"],)
            ).fetchone()[0]
            orders_cnt = c.execute(
                "SELECT count(DISTINCT order_uuid) FROM fills WHERE run_id = ?", (cfg["run_id"],)
            ).fetchone()[0]

            # Closes (settlement / shadow_merge_note / '' are nontrade, not exits)
            nontrade_exclude = (SETTLEMENT, SHADOW_MERGE_NOTE, "")
            rows = c.execute(
                "SELECT ts, method, realized_pnl, cost_basis, proceeds, shares FROM closes "
                "WHERE run_id = ? AND method NOT IN ("
                + ", ".join("?" for _ in nontrade_exclude) + ") "
                "ORDER BY ts ASC",
                (cfg["run_id"], *nontrade_exclude),
            ).fetchall()

            # Order lifecycle funnel:
            #   quoted  -> every quote row placed (one per leg per requote cycle)
            #   filled  -> distinct orders that got at least one fill (legs, singular)
            #   merged  -> pairs completed via merge/shadow_merge; a merge counts
            #              as TWO singular filled orders (one UP + one DOWN leg)
            #   stop-loss -> single legs closed by the stop-loss family
            #                (single_buy_exit / naked_exit / ladder_exit)
            quoted_cnt = c.execute(
                "SELECT count(*) FROM quotes WHERE run_id = ?", (cfg["run_id"],)
            ).fetchone()[0]
    except sqlite3.Error as exc:
        return {
            **cfg,
            "status": "error",
            "error": str(exc),
            "trades_count": 0,
            "total_pnl": 0.0,
            "score": 0,
            "readiness": "ERROR"
        }

    pnls = [float(r[2] or 0.0) for r in rows]
    n = len(pnls)

    if n == 0:
        return {
            **cfg,
            "status": "no_trades",
            "elapsed_min": elapsed_min,
            "rem_min": rem_min,
            "progress_pct": min(100.0, round((elapsed_min / target_minutes) * 100.0, 1)),
            "fills_cnt": fills_cnt,
            "orders_cnt": orders_cnt,
            "quoted_cnt": quoted_cnt,
            "funnel": {
                "quoted": quoted_cnt,
                "filled": orders_cnt,
                "merged": 0,
                "merged_legs": 0,
                "stop_loss": 0,
            },
            "trades_count": 0,
            "merges_count": 0,
            "exits_count": 0,
            "stop_loss_exits_count": 0,
            "paired_completions_count": 0,
            "total_pnl": 0.0,
            "mean_pnl": 0.0,
            "std_pnl": 0.0,
            "win_rate": 0.0,
            "merge_rate": 0.0,
            "exit_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "score": 0,
            "readiness": "EVALUATING",
            "badge_cls": "badge-amber",
            "series": [],
        }

    wins = [p for p in pnls if p > 0.0]
    losses = [p for p in pnls if p < 0.0]
    merge_methods = MERGE_METHODS
    stop_loss_methods = RESCUE_METHODS
    merges = [r for r in rows if r[1] in merge_methods]
    stops = [r for r in rows if r[1] in stop_loss_methods]
    single_exits = n - len(merges)
    paired_completions = len(merges)

    tot_pnl = sum(pnls)
    mean_pnl = tot_pnl / n
    var = sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1) if n > 1 else 0.0
    std_pnl = math.sqrt(var)

    win_rate = len(wins) / n
    merge_rate = len(merges) / n
    exit_rate = single_exits / n

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 1.0)

    # Max Drawdown & PnL Series
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    series = []
    # Sample down to max 60 points for charting smoothness
    step = max(1, len(rows) // 60)
    for i, r in enumerate(rows):
        p = float(r[2] or 0.0)
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > mdd:
            mdd = dd
        if i % step == 0 or i == len(rows) - 1:
            rel_t = (r[0] - first_ts) / 60.0 if first_ts else 0.0
            series.append({
                "t": round(rel_t, 1),
                "cum": round(cum, 4),
                "pnl": round(p, 4)
            })

    # Value at Risk 95% (empirical 5th percentile)
    sorted_pnls = sorted(pnls)
    var_95 = sorted_pnls[max(0, int(0.05 * n))]

    # Sharpe Proxy (Per 100 Trades Normalized)
    sharpe_proxy = (mean_pnl / std_pnl * 10.0) if std_pnl > 0.0 else 0.0

    # Monte Carlo 100-Trade Simulation (500 iterations)
    rng = random.Random(42 + cfg["port"])
    mc_ends = []
    for _ in range(500):
        mc_run = sum(rng.choice(pnls) for _ in range(100))
        mc_ends.append(mc_run)
    mc_ends.sort()
    mc_p5 = mc_ends[int(0.05 * len(mc_ends))]
    mc_p50 = mc_ends[int(0.50 * len(mc_ends))]
    mc_p95 = mc_ends[int(0.95 * len(mc_ends))]
    prob_positive_100 = sum(1 for x in mc_ends if x > 0) / len(mc_ends)

    # Live Readiness Scoring (0 to 100)
    # 1. Total PnL & EV (max 30 pts)
    pnl_pts = min(30.0, max(0.0, (tot_pnl / 15.0) * 30.0)) if tot_pnl > 0 else 0.0
    # 2. Merge Rate (max 25 pts)
    merge_pts = min(25.0, (merge_rate / 0.60) * 25.0)
    # 3. Low Drawdown (max 20 pts)
    dd_pts = max(0.0, 20.0 - (mdd / 1.5))
    # 4. Profit Factor (max 15 pts)
    pf_pts = min(15.0, (profit_factor / 1.5) * 15.0)
    # 5. Statistical Confidence (trades >= 50, max 10 pts)
    conf_pts = min(10.0, (n / 50.0) * 10.0)

    score = int(round(pnl_pts + merge_pts + dd_pts + pf_pts + conf_pts))

    if score >= 75 and tot_pnl > 5.0 and prob_positive_100 >= 0.70:
        readiness = "READY FOR LIVE"
        badge_cls = "badge-green"
    elif score >= 55 and tot_pnl >= 0.0:
        readiness = "NEEDS TUNING"
        badge_cls = "badge-amber"
    else:
        readiness = "DO NOT DEPLOY"
        badge_cls = "badge-red"

    return {
        **cfg,
        "status": "ok",
        "elapsed_min": elapsed_min,
        "rem_min": rem_min,
        "progress_pct": min(100.0, round((elapsed_min / target_minutes) * 100.0, 1)),
        "fills_cnt": fills_cnt,
        "orders_cnt": orders_cnt,
        "quoted_cnt": quoted_cnt,
        "funnel": {
            "quoted": quoted_cnt,
            "filled": orders_cnt,
            "merged": len(merges),
            "merged_legs": len(merges) * 2,
            "stop_loss": len(stops),
        },
        "trades_count": n,
        "merges_count": len(merges),
        "exits_count": single_exits,
        "stop_loss_exits_count": len(stops),
        "paired_completions_count": paired_completions,
        "total_pnl": round(tot_pnl, 4),
        "mean_pnl": round(mean_pnl, 4),
        "std_pnl": round(std_pnl, 4),
        "win_rate": round(win_rate * 100.0, 1),
        "merge_rate": round(merge_rate * 100.0, 1),
        "exit_rate": round(exit_rate * 100.0, 1),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(mdd, 2),
        "max_win": round(max(pnls) if pnls else 0.0, 4),
        "max_loss": round(min(pnls) if pnls else 0.0, 4),
        "avg_win": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "var_95": round(var_95, 4),
        "sharpe_proxy": round(sharpe_proxy, 2),
        "mc_p5": round(mc_p5, 2),
        "mc_p50": round(mc_p50, 2),
        "mc_p95": round(mc_p95, 2),
        "prob_positive_100": round(prob_positive_100 * 100.0, 1),
        "score": score,
        "readiness": readiness,
        "badge_cls": badge_cls,
        "series": series,
    }


def collect_all_stats(tourney_key: str = CURRENT_SETUP_KEY) -> dict[str, Any]:
    """Analyze all tournament arms for the selected tournament and compile overall metrics."""
    meta, target_minutes = resolve_tournament_meta(tourney_key)
    arms_config, record_error = resolve_tournament_arms_with_status(tourney_key)
    arms_data = [analyze_arm_database(cfg, target_minutes) for cfg in arms_config]
    
    # Sort by Total PnL descending for leaderboard
    sorted_arms = sorted(arms_data, key=lambda x: x.get("total_pnl", -999.0), reverse=True)
    
    total_trades = sum(a.get("trades_count", 0) for a in arms_data)
    total_merges = sum(a.get("merges_count", 0) for a in arms_data)
    total_fills = sum(a.get("fills_cnt", 0) for a in arms_data)
    total_quoted = sum(
        int((a.get("funnel") or {}).get("quoted", 0) or 0) for a in arms_data
    )
    total_filled_orders = sum(
        int((a.get("funnel") or {}).get("filled", 0) or 0) for a in arms_data
    )
    total_merged_legs = sum(
        int((a.get("funnel") or {}).get("merged_legs", 0) or 0) for a in arms_data
    )
    total_stop_loss_exits = sum(
        int((a.get("funnel") or {}).get("stop_loss", 0) or 0) for a in arms_data
    )

    # Aggregate strip (frontend-only, from the sums above): summed realized
    # PnL, closes-weighted win/merge %, best-minus-worst spread. NO_DATA
    # honesty when no arm DB has reported yet -- never a fabricated zero.
    ok_arms = [a for a in arms_data if a.get("status") == "ok"]
    agg_has_data = bool(ok_arms)
    total_closes = sum(int(a.get("trades_count", 0) or 0) for a in ok_arms)
    agg_realized_pnl = round(sum(float(a.get("total_pnl", 0.0) or 0.0) for a in ok_arms), 2)
    agg_win_rate = (
        round(sum(float(a.get("win_rate", 0.0) or 0.0) * int(a.get("trades_count", 0) or 0) for a in ok_arms) / total_closes, 1)
        if total_closes else 0.0
    )
    agg_merge_rate = (
        round(sum(int(a.get("merges_count", 0) or 0) for a in ok_arms) / total_closes * 100.0, 1)
        if total_closes else 0.0
    )
    _pnls = [float(a.get("total_pnl", 0.0) or 0.0) for a in ok_arms]
    agg_spread = round(max(_pnls) - min(_pnls), 2) if _pnls else 0.0
    
    # Global tournament progress
    max_elapsed = max((a.get("elapsed_min", 0.0) for a in arms_data), default=0.0)
    progress_pct = min(100.0, round((max_elapsed / target_minutes) * 100.0, 1))
    time_rem_min = max(0.0, target_minutes - max_elapsed)
    
    # Leaders
    leader_1 = None
    leader_2 = None
    if tourney_key == "484":
        leader_1 = max(
            (a for a in arms_data if a.get("issue") == 484),
            key=lambda x: x.get("total_pnl", -999.0),
            default=None
        )
        leader_2 = max(
            (a for a in arms_data if a.get("issue") == 485),
            key=lambda x: x.get("total_pnl", -999.0),
            default=None
        )
        l1_title = "#484 Strategy Leader"
        l2_title = "#485 Execution Leader"
    else:
        # Fresh round (held #487, live #492): top two by PnL.
        if sorted_arms:
            leader_1 = sorted_arms[0]
            leader_2 = sorted_arms[1] if len(sorted_arms) > 1 else sorted_arms[0]
        _tag = meta.get("issue_primary", tourney_key)
        l1_title = f"#{_tag} Top Performer"
        l2_title = f"#{_tag} Runner-Up"

    top_arm = sorted_arms[0] if sorted_arms else {}

    return {
        "tourney_key": tourney_key,
        "tourney_meta": meta,
        # None unless a record exists and could not be read: the page shows this
        # so a broken record never passes for "the run has not started".
        "record_error": record_error,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_trades": total_trades,
        "total_merges": total_merges,
        "total_fills": total_fills,
        "total_quoted": total_quoted,
        "total_filled_orders": total_filled_orders,
        "total_merged_legs": total_merged_legs,
        "total_stop_loss_exits": total_stop_loss_exits,
        "agg_has_data": agg_has_data,
        "agg_realized_pnl": agg_realized_pnl,
        "agg_win_rate": agg_win_rate,
        "agg_merge_rate": agg_merge_rate,
        "agg_spread": agg_spread,
        "max_elapsed_min": round(max_elapsed, 1),
        "elapsed_hours": round(max_elapsed / 60.0, 2),
        "target_hours": round(target_minutes / 60.0, 2),
        "rem_hours": round(time_rem_min / 60.0, 2),
        "progress_pct": progress_pct,
        "is_completed": max_elapsed >= target_minutes - 1.0,
        "l1_title": l1_title,
        "leader_484": leader_1["name"] if leader_1 else "N/A",
        "leader_484_label": leader_1.get("label", "N/A") if leader_1 else "N/A",
        "leader_484_port": leader_1.get("port", 0) if leader_1 else 0,
        "leader_484_pnl": leader_1.get("total_pnl", 0.0) if leader_1 else 0.0,
        "l2_title": l2_title,
        "leader_485": leader_2["name"] if leader_2 else "N/A",
        "leader_485_label": leader_2.get("label", "N/A") if leader_2 else "N/A",
        "leader_485_port": leader_2.get("port", 0) if leader_2 else 0,
        "leader_485_pnl": leader_2.get("total_pnl", 0.0) if leader_2 else 0.0,
        "top_score": top_arm.get("score", 0),
        "top_readiness": top_arm.get("readiness", "PENDING"),
        "arms": sorted_arms,
    }


def generate_cockpit_html(initial_data: dict[str, Any]) -> str:
    """Generate the full responsive, interactive HTML dashboard."""
    json_embedded = json.dumps(initial_data)
    # A record that exists but cannot be read must not look like "the run has not
    # started". The container is always emitted -- hidden when there is nothing to
    # say -- so the 5s /api/stats poll can show or hide it without a page reload.
    record_error = initial_data.get("record_error")
    record_warning_style = "display: block;" if record_error else "display: none;"
    record_warning_text = ""
    if record_error:
        record_warning_text = (
            "⚠️ Tournament record failed to parse: "
            f"{html.escape(str(record_error))}. "
            "The table shows default arms, not live data."
        )

    # Pre-render Live Readiness Decision Matrix rows for static snapshot & initial paint
    matrix_rationale = {
        "queue-clear-5m": "👑 <strong>Top Efficiency & Tail Risk Champion.</strong> Stale-book filter cuts max DD by 73% ($2.60) with Sharpe 4.67 and positive tail return (MC P5 +$12.90). Core prerequisite for live execution.",
        "balanced-mid": "🌟 <strong>Top Realized PnL Champion.</strong> Shipped dynamic 0.50x (2–4¢) spread tracking generated highest net profit (+$23.39) on 161 trades. Selected as the live pricing baseline.",
        "loose-baseline": "📊 <strong>Control Reference.</strong> Solid volume and $22.84 PnL, but unconstrained queues and missing stop loss leave orphan legs open to directional drift.",
        "static-floor-2c": "⚠️ <strong>Provisional.</strong> High volume ($17.99 PnL) but flat 2¢ floor without volatility tracking suffered -$2.28 spike loss during market blowout.",
        "tight-touch": "⚠️ <strong>Qualified Maker.</strong> Tight 0.25x fills fast on narrow books, but suffers 53% quote starvation on wide spreads. Positive EV but lower reach.",
        "prudent-hybrid": "⚠️ <strong>Provisional.</strong> Strong trade count and $19.24 PnL, but lifted 6¢ clamp suffered tournament-high drawdown ($11.29) during volatility cluster.",
        "low-churn-deadband": "🚫 <strong>Disqualified.</strong> 10¢ deadband is overly sticky; holds stale quotes into adverse drifts, bleeding $10.77 drawdown and -$3.63 MC P5.",
        "hair-trigger-2c": "🚫 <strong>Disqualified.</strong> 2¢ stop triggered 80 stop exits (57.5% exit rate!), liquidating complementary legs on normal tick flutter and gutting win rate to 42.4%.",
        "wide-shield": "🚫 <strong>Disqualified.</strong> Conservative 8¢ stop lets toxic orphan legs bleed deeply (-$0.37 avg loss) with negative tail expectancy (MC P5 -$6.76).",
        "patient-stop-9c": "⛔ <strong>REJECTED (Score < 75).</strong> 9¢ stop leash allowed catastrophic single-trade bleed (-$2.10), tanking Sharpe to 0.52 and MC P5 to -$9.56.",
    }
    ranked_arms_matrix = sorted(
        initial_data.get("arms", []),
        key=lambda x: (x.get("score") or 0, x.get("total_pnl") or 0.0),
        reverse=True,
    )
    dm_rows = []
    for idx, arm in enumerate(ranked_arms_matrix):
        pnl = arm.get("total_pnl", 0.0)
        pnl_cls = "positive" if pnl >= 0 else "negative"
        pnl_prefix = "+$" if pnl >= 0 else "-$"
        mcp5 = arm.get("mc_p5", 0.0)
        mcp5_cls = "positive" if mcp5 >= 0 else "negative"
        mcp5_prefix = "+$" if mcp5 >= 0 else "-$"
        name = arm.get("name", "")
        rationale = matrix_rationale.get(name, html.escape(arm.get("desc", "")))
        dm_rows.append(
            f'<tr>'
            f'<td><strong>#{idx+1}</strong> <span style="color: {arm.get("color", "#fff")}">●</span> <strong>{html.escape(arm.get("label") or name)}</strong>'
            f'<div style="font-size: 10px; color: var(--text-muted); line-height: 1.2; margin-top: 2px;">{html.escape(arm.get("desc", ""))}</div></td>'
            f'<td><span class="badge {arm.get("badge_cls", "badge-amber")}">{arm.get("readiness", "EVALUATING")}</span></td>'
            f'<td class="tabular"><strong>{arm.get("score", 0)}</strong> / 100</td>'
            f'<td class="tabular {pnl_cls}"><strong>{pnl_prefix}{abs(pnl):.2f}</strong></td>'
            f'<td class="tabular">{arm.get("mean_pnl", 0.0):.3f}</td>'
            f'<td class="tabular">{(arm.get("sharpe_proxy") or 0.0):.2f}</td>'
            f'<td class="tabular">{(arm.get("profit_factor") or 0.0):.2f}</td>'
            f'<td class="tabular">{(arm.get("win_rate") or 0.0):.1f}%</td>'
            f'<td class="tabular">{(arm.get("merge_rate") or 0.0):.1f}%</td>'
            f'<td class="tabular negative">${(arm.get("max_drawdown") or 0.0):.2f}</td>'
            f'<td class="tabular {mcp5_cls}">{mcp5_prefix}{abs(mcp5):.2f}</td>'
            f'<td style="max-width: 320px; font-size: 11.5px; line-height: 1.35;">{rationale}</td>'
            f'</tr>'
        )
    dm_tbody_content = "\n".join(dm_rows)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spread Hunter — Tournament Analytics & Decision Cockpit</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg-base: #0a0d14;
    --bg-card: #121722;
    --bg-card-hover: #171e2c;
    --bg-subtle: #1c2436;
    --border: #232c3f;
    --border-bright: #364461;
    --text-primary: #f0f4fc;
    --text-muted: #8b9bb4;
    --green: #00e676;
    --green-glow: rgba(0, 230, 118, 0.18);
    --red: #ff5252;
    --red-glow: rgba(255, 82, 82, 0.18);
    --blue: #00b0ff;
    --amber: #ffd600;
    --purple: #b388ff;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg-base);
    color: var(--text-primary);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13.5px;
    line-height: 1.5;
    padding: 20px;
    min-height: 100vh;
  }}
  .tabular {{ font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
  
  /* Tournament Navigation Switcher */
  .tourney-nav {{
    display: flex;
    align-items: center;
    gap: 8px;
    background: #0f141f;
    border: 1px solid var(--border);
    padding: 6px 12px;
    border-radius: 8px;
    margin-bottom: 16px;
  }}
  .tourney-nav-label {{
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    margin-right: 6px;
  }}
  .tourney-pill {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    text-decoration: none;
    color: var(--text-muted);
    border: 1px solid transparent;
    transition: all 0.2s;
  }}
  .tourney-pill:hover {{
    color: var(--text-primary);
    background: var(--bg-card);
    border-color: var(--border);
  }}
  .tourney-pill.active {{
    background: rgba(0, 230, 118, 0.12);
    color: var(--green);
    border-color: rgba(0, 230, 118, 0.4);
  }}

  /* Top Bar */
  header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 24px;
    margin-bottom: 20px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  }}
  .header-titles h1 {{
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.3px;
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .pulse-dot {{
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 12px var(--green);
    animation: pulse 1.8s infinite;
  }}
  @keyframes pulse {{
    0% {{ transform: scale(0.9); opacity: 0.7; }}
    50% {{ transform: scale(1.3); opacity: 1; }}
    100% {{ transform: scale(0.9); opacity: 0.7; }}
  }}
  .header-titles p {{
    color: var(--text-muted);
    font-size: 12px;
    margin-top: 3px;
  }}
  .header-controls {{
    display: flex;
    align-items: center;
    gap: 14px;
  }}
  .progress-card {{
    background: var(--bg-subtle);
    padding: 8px 16px;
    border-radius: 8px;
    border: 1px solid var(--border);
    text-align: right;
  }}
  .progress-bar-bg {{
    width: 180px; height: 6px; background: #232c3f;
    border-radius: 3px; overflow: hidden; margin-top: 5px;
  }}
  .progress-bar-fill {{
    height: 100%; background: linear-gradient(90deg, #00b0ff, #00e676);
    border-radius: 3px; transition: width 0.4s ease;
  }}
  .btn {{
    background: var(--bg-subtle);
    color: var(--text-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }}
  .btn:hover {{ background: var(--border); border-color: var(--text-muted); }}

  /* Metric Highlight Cards */
  .metrics-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 14px;
    margin-bottom: 20px;
  }}
  .m-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    transition: border-color 0.2s;
  }}
  .m-card:hover {{ border-color: var(--border-bright); }}
  .m-label {{ color: var(--text-muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }}
  .m-val {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
  .m-sub {{ font-size: 11.5px; color: var(--text-muted); margin-top: 4px; }}

  /* Tabs */
  .tabs {{
    display: flex;
    gap: 8px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 20px;
    padding-bottom: 4px;
  }}
  .tab-btn {{
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-size: 13.5px;
    font-weight: 600;
    padding: 8px 16px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.2s;
  }}
  .tab-btn:hover {{ color: var(--text-primary); background: var(--bg-card); }}
  .tab-btn.active {{ color: var(--text-primary); background: var(--bg-subtle); border: 1px solid var(--border); }}

  /* Panels */
  .tab-pane {{ display: none; }}
  .tab-pane.active {{ display: block; }}

  /* Cards and Charts Layout */
  .charts-grid {{
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
    margin-bottom: 20px;
  }}
  .chart-box {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px;
  }}
  .chart-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 14px;
  }}
  .chart-title {{ font-size: 14px; font-weight: 700; }}
  .chart-legend {{ font-size: 11.5px; color: var(--text-muted); }}

  /* Table styling */
  .table-box {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    margin-bottom: 20px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    text-align: left;
  }}
  th {{
    background: var(--bg-subtle);
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
  }}
  td {{
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
    font-size: 12.5px;
  }}
  tr:hover td {{ background: var(--bg-card-hover); }}
  .badge {{
    display: inline-block;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
  }}
  .badge-green {{ background: var(--green-glow); color: var(--green); border: 1px solid rgba(0,230,118,0.3); }}
  .badge-amber {{ background: rgba(255,214,0,0.15); color: var(--amber); border: 1px solid rgba(255,214,0,0.3); }}
  .badge-red {{ background: var(--red-glow); color: var(--red); border: 1px solid rgba(255,82,82,0.3); }}

  .positive {{ color: var(--green); }}
  .negative {{ color: var(--red); }}

  /* Decision Callout */
  .decision-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-top: 16px;
  }}
  .decision-box {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px;
  }}
  .decision-box h3 {{
    font-size: 15px; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;
  }}
  .decision-box ul {{
    padding-left: 20px; color: var(--text-muted); font-size: 12.5px; line-height: 1.7;
  }}
  .decision-box strong {{ color: var(--text-primary); }}
</style>
</head>
<body>

<!-- Tournament Navigation Switcher: the current setup is the only page served.
     The past-run pages (#484, #492, #608) were purged; their routes 404. -->
<div class="tourney-nav">
  <span class="tourney-nav-label">Tournament Evaluation:</span>
  <a href="/487" class="tourney-pill active">
    <span>🧪</span>
    <span>{initial_data.get('tourney_meta', {}).get('nav_label') or '#487 Current Setup (10 Arms)'}</span>
    <span class="badge badge-green" style="font-size: 9px; padding: 1px 5px;">CURRENT SETUP</span>
  </a>
</div>

<header>
  <div class="header-titles">
    <h1><span class="pulse-dot"></span> {initial_data.get('tourney_meta', {}).get('title', 'Spread Hunter — Tournament Cockpit')}</h1>
    <p>{initial_data.get('tourney_meta', {}).get('subtitle', '12-Hour Multi-Arm Live Shadow Evaluation')}</p>
    <p style="color: var(--text-muted); font-size: 12px; line-height: 1.35; margin-top: 4px; max-width: 820px;">{initial_data.get('tourney_meta', {}).get('description', '')}</p>
  </div>
  <div class="header-controls">
    <div class="progress-card">
      <div style="font-size: 11px; color: var(--text-muted); display: flex; justify-content: space-between;">
        <span>Progress: <strong id="progress-text" class="tabular">{initial_data.get('elapsed_hours', 0.0):.1f}h / {initial_data.get('target_hours', 12.0):.1f}h</strong></span>
        <span id="rem-text" class="tabular">~{initial_data.get('rem_hours', 12.0):.1f}h rem</span>
      </div>
      <div class="progress-bar-bg">
        <div id="progress-bar" class="progress-bar-fill" style="width: {initial_data.get('progress_pct', 0.0):.1f}%;"></div>
      </div>
    </div>
    <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 4px;">
      <button class="btn" id="btn-refresh" onclick="fetchLiveStats()">↻ Refresh Data</button>
      <span id="refresh-status" style="font-size: 11px; color: var(--text-muted);">Loaded</span>
    </div>
  </div>
</header>

<div id="record-warning" class="badge-red" style="{record_warning_style} width: 100%; box-sizing: border-box; padding: 10px 14px; border-radius: 8px; font-size: 12.5px; line-height: 1.5; margin: 0 0 18px;">{record_warning_text}</div>

<!-- Top Headline Metric Cards -->
<div class="metrics-grid">
  <div class="m-card">
    <div class="m-label" id="m-l1-title">{initial_data.get('l1_title', 'Top Performer')}</div>
    <div class="m-val tabular positive" id="m-leader-484">{initial_data.get('leader_484_label', initial_data.get('leader_484', 'N/A'))}</div>
    <div class="m-sub" id="m-leader-484-pnl">PnL: {('+$' if initial_data.get('leader_484_pnl', 0.0) >= 0 else '-$') + f"{abs(initial_data.get('leader_484_pnl', 0.0)):.2f}"} | Port {initial_data.get('leader_484_port', 0)}</div>
  </div>
  <div class="m-card">
    <div class="m-label" id="m-l2-title">{initial_data.get('l2_title', 'Runner-Up')}</div>
    <div class="m-val tabular positive" id="m-leader-485">{initial_data.get('leader_485_label', initial_data.get('leader_485', 'N/A'))}</div>
    <div class="m-sub" id="m-leader-485-pnl">PnL: {('+$' if initial_data.get('leader_485_pnl', 0.0) >= 0 else '-$') + f"{abs(initial_data.get('leader_485_pnl', 0.0)):.2f}"} | Port {initial_data.get('leader_485_port', 0)}</div>
  </div>
  <div class="m-card">
    <div class="m-label">Total Pair Merges</div>
    <div class="m-val tabular" id="m-merges">{initial_data.get('total_merges', 0):,}</div>
    <div class="m-sub" id="m-merges-sub">Across all {len(initial_data.get('arms') or [])} tournament arms</div>
  </div>
  <div class="m-card">
    <div class="m-label">Total Quoted Orders</div>
    <div class="m-val tabular" id="m-fills">{initial_data.get('total_quoted', 0):,}</div>
    <div class="m-sub">Funnel entry: every quote placed across all arms</div>
  </div>
  <div class="m-card">
    <div class="m-label">Top Live Candidate Score</div>
    <div class="m-val tabular positive" id="m-top-score">{initial_data.get('top_score', 0)} / 100</div>
    <div class="m-sub">Readiness: {initial_data.get('top_readiness', 'EVALUATING')}</div>
  </div>
</div>

<!-- Tabs Navigation -->
<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('overview', this)">🏆 Leaderboard &amp; Overview</button>
  <button class="tab-btn" onclick="switchTab('hybrid-config', this)">⚙️ HYBRID Configuration Blueprint</button>
  <button class="tab-btn" onclick="switchTab('pnl-chart', this)">📈 Cumulative PnL Curves</button>
  <button class="tab-btn" onclick="switchTab('risk-stats', this)">⚖️ Risk &amp; Volatility (EV, &sigma;, MDD)</button>
  <button class="tab-btn" onclick="switchTab('execution', this)">🎯 Merge vs Fallout Quality</button>
  <button class="tab-btn" onclick="switchTab('monte-carlo', this)">🎲 Monte Carlo Projections</button>
  <button class="tab-btn" onclick="switchTab('recommendations', this)">🚀 Live Readiness Decision Matrix</button>
</div>

<!-- TAB 1: OVERVIEW & LEADERBOARD -->
<div id="tab-overview" class="tab-pane active">
  <div class="table-box" style="margin-bottom: 16px;">
    <div style="display: flex; gap: 18px; flex-wrap: wrap; padding: 12px 16px; font-size: 13px;">
      <span>Aggregate realized PnL: <strong class="tabular" id="agg-pnl">{('+$' if initial_data.get('agg_realized_pnl', 0.0) >= 0 else '-$') + f"{abs(initial_data.get('agg_realized_pnl', 0.0)):.2f}" if initial_data.get('agg_has_data') else 'NO_DATA'}</strong></span>
      <span>Closes-weighted win: <strong class="tabular" id="agg-win">{f"{initial_data.get('agg_win_rate', 0.0):.1f}%" if initial_data.get('agg_has_data') else 'NO_DATA'}</strong></span>
      <span>Closes-weighted merge: <strong class="tabular" id="agg-merge">{f"{initial_data.get('agg_merge_rate', 0.0):.1f}%" if initial_data.get('agg_has_data') else 'NO_DATA'}</strong></span>
      <span>Best-minus-worst: <strong class="tabular" id="agg-spread">{('$' + f"{initial_data.get('agg_spread', 0.0):.2f}") if initial_data.get('agg_has_data') else 'NO_DATA'}</strong></span>
    </div>
  </div>
  <div class="table-box">
    <table>
      <thead>
        <tr>
          <th>Rank &amp; Arm</th>
          <th>Sharpe</th>
          <th>Profit Factor</th>
          <th>VaR95</th>
          <th>MC P5</th>
          <th>Filled Orders</th>
          <th>Stop-Loss Exits</th>
          <th>Merge %</th>
          <th>Total PnL</th>
          <th>EV / Trade</th>
          <th>Std Dev (&sigma;)</th>
          <th>Max DD</th>
          <th>Worst Catalyst &amp; Hypothesized Tweak</th>
          <th>Live Score</th>
          <th>Readiness Verdict</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="leaderboard-tbody">
        <!-- Rendered dynamically -->
      </tbody>
    </table>
  </div>

  <div class="charts-grid">
    <div class="chart-box">
      <div class="chart-header">
        <div class="chart-title">Realized PnL vs Max Drawdown (Risk-Return Frontier)</div>
        <div class="chart-legend">Higher and further left is superior</div>
      </div>
      <div style="height: 280px;">
        <canvas id="scatterChart"></canvas>
      </div>
    </div>
    <div class="chart-box">
      <div class="chart-header">
        <div class="chart-title" id="radar-title">Multi-Factor Radar: Leader vs Control</div>
        <div class="chart-legend">Balanced profile comparison</div>
      </div>
      <div style="height: 280px;">
        <canvas id="radarChart"></canvas>
      </div>
    </div>
  </div>
</div>

<!-- TAB: HYBRID CONFIGURATION BLUEPRINT -->
<div id="tab-hybrid-config" class="tab-pane">
  <div style="background: linear-gradient(135deg, rgba(0, 230, 118, 0.12), rgba(0, 176, 255, 0.12)); border: 1px solid rgba(0, 230, 118, 0.4); border-radius: 12px; padding: 22px; margin-bottom: 20px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 10px;">
      <div>
        <span class="badge badge-green" style="font-size: 12px; padding: 4px 10px; margin-bottom: 6px;">🌟 #799 LEADER RECIPE — INTERIM</span>
        <h2 style="font-size: 18px; font-weight: 700; color: #f0f4fc;">HYBRID Blueprint — Built From the #799 Leader Arm</h2>
        <p style="color: var(--text-muted); font-size: 12.5px; margin-top: 4px;">
          Row 1 is the leader arm's recipe, copied from the env it launched with. Every row after it is labeled with what
          sets the value, because #799 varies exactly one knob per arm and did not test the deployment layer. Read against the run so far:
          <strong id="hb-quotes" class="tabular">—</strong> quotes and
          <strong id="hb-trades" class="tabular">—</strong> trades across
          <strong id="hb-arms" class="tabular">—</strong> arms, still in flight.
        </p>
      </div>
      <div style="text-align: right;">
        <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 600;">Pricing Profile — the leader's own</div>
        <div class="tabular" style="color: var(--green); font-size: 15px; font-weight: 700;">core_brain.config.TOURNAMENT_PRESETS["balanced"]</div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">A shipped preset, and the leader arm's single env override. Its live standing is in the card below.</div>
      </div>
    </div>
  </div>

  <div class="table-box" style="margin-bottom: 24px;">
    <table>
      <thead>
        <tr>
          <th>Parameter</th>
          <th>Config Value</th>
          <th>CLI / Env Key</th>
          <th>Set By</th>
          <th>Live Function &amp; Mathematical Rationale</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Volatility Pricing Preset</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>Dynamic 0.50x, 2-4¢</strong></td>
          <td><code>BOT_QUOTE_TOURNAMENT_PRESET=balanced</code></td>
          <td><span class="badge badge-green">#799 balanced-mid — the leader's only env key</span></td>
          <td><strong>Measured Recipe:</strong> The leader arm ran the shipped baseline with exactly one override, the <code>balanced</code> preset: book-spread tracking at 0.50x inside a 2¢ floor and a 4¢ ceiling. Reproducing that arm's pricing takes this one variable.</td>
        </tr>
        <tr>
          <td><strong>Spread Offset Ceiling</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>4.0¢</strong></td>
          <td><code>BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS</code> — preset default, no line needed</td>
          <td><span class="badge badge-green">balanced preset</span></td>
          <td><strong>Reach Anchor:</strong> The ceiling is 4¢ because that is what the preset ships; the leader sets no override here, so no <code>BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS</code> line belongs in the deployment file. An earlier version of this tab lifted it to 6¢ — a value no #799 arm ran.</td>
        </tr>
        <tr>
          <td><strong>Requote Deadband</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>0.060 (6.0¢)</strong></td>
          <td><code>BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.06</code></td>
          <td><span class="badge badge-amber">Override — 3¢ ships; #799 did not settle it</span></td>
          <td><strong>Queue Priority Defender:</strong> Suppresses cancellation &amp; requote while the book shifts ≤ 6¢, protecting queue position. Named honestly: the shipped default is 3¢, and the only arm to vary this knob ran 10¢ <em>and</em> switched the dynamic offset off, finishing 9th of 10. 6¢ is a deliberate middle, not a measured win.</td>
        </tr>
        <tr>
          <td><strong>Single-Buy Stop Drop</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>$0.045 (4.5¢)</strong></td>
          <td><code>BOT_RISK_STOP_MODE=ABSOLUTE_DROP</code><br><code>BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.045</code></td>
          <td><span class="badge badge-amber">Override — no stop ships</span></td>
          <td><strong>Tail Risk Limiter:</strong> A hard loss stop under absolute drop mode, firing 0.045 below entry, so an orphan leg cannot become an unchosen directional bet. The shipped default is mode <code>NONE</code>, so this is a deployment decision rather than a tournament result: #799's stop arms ran 2¢, 8¢ and 9¢ and finished 7th, 8th and 10th, while the no-stop control finished 2nd. Watched live on the leader arm: <span id="hb-stops" class="tabular">—</span> stop exits, <span id="hb-stoprate" class="tabular">—</span>% of its closed trades.</td>
        </tr>
        <tr>
          <td><strong>Queue-Clear Gate</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>5 min</strong></td>
          <td><code>BOT_ORDER_GATE_QUEUE_CLEAR_ON=1</code><br><code>BOT_ORDER_QUEUE_MAX_CLEAR_MIN=5</code></td>
          <td><span class="badge badge-green">#799 queue-clear-5m — leads on Sharpe, PF, drawdown</span></td>
          <td><strong>Stale Book Filter:</strong> Refuses books whose queue is unlikely to clear, so the second leg can fill before the market's regime changes. 5 minutes is the value the arm actually ran, and the clearest single-knob result in #799: it leads the field on Sharpe, profit factor, max drawdown and Monte Carlo P5, on an edge per trade indistinguishable from the leader's, with the best win and merge rates. It ranks 6th on realized PnL only because it trades least — last of the ten on volume — which is the bargain this gate makes: fewer, better fills for a third of the drawdown. Its Sharpe right now is <span id="hb-qcsharpe" class="tabular">—</span>; if that has stopped leading the field, the standing card above names the current leader. The 3 minutes in the previous revision was a window no arm ran.</td>
        </tr>
        <tr>
          <td><strong>Aged-Out Rescue Lead</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>45.0s</strong></td>
          <td><code>BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC=45.0</code></td>
          <td><span class="badge badge-amber">Override — 900s ships</span></td>
          <td><strong>Resolution Unwind:</strong> Begins emergency liquidation of single legs 45s before the market's resolution freeze. The shipped lead is 900s, so this is fifteen times earlier — a deployment choice, untouched by #799.</td>
        </tr>
        <tr>
          <td><strong>Absolute Pair Cap</strong></td>
          <td class="tabular" style="font-size: 14px; color: var(--amber);"><strong>$0.990</strong></td>
          <td><code>BOT_POS_PRICE_MAX_PAIR_COST_USD</code></td>
          <td><span class="badge badge-amber">Core Safety Law</span></td>
          <td><strong>Arbitrage Floor:</strong> Shipped default guaranteeing minimum $0.01 gross margin per pair before $1.00 USDC resolution payout.</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="decision-grid" style="margin-bottom: 24px;">
    <div class="decision-box" style="border-left: 4px solid var(--green);">
      <h3 style="color: var(--green);">📊 Leader Arm — Live Standing in #799 (Interim)</h3>
      <p style="color: var(--text-muted); font-size: 11.5px; margin: 0 0 10px 0; line-height: 1.45;">
        Recomputed from <code>balanced-mid</code>, the arm this blueprint is built from, on the same 5s poll as the rest of this page.
        Not a forecast, and not a verdict: the run is <strong id="hb-progress" class="tabular">—</strong> complete, and this run's
        <code>results.json</code> carries the note “Shadow results are modeled… The stat gate decides.” The standing row below says whether that arm is still ahead.
      </p>
      <ul style="list-style-type: none; padding-left: 0; line-height: 1.8;">
        <li>🎯 <strong>Observed Win Rate:</strong> <span id="hb-win" class="tabular positive">—</span> <span style="color: var(--text-muted);">vs league <span id="hb-aggwin" class="tabular">—</span></span></li>
        <li>🔗 <strong>Merge-to-Close:</strong> <span id="hb-merge" class="tabular positive">—</span> <span style="color: var(--text-muted);">vs league <span id="hb-aggmerge" class="tabular">—</span></span></li>
        <li>⚡ <strong>Sharpe Proxy:</strong> <span id="hb-sharpe" class="tabular positive">—</span> <span style="color: var(--text-muted);">league best <span id="hb-bestsharpe" class="tabular">—</span> (<span id="hb-bestsharpearm">—</span>)</span></li>
        <li>💰 <strong>Realized EV / Trade:</strong> <span id="hb-ev" class="tabular positive">—</span> per closed position</li>
        <li>⚖️ <strong>Profit Factor:</strong> <span id="hb-pf" class="tabular positive">—</span></li>
        <li>🎲 <strong>Monte Carlo P5 (100-Trade Draw):</strong> <span id="hb-mcp5" class="tabular positive">—</span></li>
        <li>📉 <strong>Max Drawdown:</strong> <span id="hb-mdd" class="tabular negative">—</span></li>
        <li>🏁 <strong>Standing Among the Arms:</strong> <span id="hb-rank" class="tabular">—</span>, at <span id="hb-pnl" class="tabular positive">—</span> realized — leader <span id="hb-leader">—</span> (<span id="hb-leaderpnl" class="tabular positive">—</span>)</li>
      </ul>
    </div>

    <div class="decision-box" style="border-left: 4px solid var(--red);">
      <h3 style="color: var(--red);">⚠️ Stress Scenarios &amp; Failure Mode Defenses</h3>
      <ul style="list-style-type: none; padding-left: 0; line-height: 1.8;">
        <li>💥 <strong>1. Flash Directional Breakout:</strong> Leg 1 fills immediately prior to a sharp >15¢ event before Leg 2 posts.
          <div style="font-size: 11px; color: var(--text-muted); margin-left: 14px;"><strong>Defense:</strong> the 4.5¢ absolute-drop stop cuts the orphan at -$0.045 instead of letting it run. Observed on this arm: <span id="hb-stops2" class="tabular">—</span> stop-loss exits.</div>
        </li>
        <li>🧊 <strong>2. Liquidity Vacuum / Stalled Book:</strong> Taker volume halts right after Leg 1 fill, leaving second leg stranded.
          <div style="font-size: 11px; color: var(--text-muted); margin-left: 14px;">            <strong>Defense:</strong> the 5m queue-clear filter avoids stagnant books; the 45s rescue unwinds the leg into settlement.</div>
        </li>
        <li>🏓 <strong>3. High-Frequency Ping-Pong Jitter:</strong> Micro-tick flutter triggers repeated cancellations.
          <div style="font-size: 11px; color: var(--text-muted); margin-left: 14px;"><strong>Defense:</strong> 6.0¢ deadband suppresses cancel/re-quote order churn, maintaining book priority.</div>
        </li>
      </ul>
    </div>
  </div>

  <div class="decision-grid" style="margin-bottom: 24px;">
    <div class="decision-box">
      <h3 style="color: var(--blue);">💻 Ready-to-Run PowerShell &amp; CLI Commands</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">Rehearse the spec, then serve this page. Live trading is a separate, operator-run step:</p>
      <pre style="background: #090c12; border: 1px solid var(--border); border-radius: 8px; padding: 12px; font-family: monospace; font-size: 12px; color: #a5d6a7; overflow-x: auto;">
# 1. Interactive Menu - rehearse the leader's preset:
.\\scripts\\spread-hunter-menu.ps1 shadow-run -Preset balanced -Minutes 60

# 2. Or run it directly, with the leader's preset in the environment:
$env:BOT_QUOTE_TOURNAMENT_PRESET = "balanced"
python -m core_brain.shadow_run --minutes 60 --interval 1.0 --db data/leader_balanced_shadow_01.db --run-id shadow-leader-balanced-01 --starting-bankroll-usd 100

# 3. Launch Tournament Cockpit Server:
python -m scripts.tournament_cockpit_server --port 8800
      </pre>
    </div>

    <div class="decision-box">
      <h3 style="color: var(--green);">🛡️ Live Production Environment Variables (.env)</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">Exact configuration for live deployment:</p>
      <pre style="background: #090c12; border: 1px solid var(--border); border-radius: 8px; padding: 12px; font-family: monospace; font-size: 12px; color: #81d4fa; overflow-x: auto;">
# --- Built from the #799 leader arm (`balanced-mid`) ---
# The first line is that arm's own env. Every line after it is a
# deliberate override of a shipped default, which #799 did not vary.
BOT_QUOTE_TOURNAMENT_PRESET=balanced
BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.06
BOT_RISK_STOP_MODE=ABSOLUTE_DROP
BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.045
BOT_ORDER_GATE_QUEUE_CLEAR_ON=1
BOT_ORDER_QUEUE_MAX_CLEAR_MIN=5
BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC=45.0
# BOT_POS_PRICE_MAX_PAIR_COST_USD=0.99  # Shipped default (0.99); do not alter
      </pre>
    </div>
  </div>
</div>

<!-- TAB 2: PNL CURVES -->
<div id="tab-pnl-chart" class="tab-pane">
  <div class="chart-box" style="margin-bottom: 20px;">
    <div class="chart-header">
      <div class="chart-title">Cumulative PnL Trajectory Across {initial_data.get('target_hours', 12.0):g} Hours (Minutes 0 to {initial_data.get('target_hours', 12.0) * 60:g})</div>
      <div class="chart-legend">Click legend items to toggle arms</div>
    </div>
    <div style="height: 480px;">
      <canvas id="pnlLineChart"></canvas>
    </div>
  </div>
</div>

<!-- TAB 3: RISK & VOLATILITY -->
<div id="tab-risk-stats" class="tab-pane">
  <div class="charts-grid">
    <div class="chart-box">
      <div class="chart-header">
        <div class="chart-title">Expected Return (EV) vs Volatility (&sigma;) Per Trade</div>
        <div class="chart-legend">Positive expectancy with narrow dispersion wins</div>
      </div>
      <div style="height: 320px;">
        <canvas id="evBarChart"></canvas>
      </div>
    </div>
    <div class="chart-box">
      <div class="chart-header">
        <div class="chart-title">Profit Factor Comparison</div>
        <div class="chart-legend">Gross Gains / Gross Losses (Target &gt; 1.50)</div>
      </div>
      <div style="height: 320px;">
        <canvas id="pfBarChart"></canvas>
      </div>
    </div>
  </div>
</div>

<!-- TAB 4: EXECUTION & MERGES -->
<div id="tab-execution" class="tab-pane">
  <div class="chart-box" style="margin-bottom: 20px;">
    <div class="chart-header">
      <div class="chart-title">Execution Composition: Successful Merges vs Single-Leg Exits</div>
      <div class="chart-legend">Single-leg exits incur haircuts; higher merge proportion protects edge</div>
    </div>
    <div style="height: 360px;">
      <canvas id="mergeStackedBarChart"></canvas>
    </div>
  </div>
</div>

<!-- TAB 5: MONTE CARLO -->
<div id="tab-monte-carlo" class="tab-pane">
  <div class="chart-box" style="margin-bottom: 20px;">
    <div class="chart-header">
      <div class="chart-title">100-Trade Monte Carlo Projections: 5th Percentile, Median, and 95th Percentile</div>
      <div class="chart-legend">Empirical resampling (500 simulations) of realized trade distribution</div>
    </div>
    <div style="height: 360px;">
      <canvas id="mcBarChart"></canvas>
    </div>
  </div>
</div>

<!-- TAB 6: RECOMMENDATIONS -->
<div id="tab-recommendations" class="tab-pane">
  <div style="background: linear-gradient(135deg, rgba(0, 230, 118, 0.12), rgba(0, 176, 255, 0.12)); border: 1px solid rgba(0, 230, 118, 0.4); border-radius: 12px; padding: 22px; margin-bottom: 20px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 10px;">
      <div>
        <span class="badge badge-green" style="font-size: 12px; padding: 4px 10px; margin-bottom: 6px;">🚀 #799 PRODUCTION READINESS VERDICT</span>
        <h2 style="font-size: 18px; font-weight: 700; color: #f0f4fc;">Live Readiness Decision Matrix — #799 Tournament Evaluation</h2>
        <p style="color: var(--text-muted); font-size: 12.5px; margin-top: 4px;">
          Multi-factor readiness audit across 10 tournament arms. Production gate requires: <strong>Score &ge; 75</strong>, <strong>Realized PnL &gt; $5.00</strong>, and <strong>Monte Carlo Prob(PnL &gt; 0) &ge; 70%</strong>.
        </p>
      </div>
      <div style="text-align: right;">
        <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 600;">Scoring Model Framework</div>
        <div class="tabular" style="color: var(--green); font-size: 14px; font-weight: 700;">PnL (30) &bull; Merge% (25) &bull; Low DD (20) &bull; PF (15) &bull; Sample (10)</div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">100-point composite score penalizing drawdown, fee churn, and tail bleed.</div>
      </div>
    </div>
  </div>

  <div class="table-box" style="margin-bottom: 24px;">
    <div style="padding: 12px 16px; background: var(--bg-subtle); border-bottom: 1px solid var(--border); font-size: 13px; font-weight: 600; color: #f0f4fc;">
      📊 Cross-Arm Live Readiness Decision Matrix
    </div>
    <table>
      <thead>
        <tr>
          <th>Rank &amp; Strategy Arm</th>
          <th>Readiness Verdict</th>
          <th>Live Score</th>
          <th>Total PnL</th>
          <th>EV / Trade</th>
          <th>Sharpe</th>
          <th>Profit Factor</th>
          <th>Win Rate</th>
          <th>Merge Rate</th>
          <th>Max DD</th>
          <th>MC P5</th>
          <th>Observed Failure Mode &amp; Deployment Decision</th>
        </tr>
      </thead>
      <tbody id="decision-matrix-tbody">
{dm_tbody_content}
      </tbody>
    </table>
  </div>

  <div class="decision-grid" style="margin-bottom: 24px;">
    <div class="decision-box" style="border-left: 4px solid var(--green);">
      <h3 style="color: var(--green);">🌟 Recommended Production Configuration (#799 Champion Synthesis)</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 12px; line-height: 1.45;">
        Synthesizes the measured winners of the #799 tournament run into the target live deployment stack:
      </p>
      <ul>
        <li><strong>Volatility Pricing Base: <code>balanced-mid</code> (Preset: <code>balanced</code>)</strong> — Book-spread tracking at dynamic 0.50x (2–4¢ floor/ceiling) captured the highest realized PnL (+<strong>$23.39</strong> across 161 trades) without quote starvation.</li>
        <li><strong>Queue-Clear Gate: <code>queue-clear-5m</code> (Mandatory Guard)</strong> — Slashes max drawdown by <strong>73%</strong> (to $2.60) while delivering tournament-high Sharpe (<strong>4.67</strong>), Profit Factor (<strong>3.23</strong>), and positive tail expectancy (MC P5 +$12.90).</li>
        <li><strong>Disciplined Deadband: <code>BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.06</code> (6.0¢)</strong> — Prevents fee churn and queue rank loss without the toxic stickiness of 10¢.</li>
        <li><strong>Orphan Loss Stop: <code>BOT_RISK_STOP_MODE=ABSOLUTE_DROP</code> &bull; <code>0.045</code> (4.5¢)</strong> — Protects single legs without the 57% stop churn of 2¢ hair-triggers or the -$2.10 deep tail bleed of 9¢ leashes.</li>
        <li><strong>Aged-Out Rescue Lead: <code>BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC=45.0</code></strong> — Orderly emergency single-leg unwind into resolution.</li>
      </ul>
    </div>

    <div class="decision-box" style="border-left: 4px solid var(--red);">
      <h3 style="color: var(--red);">⚠️ Disqualified Configurations &amp; Measured Failure Modes (#799 Evidence)</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 12px; line-height: 1.45;">
        Configurations empirically rejected based on measured tournament breakdown:
      </p>
      <ul>
        <li><strong>Avoid: <code>patient-stop-9c</code> (FAILED THRESHOLD &mdash; Score 68 / 100)</strong> — 9.0¢ leash allowed catastrophic single-trade bleed (-$2.10 worst loss) and negative tail expectancy (MC P5 -$9.56, lowest Sharpe 0.52).</li>
        <li><strong>Avoid: <code>hair-trigger-2c</code> (Score 77)</strong> — Premature stop triggers dumped 80 trades (57.5% exit rate) on 1-tick normal noise, wrecking win rate to 42.4%.</li>
        <li><strong>Avoid: <code>low-churn-deadband</code> (Score 80)</strong> — 10.0¢ deadband proved overly sticky; held stale orders into adverse market drifts, suffering $10.77 drawdown.</li>
        <li><strong>Avoid: <code>static-floor-2c</code> (Score 95, but tail risk)</strong> — Static 2¢ floor without volatility widening suffered -$2.28 single loss spike during market blowout.</li>
        <li><strong>Avoid: <code>wide-shield</code> (Score 75)</strong> — 8.0¢ stop let toxic orphan legs bleed deeply (-$0.37 avg loss per stop) with negative MC P5 (-$6.76).</li>
      </ul>
    </div>
  </div>

  <div class="decision-grid" style="margin-bottom: 24px;">
    <div class="decision-box" style="border-left: 4px solid var(--blue);">
      <h3 style="color: var(--blue);">🛡️ Operational Deployment Policy &amp; Circuit Breakers</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">Enforced risk controls and automated kill switches for live execution:</p>
      <ul>
        <li><strong>Portfolio Daily Drawdown Limit: $15.00</strong> — Immediate trading halt and cancel-all if 24h rolling realized drawdown touches $15.00.</li>
        <li><strong>Dynamic Exposure Caps (<code>core_brain/config.py</code>)</strong>:
          Max single order exposure: <strong>25%</strong> of bankroll ($25 at $100 baseline);
          Max naked single-leg exposure: <strong>6%</strong> ($6.00);
          Total bankroll ceiling: <strong>90%</strong> ($90.00).
        </li>
        <li><strong>Immutable Pair Cap: <code>BOT_POS_PRICE_MAX_PAIR_COST_USD=0.99</code></strong> — Shipped invariant guaranteeing minimum $0.01 margin before $1.00 settlement payout.</li>
      </ul>
    </div>

    <div class="decision-box" style="border-left: 4px solid var(--amber);">
      <h3 style="color: var(--amber);">📋 Live Production Go / No-Go Checklist</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">Stat gate validation requirements before switching from shadow to live trading:</p>
      <ul>
        <li>&#9989; <strong>Composite Score &ge; 75</strong> — Multi-factor readiness rating passing EV, merge rate, drawdown, and PF thresholds.</li>
        <li>&#9989; <strong>Statistical Sample &ge; 50 Closed Trades</strong> — Adequate empirical sample depth before flipping live venue execution.</li>
        <li>&#9989; <strong>Monte Carlo Positive Outcome &ge; 70%</strong> — 500-iteration resampled trade sequences confirming positive expectancy.</li>
        <li>&#9989; <strong>Merge Efficiency &ge; 55%</strong> — Verification that two-sided fills dominate single-leg rescue exits.</li>
      </ul>
    </div>
  </div>
</div>

<script>
let state = {json_embedded};
let charts = {{}};

function switchTab(name, btn) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  const activeBtn = btn || (typeof event !== 'undefined' ? event.target : null);
  if (activeBtn) activeBtn.classList.add('active');
  const target = document.getElementById('tab-' + name);
  if (target) target.classList.add('active');
}}

function renderAll() {{
  // The warning must survive a refresh, and appear if a record stops parsing
  // mid-run: /api/stats carries `record_error` on every poll.
  const recWarn = document.getElementById('record-warning');
  if (recWarn) {{
    if (state.record_error) {{
      recWarn.innerText = '⚠️ Tournament record failed to parse: ' + state.record_error + '. The table shows default arms, not live data.';
      recWarn.style.display = 'block';
    }} else {{
      recWarn.style.display = 'none';
    }}
  }}
  // Header metrics
  const elapsedH = (state.elapsed_hours != null ? Number(state.elapsed_hours) : 0).toFixed(1);
  const remH = (state.rem_hours != null ? Number(state.rem_hours) : 12.0).toFixed(1);
  const targetH = (state.target_hours != null ? Number(state.target_hours) : 12.0).toFixed(1);
  const pct = Math.min(100, Math.max(0, state.progress_pct || 0)).toFixed(1);
  document.getElementById('progress-text').innerText = elapsedH + 'h / ' + targetH + 'h';
  document.getElementById('rem-text').innerText = '~' + remH + 'h rem';
  document.getElementById('progress-bar').style.width = pct + '%';
  document.getElementById('m-leader-484').innerText = state.leader_484_label || state.leader_484;
  document.getElementById('m-leader-484-pnl').innerText = 'PnL: ' + (state.leader_484_pnl >= 0 ? '+$' : '-$') + Math.abs(state.leader_484_pnl).toFixed(2);
  document.getElementById('m-leader-485').innerText = state.leader_485_label || state.leader_485;
  document.getElementById('m-leader-485-pnl').innerText = 'PnL: ' + (state.leader_485_pnl >= 0 ? '+$' : '-$') + Math.abs(state.leader_485_pnl).toFixed(2);
  document.getElementById('m-merges').innerText = state.total_merges.toLocaleString();
  document.getElementById('m-fills').innerText = (state.total_quoted || 0).toLocaleString();
  const aggPnl = document.getElementById('agg-pnl');
  if (aggPnl) aggPnl.innerText = state.agg_has_data
    ? ((state.agg_realized_pnl >= 0 ? '+$' : '-$') + Math.abs(state.agg_realized_pnl).toFixed(2)) : 'NO_DATA';
  const aggWin = document.getElementById('agg-win');
  if (aggWin) aggWin.innerText = state.agg_has_data ? Number(state.agg_win_rate).toFixed(1) + '%' : 'NO_DATA';
  const aggMerge = document.getElementById('agg-merge');
  if (aggMerge) aggMerge.innerText = state.agg_has_data ? Number(state.agg_merge_rate).toFixed(1) + '%' : 'NO_DATA';
  const aggSpread = document.getElementById('agg-spread');
  if (aggSpread) aggSpread.innerText = state.agg_has_data ? '$' + Number(state.agg_spread).toFixed(2) : 'NO_DATA';

  // HYBRID blueprint tab: the leader arm's standing, read from the same poll as
  // the rest of the page. The blueprint is a specification and may be static, but
  // its *numbers* must come from the run they cite -- that is what made the
  // previous hardcoded forecast wrong the moment the run moved past it.
  const hbSet = (id, text) => {{
    const el = document.getElementById(id);
    if (el) el.innerText = text;
  }};
  const hbArms = state.arms || [];
  const hbArm = hbArms.find(a => a.name === 'balanced-mid');
  if (hbArm) {{
    const hbPct = (v) => (v == null ? '\u2014' : Number(v).toFixed(1) + '%');
    const hbUsd = (v) => ((v >= 0 ? '+$' : '-$') + Math.abs(Number(v) || 0).toFixed(2));
    hbSet('hb-quotes', (state.total_quoted || 0).toLocaleString());
    hbSet('hb-trades', (state.total_trades || 0).toLocaleString());
    hbSet('hb-arms', String(hbArms.length));
    hbSet('hb-progress', Number(state.progress_pct || 0).toFixed(1) + '%');
    hbSet('hb-win', hbPct(hbArm.win_rate));
    hbSet('hb-aggwin', hbPct(state.agg_win_rate));
    hbSet('hb-merge', hbPct(hbArm.merge_rate));
    hbSet('hb-aggmerge', hbPct(state.agg_merge_rate));
    hbSet('hb-sharpe', Number(hbArm.sharpe_proxy || 0).toFixed(2));
    const hbBest = [...hbArms].sort((a, b) => (b.sharpe_proxy || 0) - (a.sharpe_proxy || 0))[0];
    if (hbBest) {{
      hbSet('hb-bestsharpe', Number(hbBest.sharpe_proxy || 0).toFixed(2));
      hbSet('hb-bestsharpearm', hbBest.label || hbBest.name);
    }}
    // The queue-gate row cites the arm that ran it. Binding its Sharpe keeps that
    // row honest if the arm stops leading the field.
    const hbQc = hbArms.find(a => a.name === 'queue-clear-5m');
    if (hbQc) hbSet('hb-qcsharpe', Number(hbQc.sharpe_proxy || 0).toFixed(2));
    // 3 decimals: the leaderboard's EV / TRADE column is the value to match.
    hbSet('hb-ev', (hbArm.mean_pnl >= 0 ? '+$' : '-$') + Math.abs(Number(hbArm.mean_pnl) || 0).toFixed(3));
    hbSet('hb-pf', Number(hbArm.profit_factor || 0).toFixed(2));
    hbSet('hb-mcp5', hbUsd(hbArm.mc_p5));
    hbSet('hb-mdd', '$' + Number(hbArm.max_drawdown || 0).toFixed(2));
    const hbRanked = [...hbArms].sort((a, b) => (b.total_pnl || 0) - (a.total_pnl || 0));
    hbSet('hb-rank', '#' + (hbRanked.findIndex(a => a.name === hbArm.name) + 1) + ' of ' + hbRanked.length);
    hbSet('hb-pnl', hbUsd(hbArm.total_pnl));
    if (hbRanked[0]) {{
      hbSet('hb-leader', hbRanked[0].label || hbRanked[0].name);
      hbSet('hb-leaderpnl', hbUsd(hbRanked[0].total_pnl));
    }}
    const hbStops = Number(hbArm.stop_loss_exits_count || 0);
    const hbTrades = Number(hbArm.trades_count || 0);
    hbSet('hb-stops', String(hbStops));
    hbSet('hb-stops2', String(hbStops));
    hbSet('hb-stoprate', hbTrades > 0 ? (100.0 * hbStops / hbTrades).toFixed(1) : '0.0');
  }}

  // Live Readiness Decision Matrix Table
  const dmTbody = document.getElementById('decision-matrix-tbody');
  if (dmTbody && state.arms && state.arms.length) {{
    dmTbody.innerHTML = '';
    const matrixRationale = {{
      'queue-clear-5m': '👑 <strong>Top Efficiency & Tail Risk Champion.</strong> Stale-book filter cuts max DD by 73% ($2.60) with Sharpe 4.67 and positive tail return (MC P5 +$12.90). Core prerequisite for live execution.',
      'balanced-mid': '🌟 <strong>Top Realized PnL Champion.</strong> Shipped dynamic 0.50x (2–4¢) spread tracking generated highest net profit (+$23.39) on 161 trades. Selected as the live pricing baseline.',
      'loose-baseline': '📊 <strong>Control Reference.</strong> Solid volume and $22.84 PnL, but unconstrained queues and missing stop loss leave orphan legs open to directional drift.',
      'static-floor-2c': '⚠️ <strong>Provisional.</strong> High volume ($17.99 PnL) but flat 2¢ floor without volatility tracking suffered -$2.28 spike loss during market blowout.',
      'tight-touch': '⚠️ <strong>Qualified Maker.</strong> Tight 0.25x fills fast on narrow books, but suffers 53% quote starvation on wide spreads. Positive EV but lower reach.',
      "prudent-hybrid": '⚠️ <strong>Provisional.</strong> Strong trade count and $19.24 PnL, but lifted 6¢ clamp suffered tournament-high drawdown ($11.29) during volatility cluster.',
      'low-churn-deadband': '🚫 <strong>Disqualified.</strong> 10¢ deadband is overly sticky; holds stale quotes into adverse drifts, bleeding $10.77 drawdown and -$3.63 MC P5.',
      'hair-trigger-2c': '🚫 <strong>Disqualified.</strong> 2¢ stop triggered 80 stop exits (57.5% exit rate!), liquidating complementary legs on normal tick flutter and gutting win rate to 42.4%.',
      'wide-shield': '🚫 <strong>Disqualified.</strong> Conservative 8¢ stop lets toxic orphan legs bleed deeply (-$0.37 avg loss) with negative tail expectancy (MC P5 -$6.76).',
      'patient-stop-9c': '⛔ <strong>REJECTED (Score < 75).</strong> 9¢ stop leash allowed catastrophic single-trade bleed (-$2.10), tanking Sharpe to 0.52 and MC P5 to -$9.56.'
    }};
    const rankedForMatrix = [...state.arms].sort((a, b) => (b.score || 0) - (a.score || 0) || (b.total_pnl || 0) - (a.total_pnl || 0));
    rankedForMatrix.forEach((arm, idx) => {{
      const pnl = arm.total_pnl || 0;
      const pnlClass = pnl >= 0 ? 'positive' : 'negative';
      const pnlPrefix = pnl >= 0 ? '+$' : '-$';
      const mcp5 = arm.mc_p5 || 0;
      const mcp5Class = mcp5 >= 0 ? 'positive' : 'negative';
      const mcp5Prefix = mcp5 >= 0 ? '+$' : '-$';
      const rationale = matrixRationale[arm.name] || (arm.desc || '');
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>#${{idx+1}}</strong> <span style="color: ${{arm.color}}">●</span> <strong>${{arm.label || arm.name}}</strong>
          <div style="font-size: 10px; color: var(--text-muted); line-height: 1.2; margin-top: 2px;">${{arm.desc || ''}}</div>
        </td>
        <td><span class="badge ${{arm.badge_cls || 'badge-amber'}}">${{arm.readiness || 'EVALUATING'}}</span></td>
        <td class="tabular"><strong>${{arm.score || 0}}</strong> / 100</td>
        <td class="tabular ${{pnlClass}}"><strong>${{pnlPrefix}}${{Math.abs(pnl).toFixed(2)}}</strong></td>
        <td class="tabular">${{(arm.mean_pnl || 0).toFixed(3)}}</td>
        <td class="tabular">${{(arm.sharpe_proxy ?? 0).toFixed(2)}}</td>
        <td class="tabular">${{(arm.profit_factor ?? 0).toFixed(2)}}</td>
        <td class="tabular">${{(arm.win_rate ?? 0).toFixed(1)}}%</td>
        <td class="tabular">${{(arm.merge_rate ?? 0).toFixed(1)}}%</td>
        <td class="tabular negative">$${{(arm.max_drawdown || 0).toFixed(2)}}</td>
        <td class="tabular ${{mcp5Class}}">${{mcp5Prefix}}${{Math.abs(mcp5).toFixed(2)}}</td>
        <td style="max-width: 320px; font-size: 11.5px; line-height: 1.35;">${{rationale}}</td>
      `;
      dmTbody.appendChild(tr);
    }});
  }}

  // Table
  const tbody = document.getElementById('leaderboard-tbody');
  tbody.innerHTML = '';
  state.arms.forEach((arm, idx) => {{
    const tr = document.createElement('tr');
    const pnlClass = arm.total_pnl >= 0 ? 'positive' : 'negative';
    const pnlPrefix = arm.total_pnl >= 0 ? '+$' : '-$';
    const funnel = arm.funnel || {{}};
    tr.innerHTML = `
      <td><strong>#${{idx+1}}</strong> <span style="color: ${{arm.color}}">●</span> <strong>${{arm.label}}</strong>
        <div style="font-size: 10px; color: var(--text-muted); line-height: 1.2; margin-top: 2px; max-width: 260px;">${{arm.desc || ''}}</div>
      </td>
      <td class="tabular">${{(arm.sharpe_proxy ?? 0).toFixed(2)}}</td>
      <td class="tabular">${{(arm.profit_factor ?? 0).toFixed(2)}}</td>
      <td class="tabular">${{(arm.var_95 ?? 0).toFixed(4)}}</td>
      <td class="tabular">${{(arm.mc_p5 ?? 0).toFixed(2)}}</td>
      <td class="tabular">${{funnel.filled || arm.orders_cnt || 0}}</td>
      <td class="tabular">${{arm.stop_loss_exits_count || 0}}</td>
      <td class="tabular">${{arm.merge_rate || 0}}%</td>
      <td class="tabular ${{pnlClass}}"><strong>${{pnlPrefix}}${{Math.abs(arm.total_pnl || 0).toFixed(2)}}</strong></td>
      <td class="tabular">${{(arm.mean_pnl || 0).toFixed(3)}}</td>
      <td class="tabular">${{(arm.std_pnl || 0).toFixed(3)}}</td>
      <td class="tabular negative">$$${{(arm.max_drawdown || 0).toFixed(2)}}</td>
      <td style="max-width: 290px; text-align: left;">
        <div style="font-weight: 600; color: #ffab91; font-size: 11px; font-family: monospace;">🚨 ${{arm.worst_catalyst || 'N/A'}}</div>
        <div style="font-size: 10px; color: var(--text-muted); line-height: 1.2; margin-top: 2px;">${{arm.worst_catalyst_desc || ''}}</div>
        <div style="margin-top: 4px; padding-top: 3px; border-top: 1px dashed rgba(255,255,255,0.1);">
          <span style="font-weight: 600; color: #69f0ae; font-size: 11px; font-family: monospace;">💡 Tweak: ${{arm.hypothesized_tweak || 'N/A'}}</span>
          <div style="font-size: 10px; color: #b0bec5; line-height: 1.2; margin-top: 1px;">${{arm.tweak_rationale || ''}}</div>
        </div>
      </td>
      <td class="tabular"><strong>${{arm.score || 0}}</strong></td>
      <td><span class="badge ${{arm.badge_cls}}">${{arm.readiness}}</span></td>
      <td><a href="/open-dash?port=${{arm.port}}&db=${{encodeURIComponent(arm.db || '')}}" target="_blank" class="btn" style="padding: 3px 8px; font-size: 11px;">Open Dash</a></td>
    `;
    tbody.appendChild(tr);
  }});

  renderCharts();
}}

function renderCharts() {{
  // PnL Line Chart
  const lineCtx = document.getElementById('pnlLineChart');
  if (lineCtx) {{
    if (charts.line) charts.line.destroy();
    const datasets = state.arms.map(arm => ({{
      label: arm.label,
      borderColor: arm.color,
      backgroundColor: arm.color,
      borderWidth: arm.name === 'control' || arm.name === 'deadband-wide' ? 3 : 1.5,
      data: (arm.series || []).map(pt => ({{ x: pt.t, y: pt.cum }})),
      tension: 0.1,
      pointRadius: 0
    }}));
    charts.line = new Chart(lineCtx, {{
      type: 'line',
      data: {{ datasets }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ type: 'linear', title: {{ display: true, text: 'Elapsed Minutes (0 to {initial_data.get('target_hours', 12.0) * 60:g})', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }},
          y: {{ title: {{ display: true, text: 'Cumulative Realized PnL ($)', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{
          legend: {{ labels: {{ color: '#f0f4fc', font: {{ size: 11 }} }} }}
        }}
      }}
    }});
  }}

  // Scatter Chart (Risk-Return Frontier)
  const scatterCtx = document.getElementById('scatterChart');
  if (scatterCtx) {{
    if (charts.scatter) charts.scatter.destroy();
    charts.scatter = new Chart(scatterCtx, {{
      type: 'scatter',
      data: {{
        datasets: state.arms.map(arm => ({{
          label: arm.label,
          backgroundColor: arm.color,
          data: [{{ x: arm.max_drawdown, y: arm.total_pnl }}],
          pointRadius: 8
        }}))
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ title: {{ display: true, text: 'Max Drawdown ($)', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }},
          y: {{ title: {{ display: true, text: 'Total Realized PnL ($)', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ display: false }} }}
      }}
    }});
  }}

  // Radar Chart
  const radarCtx = document.getElementById('radarChart');
  if (radarCtx) {{
    if (charts.radar) charts.radar.destroy();
    const ctrl = state.arms.find(a => /^(control|.*baseline)/.test(a.name)) || state.arms[0];
    // The leaderboard is sorted by PnL, so `arms[0]` is the arm to read against
    // the control. The old pair was pinned to #487's `control` and
    // `deadband-wide` names, which no other run has.
    const rival = state.arms.find(a => a.name !== (ctrl && ctrl.name)) || ctrl;
    const radarTitle = document.getElementById('radar-title');
    if (radarTitle && ctrl && rival) {{
      radarTitle.innerText = 'Multi-Factor Radar: ' + rival.label + ' vs ' + ctrl.label;
    }}
    charts.radar = new Chart(radarCtx, {{
      type: 'radar',
      data: {{
        labels: ['PnL (Norm)', 'Win Rate %', 'Merge Rate %', 'Low Drawdown', 'Profit Factor', 'EV/Trade'],
        datasets: [
          {{
            label: ctrl.label,
            data: [
              Math.max(0, ctrl.total_pnl * 4),
              ctrl.win_rate,
              ctrl.merge_rate,
              Math.max(0, 100 - ctrl.max_drawdown * 8),
              Math.min(100, ctrl.profit_factor * 40),
              Math.max(0, ctrl.mean_pnl * 300)
            ],
            borderColor: ctrl.color,
            backgroundColor: 'rgba(0, 230, 118, 0.2)'
          }},
          {{
            label: rival.label,
            data: [
              Math.max(0, rival.total_pnl * 4),
              rival.win_rate,
              rival.merge_rate,
              Math.max(0, 100 - rival.max_drawdown * 8),
              Math.min(100, rival.profit_factor * 40),
              Math.max(0, rival.mean_pnl * 300)
            ],
            borderColor: rival.color,
            backgroundColor: 'rgba(118, 255, 3, 0.2)'
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{ r: {{ grid: {{ color: '#232c3f' }}, angleLines: {{ color: '#232c3f' }}, pointLabels: {{ color: '#8b9bb4' }} }} }},
        plugins: {{ legend: {{ labels: {{ color: '#f0f4fc' }} }} }}
      }}
    }});
  }}

  // EV Bar Chart
  const evCtx = document.getElementById('evBarChart');
  if (evCtx) {{
    if (charts.ev) charts.ev.destroy();
    charts.ev = new Chart(evCtx, {{
      type: 'bar',
      data: {{
        labels: state.arms.map(a => a.label),
        datasets: [
          {{
            label: 'EV per Trade ($)',
            data: state.arms.map(a => a.mean_pnl),
            backgroundColor: state.arms.map(a => a.mean_pnl >= 0 ? '#00e676' : '#ff5252')
          }},
          {{
            label: 'Std Dev (Risk Dispersion)',
            data: state.arms.map(a => a.std_pnl),
            backgroundColor: '#00b0ff'
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ grid: {{ color: '#1c2436' }} }},
          y: {{ grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#f0f4fc' }} }} }}
      }}
    }});
  }}

  // Profit Factor Chart
  const pfCtx = document.getElementById('pfBarChart');
  if (pfCtx) {{
    if (charts.pf) charts.pf.destroy();
    charts.pf = new Chart(pfCtx, {{
      type: 'bar',
      data: {{
        labels: state.arms.map(a => a.label),
        datasets: [{{
          label: 'Profit Factor',
          data: state.arms.map(a => a.profit_factor),
          backgroundColor: state.arms.map(a => a.profit_factor >= 1.5 ? '#00e676' : (a.profit_factor >= 1.0 ? '#ffd600' : '#ff5252'))
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ grid: {{ color: '#1c2436' }} }},
          y: {{ grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ display: false }} }}
      }}
    }});
  }}

  // Merges vs Exits Stacked Bar Chart
  const mergeCtx = document.getElementById('mergeStackedBarChart');
  if (mergeCtx) {{
    if (charts.merge) charts.merge.destroy();
    charts.merge = new Chart(mergeCtx, {{
      type: 'bar',
      data: {{
        labels: state.arms.map(a => a.label),
        datasets: [
          {{
            label: 'Paired Merges ($1.00 Payout)',
            data: state.arms.map(a => a.merges_count),
            backgroundColor: '#00e676'
          }},
          {{
            label: 'Single-Leg Stopped Exits',
            data: state.arms.map(a => a.exits_count),
            backgroundColor: '#ff5252'
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ stacked: true, grid: {{ color: '#1c2436' }} }},
          y: {{ stacked: true, grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#f0f4fc' }} }} }}
      }}
    }});
  }}

  // Monte Carlo Bar Chart
  const mcCtx = document.getElementById('mcBarChart');
  if (mcCtx) {{
    if (charts.mc) charts.mc.destroy();
    charts.mc = new Chart(mcCtx, {{
      type: 'bar',
      data: {{
        labels: state.arms.map(a => a.label),
        datasets: [
          {{ label: 'Worst Case (5th %ile)', data: state.arms.map(a => a.mc_p5), backgroundColor: '#ff5252' }},
          {{ label: 'Median Expectation (50th %ile)', data: state.arms.map(a => a.mc_p50), backgroundColor: '#ffd600' }},
          {{ label: 'Best Case (95th %ile)', data: state.arms.map(a => a.mc_p95), backgroundColor: '#00e676' }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ grid: {{ color: '#1c2436' }} }},
          y: {{ title: {{ display: true, text: 'Projected 100-Trade PnL ($)', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#f0f4fc' }} }} }}
      }}
    }});
  }}
}}

let lastUpdateTime = new Date().toLocaleTimeString();

function fetchLiveStats() {{
  const statusEl = document.getElementById('refresh-status');
  const tourneyKey = (state && state.tourney_key) ? state.tourney_key : '487';
  fetch('/api/stats?tourney=' + tourneyKey)
    .then(r => {{
      if (!r.ok) {{
        throw new Error('HTTP ' + r.status);
      }}
      return r.json();
    }})
    .then(data => {{
      state = data;
      lastUpdateTime = new Date().toLocaleTimeString();
      if (statusEl) {{
        statusEl.textContent = 'Updated ' + lastUpdateTime;
        statusEl.style.color = 'var(--text-muted)';
      }}
      renderAll();
    }})
    .catch(err => {{
      console.log('Stats refresh offline or error:', err);
      if (statusEl) {{
        statusEl.textContent = '⚠️ Update failed (' + (lastUpdateTime ? 'last ' + lastUpdateTime : 'offline') + ')';
        statusEl.style.color = 'var(--amber)';
      }}
    }});
}}

// Initial render
renderAll();
// Polling interval
setInterval(fetchLiveStats, 5000);
</script>
</body>
</html>
"""


_STATS_CACHE: dict[str, dict[str, Any]] = {}
_STATS_CACHE_TIME: dict[str, float] = {}
_STATS_CACHE_LOCK = threading.Lock()
_STATS_CACHE_TTL_SEC: float = 3.0


def get_cached_stats(tourney_key: str = CURRENT_SETUP_KEY) -> dict[str, Any]:
    global _STATS_CACHE, _STATS_CACHE_TIME
    with _STATS_CACHE_LOCK:
        now = time.time()
        last_time = _STATS_CACHE_TIME.get(tourney_key, 0.0)
        cached = _STATS_CACHE.get(tourney_key)
        if not cached or (now - last_time) >= _STATS_CACHE_TTL_SEC:
            _STATS_CACHE[tourney_key] = collect_all_stats(tourney_key)
            _STATS_CACHE_TIME[tourney_key] = now
        return _STATS_CACHE[tourney_key]



def open_dash_target(query: str) -> tuple[int, Path] | None:
    """Parse `/open-dash`'s query into (arm port, db file), or None when out of bounds.

    A GET that kills a listening process must not be aimable at whatever the
    caller likes -- any page the operator has open can navigate to it. Only an
    arm port is accepted, and only a store inside `data/tournaments`.
    """
    import urllib.parse
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    try:
        port = int(params.get("port", ["0"])[0])
    except ValueError:
        return None
    if not 8801 <= port <= 8810:
        return None
    db_path = params.get("db", [""])[0]
    db_file = (ROOT / db_path if db_path else ROOT).resolve()
    if (ROOT / "data" / "tournaments").resolve() not in db_file.parents:
        return None
    return port, db_file


def _ensure_arm_dashboard(port: int, db_path: str) -> None:
    from scripts.reconcile_tournament_stack import (
        is_port_listening, kill_process_on_port, _detached_kwargs, ROOT
    )
    from core_brain.code_revision import decision_code_mtime
    import urllib.error
    import urllib.request
    import subprocess
    import sys
    import time
    from pathlib import Path

    tree_mtime = decision_code_mtime() or time.time()
    needs_restart = False
    full_db = Path(db_path) if Path(db_path).is_absolute() else (ROOT / db_path)

    if not is_port_listening(port):
        needs_restart = True
    else:
        try:
            url = f"http://127.0.0.1:{port}/api/system/status"
            req = urllib.request.Request(url, headers={"User-Agent": "Cockpit-AutoEnsure"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                dash_started = data.get("services", {}).get("dash", {}).get("started_at", 0)
                if dash_started and dash_started < tree_mtime:
                    needs_restart = True
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ConnectionRefusedError) or "refused" in str(exc.reason).lower():
                needs_restart = True
            else:
                print(f"[cockpit] dash :{port} status timeout; leaving it running ({exc})")
        except Exception as exc:
            print(f"[cockpit] dash :{port} status check failed; leaving it running ({exc})")

    if needs_restart:
        kill_process_on_port(port)
        log_dir = ROOT / "runtime" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        lf = open(log_dir / f"dash_{port}.log", "a", encoding="utf-8")
        cmd = [sys.executable, "-m", "dashboard.server", "--port", str(port), "--db", str(full_db)]
        subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, **_detached_kwargs())
        for _ in range(15):
            if is_port_listening(port):
                break
            time.sleep(0.1)

class CockpitHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # Mute normal HTTP log spam
        pass

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0].rstrip("/")
        query = self.path.split("?")[1] if "?" in self.path else ""

        # Route 1: the current setup page (/ and its aliases) -- the only page.
        # Ten arms are listed with their hypothesis; every metric reads NO_DATA
        # until the 720-minute run writes its record. The purged past-run paths
        # (/492, /484, /608) are absent from PAGE_PATHS, so they fall to the 404.
        page_key = tourney_key_for_path(self.path)
        if page_key is not None:
            stats = get_cached_stats(page_key)
            html = generate_cockpit_html(stats)
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        # Route 2: JSON API (/api/stats?tourney=<key>) -- the page's Refresh
        # button and the menu's service probe (`probe_path` in dashboard/server.py).
        # A retired key serves the page's own tournament instead of resurrecting
        # a purged one.
        elif clean_path == "/open-dash":
            target = open_dash_target(query)
            if target is None:
                self.send_error(400, "open-dash needs port=8801..8810 and a db= inside data/tournaments")
                return
            port, db_file = target
            _ensure_arm_dashboard(port, str(db_file))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}")
            self.end_headers()
        elif clean_path == "/api/stats":
            tourney = CURRENT_SETUP_KEY
            for param in query.split("&"):
                if param.startswith("tourney="):
                    tourney = param.split("=")[1].strip()
            if tourney not in SERVED_TOURNAMENT_KEYS:
                tourney = CURRENT_SETUP_KEY
            stats = get_cached_stats(tourney)
            data = json.dumps(stats).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()


def run_cockpit_server(port: int = 8800) -> None:
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Export the standalone static copy of the one page there is: the current
    # setup. The past-run snapshots (#484, #492, #608) are no longer written --
    # those pages are purged, and their stale files are deleted on the way in.
    stats_current = collect_all_stats(CURRENT_SETUP_KEY)
    static_current = generate_cockpit_html(stats_current)
    out_file_main = reports_dir / "tournament_cockpit.html"
    out_file_main.write_text(static_current, encoding="utf-8")
    print(f"Static Cockpit (#487 current setup) generated at: {out_file_main}")

    # The purged pages leave stale snapshots behind on every machine that ran
    # the old server; drop them so nothing links to a page that no longer exists.
    for stale in (
        "tournament_cockpit_484.html",
        "tournament_cockpit_492.html",
        "tournament_cockpit_487.html",
        "tournament_cockpit_608.html",
    ):
        with contextlib.suppress(OSError):
            (reports_dir / stale).unlink()

    class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = ThreadedServer(("127.0.0.1", port), CockpitHandler)
    print(f"Tournament Cockpit server running at http://127.0.0.1:{port} (page: / and /487 [#487 current setup]; the purged past-run pages /484, /492 and /608 return 404)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Cockpit server...")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tournament Cockpit Server")
    parser.add_argument("--port", type=int, default=8800, help="Port to serve on (default: 8800)")
    args = parser.parse_args()
    run_cockpit_server(args.port)


if __name__ == "__main__":
    main()
