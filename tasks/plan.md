# Implementation Plan: Apply the spread bar where the filter happens (#674 follow-up N2)
Branch: `i690/apply-spread-bar-at-market-filter` | Issue: `#690`

**Size: Small. Task type: Code (Backend/Logic) + Debug (regression coverage) + Docs (comments, ledger).**
Rationale: one loader function changes (`scoring/config.py:load()`); everything else is regression
coverage and comment/ledger truth-fixing. No public API, schema, or dependency change.

---

## 1. What the issue asks vs. what the code does

The issue title reads *"reject markets over 2 cents and show reason"*. Both halves of that sentence
**already ship**, by a different setting than the one the issue names:

| Measure | Setting | Value | Where |
|---|---|---|---|
| Bid–ask width of a book (the rejection bar) | `filter_common_spread_max_cents` (property of `max_book_spread * select_spread_pct`) | `0.0205` (2.05¢) | `core_brain/config.py:512`, read by `scripts/filter_markets.py:99` as `MAX_BOOK_SPREAD` |
| Distance from mid to our resting quote (quoting parameter) | `max_spread_from_mid` | `0.045` picker / `0.15` engine | `core_brain/quotes.py:806,842` — **no ranker code reads it** |

Verified by `rg -n "max_spread_from_mid" scoring/ core_brain/ scripts/ dashboard/`: the only
non-test readers are `core_brain/quotes.py` (quoting window), `dashboard/server.py:4851-4865`
(parameter card, engine scope), and the wide-book-trial `max(window, wide)` line in each loader.
`scripts/filter_markets.py` never mentions it. A 50/53 book is already rejected with
`YES: spread 0.0300 > 0.0205` (`scoring/selector.py:239` formats the number, `pair_books_allowed`
prefixes the side), `_cause()` already buckets it as `YES spread` (`scripts/filter_markets.py:1783`),
and `categorizeGate()` already files it in the dashboard **spread** column
(`dashboard/static/app.js:9514`).

**So the real defect is narrower and the plan must not overstate it.** `scoring/config.py:172-178`
reads the spread override only inside `if cid:`. An unpinned ranker run therefore ignores
`BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` / `HUNTER_MAX_SPREAD_CENTS` / `HUNTER_MAX_SPREAD` entirely and
keeps the picker default `0.045`, while `core_brain/config.py:628-642` applies it on every run
through a second, tolerant read. That is a **settings-parity** bug, not an admission bug. Lifting the
read changes **no** market's eligibility, score, ordering, or rejection reason.

## 2. Acceptance criteria

- **AC-1** `scoring.config.load()` sets `max_spread_from_mid` from the env on **every** run, with the
  three-name precedence `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` → `HUNTER_MAX_SPREAD_CENTS` →
  `HUNTER_MAX_SPREAD` already implemented by `_env_get`.
- **AC-2** Pinned runs (`cid` present) keep today's behaviour byte-for-byte: `float(val) / 100.0`,
  raising on malformed text. Existing pinned tests pass unmodified.
- **AC-3** Unpinned runs mirror `core_brain/config.py:636-642`: `float()` failure → `0.0`; only
  `cents > 0` is applied, divided by 100. No crash on garbage, no negative or zero window.
- **AC-4** The `HUNTER_MAX_SPREAD is deprecated (#632)` warning still fires exactly once per `load()`,
  with byte-identical text, and now also on unpinned runs.
- **AC-5** The read sits **before** the wide-book trial block, so `max(window, wide)` sees the loaded
  value on unpinned runs (today it sees the `0.045` class default).
- **AC-6** Wide-book rejection continues to use `filter_common_spread_max_cents` = `0.0205`.
  `max_spread_from_mid` is **never** passed to `pair_books_allowed()` / `book_allowed()`.
- **AC-7** A 50/53 book with **no** `max_spread` override is rejected with
  `reject_reason == "YES: spread 0.0300 > 0.0205"` and `_cause(...) == "YES spread"` — and is still
  rejected identically when an unpinned `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID=50` is set (the two
  settings stay separate).
