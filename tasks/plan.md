# Plan — #753 Retire the dead hold-lever surface left behind by #387

Branch: `i753/retire-dead-hold-levers` | Issue: #753

**Tier: Standard** (~20 files, no schema change, no new behaviour — a deletion
whose only decision is how far the dead surface reaches). **Type: Code**
[Backend/Logic] + [UI] (operator-facing copy).

## Intake note (CodeRabbit plan comment, read once — do not re-read)

- **Adopted:** its four-phase shape, the `.env.example` cleanup, the launcher
  rejection of the retired names, the arm-file cleanup, the neutral description of
  `price_moved` / `regate_pair_cost`, `_hold_state()` deletion on the grounds that
  it read the reporting process's config, and the new absence test file.
- **Rejected:** (a) its instruction to delete the two #485 cockpit forensic cards
  outright — they are the only record those runs happened; annotate the
  hypothesized tweak instead. (b) its claim that `.env` loading must gain explicit
  "ignore retired names" code — deleting the parsing is already sufficient, and a
  list of retired names would be new dead code. (c) `test_config_single_source.py`
  and `test_ui_276_explainer_tokens.py` as files needing changes — both grep clean
  for the retired symbols; they are regression guards only.
- **Resolved `[UNVERIFIED]` items** (checked against live code):
  - *"`plan_orders` still accepts four unread parameters"* → **false count**: it
    accepts **six** unread ones. `hedge_held` (line 219) and `visit_outcome`
    (line 224) are equally unread; the docstring itself lists all seven survivors
    and says `only dead_band and cfg.bot_quote_size_min_shares are read here`
    (lines 271-274).
  - *"The production call site never passes the hold arguments"* → **true**, and
    it does pass the other four (`trader_loop.py:2143-2151`).
  - *"`hedge_asks` and `queue_ahead` exist only to feed the retired holds"* →
    **half false**: `queue_ahead` also feeds `cancel_queue_ahead` attribution
    (`trader_loop.py:2286`); `hedge_asks`/`hedge_held` are planner-only and die.
  - *"`visit_outcome` can be dropped from the call site"* → **false**:
    `visit_outcome.value` is written to the registry by `_note_refusal_visit`
    (line 2141). Compute it; stop passing it.
  - *"`cfg` is dead"* (issue finding 1) → **false**: read for
    `bot_quote_size_min_shares`. It stays.
  - *"`configs/tournaments/*.json` carry `hold_queue_shares` keys"* → **false
    naming**: the arm files set the *environment* names
    (`BOT_ORDER_REQUOTE_HOLD_*`) inside `env`, never the field names.

## Interface contract (locked)

```python
def plan_orders(
    open_orders: list[dict], intents: list[QuoteIntent], price_eps: float = 1e-9, *,
    dead_band: float = 0.0, cfg=None, reasons: Optional[dict] = None,
    preserve_order_ids: Optional[set[str] | frozenset[str]] = None,
    replace_order_ids: Optional[set[str] | frozenset[str]] = None,
    cancel_order_ids: Optional[set[str] | frozenset[str]] = None,
    lifecycle_pair_id: str | None = None,
) -> tuple[list[dict], list[QuoteIntent]]
```

- `MakerConfig`: the two `bot_order_requote_hold_*` fields, their `_bounded_float`
  parsing and their bounds disappear. No replacement field, no deprecation shim.
- `TOURNAMENT_PRESETS`: same six names, same non-hold keys, descriptions rewritten
  to name only live distinguishing settings.
- `scripts.shadow_tournament`: `_RETIRED_PRESET_KEYS` gains
  `bot_order_requote_hold_queue_shares`, `bot_order_requote_hold_below_target_cents`;
  a new retired-environment set
  (`BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES`, `BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS`)
  is rejected on the `arm["env"]` path with a message naming the setting and #387.
- `core_brain.cancel_report`: `_hold_state()` removed; `format_report` keeps the
  `{share:.0f}% of cancels were price moves` line and drops the two sentences
  after it.
- `dashboard/server.py`: two parameter cards removed; `/api/parameters` shape and
  every other card unchanged.
- `dashboard/static/strategy_explainer.html`: two `<tr>` rows removed, design
  tokens untouched.

## Dependency graph

