# SPEC.md — Issue #838: HYBRID Configuration Blueprint, multi-arm champion spec

Branch: `i838/cockpit-hybrid-blueprint-champion-spec` | Issue: #838

## Goal

The Tournament Cockpit's **HYBRID Configuration Blueprint** tab is a static
5-parameter prototype that advertises `core_brain.config.TOURNAMENT_PRESETS["hybrid"]`.
That preset is `bot_quote_gate_dynamic_offset_on=False` plus a 6¢ deadband — it is
not the configuration the multi-arm tournament evidence points at. An operator who
opens the tab and copies what it prints gets a different bot than the one the tab
claims to describe.

This change replaces the pane's contents with the synthesised champion
specification: six parameters with their grounded sources, a performance forecast
card, a failure-mode card, and copyable commands that actually produce the champion.

## Champion specification (locked)

Nine environment keys, and the pane must show these exact values in all three
places (parameter table, PowerShell block, `.env` block):

| Key | Value |
| --- | --- |
| `BOT_QUOTE_TOURNAMENT_PRESET` | `prudent` |
| `BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON` | `1` |
| `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS` | `6` |
| `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS` | `0.06` |
| `BOT_RISK_STOP_MODE` | `ABSOLUTE_DROP` |
| `BOT_RISK_STOP_ADVERSE_DROP_CENTS` | `0.045` |
| `BOT_ORDER_GATE_QUEUE_CLEAR_ON` | `1` |
| `BOT_ORDER_QUEUE_MAX_CLEAR_MIN` | `3.0` |
| `BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC` | `45.0` |

Six parameter rows, in this order: Requote Deadband, Volatility Pricing Offset,
Single-Buy Loss Stop, Queue-Clear Gate, Aged-Out Rescue Lead, Absolute Pair Cap.
Five columns, unchanged: Parameter | Config Value | CLI / Env Key | Source Champion |
Live Function & Mathematical Rationale.

Six forecast figures: `80% - 84%`, `82% - 86%`, `> 10.5`, `+$0.38 - +$0.46`,
`> 8.0`, `> +$30.00`.

Three failure scenarios: Flash Directional Breakout, Liquidity Vacuum / Book
Desertion, High-Frequency Ping-Pong Jitter.

## Acceptance criteria

1. `tab-hybrid-config` in `scripts/tournament_cockpit_server.py` renders the full
   champion parameter suite with explicit configuration values, CLI/Env keys,
   source arms, and mathematical grounding.
2. A dedicated Performance Forecast card displays projected Win Rate, Merge Ratio,
   Sharpe Proxy, EV/Trade, Profit Factor, and the Monte Carlo floor.
3. A dedicated Failure Modes & Stress Scenarios card documents Flash Breakout,
   Liquidity Vacuum, and Ping-Pong Jitter alongside their built-in mitigations.
4. Ready-to-run PowerShell / CLI commands and `.env` variables match the champion
   parameters exactly — **with one deliberate deviation on the pair cap** (below).
5. `reports/tournament_cockpit.html` shows the matching markup. It is gitignored
   generated output, so this is satisfied by regenerating it from the template,
   not by hand-editing it.
6. `python -m pytest -q tests/test_tournament_cockpit_arms.py tests/test_tournament_cockpit_funnel.py`
   passes.

## Edge cases and honesty rules

- **Pair cap.** The issue specifies `BOT_POS_PRICE_MAX_PAIR_COST_USD=0.990` and
  criterion 4 says the `.env` block must match exactly. Putting that assignment in
  a `.env` blocks the live engine: `resolve_pair_cost_trial` raises `ValueError`
  for any non-blank value outside a rehearsal, and `core_brain/venue.py:24` calls
  `load()` at import time. `$0.990` is already the shipped default
  (`core_brain/config.py:56`). The blueprint therefore shows `$0.990` as the
  shipped default, says to leave the variable unset, and prints no assignment —
  not even a commented one, so an operator cannot paste it back.
- **Stop distance is a trigger, not a loss cap.** A gap or a thin book can make the
  realised single-leg loss larger than the trigger distance. The card says so.
- **Queue-clear is an estimate, not a fill deadline.** It is an upper-bound
  estimate from size ahead ÷ recent traded rate, and no recent flow makes it
  infinite (entry refused).
- **Aged-out rescue lead was not varied by the tournament.** Every arm inherited
  the shipped value; `45.0` is a new, untested operating point.
- **`prudent` alone gives 2–4¢.** The `2-6¢` band needs
  `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=6`, which is what the `prudent-hybrid` arm
  ran and what the pane must say.
- **The forecast figures are projections, not measurements.** `data/tournaments/`
  holds no #799 run record in a fresh checkout, so `289 trades / 210 merges /
  74 stops / Sharpe 9.52 / $0.70 drawdown / -$2.28 / 44.1%` cannot be reproduced
  from the repository. They stay in the pane as **attributed** evidence, clearly
  labelled, never as a locally verified result, and the forecast card is labelled
  as a projection for an untested combination.
- **Shadow runs read the shell environment, not `.env`.** `core_brain.shadow_run`
  reads `os.environ` and never loads a dotenv file, so the `.env` block applies to
  the live trader while the `$env:` lines drive a rehearsal. The pane says so.

## Explicitly out of scope

- Any change under `core_brain/` — no execution, risk, or quoting behaviour moves.
- Every other cockpit pane, the CSS, the tab bar and the `switchTab` script.
- `.env.example`. Its stop-mode names are stale, but fixing them is a separate
  issue with its own blast radius.
- Making `reports/tournament_cockpit.html` tracked, or any `.gitignore` change.
- Populating the leaderboard's worst-catalyst / hypothesised-tweak columns for the
  #799 arms — that is issue #837's job, not this one's.
