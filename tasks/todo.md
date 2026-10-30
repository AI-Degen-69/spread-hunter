# Build checklist — Issue #872

Branch: `i872/unify-all-currency-settings-to-integer-cents`

## T1 — Whole-cent strictness for the two offset keys `[Backend/Logic]` S
- [ ] `_whole_cents(name, raw, lo, hi)` in `core_brain/config.py`
- [ ] Use it for `BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS` / `_MAX_CENTS`
- [ ] `tests/test_config.py`: autouse env/reset fixture + offset strictness tests
- [ ] Focused run green

## T2 — Honest USD names for the two dollar keys `[Backend/Logic]` L
- [ ] Rename `bot_order_requote_dead_band_cents` → `..._usd` (field, loader, comment, preset, env var)
- [ ] Rename `bot_risk_stop_adverse_drop_cents` → `..._usd` (field, loader, comment, env var)
- [ ] Alias window reader: old names warn once per key per process
- [ ] `DROP_CENTS_MIN/MAX` → `DROP_USD_MIN/MAX` + lifecycle parameter names
- [ ] Engine readers: trader_loop, risk_guardian, single_buy_saver, scripts (4 files)
- [ ] Dashboard cards: `name`, `key`, `env_var` (values untouched)
- [ ] `.env.example` renamed lines with correct unit comments
- [ ] Mechanical test renames across 15 files — expectations unchanged
- [ ] Focused run green

## T3 — Shipped inputs, docs, cockpit copy, inventory `[Docs]` M
- [ ] `configs/tournaments/i799-tournament-test_arms.json` keys + tweak text
- [ ] `docs/agents/strategy.md` + `docs/live-lane-audit.md` unit statements
- [ ] `scripts/tournament_cockpit_server.py` suggestions/labels + its tests
- [ ] `docs/backlog/i872-currency-unit-inventory.md` (remaining 13 `_cents` fields)
- [ ] Focused run green

## T4 — Gates and handoff `[Testing]` S
- [ ] Grep gate: no `*_cents` identifier holds dollars
- [ ] Targeted suites in one run
- [ ] Operator "How to verify" block (hands-on only)
- [ ] PR body + push + PR open
