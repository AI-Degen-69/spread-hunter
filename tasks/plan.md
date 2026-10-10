# Plan — Issue #461: Horizon gate falsely rejects open election markets past venue end date

Branch: i461/fix-horizon-gate-falsely-rejects-open-election-mar | Issue: #461
Tier: Small (one function + its single call site; tests in one existing file; no new deps, no schema/API change) | Type: Code + Debug
Stack: Python, pytest, `requests`-based screener (`scripts/filter_markets.py`); focused test file `tests/test_unified_universe.py`
Labels: bug, ready-for-agent, needs-answers (no `quick-fix` label → Step 0C: no gate, straight to planning)

## CodeRabbit plan intake (read once, not twice; echo ignored)

- Adopted: optional `state` kwarg on `expired_at_intake` (default `None`, `resolve_state` shape); `evaluate` builds state via `resolve_state` at the EXPIRY GATE and passes it in; refusal reason `{venue verdict}; horizon passed (expired {when} ago)`; sports exemption stays first; `state=None` keeps the exact old reason.
- Rejected: the 6-way split (Tasks 1.1–2.3) is bloat — merged into 4 atomic tasks below (T1 read-only venue check; T2 tests RED; T3 fix GREEN; T4 focused verification). No new files, no new abstractions.
- [UNVERIFIED] at intake: the exact venue `endDate`/`closed`/`acceptingOrders` of the 3 reported markets (Brazil ×2, LA mayoral) — resolved by T1 read-only check, not assumed. Fix shape is identical under both ticket hypotheses (venue-open → admit), so T1 is a go/no-go confirmation, not a design input.

## Open question (needs-answers) — resolved from code, no operator question asked

Q: Is `endDate` the first-round date (runoff-aware exemption) or a bad date (bad-date handling)?
A: Both hypotheses lead to the same fix: `tradable`'s horizon arm already admits any venue-open market with a date present (`scripts/filter_markets.py:726-732`), so consulting venue state in `expired_at_intake` covers both. Recorded here so Stations III–V never re-ask it.

## Domain skills

Loaded (verified on disk): `test-driven-development` (RED→GREEN on T2→T3), `debugging-and-error-recovery` (Prove-It: failing admission test before the fix). Tagged: `incremental-implementation`, `doubt-driven-development`.

## Spec (embedded — Small tier, no SPEC.md ceremony)

Goal: a market the venue reports open AND accepting orders is never refused as expired on `endDate` alone; it falls through to the distance/horizon arms. Closed / not-accepting / UMA-resolved / unreadable past-`endDate` markets are still refused before any network fetch, with a reason naming the venue signal + the clock signal.
Out of scope: `≤ 30.0d` horizon-distance arm, pre-start/in-play gates, volume/movement/depth gates, quoting, `family_probe.py:247` sibling check, `filter_loop.py`.
Acceptance: the 5 boxes in the issue (open-equivalent admitted; closed still refused + auditable; sports unchanged; new regression tests; `pytest -q tests/test_unified_universe.py` green).

## Interface contract (locked before logic)

```python
def expired_at_intake(end_iso, start_iso=None, category=None, now_iso=None, state=None) -> tuple[bool, str]
# state: None | resolve_state() triple (resolved, reason, end_iso) — same shape tradable already accepts
```

| `state` | past-`endDate`, non-sports | result |
|---|---|---|
| `None` (legacy callers) | any | `(True, "horizon passed (expired {when} ago)")` — byte-identical to today |
| open (`(False, ...)`) | past | `(False, "")` → reaches `tradable` horizon arm, which already admits it |
| closed / not-accepting / UMA-resolved / unreadable | past | `(True, "{venue verdict}; horizon passed (expired {when} ago)")` |
| any | future / unknown / malformed | unchanged (`(False, "")`), sports exemption unchanged and first |

`_cause` unchanged: the new reason contains both `resolved` and `horizon`, so it stays in the `horizon` bucket (`filter_markets.py:1933-1934`) and the dashboard HORIZON GATE card needs no change. `evaluate` call site (EXPIRY GATE): build state with the same args as the later `tradable` call (`closed`, `accepting_orders`, `end_date_iso`, both UMA fields); optionally reuse it downstream only if step order is untouched.

## Improvement proposal (adopted by default — simplification/edge-case hardening)

The refusal reason must keep both keywords (`resolved…; horizon passed…`) so `_cause()` keeps bucketing it as `horizon` — otherwise the dashboard HORIZON GATE card silently loses these refusals. Evidence, verbatim: `if "resolved" in r or "horizon" in r: return "horizon"` (`scripts/filter_markets.py:1933-1934`). Adopted into T3.

## Dependency graph

