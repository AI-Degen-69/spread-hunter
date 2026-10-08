# Plan — #422: shadow_run: every rehearsal starts at a fixed bankroll

Branch: i422/shadowrun-every-rehearsal-starts-at-a-fixed-bankro | Issue: #422

- Tier: **Standard** — 3 files (`shadow_run.py` + tests + one docs page), internal module change, one architectural decision (one bankroll resolution + one flag).
- Task type: **Code** — bankroll resolution + CLI flag + regression tests (docs touch is wording-only).
- Stack: Python, pytest; no external dependency.
- CodeRabbit plan: **adopted as scaffolding, merged into 4 tasks** — all 4 design choices adopted (config default as single source; one flag with old name as alias; ordinary flag→env→config vs paired flag→100 precedence; statistical harness left alone). Verified from code (nothing left `[UNVERIFIED]`): live read at `shadow_run.py:916-927` with `cfg=None` + `shadow_cfg()` intact; paired pin at `929-936` with `<= 0` check; `_parse_args` at `1402` with `--paired-starting-bankroll-usd` default 100.0 (line 1431) and `--funder` balance-read help (1433-1435); `main` forwards bankroll for paired arms only (1539-1542); `run_shadow(starting_bankroll_usd=None)` at line 850; `bankroll_usd = 100.0` at `config.py:41` with env overrides at 1521-1528; `derive_dynamic_caps` at `config.py:1627`; funder doc at `first-run.md:187` and signer/store/clock line at 190-191; out-of-scope files exist and behave as claimed (`statistical_validation_run/run.py:364-368` own live read, `scripts/shadow_tournament.py:315` subprocess launch, `tests/test_shadow_run_run_id.py` CLI-test style). Rejected: the 5-task split (merged into 4 per Rule 4); nothing else material.
- Open questions resolved from code (no operator question): the ticket's dollar figures were lost in rendering — `$100` is confirmed by the paired default (100.0), `config.py:41` (100.0), and the plan's own cap math ($25 order cap = 25% of $100). The `funder` param must stay (statistical harness passes it at `run.py:411`) but goes unused for bankroll.
- Improvement proposal (adopted, simplification): the paired-without-flag default reads the `MakerConfig` field default instead of re-hard-coding 100.0. Evidence: the issue says "`core_brain/config.py:41` stays the default source of truth" while `_parse_args` line 1431 hard-codes `default=100.0` — a second copy of the number that can drift; `main` forwards `MakerConfig`'s field default for paired arms with no flag. Folded into T2.
- Type-design analyzer: skipped with reason — no non-trivial domain model (one optional float param, one argparse alias; nothing to encapsulate).
- Sub-issues: skipped per repo precedent (Standard #419 shipped without them; the plan file is the tracker).
- Safety: do not open or rewrite `data/orders.db`; tests use temporary DBs. No live quoting, Trader loop, manual completion, or dashboard START.

## Locked behavior (see SPEC.md; summary)

- Ordinary run, `cfg=None`: `shadow_cfg()` → `config.load()` decides the bankroll (flag → env → $100); the `fetch_live_balance` read is gone.
- `starting_bankroll_usd` given: `math.isfinite` + `> 0`, else `ValueError` containing "bankroll"; pin via `dc_replace(cfg, bankroll_usd=...)`.
- CLI: `--starting-bankroll-usd` with `--paired-starting-bankroll-usd` as alias on the same dest, default `None`; `main` forwards the flag if set, the `MakerConfig` field default for paired arms without it, else `None`.
- One startup `log.info` states the starting bankroll and its source (explicit override, environment, or config).

## Interface contracts (frozen)

- `run_shadow(..., starting_bankroll_usd: Optional[float] = None, ...)` — signature unchanged; `None` keeps config/env behavior, a value pins after validation. `funder` stays keyword-compatible, unused for bankroll.
- CLI: `--starting-bankroll-usd USD` / alias `--paired-starting-bankroll-usd USD`; `--funder ADDR` help reworded to compatibility ("kept for compatibility, does not set the shadow bankroll").
- `derive_dynamic_caps`, `trader_loop._fleet_state`, `record_paired_run_start` / `record_paired_equity_mark` inputs: unchanged.

## Dependency graph

- T1 → T2 → T3; T4 depends on T2 (docs describe T1/T2 behavior)

## Tasks

### T1 [x] — RED+GREEN: one fixed bankroll path in `run_shadow` [Backend/Logic] (M)

- Target files: `core_brain/shadow_run.py`
- Build: inside `if cfg is None:`, keep `cfg = shadow_cfg()`; delete the `maker = funder or POLY_FUNDER` lookup, the `fetch_live_balance` call + warning log, and the import if unused elsewhere. Replace the paired-only pin (lines 929-936) with one resolution step for all runs: value given → `math.isfinite` + `> 0` else `ValueError("... bankroll ...")`, pin with `dc_replace`; not given → leave `cfg.bankroll_usd` untouched. Keep early paired checks (902-911) and paired consumption (1013-1034) byte-identical. Add one startup `log.info` with bankroll + source (explicit/env/config).
- Helper skill: `test-driven-development`.
- Depends on: nothing.
- Verify: `python -m pytest -q tests/test_shadow_run.py` — new T3 tests fail before, all green after.

**Checkpoint:** ordinary rehearsals ignore the wallet; explicit and env overrides still pin.

### T2 [x] — GREEN: unify the CLI flag, keep the old name working [Backend/Logic] (S)

- Target files: `core_brain/shadow_run.py`
- Build: in `_parse_args`, define `--starting-bankroll-usd` with `--paired-starting-bankroll-usd` as a second option string on the same dest, default `None`; help text names the config bankroll ($100) / `SPREAD_HUNTER_BANKROLL` default and the $100 paired default. In `main`: flag set → forward for any run; not set + paired arm → forward the `MakerConfig` field default for `bankroll_usd` (no hard-coded copy); not set + ordinary → forward `None`. Reword `--funder` help to compatibility wording (no "live balance read").
- Helper skill: `incremental-implementation`.
- Depends on: T1.
- Verify: `python -m pytest -q tests/test_shadow_run.py tests/test_shadow_run_run_id.py` — CLI/forwarding tests green, old paired commands still parse.

### T3 [x] — GREEN: focused tests that fail if the live read returns [Backend/Logic] (M)

- Target files: `tests/test_shadow_run.py` (CLI-forwarding test may live in `tests/test_shadow_run_run_id.py` style)
- Build: reuse the `fake_loop_run` monkeypatch pattern to capture the `cfg` the loop receives. Fixed-bankroll: ordinary run, `cfg=None`, `funder="0xabc"`, `fetch_live_balance` monkeypatched to return 5000 and record/fail on call → `bankroll_usd == 100.0`, `derive_dynamic_caps(cfg)["max_order_usd"] == 25.0`, balance fn never called. Override: `starting_bankroll_usd=250.0` → 250.0. Env: `SPREAD_HUNTER_BANKROLL=50` → 50. Invalid: NaN/inf → `ValueError` matching "bankroll". CLI: both flag names parse to the same value; ordinary w/o flag forwards `None`; paired w/o flag forwards 100.0. Leave existing paired tests (incl. `test_admission_arm_requires_markets_path_and_bankroll`) untouched.
- Helper skill: `test-driven-development`.
- Depends on: T2.
- Verify: `python -m pytest -q tests/test_shadow_run.py tests/test_shadow_run_run_id.py` — all green.

**Checkpoint:** the full bankroll matrix (fixed / override / env / invalid / CLI) is pinned by tests.

**Review round (PR #423) — one finding accepted, one rejected.**
- ACCEPT: the default-bankroll test assumed `$100` without clearing
  `SPREAD_HUNTER_BANKROLL`; `tests/conftest.py` scrubs only `HUNTER_*`, so an
  operator's exported value could flunk the assertion. Both the default test and
  the invalid-value test now `delenv` it (verified green with the var exported).
- REJECT: re-derive `allocation_budget` / `max_committed_usd` on the explicit
  pin (and thread `bankroll_override` through `config.load`). `config.py` is
  frozen by SPEC.md, and `max_committed_usd` is a live quoting gate
  (`quotes.py:455-461`) whose default (1000.0) is inert at a $100 bankroll --
  re-deriving it to 100 would make the cap active and silently change the
  preregistered paired arms and the recorded 2026-09-28 pilot baseline. The
  issue's three caps (order/naked/ceiling) all come from `bankroll_usd`. The
  stale-derived-field inconsistency is real, so it belongs in a follow-up issue
  rather than this ticket.

### T4 [x] — Operator docs match the new behavior [Docs] (S)

- Target files: `docs/agents/first-run.md`
- Build: at lines ~173-192: change shadow `--funder` from "balance-read funder" to compatibility wording; add "Shadow runs always start at the config bankroll ($100). Override with `--starting-bankroll-usd` or `SPREAD_HUNTER_BANKROLL`."; qualify the "only the signer, the store, and the wall clock differ" line (bankroll is now fixed, not from the wallet). Leave Trader/`balance --funder` lines (126, 162) and `docs/runs/2026-09-28-paired-depth-pilot.md` untouched.
- Helper skill: `documentation-and-adrs`.
- Depends on: T2.
- Verify: read the three spots back (record line, funder line, pilot file untouched); no pytest needed.

---

# Plan — #419: trader_loop: hold a resting order and never requote when mid <= price + 0.02 (DONE, history)

Branch: i419/trader-loop-hold-a-resting-order-and-never-requote | Issue: #419

- Tier: **Standard** — planner + visit wiring + docs paragraph, one architectural decision (guard placement at the cancel decision, not in pricing).
- Task type: **Code** — cancel-path guard + visit feed + regression tests.
- Stack: Python, pytest; no external dependency.
- CodeRabbit plan: **adopted as scaffolding, merged into 3 tasks** — all 4 design choices adopted (guard at cancel decision; price-driven routes only; named module constant; today's behavior on missing/crossed books); task phases merged per Rule 4. Verified from code (nothing left `[UNVERIFIED]`): `plan_orders` at `trader_loop.py:237` with `price_eps` and `held_tokens` already present; grace expiry shares `REFUSED_TERMINAL` (`trader_loop.py:1759-1762`), so a distinct member is required; `quotes.mid_price` (`quotes.py:103`) takes bid/ask with no crossed-check, so the caller skips crossed books; books carry `token_id`/`best_bid`/`best_ask` (`trader_loop.py:1709-1713`); test spies forward `**kwargs` (`test_trader_loop.py:1073,2301`); `requote_dead_band = 0.03` (`config.py:875`) with retired meaning — the new constant is justified; every cited test name exists. Rejected: nothing material.
- Open questions resolved from code (no operator question): the residual requote/cancel originates in exactly two places — the `not_quoted` branch (`trader_loop.py:432`, the only remaining canceller after #387's NO RE-CHECK holds every wanted token) and `lifecycle_replace` (`trader_loop.py:413-419`). The 0.02 is a named constant per the issue default (`MID_HOLD_BAND`), since `requote_dead_band` is 0.03 with a retired meaning.
- Improvement proposal (adopted, simplification): guard-held tokens join the EXISTING `held_tokens` set instead of a second set. Evidence: `trader_loop.py:402-408` — "A held order rests at a price outside the tolerance by definition, so the submit loop below would not recognise it as covering this cycle's intent and would post a second order beside it" — the identical hazard, and the existing skip at lines 448-451 then covers the ordinary branch for free; only the lifecycle-pair branch (lines 445-447, which bypasses the check) needs the same one-line skip.
- Type-design analyzer: skipped with reason — no non-trivial domain model (one optional dict param defaulting to `None`, one enum member; nothing to encapsulate).
- Sub-issues: skipped per repo precedent (Standard #416 shipped without them; the plan file is the tracker).
- Safety: do not open or rewrite `data/orders.db`; tests use temporary DBs. No live quoting, Trader loop, manual completion, or dashboard START.

## Locked behavior (see SPEC.md; summary)

- Hold iff the token's mid exists, the book is two-sided and uncrossed, and `mid <= order.price + 0.02` (equality included via `price_eps`).
- Guarded routes: `not_quoted` cancels, grace expiry, `lifecycle_replace`.
- Unguarded routes: named terminal refusals, hard stop, `lifecycle_cancel`, explicit cancel set, cancel-wins-over-replace.
- One fill-side invariant preserved: a held token is never submitted twice.

## Interface contracts (frozen)

- `plan_orders(..., token_mids: Optional[dict] = None)` — keyword-only, `None` keeps today's behavior exactly; existing callers and test spies (`**kwargs`) need no change.
- New `VisitOutcome` member for grace expiry (today it arrives as `REFUSED_TERMINAL`, indistinguishable from a named terminal refusal).
- `token_mids`: token -> mid, built in `_visit_one` from `ev.up_book`/`ev.down_book` via `quotes.mid_price`; token omitted when its id is missing, either side is missing, or bid >= ask.
- Guard-held tokens join `held_tokens`; both submit branches skip them.

## Dependency graph

- T1 → T2 → T3

## Tasks

### T1 [x] — RED+GREEN: mid-hold band on the missing-intent branch [Backend/Logic] (M)

- Target files: `core_brain/trader_loop.py`, `tests/test_plan_orders_mid_hold.py` (new)
- Build: add `MID_HOLD_BAND = 0.02` with a one-line rule comment; add keyword-only `token_mids=None` to `plan_orders`; add a helper holding iff the token has a mid and `mid <= price + MID_HOLD_BAND + price_eps`. In the no-intent branch keep this order: transient-refusal hold → terminal-refusal cancel (incl. hard stop) → in-band hold (no `CANCEL_NOT_QUOTED`, token joins `held_tokens`) → `not_quoted` cancel. Add the distinct grace-expiry `VisitOutcome` member at the `_visit_one` assignment (`trader_loop.py:1759-1762`) and thread it so grace expiry holds in band while terminal refusals cancel. New test file in the style of `test_plan_orders_asymmetric_hold.py` (UP order at 0.48): equality mid 0.50 holds (empty cancels, no UP submit, no UP `not_quoted`); in-band 0.46 holds; out-of-band 0.501 cancels `not_quoted`; missing mid cancels; `token_mids=None` cancels; terminal refusal at 0.50 cancels; grace expiry at 0.50 holds with nothing submitted; drifted intent (UP intent at 0.40, mid 0.46) neither cancels nor submits.
- Helper skill: `test-driven-development`.
- Depends on: nothing.
- Verify: `python -m pytest -q tests/test_plan_orders_mid_hold.py tests/test_plan_orders_asymmetric_hold.py` — new tests fail before, all green after.

**Checkpoint:** in-band missing-intent orders hold instead of cancelling; terminal and out-of-band behavior untouched.

### T2 [x] — GREEN: hold in-band lifecycle replacements, no duplicate submits [Backend/Logic] (M)

- Target files: `core_brain/trader_loop.py`, `tests/test_plan_orders_mid_hold.py`
- Build: an order in `replace_order_ids` but not `cancel_order_ids` that is in band is held (no `lifecycle_replace`, token joins `held_tokens`); `lifecycle_cancel`, explicit preservation, wanted-token holds, and cancel-wins-over-preserve stay exactly. Both submit branches skip guard-held tokens — the ordinary branch via the existing `held_tokens` check, the lifecycle-pair branch with the same one-line skip. Extend the test file (DOWN hedge at 0.48, lifecycle-pair DOWN intent at 0.51): in-band mid 0.49 → no cancel, no DOWN submit; out-of-band 0.51 → `lifecycle_replace` cancel + 0.51 intent submitted; missing mid → replacement as today; replace+cancel in band → cancelled; `lifecycle_cancel` on UP at 0.48 with mid 0.46 → cancelled `lifecycle_cancel`.
- Helper skill: `incremental-implementation`.
- Depends on: T1.
- Verify: `python -m pytest -q tests/test_plan_orders_mid_hold.py tests/test_plan_orders_asymmetric_hold.py` — all green.

### T3 [x] — Wire real mids from `_visit_one`, protect visit contracts, document [Backend/Logic] (M)

- Target files: `core_brain/trader_loop.py`, `tests/test_trader_loop.py`, `docs/agents/strategy.md`
- Build: in `_visit_one`, build `token_mids` from this cycle's UP/DOWN books keyed by `token_id` via `quotes.mid_price`; omit on missing id, missing side, or bid >= ask. Pass it on every `(plan_fn or plan_orders)(...)` call (spies forward `**kwargs`, no wrapper changes). A rotated-away token has no book and still cancels. Move the shared `TestRefusedHold` books to 0.66/0.68 (out of band for the 0.60 UP order) so the grace-expiry test keeps asserting cancel; add a separate in-band grace test (0.59/0.61 books, transient refusal × GRACE cycles → held, nothing submitted). Add visit tests: in-band (UP 0.48, book 0.45/0.47, DOWN-only intent → no UP cancel) and crossed book (0.50/0.48 → `not_quoted` cancel). Re-check: terminal-refusal, token-rotation, quote-resets-streak, patient-wait (UP remainder via cancel set), escalation (DOWN mid 0.51 outside the 0.48 band → replace proceeds), escalation-preserve, hard-stop, refused-escalation — assertions unchanged. Append one paragraph to `docs/agents/strategy.md` (inclusive rule, covered routes, still-cancelling routes, missing/crossed fallback) without rewriting the dead-band text. Confirm zero changes in `shadow_fills.py`, `config.py`, `quotes.py`, `_market_cfg`.
- Helper skill: `incremental-implementation`.
- Depends on: T2.
- Verify: `python -m pytest -q tests/test_trader_loop.py tests/test_plan_orders_asymmetric_hold.py tests/test_plan_orders_mid_hold.py` — all green.
