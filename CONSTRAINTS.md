# CONSTRAINTS - #569 (locked by Station II, enforced through Station V)

Governs the `i569/loose-baseline-reference-run` branch only.

## Scope & File Boundaries
- No production code changes. Writable: one arms JSON, `docs/runs/` note, runtime artifacts (plan/results/DB/log). Everything else read-only.
- Setting values are fixed: preset via `HUNTER_TOURNAMENT_PRESET=baseline` env only. No config edits, no trial knobs.

## Safety (money path)
- Shadow only: no signer, no live execution, no real money. Never touch `data/orders.db` (read it for nothing, write nothing to it).
- Fresh DB per run under `data/`; never reuse or overwrite an existing store.

## Quality bar
- `python -m pytest -q tests/test_shadow_tournament.py` green at closeout.
- No new dependencies. No linter/test suppression. Report numbers must match the results JSON exactly — no hand-editing figures.
