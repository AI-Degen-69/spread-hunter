# Plan — #513 [ADD] Fill-path edge metrics for merge vs rescue expectancy

Branch: i513/add-fill-path-edge-metrics-for-merge-vs-rescue | Issue: #513
Size: Standard | Type: Code + Design/UI | Stack: Python 3.12 + pytest, vanilla JS dashboard
Labels: `enhancement`, `ready-for-agent`
Note: supersedes the #506 tsc-gate plan in this file's history (kept in git). CodeRabbit
plan reply: adopted in part — see intake note.

## CodeRabbit intake note

Read once (rendered copy only; the byte-identical `<!-- <rawResChunk> -->` echo was skipped).

**Adopted**
- Bucket design: merge = `merge`/`shadow_merge`; rescue = the three named single-buy exits;
  `venue_sync` excluded; everything else (including `shadow_settlement`) disclosed as "other".
  Verified against `statistical_validation_run/artifacts.py:74 rescue_stats`, which uses the
  identical denominator (merges + exits) — so the analytics tab and the run report's rescue
  block will finally agree. Its claim that this precedent exists is **correct**.
- No stop-offset constant; break-even from realized close dollars. Verified: the issue's
  `entry_price - 0.04` assumption is false — `core_brain/config.py:1053
  single_buy_max_loss_usd: float = 0.04` is a per-share loss limit that profile presets move
  (0.030–0.065), and `core_brain/single_leg_lifecycle.py:237 hard_stop_bid: float = 0.15` is
  an absolute bid floor.
- `None` + a status string for every missing or zero-division case; strict-JSON-safe output.
- Always-present `fill_path_edge` key in `build()`; `core_brain/kpi.py` untouched. Verified:
  `kpi.py:1417` drops venue-sync closes by run_id sentinel, so the excluded bucket never
  fires through the real kpi path — the guard still protects direct callers and tests.
- Drift-guard test tying the method tuples to their canonical homes.
- Dashboard readout **escalated to the operator as a scope expansion** (Step 5) and approved;
  it is now T3, not an assumption.

**Rejected**
- **New module `core_brain/fill_path_analytics.py`.** `statistical_analytics.py` already
  declares `MERGE_METHODS` (`:35`) and `_finite` (`:59`), and every other section of the
  payload its `build()` returns; its docstring calls `build()` "the whole
  `statistical_analytics` payload", and its siblings `position_returns` (`:69`) and
  `pair_costs` (`:113`) already split closes by merge method. A sibling file would split one
  payload across two files **and** add a fifth copy of `("merge", "shadow_merge")` — already
  at `registry_state.py:41`, `kpi.py:879`, `statistical_analytics.py:35`,
  `statistical_validation_run/artifacts.py:59`. Its circular-import worry disappears entirely
  when there is no new module.
- **`FillPathEdge` frozen dataclass + `to_dict()`.** No function in that module returns a
  dataclass: `position_returns`, `pair_costs`, `probability_bell`, `markout` and `monte_carlo`
  all return plain dicts of numbers/`None`. `run_scorer.to_dict()` is a *different* module's
  pattern. Matching the local module beats matching a remote one, and a plain dict is
  directly strict-JSON-assertable.
- CodeRabbit's "reachable `report.json` through `artifacts.py`" — **verified true, by a
  different mechanism than implied**: `artifacts.py:609` does `report = dict(kpi)` and dumps it
  at `:634`, so the whole kpi payload (including `statistical_analytics`) lands in
  `report.json` with no edit there. `artifacts.py` must NOT be modified.
- Its "keep it simple: 100 lines not 1000" is honoured — 3 tasks, one new test file.

**[UNVERIFIED] remaining:** none. Every seam below was read in the tree at planning time. One
documented repo contradiction is recorded under Contradictions and deliberately not fixed here.

## Spec (embedded — Standard tier)

Goal: the operator can judge whether quoting pays **by itself**, separately from single-buy
bailout losses — because one healthy merge edge can hide a rescue bleed, and vice versa.

