# Stage 2–4 Architecture — Fill Detection, Exit, Completion

**Author:** Prime. **Date:** 2026-08-16. **Branch:** `session-66-live-exec-loop` (at `bd1f235`).
**Status:** written, pending `/plan-eng-review` and `/plan-devex-review`.

Stages 0 and 1 are closed: the relayer submit path is crash-safe and audited, and `mergePositions`
is implemented with pre-flight guards that behave identically in dry run and live. Stage 5, the
automated quoting loop, remains out of scope for this phase.

---

## 1. The decision: REST polling, not the WebSocket user channel

### Evidence

| Fact | Tag | Source |
| --- | --- | --- |
| `py_clob_client_v2` contains no WebSocket client, class, or method | MEASURED | `grep -ril "websocket\|wss://"` over the installed package returns nothing |
| `get_order(order_id)` -> `GET /data/order/{id}` | MEASURED | `client.py:529`, `endpoints.py:62` |
| `get_open_orders(OpenOrderParams)` -> `GET /data/orders`, auto-paginated | MEASURED | `client.py:534`, `endpoints.py:65` |
| `get_trades(TradeParams)` -> `GET /data/trades`, auto-paginated, filterable by `maker_address`, `after`, `before` | MEASURED | `client.py:577`, `endpoints.py:67` |
| Both existing WebSocket consumers in this repo use the **public market** channel, unauthenticated | MEASURED | `scripts/record_books.py:42,316-319`; `strategy/live_exec.py:1111-1125` |
| User channel: `wss://ws-subscriptions-clob.polymarket.com/ws/user`, subscribe `{"auth": {"apiKey", "secret", "passphrase"}, "type": "user"}`; emits Order events, Trade events (status `MATCHED`/`MINED`/`CONFIRMED`/`RETRYING`/`FAILED`), and PING/PONG every 10 s | MEASURED | `docs.polymarket.com/api-reference/wss/user` |
| CLOB general **9,000 req / 10 s**; `/book`,`/price`,`/midpoint` 1,500 / 10 s; balance GET 200 / 10 s. Data API general 1,000 / 10 s, `/trades` **200 / 10 s**, `/positions` 150 / 10 s. Relayer `/submit` **25 / 1 min** | MEASURED | `docs.polymarket.com/api-reference/rate-limits` |

Both figures previously tagged ASSUMED are now measured from Polymarket's own reference. Two of the
four arguments below weakened as a result, and the reasoning is restated honestly rather than
preserved.

### Why polling wins

1. **REST is mandatory either way — this is the argument that decides it.** A WebSocket cannot
   replay what it missed while the process was dead. Any WebSocket design still needs
   `get_open_orders` + `get_trades` reconciliation on every restart. Polling is not the alternative
   to WebSocket; it is the floor underneath it. Building the floor first is strictly ordered work.
2. **~~Hand-rolling L2 HMAC signing~~ — WITHDRAWN.** The subscribe payload is the three L2
   credentials in plain JSON, and `_client()` already derives them at `strategy/live_exec.py:90` via
   `create_or_derive_api_key()`. There is no per-message signing. What remains is a socket client
   (the `websockets` package is already a dependency, used in `scripts/record_books.py`), a
   reconnect policy, and gap detection — real work, but far less than assumed.
3. **Latency does not pay here.** The fill signal triggers a merge or an exit. A merge's value is
   `1.00 - pair_cost` and does not decay with time; the stop-loss fires on `pair_cost >= 0.995`, a
   condition that persists rather than flickering. Sub-second detection buys nothing over 5-second.
4. **Silent failure is the real risk.** A dead socket looks identical to a quiet market. A failed
   HTTP request returns a status code. With `N_real = 0` — no real fill ever observed — the first
   live fills must be detected by the mechanism that fails loudly.
5. **Rate limits are not a constraint.** A 5 s cadence is 2 `/trades` calls per 10 s against a
   documented 200, and 2 CLOB calls against 9,000. Polling has roughly 100x headroom, so the
   original worry that motivated the WebSocket question does not apply.

**Revisit criterion:** add the user channel once Stage 2 is proven, as an *accelerator layered over*
the reconciliation loop — never as a replacement for it. Its Trade event carries
`MATCHED/MINED/CONFIRMED/RETRYING/FAILED`, which is richer than polling infers, and that is the
reason to add it later. Not latency.

---

## 2. Shared foundation: the order registry

All three stages depend on one thing that does not exist yet — a durable record of *our* live
orders, keyed by venue order id.

`run/live_orders.json` is an append-and-update audit log for relayer transactions. It is the wrong
shape for order state: it is a flat array scanned linearly, and it has no concept of an order's
lifecycle. Do not extend it for this.

**Build `strategy/order_registry.py`** — SQLite in a **new `run/live.db`**, not `run/fleet.db`.
`fleet.db` holds simulator output, and `AGENTS.md:73` already warns that mixing configs in one
database produces silently invalid data. Live-money state and simulated state must not share a file.

### `orders`

