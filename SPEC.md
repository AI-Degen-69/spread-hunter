# SPEC — #408: Re-check UMA resolution state for markets already quoting

Scope note: this file covers issue #408 only
(branch `i408/re-check-uma-resolution-state-for-markets-alread`).
It supersedes the #402 spec (done work). Deleted or superseded when the
next Standard/Large issue writes its own.

## Problem (operator words)

A market flips to `umaResolutionStatus=proposed` in the middle of a run and
keeps quoting — the per-cycle re-fetch reads the CLOB endpoint, which carries
no UMA field at all. The resting quotes sit on a market that is resolving.

Motivating episode, 2026-10-07, shadow run `shadow-01-prudent`
(`data/01_shadow_prudent_07-10_16-48.db`), market `cs2-tu-xdm-2026-10-07`:
quoted both legs at 16:49, venue flipped to proposed between 16:49 and 16:59,
rerank correctly dropped it at 16:59 — yet at 17:04 the session was still
`decide` + `submit` on it, quotes resting, `cancelled=0`.

## Goals

1. A market with resting quotes whose Gamma UMA status becomes
   proposed/disputed/resolved has those quotes cancelled within one poll
   cycle, with the discard reason named in the log and `market_events`.
2. The re-check uses the public Gamma read (CLOB carries no UMA field) and
   adds no signer/API-key dependency to the loop.
3. A market whose status stays clean is untouched by the new check (no extra
   cancellation, no behavior change on the happy path).

## Acceptance criteria (from the issue)

- [ ] A market with resting quotes whose Gamma UMA status becomes
      proposed/disputed/resolved has those quotes cancelled within one poll
      cycle, with the discard reason named in the log and `market_events`.
- [ ] The re-check uses the public Gamma read (CLOB carries no UMA field)
      and adds no signer/API-key dependency to the loop.
- [ ] A market whose status stays clean is untouched by the new check (no
      extra cancellation, no behavior change on the happy path).
- [ ] Targeted suites pass: tests/test_uma_resolution_gate.py
      tests/test_trader_loop.py tests/test_shadow_run.py.

## Established facts (verified from code, not assumed)

- UMA parsing exists: `parse_uma_resolution_status` /
  `extract_uma_resolution_status` (`market_resolution.py:98-141`).
- The existing open-state reader takes the FIRST row without checking its
  condition id (`market_resolution.py:414`: `parse_end_state(rows[0], ...)`).
  The new reader must not repeat this: a mismatched row is absent, not clean.
- Visit order today: `_visit_one` calls `seam.fetch_market(cid)` FIRST and
  returns ERROR on fetch failure before any cancellation
  (`trader_loop.py:1055-1069`). The UMA check goes before that fetch.
- Cancel-reason constants live beside each other
  (`trader_loop.py:74-77`: `CANCEL_NOT_QUOTED`, `CANCEL_PRICE_MOVED`,
  `CANCEL_REGATE_PAIR_COST`, `CANCEL_MARKET_DROPPED`).
- Optional seam ports are the established pattern: `flow_fn` is unset-safe
  (`trader_loop.py:504-509`); the shadow builder already wires `flow_fn` and
  `record_cancel` (`shadow_run.py:760-779`).
- `cycle_stream.emit` takes a free-form action string (`cycle_stream.py:364`);
  no event-kind filter to extend. Only `_make_logging_emit`
  (`shadow_run.py:410`) needs the readable discard line.
- Cache model exists: `AgedOutMarketStateCache`, 30s TTL, injectable clock,
  unreachable never cached (`single_buy_saver.py:1366-1423`).
- `log_market_event` / `reason_code` exist on the registry (per #402 spec;
  build re-confirms exact fields against `order_manager.py` BLOCKED usage).

## Edge cases

- Gamma unreadable/malformed/mismatched: fail OPEN — warn, continue the
  visit unchanged, retry next visit, cache nothing. A Gamma outage must never
  mass-cancel the book.
- Flagged result: terminal — pin it (no expiry) so later visits keep
  cancelling without a new read; cancel failures are reported and retried.
- `pending` rows are in flight: leave them; the next visit catches them as
  `open`.
- Empty-feed refresh keeps the previous universe (the 17:04 episode): the
  UMA check runs per visited market regardless of feed state.

## Out of scope

- Selection-time gate (#377), `_parse_market_row`, `fetch_pinned_market`,
  dropped-market cleanup, resolution sweep, #401 tape code.
- Any price/size/spread knob, enforce flags, money levers (frozen in
  CONSTRAINTS.md).
- Feed-absence cancellation beyond this case, any live position, `data/orders.db`.
