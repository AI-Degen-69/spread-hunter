Branch: i717/remove-retired-reward-vocabulary | Issue: #717

# Plan — #717 Remove the retired reward vocabulary from the quoting engine

- Size: **Small** — one function deleted, four visible strings reworded, four test pins
  re-pointed, plus the comment/docstring sweep the acceptance search forces. No logic, no
  new module, no new dependency.
- Task type: **Code** (wording + dead-code removal on the money path) + **Docs**
  (operator-facing prose that names the retired model).
- Labels: `ready-for-agent` only — no `quick-fix` label, so no lane divert (Step 0C skipped
  per rule 1).
- Base: `origin/main` @ `4e12ce6` (HEAD == `origin/main` at branch time; `main` itself is
  checked out in another worktree, so the branch was cut from the same SHA instead of
  switching).

## CodeRabbit plan intake (read once, verified against the tree)

- **Adopted:** the wording map (distance refusal → "quote window"; truncation refusal →
  "quote window truncated"; price-risk refusal → "venue minimum"; success reason →
  "quote {distance}c under mid {mid}" with the waiver and dynamic-offset suffixes kept);
  "venue minimum" over "quote minimum" because the same output already says "venue floor"
  and "venue minimum"; dropping the stale "4.5c" figure from `unhedged_stop_loss.py` (the
  shipped engine window default is 0.15, so a plain word swap would write a new false
  claim); the 3-task shape; the "re-point, never delete" rule for the test pins; the
  four-command acceptance proof.
- **Rejected:** CodeRabbit's optional phase (an added assertion, a document sync, a
  shadow-only operator check). The document sync is folded into T3 anyway, because
  `docs/agents/strategy.md:83` names `quotes.reward_score()` and that sentence becomes
  false the moment the function is deleted. The added assertion and the shadow check are
  not requested by the issue and are skipped.
- **Verified seams (all exist verbatim, read in this session):** `reward_score()` at
  `core_brain/quotes.py:108` (def at 108, docstring 109-119, body 120-122); the call site
  at `core_brain/quotes.py:574`; the distance refusal at `:511-513`; the truncation refusal
  at `:559-562`; the price-risk refusal at `:563-566`; the success reason at `:572-576`;
  the ZERO ALLOCATION comment at `:342-353`; `risk.size_for` at `core_brain/risk.py:87` with
  the `quote_shares <= 0` guard at `:123-124` **before** the sizing-mode branch, so the
  guard holds in shares mode *and* dollars mode; `market_lifecycle.LifecycleStop` markers
  (`core_brain/market_lifecycle.py:26-36`) — none of them contains "reward", "quote" or
  "window", so no reworded string can accidentally match a terminal stop;
  the four pins at `tests/test_trader_loop.py:1517-1518`, `:1530-1531`, `:3198-3199` and
  `tests/test_shadow_run.py:1652-1653`.
- **`[UNVERIFIED]` from code:** none left open. CodeRabbit's claim that the venue's
  "rewardsMinSize is 50" floor is what `min_quote_shares` came from was checked against
  `core_brain/risk.py:109-112` — that docstring still carries the retired rationale and is
  recorded as an out-of-scope row, not edited here.

## Spec (embedded — Small tier, no SPEC.md)

- **Goal:** the quoting engine stops speaking a retired liquidity-reward model, and the
  operator-visible strings say what they actually measure.
- **The strings, old → new (numbers unchanged, order unchanged, suffixes unchanged):**

  | Site | Old | New |
  |---|---|---|
  | distance refusal (`quotes.py:513`) | `"{side}: {d}c from mid > {w}c reward window"` | `"{side}: {d}c from mid > {w}c quote window"` |
  | truncation refusal (`quotes.py:560`) | `"{side}: reward window truncated the quote to {p}% of the distance asked for, cutting {ladder}sh under the {min}sh minimum"` | `"{side}: quote window truncated the quote to {p}% of the distance asked for, cutting {ladder}sh under the {min}sh minimum"` |
  | price-risk refusal (`quotes.py:566`) | `"{side}: price risk at {price} cut {ladder}sh to under the {min}sh reward minimum"` | `"{side}: price risk at {price} cut {ladder}sh to under the {min}sh venue minimum"` |
  | success reason (`quotes.py:573-576`) | `"reward quote {d}c under mid {mid}, score {N}{waived}{dyn_note}"` | `"quote {d}c under mid {mid}{waived}{dyn_note}"` |

