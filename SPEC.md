# SPEC — #422: Every shadow rehearsal starts at a fixed $100 bankroll

Scope note: this file covers issue #422 only
(branch `i422/shadowrun-every-rehearsal-starts-at-a-fixed-bankro`).
It supersedes the #419 spec (done work). Deleted or superseded when the
next Standard/Large issue writes its own.

## Problem (operator words)

The same rehearsal command rehearses at a different bankroll one day to the
next. An ordinary shadow run reads the LIVE wallet balance through the funder
address and only falls back to the config bankroll when that read fails — so
every risk cap (order 25%, naked 6%, ceiling 90%) silently moves with the
wallet, and two runs can never be compared.

## Goals

1. Every shadow rehearsal starts from a fixed $100 bankroll by default —
   `bankroll_usd = 100.0` in `core_brain/config.py:41` stays the source of truth.
2. An explicit override (`--starting-bankroll-usd` or `SPREAD_HUNTER_BANKROLL`)
   still sets a different bankroll for experiments.
3. Paired arms use the same mechanism, not a second copy of it
   (unify, don't fork; old `--paired-starting-bankroll-usd` keeps working).

## Acceptance criteria (from the issue)

- [x] A shadow run with a live balance far from $100 still rehearses caps
      computed from $100 (order cap $25, naked $6, ceiling $90)
- [x] An explicit override flag still sets a different bankroll when passed
- [x] Focused shadow-run tests pass unchanged in meaning
      (update only pins that assumed the live read)

## Edge cases

- Precedence, ordinary run: explicit flag → env (`SPREAD_HUNTER_BANKROLL` /
  `HUNTER_BANKROLL` via `config.load()`) → config default $100.
- Precedence, paired run: explicit flag → $100 default (today's preregistered
  behavior; paired arms ignore the env, unchanged).
- `starting_bankroll_usd` of NaN or infinity raises `ValueError` matching
  "bankroll" (`math.isfinite` check; today's `<= 0` misses them).
- `funder` keyword stays for compatibility (`statistical_validation_run/run.py`
  passes it) but no longer sets the shadow bankroll.
- Callers that pass `cfg` keep using that `cfg` as-is (ladder scripts,
  statistical validation).
- A reused shadow store with old account marks can still move the sizing base
  after startup — out of scope, runs already require a fresh store.

## Explicit out of scope

- Live trading and the cap math itself (`derive_dynamic_caps`,
  `trader_loop._fleet_state`: untouched).
- `core_brain/config.py` bankroll default, env parsing, and bounds: untouched.
- `statistical_validation_run/run.py` (reads its own live balance at
  `run.py:364-368`, passes its own `cfg`): separate ticket, untouched.
- `scripts/shadow_tournament.py` (launches the CLI as a subprocess):
  no edit needed; old paired flag name keeps working as an alias.
- `docs/runs/2026-09-28-paired-depth-pilot.md`: its
  `--paired-starting-bankroll-usd 100` commands keep working, untouched.
