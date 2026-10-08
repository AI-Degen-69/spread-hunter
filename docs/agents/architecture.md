# Architecture

## Layout

```
spread-hunter/
  core_brain/             Core trading & execution engine
    quotes.py             THE decision layer: where to rest both legs, and why not to
    risk.py               Sizing ladder, inventory skew, dollar caps, hard blocks
    unhedged_stop_loss.py Per-market markout gate + trader posture
    trader_loop.py        Multi-market rotation: decide -> plan -> submit/cancel
    order_manager.py      CLI: status, quote, poll, merge, redeem, exit, cancel
    single_leg_lifecycle.py Pure per-pair policy for one-sided fills
    single_buy_saver.py   Guarded completion and exit execution for single-leg positions
    shadow_run.py         Isolated full-loop rehearsal with the shared lifecycle policy
    merge_pairs.py        Gasless merge & redemption (ABI, alt-bn128, EIP-712)
    order_registry.py     SQLite order/fill tracking + reconcile (data/orders.db)
    registry_state.py     Read side of the registry; what the dashboard renders
    live_fill_engine.py   Turns venue fills into registry rows
    markout.py            Post-fill mark-to-market used by the stop-loss
    venue.py              Venue client wiring + the MAX_ORDER_USD / MAX_TOTAL_USD caps
    market_feed.py        Reads the market filter's graduated universe (runtime/markets.json)
    markets.py            Venue market lookup
    cycle_stream.py       Append-only telemetry ring + cycle_intent rows
    account.py            Wallet balance & float marks
    kpi.py                Live performance metrics & markouts
    audit.py              3-way reconciliation (Registry vs Venue vs Chain)
    config.py             Live tuning configuration
    runtime_paths.py      Where a runtime state file lives, across the run/ rename
  dashboard/
    server.py             Operations dashboard (:8799), FastAPI + uvicorn
    static/               Dashboard SPA (index.html, app.js, styles.css)
  scripts/
    filter_markets.py     Fetch, filter and score PolyMarket candidate pairs
    filter_loop.py        Continuous filter loop (every 10 minutes)
    global_stop_loss.py   Watchdog: over-cap pairs & repeat single-buy exits
    audit_settlement.py   Settlement & balance verification
    spread-hunter-menu.ps1 Interactive operations menu
  scoring/                Scoring, allocation and selection rules the Market Filter uses
  strategy/               Signal and sizing logic
  data/
    orders.db             THE primary order and fill registry
    NN_shadow_*.db        Per-rehearsal shadow stores
    stats_*_<run-id>.db   Per-run statistics stores (StatisticsStore)
    price_tape.db         Recorded venue tape, for research
  runtime/
    markets.json          The filtered universe the Trader quotes
    processes.json        PIDs of the running stack (filter / query / decide)
    cycle_events.jsonl    Ring buffer of operational events
    *.log                 Every process log the menu redirects
  reports/                Generated statistics reports (gitignored)
  docs/issues/            Issue showcases: <id>-presentation-<slug>.html
  docs/archive/           Superseded human-written docs (readable, not active contract)
  tests/                  Full hermetic unit & integration test suite
```

## Single-leg lifecycle (#413)

`single_leg_lifecycle.py` is the single policy owner for a one-sided fill. Its per-pair
state is persisted in the order registry so a restart does not erase an escalation:

1. **`DUAL_RESTING`** — no filled exposure; both maker legs can rest at their target prices.
2. **`PATIENT_WAIT`** — one leg filled; keep the opposite maker at its target. This state
   does not taker-complete the pair.
3. **`ESCALATED_HEDGE`** — when the held leg's fill average minus its best bid reaches
   twice the dynamic quote offset, the state sticks and the Trader quotes the opposite
   leg at the highest tick-aligned price that fits
   `min($0.99, configured max_pair_cost) - held average`.
4. **`HARD_STOP`** — a held-leg best bid at or below `$0.15` takes precedence over the
   other routes and requests a guarded exit.
5. **`PAIR_LOCKED`** — both legs are balanced and their average prices total no more than
   the configured cap, itself bounded at `$0.99`.

The Trader owns the escalated maker quote; the order-manager poll and shadow loop call the
same lifecycle policy for hard stops and the market-end-aware settlement fallback.
`single_buy_saver.py` is the guarded executor, not a second policy owner. The settlement
fallback is the only automatic taker-completion exception: when due, it tries one
cap-checked completion and uses the guarded exit if completion is refused. A hard-stop
exit retains venue-position, cancellation, fill-reread, residual-size, depth, and
slippage protections. `unhedged_stop_loss.py` remains the separate per-market markout gate;
it does not replace the pair lifecycle.

## Where generated files go

Every writer names an absolute destination anchored to the repo, never a path
relative to the cwd and never the repo root. Two artifacts violated this and
each ended up with two homes, decided by which code path produced it:

| Artifact | Home | Anchored by |
| --- | --- | --- |
| Statistics store | `data/` | `--data-dir`, default `data`; every menu launch passes it |
| Statistics report | `reports/` | `statistics_report.DEFAULT_REPORT_DIR` (`LIVE_ROOT / "reports"`) |
| Runtime state | `runtime/` | `core_brain/runtime_paths.py` |
| Process logs | `runtime/` | the menu's `-RedirectStandard*` arguments |
| Issue showcase | `docs/issues/` | Station VII's `<id>-presentation-<slug>.html` contract |

Two rules keep it that way:

1. **A default destination is a module constant, not a literal at the call
   site.** `Path("reports")` resolves against whatever directory the process
   started in. It looked correct only because the menu passes
   `-WorkingDirectory $ProjectPath`; a scheduled task or a shell in `scripts/`
   would have written the run's only human-readable artifact somewhere nobody
   looks.
2. **Never add a `.gitignore` rule to hide a misplaced file.** `stats_*.db*`
   was added with the comment *"databases dropped in the repo root"* -- the
   spill was hidden rather than the writer fixed, and 436 MB accumulated.
   Fix the writer, then move what already landed.

Ignore patterns for these directories are anchored with a leading `/`. An
unanchored `reports/` matches a directory of that name at ANY depth, and it
silently swallowed `docs/reports/` -- two issue showcases sat there untracked.

## Cleanup decisions (#366)

| File | Decision | Destination / rationale |
| --- | --- | --- |
| `docs/297-`, `304-`, `310-`, `343-presentation-*.html` | Moved | `docs/issues/`, per the showcase contract above |
| `project/` | Deleted | Empty leftover directory; sole tracked file was its own `.gitignore`, nothing imports from it |
| `SHARED_TASK_NOTES.md` | Deleted | Backlog of already-implemented iterations, referenced nowhere |
| `SPEC.md`, `CONSTRAINTS.md` | Archived | `docs/archive/`, history preserved; three historical showcases name them as text labels |
| `metadata.json` | Kept at root | Host-owned: no repo reference found, so deleting could break tooling outside this repo |

Enforced by `tests/test_docs_layout.py` (showcase placement + `project/` absence).

## Data storage retention policy

Local working state (`data/`, `runtime/`, `reports/`) is governed by `core_brain/data_retention.py`:

| Storage family | Pattern / Path | Default policy | Protection rules |
| --- | --- | --- | --- |
| Production registry | `data/orders.db*` | **Retained forever** | Hard-protected; refused by `assert_not_protected_store()` |
| Price tape | `data/price_tape.db*` | **Retained** | Excluded from automated deletion |
| Active protected runs | `*01_shadow*` | **Retained** | Protected by `user_protected_patterns` |
| Rehearsal statistics | `data/stats_*.db*`, `*shadow*` | 14 days | Preserves newest store per family unconditionally |
| Runtime state | `runtime/*`, `run/*` | 14 days | Stale logs/heartbeats pruned |
| Statistics reports | `reports/*_statistics_report*` | 14 days | Pruned beyond 14 days |
| Archive files | `data/archive/*` | 14 days | Pruned beyond 14 days |
| Orphan WAL/SHM | `data/*.db-wal`, `data/*.db-shm` | Immediate cleanup | Pruned when parent `.db` is missing |

Audit and cleanup commands:
```bash
python -m core_brain.data_retention --audit                     # read-only storage audit
python -m core_brain.data_retention --audit --output-inventory docs/data_inventory.md # update inventory
python -m core_brain.data_retention --prune --dry-run           # simulate prune
python -m core_brain.data_retention --prune --no-dry-run        # execute prune
```

## Runtime state across the rename

`runtime/` holds state that is **not** in git: it is on the operator's disk, written by
processes that may still be running when new code starts. So readers resolve state files
through `core_brain/runtime_paths.py`, which prefers the `runtime/` path and falls back to
the pre-rename `run/` path while only that one exists. Writers always write `runtime/`,
which disarms the fallback as soon as the new file appears.

Two of those files are money, not cosmetics:

- **`processes.json`.** `start_bot()` refuses a second stack only when the status reads
  RUNNING, and that status comes from this file. A registry the code cannot find reads as
  STOPPED, and START then launches a second live Trader beside the running one.
- **`markets.json`.** The Trader quotes only what this file lists. A feed the code cannot
  find is an empty universe until the Market Filter regenerates it.

### Trial feeds (shadow-03, #291)

A depth-bar trial ranker publishes to `runtime/trials/<run-id>/` (via
`scripts/filter_markets.py --out-dir`, refreshed by `scripts/filter_loop.py`
with the same flag plus `--trial-depth`) instead of the shared feed above, so
the shadow-01/02 baselines never see trial rows. The trial shadow loop reads
only its own feed through `core_brain/shadow_run.py --markets-path`
(`_market_specs(path=...)` underneath). The feed choice persists in
`data/<store>.trial.json` (absolute paths) and Menu R replays it; stores
without a manifest resume on the shared feed exactly as before. The trial
screener is recorded in its per-run session file only, never as the global
`filter` entry in `processes.json`.

Add a state file: write it through `runtime_file(...)`, read it through
`resolve_runtime_file(...)`, and if you ever rename one, add the old name to
`LEGACY_FILE_NAMES` in the same commit.
