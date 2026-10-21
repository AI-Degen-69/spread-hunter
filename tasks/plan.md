Branch: i730/record-stop-slippage | Issue: #730

# Plan — #730 [MEASURE] Record stop-slippage on hard-stop exits

## Tier / type

**Large** — database schema change (two nullable `closes` columns + one new
append-only table), cross-cutting over 8 files and the KPI payload version.
No new dependency. **Types:** [Backend/Logic] (primary) + [Design/UI]
(dashboard card) + [Docs] (N3 ledger row). Verification modes: pytest.
Measure-only: nothing here may change selling behaviour, the FOK submission,
`MAX_SELL_SLIPPAGE` (0.02, `single_buy_saver.py:76`), reason labels, or reset
logic.

## Spec (embedded pointer — SPEC.md holds the full version)

Record, per hard-stop exit, the floor the order was sent at and where the
booked fill numbers came from; aggregate mean/worst per-share slippage vs
floor and vs decision bid, plus below-floor and refusal counts, into an
always-present `stop_slippage` KPI section. Unknown (unmeasured) is never
zero. Full goals/acceptance/edge cases: `SPEC.md`.

## CodeRabbit plan intake (read once — Stations III–V must NOT re-read the comment)

- **Adopted:** nullable `closes.submitted_floor` / `closes.fill_source`
  (no backfill); `fill_source` ∈ {reported, simulated, assumed} decided in
  `shadow_exec._sell_into_bids`; an append-only `hard_stop_refusals` table
  (refused + error outcomes, outside every DELETE); an always-present pure
  `stop_slippage` summary in `statistical_analytics` following the
  `rescue_friction_summary` / `queue_realism` order; refusal rows wired
  through `kpi.report()` as an optional additive argument (quotes pattern);
  a read-only `python -m core_brain.kpi --db` CLI (no `OrderRegistry`,
  SQLite `mode=ro`); a dashboard card beside rescue-friction using the
  `figure()`/unmeasured pattern; demo seeds in `scripts/build_demo_db.py`;
  N3 marked addressed in `docs/issues/646-noticed-but-not-touching.md`.
- **Rejected (with reason):**
  - *"Keep tests unchanged and passing"* while adding `fill_source` — not
    possible without the fact living somewhere reachable. Verified: the fill
    size the walk returns can be the requested size even when the walk took
    **nothing** (`shadow_exec.py:1394` returns `(float(shares), float(floor))`
    on the conservative path), so the source is not derivable from the
    response, and `_sell_into_bids` is called directly by 9 test unpack
    sites (`test_exit_price_realism.py:129,252,282,309,316,325,356,378`,
    `test_hard_stop_fresh_exit.py:77`). Resolution adopted: `_sell_into_bids`
    returns a 3-tuple `(filled, avg, took_shares)`; the one production caller
    (`shadow_exec.py:1475`) and those 9 unpack sites become
    `filled, avg, _ = ...` — mechanical unpack edits, zero assertion changes.
  - *"Extend `tests/test_partial_exit_stays_managed.py`"* — dropped; the same
    reset-survival assertions live once, in the new
    `tests/test_stop_slippage.py` (same coverage, one file).
