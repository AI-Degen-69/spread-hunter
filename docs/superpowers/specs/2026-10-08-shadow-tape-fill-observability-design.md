# Shadow Tape Fill Observability

**Date:** 2026-10-08
**Status:** Approved design; awaiting spec review
**Scope:** Shadow rehearsal trade-tape ingestion and operator diagnostics

## Problem

Shadow fills are credited by `core_brain/shadow_fills.py` only when trade volume for
the order's exact token and price consumes its recorded queue position. This avoids
inventing fills from a price move or a book level disappearing through cancellations.

The October 8 Dodgers–Braves rehearsal exposed an observability failure. Its five-share
DOWN order at `$0.41` recorded about 22,425 shares ahead. Queue marks recorded zero
traded volume at that level; the order was later cancelled as `not_quoted` when the
market entered the decided-price range. The operator observed trading at the `$0.41`
level on Polymarket. The rehearsal did not retain enough information to distinguish:

- a successful tape read with no qualifying prints;
- prints rejected because token, side, price, timestamp, or payload did not match;
- incomplete pagination or a failed read;
- prints that consumed queue but did not reach the simulated order.

`markets.recent_trades` currently returns an empty mapping for some read failures, and
the shadow path persists zero traded volume without recording whether the read was
complete. Therefore “no fill” can look like “no trade,” even when the actual cause is
missing or unusable input.

## Goals

- Preserve queue-aware, tape-confirmed shadow fills. A market price touch alone is not a
  simulated fill.
- Distinguish a complete read with no matching prints from unavailable, malformed,
  partially read, or truncated tape.
- Explain per market and per cycle why observed prints did or did not consume an order's
  queue and create a fill.
- Make the evidence visible both in the shadow-run log and on the dashboard using the
  same shadow store.
- Keep all simulation changes out of the live fill engine and preserve its venue-confirmed
  fill invariant.

## Non-goals

- Do not infer or guarantee a live fill from a last-traded-price display, best-bid change,
  book-level disappearance, or time resting.
- Do not ignore queue priority or use a probabilistic fill fallback when the tape cannot
  be read.
- Do not change live order placement, live reconciliation, quote selection, or lifecycle
  policy.
- Do not claim that a shadow fill predicts the exact execution of a live order. Public
  tape and order-book snapshots cannot reveal the simulated order's true venue queue
  priority.

## Chosen behavior

The user selected the strict tape-and-queue model:

1. A qualifying print must identify the same token and exact tick-normalized order price
   and represent trade flow capable of reaching a resting BUY.
2. Qualifying shares consume the measured queue ahead first.
3. Only remaining qualifying shares may credit the simulated order, up to its remaining
   size, at the order's limit price.
4. A price crossing or book update without qualifying trade evidence does not create a
   fill.
5. When tape evidence is unavailable or incomplete, the fill model credits nothing from
   that read and explicitly reports unknown/incomplete evidence rather than a measured
   zero.

This can conservatively miss fills if Polymarket's public tape omits or delays prints.
That limitation must be visible; it must not be hidden behind a synthetic fill.

## Design

### Detailed tape-read result

Add a typed result for the shadow fill path containing:

- normalized `volume_by_token_price`, consumed by the existing pure queue model;
- overall read status and the status of each tape walk:
  - `complete`: every requested walk finished and produced qualifying volume;
  - `empty`: every requested walk finished, but no qualifying volume was found;
  - `partial`: at least one requested walk failed, or malformed rows mean its evidence
    cannot be considered complete, while another walk or valid rows remain usable;
  - `truncated`: the bounded page limit was reached before a walk established that it
    had read the full available window;
  - `unavailable`: no requested walk returned usable evidence;
- status for each tape walk (taker view and maker-side supplement);
- page and row counts, time coverage, accepted qualifying volume, and rejected-row
  counts grouped by reason. Expected non-qualifying trades (wrong aggressor side or a
  different price/token) are not malformed; unreadable or invalid rows (missing identity,
  side, token, timestamp, non-finite price, or non-positive size) make that walk partial;
