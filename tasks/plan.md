# Plan — Issue #781: Remove mislabeled "Strategy Pricing Band" gate row

Branch: freebuff/ii-plan-issue-781-108c2935-b3e5-4812-92dc-2bc848bbc47c | Issue: #781
(canonical alias `i781/dashboard-strategy-pricing-band-gate-shows-wrong-rang` not
created — the session worktree already provides this isolated branch, so
Stations III–V build, review, and push here.)

## Size & type

- **Tier: Small** — one mislabeled table row plus its verdict logic (`band` entry +
  `_gateBand`) plus its test assertions. Straightforward once read; no new
  abstraction, no architectural decision.
- **Task type: Code (default) + Design/UI** — a gates-table surface change with
  copy removal. Execution tags below; logic verified via the Node harness,
  surface verified in the browser.

## Step 0C — Quick-fix lane

Labels on #781: `Bug`, `Performance`, `UI & Design`, `Ready for Agent`, `Testing`.
No `quick-fix` label → continued to Step 0A. No gate run, no lane offered.

## Step 0A — CodeRabbit plan intake

A CodeRabbit plan comment exists on the issue.

- **Adopted:** skeleton (gate row + quoting-band readout as two work items, merged
  below into deletion-only tasks) and verified seam pointers (`analysis_v2.js`
  band/`_gateBand`, `index.html:643`, `SWEET_SPOT_LOW/HIGH`, `/api/parameters`
  `raw_value` entries, `decisionGatesRows` as follow-up).
- **Rejected:** inventing a "Fill Sweet Spot ≥ 70% → GO" threshold gate with a
  provisional 25-fill minimum, renaming the row instead of removing it, adding a
  "Current quoting configuration" readout with a third `renderAnalysisV2`
  argument, and mirroring sweet-spot bounds into front-end constants. Reasons:
  the 70%/25 numbers exist nowhere in the codebase (product decisions the issue
  explicitly leaves to the operator — default: no new gate), and the readout
  adds UI the issue's defaults decline (Parameters view already shows the live band).
- **Still [UNVERIFIED]:** CodeRabbit's cited `tests/test_statistical_analytics.py:618–635`
  wording prohibition (no hypothesis-wording ban found in `tests/` — only the
  `Active Rule` assertion at `test_analysis_v2_binding.py:333`); its `app.js`
  line numbers (~6125–6135) are approximate and must be re-read live.

## Open questions (from the issue) — resolved from code where possible

1. **Where should the quoting band be displayed?** Resolved: no new UI. The
   Parameters view already renders the live band from `/api/parameters`
   (`bot_quote_price_band_min_cents` / `max_cents` as `raw_value` floats plus the
   `bot_quote_gate_price_band_on` boolean — `dashboard/server.py:4724+`,
   `app.js:3003 cachedParameters`), and `strategy_explainer.html:730` already
   documents `$0.10 ≤ P ≤ $0.90`. Nothing to build.
2. **New sweet-spot fill-rate gate (e.g. ≥70% → GO)?** Genuinely unresolvable from
   code — no threshold or minimum-fill rule exists for this measurement. Per the
   issue's default assumptions: NO new gate. Recorded as rejected scope (see above).
3. **Should `SWEET_SPOT_LOW/HIGH` become configurable?** Resolved: keep fixed
   analytical benchmarks (`statistical_analytics.py:133–134`, `0.15`/`0.85`).
   No backend change in this plan.

## Spec (embedded — Small tier, no SPEC.md ceremony)

- **Goal:** the Statistical Decision Gates table no longer presents the 0.15–0.85
  analytical sweet spot as an enforced "Strategy Pricing Band / Active Rule" gate.
- **Acceptance:** (a) the row is gone from `index.html`; (b) `gateVerdicts` has no
  `band` key and there is no `_gateBand` derived field (no hardcoded `$0.15–$0.85`
  in gates context); (c) `sweet_spot_pct` remains in the analytics payload and the
  ch4 bell chart keeps showing it as a descriptive metric; (d) the quoting band
  ($0.10–$0.90) is confirmed visible with live config values in the Parameters view.
- **Edge cases:** empty payload `{}` must render no band cells at all (today it
  fabricates `0.0% in sweet spot` + `Active Rule` — verified via the harness);
  live payload must lose exactly one `data-bind` hook (`_gateBand`) and one
  `data-band` ref (`gates.band`); hookCount floor (`>= 45`) is unaffected.
- **Out of scope:** `decisionGatesRows` older table in `app.js` (same conflation,
  opposite direction — see the one improvement proposal below, opt-in only);
  bell badge colour; `sweet_spot_low/high` in the payload; strategy_explainer copy.

## Interface contracts

- `probability_bell` payload shape unchanged (`bins`, `samples_count`,
  `sweet_spot_pct`, `mean`, `stdev`, `ci_critical`).
- `renderAnalysisV2(root, kpi)` stays 2-argument; `analysis_v2.js` stays
  fetch-free (existing test guards both the import surface and the no-fetch rule).