| column | meaning |
| --- | --- |
| `id` | **local uuid4, primary key — assigned by us before the order is sent** |
| `order_id` | venue id, nullable, unique when set — filled in from the POST response |
| `condition_id`, `token_id`, `side` | what and where |
| `price`, `original_size` | as posted |
| `size_matched` | **derived: `SELECT SUM(size) FROM fills WHERE order_uuid = id`. Never written directly.** |
| `status` | `pending` / `open` / `partial` / `filled` / `cancelled` / `unknown` |
| `posted_ts`, `last_polled_ts` | staleness and reconciliation windows |
| `pair_id` | groups the UP and DOWN legs of one intended pair |
| `max_pair_cost_at_post` | the threshold this pair was quoted under |

**Why the key is local, not the venue's.** The invariant is that a row exists *before* the order is
sent — but the venue assigns `order_id` and it does not exist until the POST returns. Keying on it
makes the invariant unsatisfiable: a placement that times out leaves a live order at the venue with
no row anywhere. That is the Stage 0 unrecorded-broadcast defect one layer up. So: write the row
with a local uuid and `status="pending"`, send, then attach `order_id` — exactly the shape
`_log_order` proved.

**Orphan adoption is therefore mandatory.** Reconciliation must be able to match a venue order that
has no local row, on `(token_id, price, original_size, posted_ts ± window)`, and either bind it to a
`pending` row or record it as unattributed. Without this, a timed-out placement is invisible forever.

**`max_pair_cost_at_post`** exists because Stage 3 compares live `pair_cost` against the threshold —
and config can change between posting and exit. The pair must be judged by the rule it was quoted
under, not the rule in force at exit time.

### `fills` — evidence, not inference

| column | meaning |
| --- | --- |
| `trade_id` | venue trade id, primary key — the dedupe key |
| `order_uuid` | FK to `orders.id` |
| `size`, `price`, `venue_ts` | as reported by the venue |

`size_matched` must be a sum over deduped rows, never a snapshot value copied into a mutable column.
A stale paginated response can otherwise make it *regress* — and Stage 4 sizes its taker cross from
that number, so a regression under-hedges with real money. Stage 0's lesson was evidence over
inference; this is the same lesson applied to fills.

---

## 3. Stage 2 — fill detection (read-only, opens no exposure)

A `poll` subcommand and a reusable `reconcile_orders()` function.

1. **Reconcile.** Call `get_open_orders()` once. Any registry row marked `open` that is absent from
   the response is no longer resting — it filled or was cancelled. Disambiguate with
   `get_trades(TradeParams(maker_address=..., after=last_polled_ts))`. A registry row is only ever
   moved to `filled` on **trade evidence**, never on absence alone.
2. **Partial fills are first-class.** `size_matched < original_size` is the normal case for a maker.
   Store it; do not round it to filled/unfilled. Stage 4 sizes its taker cross from exactly this
   number.
3. **Cadence.** Default 5 s, configurable. One `get_open_orders` plus at most one `get_trades` per
   cycle. Against `fleet.py`'s existing `REQ_PER_SEC = 2.0` budget this is negligible.
4. **Backoff.** On `429` or any 5xx: exponential backoff, cap 60 s, log every transition. Never a
   bare retry loop.
5. **Restart correctness — overlap, do not compute an exact boundary.** On start, reconcile before
   anything else. Query `get_trades(after = last_polled_ts - 60s)` and dedupe on `trade_id`.

   Do **not** query `after = last_polled_ts` and call it exact. `last_polled_ts` comes from our
   local clock; trade timestamps come from the venue's, and Session 65's NTP sampling measured that
   this skew is real here, not hypothetical. Add an unknown inclusive/exclusive boundary on `after`
   and trades landing in the boundary second are silently dropped or double-counted. An overlapping
   window with an idempotent dedupe key has neither failure mode, and costs one extra page.
6. **Read-only.** Stage 2 places no order, cancels nothing, sends nothing. It only observes and
   records.

**Acceptance:** a simulated fill sequence replayed through fixtures produces the correct registry
transitions; a mid-sequence restart produces the same end state as an uninterrupted run; a `429`
produces backoff, not a crash.

---

## 4. Stage 3 — stop-loss / naked exit (closes exposure)

Port the trigger at `strategy/sweep.py:798-825`. It fired 16 times across 26,777 pairs, all on
`pair_cost >= 0.995` (`max_pair_cost`), and cost 3.67c per exit against 3.68c gained per completed
pair. **Do not redesign it. Port it.**

1. Read the one-sided pair from the registry via `pair_id`.
2. Evaluate the same condition against the live book.
3. On trigger: cancel the resting leg, then market-sell the filled leg.
4. Both actions write to the registry and to `run/live_orders.json` under the Stage 0 pattern.
5. Guard: refuse to sell more than the registry says is held. Same fail-closed shape as `merge`.

**The ordering rule that must not be broken:** cancel first, then sell. Selling first leaves a live
resting order that can fill into a position just closed, re-opening exposure at the worst moment.

