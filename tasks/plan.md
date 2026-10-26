# Plan — Issue #831: Closed-trades outcome shows wins and losses with exec-type pills

Branch: `i831/closed-trades-wins-losses-exec-pills` | Issue: #831
Size: **Standard** (2 production files, 1 test file, one shared-rule decision)
Task type: **Code + Design/UI** — Stitch dashboard surface, no backend change
Next: `/iii-build-plan` (Build). Then `iv-review-build-and-pr`.

## Problem

`stitchClosedTradesRows` counts execution *methods* (`mergedCount`, `stopLossCount`,
`graceExpiredCount`, `settledCount`) and paints them as `N Merged` / `N Stop` /
`N Expired`. A market with 2 profitable merges and 4 losing sales reads "2 Merged" and
hides the four losses. The ledger rows already compute a per-execution PnL but print the
exec type as unstyled text (`<span class="stitch-exec-type">`, which has no CSS rule), and
the expanded ledger repeats the parent row's Total Cost / Net Realized in a summary strip.

## Spec (embedded — no separate SPEC.md)

Prior planning artifacts in this repo are pruned once an issue closes (#794), and the
issue's acceptance criteria already are the spec, so the contract lives here instead of a
root `SPEC.md`.

**Goals**

1. Per-market OUTCOME BREAKDOWN counts money, not mechanics: green `N Wins`
   (`> 0`) and red `N Losses` (`< 0`); exactly `$0.00` counts as neither.
2. Every ledger row's EXEC TYPE is a pill: `Merge`, `Sale`, `Stop Loss`,
   `Grace Expired`, `Resolved`.
3. The expanded-ledger summary strip is gone; the parent row still shows Total Cost and
   realized PnL (`$` and `%`).

**Acceptance criteria** (mirrors the issue)

- [ ] Green Wins + red Losses per market; zero-PnL executions counted as neither.
- [ ] Every ledger row shows its exec type as a pill, with the five labels above.
- [ ] Summary strip removed; parent row's Total Cost and realized PnL unchanged.
- [ ] `python -m pytest -q tests/test_stitch_closed_trades.py` green.

**Edge cases locked**

- `realized_pnl` present but `0` → neither win nor loss, row prints `+$0.00`
  (explicit zero is never replaced by `proceeds - cost_basis`).
- `realized_pnl` absent or `null` → win/loss decided by `proceeds - cost_basis`
  (same rule the ledger row already applies, now shared).
- A market whose settlements are all `$0.00` → no win/loss pill; neutral
  `${executionItemsCount} Closed` pill, matching today's fallback wording.
- No settlements at all → the synthetic order fallback, `Merge` / `Sale` only.
- Parent `m.realized_pnl` is never re-summed from settlements and never derived
  from the new helper (#579).

**Out of scope:** PnL computation, open-orders / positions tables, backend method names,
the legacy Closed Trades renderer, the seven-column layout.

## Interfaces

| Symbol | Where | Contract |
| --- | --- | --- |
| `stitchSettlementPnl(s)` | `app.js`, directly under `closedTradeCostBasis` (line 9124 today) | `Number(s.realized_pnl)` when `!== null && !== undefined`; else `(Number(s.proceeds)||0) - (Number(s.cost_basis)||0)`. Pure, no DOM. |
| `.stitch-exec-type.<modifier>` | `stitchLedgerChildRow` (line 8954) | Display label → modifier map: `Merge→is-merge`, `Sale→is-sale`, `Stop Loss→is-stop-loss`, `Grace Expired→is-grace-expired`, `Resolved→is-resolved`; unknown label → no modifier. Object map, never a string array (taxonomy guard). |
| `.stitch-pill-count.win` / `.loss` | `prototype.css` (`.stitch-pill-count` at 1591) | Win takes the current `.merged` tones, loss takes the current `.stop-loss` tones. `.neutral` stays. |
| `.proto-body .stitch-ledger-subtable .stitch-exec-type` | `prototype.css` (subtable at 1742) | Neutral slate pill: shape shared with `.stitch-pill-count`, colour neutral so green/red keep one meaning (glossary: one tone, one meaning). |

## Tasks

Dependency graph: `T1 → T2`; `T3` independent; `T4` needs `T1,T2,T3`.

### T1 — Extract the money rule `stitchSettlementPnl` (S, `[Logic]`)
- Files: `dashboard/static/app.js` (only).
- Add the helper next to `closedTradeCostBasis`; `renderStitchClosedLedgerRows` calls it
  instead of its inline `s.realized_pnl != null ? … : proceeds - costBasis`. Behaviour
  byte-identical.
- Depends on: —
- Verify: `python -m pytest -q tests/test_stitch_closed_trades.py` (all currently green
  before and after; this task is a refactor, its proof is the unchanged suite).

### T2 — Outcome Breakdown counts Wins and Losses (S, `[Design/UI]`)
- Files: `dashboard/static/app.js` (`stitchClosedTradesRows`), `dashboard/static/prototype.css`.
- Delete the four method counters and their classification loop; count
  `winCount` / `lossCount` off `stitchSettlementPnl`.
- Markup: green `● N Win(s)` / red `● N Loss(es)`, neutral `${executionItemsCount} Closed`
  when both are 0. Title attributes in the existing style.
- CSS: `.stitch-pill-count.win`, `.stitch-pill-count.loss`; retire `.merged`,
  `.stop-loss`, `.grace-expired` count modifiers after a grep proves zero uses.
- Depends on: T1.
- Verify: page renders both pills for a mixed market; focused tests in T4.

### T3 — Exec-type pills and the summary strip (S, `[Design/UI]`)
- Files: `dashboard/static/app.js`, `dashboard/static/prototype.css`.
- `stitchLedgerChildRow`: pill class + label-derived modifier on `.stitch-exec-type`.
- Labels: `Pair Merge` → `Merge` (classification + both synthetic fallbacks),
  `Settlement` → `Resolved`. `Sale`, `Stop Loss`, `Grace Expired` unchanged; the
  classification chain and its order untouched.
- Delete the `stitch-closed-ledger-strip` block (Total Cost / Net Realized / merged, stop,
  grace and "0 Orphan / 0 Stops" badges). Keep the card, the table, `colspan="7"`.
- CSS: scoped neutral pill rule; drop `.stitch-closed-ledger-strip`, `.stitch-strip-*`,
  `.stitch-strip-metric b.pos/.neg`, `.stitch-strip-badges`, `.stitch-badge-outcome*`
  once a grep proves no `dashboard/static/*.js` emits them.
- Depends on: —.
- Verify: expand a row, five labels readable, no strip.

### T4 — Regression tests (S, `[Tests]`)
- Files: `tests/test_stitch_closed_trades.py`.
- Update the pair test: `Pair Merge` assertion → `is-merge` pill plus
  `"Pair Merge" not in html` (`"Merge"` alone would also match the old label);
  `1 Merged` → `1 Win`.
- Add: mixed market (2 profitable merges + 4 losing sells → `2 Wins` / `4 Losses`,
  no `Merged`); explicit `realized_pnl: 0` with non-zero proceeds − basis (counts as
  neither, row prints `+$0.00`); absent and `null` `realized_pnl` (fallback decides);
  all labels/modifiers (`merge`, `shadow_merge`, `sell`, `stop_loss_exit`, `aged_out` /
  `grace_expired`, `settlement`, `shadow_settlement`; `Settlement` absent as a label);
  synthetic fallback (paired merged / paired unmerged / solo merged / solo unmerged →
  Merge & Sale pills); strip removed with parent Total Cost and `m.realized_pnl` intact
  while the settlement sum differs from the parent value.
- Depends on: T1, T2, T3.
- Verify: focused suite green, and every new assertion fails if the behaviour is reverted.

**Checkpoints:** after T2 — the operator-visible half (Wins / Losses) is renderable;
after T4 — full suite green and the operator click path below.

## CodeRabbit plan intake (read once, not re-read by later stations)

- **Adopted:** (a) share one PnL rule between parent counts and ledger rows instead of
  reading raw `s.realized_pnl` for the counts — otherwise a settlement without
  `realized_pnl` counts as neither while its row shows a number; (b) preserve explicit
  zero; (c) neutral exec-type pill with a per-type modifier rather than green/red by type;
  (d) conditional win/loss pills + neutral fallback; (e) label-only renames, keep the
  classification chain; (f) delete strip and dead CSS after a grep; (g) its test list.
- **Rejected:** its two-phase, two-task-per-phase split with duplicated "prompt for AI
  agents" blocks (four tasks in this plan, no echo prompts); naming the helper
  `stitchSettlementPnl` was kept but the *ledger* call is a pure refactor, not a new
  behaviour; no new `is-*` colour rules were invented for exec types.
- **Verified against the code, not trusted:** `stitchLedgerChildRow` 8954,
  `renderStitchClosedLedgerRows` 8991, `closedTradeCostBasis` 9124,
  `stitchClosedTradesRows` 9128, strip 9280-9292, `.stitch-pill-count` 1591-1621,
  `.stitch-ledger-subtable` 1742, exports 11002-11003 — all exist as cited.
- **Stayed `[UNVERIFIED]` / resolved from code:** "Total row" in the issue's open
  questions is the expanded strip (no other row shows Total Cost / Net Realized);
  "Cell" has no `Sell` label in the codebase, so `Sale` stays; `Resolved` maps to the
  settlement methods; exactly `$0.00` counts as neither.

## One improvement proposal (evidence)

> "Today the OUTCOME BREAKDOWN column shows \"N Merged\" plus stop/grace pills: a market
> with 2 profitable merges and 4 losing sales reads \"2 Merged\" and hides the 4 losses."
> — issue #831

The fix also **deletes the second classification loop** rather than adding a third reading
of the same settlements: after T1–T2 the merge/stop/grace/settlement predicates live only
in the ledger builder, and the parent row asks one question (is this execution's money
positive or negative?). Classified as a simplification → adopted by default; two count
paths can no longer disagree about the same settlement.

## Risks

- `dashboard/static/*.js` is one 11k-line file with no module boundaries: the three
  Stitch builders share helpers, so a signature change to `stitchLedgerChildRow` ripples
  into both synthetic fallbacks. Mitigation: keep its signature, derive the modifier
  inside it from the label.
- The taxonomy guard is text-based; a modifier map must be an object, not an array of
  quoted strings.
- A grep-only CSS cleanup can delete a rule another page still uses; `.stitch-leg-pill`,
  `.stitch-pnl-val`, `.stitch-arb-badge`, `.stitch-status-badge` are explicitly excluded.
