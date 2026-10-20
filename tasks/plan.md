Branch: i709/dashboard-scoring-config-fallback | Issue: #709

# Plan — #709 stop substituting engine config for screener params

Size: Standard | Type: Code/UX | Shape A (server-side, no app.js change)

## Environment intake
- Route: `dashboard/server.py` `/api/parameters` (def at ~4235). Fallback at ~4283-4287.
- `cfg` and `scfg` are both `scoring.config.MakerConfig`; `cfg = load_cfg(for_display=True)` at ~4246 (engine, survives `BOT_QUOTE_WIDE_BOOK_TRIAL`), `scfg = load_scoring()` raises ValueError in a non-rehearsal process when that var is set (scoring/config.py `resolve_wide_book_trial`, rehearsal gate).
- 13 `scope: "market_screener"` cards; 19 `scfg.` reads incl. `getattr(scfg, ..., <literal>)` picker-bar reads; 46 engine cards use `cfg` — untouched.
- `tests/test_dashboard_server.py` guards to respect: badge test asserts `len(params) == 59` + every `env_var` is a str, non-empty ones documented in `.env.example` → synthetic card only on the FAILURE path, `env_var: ""`, `raw_value: None`. Class guard `test_no_parameter_card_value_restates_the_default_in_another_unit` regexes card values — marker text must not end in `(…¢)` or `(…m)/(…h)`.
- tests/conftest.py scrubs all `BOT_*`/`HUNTER_*`/`FILTER_*` env per test → monkeypatch.setenv is the deterministic trigger; no `.env` in this worktree.
- pytest process is never a rehearsal (`rehearsal._REHEARSAL = False`).

## Tasks
- T1 [Backend/Logic] dashboard/server.py: on `except (TypeError, ValueError)` — log WARNING with the reason (module logger, line 42), build `_NoScoringConfig(reason)` sentinel (attrs return a `str` subclass whose `__format__` always renders `⚠ scoring config unavailable — see log`, so all 19 `scfg.` reads incl. format specs and `getattr(..., default)` reads render the marker instead of engine numbers), insert ONE synthetic card (`key: scoring_config_load_failed`, scope market_screener, subgroup active_live, value = `⚠ <Type>: <reason>`, env_var "", raw_value None, default "") at params[0]. Depends on: none. Verify: focused pytest.
- T2 [Backend/Logic] tests/test_dashboard_server.py: two tests — failure test (trial var set → 200, synthetic card present with reason text visible, zero screener-scope card values contain `$0.15`, WARNING record present) and healthy test (trial var unset → no synthetic card, exactly 13 market_screener cards, max_spread card untouched). Depends on: T1.
- T3 [Docs] docs/issues/709-noticed-but-not-touching.md: two open rows — (1) THE500: unguarded `load_cfg(for_display=True)` + no route error handler ⇒ illegal `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` (post-#707) 500s the whole panel incl. engine scope; (2) `docs/agents/git-workflow.md` still prescribes the `@coderabbitai summary` PR-body placeholder dropped from the last merged PRs. Status open, owner + when, Resolved-by empty. Depends on: T1.
- T4 [Process] per-issue files CONSTRAINTS.md / tasks/plan.md (this) / tasks/todo.md replaced wholesale for #709 (were #705's); merge discipline: on main-moves keep OWN side of per-issue files. Depends on: none.