```
T1 (config surface) ──┬─→ T3 (operator surfaces: cards, explainer, cockpit)
T2 (planner surface) ─┴─→ T4 (launcher + arm files + docs)
                              T4 ──→ T5 (absence test file)
```

T1 and T2 are independent of each other (the planner never imports the fields) but
land in the same PR; T3 depends on T1 because both surfaces read the deleted
attributes. T5 asserts the end state and therefore comes last.

## Tasks

### T1 · [Backend/Logic] · S · Delete the config surface — ✅ done
**Files:** `core_brain/config.py`, `.env.example`,
`tests/test_dynamic_offset.py`, `tests/test_completable_pair_gate.py`
**Depends on:** none
**Build:** remove both `MakerConfig` fields (lines 197-198) and their parsing /
bounds (lines ~375-383); remove both keys from all six presets (lines 526-581) and
rewrite the four `… with queue hold` descriptions plus the hybrid description that
advertises `queue stability`; drop the two variables, their unit notes from
`.env.example` (lines 54-55). Update the tests that pin the preset values (155-199) and the
`load()` bound tests (209-238) — the latter become "the retired variable is
ignored", asserted in T5.
**Verify:** `python -m pytest -q tests/test_dynamic_offset.py
tests/test_completable_pair_gate.py tests/test_loose_baseline.py
tests/test_screener_preset.py tests/test_config_single_source.py`

### T2 · [Backend/Logic] · M · Trim the planner surface, keep the behaviour — ✅ done
**Files:** `core_brain/trader_loop.py`, `tests/test_plan_orders_asymmetric_hold.py`,
`tests/test_trader_loop.py`, `tests/test_cancel_attribution.py`
**Depends on:** none
**Build:** drop the six unread parameters from the signature (lines 218-224) and
the four keys from `plan_kwargs` (2146-2150); delete the now-consumer-less
`hedge_asks` / `hedge_held` blocks (2081-2100) but **keep** `queue_ahead`,
`visit_outcome.value` and `_queue_ahead_for` because they feed attribution and the
registry write. Replace the two `RETIRED BY #387` docstring blocks (232-274) with
one paragraph: a wanted resting order is kept at its own price regardless of price
drift or queue position. No change to the body. Delete the hold tests in
`test_plan_orders_asymmetric_hold.py` (51-145, 174-199 incl. `TestConfigWiring`),
the hold cases in `test_trader_loop.py` (255-312) and its 387-406 case, and the
hold kwargs in `test_cancel_attribution.py` (132-232, 625, 710, 726) while keeping
every assertion those tests make about live behaviour.
**Verify:** focused pytest on those three files + `test_completable_pair_gate.py`
+ `test_live_e2e_lifecycle.py`.

### T3 · [UI] · S · Clean the three operator surfaces
**Files:** `dashboard/server.py`, `dashboard/static/strategy_explainer.html`,
`scripts/tournament_cockpit_server.py`, `tests/test_dashboard_server.py`,
`tests/test_shadow_tournament.py`
**Depends on:** T1
**Build:** delete the two parameter cards (`server.py:5128-5158`); delete the two
explainer rows (`strategy_explainer.html:887,893`); delete the "Queue Hold
Threshold" and "Hold Deviation Buffer" rows from the HYBRID blueprint table
(`tournament_cockpit_server.py:1715-1728`) and annotate the two #485 card tweaks
(`128-131`, `142-145`) as retired by #387. Update the dashboard env-key map
(`test_dashboard_server.py:3414-3415`) and the blueprint key list
(`test_shadow_tournament.py:1275-1287`).
**Verify:** focused pytest on both test files + `test_ui_276_explainer_tokens.py`;
then open `/api/parameters` and the explainer in the browser.

