# CONSTRAINTS — #925

Branch: i925/trim-open-positions-table | Issue: #925

## Zero regressions

- Targeted checks for the touched stitch positions code must pass:
  `python -m pytest -q tests/test_stitch_positions_marks.py tests/test_orders_trades_table.py tests/test_dashboard_server.py`.
- New behaviour needs a test that fails without the change.
- Classic-surface assertions (`positionsRows`, `OT_COLUMNS`, Orders, Active Markets, Closed Trades) stay untouched.
- Full-repo `python -m pytest -q` is the CI merge gate; do not run it locally as a review step.
- A live tournament run may report `block_real_state_writes` teardown errors naming `data/` or `runtime/` files; re-read the failure list and do not treat those as this change.

## Deliberate choices — do not "fix" these

- Keep existing sort keys; do not renumber them. Reject removed keys with a stitch-only allowlist.
- Remove `.proto-body .stitch-mark-val` only after a final `app.js` grep shows no users; keep `.stitch-pair-cost`, `.stitch-mid-sub`, `.stitch-status-badge`, `.stitch-status-dot`, `.stitch-pnl-*`.
- Keep the `is-hedge-pending` row tint and the row-tint-only signal for a lone held leg with a resting hedge; extend the same row-tint treatment to `is-unpaired` without adding a column or making color the only signal.
- HELD INVENTORY alignment and `Cost: $XX.XX` are positions-only (`stitch-positions-held` / positions-only option on `dualLegLine`); Orders keeps its existing layout and copy.
- Current price remains live mid when fresh, otherwise quote mid; it is not switched to ask without a separate issue/decision.
- Keep `positionMarkValue` unchanged; the unrealised number remains par-based.
- CodeRabbit's plan is input, not authority. Its undefined "Assumption 5" was resolved locally: missing value => `--`, empty percentage, `flat`.
- No new dependencies, no schema changes, no public API changes outside the stitch positions surface.

## Anti-cheat

- Never skip, delete, weaken or rewrite an assertion to make a run green.
- Never suppress linters/type checks or add ignore comments.
- Do not delete classic tests or change the classic table to satisfy stitch tests.

## Live-safety / money

- No order-placing or venue-writing command may be run for this issue.
- Dashboard verification is read-only; do not rewrite `data/orders.db` or runtime state.
- The Owner's verification is hands-on dashboard behaviour, not test counts.

## Simplicity

- No new abstractions beyond one both-legs price source and one P&L formatter shared by render and repaint.
- No `proto-body` guard around all of `paintLiveMarks`; classic repaint depends on the shared path.
