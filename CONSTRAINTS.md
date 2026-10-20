Branch: i706/remove-legacy-max-spread-env-aliases | Issue: #706

# Constraints - #706 retire the legacy max-spread env names

Operator directive, verbatim: **"Zero fallback. Zero warnings. Zero mention of old names."**
`BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` is the only accepted name for the spread window.

## The bar

- `git grep -n HUNTER_MAX_SPREAD` returns **zero** hits in production code (`core_brain/`,
  `scoring/`, `dashboard/`, `scripts/`), `tests/`, `.env.example` and live docs
  (`docs/agents/**`). `docs/issues/**` ledgers are exempt historical records and stay
  byte-identical except the `#707` ledger row N3 status (`open` -> `fixed #706`).
- No deprecation warning about a spread variable exists anywhere after this change. Both
  old warnings are deleted, not reworded or harmonised.
- The retired names are **ignored, not rejected**. Nothing reads them and nothing reports
  them, which is what the acceptance criterion "setting only a legacy name has zero effect"
  means in practice.
- The `#707` validation survives on every remaining read: `_bounded_float(..., 1.0, 99.0)`
  and the message naming `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` for `inf`, `nan`, `0`, `100`,
  negatives and sub-cent values. A non-number is still tolerated on the unpinned path only.

## Two directive tensions, resolved here so a later session does not "fix" them back

1. **"Surface a clear error for a wrong env var" vs "zero mention of old names".** An error
   that names the retired variable mentions it, and an error that does not name the variable
   is not actionable. Resolution (the one the ticket's own AC implies): the loaders ignore
   unknown `HUNTER_*` names entirely; the only error surfaced is for the **canonical** name's
   bad value, which `#707` already raises. Do not add a "retired variable" error later.
2. **"A test proves the legacy name has zero effect" vs "zero mention in tests".** A test
   that sets the retired name writes the literal back into the suite and the grep bar can
   never reach zero. Resolution: the deletion is the proof (no read site exists), pinned by
   (a) the grep bar itself and (b) a test that a name which is *not* the canonical one —
   including a near-miss spelling — leaves the shipped default in place for both loaders,
   pinned and unpinned. Do not reintroduce the retired literal to make this test "stronger".

## Untouched

- Money path: `max_spread_from_mid` stays a distance-from-mid quoting window, never a
  bid-ask width, never fed to `MAX_BOOK_SPREAD` or `filter_common_spread_max_cents` (0.0205).
  `max_book_spread = 0.06`, `max_pair_cost`, `select_spread_pct = 41/120`, the trial bound
  (0.30) and the two shipped defaults (ranker 0.045 / engine 0.15) all stay.
- The two engine read blocks stay separate (pinned strict, general tolerant). The blocked
  form is confirmed by #632 ledger item 3; merging them is not this ticket.
- Every other env var's alias chain (`BOT_QUOTE_SIZE_MIN_SHARES`/`HUNTER_MIN_QUOTE_SHARES`,
  `BOT_QUOTE_GRID_TICK_SIZE_USD`/`HUNTER_PRICE_TICK`, ...) is out of scope.
- `tests/test_stop_loss_config.py:88` is about a different retired variable; do not touch it.

## Process

- No direct push to `main`; no venue/live command; no write to `data/orders.db`.
- Deletions removed tests that assert the removed behaviour only. No remaining test is
  skipped, disabled or softened to get green.
- The full `python -m pytest -q` suite runs ONCE locally before the push (deletion carve-out:
  importers of deleted symbols can sit in files this diff never touches). CI is the gate.
- No `pytest`/`gh`/`git` step inside the operator's `## How to verify`.
- Accepted consequence, stated in the PR: a shell that still exports the retired name gets
  the shipped window, not its value.
