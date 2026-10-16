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