Adds to `core_brain/statistical_analytics.py` (the module whose payload the analytics tab and
`report.json` already read):

- `RESCUE_METHODS = ("single_buy_exit", "naked_exit", "ladder_exit")`
- `EXCLUDED_METHODS = ("venue_sync",)`
- `fill_path_edge(closes) -> dict[str, Any]` — pure, no I/O, no writes, no network.
- `break_even_rescue_rate(merge_mean, rescue_mean) -> tuple[Optional[float], str]`
- `build()` gains one key: `"fill_path_edge": fill_path_edge(closes)`.
- Plus a rendered readout on the dashboard's Performance & Analytics tab (T3, approved).

Out of scope (issue text, all verified present and untouched): `shadow_fills.credit_fills`, the
GO gate (`kpi.evaluate_stat_gate`, `artifacts.build_gate_rows`/`resolve_verdict`), the fill
validation ladder (#470/#471/#474), live-vs-simulated comparison, quoting/sizing/stop-trigger
behaviour, parts 2–4 of the telemetry suite (#514–#517), and a Markdown section in
`artifacts.render_report_md()`.

Files that must NOT change: `core_brain/kpi.py`, `statistical_validation_run/artifacts.py`,
`core_brain/order_registry.py`, `core_brain/registry_state.py`, `core_brain/config.py`,
`core_brain/single_leg_lifecycle.py`, `core_brain/single_buy_saver.py`,
`core_brain/shadow_fills.py`, `core_brain/shadow_exec.py`, `core_brain/order_manager.py`,
`core_brain/market_resolution.py`, `core_brain/statistics_report.py`, `dashboard/server.py`,
`scripts/rescue_exit_report.py`, `data/orders.db`.

## Interface contracts (Step 4)

```python
RESCUE_METHODS = ("single_buy_exit", "naked_exit", "ladder_exit")  # mirrors kpi.SINGLE_BUY_EXIT_METHODS, artifacts.EXIT_METHODS
EXCLUDED_METHODS = ("venue_sync",)                                 # mirrors kpi.NON_TRADE_CLOSE_METHODS

def fill_path_edge(closes: Sequence[Any]) -> dict[str, Any]: ...  # shipped: dual-shape input read via _close_field; no registry import
def break_even_rescue_rate(merge_mean: Optional[float],
                           rescue_mean: Optional[float]) -> tuple[Optional[float], str]: ...
```

`fill_path_edge` returns exactly these 15 keys, so the JSON schema is frozen for Stations III–V:

| key | type | meaning |
| --- | --- | --- |
| `merge_count` | int | closes classified as a merge path |
| `merge_pnl_usd` | float | summed realized dollars over those closes |
| `merge_expectancy_usd` | float \| None | `merge_pnl_usd / merge_count`; `None` when count is 0 |
| `rescue_count` | int | closes classified as rescue |
| `rescue_pnl_usd` | float | summed realized dollars over those closes |
| `rescue_expectancy_usd` | float \| None | `rescue_pnl_usd / rescue_count`; `None` when count is 0 |
| `rescue_rate` | float \| None | `rescue_count / (merge_count + rescue_count)`; `None` when the divisor is 0 |
| `net_expectancy_usd` | float \| None | `(merge_pnl_usd + rescue_pnl_usd) / (merge_count + rescue_count)`; `None` when the divisor is 0 |
| `break_even_rescue_rate` | float \| None | the rescue rate at which net expectancy is exactly 0 |
| `break_even_status` | str | `"measured"` \| `"unmeasured"` \| `"merge_not_profitable"` \| `"rescue_not_losing"` |
| `other_count` | int | closes in neither path (e.g. `shadow_settlement`, `sell`) |
| `other_pnl_usd` | float | their summed realized dollars |
| `excluded_count` | int | bookkeeping rows (`venue_sync`) — not trades, not counted anywhere else |
| `skipped_count` | int | rows whose `realized_pnl` is missing, non-numeric, NaN or infinite |
| `units` | str | always `"usd_per_close"` |

Contract rules:
1. One close record is one equal observation, in dollars per close. Two partial-close records
   stay two observations.
2. Bucket order is merge → rescue → excluded → other; the three tuples are disjoint.
3. A row with a non-finite `realized_pnl` is skipped and counted in `skipped_count`; it enters
   no bucket, no sum and no denominator.
4. Only `merge` and `rescue` enter `rescue_rate` and `net_expectancy_usd`. `other`, `excluded`
   and `skipped` never do.
5. Values are computed from raw figures and rounded to 6 decimals at output only.
6. `break_even_rescue_rate` = `merge_mean / (merge_mean - rescue_mean)` when both means exist,
   `merge_mean > 0` and `rescue_mean < 0` (the divisor is then always positive, and the result
   is always < 1.0 — an all-rescue run is therefore always below its own break-even).
   `None`/no closes → `(None, "unmeasured")`; `merge_mean <= 0` → `(None, "merge_not_profitable")`;
   `rescue_mean >= 0` → `(None, "rescue_not_losing")`. Evaluated in that order, so
   `(0.0, 0.0)` reports `merge_not_profitable`.
7. Each record's `method` is read as `str(... or "").strip().lower()` and classified by exact
   match. `strip()` is deliberate: the registry writes literal strings, and a hand-seeded row
   (`scripts/seed_preview_fixture.py`) must not land in `other` over a stray space.
8. `build()`'s existing keys and their values are unchanged; the new key is additive.
9. JS: `renderFillPathEdge(stats)` takes the `statistical_analytics` object, writes
   `#fill-path-edge-readout`, returns `undefined`, and never throws on a missing, empty or
   partial payload. It reads `stats.fill_path_edge` only.
10. `renderFillPathEdge` must be added to the `module.exports` object at
    `dashboard/static/app.js:8645+` — the node harness reaches renderers through it.

## Dependency graph

```
T1 (module + focused tests)  ──►  T2 (payload wiring)
                             └─►  T3 (dashboard readout; consumes T1's schema)
```
T1 is riskiest (classification + math), so it runs first. T2 and T3 are independent of each
other; T2 first because it is the ticket's own acceptance criterion.

## Tasks

- [x] **T1 (M, [Backend/Logic])** — DONE: RED = `ImportError: cannot import name 'EXCLUDED_METHODS'`
  (exit 2, collection error); GREEN = 25 passed; 116 passed across the six sibling suites;
  committed `feat(core_brain): merge-vs-rescue fill-path expectancy metrics (#513)`.
  `core_brain/statistical_analytics.py` + new
  `tests/test_fill_path_analytics.py`. Adds `RESCUE_METHODS`, `EXCLUDED_METHODS`,
  `fill_path_edge()`, `break_even_rescue_rate()` per the contracts above; reuses the existing
  `MERGE_METHODS` (`:35`) and `_finite` (`:59`); extends the module docstring so it no longer
  claims the payload is only the five charts. Docstring and comments record that the buckets
  are an **accounting proxy**, not literal `PAIRED_MERGE`/`RESCUE_STOP` execution labels, and
  that no stop-offset constant exists because realized close dollars already contain the real
  exit fill. Depends on: none. Verify: `python -m pytest -q tests/test_fill_path_analytics.py`
  (RED first: import error before the change).
  Skill: `test-driven-development`.
- [x] **T2 (S, [Backend/Logic])** — DONE: RED = `KeyError: 'fill_path_edge'` (2 failed);
  GREEN = 76 passed across the three suites; real-store check through `kpi.report` on a copy of
  `data/02_shadow_prudent_13-10_03-01.db` returns the key with `break_even_status: "measured"`.
  Committed `feat(core_brain): expose fill-path edge metrics in the analytics payload (#513)`.
  `build()` (`:299`) gains
  `"fill_path_edge": fill_path_edge(closes)`; extend the populated and empty `build()` tests in
  `tests/test_statistical_analytics.py` (currently RED with a `KeyError`). Depends on: T1.
  Verify: `python -m pytest -q tests/test_fill_path_analytics.py tests/test_statistical_analytics.py`.
  Skill: `test-driven-development`.
- [x] **T3 (M, [Design/UI])** — DONE: card renders on the live dashboard against `data/02_shadow_prudent_13-10_03-01.db`
  (copy) with `+$0.10` / `-$1.25` / `50.0%` / `7.4%` / `-$0.57`, survives a sub-view switch, and the
  singular-count fix (`1 close`, not `1 closes`) went RED→GREEN with the tree restored byte-identically
  (md5 `a41c2b5e` before and after). Committed
  `feat(dashboard): show fill-path edge beside the average close (#513)`.
  The readout card. `dashboard/static/index.html`: new
  `<div id="fill-path-edge-readout">` inserted between the Tier-1 row's closing tag (after
  `:476`) and the `<!-- Statistical Power & Distribution Chips -->` comment at `:477`, i.e.
  immediately above `#kpi-grid` at `:478`. It must **not** carry `.stats-chart-card`: that class
  participates in the sub-view filter (`app.js:2883-2885`, `:2909`) and every analytics
  sub-view would blank it. `dashboard/static/app.js`: standalone `renderFillPathEdge(stats)`
  following the `renderSampleSufficiency` shape (`:4271` — `const host =
  document.getElementById(...); if (!host) return;` then `host.innerHTML = ...`), called from
  `renderAnalyticsSurface` (`:4506`) in its own `try/catch` beside the other isolated
  renderers, and added to `module.exports`. Depends on: T1. Verify: browser preview + the node
  harness. Skill: `frontend-ui-engineering`.

Checkpoint after T1: module green, RED demonstrated, real-store numbers reproduced from a
scratch script. Checkpoint after T2: payload key present and empty-safe. Checkpoint after T3:
the card renders on the dashboard with real store data and never prints `NaN`/`undefined`.

## T1 test cases (the ticket's required file)

Dictionary fixture in the style of `_close` in `tests/test_statistical_analytics.py`; asserts on
rendered copy of the payload, with `pytest.approx` where rounding applies.

- **All-merge** (`merge` +0.30, `shadow_merge` +0.10): `merge_expectancy_usd == 0.20`,
  `rescue_count == 0`, `rescue_expectancy_usd is None`, `rescue_rate == 0.0`,
  `net_expectancy_usd == 0.20`, `break_even_status == "unmeasured"`.
- **All-rescue** (`single_buy_exit` −0.40, `ladder_exit` −0.20): `rescue_expectancy_usd == -0.30`,
  `merge_expectancy_usd is None`, `rescue_rate == 1.0`, `net_expectancy_usd == -0.30`,
  `break_even_status == "unmeasured"`.
- **Mixed** (three `merge` +0.30; one `naked_exit` −0.60; one `shadow_settlement` +1.00; one
  `venue_sync` +5.00): `merge_expectancy_usd == 0.30`, `rescue_expectancy_usd == -0.60`,
  `rescue_rate == 0.25`, `net_expectancy_usd == 0.075`, `break_even_rescue_rate == 0.333333`,
  `break_even_status == "measured"`, `other_count == 1`, `other_pnl_usd == 1.0`,
  `excluded_count == 1` — and that net expectancy equals `0.75*0.30 + 0.25*(-0.60)`. The
  `venue_sync` row's +5.00 must move **no** mean, which is the assertion that actually bites.
- **Empty input**: every count 0, every mean/rate/net/threshold `None`,
  `break_even_status == "unmeasured"`.
- **Zero division**: a single `shadow_settlement` close → `rescue_rate is None`,
  `net_expectancy_usd is None`, `other_count == 1`. Plus the three
  `break_even_rescue_rate` refusals: `(0.0, 0.0)` and `(-0.10, -0.50)` both
  `(None, "merge_not_profitable")`; `(0.20, 0.0)` → `(None, "rescue_not_losing")`.
- **Classification**: `" Shadow_Merge "` counts as merge (rule 7); `sell` counts as `other`.
- **Bad numbers**: NaN, `inf`, missing and `"abc"` `realized_pnl` values → `skipped_count == 4`,
  and no mean changes.
- **Partial closes**: two `single_buy_exit` records on one `condition_id` → `rescue_count == 2`.
- **Input types**: a list of `CloseRecord` objects (`core_brain/order_registry.py:759`) returns
  the same dict as the matching dictionary list.
- **Purity**: two calls return equal results, and the input list still equals a deep copy taken
  before the call.
- **Strict JSON**: `json.dumps(payload, allow_nan=False)` succeeds for the empty, mixed and
  bad-number cases.
- **Drift guard**: `set(MERGE_METHODS) == set(registry_state.MERGE_CLOSE_METHODS)`,
  `set(RESCUE_METHODS) == set(artifacts.EXIT_METHODS)` and
  `set(RESCUE_METHODS) == set(kpi.SINGLE_BUY_EXIT_METHODS)`. Importing `kpi` and the
  validation-run layer is safe **in a test**; the module itself must not import either
  (`kpi` imports `statistical_analytics`, so that direction is a real cycle).

## T3 readout contract

Card title `FILL-PATH EDGE — MERGE VS RESCUE` (Title Case tag), values as:

- Merge: `fmtSignedUSD(merge_expectancy_usd)` + ` per close` + close count; `unmeasured` when `None`.
- Rescue: same shape.
- Rescue rate: `(rescue_rate*100).toFixed(1) + '%'` with its divisor shown (`n of m completions`).
- Break-even: `(break_even_rescue_rate*100).toFixed(1) + '%'`, else the status in plain words.
  **Shipped wording, reconciled here (T3):** `merge_not_profitable` → "no rescue rate breaks even
  while merges lose"; `rescue_not_losing` → "no rescue rate breaks even while rescues do not lose";
  `unmeasured` → "unmeasured — no completed closes" (only when *both* paths are empty) or
  "unmeasured — one path has no closes". The planning-time strings above were replaced because
  they answer a precondition ("must be profitable first") where the slot's job is to answer
  *why no number exists*, and because one `unmeasured` string could not tell a run with no
  completions at all from one where a single path never resolved.
- Net expectancy: `fmtSignedUSD(net_expectancy_usd)` + ` per close`.
- Disclosed counts: other (count + `fmtSignedUSD(other_pnl_usd)`), excluded, skipped.
- Caption, sentence case with a full stop (`docs/agents/glossary.md`, "Dashboard copy"):
  "Merge vs rescue closes — accounting view, not maker/taker attribution."
- Negative values go through `signClass` so they read as losses; a `None` never becomes `0`.

`tests/js/analytics_surface_harness.cjs` must register `fill-path-edge-readout` in its stub-id
list, call `app.renderFillPathEdge(kpi.statistical_analytics || {})` inside a `try/catch` that
pushes to `errors` (a direct call, so a throw is reported rather than swallowed by the in-app
guard), and add `fill_path_edge_html` to the JSON it prints.
`tests/test_analytics_surface_mount.py` gains: populated payload → text contains `per close`,
`0.30`, `0.60`, `25.0%`, `33.3%`; empty payload **and** a payload with no `fill_path_edge` key →
text contains `unmeasured` and contains none of `NaN`, `undefined`, `null`; status
`merge_not_profitable` → the plain-words reason, not a number.

## Real data this was designed against (read-only, production stores untouched)

`data/02_shadow_prudent_13-10_03-01.db` — 170 closes, three methods:

| method | n | total | mean |
| --- | --- | --- | --- |
| `shadow_merge` | 89 | +$20.71 | +$0.2327 |
| `single_buy_exit` | 65 | −$22.21 | −$0.3417 |
| `shadow_settlement` | 16 | +$31.59 | +$1.9745 |

Derived: `rescue_rate = 65/154 = 0.4221`; `break_even_rescue_rate = 0.2327/(0.2327+0.3417) =
0.4052`; `net_expectancy_usd = -1.50/154 = -$0.0097/close`. So the rescue rate sits **above** its
break-even and the two-path edge is slightly negative, while the run's actual total is **+$30.09**
— all of it in the 16 `shadow_settlement` closes that the merge-vs-rescue split cannot see.
This is exactly why `other_count`/`other_pnl_usd` must be rendered next to the headline and why
the readout (T3) carries them rather than leaving them in JSON. `venue_sync` count in this store
is 0, so `excluded_count` reads 0 — the operator should expect 0, not treat it as a bug.

## Issue vs repository contradictions (the plan follows the code)

1. **`PAIRED_MERGE` / `RESCUE_STOP` are not recorded values.** `CloseRecord.method` is a free-form
   string. `merge`/`shadow_merge` are merge; the three named exits are rescue. Documented in code.
2. **The issue's default "every other method is rescue" is wrong.** `shadow_settlement`
   (hold-to-resolution), `venue_sync` (wallet reconciliation) and fixture `sell` rows are not
   rescue exits, and `shadow_settlement` is not a taker stop-loss — which is the issue's own
   definition of `RESCUE_STOP`. Repo-wide, `("single_buy_exit","naked_exit","ladder_exit")` is
   the single-buy-exit set in ~12 places plus four named constants
   (`kpi.py:884`, `ladder.py:19`, `artifacts.py:60`, `scripts/ladder_shadow_rehearsal.py:233`);
   `shadow_settlement` appears in none of them.
3. **Documented divergence, not fixed here:** `scripts/rescue_exit_report.py:44` defines
   `RESCUE_METHODS = ("single_buy_exit", "shadow_settlement")` — the only place that pairs a
   settlement row with a rescue. It is out of scope and on the do-not-touch list; recorded so a
   future session resolves it deliberately.
4. **No 4-cent stop offset exists.** `0.04` is `single_buy_max_loss_usd`; the live stop is
   `hard_stop_bid = 0.15` absolute. Hence the break-even comes from realized dollars.
5. **`reason`** (`adverse_drift` / `grace_expired`) is recorded only on single-buy exits and is
   not used: classification is by `method`.
6. **The issue's `merge_pairs.py:41` pointer is not needed** — nothing in this change touches
   `merge_pairs`. Its `statistical_analytics.MERGE_METHODS` at ":35" is **correct** (verified).

## Improvement proposal (Step 5 — scope expansion, operator-approved this session)

One proposal, grounded in verbatim evidence: the ticket wires the metrics into the analytics
*payload* ("Wiring the new metrics into the existing statistical analytics surface where it
reads closes"), which reaches `/api/kpi` and `report.json` but **no screen**. The real store above
shows why that is not enough: net expectancy reads −$0.0097/close next to a +$30.09 run total, and
the +$31.59 of excluded `shadow_settlement` closes is what reconciles them — a number that must be
visible beside the headline, not buried in JSON. Presented as a question before planning; the
operator chose **"Yes — add the readout card"**, so T3 is in scope. Had it been declined, T3 would
have been recorded here as rejected-out-of-scope with this same evidence.

## Interface / dependency notes

- No new external dependencies. No new module. No network, signer or database write anywhere in
  this change.
- `build()` has exactly one production caller: `kpi.py:2369`. `report.json` picks the key up for
  free via `artifacts.py:609 report = dict(kpi)`.
- Circular-import direction to respect: `kpi` → `statistical_analytics`. Never the reverse.

## Open questions resolved from code (no operator questions outstanding)

- **Classification** (issue open question 1): resolved — merge = `merge`/`shadow_merge`;
  rescue = `single_buy_exit`/`naked_exit`/`ladder_exit`; `venue_sync` excluded;
  `shadow_settlement` and everything else disclosed as `other`. See Contradictions 2 and 3.
- **Stop offset** (issue open question 2): resolved — no constant. `hard_stop_bid` is an
  absolute 0.15 floor and `single_buy_max_loss_usd` is dynamic, so the break-even is derived
  from realized close dollars and cannot drift from execution.
