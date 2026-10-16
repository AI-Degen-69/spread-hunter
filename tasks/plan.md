# Plan — #559 [T2] Consumers import the taxonomy; three defects fixed (#520)

Branch: i559/consumers-import-taxonomy | Issue: #559 (sub-issue of #520, blocked by #558 CLOSED)
Worktree: none — fresh branch from `main`; the stale `i520/` worktree/branch were retired (50 commits behind, predated the #558 merge).

## Tier & type

**Standard** — 4 source files plus 2 test files, one architectural decision (settlement is not a rescue), all populations already on the canonical module from #558. No new dependencies.
Task type: **Code** (primary). Helper skills: `test-driven-development`, `incremental-implementation`.

## Discovery (Step 0A intake)

**CodeRabbit plan comment:** mined, not re-read. Adopted — the three defect sources, the `settled`/`other` bucket design, the merge-count fix, the rescue/settlement split, and the M/C regression list. Rejected — the `bucket_for` routing and the app.js card work belong to #560/T3, not this issue; this plan routes `pnl_by_fill_path` through the canonical families directly (smaller diff, same result). All `[UNVERIFIED]` items resolved below against live code.

**#513–#517 coordination:** all five are CLOSED and their content is on `main` (git log shows #517 + #558 commits). `statistical_analytics.py` carries no settlement handling (`grep shadow_settlement` → empty), so there is no merged method set to fold in and no open issue to post canonical names on. T0 re-confirms with one grep; no cross-posting.

**Verified against live code (all symbols confirmed at these sites):**
- Canonical module landed (#558): `core_brain/close_methods.py` exports `MERGE_METHODS`, `RESCUE_METHODS`, `SETTLEMENT_METHODS`, `BOOKKEEPING_METHODS`, `TAKER_RESOLVED_METHODS`. `kpi.py:21-26` already imports them.
- Defect 1 — `kpi.py:918-963` `pnl_by_fill_path` returns exactly 3 keys; settlements and unknown methods fall through both branches and land nowhere, so `total` ≠ run realized PnL.
- Defect 2 — `kpi.py:2198` counts `merge_closes` with `== "merge"`, ignoring `shadow_merge` (the only merge a rehearsal writes). `_single_exits` (`:2199`) already counts the full exit triple — leave it alone.
- Defect 3 — `artifacts.py:74-97` `rescue_stats` has no `settlements` key (settlements sink into `other_methods`); `scripts/rescue_exit_report.py:50-52` defines a local `RESCUE_METHODS = (SINGLE_BUY_EXIT, SHADOW_SETTLEMENT)`, calling a settlement a rescue. The two disagree.
- Frozen inventory/attribution/cost-basis populations (RESCUE_METHODS call sites — read, do not touch): `market_resolution.py:654,685`, `paired_shadow.py:742`, `order_registry.py:2427`, `shadow_exec.py:1652`, `single_buy_saver.py:550`, `statistical_analytics.py:304,732`, `kpi.py:1143` (TAKER_RESOLVED recost), `kpi.py:1504` (inventory filter).
- Existing-test pin: `tests/test_pnl_by_fill_path.py:61-73` asserts the exact 3-key empty shape — the single permitted assertion edit.
- Guard base exists: `tests/test_close_method_taxonomy.py` (T1 T-cases landed).

**Resolved open questions:** #520 carries `needs-answers`, but every question that touches T2 resolves from code above (writer table, measured 92/16/65 values, contradiction list in the parent). Nothing left that only the operator can answer — no batch asked.

**Persona skips (recorded):** `code-explorer` skipped — the four defect sites and seven frozen sites were traced by direct grep/read above; `type-design-analyzer` skipped — no new interfaces (two dict keys added to existing shapes, locked below).

## Spec (inline — no SPEC.md)

**Goal:** fix the three measured defects at their source so every non-`venue_sync` close lands in exactly one fill path, rehearsal merges count, and settled closes are never called rescues — while inventory, attribution, and cost-basis populations stay bit-identical.

**Acceptance:**
- `pnl_by_fill_path` returns all five keys (`maker_merged`, `taker_completed`, `single_buy_exit`, `settled`, `other`) in `by_path` and `pct`; `total` equals run realized PnL on the reference store.
- `merge_closes` counts `MERGE_METHODS` membership; `_single_exits` unchanged.
- `rescue_stats` returns a `settlements` count; `rescue_exit_report` splits rescue exits vs settlements per method with identical row selection, ordering, and loss arithmetic.
- `python -m pytest -q tests/test_pnl_by_fill_path.py tests/test_kpi.py tests/test_stat_validation_artifacts.py tests/test_fill_path_analytics.py tests/test_close_method_taxonomy.py` → pass (agent-run, background only — never an operator step).

**Out of scope:** `dashboard/static/app.js` (belongs to #560/T3); `build_gate_rows` / `resolve_verdict`; `close_methods.py` (read-only); anything in `data/`; new dependencies.

## Interface contracts (locked)

- `pnl_by_fill_path` shape: `by_path`/`pct` gain exactly `settled` (from `SETTLEMENT_METHODS`) and `other` (everything else trade-like, incl. legacy `sell`). Non-trades still skipped via `NON_TRADE_CLOSE_METHODS`. `pct = bucket / total`, near-zero → all-`None` rule kept. Docstring: shares are signed, can fall outside [0,1].
- `rescue_stats` shape: gains `settlements: int` (from `SETTLEMENT_METHODS`); `merges`, `exits`, both rates, and the merges-plus-exits denominator unchanged. `other_methods` excludes settlements (they have their own key now).
- `rescue_exit_report`: SQL row selection predicate may widen from the local tuple to `(SINGLE_BUY_EXIT,) + SETTLEMENT_METHODS` sourced from canonical names; per-method count/loss split added for rescue exits vs settlements. "AGED OUT" / "AGED-OUT RESCUE" labels kept.

## Tasks

Dependency graph: T0 → (T1, T2, T3) → T4. T1/T2/T3 are independent of each other.

### [x] T1 [S] [Code] — Defect 1: total-preserving fill paths
- `core_brain/kpi.py:918-963`: route through canonical families — skip `NON_TRADE_CLOSE_METHODS`, keep the maker/taker merge split, exits → `single_buy_exit`, `SETTLEMENT_METHODS` → `settled`, everything else → `other`. Always return all five keys in `by_path` and `pct`. Fix the docstring (signed shares).
- The single permitted assertion edit: `tests/test_pnl_by_fill_path.py:61-73` empty shape → five keys. Add R-cases (settlement lands in `settled`, unknown/`sell` lands in `other`, total reconciles).
- Frozen: do not touch `kpi.py:1143`, `:1504`, or the execution funnel (`:980-981`).
- Depends on: T0
- Verify: `python -m pytest -q tests/test_pnl_by_fill_path.py tests/test_fill_path_analytics.py`

**Checkpoint 1** — after T1+T2: fill-path totals reconcile and merge counts read correctly on a temp store.

### [x] T2 [XS] [Code] — Defect 2: merge count counts `shadow_merge`
- `core_brain/kpi.py:2198`: `_merge_closes` by membership in `MERGE_METHODS`. Leave `:2199` exit counting unchanged. Replace the stale `:2196-2197` comment with one factual line (see proposal below) — no warning code exists, so none is added.
- Add M1 regression on a temp store (rehearsal-shaped closes: `shadow_merge` counted, `single_buy_exit` not).
- Depends on: T0
- Verify: `python -m pytest -q tests/test_kpi.py tests/test_close_method_taxonomy.py`

### [x] T3 [S] [Code] — Defect 3: settlement is not a rescue
- `statistical_validation_run/artifacts.py:74-97`: add `settlements` count from `SETTLEMENT_METHODS`; exclude settlements from `other_methods`. Keep `merges`, `exits`, both rates, denominator. Do not touch `build_gate_rows` / `resolve_verdict`.
- `scripts/rescue_exit_report.py`: source the selection from canonical names (`SINGLE_BUY_EXIT` + `SETTLEMENT_METHODS`); keep row selection, ordering, loss-share arithmetic, and both AGED OUT labels. First grep tests for pinned prose (`rescue closes`, `aged-out settlements`) and change settlement-as-rescue wording only where no test pins it; add the per-method count/loss split.
- Add C-cases (settlement counted once, under `settlements`, never under `exits`/`rescues`).
- Depends on: T0
- Verify: `python -m pytest -q tests/test_stat_validation_artifacts.py`

**Checkpoint 2** — after T3: rescue block and forensics report agree on the reference store copy.

### [x] T4 [S] [Code] — Guard test extension + acceptance sweep
- Extend `tests/test_close_method_taxonomy.py` with the M/C regression cases from T1–T3 (merge membership, settled/other buckets, settlements-not-rescues). No new scan scope — the T1 drift scan already covers it.
- Run the issue's acceptance command (all four suites + taxonomy). Every accounting suite passes with no changed assertions except the one deliberate T1 edit.
- Record the measured "1 against 65" exit-count gap disposition (left unchanged per plan) as a follow-up note at the bottom of this file, not as code.
- Depends on: T1, T2, T3
- Verify: the acceptance command from the Spec section above.

## Improvement proposal (Step 5)

**Adopt-by-default (stale-comment correction).** Evidence, verbatim from live code (`core_brain/kpi.py:2196-2197`): *"Exit-shape counts (merges vs one-sided dumps) feed the card's details line, which owns the "all exits were naked" warning."* No warning code exists on the card (the details line never reads `merge_closes`/`single_buy_exits`), so repointing the comment at a warning would document fiction. Proposal: replace with one factual line — *"Exit-shape counts for the card's details line."* — folded into T2. A scope expansion (building the warning) was considered and rejected — it is new behavior this issue never asks for.

## Dispositions recorded at build close (T4)

- **The "1 against 65" exit-count gap: left unchanged, recorded, not silenced.** The pair names one count at two scopes. #520's own measurements read `single_buy_exits: 1` on the reference store's newest run (2 closes) and 65 `single_buy_exit` rows across `data/`. The card counts the run-filtered closes through the canonical three-exit set (`kpi.py:2199`); the 65 is the store-wide population. Two scopes, no defect in either — so T2 left `_single_exits` exactly as it was and this issue changed neither number. The reference store (`data/02_shadow_prudent_13-10_03-01.db`) is absent from this checkout (`data/` holds only `demo_orders.db` and `orders.db`), so the pair is recorded from the parent's figures rather than re-measured here. Re-measure when a store carrying settlements is available.
- **`KPI_PAYLOAD_VERSION` not bumped — candidate, not touched.** `pnl_by_fill_path` now ships two extra keys (`settled`, `other`), and `core_brain/kpi.py` documents the hand-bumped envelope version as how a stale backend is told apart from a genuinely unmeasured field. The consumer of those keys is the fill-path card in `dashboard/static/app.js`, which #560/T3 owns, so the bump belongs with that work rather than with the producers here.
- **`tests/test_541_golden_payload.json` follows T1's payload delta (a consumer Station II's inventory missed).** The golden freezes `portfolio`, `trade_analytics` and `statistical_analytics`; `trade_analytics` embeds `pnl_by_fill_path`, so T1's two new buckets reach it. Reverted-T1 evidence: `tests/test_541_stat_goldens.py` → `2 passed` with `core_brain/kpi.py` at `origin/main`, `1 failed` with T1 in place. Regenerated through the file's own `REGEN_541_GOLDEN=1` path: the diff is **four inserted keys, `by_path.settled`, `by_path.other`, `pct.settled`, `pct.other`, all `0.0` — no existing value moved**. The file's docstring now records this as the second sanctioned regeneration so a future value drift cannot pass as a refresh. This file was not in the plan, the parent's regression list, or the acceptance command — the sweep that found it was the full local suite.
- **`PORT=0` in the ambient shell fails one unrelated dashboard test — environmental, not this change.** This session's shell exports `PORT=0`, and `dashboard/server.py::resolve_port` rejects a `PORT` outside 1–65535 by design, so `tests/test_run_scorer.py::test_dashboard_reliability_endpoint` fails with `ValueError: PORT='0' is outside 1-65535`. Evidence it is not this build: the same test fails identically with the changed modules reverted to the pre-fix revisions (`df0f3e5`, no build in the tree) and passes with `PORT` unset (`11 passed` in that file); the rest of the adjacent sweep is `367 passed`, exit 0. No code change was made for it, and it is not a suppression — the test itself is untouched.
