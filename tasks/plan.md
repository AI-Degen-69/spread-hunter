# Plan: Issue #306 — Single-buy rescue consumes 49% of the strategy's profit

**Branch:** `feat/306-rescue-exit-forensics` | **Issue:** #306

**Size tier:** Standard — one new read-only report script + one small behavioural
instrumentation change (persist the route reason) + one rehearsal/verification
document. **Task type:** Code + Measurement. **No rescue-policy change ships.**

## Locked constraints (from the issue + CodeRabbit plan)

- Do NOT change `should_exit()`, drift thresholds (`single_buy_max_loss_pct=0.10`,
  `single_buy_max_loss_usd=0.045`), grace defaults, `pairs_exit_window_sec=900`,
  or the route order (complete → drift → hold → expiry). The fail-closed order stays.
- The report script is **read-only**: SQLite stores opened in `mode=ro` URI
  (`scripts/grace_sweep_report.py::_ro` is the pattern). Never write to either store.
- Never commit `data/` files. The findings doc carries aggregate report output only.
- New/changed behaviour needs a test that is RED without the change.
- Anti-cheat: no skipped tests, no weakened assertions.

## Verified seams (all read verbatim from the repo, zero guesswork)

- `scripts/grace_sweep_report.py` — read-only report pattern: `_ro()` ro-URI
  connection, argparse, limitations stated in the docstring.
- `core_brain/single_buy_saver.py` — `_route_pair()` sets
  `res["reason"] = "adverse_drift" | "grace_expired"` **in the returned dict only**;
  `exit_single_buy(...)` writes the close via `_record_exit_close()` →
  `registry.log_close(CloseRecord(...))`. `CloseRecord` has **no** reason field.
- `core_brain/order_registry.py` — `closes` table (line 283) has no reason column;
  migration pattern exists: `cols` check + `ALTER TABLE closes ADD COLUMN ...`
  (tx_hash/run_id precedents at lines 492-494). `log_close()` INSERT is explicit-column.
- `core_brain/shadow_exec.py` — `queue_marks` table: `ts, condition_id, market_slug,
  token_id, price, level_size, traded, cancel_decay, queue_minutes, run_id,
  best_bid, best_bid_size` (best_bid added by migration; older rows may be NULL).
- `scripts/book_tape_recorder.py` — separate store, `book_samples` table with
  `ts, run_id, condition_id, market_slug, tick, best_bid_up, best_ask_up,
  best_bid_down, best_ask_down, mid_up, mid_down, mid_sum, touch_pair_cost, ...`.
- `core_brain/market_resolution.py` — close method value `shadow_settlement`.
- Config fields verified: `pairs_exit_window_sec`, `single_buy_grace_sec`,
  `single_buy_max_loss_pct`, `single_buy_max_loss_usd` in `core_brain/config.py`.
- Callers of `exit_single_buy`: `core_brain/single_buy_saver.py::_route_pair` and
  `core_brain/stray_guard.py` — the reason parameter must default to `None` so
  stray-guard keeps working.
- Stores present locally (operator-owned, git-ignored): `data/01_shadow_12-09_00-58.db`
  (shadow-01) and book-tape stores `data/13_booktape_*.db`, `data/16_booktape_grace.db`.
- Findings-doc structural precedent: `docs/runs/2026-09-02-run153-grace-sweep.md`.

## Assumptions & risks

- The local `data/01_shadow_12-09_00-58.db` is assumed to be the shadow-01 store
  named in the issue; it is git-ignored and read-only to this work.
- No same-window book tape for shadow-01 is known to exist → Question 1 may be
  "unanswerable from this store"; the report must emit that verdict explicitly.
- `queue_marks.best_bid` is NULL on older rows → classifier has an `unresolved` path.
- Phase 3's rehearsal step (shadow_run + book_tape_recorder) reaches the venue's
  data API in shadow mode but spends nothing; it is listed as an operator-run step
  per repo safety rules, NOT executed by the agent without a go-ahead.

## Tasks (dependency order)

