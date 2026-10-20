# 706 - noticed but not touching (Station II, 2026-10-20)

Found while planning #706 (ledger item O2 of #690). None of it is on the path this ticket
changes, so none of it belongs here; each row names who owns it and how it should be handled
later.

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | `core_brain/config.py` `MakerConfig.from_env` reads `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` twice: once in the pinned-market block (strict) and again in the general block (tolerant), the second read landing on the same value the first one already stored. It is invisible today because both paths compute the same number from the same name, and #707's per-path matrix pins the difference in parsing rules that keeps them apart. | Collapsing them is a restructure of the money-path loader, not an alias deletion: it moves the strict/tolerant split into a branch and would have to re-justify the #707 behaviour on the way. #632 ledger item 3 records the duplicate parse as its own idea. | you | with the next config-loader simplification | open |
| N2 | Every other execution knob still carries its own legacy alias chain (`BOT_QUOTE_SIZE_MIN_SHARES`/`HUNTER_MIN_QUOTE_SHARES`/`HUNTER_MIN_SIZE`, `BOT_QUOTE_GRID_TICK_SIZE_USD`/`HUNTER_PRICE_TICK`/`HUNTER_TICK`, `BOT_QUOTE_WIDE_BOOK_TRIAL`/`HUNTER_WIDE_BOOK_TRIAL`, ...), each with a `#632` deprecation warning. The spread setting is now the only one with a single accepted name. | The operator directive for #706 is scoped to the spread names; retiring the rest is one ticket per name family on the money path and would swamp this diff. `tests/test_wide_book_trial.py` still drives the trial through the legacy trial name for the same reason. | you | one ticket per name family, after #706 | open |
