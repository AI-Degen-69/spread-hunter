# Handoff → Station IV (Review, Verify & Ship) — Issue #690

Written by Station III (`iii-build-plan`) at the end of the build. Read this instead of
re-deriving the build from the diff. `tasks/plan.md` is the spec; this file is what a
reviewer needs that the spec cannot tell you: what shipped, what was proven, and where the
soft spots are.

## 1. One-paragraph orientation

Issue #690 is ledger item **N2** from #674. The literal ask — *"reject markets over 2 cents
and show the reason"* — **already shipped** before this branch, through a *different*
setting (`filter_common_spread_max_cents`, 2.05¢, a bid–ask **width**). What was actually
broken is narrower: `scoring/config.py:load()` read `max_spread_from_mid` (a
**distance-from-mid** quoting window) only inside `if cid:`, so an unpinned ranker run
ignored the operator's env while `core_brain/config.py` honoured it. This PR is a
**settings-parity fix**. It changes **no** market's eligibility, order, or rejection reason.
If a reviewer treats it as an admission change, the review will go the wrong way.

## 2. Branch state

- Branch: `i690/apply-spread-bar-at-market-filter`
- Base: `origin/main` @ `0d392e5`
- 5 commits ahead, **not pushed**, no PR yet, working tree clean

```
b392b66 docs(690): close N2 in the #674 ledger and mark the build tasks done
1730288 test(filter_markets): prove the 2.05c gate rejects 50/53 and keeps its reason (#690)
c2ec362 test(scoring): pin unpinned spread-window parity and the trial window (#690)
0ccb043 fix(scoring): read the spread window on unpinned ranker runs (#690)
cfb02dc docs(690): station II plan artifacts for spread-bar loader parity
```

Commits map 1:1 to `tasks/plan.md` T1–T4. Read the full diff before merging — this branch is
stacked on nothing, but `docs/` artifacts (`CONSTRAINTS.md`, `tasks/*`) are part of the diff
and inflate it beyond the code change.

## 3. Diff surface — what to actually review

| File | Nature | What to check |
|---|---|---|
| `scoring/config.py` | **The only behaviour change** (+34/−7) | The read left `if cid:` and now sits above the wide-book trial block. Pinned branch uses `float(sp)/100.0` unchanged; unpinned mirrors `core_brain/config.py:636-642`. |
| `tests/test_config_single_source.py` | +91, tests only | Unpinned matrix + engine parity |
| `tests/test_wide_book_trial.py` | +24/−1, tests only | Unpinned trial window; `_load_with` clears two more names |
| `tests/test_redesigned_filter_pipeline.py` | +69, tests only | 50/53 rejection on the shipped bar; wide window cannot widen the gate |
| `tests/test_dashboard_server.py` | +42, tests only | Reason text survives to `funnel.filters[].examples[].reason` |
| `scripts/filter_markets.py` | **Comments only** (+6/−2) | `MAX_BOOK_SPREAD` provenance note; stale reward-width claim removed |
| `docs/issues/674-…`, `tasks/*`, `CONSTRAINTS.md` | docs | N2 marked fixed; plan marks `[x]` |

`scripts/filter_markets.py` must still have exactly **6** `MAX_BOOK_SPREAD` occurrences —
`rg -c "MAX_BOOK_SPREAD" scripts/filter_markets.py`. If that number moved, logic changed.

**Untouched on purpose:** `core_brain/config.py`, `core_brain/quotes.py`,
`core_brain/trader_loop.py`, `core_brain/order_manager.py`, `dashboard/static/app.js`, the
`0.0205` ceiling, `max_book_spread`, `select_spread_pct`, the trial bound `0.30`.

## 4. The behaviour contract (what a reviewer should hold the diff to)

1. **Pinned = strict.** `float(val)/100.0`, raises on garbage. Byte-identical to before; the
   pre-existing pinned tests pass unmodified — that is the proof.
2. **Unpinned = tolerant, mirroring the engine.** `float()` failure → `0.0`; only `cents > 0`
   is applied. Garbage/zero/negative are *inactive*, never enforced-at-zero.
3. **`inf` passes on both sides.** The engine's tolerant read is `if cents > 0`, and
   `float("inf") > 0` is true. Scoring mirrors it deliberately. **This is pinned by a test
   with an explanatory docstring** — do not "fix" it here, and do not let a review round
   tighten one side only, because that recreates the divergence this PR removes. If it is
   wrong, it is wrong in the engine and belongs in its own ticket (filed as O3).
4. **Ordering matters.** The read must precede `wide = resolve_wide_book_trial(...)`, else
   `max(window, wide)` compares against the class default on unpinned runs. That is the
   whole point of T1.
