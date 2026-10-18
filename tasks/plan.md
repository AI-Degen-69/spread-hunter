# Plan — Issue #652 `[REFACTOR] Rename the picker's legacy filter bars to the canonical config names`

Branch: `i652/rename-picker-filter-bars` | Issue: [#652](https://github.com/AI-Degen-69/spread-hunter/issues/652)

- Size tier: **Standard** (2 config modules + 3 test files, internal module change, one architectural decision)
- Task type: **Code** — refactor with a test/guard component. Execution tag per task: `[Backend/Logic]`
- Language/runtime: Python 3, test runner `pytest` (`pytest.ini`, `testpaths = tests`)
- Base: **`main` at `01157a3`** (see "Sequencing" below — the issue's default is stale)
- Verified by: agent-run focused `pytest` selections (Station III/IV). Full suite stays in GitHub CI.

---

## 1. Spec

### Goal

`scoring/config.py` and `core_brain/config.py` name every market-picker filter bar the same way.
The eight legacy `select_*` / `enforce_*` declarations leave the scoring class body and are inherited
from `core_brain.config.MakerConfig`; the legacy spellings keep resolving for the dashboard, the
ranker scripts and the existing tests. **No numeric value moves.**

### The eight bars (verified against live code, HEAD `01157a3`)

| old name (readable after, via alias) | canonical core field | value (unchanged) | `scoring.load()` env var |
| --- | --- | --- | --- |
| `select_min_movement_usd` | `filter_live_vel_min_usd` | 200.0 | `HUNTER_MIN_MOVEMENT_USD` |
| `select_movement_window_sec` | `filter_live_vel_lookback_sec` | 1800.0 | `HUNTER_MOVEMENT_WINDOW_SEC` |
| `select_velocity_gate_enabled` | `filter_live_gate_velocity_on` | True | `HUNTER_VELOCITY_GATE` |
| `select_min_activity_ratio` | `filter_live_activity_min_ratio` | 0.0 | `HUNTER_MIN_ACTIVITY_RATIO` |
| `select_min_range_cents` | `filter_live_move_min_range_cents` | 2.0 | `HUNTER_MIN_RANGE_CENTS` |
| `select_volatility_window_sec` | `filter_live_move_lookback_sec` | 7200.0 | `HUNTER_VOLATILITY_WINDOW_SEC` |
| `select_max_queue_minutes` | `filter_std_queue_max_clear_min` | 15.0 | — (none; do not add one) |
| `enforce_max_queue_minutes` | `filter_std_gate_queue_max_on` | False | — (none; do not add one) |

Plus one **additive** name: `filter_common_spread_max_cents`, a read-only alias for the derived cap
`select_max_book_spread` (0.0205). `select_max_book_spread` keeps working; nothing is renamed away.

> Naming trap: the station brief said `select_velocity_gate`. **That name does not exist anywhere in the
> repo** (`grep -rnw select_velocity_gate` → 0 hits). The shipped field is `select_velocity_gate_enabled`
> (`scoring/config.py:119`, pinned at `tests/test_config_single_source.py:59`,
> `tests/test_config_divergence_guard.py:31`). Plan uses the real name.

### Acceptance criteria → the test that proves each

| # | Criterion (from the issue) | Proven by |
| --- | --- | --- |
| A1 | Each bar exists once, on `core_brain.config.MakerConfig`; the scoring class body declares only the five `PICKER_PROFILE` fields plus `db_path` | `tests/test_config_divergence_guard.py::test_the_class_body_declares_only_the_pickers_own_fields` (allow-list narrowed to `PROFILE_NAMES`) + new `test_the_class_body_declares_no_other_property_or_method` |
| A2 | Reading each old name off a `scoring.config.load()` config returns the canonical value **and emits the deprecation warning** | new parametrised test in `tests/test_config_single_source.py`, `pytest.warns(DeprecationWarning)` |
| A3 | No default moved: 200.0 / 1800.0 / True / 0.0 / 2.0 / 7200.0 / 15.0 / False, and every pinned `SCORING_DEFAULTS` value still holds | new pristine-load test + existing `test_scoring_defaults_unchanged` / `test_every_pinned_field_still_exists`, **untouched** |
| A4 | Each of the six env overrides reaches the canonical field end to end | new parametrised test (set one var, clear the other five, `scoring.config.load()`, assert the canonical field) |
| A5 | The repointed guard fails against the pre-change class body and passes after | written RED first (T1), run before and after T2+T3 |
| A6 | The three focused test files pass | agent-run `pytest` selection |
| A7 | The six reader/regression files still pass | agent-run `pytest` selection |
| A8 | Hands-on: dashboard picker cards show configured values, not the hard-coded fallbacks | operator walkthrough (§7) |

### Edge cases that must hold

- `dataclasses.replace(cfg, select_max_queue_minutes=5.0)` and
  `replace(cfg, enforce_max_queue_minutes=True, select_max_queue_minutes=15.0)` — the two live call sites
  (`tests/test_queue_clear_gate.py:664`, `tests/test_maker_queue_bar.py:299-301`) — keep working unchanged.
- `MakerConfig(**{"select_min_movement_usd": 777.0})` (legacy keyword) must reach the canonical field.
- `filter_common_spread_max_cents` must be **refused** by the constructor, absent from `dataclasses.fields()`,
  and unassignable on an instance.
- `scoring.config.MakerConfig` field set must equal core's exactly — including the ~110 synthetic
  legacy fields — with no name left over on either side.

### Explicitly out of scope

- Any numeric value, any parsing/clamping/finite-check rule, any fallback in `load()`. A value change is
  its own ticket (#572 / #573 precedent, `tests/test_config_single_source.py:9-12`).
- The `bot_*` execution side — already canonical.
- `dashboard/server.py`, `dashboard/static/strategy_explainer.html`, `scripts/filter_markets.py`:
  **zero edits**. The aliases keep their reads correct.
- `filter_std_spread_max_book` and the `#632` `_frac`/`_fraction` properties: leave alone.
- The constructor wrapper's default-sensitive precedence rule: shared behaviour from the canonical pass,
  applies to every entry in the table. Rewriting it is a separate ticket (see N2).
- The class name (`ScoringConfig` is only a test-local alias at `tests/test_market_selection_bars.py:17`).
- `docs/ideas/variable-names-refactor.md` — stale, not edited.

---

## 2. Intake — CodeRabbit plan triage (read once, costed here so Stations III–V never re-read it)

**Adopted** (all spot-checked against live code): the alias-table location; migrating the six `load()`
keys to canonical names; `filter_common_spread_max_cents` as an additive read-only property forwarding
`select_max_book_spread`; adding that name to the read-only refusal set; the affirmative guard rewrite;
the pinned old→canonical map; the six end-to-end env tests; the derived-cap pins; the hands-on
verification block.

**Rejected / corrected:**

1. **Task 1 "Start from the correct base branch" — WRONG, and the highest-value correction in this plan.**
   It says "The canonical schema exists only on PR `#651`. Branch from it, not from `main`."
   On this checkout `214d196 [ADD] Group market and bot settings on the parameters page (#651)` **is an
   ancestor of `HEAD`** (`git merge-base --is-ancestor 214d196 HEAD` → true), `_LEGACY_TO_CANONICAL` lives
   at `core_brain/config.py:35`, and the eight canonical fields at `core_brain/config.py:249-266`.
   There is no stacked branch: **base is `main`**, and a branch off `#651` would be wrong (it would
   re-import already-merged work). Its "Phase 2 first, then Phase 1" ordering and the pinned `#651`
   PR-base note in the PR body are dropped with it.
2. **Its "82 fields: 74 shared + 8 picker-only" arithmetic** is a pre-change statement; keep the pin file
   untouched and let the tests speak (see §4).
3. **`select_velocity_gate_enabled` vs the brief's `select_velocity_gate`** — CodeRabbit used the real name;
   the brief did not. Plan uses CodeRabbit's.
4. **`docs/ideas/variable-names-refactor.md:28-29`** is cited for a `_cents` suffix contract. The file is
   stale relative to the shipped table; its uppercase vocabulary is not authority. The `_cents` question
   stays an operator note, not a rename.

**Left `[UNVERIFIED]` deliberately:** none. Every seam above was read in the working tree on `01157a3`.

---

## 3. Resolved open questions (all four answered **from code**, no operator round needed)

**Q1 — Where the aliases live → `_LEGACY_TO_CANONICAL` (the issue's default), and `load()` migrates to
canonical keys.** Proven, not assumed: `_ConfigBase._wrapped_init` (`core_brain/config.py:209-218`) only
translates a legacy keyword through that table, and the "fill defaults" loop explicitly skips table names
(`:219-227`). A probe that injected the eight pairs into the running module and constructed a
post-change subclass showed the legacy keyword reaching the canonical field (`777.0`), while an unmapped
keyword is silently stored by `object.__setattr__`. Hand-written `@property` aliases in `scoring/config.py`
would have made `HUNTER_MIN_MOVEMENT_USD` and its five siblings **inert**. Widening the touched files to
`core_brain/config.py` is therefore required, not optional.

**Q2 — Derived cap name → additive read-only property, name kept as requested.**
`select_max_book_spread` (`core_brain/config.py:425-430`) is `round(max_book_spread * select_spread_pct, 6)`
with a `wide_book_trial` pass-through; verified 0.0205 default, 0.1025 at `max_book_spread=0.30`, 0.15 at
`wide_book_trial=0.15`. The suffix reads as fraction, as `bot_quote_price_band_min_cents = 0.10`
(`core_brain/config.py:284`) and `bot_quote_spread_static_offset_cents = 0.020` (`:294`) already do. Keep the requested name, document the unit in the
docstring, and surface the suffix question to the operator in the PR body. **Not** a blocker.

**Q3 — Which vocabulary wins → the shipped `_LEGACY_TO_CANONICAL` table.**
The idea doc proposes `FILTER_LIVE_VEL_MIN_NOTIONAL_USD` / `FILTER_COMMON_QUEUE_MAX_WAIT_MIN`; the code has
`filter_live_vel_min_usd` / `filter_std_queue_max_clear_min`. The table ships, the doc does not. Untouched.

**Q4 — Sequencing → resolved: `#651` IS MERGED. Branch from `main`.**
See §2 rejection 1 for the evidence. This removes the stacked-merge risk that `docs/agents/git-workflow.md`
warns about and makes the ticket standalone.

---

## 4. Interface contract (locked before any edit)

### 4.1 `core_brain/config.py` — eight new entries in the existing table

Add a clearly labelled picker block to `_LEGACY_TO_CANONICAL` (`:35-150`). The wiring loop at
`:766-774` already installs the `_LegacyProperty` descriptor and the synthetic dataclass field per entry —
**do not touch the loop**, and do not add a second mechanism.

```python
    # Picker filter bars (#652): the eight names the ranker used to declare for
    # itself. Core owns the value; these stay readable for the dashboard cards
    # (dashboard/server.py:4312-4508), scripts/filter_markets.py and the tests.
    "select_max_queue_minutes": "filter_std_queue_max_clear_min",
    "enforce_max_queue_minutes": "filter_std_gate_queue_max_on",
    "select_movement_window_sec": "filter_live_vel_lookback_sec",
    "select_min_movement_usd": "filter_live_vel_min_usd",
    "select_velocity_gate_enabled": "filter_live_gate_velocity_on",
    "select_min_activity_ratio": "filter_live_activity_min_ratio",
    "select_min_range_cents": "filter_live_move_min_range_cents",
    "select_volatility_window_sec": "filter_live_move_lookback_sec",
```

Contract: for each pair, `getattr(scoring_cfg, old)` emits `DeprecationWarning` and returns the canonical
value; `MakerConfig(**{old: v})` reaches the canonical field; `old` appears in `dataclasses.fields(scoring)`
and `dataclasses.fields(core)`; `old not in _READ_ONLY_PROPERTIES`.

### 4.2 `core_brain/config.py` — the derived cap alias

Next to `select_max_book_spread` (`core_brain/config.py:425-430`), getter only:

```python
    @property
    def filter_common_spread_max_cents(self) -> float:
        """The picking bar, canonically named. A price FRACTION, not whole cents:
        same convention as `bot_quote_price_band_min_cents` and
        `bot_quote_spread_static_offset_cents`. Read-only; forwards the derived
        value so the trial pass-through and six-decimal rounding live in one place."""
        return self.select_max_book_spread
```

and add `"filter_common_spread_max_cents"` to the `_READ_ONLY_PROPERTIES` set literal in
`_wrapped_init` (`:185-191`).

Contract, all four measured on live code:
- default 0.0205, equal to `select_max_book_spread`;
- `= 0.1025` at `max_book_spread=0.30`; `= 0.15` at `wide_book_trial=0.15`;
- **constructor refuses it with `TypeError`** — "got an unexpected keyword argument" — the same shape as
  `select_max_book_spread` today. *Without* the refusal entry the call fails with the confusing
  `AttributeError: property 'MakerConfig' object has no setter` raised inside `object.__setattr__`, measured;
  that is why the entry is part of the change, not optional polish;
- not a dataclass field; instance assignment raises `FrozenInstanceError` (a subclass of `AttributeError`).

No setter, no dataclass field, no `_LEGACY_TO_CANONICAL` entry for this name.

### 4.3 `scoring/config.py` — nothing declared, everything inherited

- Delete the eight declarations and their local comments (today `:79-135`). Move a bar's explanation to
  the canonical core field only where it says something the core comment does not (queue bar ↔
  `pairs_exit_window_sec`, movement gate ↔ `#416`); do not duplicate prose.
- Keep the five `PICKER_PROFILE` overrides and `db_path()` **byte-identical**.
- Update the module docstring (`:7-16`), which currently promises "the eight picker bars below", and the
  `# --- bars only the ranker gates on ---` heading.
- In `load()`, six dictionary keys become canonical — keys only:

| line | from | to |
| --- | --- | --- |
| `:271` | `kw["select_min_movement_usd"]` | `kw["filter_live_vel_min_usd"]` |
| `:279` | `kw["select_movement_window_sec"]` | `kw["filter_live_vel_lookback_sec"]` |
| `:282` | `kw["select_velocity_gate_enabled"]` | `kw["filter_live_gate_velocity_on"]` |
| `:288` | `kw["select_min_range_cents"]` | `kw["filter_live_move_min_range_cents"]` |
| `:296` | `kw["select_volatility_window_sec"]` | `kw["filter_live_move_lookback_sec"]` |
| `:313` | `kw["select_min_activity_ratio"]` | `kw["filter_live_activity_min_ratio"]` |

Local variable names, parsing, `math.isfinite` checks, clamping, bounds and fallbacks stay exactly as they
are. No env var is added for the two queue bars.

### 4.4 `tests/test_config_divergence_guard.py` — the guard contract

```python
# The eight bars core now owns for the picker. They must be real core fields,
# reachable on the picker at the same default, and reachable by their old names.
CORE_OWNED_PICKER_BARS = frozenset({...the eight canonical names...})

# Pinned old -> canonical, so re-pointing an alias at a different field fails here.
PICKER_BAR_ALIASES = {  # the eight pairs, written out
    ...}
```

- `PICKER_ONLY` is **deleted**; the class-body allow-list becomes `PROFILE_NAMES` only (see §5).
- `test_picker_only_fields_do_not_exist_on_core` is **replaced** by an affirmative test asserting, for each
  canonical name: it is a core dataclass field; it is a scoring dataclass field; both defaults are equal;
  `PICKER_BAR_ALIASES[old] == _LEGACY_TO_CANONICAL[old]`.
- New reverse-reachability test: `scoring_fields - core_fields == set()`.
- New `test_the_class_body_declares_no_other_property_or_method`: collect `FunctionDef`/`AsyncFunctionDef`
  names from the scoring class body and assert the set is `{db_path}`.

**Which assertions carry the RED** (measured today, `01157a3`): the scoring class body declares exactly 13
annotated names — the five `PICKER_PROFILE` fields plus the eight bars — with zero unannotated assignments
and `functions == ["db_path"]`. So the narrowed allow-list test fails today with the eight old names as its
excess (RED), the reverse-reachability test fails, and the alias-table assertion fails (none of the eight is
in the table yet). The `{db_path}` method check passes on both sides — it is hardening, not RED — and no
existing test in the file other than the two being replaced changes meaning.

---

## 5. The one improvement proposal (adopted — edge-case hardening, not scope expansion)

**Evidence, verbatim from `tests/test_config_divergence_guard.py:24-33, 123-129`:**

```python
PICKER_ONLY = frozenset({
    "enforce_max_queue_minutes",
    "select_max_queue_minutes",
    ...
})
...
extra = set(_declared_names()) - PICKER_ONLY - PROFILE_NAMES
```

**The hole:** the ticket says to repoint `PICKER_ONLY` to the canonical names. Do that naively and the
allow-list becomes the canonical names — so re-declaring `filter_live_vel_min_usd: float = 200.0` in the
scoring class body is **no longer flagged**: it is a member of the allowed set. That is the exact failure
the guard exists to prevent, and it is the ticket's own warning made concrete — the issue says *"a guard
that passes because its set is empty is worse than no guard"*.

**Fix (folded into T1):** the class-body allow-list becomes `PROFILE_NAMES` alone. A re-declared bar fails
even when its value is identical, which is the property the guard is supposed to have. The canonical names
move to the affirmative test in §4.4, where they belong. Cost: one constant's usage, not a new file.

**Rejected alternative, recorded so it does not resurface:** adding `filter_common_spread_max_cents` to the
dashboard parameter-card registry. No evidence in the issue or the code asks for a new card; the ticket says
"rename no dashboard key". Not proposed.

---

## 6. Tasks (dependency graph first, riskiest first)

```
T1 (guard, RED)  ──►  T2 (core owns)  ──►  T3 (picker inherits)  ──►  T4 (regression pins)
                         └──────── land together, one commit ────────┘
```

| ID | Size | Domain | Files | What is built | Depends on | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| **T1** | S | `[Backend/Logic]` | `tests/test_config_divergence_guard.py` | Replace `PICKER_ONLY` with `CORE_OWNED_PICKER_BARS` + pinned `PICKER_BAR_ALIASES`; replace the negative core-field test with the affirmative one; narrow the class-body allow-list to `PROFILE_NAMES`; add reverse reachability and the `{db_path}` method check. | — | **RED**, agent-run: `python -m pytest -q tests/test_config_divergence_guard.py` must FAIL on the pre-change class body. Capture the failure output. |
| **T2** | M | `[Backend/Logic]` | `core_brain/config.py` | The eight alias pairs (§4.1) + `filter_common_spread_max_cents` property and its read-only refusal entry (§4.2). Wiring loop (`:766-774`) and every canonical default untouched. | T1 | Guard `test_every_...` passes; probe: `scoring`/`core` field sets equal. Lands **with** T3. |
| **T3** | M | `[Backend/Logic]` | `scoring/config.py` | Delete the eight declarations and refresh the docstring; migrate the six `load()` keys to canonical. Values, parsing and fallbacks unchanged. | T2 (same commit) | `python -m pytest -q tests/test_config_divergence_guard.py tests/test_config_single_source.py tests/test_market_selection_bars.py` — all green, `SCORING_DEFAULTS` untouched. |
| **T4** | S | `[Backend/Logic]` | `tests/test_config_single_source.py`, `tests/test_market_selection_bars.py` | Six parametrised end-to-end env tests (clear the other five, set one, assert canonical + `pytest.warns` on the old name, `"off"` → `False` for the gate); the all-eight old-name warning/default test; the pristine-load defaults; the derived-cap pins (§4.2 contract). | T3 | Agent-run focused selections (§7); each new test verified to fail without T2+T3. |

**Checkpoints:** after T3 → one-line progress report: guard green, the three focused files green, the pin
file untouched. After T4 → the six reader/regression files green, then hand off to Station IV.

**Commit shape:** T2+T3 are one logical change (the two config modules keep the picker's values only
together) → one `refactor(config): ...` commit. T1 alone is one `test(config): ...` commit; T4 one more.
`docs/agents/git-workflow.md` type/scope conventions apply; the plan files travel with the branch.

---

## 7. Verification

### Agent-run focused checks (never handed to the operator)

```bash
# after T3 (and again after any review fix touching the config modules)
python -m pytest -q tests/test_config_divergence_guard.py tests/test_config_single_source.py tests/test_market_selection_bars.py

# after T4
python -m pytest -q tests/test_velocity_gate.py tests/test_movement_gate.py \
  tests/test_maker_queue_bar.py tests/test_filter_loop.py tests/test_config_for_display.py \
  tests/test_queue_clear_gate.py tests/test_wide_book_trial.py tests/test_loose_baseline.py
```

The full `python -m pytest -q` suite is the GitHub CI merge gate on Ubuntu and Windows — not run locally here.

### Evidence already gathered for this plan (non-destructive, no file edited)

A throwaway probe injected the eight pairs into the running module objects and built a post-change replica
of `scoring.config.MakerConfig`. Measured: field sets equal (238 = 238, scoring-minus-core empty); all
eight old-name reads return the canonical value and warn; legacy constructor keyword reaches the canonical
field; both live `replace()` call sites behave; pristine defaults exactly `200.0 / 1800.0 / True / 0.0 /
2.0 / 7200.0 / 15.0 / False`; **all 82 `SCORING_DEFAULTS` names present and value-identical with zero
mismatches**, so that pin passes untouched. The probe script was deleted; its numbers are the basis for
A3/A4 and for leaving the pin file alone.

### How to verify (operator, hands-on — no pytest)

```powershell
# 1. Fresh shell: drop any inherited overrides
Get-ChildItem Env:HUNTER_* | ForEach-Object { Remove-Item "Env:$($_.Name)" }

# 2. Push the movement bar to a value the fallback can never produce
$env:HUNTER_MIN_MOVEMENT_USD = "777"

# 3. Dashboard only
python -m dashboard.server --host 127.0.0.1 --port 8799
```

1. Open `http://127.0.0.1:8799` and find the market-picker parameter cards.
2. Expect **Movement Lookback Window 1800s (30m)**, **Movement bar $777.00** (the override above — a
   `$200.00` card here means the alias stopped resolving), and the queue card **15.0m** with
   **"Telemetry Only"**.
3. Failure signal: a card shows the hard-coded fallback literal from `dashboard/server.py:4312-4508`
   instead of a configured value — that means an alias stopped resolving.

Do **not** click START, do not launch the Trader, do not set `BRIDGE_CONTROL=1`. This is a read-only
display check; it spends nothing.

---

## 8. Out of scope / noticed but not touching

Recorded in `docs/issues/652-noticed-but-not-touching.md`: the `dataclasses.replace()` precedence limitation
that this change makes reachable for eight more names (pre-existing shared behaviour, needs its own ticket),
and the `_cents` suffix convention question CodeRabbit raised. Neither blocks this ticket.

---

## 9. Progress (Station III)

- [x] **T1** — guard repointed. RED confirmed against the pre-change class body: `3 failed, 11 passed`,
      naming the eight leftover fields, the class-body re-declaration and the empty alias entries.
- [x] **T2** — core owns the eight pairs and the derived alias. `filter_common_spread_max_cents` reads
      0.0205 / 0.1025 / 0.15 on both classes, refuses the constructor with `TypeError`, is absent from
      `dataclasses.fields()`, and cannot be assigned.
- [x] **T3** — scoring inherits. The class body holds only the five `PICKER_PROFILE` fields plus
      `db_path`; the six `load()` keys are canonical.
- [x] **T4** — regression pins added. 27 new cases; reverting only the two config modules makes **17**
      of them fail, so the change is what makes them pass. The 10 that pass either way are the deliberate
      values and refusal pins, guarding behaviour this ticket must not alter (measured at Station IV).
- [x] **T4b** — added after the `tdd-guide` persona review (T1/T4 were test-writing-heavy): the two
      dashboard-card endpoint tests and the refused-value fallbacks. Mutation-checked — deleting one
      alias entry from `_LEGACY_TO_CANONICAL` makes the card test fail on the silent fallback.

**Checks (agent-run, all green):** 66 passed (`divergence_guard`, `single_source`, `market_selection_bars`),
215 passed (the eight reader/regression files), 186 passed (`dashboard_server` plus the config sweep).
The full `python -m pytest -q` suite stays with GitHub CI on Ubuntu and Windows.

**Extra evidence beyond the test files:** the real `/api/parameters` payload is byte-identical before and
after the change, including with `HUNTER_MIN_MOVEMENT_USD=777` set — so no dashboard card moved. With the
override set the card reads `$777.00` rather than its `$200.00` fallback literal, which is the hands-on check
that distinguishes a resolved alias from the hard-coded default.

**Commits:** `dfa3237` (plan) · `2ae507e` (T1) · `b5de375` (T2+T3) · `eee3415` (T4) · `b0d30eb` (T4b).
