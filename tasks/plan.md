# Station II Plan — Issue #872: Unify all currency settings to integer cents

Branch: `i872/unify-all-currency-settings-to-integer-cents` | Issue: #872

- **Tier:** Large (config schema + 8 production call sites + 15 test files + shipped inputs + docs)
- **Task types:** Code (primary), Docs, UX/Copy (dashboard parameter copy), Testing
- **Stack / runner:** Python 3, `pytest` (`python -m pytest -q`)

---

## 1. The problem, in one line

Three settings end in `_CENTS` and do not all hold cents. `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=6`
means 6¢; `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.10` means 10¢, not 0.10¢. The issue names the
defect precisely:

> The code bounds enforce the difference correctly; the footgun is the name.

So the deliverable is: **a name never lies about its unit.** Everything else is means.

## 2. The contradiction that reshaped the approach (resolved from code, not guessed)

The issue asks for integer cents on all three keys and, in the same body, rules out the
consequence:

> **Out of scope:**
> - Any runtime behavior change — the engine must keep applying the same numeric values.

Those two cannot both hold for the adverse-drop stop, because the engine's live champion runs
that stop at **4.5¢**, and 4.5¢ has no integer-cent representation.

Evidence, all verified in the working tree:

| Evidence | Location |
| --- | --- |
| Champion env sets `"BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.045"` | `tests/test_champion_lane_parity.py:26` |
| Champion assertion pins `bot_risk_stop_adverse_drop_cents == approx(0.045)` | `tests/test_champion_lane_parity.py:69` |
| Live lane audit records the same value | `docs/live-lane-audit.md:43` |
| Cockpit advertises `0.045` (4.5¢) as the live champion stop | `scripts/tournament_cockpit_server.py:1736`, `:1811`, `:1842`, `:2001` |
| Rejection bounds are dollars: `DROP_CENTS_MIN = 0.01`, `DROP_CENTS_MAX = 0.90` | `core_brain/single_leg_lifecycle.py:29-30` |
| The trigger rounds up to a whole cent, so "just use 4 or 5" looks tempting | `core_brain/single_leg_lifecycle.py:437` (`(entry_decimal - drop_value).quantize(cent, rounding=ROUND_CEILING)`) |

The rounding does **not** rescue integer cents. Measured, not assumed:

```
entry   drop0.045  drop0.04  drop0.05
0.5     0.46       0.46       0.45
0.512   0.47       0.48       0.47     <- 0.04 diverges
0.53    0.49       0.49       0.48
0.47    0.43       0.43       0.42
0.618   0.58       0.58       0.57
0.33    0.29       0.29       0.28
entries where no integer-cent value matches 0.045: 6 / 6
```

Sub-cent entry averages (partial fills at several prices) make `0.045` land on a different cent
than either `0.04` or `0.05`. Forcing integer cents on that key therefore **moves a live stop
by up to one cent**, which is exactly what the issue's Out-of-scope line forbids.

### Decision — Option A: honest units, not unit conversion

- `bot_order_requote_dead_band_cents` (holds dollars) → **`bot_order_requote_dead_band_usd`**
- `bot_risk_stop_adverse_drop_cents` (holds dollars) → **`bot_risk_stop_adverse_drop_usd`**
- `bot_quote_spread_offset_min_cents` / `_max_cents` (already integer cents) → **unchanged names**,
  but their loader stops silently truncating fractions
- `DROP_CENTS_MIN` / `DROP_CENTS_MAX` → **`DROP_USD_MIN` / `DROP_USD_MAX`**

Each name now states the unit it actually holds, which is the issue's stated goal, and every
numeric value the engine applies is bit-identical to today. This is a **simplification of the
issue's proposed means**, adopted by default per the Adoption rule; the literal "convert the
stop to integer cents" reading is recorded as rejected in §8 with the measurement above.

**Reversal cost if the operator prefers literal integer cents:** the champion stop becomes
`5` (or `4`), `tests/test_champion_lane_parity.py`, the cockpit copy, `docs/live-lane-audit.md`
and the `.env` on the live host move with it. That is a separate decision about the stop
distance, not a config-format decision, which is why it is not smuggled into this change.

