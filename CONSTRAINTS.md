Branch: i717/remove-retired-reward-vocabulary | Issue: #717

# Constraints — #717 Remove the retired reward vocabulary from the quoting engine

The venues we trade are graduated spread markets: they pay **$0.00** in maker rewards and the
KPI report does not estimate them. The quoting engine still speaks that retired model's
language. This ticket is a wording-and-dead-code change only.

## The bar

- **The retired vocabulary is gone from the code tree.**
  `git grep -n "reward window\|reward quote\|reward minimum" -- core_brain/ tests/` returns
  nothing, and the case-insensitive form returns nothing either.
- **`quotes.reward_score()` is deleted** — the function, its docstring and its call site. No
  importer anywhere (`git grep -n "reward_score" -- core_brain/ tests/ scripts/ dashboard/`
  returns nothing).
- **The zero-allocation comment names no function that does not exist.** `reallocate` is
  gone from it, and the comment describes today's facts.
- **Every pinned test is re-pointed, never deleted.** The four transient-refusal pins
  (`tests/test_trader_loop.py` ×3, `tests/test_shadow_run.py` ×1) carry the new wording and
  every assertion stays.
- **No behaviour change beyond the text of the strings.** No default, env var name, price,
  size, gate, threshold, comparison or money-path message moves. No numeric literal in a
  message changes.
- **The visible messages keep their meaning.** The distance refusal still says how far from
  mid the quote sat and what the window is; the truncation refusal still carries the
  percentage, the ladder shares and the minimum; the price-risk refusal still carries the
  price, the ladder and the venue floor; the success reason still says where the quote sits
  and still carries the waiver suffix and the dynamic-offset tag.

## Deliberate non-changes (a later reviewer must not "fix" these back)

- **`core_brain/kpi.py`'s `rebate_est` / `rebate_est_note` / `total_with_rebate` stay.** They
  are the emitted schema and `None` is the honest value on graduated spread markets.
- **`core_brain/pnl_crosscheck.py`'s `CREDIT_TYPES` / `ACTIVITY_TYPES` / `maker_rebates`
  stay.** Real venue credits flow through them.
- **`reward_offset` / `min_reward_offset` / `bot_pos_skew_min_reward_offset_cents` keep their
  names.** They are config keys read by live code and by the dashboard; renaming them is the
  `docs/ideas/variable-names-refactor.md` ticket's job, not this one's.
- **The venue's own `rewards` payload fields keep their names.** `rewards.max_spread`,
  `rewardsMinSize` and `rewards_daily_rate` are the venue's field names, read from the
  market payload.
- **`docs/issues/708-noticed-but-not-touching.md` rows N1/N2/N6 keep their text.** They are
  the #708 record of what this ticket was published from. Only Station VI resolves rows.
- **No KPI / PnL cross-check / venue-fee contract moves.** Out of scope by the issue.

## #708 supersession (read this before "restoring" anything)

`CONSTRAINTS.md` for #708 said, in its deliberate non-changes: *"The visible message strings
in `core_brain/quotes.py` that say 'reward window' and 'reward quote ... score N' are output
the operator reads. Rewording them is a separate change to visible output"* and *"`quotes.
reward_score()` stays: a legacy diagnostic that feeds no sizing decision."*

**#717 is that separate change.** Both sentences are superseded by this file. #708's ledger
(`docs/issues/708-noticed-but-not-touching.md`, rows N1, N2, N6) is what published this
ticket. Do not read the #708 wording as a reason to put the strings or the function back.

## Process

- No live/opening command: no `quote`, no `complete`, no Trader loop, no dashboard START.
  No venue call.
- `data/orders.db` is the production registry: read-only, never rewritten.
- Shadow rehearsals use a fresh isolated DB under `data/`, never `data/orders.db`.
- Targeted suites locally only; GitHub CI on Ubuntu and Windows is the merge gate.
- No `pytest` / `gh` / `git` step inside the operator's "How to verify".
- Out-of-scope findings become an `open` row in
  `docs/issues/717-noticed-but-not-touching.md` with owner + when. Never touched mid-build.
- Anti-cheat: no skipped or disabled test, no deleted assertion, no silenced linter, no
  loosened threshold to get green.
- No new external dependency.