- **AC-8** The full reason string survives to `/api/kpi` at `funnel.filters[].examples[].reason`
  (note the `funnel` nesting — the operator brief's flat `filters[]` path was wrong).
- **AC-9** `docs/issues/674-noticed-but-not-touching.md` N2 records the read as moved and the bid–ask
  gate as still owning wide-book rejection.

## 3. Out of scope (explicitly)

- No change to `0.0205`, `max_book_spread`, `select_spread_pct`, or the trial bound `0.30`.
- No change to `core_brain/quotes.py`, `core_brain/trader_loop.py`, `core_brain/order_manager.py`.
- No new endpoint, no new dashboard panel, no `dashboard/static/app.js` edit.
- No subscription/realtime/venue work; no `.env` value edits.

---

## 4. CodeRabbit plan intake (costed once — Stations III–V need not re-read it)

The issue carries a CodeRabbit coding plan (`gh issue view 690 --comments`).

- **Adopted:** the diagnosis (option 1 of Design Choice 1: lift the read, keep the two measures
  separate), the pinned-strict / unpinned-tolerant split (Design Choice 2 option 3), the reuse of the
  existing reason path (Design Choice 3 option 2), and the test matrix (Phase 1 Task 2, Phase 2
  Tasks 1–2).
- **Rejected / restructured:** its 6 tasks were merged into 4 (Phase 2 Task 3's comment work folded
  into Task 3 with the ranker tests; its sub-issue ceremony dropped — Small work). Its claim that the
  read must move "to fix market admission" is corrected in §1: it is parity only.
- **`[UNVERIFIED]` at intake, now resolved:** it cited `tests/test_dashboard_server.py:528-529` for a
  funnel test — the assertions there use `data["funnel"]["filters"]`, so the nested key is what the
  new test must assert; and `scripts/filter_markets.py:766-768`'s "reward config feeds the score-window
  width" — confirmed stale, `rg clobRewards scripts/filter_markets.py` returns only that comment line
  itself.
- **One CodeRabbit number corrected:** it wrote `tests/test_config_single_source.py` covers "canonical
  precedence"; the existing `EXECUTION_CANONICAL_ENV` row for this field is `needs_pin=True`, so the
  unpinned matrix is genuinely new, not extended.

---

## 5. Task decomposition

### [x] Task 1 — Lift the read out of the pinned branch  `[Backend/Logic]` size **S**
**Files:** `scoring/config.py` (`load()`), only.
**Depends on:** —
- Hoist `sp, sp_name = _env_get("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "HUNTER_MAX_SPREAD_CENTS",
  "HUNTER_MAX_SPREAD")` out of the `if cid:` block, to just **after** the block closes and **before**
  `wide_raw, wide_name = _env_get("BOT_QUOTE_WIDE_BOOK_TRIAL", ...)` (currently line 186).
- Keep `if sp_name == "HUNTER_MAX_SPREAD" and sp and sp.strip(): logging.warning(...)` exactly as it
  is, now reached on both paths and still once per load.
- Branch the parse on `cid`:
  - pinned → `kw["max_spread_from_mid"] = float(sp) / 100.0` (unchanged, still raises).
  - unpinned → `try: cents = float(sp)` / `except ValueError: cents = 0.0` / `if cents > 0:
    kw["max_spread_from_mid"] = cents / 100.0` — a line-for-line mirror of
    `core_brain/config.py:636-642`, including its incidental behaviour that `inf` passes (parity is
    the point; do not "improve" it here).
- Delete the now-duplicated read from inside the pinned block. Do not touch `min_quote_shares`,
  `quote_shares`, or `price_tick` — they stay pinned-only.
- Add a short comment naming the divergence on purpose: scoring's field is the picker's rebate-style
  window (`0.045`) while core's is the resting-offset ceiling (`0.15`); the *loader rule* is what is
  being matched, not the default.
**Verify:** `python -m pytest -q tests/test_config_single_source.py tests/test_wide_book_trial.py`
(existing tests must stay green before Task 2 adds new ones).

### [x] Task 2 — Loader-parity and trial matrix tests  `[Backend/Logic]` size **S**
**Files:** `tests/test_config_single_source.py`, `tests/test_wide_book_trial.py`.
**Depends on:** Task 1.
- `tests/test_config_single_source.py` — new test(s) next to `test_execution_coupling_overrides`,
  reusing `_clear_execution_env(monkeypatch)` and **no pin**:
  - canonical `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID=25` → `0.25`.
  - `HUNTER_MAX_SPREAD_CENTS=20` → `0.20`.
  - `HUNTER_MAX_SPREAD=20` alone → `0.20` **and** one warning with the exact text
    `HUNTER_MAX_SPREAD is deprecated (#632); set BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID instead.`
  - canonical beats both legacy names when all three are set.
  - blank canonical falls through to the legacy value; nothing set → `0.045` (picker default).
  - `"wide"` (malformed) → `0.045`, no raise.
  - `"0"` and `"-5"` → `0.045`, no raise (a zero/negative window is inactive, not enforced-at-zero).
  - **parity:** under one unpinned env (`BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID=22`),
    `scoring_config.load().max_spread_from_mid == core_config.load().max_spread_from_mid == 0.22`.
- `tests/test_wide_book_trial.py` — unpinned override + active trial:
  `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID="22"`, `HUNTER_WIDE_BOOK_TRIAL="0.12"`, rehearsal declared
  → `max_spread_from_mid == 0.22` (the window wins over the narrower trial); and with `"5"` →
  `0.12` (the trial wins). **Test-hygiene fix (adopted improvement, §6):** `_load_with` at line 117
  clears only `HUNTER_MARKET`, `HUNTER_MAX_SPREAD`, `HUNTER_WIDE_BOOK_TRIAL` — add
  `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` and `HUNTER_MAX_SPREAD_CENTS`, or the new unpinned case reads
  whatever the developer's shell exports.
**Verify:** `python -m pytest -q tests/test_config_single_source.py tests/test_wide_book_trial.py`

**Checkpoint A** (after 1–2): parity proven; no ranker behaviour to show yet.

### [x] Task 3 — Ranker rejection proof, reason path, and stale comments  `[Debug]` size **S**
**Files:** `tests/test_redesigned_filter_pipeline.py`, `tests/test_dashboard_server.py` (or a
`core_brain/kpi.py` test), `scripts/filter_markets.py` (comments only).
**Depends on:** Task 1 (for the unpinned guard case).
- Ranker test, modelled on the existing `test_spread_edge_rejects_spread_exceeding_ceiling`
  (line 244) and the FakeSession book style in `tests/test_rewards_purge.py:104-121`. Build a
  candidate with YES bid `0.50` / ask `0.53` that clears volume, movement, price band and identity so
  the book gate is the first failure. Call `evaluate(...)` with **no** `max_spread` argument (so the
  shipped `MAX_BOOK_SPREAD` = `0.0205` is used). Assert `eligible is False`,
  `reject_reason == "YES: spread 0.0300 > 0.0205"`, `_cause(reject_reason) == "YES spread"`.
- Guard case in the same file: with **no** pin, set `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID=50`; the same
  book must be rejected with the identical reason. This is the test that fails if someone later wires
  `max_spread_from_mid` into `book_allowed`.
- Reason-to-`/api/kpi` test: feed a pipeline snapshot whose spread rejection example carries
  `YES: spread 0.0300 > 0.0205`; assert it appears verbatim at
  `data["funnel"]["filters"][i]["examples"][j]["reason"]` (or assert directly on
  `_funnel_from_pipeline()` in `core_brain/kpi.py:744-750`). Do not add an endpoint, do not touch
  `app.js`.
- `scripts/filter_markets.py` comments only:
  - line 99 (`MAX_BOOK_SPREAD = _CFG.filter_common_spread_max_cents`): add one line saying this
    bid–ask ceiling is derived from `max_book_spread * select_spread_pct`, **not** from
    `max_spread_from_mid` (distance from mid, a quoting window the ranker never reads).
  - lines 766-768: drop `(their reward config still feeds the score-window width, nothing else)` —
    nothing reads reward config any more (`rg clobRewards` matches this comment only).
  - No logic change: `rg -n "MAX_BOOK_SPREAD" scripts/filter_markets.py` must show the same six
    call sites after the edit.
**Verify:** `python -m pytest -q tests/test_redesigned_filter_pipeline.py tests/test_dashboard_server.py tests/test_market_selection_bars.py`

**Checkpoint B** (after 3): the 50/53 rejection, its reason text, and its dashboard path are each
pinned by a test; comments no longer conflate the two settings.

### [x] Task 4 — Ledger and guards  `[Docs]` size **XS**
**Files:** `docs/issues/674-noticed-but-not-touching.md`, `docs/issues/690-noticed-but-not-touching.md`.
**Depends on:** Tasks 1–3 (wording must match shipped behaviour).
- N2 row → status `fixed #690`, with the note that the read moved out of the pinned branch, that
  unpinned runs now use the engine's tolerant rule, and that `filter_common_spread_max_cents` /
  `0.0205` still owns wide-book rejection.
- Land the new out-of-scope ledger from §7.
**Verify:** read the two files; no code touched.

---

## 6. Improvement proposal (evidence-based, one only)

**Evidence (verbatim, `tests/test_wide_book_trial.py:119`):**
`for name in ("HUNTER_MARKET", "HUNTER_MAX_SPREAD", "HUNTER_WIDE_BOOK_TRIAL"):`

This helper is the one every `load()` case in that file runs through, and it clears none of
`BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` / `HUNTER_MAX_SPREAD_CENTS`. Today that is harmless because
`scoring.load()` ignores those names without a pin; after Task 1 those names *become* the value, so a
new unpinned case would pass or fail depending on the developer's shell. **Class: edge-case hardening →
adopted by default**, folded into Task 2. No other proposal is offered.

---

## 7. Dependency graph and checkpoints

```
Task 1 ──┬──> Task 2 ──┐
         └──> Task 3 ──┴──> Task 4
```
- Task 2 needs Task 1's shipped parse rule (it asserts the values).
- Task 3's guard case needs Task 1 (without it, the unpinned env var is inert and the guard passes
  for the wrong reason — the test must fail before Task 1 to be honest, so it is authored GREEN-after,
  RED-checked by reverting Task 1 locally once).