## 3. CodeRabbit intake (read once; Stations III–V need not reopen it)

Adopted: the three-task skeleton; store-an-int / read-as-int discipline for the offset keys; one
warning per key per process via a module-level set; a new `tests/test_config.py` (it does not
exist today — verified) because the issue's own test command names it; the arms-JSON integer gate
test; the `.env.example` inline unit comments; the two `docs/agents/strategy.md` lines.

Rejected:
- *"Store dead band and stop drop as integer `3` / `5`, divide by 100 at each reader"* — breaks the
  4.5¢ champion (measurement above) and re-introduces a ÷100 hop aimed at exactly the class of
  bug this issue is about.
- *Use `decimal` for the legacy conversion* — no conversion exists under Option A, so the import
  and the sub-cent rejection path it served are both unnecessary. No `Decimal` is introduced.
- *Reject `0.045`, and if the owner declines keep the stop in dollars* — inverted: it proposes
  breaking the champion first and falling back to the correct answer.

Left `[UNVERIFIED]` and checked during Build instead of trusted: the exact line numbers of every
dashboard/`get_parameters` card, whether `tests/test_config_single_source.py` pins these two
defaults in a snapshot, and whether cockpit tests assert the suggestion strings verbatim. All
three were checked before editing; results are in the task bodies below.

## 4. Interface contract (locked before code)

**Config fields (`core_brain/config.py`, `MakerConfig`)**

| Field | Type | Unit | Default | Bounds |
| --- | --- | --- | --- | --- |
| `bot_order_requote_dead_band_usd` | `float` | USD per share, fraction of $1.00 | `0.03` | `0.0 .. 1.0` |
| `bot_risk_stop_adverse_drop_usd` | `float` | USD per share | `0.05` | `DROP_USD_MIN .. DROP_USD_MAX` (`0.01 .. 0.90`) |
| `bot_quote_spread_offset_min_cents` | `int` | whole cents | `1` | `1 .. 10` |
| `bot_quote_spread_offset_max_cents` | `int` | whole cents | `20` | `1 .. 20` |

**Env vars:** `BOT_ORDER_REQUOTE_DEAD_BAND_USD`, `BOT_RISK_STOP_ADVERSE_DROP_USD`,
`BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS`, `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS`.