- **Resolved `[UNVERIFIED]` during intake (against the live tree):**
  - `KPI_PAYLOAD_VERSION` is **already 259** (`kpi.py:58`), consumed by #732;
    the pinned test asserts it literally (`test_analytics_api.py:185`). The
    bump is **259 → 260** on BOTH sides (`kpi.py:58` +
    `app.js:88 EXPECTED_PAYLOAD_VERSION`) AND the literal in the pinned test
    (`test_analytics_api.py:185`) must become `"260"` or CI fails.
  - The aggregator is `build(...)` (imported under the alias
    `build_statistical_analytics` at `kpi.py:28-29`); `queue_realism` is an
    inline section key at `statistical_analytics.py:2114`, not a function.
  - `HARD_STOP_SELL_REASONS` lives in `shadow_exec.py:62` and imports of
    `shadow_exec` into `statistical_analytics` are avoided via a
    function-level import (precedent: `single_buy_saver.py:1133`,
    `shadow_exec.py:1987`) so the import graph does not grow.
  - `_record_exit_close` has exactly ONE caller (`single_buy_saver.py:1168`),
    so every caller of `exit_single_buy` inherits the evidence automatically.
  - The hard-stop execute site is `decision.action == "hard_stop"` at
    `single_buy_saver.py:2047-2053` (`reason="lifecycle_hard_stop"`,
    `trigger_bid=held_bid`); the refused/error handler at `2232-2242` must
    keep producing identical `action="error"` results.
  - There is no producer of the reason string `hard_stop` in `core_brain`
    (tests only); selecting closes by reusing the `HARD_STOP_SELL_REASONS`
    frozenset covers both current (lifecycle_hard_stop) and historical
    labels.
  - No code DELETEs closes wholesale; the only closes DELETE is the residual
    tx-hash dedup inside migration (`order_registry.py:549-579`), and the
    reset-area deletes (`order_registry.py:1268,1285,1304`) touch
    lifecycle tables only — the refusal table is append-only by
    construction if nothing new deletes it.
  - `test_tournament_cockpit_funnel.py` hand-writes its `closes` DDL
    (line 62) and INSERTs explicit columns; new nullable columns and
    `_close_field()`'s missing-key → `None` handling leave it untouched.
  - `scripts/build_demo_db.py` exists, writes through real registry writers,
    and refuses the production path (`build_demo_db.py:835`).
  - `kpi.report()` builds `OrderRegistry(db_path)` (`kpi.py:1322`) which runs
    migrations — why the CLI must NOT reuse that path (repository rule:
    `data/orders.db` is never rewritten from the producer side here).
  - Old stores without the new columns: the CLI's `SELECT *` simply lacks the
    keys → `_close_field()` → `None` → historical unmeasured; the dashboard
    path migrates via the registry and reads `None` the same way.
- **Cost note:** adopted the whole structure; rejected the two items above;
  nothing left `[UNVERIFIED]`.

## Improvement proposal (Step 5) — one, evidence-based, adopted

Evidence (quote, `shadow_exec.py:1394`): `return float(shares), float(floor)`
— the walk that takes nothing still returns the requested size, so CodeRabbit's
"if the walk took shares, set it to `simulated`" cannot be read off the
response, and its own "keep the tests unchanged" is falsified by the 9 direct
`filled, avg = client._sell_into_bids(...)` unpacking sites.

Proposal (**simplification/hardening, adopt-by-default**): make the truth a
returned value — `_sell_into_bids` returns `(filled, avg, took_shares)`, the
SELL wrapper maps it into `resp["fill_source"]`, and the 9 unpack sites get a
mechanical `, _` addition. No ambiguity at exactly-at-floor fills (the case a
"Don't change the return value" response would misclassify). One sentence for
the operator: this touches 9 test files' unpack lines without changing a
single assertion — reject it and the plan falls back to a non-breaking
`out`-dict injection pattern at the cost of a slightly uglier signature.

## Open questions from the issue — resolved from code (no operator input needed)

1. How does the KPI report aggregate exits today, so the new aggregate follows
   the same shape? → `rescue_friction_summary(closes)`-style, always-present
   sections inside `statistical_analytics.build()`
   (`statistical_analytics.py:2113-2124`); ours joins them, keyed
   `stop_slippage`, additive to `build()`'s payload — no reader breaks.
2. Is `trigger_bid` stable enough for the "decision bid" reading? → It is
   already a close column (`closes` DDL `order_registry.py:309`), passed by
   name at `single_buy_saver.py:1170`, may be `None` for some callers, so the
   decision-bid measurement gets its own measured count (finite-`trigger_bid`
   gate). Resolved.
3. Refused exits never write a close — where does the unmeasured count come
   from? → The new append-only `hard_stop_refusals` table; the issue's
   "derive read-only" refers to venue calls, and Decision Context verified no
   existing durable place for refusals (lifecycle rows overwrite; reset
   deletes; `cycle_intent` capped). Resolved.

## Interfaces (locked before any business logic)

**DB (`core_brain/order_registry.py`):**

```sql
ALTER TABLE closes ADD COLUMN submitted_floor REAL   -- migration, beside trigger_bid
ALTER TABLE closes ADD COLUMN fill_source TEXT
CREATE TABLE IF NOT EXISTS hard_stop_refusals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, run_id TEXT, pair_id TEXT, condition_id TEXT,
    trigger_bid REAL, outcome TEXT NOT NULL,          -- 'refused' | 'error'
    detail TEXT
);
```

New in `CREATE TABLE closes` text as well. `fill_source` values:
`reported` (venue response carried positive, parseable price AND size) ·
`simulated` (shadow walk took shares) · `assumed` (a fallback supplied price
or/and size, incl. missing price, missing/zero size, a walk that took no
shares). Old rows stay NULL = historical unmeasured. Never backfill.