T1 (venue check, read-only) → T2 (tests RED) → T3 (fix GREEN) → T4 (focused verification). T2 before T3 is the TDD loop: T2's admission test must FAIL on current code (Prove-It), T3 makes it pass.

## Tasks (atomic vertical slices)
### T1 [x] [S] [Debug] Confirm venue fields for the 3 reported markets (read-only)
Files: none (no code). Read `endDate`, `closed`, `acceptingOrders` once per market (2× Brazil presidential, 1× LA mayoral) via Gamma/order-book API.
Expect `closed=false`, `acceptingOrders=true`, `endDate` on the first-round date. If any market is closed/not-accepting → STOP, report; the refusal is correct and the ticket needs a different fix. Record values at the bottom of this file.
Verification: values recorded in `tasks/plan.md`; zero code touched; spends nothing.
Depends on: —

### T2 [x] [M] [Backend/Logic] Regression tests first (RED)
File: `tests/test_unified_universe.py` (+ `_universe_candidate` helper stays).
- Update `test_evaluate_refuses_expired_market_before_fetching_tape_or_books` (:1134): set `closed=True`; keep all existing asserts; add `assert "market closed on the venue" in reason`. Leave `test_expired_at_intake_refuses_past_end_date_for_non_sports` (:1081) byte-identical (None-state backward compat).
- Add election helper (Politics category, past `end_date_iso`, `closed=False`, `accepting_orders=True`).
- Admission: `expired_at_intake` open-state + past date → `(False, "")`; `tradable` negative-days + open state admitted; end-to-end `evaluate` (fake-session pattern from :1065) eligible / no `horizon passed` / book fetch reached.
- Refusal: `closed=True` → contains `market closed on the venue` + `horizon passed`; `accepting_orders=False` → `venue stopped accepting orders`; `closed=None` → `resolution state unreadable`; each with `_cause(reason) == "horizon"`.
Verification: run focused file; new admission tests MUST fail on current code (RED proof), refusal/existing tests pass. Skill: `test-driven-development`.
Depends on: T1

### T3 [x] [S] [Backend/Logic] Venue-state-gated expiry (GREEN)
File: `scripts/filter_markets.py` only.
- `expired_at_intake`: add `state=None` kwarg; check order unchanged (unknown/not-past → not expired; sports/start exemption → not expired) then new venue check via `_unpack_state` (same as `tradable`); `None` → exact old reason; open → `(False, "")`; else `{verdict}; horizon passed (expired {when} ago)`; docstring: `endDate` alone is not end-of-trading, runoff elections beside sports kickoffs.
- `evaluate` EXPIRY GATE (:1544-1552): `resolve_state(closed, accepting_orders, end_date_iso, + both UMA fields)` → pass as `state`; refusal stays before UMA gate and all fetches. Do NOT touch `resolve_state`, `days_to_resolve`, `tradable`, `_cause`, `now_iso`.
Verification: T2's RED tests turn green with no other test changed. Skill: `incremental-implementation`.
Depends on: T2

### T4 [XS] [Backend/Logic] Focused verification + sports unchanged
Files: none. Run `python -m pytest -q tests/test_unified_universe.py tests/test_in_play_gate.py tests/test_pre_start_gate.py` (agent-run, background-only — never an operator verify step).
Verification: all green; `git status` shows only the 2 intended files (+ plan artifacts) modified.
Depends on: T3

## Checkpoints

- After T1: one-line go/no-go (venue-open confirmed → proceed; else stop + report).
- After T3: one-line progress (admission test green, refusal tests green).

## Venue confirmation log (T1 writes here)

GO — first-round-date hypothesis verified 2026-10-10 via Gamma `public-search` (read-only):
- Brazil: `will-luiz-incio-lula-da-silva-win-the-2026-brazilian-presidential-election` — `closed=false`, `acceptingOrders=true`, `endDate=2026-10-05T03:59:00Z` (~5.4d past). Open + past endDate = the bug shape. (Defeated siblings e.g. Tarcisio/Bolsonaro/Haddad markets are `closed=true` → correctly refused.)
- LA: `will-karen-bass-win-the-2026-los-angeles-mayoral-election` — `closed=false`, `acceptingOrders=true`, `endDate=2026-06-03T03:59:00Z` (~129.4d past, vol $1.47M). Open + past endDate = the bug shape. (First-round-winner / advance-to-2nd-round markets are `closed=true` → correctly refused.)
- RED proof: `test_evaluate_admits_open_election_market_past_end_date` fails pre-fix (`eligible False`); `state`-kwarg unit tests fail pre-fix (unexpected kwarg).

## Rejected scope (do not resurface)

- Touching `family_probe.py:247` sibling check — out of scope per issue; separate clock-only context.
- Second CodeRabbit review loop after fixes — single focused round only (repo git-workflow).
