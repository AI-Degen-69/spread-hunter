# Plan — Issue #674: `scoring.config.load()` ignores the canonical `BOT_*` overrides the engine obeys

Branch: `i674/canonical-bot-overrides-in-scoring` | Issue: #674
Size Tier: Standard | Task Types: Code, Refactor, Test

## Code Intake & Context (Station II Step 0A)

**Problem.** Two loaders read the same `.env`. `core_brain.config.MakerConfig.from_env()` was moved
onto canonical-first reads in #570/#652/#653; `scoring.config.load()` — imported by
`scripts/filter_markets.py` (module-level `_CFG = load()`), `scripts/filter_loop.py` and the
dashboard's screener cards — still reads only the deprecated `HUNTER_*` / `SPREAD_HUNTER_*`
spellings for nine execution settings. Setting `BOT_QUOTE_SIZE_MIN_SHARES=200` therefore sizes the
live bot at 200 shares and leaves the ranker at the shipped 50: no error, no warning, two different
engines from one `.env`.

**Deliberate design already in place.** `scoring/config.py:197-277` shows the shape this issue
finishes: every `FILTER_*` read already goes through
`_env_get("<CANONICAL>", "<HUNTER_LEGACY>")` and discards the source name (`trial, _ = ...`). The
`#653` migration stopped at the filter bars. This issue extends the same pattern over the nine
execution reads, and this time keeps the source name, because three of them must warn.

**Reference shape.** `core_brain/config.py:957` `_env_get(name, *legacy) -> tuple[str | None, str | None]`
returns `(value, source_name)`; `core_brain/config.py:574-800` `from_env()` shows the exact canonical
order for the same twelve variables, including `wide_name` being threaded into
`resolve_wide_book_trial(raw, *, for_display, var_name)` at `core_brain/config.py:981`.

**Spot-checked citations.** `scoring/config.py` `load()` spans 145-297 (ticket says 149-296 — same
function). `.env.example:46` and `:63` already document `BOT_QUOTE_SIZE_MIN_SHARES` and
`BOT_QUOTE_BANK_BASELINE_USD` beside their deprecated aliases, so no `.env.example` edit is needed.
`resolve_wide_book_trial()` is `scoring/config.py:104-142`; its rehearsal gate is
`rehearsal.is_rehearsal()`, not "is a key reachable", and that reasoning is correct as written.

**Verified baseline (agent-run).** `python -m pytest -q tests/test_config_single_source.py
tests/test_config_divergence_guard.py tests/test_ranker_reads_env_file.py tests/test_wide_book_trial.py`
→ **151 passed**. Worktree clean on `main` @ `5601a23`. Issue claimed.

**Ticket's verification command is wrong.** It lists `tests/test_market_selection_bars.py`, which does
not exist in this repo (no `*market*` test file at all). Substituted the four suites that actually
cover the touched code — see T4. Recorded in the PR body.

**Owned files.** `scoring/config.py` — #653 touched it last. No other open PR touches it
(`origin` branches: `main`, `plan_issue_672`, which owns `dashboard/**`).

## Improvement Proposal (Station II Step 5)

- **Evidence:** `scoring/config.py:136` hardcodes the refusal text
  `"HUNTER_WIDE_BOOK_TRIAL widens max_book_spread, ..."` and `scoring/config.py:142` hardcodes
  `_bounded_float("HUNTER_WIDE_BOOK_TRIAL", raw, 0.0, 0.30)`. After canonical routing, an operator
  who set only `BOT_QUOTE_WIDE_BOOK_TRIAL` would be refused with a message naming a variable they
  never set — the exact "which knob did I get wrong?" confusion this issue exists to remove.
  `core_brain/config.py:981` already solved it: `var_name` is a keyword-only parameter defaulting to
  the legacy name, so core's legacy-only messages read byte-identically.
- **Proposal (adopted, edge-case hardening — CodeRabbit Task 3 asks for it):** give
  `resolve_wide_book_trial` the same keyword-only `var_name="HUNTER_WIDE_BOOK_TRIAL"` parameter and
  use it in both the refusal sentence and the `_bounded_float` call; `load()` passes the source name
  `_env_get` reported. Default value keeps every existing caller and every legacy-only error message
  unchanged. No new helper, no class-body change, ~4 lines.