- `CloseRecord` += `submitted_floor: Optional[float] = None`,
  `fill_source: Optional[str] = None`; `log_close` column/value lists gain
  both.
- `OrderRegistry.log_hard_stop_refusal(...) -> None` — best-effort; a failing
  telemetry write must never alter the exit outcome.
- `OrderRegistry.get_hard_stop_refusals() -> list[dict]` — plain read.
- Reset untouched; the refusal table is added to NO delete.

**`core_brain/shadow_exec.py`:** `_sell_into_bids(token_id, shares, *, floor,
fresh=False) -> tuple[float, float, bool]` — `(filled, avg_price,
took_shares)`. The SELL response dict (~`1474-1483`) gains `"fill_source": "simulated" | "assumed"` from `took_shares`. `price`, `size`, `status`, the
fresh-vs-snapshot choice: unchanged.

**`core_brain/single_buy_saver.py`:**

```python
def _exit_fill_source(resp) -> str:    # beside _exit_fill_price/_exit_fill_size
```

Shadow `fill_source` key wins; else positive+parseable price AND size →
`reported`; else `assumed`. `exit_single_buy` passes `submitted_floor=min_price`
and the label into `_record_exit_close(...)` (one call site, line 1168) and
adds additive return-dict keys `submitted_floor` / `fill_source`. The
hard-stop branch wrap (2047-2053): on `PairExitRefused` → refusal row
(`outcome="refused"`, `trigger_bid=held_bid`); on any other exception →
`outcome="error"`, `detail=exception text`; always re-raise the original; when
the refusal write itself fails, the original exception still propagates.

**`core_brain/statistical_analytics.py`:**

```python
def stop_slippage_summary(closes, refusals=None) -> dict[str, Any]
def build(closes, fills, markouts, starting_capital,
          quotes=None, equity_curve=None,
          refusals: Optional[Sequence[Any]] = None) -> dict[str, Any]
```

Always-present section `"stop_slippage"`. Hard-stop selection only by reusing
`HARD_STOP_SELL_REASONS` (function-level import). Fill price = the sold-side
price `_record_exit_close` already wrote (`up_price` XOR `dn_price`); a close
with neither finite side price is unmeasured. Measured requires ALL of:
`fill_source in {"reported","simulated"}`, finite `submitted_floor`, finite
fill price, positive `shares`. Costs: `submitted_floor − fill_price` and
`trigger_bid − fill_price` (own count, finite `trigger_bid` only). Positive =
worse fill; negatives kept (better than reference). Values within a tiny
tolerance of zero snap to `0.0` — a fill at the floor reports `0.00`, never
"unknown". Output keys: `stop_exit_count`, `measured_count`,
`unmeasured_count`, `unmeasured_reasons.historical`, `unmeasured_reasons.assumed_fill`,
`below_floor_count`, `mean_vs_floor`, `worst_vs_floor`,
`decision_measured_count`, `mean_vs_decision`, `worst_vs_decision`,
`refused_position_count` (distinct `pair_id`), `refused_attempt_count` (raw
rows), `simulated_count`, `units: "usd_per_share"`. Zero measured → every
mean/worst is `None`, never 0. One observation per close (no share weighting).
All floats through `_finite()`; payload must survive
`json.dumps(..., allow_nan=False)`.

**`core_brain/kpi.py`:** `report()` reads refusal rows via the new registry
read method and filters them by `run_id` exactly like closes (the
`1391-1403` block), passing them into `build(...)`. `KPI_PAYLOAD_VERSION`
259→260 (`kpi.py:58`) **and** `app.js EXPECTED_PAYLOAD_VERSION` 259→260
(`app.js:88`) **and** the pinned literal `test_analytics_api.py:185` →
`"260"` + its comment gains the `#730` line. New CLI:

```
python -m core_brain.kpi --db <path> [--run-id <id>]
```

`main(argv=None) -> int` + `__main__` guard following
`scripts/pair_fill_report.py`; argparse; SQLite opened `mode=ro`; NO
`OrderRegistry`; `SELECT *` closes; a missing `hard_stop_refusals` table ⇒
treat as empty; prints only the `stop_slippage` section as indented strict
JSON. It must not modify the store.