- a concise, sanitized failure reason when a request or payload cannot be read.

Keep the current `recent_trades(...) -> dict` surface as a compatibility wrapper where
existing callers require it. The shadow runner should use the detailed result so it can
retain diagnostics without changing live execution semantics. Do not log credentials,
headers, or full trade payloads.

### Settlement and shadow-store data flow

1. For each market visit, `settle_market` performs one detailed public tape read and
   passes only its normalized qualifying volume to `credit_fills`.
2. Existing exact-price queue priority, partial-fill accounting, de-duplication, and
   fill-at-limit recording remain the fill rules.
3. Persist one compact diagnostic record per market and cycle in that run's explicit
   shadow store. The record associates status and counters with market, cycle, run ID,
   and observation time. It must never write to `data/orders.db`.
4. If the tape read is unavailable, queue state remains unchanged for that read and no
   fill is credited. The diagnostic record marks the evidence unavailable; it is not
   represented as zero observed market volume.
5. If one tape walk succeeds and a supplemental walk fails, preserve valid volume from
   the successful walk, mark the combined read `partial`, and report which walk failed.
   Re-reading pages must not duplicate previously accepted trades.

### Operator reporting

- The shadow-run log emits one concise per-market tape summary per cycle, including
  status, pages/rows, qualifying prints/volume at resting prices, and rejected-row
  counts. Failures include a concise reason and do not masquerade as an empty read.
- The dashboard shows the same per-market status and evidence counts alongside that
  market's shadow activity. It clearly distinguishes `empty` (read completed; no
  qualifying prints), `partial`/`truncated`, and `unavailable`.
- Existing order status and lifecycle events remain separate from tape-health status:
  tape evidence is not a fill and a tape-read failure is not a market refusal.

## Error handling and safety

- Validate HTTP success, response shape, row side, token identifier, finite positive
  price/size, timestamp, and pagination completeness before counting a print. Report
  ordinary non-qualifying trades separately from malformed rows that weaken evidence.
- Keep failures explicit and sanitized. Do not convert request, decode, or malformed
  response failures into a successful-empty status.
- A bounded page limit remains; reaching it with more data available is `truncated`, not
  `complete`.
- De-duplication keys and per-cycle `seen` state must prevent the same print from being
  counted twice, including when one walk is retried after a failure.
- No new signing client or venue write is introduced. Shadow continues to use its
  deny-by-default client and explicit non-production DB path.
- `core_brain/live_fill_engine.py` and live reconciliation remain unchanged.

## Validation

### Deterministic tests

- Exact token/price SELL prints consume queue before partially and fully filling an order.
- A crossing price or a print for another token, price, or non-qualifying side does not
  fill the order.
- Duplicate rows and repeated reads do not double-count volume.
- Missing/invalid fields are counted by rejection reason and cannot create a fill.
- Empty, failed, partial, and page-truncated reads have distinct statuses.
- One failed tape walk does not discard qualifying volume from a successful walk.
- Dashboard and run-log summaries report the same persisted diagnostic record.
- Shadow writes remain confined to the supplied shadow store; live fill behavior remains
  venue-confirmed only.

### Hands-on operator verification

Run a time-boxed shadow rehearsal with a new explicit store and open the dashboard against
that same store. For each market, compare the tape status and accepted print/queue counts
with the order's queue and fill rows:

- A successful read with no qualifying prints says `empty`, not unavailable.
- A failed or incomplete read says `unavailable`, `partial`, or `truncated`, with its
  reason; it must not imply no market activity.
- Matching volume below queue ahead reduces the remaining queue but leaves fill size
  unchanged.
- Matching volume beyond queue ahead produces a simulated fill at the resting limit.

No live `quote`, Trader, `complete`, or dashboard **START** action is part of verification.
