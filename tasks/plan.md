# tasks/plan.md — Issue #231 (reversion sweep grid on /reversion)

## Scope (Standard)
Expose `reversion_sweep.sweep()` on the `/reversion` page through a read-only
API route, with the pre-registration held-out split carried forward.

## Tasks
1. TDD red in `tests/test_reversion_view.py` + `tests/test_dashboard_server.py`:
   - wrapper `reversion_sweep_grid(path, games_from, now_fn)` returns cells with
     fade_c/follow_c/drift_c/n/verdict, pre_registered flag on 5c/300s,
     data-age fields, refusals inherited from `resolve_store_path`.
   - route `/api/reversion/sweep` 200s, refuses orders.db, no-cache headers.
2. Implement wrapper in `core_brain/reversion_view.py`.
3. Implement route in `dashboard/server.py` (mirror /api/reversion/results).
4. `/reversion` page: new sweep-grid section — table jump × horizon with
   fade/follow/drift, n, verdict pill; 5c/300s labelled pre-registered;
   data-age line; held-out cut surfaced with a games_from input defaulting to
   the frozen stamp 1788849238.
5. Measure route response time on the real store; record in PR. Bound/cache if slow.
6. Full suite green.

## CONSTRAINTS
- Read-only everywhere; nothing writes.
- SIGNIFICANCE_T, MIN_GROUP_TRADES, DEFAULT_JUMPS, DEFAULT_HORIZONS unchanged.
- orders.db refused by name at every layer.
- Pre-registered cell labelled, never re-tuned.