**Dashboard (`dashboard/static/app.js`, `dashboard/static/index.html`):**
`renderStopSlippage(stats, hostId)` beside `renderRescueFriction`
(`app.js:5751`), called beside it (`app.js:6210`), exported beside it
(`app.js:10622`); host `#stop-slippage-readout` in a card directly after the
rescue-friction card (`index.html` lines 515-521 pattern). Local `figure()`
pattern so `None` renders "unmeasured", never `0.00`. Shows: mean/worst vs
floor, mean/worst vs decision bid, below-floor count, "measured out of stop
exits", unmeasured reasons, refused-position count; a "simulated (rehearsal
result, not venue)" note when `simulated_count > 0`; "backend older than
page" when the section is absent.

## Dependency graph

```
T1 (registry: columns + refusals table + CloseRecord/log_close)
 ├── T2 (shadow label: _sell_into_bids 3-tuple + fill_source)
 │     └── T3 (exit pass-through: helper + one call site + refusal wrap)
 └── T4 (pure summary + build() section)
       └── T5 (kpi wiring + version both sides + CLI)
             └── T6 (dashboard card)
T7 (proof tests) runs green per task above and assembles last
       └── T8 (demo seeds + N3 ledger) after T3 (needs real writers)
```

Order risk-first: T1's schema is the root everything reads; T2 is the one
design shift past intake (3-tuple) and lands immediately after it.

## Tasks

### T1 — [Backend/Logic] Registry: nullable exit evidence + append-only refusals [x]
**Size M. Depends on: —. Verify: `python -m pytest -q tests/test_stop_slippage.py -k registry`**

Files: `core_brain/order_registry.py` — `closes` DDL (290-311), migration
block (529-537, beside `trigger_bid`/`fresh_bid`), `CloseRecord` (788-814),
`log_close` (1781-1811), new table + write/read methods by the
`log_close`/`get_all_closes` style. Reset paths untouched. RED→GREEN: the
new test asserts the columns exist after init on an old-schema store, old
rows read `None`, repeat-init is safe, refusal write→read round-trips, and a
reset leaves refusal rows intact.

### T2 — [Backend/Logic] Shadow sells label themselves [x]
**Size S. Depends on: T1. Verify: `python -m pytest -q tests/test_exit_price_realism.py tests/test_hard_stop_fresh_exit.py tests/test_shadow_exec.py`**

Files: `core_brain/shadow_exec.py` — `_sell_into_bids` (1302, fallback return
~1394) returns `(filled, avg, took_shares)`; the SELL response built at
~1474-1483 maps `fill_source`. Update the 9 test unpack sites mechanically
(`filled, avg, _ = client._sell_into_bids(...)`). The existing tests in these
files keep their assertions verbatim.

### T3 — [Backend/Logic] Exit records carry the floor and the source [x]
**Size M. Depends on: T1, T2. Verify: `python -m pytest -q tests/test_hard_stop_fresh_exit.py tests/test_dual_stop_loss.py tests/test_single_buy_saver.py`**

Files: `core_brain/single_buy_saver.py` — `_exit_fill_source` helper beside
`_exit_fill_price` (814-855); `exit_single_buy` passes
`submitted_floor=min_price` + label at the single `_record_exit_close` call
(1163-1170) and adds the additive return-dict keys; the hard-stop branch
(2047-2053) wraps ONLY the `exit_single_buy` call with best-effort
refusal/error recording then re-raise. No sold/size/fallback/threshold
change. RED→GREEN: at-floor shadow exit ⇒ `fill_source="simulated"`,
`submitted_floor=floor`; refusal paths write the row and keep the identical
`action="error"` result.

### T4 — [Backend/Logic] The pure aggregate [x]
**Size M. Depends on: T1. Verify: `python -m pytest -q tests/test_stop_slippage.py -k summary`**

Files: `core_brain/statistical_analytics.py` — `stop_slippage_summary` +
`build(..., refusals=None)` section (2085-2124), function-level import of
`HARD_STOP_SELL_REASONS`. RED→GREEN: measured-at-floor ⇒ mean `0.0` and is
`not None`; below-floor count/worst; missing price/zero size/walk-took-nothing
⇒ `assumed` ⇒ unmeasured; NULL floor/source ⇒ `historical`; partial closes =
two observations; empty run ⇒ `None` means; strict-JSON round-trip.

### T5 — [Backend/Logic] KPI wiring, version, and the read-only command [x]
**Size M. Depends on: T4. Verify: `python -m pytest -q tests/test_stop_slippage.py tests/test_kpi.py tests/test_analytics_api.py tests/test_trade_analytics.py tests/test_tournament_cockpit_funnel.py`**

