# Plan — #707 [FIX] Validate BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID range

Branch: `i707/validate-max-spread-from-mid-range` | Issue: `#707`
Size: **Small** (2 loaders + 1 test file, ~15 changed lines each). Task type: **Code** (config
validation on the money path). Skills: `test-driven-development`, `incremental-implementation`,
`source-driven-development`.

## Intake note — the CodeRabbit plan comment (read once)
- **Adopted:** the unit (bind cents `1..99`, store `fractions`), reuse of the existing
  `_bounded_float`, the alias-naming requirement, and its test matrix shape (both loaders ×
  pinned/unpinned × aliases × fractional cents).
- **Rejected:** its Design Choice 2 ("raise on any nonblank invalid value, pinned or unpinned") and
  its "merge the pinned and unpinned spread branches into one path". Both would delete the pinned
  strictness and turn a *typo* into a startup crash on the unpinned ranker path, breaking the rule
  #690 documented at `scoring/config.py:190-192` and pinned by
  `tests/test_config_single_source.py:592-594`. It also proposed removing the engine's duplicate
  read and its duplicate `HUNTER_MAX_SPREAD` warning -- a behaviour change the ticket never asked
  for and #706's territory.
- **Rejected:** its `.env.example` claim that a value must be "between 1 and 99 cents" is right, but
  its step 3 ("update observation O3 in the #690 ledger") is done here as a status flip to
  `fixed #707`, nothing more.
- **Verified against live code:** all three read sites exist as described (`core_brain/config.py`
  pinned branch + general read, `scoring/config.py` one read used by both paths); `_bounded_float`
  messages are `is not a number` / `is not finite` / `is outside {lo:g}..{hi:g}`; the ready-made
  helper is at `core_brain/config.py:943` (now 944+ after the diff) and `scoring/config.py:83`.
- **`[UNVERIFIED]` items, resolved:** none left open. Its "check whether a test counts two engine
  warnings on pinned runs" resolved to *yes, the count is unchanged* -- the pinned read and the
  general read both still warn, so no test needed repinning.

## Decisions
- **Unit:** the env var is **cents**. Bound it `1..99`, divide by 100 on the way in. A literal
  `0.01..0.99` bound on the env value would store `0.0001..0.0099` and break `.env.example` (`15`)
  and `scripts/shadow_tournament.py`, which writes whole cents.
- **The `0` boundary: option (a), a rejection.** The operator directive is "can only be 0.01-0.99".
  `0` and negatives joining the typo-tolerance was exactly the silent ignore the ticket exists to
  kill, so `0`, `-5`, `0.5`, `100`, `inf`, `nan`, `-inf` all raise. Only a *non-number* stays
  inactive, and only on the unpinned path (recorded in `CONSTRAINTS.md` §4 and repinned with its
  reason in the test).
- **Both engine reads stay.** Deleting the pinned read would silently make a typo inactive on the
  pinned money path and drop one deprecation warning. The diff adds a range check to each read and
  changes nothing else.

## Dependency graph
```
T1 (engine loader) ─┐
T2 (scoring loader)─┴─> T3 (docs: .env.example + #690 ledger O3)
                     └─> T4 (tests: boundary/rejection/alias/typo-pin)  [fails before T1+T2]
```

## Tasks
| ID | Size | Tag | Files | Description | Verify | Depends on |
|----|------|-----|-------|-------------|--------|-----------|
| T1 | S | [Backend/Logic] | `core_brain/config.py` | Pinned read and general read both route the resolved value through `_bounded_float(name, raw, 1.0, 99.0) / 100.0`; the resolved *name* is captured so the error names the alias that won. Unpinned keeps "a non-number is inactive". | `pytest tests/test_config_single_source.py` | — |
| T2 | S | [Backend/Logic] | `scoring/config.py` | Same rule in the ranker loader (pinned strict, unpinned tolerant of non-numbers only) + comment block updated, since it documented zero-and-below as inactive. | `pytest tests/test_config_single_source.py tests/test_wide_book_trial.py` | — |
| T3 | XS | [Docs] | `.env.example`, `docs/issues/690-noticed-but-not-touching.md` | State the 1..99-cent range and the error behaviour where the operator sets it; flip ledger O3 to `fixed #707`. | eye + `git diff` | T1, T2 |
| T4 | S | [Backend/Logic] | `tests/test_config_single_source.py` | Boundary accept (`1`, `99`, `7.5`, `22`), rejection (`0`, `100`, `inf`, `nan`, `-inf`, `-5`, `0.5`) × both loaders × both paths, exact message, alias naming, and the per-path typo pin. Repin the two #690 tests that asserted the retired tolerance. | same commands as T1/T2 | T1, T2 |

## Checkpoints
1. **After T1+T2:** focused suites green (build-time: full suite once, 4695 passed / 6 skipped).
2. **After T4:** the two repinned tests pass, the four new test groups fail without the source
   change (observed: they failed with the old `cents > 0` logic).
3. **Before PR:** `git status --short` shows exactly the five intended files.

## Run-environment caveats (not part of the change)
- The **full local test suite dirties the tracked demo registry** `data/demo_orders.db` (binary
  diff after the build-time full run; no test in this change writes it -- it is only read through
  `kpi_report` at `tests/test_analysis_v2_binding.py:28-54`, and it is a WAL-mode sqlite file, so a
  checkpoint on close is the likely mechanism, not established). Restored with
  `git checkout -- data/demo_orders.db` before staging; the PR carries no change to it, and the row
  is filed in `docs/issues/707-noticed-but-not-touching.md` (N4).
- The engine loader is reached at **import** time by `core_brain/venue.py:24` (`_CFG = _load_cfg()`),
  which is why `python -m core_brain.order_manager status` refuses before it can touch the venue.

## §6 — One improvement proposal
**Rejected as scope.** `docs/agents/glossary.md:99` still maps `HUNTER_MAX_SPREAD` as if it were
canonical, and the dashboard's parameters card hard-codes a `(15¢)` suffix
(`dashboard/server.py:4861`). Both are copy/UI drift already published as their own issues (#705,
O4 of the #690 ledger), and neither changes what the loader accepts. Recorded here so they do not
resurface.
