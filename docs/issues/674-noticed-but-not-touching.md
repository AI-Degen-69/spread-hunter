# 674 — noticed but not touching (Station II, 2026-10-18)

Everything below was found while mapping the nine scoring read sites. None of it is an environment-name
bug, so none of it belongs in #674: touching any of these changes legacy-only output or widens the
ticket past its own acceptance criteria.

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | `core_brain/config.py:594` sets `kw["min_t_remaining_sec"] = 0.0` inside the pinned-market branch; `scoring/config.py` keeps the inherited `15.0`. A pinned long-dated market is therefore quotable by the engine and gated by the ranker on a 15-second floor. | #674 changes *how a value is found*, not branch semantics. Adding the rule alters legacy-only output for every pinned operator, which AC-3 forbids. | you | after #674 merges | open |
| N2 | `core_brain/config.py:627-634` applies the spread bar to **every** market through a second read; `scoring/config.py` applies it only inside the pinned branch. | CodeRabbit's Design Choice 3 option 2. Lifting the read changes behaviour for an unpinned ranker — a scope and compatibility change, not a rename. | you | separate issue | open |
| N3 | `BOT_QUOTE_TOURNAMENT_PRESET` (`core_brain/config.py:577`) reaches the engine only; `scoring` has no preset knob, so a preset-run ranker scores with default bars. | The ticket lists nine settings and forbids adding core-only settings. A preset for the screener is a new feature with its own defaults to pick. | you | separate issue | open |
| N4 | Scoring's `resolve_wide_book_trial` has no `for_display` argument, unlike `core_brain/config.py:981`. Outside a rehearsal, a non-blank trial raises from `scoring.config.load()`: `scripts/filter_markets.py` dies at import, while the two guarded callers mask it — `dashboard/server.py:4284-4287` catches and silently substitutes the engine config (parameters panel shows engine numbers, not scoring's), `filter_loop.py:201-210` logs a WARN and drops the trial flag. | #674 keeps the guard intact and threads only the variable *name*. A display escape is new behaviour and belongs with the dashboard work (#672). | you / #672 | with or after #672 | open |
| N5 | The three `#632` warnings still say "set `HUNTER_MIN_QUOTE_SHARES` instead" etc. — pointing an operator at another deprecated alias rather than at the canonical `BOT_*` name this issue makes authoritative. | AC-3 requires the warning text to stay byte-identical, and older logs are searched by that text. | you | after #674 merges | open |
