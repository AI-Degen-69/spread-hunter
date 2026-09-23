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
