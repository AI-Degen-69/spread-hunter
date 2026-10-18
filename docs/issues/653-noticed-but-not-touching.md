# Noticed-but-not-touching — Issue #653

| ID | Candidate | Discovering station | Evidence | Status | Owner / timeline |
|----|-----------|---------------------|----------|--------|------------------|
| N1 | `scoring/config.py:load()` still reads execution overrides (market pin, title, URL, `HUNTER_MIN_QUOTE_SHARES`, `HUNTER_MAX_SPREAD_CENTS`, `HUNTER_PRICE_TICK`, `HUNTER_WIDE_BOOK_TRIAL`, `HUNTER_PAIRS_RULE`, latency knobs, `HUNTER_BANKROLL`) only via legacy `HUNTER_*` / `SPREAD_HUNTER_*` names rather than canonical `BOT_*` names, whereas `core_brain/config.py:from_env()` routes them through `_env_get("BOT_*", "HUNTER_*")`. | II | `scoring/config.py:157-273` vs `core_brain/config.py:575-780` | open | unassigned — follow-up ticket to unify `BOT_*` overrides in `scoring.config.load()` |
| N2 | `scripts/family_probe.py:927,929` still reads `config.select_min_volume_24h_usd` and `config.select_max_days_to_resolve` under their deprecated legacy names. | II | `scripts/family_probe.py:927-929` | open | unassigned — out of #653 scope (`filter_markets.py` and `filter_loop.py` only) |
| N3 | `dashboard/server.py` parameter cards (`:4312-4412`) still use `getattr(scfg, ...)` fallbacks and legacy `HUNTER_*` env labels. Deferred from this branch per operator Job Guard to avoid merge conflict with Agent 1. | II | `dashboard/server.py:4312-4412` | open | Agent 1 / separate branch |