- **Rejected alternative:** CodeRabbit's Design Choice 3 option 2 (copy core's second spread block so
  the bar applies to every market). It changes legacy-only output when no market is pinned, which
  breaks AC-3. Recorded in `docs/issues/674-noticed-but-not-touching.md`.

---

## Tasks

### [x] T1 — Precedence test table: canonical-only and canonical-wins must fail first (RED)
- **Size:** S | **Domain:** `[Backend/Logic]` | **Skill:** `test-driven-development`
- **Depends on:** none
- **Target files:** `tests/test_config_single_source.py`
- **What is built:**
  1. Follow the #653 shape at `tests/test_config_single_source.py:335-385` exactly: a module-level
     `EXECUTION_CANONICAL_ENV` table beside `SCREENER_CANONICAL_ENV` and one five-phase parametrized
     test, rather than three new test functions. Row:
     `(canonical_var, legacy_vars, field, can_raw, can_val, leg_raw, leg_val, default_val,
     needs_pin, needs_rehearsal)`; `legacy_vars` is a tuple because three chains carry two old names.
  2. `field` is the primary asserted field, hard-coded — never derived from `_LEGACY_TO_CANONICAL`,
     never a compatibility property (those emit `DeprecationWarning` on read):
     `bot_market_pinned_condition_id`, `bot_market_title`, `bot_market_url`,
     `bot_quote_size_min_shares`, `bot_quote_book_max_spread_from_mid`, `bot_quote_grid_tick_size_usd`,
     `bot_quote_wide_book_trial`, `bot_risk_gate_pairs_rule_on`, `bot_latency_net_oneway_ms`,
     `bot_latency_cancel_venue_ack_ms`, `bot_latency_post_venue_accept_ms`,
     `bot_quote_bank_baseline_usd`.
  3. The five phases, copied from `test_canonical_filter_env_overrides_field_and_wins_over_legacy`:
     unset → shipped default; canonical alone → canonical value; both set → canonical wins; blank and
     `"   "` canonical → the legacy value; each legacy var alone with the newer one cleared → the same
     value. Multi-legacy rows repeat phase 5 once per old name, newest first.
  4. `_clear_execution_env(monkeypatch)` mirrors `_clear_screener_canonical_env`: delete every
     canonical and legacy name in the table plus the remaining latency and bankroll aliases, so a
     shell value cannot leak in. Rows flagged `needs_pin` also set the pinned market, because those
     reads live inside the pinned branch; the `needs_rehearsal` row declares a rehearsal the way
     `tests/test_wide_book_trial.py` does.
  5. Three coupling tests, because four rows write more than one field: the size override also raises
     `bot_quote_size_shares` to `max(n, 120)` (`"200"` → `200`/`200`; `"50"` → `50`/`120`); the
     bankroll override sets `bot_quote_bank_baseline_usd` and
     `bot_risk_exposure_max_committed_usd` from one value (`"250"` → both `250.0`); the trial override
     sets `bot_quote_wide_book_trial` and `max_book_spread` and only ever widens
     `bot_quote_book_max_spread_from_mid` (`"0.12"` over a pinned spread of `"5"` → `0.12`).
  6. Values far from the defaults: spread `"25"` → `0.25` (cents in, dollars stored), tick `"0.005"`,
     latency `"9.5"` / `"210.0"` / `"95.0"`, pairs rule `"off"` → `False`.
- **Verify:** `python -m pytest -q tests/test_config_single_source.py -k scoring_loader` → the
  canonical-only and precedence tests **fail** on canonical rows; legacy-in-turn **passes**.
- **Checkpoint:** RED evidence exists and is attributable to the missing canonical read, not to a
  fixture mistake.

