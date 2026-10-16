# Constraints - #622 (keep, remove, or bound the refusal-hold quoting machinery)

Branch: i622/decision-refusal-hold-keep-remove-or-bound | Issue: #622

## Zero regressions

- Focused checks during build and after review fixes:
  - `python -m pytest -q tests/test_trader_loop.py tests/test_cancel_attribution.py`
  - `python -m pytest -q tests/test_shadow_tournament.py tests/test_grace_sweep_report.py`
  - `python -m pytest -q tests/test_refusal_hold_report.py` (new)
- Every changed behaviour needs a test that **fails without the change** (TDD: RED first).
- Full `python -m pytest -q` stays with GitHub CI on Ubuntu and Windows. Never run it
  locally during build; never hand it to the operator.

## Anti-cheat

- Never skip or delete tests, weaken assertions, delete `assert` lines, or add
  `pytest.skip` / `xfail` / lint suppressions to reach green.
- Deleting a test is allowed **only** when the behaviour it pins is deleted by the winning
  option — and the deletion must be named in `tasks/plan.md` with the option that justifies
  it. Tests pinning behaviour that still exists must stay.
- No `# type: ignore`, no `# noqa` added to silence this change.

## Production safety

- **Never write to, delete, or open `data/orders.db` for write.** The tournament, the report
  script, and every test use their own dedicated database.
- **No live-trading commands at any station.** No `quote`, no `complete`, no Trader loop, no
  signer. The tournament is launched by the **operator**; the agent runs `--dry-run` only.
- `scripts/book_tape_recorder.py` gets its own dedicated database, never the production one.

## Measurement honesty

- Arms poll independently and therefore do **not** see identical books. Every published
  result is labelled **"approximately concurrent shadow evidence"**.
- The shadow fill model fills a bid only when a trade prints **strictly below** it, and does
  not simulate queue position. Shadow fill rates are **never** presented as real venue fill
  probability.
- Arm start order is rotated across repeats, because workers start sequentially.
- Unfilled orders in time-to-fill are **censored**, not dropped or counted as zero. The
  censored count is printed.
- The decision rule is written into `docs/runs/` **before** any result is read.
- The issue's `shadow-02` churn claim ("23 of 25 cancels") **cannot be checked from
  repository data**. Say so; do not restate it as verified.

## Dependencies and scope

- **No new dependencies.** Standard library + existing repo modules only.
- Touched files are limited to: `core_brain/trader_loop.py`, `core_brain/config.py`,
  `core_brain/order_registry.py`, `core_brain/cancel_report.py`, `docs/agents/strategy.md`,
  `docs/explanations/grace-hold-explainer.html`, `docs/runs/2026-10-16-622-*.md`,
  `configs/tournaments/i622_*.json`, `scripts/refusal_hold_report.py`, the test files listed
  above plus `tests/test_trader_loop.py` / `tests/test_shadow_run.py`, and
  `docs/issues/622-noticed-but-not-touching.md`.
- Anything else discovered goes to `docs/issues/622-noticed-but-not-touching.md` with an
  owner (`you` or a named issue) and a date — never into this diff.

## Job guards

- Do not reference or reuse stale worktrees.
- No destructive git operations (reset, force-push, `checkout --`, stash) without explicit
  operator confirmation.
- Three recording homes: `docs/issues/622-noticed-but-not-touching.md` for out-of-scope code,
  `docs/runs/2026-10-16-622-refusal-hold-tournament.md` for run evidence,
  `tasks/plan.md` for environment caveats.
