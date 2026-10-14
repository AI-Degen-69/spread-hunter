# Plan — #558: [T1] Canonical close-method taxonomy module + guard test (#520)

Branch: spread-hunter_i558-561_taxonomi | Issue: #558

## Why (issue summary)

Seventeen sites re-declare the close-method vocabulary and disagree with each
other, so reports and the dashboard describe the same closes differently.
#520 unifies them in three stages: **T1 (this issue)** lands the canonical
module `core_brain/close_methods.py` — a stdlib-only leaf (`kpi.py:21`
imports `statistical_analytics`, so the reverse import is a cycle; a leaf
dissolves it) — plus the guard test that fails when a method-string literal
tuple reappears outside the module. Consumers move in #559 (core_brain +
statistical_validation_run, plus the three measured defects) and #560
(scripts + app.js).

## Tier: Small (two new files, zero consumer edits, design locked by the issue)

## Type: Code

## CodeRabbit plan intake (2026-10-14)

- **Adopted** from the #558 plan comment: the module design (12 name
  constants, frozen record, 5 buckets, writer table, legacy flags, derived
  lists), the scanner's three rules (Python AST literal / SQL `IN (` string /
  JS bounded array), and the pins (writer-or-legacy, writer AST, leaf,
  settlement-disjoint, bucket consistency).
- **Rejected** — Design Choice 1 option 2 (move every flagged consumer in
  the same change): the operator's locked split puts the moves in #559/#560
  (#520 comment: "3 tasks (T1 canonical module + guard RED, T2 consumers +
  three defects, T3 scripts + front-end)").