- **Dead code:** `reward_score()` and its docstring are deleted. The function had exactly one
  caller (the score figure in the success reason) and fed no sizing decision.
- **Stale comment:** the ZERO ALLOCATION comment stops crediting `reallocate`, a function
  that exists nowhere in the tree, and stops crediting the retired `U4` milestone. It keeps
  the headline, states today's fact (a nonpositive `quote_shares` is an explicit no-quote
  signal, and `risk.size_for` returns 0 for it in every sizing mode so a 0 can never be
  promoted to a minimum-size order), and keeps one line of the past incident.
- **Edge cases:**
  - The refusal classifier must keep its verdict: no reworded string contains a
    `LifecycleStop` marker, so every one of them stays transient.
  - The waiver suffix (`", price-risk taper x… waived: …sh is under the {min}sh venue
    floor"`) is asserted verbatim by `tests/test_live_quotes.py:159-160` and must not move.
  - The couple-allocation refusal (`"… below the venue minimum of {min}sh"`) is asserted
    verbatim by `tests/test_live_quotes.py:177` and must not move.
  - The `"clamped"` note is appended later, in `_require_two_sided`
    (`core_brain/quotes.py:929`), and is asserted by `tests/test_live_quotes.py:295`.
  - The zero-allocation refusal text (`"unfunded by the allocator -- quoting nothing"`) is
    a `LifecycleStop` marker and must not move.
- **Out of scope:** the KPI / PnL cross-check / venue-fee contracts, the `rebate_est`
  schema, any default, env var name, price, size, gate or message on the money path, the
  `reward_offset` family of config key names, the venue's own `rewards` payload fields, and
  the #708 ledger's history rows.

## Interface contracts (frozen before build)

- `core_brain.quotes` **loses** the name `reward_score`. Every other public name in the
  module stays: `mid_price`, `dynamic_offset_for`, `quote_resting_price`, `decide_quotes`,
  `QuoteIntent`, `pair_cost_over_cap`, `max_profitable_hedge_bid`.
- `QuoteIntent.reason` stays a `str` and stays a free-text operator-facing sentence. Its
  shape changes only by the removal of the `"reward quote …, score N"` prefix.
- `decide_quotes(...)` keeps its signature, its return shape and every refusal string that
  is not listed in the spec table above — byte-identical.
- `risk.size_for(...)` keeps its signature and its `quote_shares <= 0 → 0` guard.
- `market_lifecycle.classify_refusal(...)` keeps its signature and its marker table.

## Improvement proposal (adopt-by-default, hardening)

- **Proposal:** add one guard test that asserts the retired vocabulary stays out of the
  code tree, so a future string cannot reintroduce it silently.
- **Evidence (issue Acceptance criteria, verbatim):** "`git grep -n \"reward window\\|reward
  quote\\|reward minimum\" -- core_brain/ tests/` returns nothing." A grep the agent runs
  once at review time proves the ticket; a test in the suite proves it forever.
- **Classification:** simplification / drift prevention, no behaviour change, no new
  production code. Adopt-by-default into T2.

## Dependency graph & tasks (risk-first, 3 tasks)

T1 (the money path) unblocks T2 (the sweep + the guard) unblocks T3 (the docs).

