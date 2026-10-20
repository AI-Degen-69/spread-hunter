Branch: i708/unify-spread-docstring | Issue: #708

# Constraints - #708 unify the max_spread_from_mid description, triage rebate dead code

Two housekeeping items from #690 O4: one field documented three different ways, and a
`grep rebate` full of references to a model the strategy no longer uses.

## The bar

- `max_spread_from_mid` carries **one** description, byte-identical in
  `scoring/config.py` and `core_brain/config.py`, and a guard test pins that equality.
- The description states the field's real role: **the largest allowed distance between
  the book midpoint and our resting quote**. It does not say "rebate" and does not say
  "market selection" -- the issue's own wording is wrong, and `CONSTRAINTS.md` (#706,
  "Untouched") already pins the field as a distance-from-mid quoting window.
- **No value, default, env var name, parse rule or validation changes.** Engine default
  0.15, picker default 0.045, the `max(window, wide)` trial rule, the 1..99 cent bound
  and the pinned/unpinned split all stay exactly as they are.
- **No behavioural change** to KPI output, PnL crosscheck or fee math. `rebate_est`
  stays `None`, `total_with_rebate` stays `realized_pnl`, the fee tier math is untouched.
- Every `rebate` reference ends up either **kept with a written justification** in
  `docs/issues/708-noticed-but-not-touching.md` or removed. No reference is left
  unexplained.
- `scoring/allocate.py` is deleted: no tracked importer, no test, retired reward-pot
  model. The deletion is pinned by a `find_spec("scoring.allocate") is None` guard.

## Deliberate non-changes (a later reviewer must not "fix" these back)

- The **visible message strings** in `core_brain/quotes.py` that say "reward window" and
  "reward quote ... score N" are output the operator reads. Rewording them is a separate
  change to visible output; they are recorded in the ledger.
- `quotes.reward_score()` stays: a legacy diagnostic that feeds no sizing decision.
- `kpi.py`'s `rebate_est` / `rebate_est_note` / `total_with_rebate` keys stay: they are
  the emitted schema, and `None` is the honest value on graduated spread markets.
- `pnl_crosscheck.py`'s `CREDIT_TYPES` / `ACTIVITY_TYPES` / `maker_rebates` stay: real
  venue credits flow through them and `tests/test_pnl_crosscheck.py` covers them.
- `venue_fees.py`'s rebate sign-safety comment stays: the fee-tier math is load-bearing.
- `scoring/config.py`'s line "Maker rewards/rebates are $0.00 in graduated spread
  markets" stays: it is accurate.
- Historical records stay: old measured-run comments, the dashboard explainer line, the
  validation artifacts and the prompt that forbids inventing rebate income.
- `docs/issues/690-noticed-but-not-touching.md` was **pruned from the repo** in e2f648e
  ("prune stale worktrees and closed-issue artifacts" -- every closed issue's ledger was
  deleted). It is NOT resurrected here. #690 O4 is closed by this ticket's own ledger.

## Process

- No live command, no venue call, no write to `data/orders.db`.
- No direct push to `main`; the change ships through a PR.
- Targeted suites locally only; GitHub CI on Ubuntu and Windows is the merge gate.
- No `pytest` / `gh` / `git` step inside the operator's "How to verify".