- `gateVerdicts` loses the `band` key. Verified consumers of `gates.band` /
  `_gateBand` (grep): `index.html:643`, `analysis_v2.js:210`,
  `test_analysis_v2_binding.py:333,339` — all three are touched atomically in T1+T2,
  so no dangling ref survives. `app.js` does not consume `gates.band`.
- Skill note: `frontend-ui-engineering` (account skill) tags the T1 markup edit;
  no `api-and-interface-design` change (no backend interface moves); the
  `type-design-analyzer` persona step is skipped (no domain-model change) and the
  code-explorer persona was not needed (surfaces traced directly, lines verified above).

## One improvement proposal (evidence-based, classified)

**Align the older `decisionGatesRows` table in `app.js` with the same fix.**
Evidence — `dashboard/static/app.js:5049-5056` pairs the quoting-band threshold
`'$0.10 &le; P &le; $0.90'` with the sweet-spot observed value
(`sweetSpot ... "61.4% in band"`-style text, `state: 'active'`), i.e. the same
two-concepts-in-one-row conflation, mirrored. **Classification: scope expansion**
(extra file + `tests/test_decision_gates.py` updates the issue never names) →
**opt-in only**: enters the plan solely on explicit operator approval, otherwise
stays a recorded follow-up. (Status at planning time: proposed, awaiting operator.)

## Task decomposition

```text
T1 (remove row + verdict) ---> T2 (update tests)
T3 (confirm live band in Parameters view, no code) — independent, either order
```

Checkpoint C1 after T1: harness `render` on live + empty payloads shows no
`_gateBand` / `gates.band`, grep finds no `Strategy Pricing Band` / `Active Rule`
in gates context. Checkpoint C2 after T2+T3: focused suite green, hands-on check
done → ready for `iii-build-plan auto` handoff (build station executes T1–T3).

### T1 — [Frontend/Logic] Remove the gate row and its verdict logic (S) [x]

- Files: `dashboard/static/index.html` (delete the `<tr>` at ~643),
  `dashboard/static/analysis_v2.js` (delete the `band:` entry in `gateVerdicts`
  ~139–144 including the `|| 0` fallback, delete `_gateBand` in `derived()` ~210).
- Do NOT touch: `core_brain/*`, `dashboard/server.py`, ch4 bell chart code
  (`analysis_v2.js:520+`), `decisionGatesRows` in `app.js`, `strategy_explainer.html`.
- Depends on: nothing. Helper skill: `frontend-ui-engineering`.
- Verify: (1) grep for `gates.band|_gateBand|Strategy Pricing Band|Active Rule`
  returns nothing under `dashboard/`; (2) harness `render` + `empty` actions run
  clean with no `_gateBand` key in `painted` and no `band` key in `gates`.

### T2 — [Test] Replace band assertions with absence assertions (S) [x]

- File: `tests/test_analysis_v2_binding.py` (lines ~333–341 and the empty-payload
  test): assert `gates` has no `band` key, `bands` has no `gates.band` ref, the
  Required/row-label strings are gone from `#analysis-v2` markup, and the empty
  render paints no `0.0%`/no badge for the removed row.
- Depends on: T1. Helper skill: none (harness pattern already exists).
- Verify: `python -m pytest -q tests/test_analysis_v2_binding.py` green; each new
  assertion fails without T1 (checked via `git stash` round-trip).

### T3 — [Verify] Confirm the quoting band is live in the Parameters view (XS) [x]

Executed: served a copy of the demo DB on 127.0.0.1:8801 — `/api/parameters`
returns `bot_quote_price_band_min_cents` raw `0.1` → `$0.10`, `max_cents` raw
`0.9` → `$0.90`, gate `True`; served `/` contains zero `Strategy Pricing Band`
occurrences. No code changed.

- No code. Confirm `/api/parameters` serves `bot_quote_price_band_min_cents`,
  `max_cents`, and `bot_quote_gate_price_band_on` with correct `raw_value`s and
  that the Parameters view + strategy explainer show `$0.10–$0.90` + enforcement
  state (already wired via `cachedParameters`).
- Depends on: nothing. Verify: hands-on dashboard check (see How to verify).

## How to verify (hands-on, no pytest / gh / git steps)

1. From the repo root, run `python scripts/build_demo_db.py` only if
   `data\demo_orders.db` is missing (never touch `data\orders.db`); then run
   `python -m dashboard.server --db data\demo_orders.db --host 127.0.0.1 --port 8799`.
2. Open `http://127.0.0.1:8799`, confirm the store is `demo_orders.db`, open
   **Performance & Analytics** (do not press START).
3. In Statistical Decision Gates: the "Strategy Pricing Band" row is gone and no
   badge reads "Active Rule". Failing look: the row or the badge is still there.
4. Open the Parameters view: the Pricing Band bounds read `$0.10` / `$0.90` with
   enforcement on, matching live config. Failing look: bounds showing
   `$0.15–$0.85` or an "off" state while enforcement is on.
5. The probability-bell chart still shows its "In Sweet Spot" concentration as a
   descriptive metric. Failing look: the sweet-spot figure vanished with the row.