### [x] T2 — Rewire the twelve reads in `load()` through `_env_get` (GREEN)
- **Size:** S | **Domain:** `[Backend/Logic]` | **Skill:** `incremental-implementation`
- **Depends on:** T1
- **Target files:** `scoring/config.py`
- **What is built:**
  1. Pinned market: `cid, _ = _env_get("BOT_MARKET_PINNED_CONDITION_ID", "HUNTER_MARKET")`, then
     `cid = (cid or "").strip()`; the branch condition and everything inside it keep their order and
     effect. Title/URL: `_env_get("BOT_MARKET_TITLE", "HUNTER_TITLE")` → `or ""`.
  2. Size / spread / tick chains, warning only when the **oldest** legacy name is the source:
     ```python
     ms, ms_name = _env_get("BOT_QUOTE_SIZE_MIN_SHARES", "HUNTER_MIN_QUOTE_SHARES", "HUNTER_MIN_SIZE")
     if ms_name == "HUNTER_MIN_SIZE" and ms and ms.strip():
         logging.getLogger(__name__).warning(
             "HUNTER_MIN_SIZE is deprecated (#632); set HUNTER_MIN_QUOTE_SHARES instead.")
     if ms and ms.strip():
         kw["min_quote_shares"] = int(float(ms))
         kw["quote_shares"] = max(int(float(ms)), 120)
     ```
     Same shape for
     `_env_get("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "HUNTER_MAX_SPREAD_CENTS", "HUNTER_MAX_SPREAD")`
     with `float(sp) / 100.0` **kept verbatim** plus the `HUNTER_MAX_SPREAD` warning, and for
     `_env_get("BOT_QUOTE_GRID_TICK_SIZE_USD", "HUNTER_PRICE_TICK", "HUNTER_TICK")` plus the
     `HUNTER_TICK` warning. Warning text, log level and ordering unchanged.
  3. Wide-book trial:
     `wide_raw, wide_name = _env_get("BOT_QUOTE_WIDE_BOOK_TRIAL", "HUNTER_WIDE_BOOK_TRIAL")` →
     `resolve_wide_book_trial(wide_raw or "", var_name=wide_name or "HUNTER_WIDE_BOOK_TRIAL")`. The
     guard gains `*, var_name: str = "HUNTER_WIDE_BOOK_TRIAL"` and uses it in both message strings;
     the `0.0`–`0.30` bound, rehearsal gate, `> 0` side effects and never-lower rule stay.
  4. Pairs rule `_env_get("BOT_RISK_GATE_PAIRS_RULE_ON", "HUNTER_PAIRS_RULE")`; latency chains
     `_env_get("BOT_LATENCY_NET_ONEWAY_MS", "HUNTER_NET_ONEWAY_MS", "HUNTER_CANCEL_NET_ONEWAY_MS")`,
     `_env_get("BOT_LATENCY_CANCEL_VENUE_ACK_MS", "HUNTER_CANCEL_VENUE_ACK_MS")`,
     `_env_get("BOT_LATENCY_POST_VENUE_ACCEPT_MS", "HUNTER_POST_VENUE_ACCEPT_MS")`; bankroll
     `_env_get("BOT_QUOTE_BANK_BASELINE_USD", "SPREAD_HUNTER_BANKROLL", "HUNTER_BANKROLL")`. The
     pairs-rule token set, the bankroll `isfinite`/`> 0` rejection message and the two-field write
     stay exactly as they are.
  5. Update the two docstrings that name only the legacy variable — `resolve_wide_book_trial`'s
     summary line and `load()`'s `HUNTER_DB … HUNTER_MARKET` paragraph — so the module stops implying
     the canonical names are someone else's business.
