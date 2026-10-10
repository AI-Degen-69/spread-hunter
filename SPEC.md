# SPEC — #459: Live stream TRADES tab shows trades in plain English

## Goals

1. Real trade activity (fills, exits, completions, merges, redeems) appears in the
   dashboard TRADES tab regardless of which service emitted it.
2. Every stream row reads as one plain-English sentence with local time and market
   name; no service abbreviations, action codes, or raw slugs on the main line.
3. Decide rows show planned prices or the skip reason in words.
4. Trading behavior, execution, and accounting are unchanged — telemetry only
   observes existing outcomes.

## Acceptance criteria

- While trades happen, TRADES never shows an empty state; fills, exits,
  completions, merges, and redeems each render a sentence.
- Main lines contain no `_`, no `[DECIDE`/`[QUERY`/`[FILTER` tags, no raw slugs.
- Decide rows: `intent_count > 0` → "Decided to quote …" with prices;
  `intent_count == 0` → "Skipped … because …" with the reason in words.
- Every inventoried action has a sentence; unknown actions fall back to a
  plain-words prefix fallback, never a code.
- ALL, MARKET FILTER, ALERTS, CLEAR FEED, and AUTOSCROLL behave as today.
- New tests fail without the change and pass with it.

## Edge cases (from issue analysis)

- No producer emits `pairs_*` today; merge/redeem truth lives only in the
  relayer helper `_submit_and_log` — emit there, not in the lifecycle pass.
- Decide events predate submission and also fire in dry-run: "Decided to quote",
  never "Quoted at".
- Exit size/price may be estimates → "about" wording.
- `transaction_hash` may hold a relayer id, not a chain hash — keep hash and id
  separate, never describe as on-chain proof.
- Both the poll loop and the Trader reconcile; the first to insert a fill
  reports it — wire the observer in both or some fills go missing.
- `lifecycle_*` waiting events repeat every poll: keep them out of TRADES,
  do not change their volume.
- The event buffer must retain `extra` or sentences lose market names/reasons.

## Out of scope

- Stream transport and replay (`dashboard/server.py`) — untouched.
- `single_buy_saver.py` execution/lifecycle results — untouched.
- `global_stop_loss.py` `disable_rotation` vs `can_rotate` mismatch and
  `guardrail_alert` never reaching the ring — separate follow-up issue, not this
  change (ALERTS semantics stay as-is).
- Any change to quoting, sizing, strategy, reconciliation accounting, or venue calls.
- `data/orders.db` production registry.
