# CONSTRAINTS — #492: Re-run the original 10-arm shadow tournament on fixed code

Branch: test/tournament-rerun-fixed-code-492 | Issue: #492

## 1. File boundary (what may change)

Allowed:
- `configs/tournaments/i492_tournament-1_arms.json` — the ten arm definitions (the plan's only data).
- `scripts/shadow_tournament.py` — CLI / plan generation only.
- `tests/test_shadow_tournament.py`
- `docs/runs/2026-10-18-492-tournament-rerun.md`, `docs/issues/492-noticed-but-not-touching.md`.

Forbidden: `core_brain/**`, `dashboard/**`, `scoring/**`, `CONSTRAINTS.md`-pinned siblings, any
other issue's arms file, new dependencies.

## 2. Arm fidelity — the entire point of the rerun

- Exactly ten arms, in this order: `control`, `conservative`, `balanced`, `aggressive`,
  `prudent`, `stickhold`, `queuehug`, `deadband-tight`, `deadband-wide`, `snapstop`.
- Each arm reproduces its #484/#485 first-run value. Locked translations, forced by
  refactors that landed after the first run:
  - `HUNTER_SINGLE_BUY_MAX_LOSS_USD=x` (dollars) → `HUNTER_STOP_LOSS_MODE=ABSOLUTE_DROP`
    + `HUNTER_DROP_CENTS=x`. #573 renamed the knob to cents, #619 replaced it with the
    entry-relative drop trigger. Same threshold, current units.
  - `HUNTER_SINGLE_BUY_GRACE_SEC` → **dropped**. Retired by #619 with no like-for-like
    replacement; `aged_out_rescue_lead_sec` (default 900 s) is a different mechanism and
    must not be substituted.
  - `HUNTER_REWARD_OFFSET=0.02` → **dropped**. The env override was purged; the field
    default (`bot_quote_spread_static_offset_cents = 0.020`) is already 0.02.
  - `control` must not name a `HUNTER_TOURNAMENT_PRESET`; the #484 `control` preset was
    purged and an unknown name raises in `config.from_env`.
- No retired knob may appear in the arms file: `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS`,
  `HUNTER_STOP_LOSS_TRIGGER_PCT`, `HUNTER_SINGLE_BUY_GRACE_SEC`,
  `HUNTER_SINGLE_BUY_MAX_LOSS_USD`, `HUNTER_SINGLE_BUY_MAX_LOSS_PCT`,
  `HUNTER_REWARD_OFFSET`, `HUNTER_REFUSAL_HOLD_POLICY`, `HUNTER_REFUSAL_HOLD_DRIFT`.
- No #487 catalyst tweak value may appear, per arm (0.060 control drop, 0.045 conservative
  drop, 0.25 balanced mult, 0.045 aggressive drop, 0.30 prudent mult, 500 stickhold queue,
  0.08 queuehug hold-below, 0.05 deadband-tight band, 0.25 deadband-wide mult, 45 s grace).

## 3. Naming, isolation, safety

- `--run-number 1` only; no `--stamp`. Slugs `<base>-1`; DBs
  `data/i492_tournament-1_<slug>-1.db`; dashboard ports 8801–8810; plan record
  `runtime/tournaments/i492_tournament-1.json`; results
  `reports/tournaments/i492_tournament-1_results.json`; log
  `runtime/tournaments/i492_tournament-1.log`.
- Ten distinct stores and ten distinct run ids; `data/orders.db` is never named, opened or
  written (`assert_not_production_registry` must pass for every arm).
- `--dry-run` writes nothing: no plan file, no DB, no results file.

## 4. Verification gates

- `python -m pytest -q tests/test_shadow_tournament.py` (plus the run-number naming and hub
  suites) green.
- `python -m scripts.shadow_tournament --issue 492 --minutes 720 --run-number 1 --dry-run`
  exits 0 and prints ten arms, `control-1` … `snapstop-1`, ports 8801–8810.
- The 12-hour run itself is the operator's to launch; this change must not start it.