- **Verify:** T1's selection goes green; `python -m pytest -q tests/test_config_single_source.py
  tests/test_config_divergence_guard.py tests/test_wide_book_trial.py`.
- **Checkpoint:** all twelve canonical rows and all legacy rows green; divergence guard untouched and
  green, proving no logic leaked into the class body.

### [x] T3 — Prove warnings, blank fallthrough, refusal naming, and the ranker's `.env` path
- **Size:** S | **Domain:** `[Backend/Logic]` | **Skill:** `test-driven-development`
- **Depends on:** T2
- **Target files:** `tests/test_config_single_source.py`, `tests/test_ranker_reads_env_file.py`
- **What is built:**
  1. Parametrized `caplog` test over `HUNTER_MIN_SIZE` / `HUNTER_MAX_SPREAD` / `HUNTER_TICK` with a
     pinned market: oldest legacy name alone → the existing `deprecated (#632)` text at `WARNING`;
     canonical set alongside → **no** warning; newer legacy (`HUNTER_MIN_QUOTE_SHARES`,
     `HUNTER_MAX_SPREAD_CENTS`, `HUNTER_PRICE_TICK`) alone → no warning.
  2. Blank-canonical fallthrough, matching the existing `FILTER_*` blank tests:
     `BOT_QUOTE_SIZE_MIN_SHARES=""` + `HUNTER_MIN_SIZE="200"` → `200` **and** the warning fires — the
     documented exception becomes a pinned expectation instead of an accident.
  3. `BOT_QUOTE_WIDE_BOOK_TRIAL="0.12"` outside a declared rehearsal → `pytest.raises(ValueError)`
     naming `BOT_QUOTE_WIDE_BOOK_TRIAL`; `HUNTER_WIDE_BOOK_TRIAL="0.12"` keeps naming
     `HUNTER_WIDE_BOOK_TRIAL`; `"0.31"` inside a rehearsal is rejected by the bound.
  4. One end-to-end case in `tests/test_ranker_reads_env_file.py`, beside `_bar_with_generated_env`:
     a `_cfg_with_generated_env(tmp_path, contents, expr)` sibling that prints one field of `fm._CFG`
     after `_load_repo_env([root])` plus a rebuild of `scoring.config.load()`, driven by a `.env`
     holding `BOT_MARKET_PINNED_CONDITION_ID` and `BOT_QUOTE_SIZE_MIN_SHARES=200` and no legacy
     names. That is the operator's actual scenario — canonical names in `.env`, ranker subprocess —
     and the only place the whole path is proven.
- **Verify:** `python -m pytest -q tests/test_config_single_source.py
  tests/test_ranker_reads_env_file.py tests/test_wide_book_trial.py`.
- **Checkpoint:** every acceptance criterion in `SPEC.md` maps to at least one passing assertion.

### [x] T4 — Record the divergences, run the targeted suite, hand off
- **Size:** XS | **Domain:** `[Backend/Logic]` | **Skill:** `documentation-and-adrs`
- **Depends on:** T3
- **Target files:** `docs/issues/674-noticed-but-not-touching.md`
- **What is built:**
  1. The noticed-but-not-touching table — five rows: pinned `min_t_remaining_sec`, spread only when
     pinned, `BOT_QUOTE_TOURNAMENT_PRESET`, no display-only wide-book escape, warning text still
     pointing at a legacy alias — each with an owner and a timeline.
  2. `python -m pytest -q tests/test_config_single_source.py tests/test_config_divergence_guard.py
     tests/test_ranker_reads_env_file.py tests/test_wide_book_trial.py` — the ticket's command, with
     the non-existent file substituted — reported with real numbers.
  3. Handoff block for Station III: branch, owner-visible proof steps, PR notes (blank-value
     exception, pinned-only spread, canonical trial gating, corrected verification command).
- **Verify:** four suites green; `git status` shows only the intended paths.
- **Checkpoint:** station ready — code, tests, docs and the operator's hands-on steps all present.

---

## SPEC coverage

| Acceptance criterion | Task |
| --- | --- |
| AC-1 canonical alone overrides | T1 + T2 |
| AC-2 canonical wins over legacy | T1 + T2 |
| AC-3 legacy-only identical, warnings kept | T1 (legacy-in-turn) + T3.1 |
| AC-4 warning only from the oldest legacy name | T2.2 + T3.1 |
| AC-5 cent conversion `"20"` → `0.20`, both spellings | T2.2 + existing `test_wide_book_trial` case |
| AC-6 defaults unchanged | T1 (fields asserted against shipped values) + CI full suite |
| AC-7 canonical trial refusal outside rehearsal, named | T2.3 + T3.3 |
| AC-8 class body untouched | T2 checkpoint + divergence guard |
| AC-9 targeted suites green | T4.2 |

## Dependency graph

```
T1 (RED) ──> T2 (GREEN) ──> T3 (evidence) ──> T4 (docs + handoff)
```