### T4 · [Backend/Logic] · S · Launcher rejection, arm files, written record
**Files:** `scripts/shadow_tournament.py`,
`configs/tournaments/i492_tournament-1_arms.json`,
`configs/tournaments/i608_tournament-1_arms.json`,
`tests/test_shadow_tournament.py`, `docs/issues/487-noticed-but-not-touching.md`,
`docs/ideas/variable-names-refactor.md`
**Depends on:** T2
**Build:** extend `_RETIRED_PRESET_KEYS` with both field names and add a retired
environment check on the `arm["env"]` path (`shadow_tournament.py:326-333`) whose
error names the setting and #387; remove the `BOT_ORDER_REQUOTE_HOLD_*` entries
from `stickhold`, `queuehug`, `deadband-tight`, `deadband-wide`, `snapstop`,
`hybrid` (i492) and delete the now-empty `the-gambler` / `the-chaperone` arms
(i608). Update `test_shadow_tournament.py`: 671-672 and 992-993 expectations,
918-961 and 1160-1170 fixtures, 1279-1280 blueprint keys; convert
`test_a_supported_legacy_alias_still_plans` (1152) into a rejection test. Mark
N1-N3 resolved in the #487 noticed-but-not-touching file and drop the proposed
hold renames from the variable-names idea file.
**Verify:** focused pytest on `test_shadow_tournament.py`, then build a plan from
each committed arm file (no shadow minutes spent) to confirm they still load.

### T5 · [Backend/Logic] · S · Absence test (the guard that ends this class of bug)
**Files:** `tests/test_requote_hold_retired.py` (new; module shape mirrors
`tests/test_refusal_hold_decision.py`)
**Depends on:** T1, T2, T3, T4
**Build:** assert `inspect.signature(plan_orders)` carries none of the six names;
`MakerConfig` has neither field; no preset carries either key and no preset
description mentions a queue hold; setting either `BOT_ORDER_REQUOTE_HOLD_*`
variable (valid and invalid) leaves `load()` untouched; `/api/parameters` has no
hold card and the explainer HTML carries neither identifier; `format_report`
contains no hold advice; the launcher raises for both environment names in
`arm["env"]` and for both field names in a preset dict.
**Verify:** the file fails on `git stash` of the change and passes after it.

## Checkpoints

- **After T2:** planner + config clean; focused planner/config checks green.
- **After T4:**
  `rg -n "hold_queue_shares|hold_below_target|REQUOTE_HOLD" core_brain/ scripts/
  dashboard/ configs/ .env.example` returns only the historical cockpit
  annotations.
- **After T5:** full focused set green, absence test red without the change.

## Step 5 — improvement proposal (adopted; classification: simplification)

Evidence — `core_brain/trader_loop.py:271-274`, verified by reading the body and
grepping every parameter name:

> `parameters stay in the signature -- `cfg`, `hedge_asks`, `hedge_held`,
> `queue_ahead`, `hold_queue_shares`, `hold_below_target`, `visit_outcome` --
> for the follow-up cancel-conditions issue to disposition; only `dead_band`
> and `cfg.bot_quote_size_min_shares` are read here.`

The comment names seven survivors and then says which two are read — so the dead
set is **six** parameters, not the issue's three and not CodeRabbit's four.
Removing all six in one pass deletes the follow-up work this comment defers and
stops the next reader re-deriving it. No new behaviour, no new file. Folded into
T2.

## Trap recorded for Station III

`hedge_asks` / `hedge_held` look live because they are computed from `ev` books
under a long comment about the hedge ask. Their only consumer is `plan_kwargs`.
Delete the computation with the parameter, or the deletion leaves dead code that
reads as an intentional mechanism.

## Out of scope (recorded, not touched)

- `docs/runs/*` — dated run records (e.g.
  `2026-10-04-queue-hold-200-rehearsal.md`) keep their mentions; they are evidence
  of what was tried.
- `docs/issues/487-noticed-but-not-touching.md` keeps its findings text; only the
  status line is updated (T4).

## How to verify (hands-on, operator)

1. `python -m dashboard.server` → open `http://127.0.0.1:8799` → Parameters: the
   two knobs *Queue Hold* and *Direction Hold* are gone; requote cadence, dead
   band and every other card still render with their usual values.
2. Same dashboard → strategy explainer: the rows describing "holds passive
   resting quote up to 5¢ below target" and "protects valuable queue position" are
   gone; the rows around them are unchanged.
3. `python -m core_brain.cancel_report --db <any shadow db>` → the reason table is
   unchanged and the trailing paragraph no longer claims the holds can keep a
   price move.
4. `.\scripts\spread-hunter-menu.ps1 status` → still reports the stack; open the
   tournament cockpit and confirm the HYBRID blueprint table no longer lists the
   two hold rows, while the #485 cards still show their runs.