**And re-read state between the two.** A successful cancel does not mean the pair is still
one-sided. Between the cancel returning and the sell being sent, the *other* leg can fill — the
cancel may even have raced a match that already happened. The pair is now complete and worth $1.00
at merge. Market-selling one leg of it converts a mergeable pair into a realized loss, which is the
worst outcome available on this path.

So: cancel → re-read the pair from the registry and the venue → if the pair completed, **abort the
sell and hand it to `merge`**. Only sell if the pair is still genuinely one-sided.

**Acceptance:** a fixture at `pair_cost = 0.995` fires; at `0.994` does not; a cancel that fails
aborts before the sell and leaves a recoverable state; **and a fixture where the second leg fills
between cancel and sell aborts the sell and routes to merge.**

---

## 5. Stage 4 — second-leg completion (closes exposure)

When one leg fills and `pair_cost` remains below the threshold, cross the book for the other leg.

1. Size from `size_matched`, never from the intended size.
2. Compute the taker cost at the current ask before sending. Refuse if the resulting `pair_cost`
   exceeds `max_pair_cost` — that is the stop-loss's job, and this path must not do its work badly.
3. Enforce `MAX_ORDER_USD` and the idempotency guard already built in Stage 1.
4. On success the pair is complete and becomes eligible for `merge`, closing the loop end to end:
   detect -> complete -> merge -> cash.

**Acceptance:** completion refuses when the cross would breach `max_pair_cost`; the completed pair
is picked up by `merge`'s pre-flight as fully held on both legs.

---

## 6. Operability — a first-class requirement, not polish

Stage 2 produces the first long-running process the Owner has to operate. It must answer, without
reading code:

1. **Start / stop.** One command each. A `SIGTERM` completes the current cycle and exits cleanly.
2. **What is it doing right now.** A status line per cycle: orders tracked, last poll, next poll,
   consecutive errors.
3. **What did it do overnight.** An append-only event log, one line per state transition, with
   timestamps. "Nothing happened" must be distinguishable from "it was dead."
4. **How do I know it is stuck.** A heartbeat written every cycle. Absence is the alarm.
5. **How do I stop it safely mid-flight.** `KeyboardInterrupt` follows the Stage 0 pattern already
   proven in `_submit_and_log`: record state, then exit.

---

## 7. Build order and gates

| Stage | Opens exposure? | Gate before starting |
| --- | --- | --- |
| Registry + Stage 2 | no — read-only | this document reviewed |
| Stage 3 | closes | Stage 2 approved and a restart proven correct |
| Stage 4 | closes | Stage 3 approved |
| **4.5 — one manual live order** | **OPENS, ~$1** | Stages 2-4 approved **and explicit Owner sign-off** |
| Stage 5 | **OPENS** | out of scope this phase |

Nothing in Stages 2-4 places an order that opens a position. Stage 4 crosses to *complete* a pair
that is already half-open, which reduces exposure. This is consistent with the staged exposure rule
in `AGENTS.md`.

## 7.1 Stage 4.5 — the first real fill

Stages 2-4 cannot be tested against a real fill. Stage 2 is read-only and Stage 5 is out of scope,
so nothing in this phase ever places an order: the registry stays empty and every path is validated
against replayed fixtures only. Shipping straight to Stage 5 means the first contact with the
venue's actual fill semantics happens with the machine running unattended.

So, after Stage 4 and before Stage 5: **a `quote` subcommand that places exactly one order.**

1. Minimum venue size (5 shares limit / $1 market), capped by `MAX_ORDER_USD`, one order only —
   no loop, no second quote until the first resolves.
2. `--live` requires Owner sign-off for this command specifically, per `AGENTS.md` Safety item 4.
3. The Owner watches it. This is a supervised experiment, not an unattended process.
4. It exercises the whole chain end to end with about a dollar at risk: **post → detect → complete
   → merge → cash.** Every assumption in this document either survives that or does not.
5. Every stage's guards and the registry invariant apply unchanged. If the order fills one-sided,
   Stage 3 and Stage 4 are the things being tested.

This is the cheapest possible way to find out whether the design is right. `N_real = 0` today; the
purpose of 4.5 is to make it 1 under supervision rather than discovering the answer at scale.

## 8. The Data API — a third source we were not using

`https://data-api.polymarket.com` is a distinct API group from the CLOB, and
`py_clob_client_v2` does not wrap it. It exposes user-level post-trade activity:
`/trades` (200 req / 10 s) and `/positions`, `/closed-positions` (150 req / 10 s each).

Two consequences for Stage 2:

1. **`/positions` is an independent check on the registry.** The registry records what we believe we
   hold; the Data API reports what the venue says we hold. Reconciling the two catches a whole class
   of bug that neither source can catch alone — and `merge`'s pre-flight already needs a truthful
   balance, which it currently gets from the CLOB balance endpoint (200 req / 10 s).
2. It is a plain REST call with no SDK wrapper, so it costs a `urllib` request and a parse. Add it
   as a reconciliation cross-check in Stage 2, not as the primary fill signal.
