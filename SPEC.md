# SPEC: Issue #240 - End-of-window proximity gate

## Goal
When a market is close to resolution and the loop revisits markets slower than the market can converge, refuse new pair posts (or demand a deeper entry price) — while never blocking a quote that reduces unhedged inventory. Gate off by default so shadow runs measure it first.

## Acceptance criteria (from issue)
- [ ] `evaluate_market_quote` feeds real `t_remaining` from the market object; no fake `1e9` on the live path.
- [ ] Rolling fleet cadence measured from `cycle_intent` timestamps (median gap); `None` when too few rows, and the gate fails open on unknown cadence.
- [ ] Gate fires only when: enabled + minutes-to-resolution ≤ horizon + cadence known and above budget.
- [ ] Light-side balancing quotes always allowed, even when the gate fires.
- [ ] `refuse` skips the side with a clear reason; `deepen` adds the offset through the existing pipeline + clamp.
- [ ] Verdict visible in the `why` reason string, flowing to `cycle_intent.top_skip_reason` and shadow-run log.
- [ ] `python -m pytest -q` passes.

## Scope
### In scope
- `MakerConfig` fields (`core_brain/config.py`): `enforce_endgame_gate=False`, `endgame_horizon_min`, `endgame_max_cadence_sec`, `endgame_action="refuse"`, `endgame_deepen_offset`, `observed_cadence_sec=None` + `HUNTER_*` overrides + validation.
- Cadence query helper (`core_brain/order_registry.py`), read-only, median of consecutive distinct-cycle gaps.
- Real timing in `evaluate_market_quote` (`core_brain/quotes.py`); `window_frac=None` for graduated markets without a stable window start.
- Per-cycle cadence attach in `core_brain/trader_loop.py` (`_market_cfg`, same pattern as `fleet_posture`).
- Gate rule in `_decide_quotes_from_mid` per-side loop near the `strict_paired_inventory` block.
- Tests: S&P 2026-09-18 regression, config validation, cadence helper.

### Out of scope (per issue)
- Changing caller signatures in `trader_loop.py` / `order_manager.py`; changing `fetch_live_market`; touching the screener.
- Enabling the gate live (stays off by default; shadow measurement first).

## Edge cases
- Unknown cadence (`None`) → gate never fires (fail open, same as missing-clock convention).
- Market far from resolution → gate never fires regardless of cadence.
- Flat inventory + gate fires → side skipped (`refuse`) or repriced (`deepen`); paired-loss case from the post-mortem cannot post.
- Unhedged inventory + gate fires → balancing side still posts.
- `deepen` offset still clamped by `max_spread_from_mid`.

# SPEC: Issue #242 - Portfolio equity chart and header cleanup

## Goal
Simplify the Portfolio Overview header and replace the synthetic equity curve with the real closed-trade equity series while preserving existing wallet displays and DOM contracts.

## Acceptance criteria
- [ ] Starting Bankroll appears in the broker title/header area and `broker-starting-cap` remains functional.
- [ ] The venue badge and old Settlement Currency/right-side content are removed without removing the venue wallet row or its IDs.
- [ ] The chart uses top-level `kpi.equity_series` close entries in chronological order, with a starting-capital anchor and a final Current point at the current total value.
- [ ] X-axis labels use real close timestamps; the final label is `Current`.
- [ ] The starting-capital baseline spans the plot width and is dotted.
- [ ] With no close entries, the chart is flat at starting capital; no synthetic rising curve is rendered.
- [ ] Tooltip/crosshair behavior remains available and uses fields present in the real series.
- [ ] Existing portfolio/header tests remain green and new chart coverage fails if the implementation returns to fake data.

## Scope
### In scope
- `dashboard/static/index.html`: broker header and metadata layout.
- `dashboard/static/styles.css`: obsolete venue-badge styling and only layout adjustments required by the new header.
- `dashboard/static/app.js`: chart series construction, labels, baseline, empty state, and tooltip fields.
- `tests/js/portfolio_card_harness.cjs` and portfolio-focused Python/JS test coverage.

### Out of scope
- Backend KPI calculation, `/api/kpi` schema, wallet number computation, trading behavior, chart controls/timeframe semantics, and new dependencies.

## Edge cases
- Ignore `equity_series` entries that are not `type === "close"` when building close steps.
- Preserve chronological order even when input data contains non-close entries.
- Empty close series must still render a valid flat baseline and a usable tooltip/crosshair surface.
- Current total value may differ from the last close and must remain the final plotted value.