- **Rejected** — the `/api/close-methods` route and its JSON-safe
  description function: contradicts the operator-adopted #520 Design Choice
  4 ("Add one taxonomy block in `app.js`, and add a Python parity test for
  it"; "options 2 and 3 need two new delivery paths"). `DASHBOARD_FORCED_EXIT_METHODS`
  stays — #560's parity test reads it.
- **Deferred** — `bucket_for(method)`: no consumer exists at T1; #559 adds
  it when `pnl_by_fill_path` needs it.
- **[UNVERIFIED] → resolved**: writer line numbers in #520's evidence table
  have drifted (kpi's constants moved from :879-884 to :826-831). Writer
  *paths* are stable and are locked in the contract below; Station III
  re-derives each writer's live location before writing its pin.

## Resolved questions (from code, 2026-10-14)

- **Cycle claim VERIFIED**: `kpi.py:21` `from core_brain.statistical_analytics
  import ...` — the leaf module dissolves it.
- **Current constant state** (line numbers drifted from #520's citations):
  `kpi.py:826/:829/:831/:1118` + inline blocks `:1494/:1500`;
  `statistical_analytics.py:92/:101/:106` (the #513 additions);
  `registry_state.py:41`; `ladder.py:19`; `artifacts.py:36-42` already
  imports `MERGE_METHODS` + `SINGLE_BUY_EXIT_METHODS` from kpi (no local
  defs — #520's citation stale); `app.js:8159`
  `EXIT_METHODS = new Set(['single_buy_exit','naked_exit','ladder_exit','aged_out_exit','stop_loss_exit'])`.
- **Legacy names are real readers, not phantoms**: `shadow_merge_note`
  (`tournament_cockpit_server.py:627` SQL, `tournament_hub.py:101`) and
  `settlement` (cockpit SQL).
- **#513–#517 all CLOSED** → no coordination posting needed; their method
  sets already live in `statistical_analytics.py` and are absorbed by #559.
- **Guard RED/GREEN design — LOCKED by operator decision (this session)**:
  the scan asserts **zero** stray lists. It is RED at T1 on the current
  sites, stays red through #559 (scripts + app.js remain), and clears only
  at #560. T1's and #559's PRs fail CI **by design** — Station IV stops
  before merge and hands the red PR to the owner; main is red until #560
  lands. No pin, no allowlist, no xfail. One file-class exclusion:
  `scripts/hooks/` (operator decision — the git-workflow guard's
  `["pr", "merge"]` is git vocabulary, not close methods).

## Interface contract (locked before business logic)

Module `core_brain/close_methods.py` — stdlib only, `from __future__ import
annotations`, no `core_brain` imports:

- Constants: `MERGE`, `SHADOW_MERGE`, `SINGLE_BUY_EXIT`, `LADDER_EXIT`,
  `NAKED_EXIT`, `SHADOW_SETTLEMENT`, `SETTLEMENT`, `VENUE_SYNC`, `SELL`,
  `AGED_OUT_EXIT`, `STOP_LOSS_EXIT`, `SHADOW_MERGE_NOTE`.
- `@dataclass(frozen=True) class CloseMethod: name, bucket, writer, legacy,
  note` + `METHODS: tuple[CloseMethod, ...]`, declared in an order that makes
  the derived lists match today's tuples exactly.
- Buckets: **merge** (`merge`, `shadow_merge`) / **rescue**
  (`single_buy_exit`, `naked_exit`, `ladder_exit`) / **settlement**
  (`shadow_settlement`, `settlement`) / **bookkeeping** (`venue_sync`) /
  **other** (`sell`, `aged_out_exit`, `stop_loss_exit`, `shadow_merge_note` —
  each with a note naming the consumer that treats it specially).
- Writers (dotted paths from #520's evidence table):
  `core_brain.order_manager._submit_and_log` ← `merge`;
  `core_brain.shadow_exec.record_shadow_merges` ← `shadow_merge`;
  `core_brain.single_buy_saver._record_exit_close` (parameter default) ←
  `single_buy_exit`; `core_brain.single_buy_saver._route_pair` ←
  `ladder_exit`; `core_brain.market_resolution.book_shadow_settlement` ←
  `shadow_settlement`; `core_brain.order_manager.venue_sync` ←
  `venue_sync`; `core_brain.order_registry.CloseRecord` (field default) ←
  `sell`.
- Legacy (no writer): `naked_exit`, `settlement`, `aged_out_exit`,
  `stop_loss_exit`, `shadow_merge_note`.
- Derived: `MERGE_METHODS`, `RESCUE_METHODS`, `SETTLEMENT_METHODS`,
  `BOOKKEEPING_METHODS` (from `METHODS`, declared order — must equal today's
  kpi/statistical_analytics tuples), `ALL_METHOD_NAMES`,
  `DASHBOARD_FORCED_EXIT_METHODS` (= rescue + `AGED_OUT_EXIT` +
  `STOP_LOSS_EXIT` — today's app.js `EXIT_METHODS`).
- Module docstring rules: legacy = readers accept the name but no current
  code writes it; never rename a stored name; **a settlement is not a
  rescue** (a settlement is a leg held until the market resolves; a rescue
  is an exit before that point).

Guard test `tests/test_close_method_taxonomy.py` — imports **only**
`core_brain.close_methods`, never the scanned modules:

- Scan roots anchored at `Path(__file__).resolve().parent.parent` (the
  `test_ci_critical_source.py` pattern): all `*.py` recursively under
  `core_brain/`, `statistical_validation_run/`, `scripts/`, plus top-level
  `dashboard/static/*.js`. Skip caches, the canonical module, and
  `scripts/hooks/` (CI tooling — operator decision, N4).
- **Python rule**: flag a tuple/list/set literal whose every element is a
  string constant (or `None`) and at least one is a declared name. Do not
  flag `sell`-only lists (order side), mixed demo rows
  (`("merge", 0.3, ...)`), or single writer strings (`method="merge"`).
- **Query rule**: flag any Python string constant containing `IN (` with a
  quoted declared name. Known limit: `run_scorer.py`'s
  `method = 'single_buy_exit'` equality is NOT flagged — it is still in
  #559's explicit move scope.
- **JS rule**: bounded pattern flagging an array literal of quoted strings
  holding a declared name (covers `new Set([...])`); leave `METHOD_BADGES`
  and comparison chains alone.
- Report hits as relative path, line, trimmed source line. **The scan
  asserts zero hits — RED at T1 by design** (operator decision above).
- **Self-checks**: file list non-empty and includes `kpi.py`,
  `rescue_exit_report.py`, `app.js`; inline samples prove each rule fires
  (Python tuple, `IN (` query, JS `Set`) and each exemption holds (writer
  keyword, mixed row, `("buy","sell")`).
- **Pins** (all must be GREEN at T1): each record has exactly one of writer
  / `legacy=True`; writer check via AST (split the dotted path, find the
  node incl. the `Class.method` form, assert the method string appears as a
  constant in its body / argument default / class field default, and the
  node references `CloseRecord`, `log_close`, or a `method` parameter or
  field); leaf check via `sys.stdlib_module_names` + no `core_brain`
  import; settlement disjointness (`SETTLEMENT_METHODS ∩ RESCUE_METHODS =
  ∅`, both settlement-bucketed); each derived list equals its bucket's
  names; every record name in `ALL_METHOD_NAMES`.

## Dependency graph

- T1 (module): no dependencies — the leaf everything else imports.
- T2 (guard): depends on T1 (needs the declared names to scan against).
- T3 (red proof): depends on T2.

## Tasks

### T1 [x] [Code] Build the leaf module — S

- Target files: `core_brain/close_methods.py` (new).
- What: the contract above — docstring rules, 12 constants, frozen record,
  `METHODS` table in derived order, 5 buckets, writer table, legacy flags,
  notes for the `other` bucket, derived lists. Stdlib only (`dataclasses`
  allowed). Re-derive each writer's live location before T2 writes its pin.
- Skill: test-driven-development, constraint-driven-development.
- Depends on: none. Verify: T2's pins go GREEN against it.

### T2 [x] [Code] Write the guard test — M

- Target files: `tests/test_close_method_taxonomy.py` (new).
- What: the scanner + self-checks + pins above. Import only
  `core_brain.close_methods`.
- Skill: test-driven-development, incremental-implementation.
- Depends on: T1. Verify: `python -m pytest -q
  tests/test_close_method_taxonomy.py` → pins and self-checks GREEN; the
  scan test RED and listing the known sites. An **unexpected** site in the
  failure list is a discovery to record, not to fix (fixing is #559/#560's
  scope).
- Checkpoint: **pins green + scan red with the expected list → T3.**

### T3 [x] [Code] Red proof + closeout — XS

- Target files: none (reads captured output).
- What: run the focused file, capture the scan's failure list, confirm the
  pins are green, and record the exact list here so #559/#560 inherit the
  work list. Expectation: #520's site inventory adjusted for drift —
  kpi (4 constants + 2 inline blocks), statistical_analytics (3),
  registry_state, ladder, market_resolution (3), order_registry,
  paired_shadow (2), shadow_exec, single_buy_saver, rescue_exit_report (2),
  paired_depth_report (2), ladder_shadow_rehearsal, tournament_cockpit SQL,
  tournament_hub, app.js (`artifacts.py` no longer holds literals).
- Skill: verification-before-completion.
- Depends on: T2. Verify: captured output in the closeout.
- Checkpoint: **scan red by design → Station IV. The PR cannot pass CI;
  Station IV stops before merge and hands the red PR to the owner.**

**Captured RED list (2026-10-14 — 17 files, 36 line hits; `scripts/hooks/`
excluded per operator decision) — the #559/#560 work order:**

- **#559 scope** (core_brain/, 25 hits): `kpi.py` 826, 829, 831, 1118, 1494,
  1500, 2195; `ladder.py` 19; `market_resolution.py` 639, 653, 681, 684;
  `order_registry.py` 2427, 2448; `paired_shadow.py` 739, 741;
  `registry_state.py` 41; `run_scorer.py` 185, 198; `shadow_exec.py` 1381,
  1384; `single_buy_saver.py` 550; `statistical_analytics.py` 92, 101, 106.
  (`statistical_validation_run/` holds no literals — artifacts.py imports.)
- **#560 scope** (scripts/ + dashboard, 11 hits): `ladder_shadow_rehearsal.py`
  233; `paired_depth_report.py` 509, 1017; `rescue_exit_report.py` 43, 44;
  `statistical_trade_audit.py` 39; `tournament_cockpit_server.py` 626, 694,
  695; `tournament_hub.py` 101; `dashboard/static/app.js` 8159.
- **Excluded** (N4, resolved): `scripts/hooks/git_workflow_guard.py` 131 —
  CI tooling whose `["pr", "merge"]` check is git vocabulary, never a
  taxonomy consumer; excluded from the scan roots by operator decision.
  Every remaining site is #559/#560 scope, so the guard clears at #560 as
  designed.
- **Discoveries beyond #520's inventory**: `kpi.py:2195` (N1),
  `tournament_cockpit_server.py:626` (N2), `tournament_hub.py:101` (N3) —
  all recorded in `docs/issues/558-noticed-but-not-touching.md`.

## How to verify (operator, hands-on — no pytest)

1. Open the PR diff, read `core_brain/close_methods.py`'s `METHODS` table:
   12 names, each in exactly one bucket, 7 writers naming real functions,
   5 legacy names. This table is the taxonomy the whole #520 chain builds on.
2. Open the PR's failing check output: the guard's failure list should read
   like #520's known drift sites (kpi.py, statistical_analytics.py, the
   scripts, app.js). That list is the work order for #559/#560.
3. `python -c "from core_brain.close_methods import MERGE_METHODS, RESCUE_METHODS; print(MERGE_METHODS, RESCUE_METHODS)"`
   → `('merge', 'shadow_merge') ('single_buy_exit', 'naked_exit', 'ladder_exit')`.

## Checkpoints

1. After T2: pins + self-checks green; scan red listing the known sites.
2. After T3: failure list recorded; ready for Station IV with the
   red-by-design note (owner merges T1 and #559 by override; green at #560).