5. **One warning per load**, text byte-identical:
   `HUNTER_MAX_SPREAD is deprecated (#632); set BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID instead.`
   It now fires on the unpinned path too. Old logs are searched by that string (#688 fixed
   its wording once already — do not touch it).
6. **The book gate never reads this value.** `pair_books_allowed` / `book_allowed` get
   `filter_common_spread_max_cents`, nothing else.

## 5. Verification already run (do not re-run blind; do re-run if you edit the files)

| Gate | Command | Result |
|---|---|---|
| RED before the fix | 3 suites + 1 kpi test | `5 failed, 155 passed` |
| GREEN after | `pytest -q tests/test_config_single_source.py tests/test_wide_book_trial.py tests/test_redesigned_filter_pipeline.py tests/test_market_selection_bars.py tests/test_dashboard_server.py::test_kpi_funnel_keeps_the_full_spread_rejection_reason` | `182 passed`, exit 0 |
| Adjacent suites | `+ tests/test_config_divergence_guard.py tests/test_rewards_purge.py tests/test_unified_universe.py` | `261 passed` |
| Full dashboard file | `pytest -q tests/test_dashboard_server.py` | `137 passed` |
| Operator-surface proof | `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID=22 python -c "...load()..."` | `picker 0.22` / `engine 0.22` (was `0.045`) |

The GREEN run was repeated **after** the last edit to `scoring/config.py` (a comment trim),
so it covers the shipped file. Full `python -m pytest -q` was **not** run locally — it is the
CI merge gate.

## 6. Soft spots — where to push

These are the honest weak points. None of them is a defect, but a reviewer who finds them
and reports them as bugs should get a considered answer, not a scramble.

- **A guard test that is half config assertion.** `test_a_wide_quote_window_never_widens_the_book_gate`
  asserts `cfg.filter_common_spread_max_cents == 0.0205` *and* `fm.MAX_BOOK_SPREAD == 0.0205`.
  Both are import-time values, so an env var cannot move them inside one process; the test
  fails if someone re-derives the ceiling from `max_spread_from_mid`, but it does **not**
  prove a runtime flip, because no such path exists.
- **Two new tests were green before the change.** `test_the_shipped_two_point_zero_five_cent_bar_rejects_a_fifty_fifty_three_book`
  and the KPI reason test pin behaviour that already shipped. That is intentional regression
  coverage, not evidence of a fix. The RED set that proves the fix is the unpinned matrix,
  the legacy-warning case, the parity case, `[scoring]` of the trial case, and the wide-window
  guard.
- **`inf` is a documented quirk, not an oversight.** See contract item 3.
- **Blank canonical name falls through to the legacy name.** That is pre-existing `_env_get`
  semantics (`core_brain/config.py:931-940` treats blank as unset), **not** "blank means the
  default". `test_unpinned_bad_spread_values_are_inactive_not_enforced` covers `""` with no
  legacy set, which is the default path.
- **Defaults still differ by design:** picker `0.045`, engine `0.15`. Only the *rule* was
  matched. A reviewer asking "why aren't they equal" should get this answer.

## 7. Out of scope — already recorded, do not expand this PR

`docs/issues/690-noticed-but-not-touching.md` holds O1–O6 (dashboard card hardcodes `(15¢)`;
the two loaders word the deprecation warning differently; the engine accepts `inf`; the
field has three documented roles; the dashboard substitutes engine config on a scoring load
failure; `_load_with` hygiene — the last one is fixed in this PR). `docs/issues/674-…` N2 now
reads `fixed #690`. Anything new you notice belongs in that ledger as an `open` row, not in
the diff.

## 8. Repo rules that bite at this station

- **CI is the merge gate, not local pytest.** Inspect with `gh pr checks <n>`. Do not repeat
  the full local suite.
- **Never ask the operator to run pytest**, and never cite test counts as proof to them. Their
  verification is hands-on: `tasks/plan.md` §8 (the parity one-liner, the `--dry-run`
  ranker into `runtime/previews/spread-follow-up/`, and `runtime/markets.json` untouched).
- **One push per review round.** Batch every accepted fix into one commit, push once.
- **One focused CodeRabbit round.** Post `@coderabbitai review` as its own comment after
  opening the PR; check for an existing review first, then 5m → 4m → 3m → 2m → 1m. Reply
  `ACCEPT:` / `REJECT:` to every thread, resolve only replied-to threads, no
  `@coderabbitai autofix`, no second review round.
- **Never touch `data/orders.db`.** No live commands, no dashboard START, no `BRIDGE_CONTROL`.
- `docs/agents/git-workflow.md` before the first push.

## 9. What "done" means for this PR

PR body states plainly: **parity fix, not an admission change**; `0.0205` still owns wide-book
rejection; the 50/53 rejection and its reason already existed and are now pinned. That framing
is the difference between a reviewer accepting this in one round and a reviewer asking why the
issue's promise is not implemented.