Files: `core_brain/kpi.py` (refusals read + run_id filter + `build()` arg;
`KPI_PAYLOAD_VERSION` 259→260 at line 58; new `main()` + `__main__`), plus
the version pair updates: `app.js:88` → 260 and
`tests/test_analytics_api.py:185` → `"260"` with the comment line `#730
added the stop-slippage card: both sides sit at 260.` RED→GREEN: the CLI on
an old-schema store prints the section and leaves the file bytes unchanged;
stale backend vs new page shows the restart note.

### T6 — [Design/UI] The card [ ]
**Size S. Depends on: T5. Verify: `python -m pytest -q tests/test_negative_values_read_as_losses.py tests/test_analytics_api.py`**

Files: `dashboard/static/app.js` (renderer by `renderRescueFriction:5751`,
call ~6210, export ~10622), `dashboard/static/index.html` (host after the
rescue-friction card, 515-521 pattern). `figure()` so `None` ⇒ "unmeasured".
RED→GREEN: a fixture payload with `mean_vs_floor = 0.0` renders `0.00` and
`mean_vs_floor = None` renders "unmeasured"; missing section ⇒ the
"backend older than page" copy.

### T7 — [Backend/Logic] Assemble the proof [ ]
**Size L. Depends on: T3, T4, T5. Verify: the files named per task above, plus `python -m pytest -q tests/test_stop_slippage.py`**

File: `tests/test_stop_slippage.py` (single home; grows RED-first with each
task). Full acceptance matrix: at-floor ⇒ `0.0` not `None`; below-floor
count/worst; no-price/no-size/no-walk ⇒ `assumed` ⇒ unmeasured; historical
NULL rows; repeated hard-stop refusals counted once per position while
attempts count rows, `action="error"` unchanged, no close row; partials 4+6
shares = two measurements surviving reset; two runs sharing a condition
summarised separately (per-`run_id` filtering); empty run `None`s;
`allow_nan=False`; migration on an old store; the CLI read-only old-store
case.

### T8 — [Docs] Operator demo + ledger truth [ ]
**Size S. Depends on: T3, T5. Verify: run `python scripts/build_demo_db.py --out data/operator-demo.db` then `python -m core_brain.kpi --db data/operator-demo.db` and read the seeded figures off the output.**

Files: `scripts/build_demo_db.py` (seed: reported at-floor fill, reported
below-floor fill, an assumed fallback close, one refusal row — via the real
registry writers; the builder's production-path refusal stays),
`docs/issues/646-noticed-but-not-touching.md` (row N3 → published as #730,
mark addressed). No new scenario launcher (Design Choice 5 stands).

## Checkpoints

- **CP1 (after T2):** an at-floor shadow sell is labelled `simulated`; a
  walk that takes nothing is `assumed`; every existing shadow-test assertion
  still true.
- **CP2 (after T4):** the summary answers the issue's three invariants on
  hand-built rows, with no registry or venue involved.
- **CP3 (after T5):** `python -m core_brain.kpi --db data/operator-demo.db`
  prints measured and unmeasured figures; the pinned version test is green
  on 260 on both sides.
- **CP4 (after T8):** the demo store shows `0.00`, one below-floor, and two
  unmeasured rows (one assumed, one refused) — the operator's hands-on
  snapshot lives in `build_demo_db.py` output + the same CLI command.

## Risks

1. **The CLI vs the bare import chain.** `python -m core_brain.kpi` imports
   the `statistical_analytics` module → function-level `shadow_exec` import.
   Verified chain is cycle-free today; if a future import in shadow grows a
   cycle back, the whole-build fallback is moving `HARD_STOP_SELL_REASONS`
   selection behind a small local frozenset while naming the source —
   decide at T4's first red.
2. `simulated` counting mixes rehearsal and live rows in one aggregate.
   Mitigated: `simulated_count` is explicit and the card labels rehearsal
   data as rehearsal, never venue, per safety rules.
3. Partial-close dedup: the tx-hash dedup DELETE only ever touches rows with
   a `tx_hash`; exit closes never carry one. No interaction expected — keep
   the reset-survival test as the guard.

## NOTICED-BUT-NOT-TOUCHING (see `docs/issues/730-noticed-but-not-touching.md`)

- Live responses that only carry `takingAmount`-style fields class as
  `assumed` ⇒ unmeasured. Correct-for-now; parsing is a separate ticket.
- `rescue_friction`'s own wording/units quirks (assumed 0.04 offset, cents)
  stay exactly as shipped — out of scope here.
- `stray_guard.py:348` and the `aged_out_rescue` / settlement callers also
  start writing the new columns (recording is centralised); their closes
  select by reason only, so the aggregate's population is unchanged.
