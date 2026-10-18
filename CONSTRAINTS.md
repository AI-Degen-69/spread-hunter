# CONSTRAINTS — Issue #674

Binding rules for `scoring.config.load()` canonical-name routing. Read before every task; a task that
breaks one is a wrong task, not a hard one.

## 1. Hard boundaries

| Area | Rule |
| --- | --- |
| `core_brain/config.py` | **Read only.** It already implements `_env_get`, `_bounded_float` and the pinned branch this issue copies the *order* of. Do not edit it. |
| `dashboard/server.py`, `dashboard/**` | **Do not touch.** Issue #672 owns them. |
| `_LEGACY_TO_CANONICAL`, `_PROFILE_*` tables | **Do not edit.** They are the compatibility contract for retired attribute names. |
| `tests/test_config_divergence_guard.py` | **Do not edit.** If it fails, logic leaked into the class body — move it back into `load()`. |
| `.env.example` | Already documents the canonical `BOT_*` names. No edit. |
| Dependencies | None added. `os`, `logging`, `math` and `core_brain.config._env_get` are already imported at `scoring/config.py` module top. |
| Class body of `MakerConfig` | **No new field, method, property, alias entry or signature change.** All work happens inside `load()` and in `resolve_wide_book_trial()`'s own signature. |

## 2. The precedence contract (must hold for all twelve variables)

```
canonical BOT_*  →  legacy newest  →  legacy oldest  →  shipped default
```

- Canonical always wins when both are set — no `or` chains, no "legacy first because it existed first".
- A legacy-only setup must still load and still produce the same config object it produces today.
- `_env_get(name, *legacy)` returns `(value, source_name)`. Use `source_name` for warnings and error
  text, never a hardcoded literal.
- Blank / whitespace-only values are **unset** (`_env_get` semantics). See §4.

## 3. Units — the trap this issue is most likely to fail

`BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` carries **whole cents** from the operator, exactly like
`HUNTER_MAX_SPREAD_CENTS` and `HUNTER_MAX_SPREAD`. The loader converts once:

```python
kw["max_spread_from_mid"] = float(sp) / 100.0     # "20" → 0.20
```

- **Do not remove** `/ 100.0` — that would turn a 20¢ bar into a $20 bar and the ranker would admit
  every market on earth.
- **Do not add** a second conversion, and do not special-case the canonical name as dollars.
- Wide-book trial (`0.0`–`0.30`) and tick size are already dollars. Do not convert them.
- `int(float(raw))` for the size chain stays; the `max(parsed, 120)` floor stays.

## 4. The one deliberate compatibility exception

`_env_get` skips blank/whitespace-only values; today's scoring code blocks the oldest legacy name
whenever a newer legacy name is merely *present*. Consequence: with `HUNTER_MIN_QUOTE_SHARES=""` and
`HUNTER_MIN_SIZE=200`, today nothing is applied, after this change `200` applies plus the `#632`
warning. Accepted, documented in the PR body, matching core and the existing `FILTER_*` blank tests.
Do not "fix" it back with pre-checks around the helper.

## 5. Branch conditions that must not move

- Size, spread, tick, title and URL are read **only inside** the pinned branch
  (`pinned_condition_id` non-empty). Do not lift them out to match core's second spread block.
- Scoring keeps its inherited `min_t_remaining_sec = 15.0`. Do not copy core's
  `min_t_remaining_sec = 0.0` pinned-branch assignment.
- Wide-book trial stays gated on `rehearsal.is_rehearsal()`, finite, `0.0 <= v <= 0.30`; `0.0` is
  validated then treated as "no trial"; it only ever *raises* `max_spread_from_mid`.
- Bankroll rejects non-finite and `<= 0` with a `ValueError`; both `bankroll_usd` and
  `max_committed_usd` come from that one value.
- A canonical value that is present but unparseable must fail exactly as a legacy value does today
  (`ValueError` from `float()` / `int()`), **not** silently fall back to a legacy name.

## 6. Anti-cheat — a change that does any of these is rejected

- Editing the test expectation instead of the read site.
- Making the canonical name work by *replacing* the legacy name (legacy fallback must still work).
- Reading `os.environ["BOT_..."]` directly, or hand-rolling a second precedence helper beside
  `_env_get`.
- Catching `ValueError` around the canonical read to "fall back" to a legacy value.
- Touching `_LEGACY_TO_CANONICAL` to make a test pass.
- Reporting "tests pass" as verification for the Owner. Owner-visible proof is the printed loader
  fields and the parameter panel, not pytest output.

## 7. Done means

- A test exists that fails without the change: canonical-only rows fail before, pass after; the
  legacy rows and the cent conversion pass both before and after (they are the regression guard).
- The four targeted suites are green, run by the agent.
- The `How to verify` block in the handoff is hands-on only: set a canonical name in `.env`, print
  the loaded fields, then set a conflicting legacy name and show the canonical still wins.