- [ ] **T1 (S) [Backend/Logic]** — Reword the four visible strings in `core_brain/quotes.py`,
  delete `reward_score()` and its call site, and rewrite the ZERO ALLOCATION comment so it
  names no function that does not exist.
  Targets: `core_brain/quotes.py` only.
  Depends on: —. Skill: `test-driven-development` (existing pins), `code-simplification`.
  Verify: `python -m pytest -q tests/test_quotes.py tests/test_live_quotes.py
  tests/test_legacy_ask_quotes.py tests/test_dynamic_offset.py
  tests/test_completable_pair_gate.py tests/test_market_quote.py`
  plus `python -c "import core_brain.quotes as q; assert not hasattr(q, 'reward_score')"`.
- [ ] **T2 (S) [Backend/Logic]** — Clear every remaining acceptance-search hit in
  `core_brain/` and `tests/`, re-point the four transient-refusal pins, and add the
  vocabulary guard test.
  Targets: `core_brain/risk.py:280`, `core_brain/trader_loop.py:118`,
  `core_brain/unhedged_stop_loss.py:8,106`, `core_brain/config.py:921` (the "15c score
  window" preset description), `tests/test_trader_loop.py:1517-1518,1530-1531,3198-3199`,
  `tests/test_shadow_run.py:1652-1653`, `tests/test_rewards_purge.py:132`,
  `tests/test_unified_universe.py:333`, and a new guard in `tests/test_quotes.py`.
  Depends on: T1. Skill: `test-driven-development`, `incremental-implementation`.
  Verify: `python -m pytest -q tests/test_trader_loop.py tests/test_shadow_run.py
  tests/test_live_quotes.py tests/test_quotes.py tests/test_risk.py
  tests/test_wide_book_trial.py tests/test_rewards_purge.py
  tests/test_unified_universe.py` + the acceptance greps.
- [ ] **T3 (S) [Docs]** — Drop the retired vocabulary from the operator-facing prose the
  change makes false, and record the out-of-scope findings in the ledger.
  Targets: `docs/agents/strategy.md:83` (names `quotes.reward_score()` — false once T1
  lands), `scripts/book_tape_recorder.py:22`, `docs/explanations/grace-hold-explainer.html`
  (×2, mirrors `trader_loop.py:118`),
  `docs/explanations/prudent_profile_and_strategy_guide.html` (the Hebrew "Reward Window
  … Liquidity Rewards" claim), `docs/issues/717-noticed-but-not-touching.md` (new ledger).
  Depends on: T1, T2. Skill: `documentation-and-adrs`, `humanizer`.
  Verify: the acceptance greps stay empty, `python -m pytest -q tests/test_docs_layout.py`
  passes, and the whole-repo case-insensitive grep is reviewed by hand.

## Checkpoints

- After T1: the four strings carry the new wording, `reward_score` is gone from the module
  namespace, the quoting suites are green.
- After T2: the acceptance search over `core_brain/` and `tests/` is empty, the four pins
  hold the new text and still assert transient, and the guard test fails if the vocabulary
  comes back.
- After T3: the operator-facing prose matches the code, and the ledger carries every
  out-of-scope find with an owner and a when.

## Acceptance proof (run at T2 and again after any review fix)

1. `git grep -n "reward window\|reward quote\|reward minimum" -- core_brain/ tests/` → empty
2. `git grep -n -i "reward window\|reward quote\|reward minimum" -- core_brain/ tests/` → empty
3. `git grep -n "reward_score" -- core_brain/ tests/ scripts/ dashboard/` → empty
4. `python -c "import core_brain.quotes as q; assert not hasattr(q, 'reward_score')"` → ok
5. `python -m pytest -q tests/test_trader_loop.py tests/test_shadow_run.py
   tests/test_live_quotes.py` → green (the issue's own command)
6. `git diff -U0 | grep -E "^[-+].*(rebate_est|total_with_rebate|ACTIVITY_TYPES|maker_rebates|
   expected_gap|fee_rate|reward_offset|unfunded by the allocator)"` → empty, except the
   allowed `reward_offset` context inside `core_brain/config.py`'s preset description is
   NOT touched — the diff must show no `reward_offset` line at all
