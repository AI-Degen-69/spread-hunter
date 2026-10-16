# Noticed but not touching — #619

Scope guard for #619. Every row: noticed code worth a future issue, left
unchanged by this plan. Status stays `open`, Resolved-by stays empty until a
follow-up issue claims the row.

| Noticed (owner: planner, 2026-10-16) | Why out of scope | Status | Resolved-by |
|---|---|---|---|
| Partial stop exit may strand the remainder: manager may not re-find the rest (`single_buy_saver.py:1658-1682`) | Execution-semantics fix, not stop math; CodeRabbit flagged, plan declines | open | |
| Rejected FOK response may record a full close (close accounting ignores `success`/`status`, `single_buy_saver.py:655-696,990-1018`) | Accounting change, separate risk decision | open | |
| No stream-driven per-tick evaluation (poll: 0.5s poll, 1s guardian, Trader visits) | New subscriptions + locking rules; separate work item | open | |
| `scripts/shadow_tournament.py` preset-cents export | Touch only if `test_shadow_tournament.py` fails | open | |
| `tests/test_sale_close_pricing.py` guard on retired sale-close fields | Widening forbidden by issue scope | open | |
| Dashboard display of the four new settings | Explicitly out of scope in issue research section | open | |
| `core_brain/unhedged_stop_loss.py` separate markout gate | Different stop, untouched by design | open | |

## Discovered during build (owner: builder, 2026-10-16)

Found while implementing T1.1/T2.1/T3.1 — none of these is in a task's file list, and
since a comment-only reference to a deleted field changes no runtime behaviour they were
left untouched. They are rows for Station VI, not for this build.

| Noticed (owner: builder, 2026-10-16) | Why out of scope | Status | Resolved-by |
|---|---|---|---|
| `core_brain/statistical_analytics.py:119-124` cites `stop_loss_trigger_usd_cents = 4.0` as "the system default in `core_brain/config.py`", and `:321-322` reasons about `hard_stop_bid` as "an absolute bid floor". Both fields are deleted, so a reader following either comment lands on nothing. `STOP_OFFSET_FALLBACK = 0.04` itself is unaffected — it is an assumption about a historical rescue close, not a live read | Comments only, no runtime effect; not in T1.1's or T3.1's file list. Retiring or re-basing the assumed reference is a measurement decision, not a stop-loss one, so it belongs in its own issue rather than this build | open | |
| `scripts/tournament_cockpit_server.py:53-97,180-182,198-242,324-326,1397,1459` still presents the retired knobs as tunable display copy ("`stop_loss_trigger_usd_cents` (4.5c static)", "`single_buy_grace_sec` (0s / zero patience)") | On the plan's untouched list. Nothing reads the fields so nothing breaks; an operator reading the cockpit is pointed at knobs that no longer exist | open | |
| `scripts/grace_sweep_report.py:3` opens by contemplating "A four-arm sweep of `single_buy_grace_sec`", the knob this issue retired | On the plan's untouched list. Docstring only; the script's timing question is now governed by `pairs_exit_window_sec` / `aged_out_rescue_lead_sec` | open | |
| `scripts/shadow_tournament.py:231-232` still maps `stop_loss_trigger_usd_cents` onto `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS` although no preset carries that key any more, so the branch is now unreachable (safe: guarded by `if ... in preset`) | On the plan's untouched list ("only if its tests fail" — they do not). `tests/test_dynamic_offset.py` now asserts the shadow child is never handed the retired var | open | |
| `.env.example` documents no stop-loss knob at all, so the four new settings have no template entry to copy, and the operator's own `.env:24` still sets the retired `HUNTER_SINGLE_BUY_GRACE_SEC`, which now warns and is ignored on every load | Not in the plan's file list. `.env` is the operator's file and is not edited by an agent; the walkthrough tells them to delete the line. Adding an entry-relative stop section to the template is a docs call | open | |