**Alias window (the issue's "old keys work as aliases" requirement).** The two retired `_CENTS`
spellings are still read when the new name is unset. They carry the *same* numeric value — this is
a rename, not a conversion — so no precision is at risk:

- one `WARNING` per key per process, via a module-level `set` reset by the test fixture;
- text: `DEPRECATED: BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.045 is retired; read as
  bot_risk_stop_adverse_drop_usd=0.045. Write BOT_RISK_STOP_ADVERSE_DROP_USD=0.045.
  The old name stops working after the deprecation window.`
- new name wins when both are set; a blank new name falls through to the old one.

This is a deliberate, documented, time-boxed exception to the repo's "a retired name is inert"
rule (`docs/agents/glossary.md:94-100`). Inert is wrong here: silently ignoring
`BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.10` would quietly run 3¢ instead of 10¢ — a silent
behaviour change, the failure this repo refuses everywhere else (see the #707 "rejects, never
clamps, never a silent ignore" comments at `core_brain/config.py:330-345`).

**Whole-cents parser.** Offset keys reject text that is not an integer, naming the key and the
unit — `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS='19.9' must be whole cents, for example 6 = 6¢`.
Today `19.9` silently truncates to `19` (verified). `6.0` is rejected as of this change; no arms
file or `.env.example` uses a decimal form for these two keys (verified by grep).

**Dashboard contract (`GET /api/parameters`).** Card count stays 57. `value`, `raw_value`,
`trigger`, `default` render byte-identically (`$0.03`, `$0.05`, `20¢`). Only `name`, `key` and
`env_var` move to the new spellings. `tests/test_dashboard_server.py:3652-3658` requires every
non-empty `env_var` to appear in `.env.example`, so the card rename and the `.env.example`
rename ship in the same commit.

**Must not change:** engine arithmetic and trigger values; risk ratios
(`bot_risk_exposure_*_pct`, `bot_quote_*_pct`, `bot_risk_*_pct`); `data/orders.db`; saved
`data/tournaments/*/plan.json`; the merge/settlement path; any `Decimal` in money math.

## 5. Tasks

### [x] T1 — Whole-cent strictness for the two offset keys · S · `[Backend/Logic]`
Depends on: —

- `core_brain/config.py`: add `_whole_cents(name, raw, lo, hi)` next to `_bounded_float()`
  (`:720`). Integer text only, inclusive bounds, `ValueError` naming the key and the unit.
  Use it for `BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS` (`:541-545`) and `..._MAX_CENTS` (`:547-550`),
  replacing `int(_bounded_float(...))`.
- Seed `tests/test_config.py` with an autouse fixture that deletes every `BOT_*` variable and
  resets the deprecation set, plus: integer text accepted, bounds inclusive, `19.9`/`6.0`/`abc`/
  `nan`/`inf` rejected, blank keeps the default/preset.
- Verify: `python -m pytest -q tests/test_config.py tests/test_dynamic_offset.py tests/test_shadow_tournament.py -k "offset or cents or preset"`

### [ ] T2 — Honest USD names for the two dollar keys · L · `[Backend/Logic]`
Depends on: T1 (same loader region)

One atomic rename; a half-renamed config does not import, so this does not split further.

- `core_brain/config.py`: rename the two fields, their loader blocks (`:397-400`, `:456-459`),
  their unit comments (`:208`, `:247`), the `hybrid` preset key (`:605`), and add the alias
  reader.
- `core_brain/single_leg_lifecycle.py`: `DROP_CENTS_MIN/MAX` → `DROP_USD_MIN/MAX`; the
  `stop_trigger` / `_validated_stop_knobs` / `evaluate_single_leg_lifecycle` parameters and the
  `ValueError` text.
- Readers: `core_brain/trader_loop.py:2229` and `:1751`; `core_brain/risk_guardian.py:93-94`
  (fallback stays `0.05`); `core_brain/single_buy_saver.py:2290` (fallback stays `0.05`);
  `scripts/stream_market_decisions.py:172`; `scripts/verify_loose_baseline_tape.py:179,195`;
  `scripts/shadow_tournament.py:281-282`; `scripts/tournament_hub.py:151` (read new key, fall back
  to the old one for saved plans).
- `dashboard/server.py`: the two `get_parameters` cards (dead band, adverse drop) — `name`,
  `key`, `env_var` only.
- `.env.example:53,62`: new names, unit comments corrected to "USD per share".
- Tests: mechanical rename of the field/kwarg/keys in `test_config_single_source.py`,
  `test_completable_pair_gate.py`, `test_stop_loss_config.py`, `test_single_leg_lifecycle.py`,
  `test_dual_stop_loss.py`, `test_risk_guardian.py`, `test_single_buy_saver.py`,
  `test_trader_loop.py`, `test_dynamic_offset.py`, `test_aged_out_rescue.py`,
  `test_champion_lane_parity.py` (value stays `0.045`), `test_shadow_tournament.py`,
  `test_dashboard_server.py`, `test_tournament_arms_i843.py`, `test_tournament_cockpit_arms.py`
  where the key name (not the copy) appears.
- **Expected values do not move.** `0.05` stays `0.05`, `0.045` stays `0.045`, `0.060` stays
  `0.060`. Any test whose expectation changes is evidence of a unit slip — stop and re-read.
- Verify: `python -m pytest -q tests/test_config.py tests/test_stop_loss_config.py tests/test_single_leg_lifecycle.py tests/test_dual_stop_loss.py tests/test_risk_guardian.py tests/test_champion_lane_parity.py tests/test_dashboard_server.py tests/test_trader_loop.py tests/test_completable_pair_gate.py`

### [ ] T3 — Shipped inputs, docs, cockpit copy, inventory · M · `[Docs]`
Depends on: T2

- `configs/tournaments/i799-tournament-test_arms.json`: env keys → new names; values unchanged
  (`"0.10"`, `"0.080"`, `"0.020"`, `"0.090"`, `"6"`); `hypothesized_tweak` strings → new names.
  `configs/tournaments/i843-pair-test_arms.json` has no `_CENTS` keys — untouched (verified).
- `docs/agents/strategy.md:121` ("in DOLLARS per share despite the name" — now the name says it)
  and `:149,155`; `docs/live-lane-audit.md:41-43`.
- `scripts/tournament_cockpit_server.py` suggested snippets/labels (`:1722`, `:1736`, `:1810-1812`,
  `:1841-1843`, `:2000-2001`) + the cockpit tests that assert those strings.
- `docs/backlog/i872-currency-unit-inventory.md`: the remaining 13 `_cents` fields with their real
  units (e.g. `bot_quote_price_band_min_cents = 0.10` is **dollars**; `filter_live_move_min_range_cents
  = 2.0` is **cents**; `bot_pos_skew_max_offset_cents = 0.015` is **dollars**) and why each needs its
  own decision — several hold sub-cent values, so an int migration there is a behaviour change.
- Verify: `python -m pytest -q tests/test_shadow_tournament.py tests/test_tournament_cockpit_arms.py tests/test_tournament_arms_i843.py`

### [ ] T4 — Gates and handoff · S · `[Testing]`
Depends on: T1, T2, T3

- Grep gate: no production `*_cents` identifier holds a dollar fraction.
  `grep -rn "_cents" --include=*.py core_brain/ scoring/ dashboard/ scripts/` reviewed by hand.
- Targeted suites from T1–T3 in one run.
- Operator "How to verify" block (hands-on only, no pytest) + PR body.

**Checkpoints:** after T1 (offset strictness green), after T2 (rename + numbers unchanged), after
T3 (grep + docs), after T4 (targeted run + handoff). In `auto` mode these are one-line reports.

## 6. Deliberately out of scope

- The other 13 `_cents` fields → inventory in T3 + follow-up issue. Their units differ per field and
  several hold sub-cent values (`3.68`, `2.05`, `0.015`), so each is its own decision.
- Dropping the retired `_CENTS` spellings → follow-up issue, after the deprecation window closes.
- Any dashboard currency *formatting* change — the issue puts presentation out of scope, and the
  card text must not move.
- Converting the adverse-drop stop to integer cents → rejected with measurement in §2/§8.
- `Decimal` in money math, risk-ratio changes, merge/settlement arithmetic, `data/orders.db`.

## 7. Open questions from the issue, answered from code

1. *Naming convention* — Option A, but applied as **honest unit suffixes** (`_USD` / `_CENTS`)
   rather than "everything becomes cents". Reason: the code's existing convention already spells
   dollars `_usd` (`bot_pos_price_max_pair_cost_usd`, `bot_quote_bank_baseline_usd`), so the
   repo has settled the naming question; the two keys were simply misnamed.
2. *Deprecation window* — aliases read with one warning per key per process; removal is a
   follow-up issue. No timer in code, matching how #732 retired names.
3. *Are there other `_CENTS` settings?* — Yes, 13. Inventoried in T3; none converted this round.
   Full grep archived in `docs/backlog/i872-currency-unit-inventory.md`.

## 8. Rejected proposals log

| Proposal | Verdict | Why |
| --- | --- | --- |
| Store the two dollar keys as integer cents, ÷100 at readers | Rejected | Moves the 4.5¢ live champion stop by up to 1¢ (measured, §2); re-creates a ÷100 hop |
| `decimal` module for exact legacy conversion | Rejected | No conversion exists under Option A |
| Reject `0.045` and fall back to dollars only if the owner objects | Rejected (inverted) | Breaks the documented champion first, then falls back to the right answer |
| `*_CENTS_INT` / `*_DOLLAR_FRACTION` new names | Rejected | No existing file uses either; `_USD` already is this repo's dollar spelling |
| Rename remaining 13 `_cents` fields too | Deferred | Mixed units and sub-cent values; needs per-field decisions, recorded for a follow-up |