- Task 4 needs the final wording of 1–3.
- Checkpoint A after Task 2, Checkpoint B after Task 3. In `/iii-build-plan auto` each is a one-line
  progress note, not a pause.

## 8. How to verify (operator, hands-on — zero spend, no venue contact)

**1. Prove the loader parity directly.** In PowerShell:

```powershell
$env:BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID=22
python -c "from scoring.config import load as s; from core_brain.config import load as c; print('picker', s().max_spread_from_mid); print('engine', c().max_spread_from_mid)"
Remove-Item Env:BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID
```

Both lines must print `0.22`. Before this change the picker printed `0.045` — that number is the whole
ticket. Failed looks like: `picker 0.045`.

**2. See the 2¢ bar and its reason on a real universe, publishing nothing.**

```powershell
python -m scripts.filter_markets --top 20 --dry-run --out-dir runtime/previews/spread-follow-up
```

Open [runtime/previews/spread-follow-up/market_universe.json](runtime/previews/spread-follow-up/market_universe.json)
and look at rows whose `reject_reason` starts `YES: spread` / `NO: spread`. Every such row must carry a
number **above** `0.0205`, e.g. `"YES: spread 0.0300 > 0.0205"`. Failed looks like: a spread rejection
whose measured value is `<= 0.0205`, or a row with a reason and no number.

**3. Confirm the dry run wrote nothing the Trader reads.** `runtime/markets.json` must have the same
last-modified stamp and contents as before step 2 — the ranker's dry run leaves the Trader's list
alone. Failed looks like: a fresh mtime on `runtime/markets.json`.

Do **not** press START on the dashboard and do not set `BRIDGE_CONTROL` for any of this; nothing here
places an order.

## 9. Explicit assumptions (flag if wrong before build)

- A-1: the operator's `.env` may or may not set `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID`. Either way the
  change is correct; it only makes an unpinned ranker honour it. Step 1 above overrides the shell
  environment, so it proves the rule regardless of `.env` (note `scripts/filter_markets.py` loads
  `.env` with `override=False`, a bare `python -c` does not).
- A-2: "the ranker rejects markets with a spread wider than 2 cents" is already true; this issue makes
  the *loader* honest. If the operator instead wants admission to use `max_spread_from_mid`, that is a
  different ticket and requires approval — see §1 and AC-6.
