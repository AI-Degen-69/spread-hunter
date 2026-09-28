# SPEC: Issue #291 - Shadow-03 depth-bar trial on its own feed

## Goal
A third parallel shadow rehearsal (shadow-03) trials a loosened top-3
bid-depth bar ($250 instead of $500) on its own ranker feed and output area,
measuring forward on live books whether depth-rejected markets pay — without
contaminating the shadow-01/02 baselines.

## Acceptance criteria (from issue)
- [ ] shadow-03 loop quotes trial-depth markets from its own feed while the 01/02 feed bytes are unchanged
- [ ] Menu R lists 01/02/03 and port 8803 serves the 03 store
- [ ] New feed-override tests fail without the change and pass with it
- [ ] python -m pytest -q tests/test_trial_readiness.py

## Scope
### In scope
- Ranker `--out-dir` (all RUN-anchored artifacts) + loop forwarding of `--out-dir` / `--trial-depth`
- `_market_specs(path=None)` + shadow `--markets-path` (precedence: injected fn → flag → default)
- Menu `shadow-trial` non-destructive launch, trial manifest (`data/<store>.trial.json`, absolute paths), manifest-driven Menu R resume
- `docs/agents/architecture.md` trial-feeds note

### Out of scope (per issue + CodeRabbit plan)
- Shipped $500 bar stays untouched; no 01/02 baseline behavior change; no live execution
- No volume trial; no ladder work (#49)
- Dashboard graduated-market/KPI panels keep reading the shared feed (port 8803 serves the 03 store)

## Interface contracts
- `filter_markets --out-dir <dir=RUN>`; `filter_loop --out-dir <dir> --trial-depth <usd>` (CLI trial wins over `HUNTER_DEPTH_TRIAL_USD`; absent flags → byte-identical command)
- `_market_specs(max_markets, path=None) -> list[8-field dict]`; `shadow_run --markets-path <file=None>`
- Trial manifest `{trial_depth_usd: 250, ranker_out_dir: <abs>, markets_path: <abs>}` next to the store; mirrored in `runtime/shadow-session-<run-id>.json`; `*.trial.json` never matches `NN_shadow_*.db` discovery

## Edge cases
- Invalid initial feed fails loudly; failed refresh keeps the last list
- No-manifest resume is byte-for-byte the current path (01/02 unaffected)
- Trial screener never registered as the global `filter` entry in `runtime/processes.json`
- While 01/02 live: no fresh start (menu 4), no stop without a run ID

# SPEC: Issue #295 — Orders & Trades markets show Uncategorized instead of venue category

## Goal
Every graduated market shows its real venue category in all four Orders & Trades
views (Active Markets, Open Orders, Positions, Closed Trades) — the venue label
verbatim when Gamma publishes one, a deterministic E-Sports/Politics keyword
fallback when it does not — instead of `Uncategorized` / `--`.

## Acceptance criteria (from issue)
- [ ] `Lol Kcb Wd 2026 09 26`-shaped markets resolve to an E-Sports label and
  `Will Luiz Incio Lula Da Silva Win The 2026 Brazilian Presidential Election`-shaped
  markets resolve to Politics when venue metadata is empty
- [ ] Markets with real venue category/tags show the venue label verbatim (no keyword override)
- [ ] Active Markets, Open Orders, Positions, and Closed Trades all show the same
  non-empty category for the same market (kpi + registry_state unified)
- [ ] New regression test pins both operator examples plus the venue-label-verbatim case
- [ ] Verification: `python -m pytest -q tests/test_orders_trades_table.py`

## Scope
### In scope
- Gamma category/tag extraction in the filter (`gamma_universe` + eligible rows)
- Category persistence in graduated `runtime/markets.json` rows and `market_universe.json` rows
- One shared resolver (`core_brain/market_meta.py`) serving `kpi.py` and `registry_state.py`
- Frontend fallback rendering (Active Markets cell + Market-cell captions elsewhere)
- Tests pinning the two operator examples

### Out of scope (per issue)
- Changing market selection/ranking by category
- Backfilling `data/orders.db` history
- New dashboard filter UI

## Interface contracts
- `core_brain/market_meta.py::resolve_market_meta(cid, closes, quotes) -> dict`
  with keys `condition_id, title, slug, url, category, days_to_resolve, min_size,
  volume_24h, source`; `category` never blank (falls back to `UNCATEGORIZED`)
- `classify_display_category(title, event_title, slug) -> str | None`
  pure function; returns `E-Sports`, `Politics`, or `None`
- `UNCATEGORIZED` lives in `market_meta` and is re-exported from `kpi`
- Category precedence: feed `category` → `series_title` → `market_group` →
  first `tags` label → keyword fallback → `Uncategorized`
- Eligible/universe rows gain a `tags: list[str]` field; no ranking input changes

## Edge cases
- Malformed Gamma shapes (events as dict/string/empty, series missing) never raise;
  fields stay blank and the keyword fallback still applies
- Slug-only markets (left the top-20 feed, absent from universe) classify from
  title/slug alone
- Venue label always wins over keywords (`Crypto` row with election title stays `Crypto`)
- Open Orders with no KPI entry falls back to the `state.pairs` market identity,
  then truncated condition id; category caption shows `Uncategorized`
