# Noticed but not touching — #684

Scope guard for #684. Every row is a defect or drift this station noticed and
deliberately left unchanged. Status stays `open` and Resolved-by stays empty
until a follow-up claims the row. Owner is `you` (the operator) when no existing
issue covers it, otherwise the issue number. Dates are when the row was written.

## Discovered while planning (owner: planner, 2026-10-19)

| Noticed | Why out of scope | Owner | When | Status | Resolved-by |
|---|---|---|---|---|---|
| `configs/tournaments/i622_tournament-1_arms.json` still carries `HUNTER_REFUSAL_HOLD_DRIFT` (arm `hold-drift`), so the file can no longer be planned once the #684 guard lands | The file is the historical record of the #622 experiment; rewriting it would falsify what was run, and PR #691 already settled which keys those files are allowed to keep. The planner refuses it on purpose instead of silently running the shipped default | you | 2026-10-19 | resolved | by design (#684) |
| `scripts/tournament_cockpit_server.py:1573` and `:1653` still present `HUNTER_REWARD_OFFSET` in their knob copy and example command | Dashboard copy is outside the plan-time validator and belongs to the cockpit surface, not the tournament planner. The name is already recorded in `docs/issues/492-noticed-but-not-touching.md` | 492 | 2026-10-19 | duplicate | `docs/issues/492-noticed-but-not-touching.md` |
| `core_brain/config.py:760-781` recommends legacy names (`HUNTER_DROP_CENTS`, `HUNTER_DRAWDOWN_PCT`, `HUNTER_AGED_OUT_RESCUE_LEAD_SEC`) as the fix for the retired stop keys, while the new planner guidance names the canonical `BOT_*` equivalents | Both spellings load, so nothing is broken; changing the runtime text means touching `core_brain/config.py`, which this issue keeps out of scope for merge-conflict reasons. Aligning the two vocabularies is a cleanup for whoever owns the config surface | you | 2026-10-19 | resolved | #694 |
| The retired-key inventory now exists in two places: `RETIRED_ENV_OVERRIDES` in `scripts/shadow_tournament.py` and the warning tables in `core_brain/config.py:760-781` | Merging them needs a shared module in `core_brain`, which the issue explicitly keeps out of scope. A test pins `RETIRED_ENV_OVERRIDES` equal to the independent `I492_RETIRED_KNOBS` set, so drift fails CI instead of hiding | you | 2026-10-19 | resolved | #695 |