### T1 — Read-only rescue exit report (`scripts/rescue_exit_report.py`) [x]
- **Size:** M | **Domain:** Measurement tooling | **Helper:** `test-driven-development`
- **Files:** `scripts/rescue_exit_report.py` (new), `tests/test_rescue_exit_report.py` (new)
- **Build:** ro-URI registry connection; optional `--booktape` path; `--top N` (default 4).
  Select `single_buy_exit` + `shadow_settlement` closes; join fills by
  `fills.order_uuid = orders.id`; per exit emit shares, paid/share, sold/share,
  P&L, fill-to-exit seconds, P&L share of total rescue loss; summary table of
  path/n/PnL/capital; flag `shadow_settlement` legs that were one-sided and aged
  past `pairs_exit_window_sec` before market end (the fail-closed gap).
  Bid-path classifier from `queue_marks.best_bid` (+ optional `book_samples`):
  drift threshold from run-configured dollar/pct values → `late_trigger` /
  `gapped` / `unresolved`. Question 1 = sampled upper bound vs light-leg quote
  price within exit window, else "unanswerable from this store". Question 3 =
  quote-time feature comparison vs merged pairs + absent-fields list + n=4 caveat.
- **Depends on:** —
- **Verify:** focused suite `python -m pytest -q tests/test_rescue_exit_report.py`
  — synthetic tmp stores: loss ranking, all three classifications, NULL best_bid,
  unanswerable verdict without book tape, Question 1 upper bound, aged-out
  settlement detection, and **both stores byte-unchanged after a run** (assert
  no write: open ro and compare `PRAGMA schema_version` + row counts). RED first.

### T2 — Persist the route reason on `single_buy_exit` closes [x]
- **Size:** S | **Domain:** Instrumentation | **Helper:** `test-driven-development`
- **Files:** `core_brain/single_buy_saver.py`, `core_brain/order_registry.py`,
  `scripts/rescue_exit_report.py`, `tests/test_dual_stop_loss.py`,
  `tests/test_single_buy_saver.py`
- **Build:** `exit_single_buy(..., reason: Optional[str] = None)`; `_route_pair`
  passes `"adverse_drift"` / `"grace_expired"`; `_record_exit_close` writes it only
  after a successful sale. `CloseRecord.reason: Optional[str] = None` field +
  nullable `closes.reason` column via the existing `PRAGMA table_info` migration
  pattern; `log_close` INSERT extended. Report prefers a recorded reason over the
  reconstructed classification. **No threshold, grace, window, or route-order change.**
- **Depends on:** T1 (report consumes the reason)
- **Verify:** focused suites `tests/test_dual_stop_loss.py`,
  `tests/test_single_buy_saver.py`, `tests/test_rescue_exit_report.py` —
  reason persisted on drift + expiry exits; reasonless callers (stray-guard path)
  still record; old stores without the column still open; route order
  completion → drift → hold → expiry unchanged. RED first.

### T3 — Findings document `docs/runs/2026-09-29-shadow01-rescue-exits.md` [x]
- **Size:** S | **Domain:** Documentation | **Helper:** —
- **Files:** `docs/runs/2026-09-29-shadow01-rescue-exits.md` (new)
- **Build:** follow `2026-09-02-run153-grace-sweep.md` structure. Run the report
  (agent may run it read-only on local stores) against `data/01_shadow_12-09_00-58.db`
  and any same-window book-tape store; paste **aggregate output only**. Each of the
  three questions gets an answer or an explicit "unanswerable from this store" naming
  the missing store. Cite run 153 (4/13 companions within 1.5s, no gain at 5–120s),
  the shadow-01 grace setting, classification counts, Question 3 feature comparison,
  absent fields, and the n=4 limit. Record the aged-out/settlement gap as a follow-up
  and the future policy-change rehearsal procedure (max-setting rehearsal on its own
  store + same-window book tape; any longer hold needs a market-end-aware deadline).
- **Depends on:** T1 + T2
- **Verify:** operator reads the doc; report output included is aggregate only;
  `git status` shows nothing under `data/`.

## Explicitly NOT modified

- `core_brain/quotes.py`, `core_brain/trader_loop.py`, `core_brain/shadow_exec.py`
  (beyond none), `core_brain/config.py` thresholds, `scripts/filter_markets.py`.
- `data/**` — never committed.

## Verification & TDD summary

- Focused suites: `tests/test_rescue_exit_report.py`,
  `tests/test_single_buy_saver.py`, `tests/test_dual_stop_loss.py`,
  `tests/test_auto_pairs.py`, `tests/test_shadow_run.py`.
- Full `python -m pytest -q` stays with GitHub CI on push (merge gate).
- Operator "How to verify" lives in the PR description: run the report on
  shadow-01, open the findings doc, see reasons recorded in fresh shadow stores.
