# Noticed-but-not-touching — Issue #652 (Station II planning)

Two candidates were found while planning. Neither is fixed here; both are recorded with evidence so they
are not rediscovered, and neither was dispositioned by the operator yet.

| ID | Candidate | Discovering station | Evidence | Status | Owner / timeline |
|----|-----------|---------------------|----------|--------|------------------|
| N1 | `dataclasses.replace()` silently ignores a legacy keyword when the target canonical field already holds a non-default value. This change makes the limitation reachable for eight more names: before it, `replace(cfg, select_max_queue_minutes=15.0)` would restore the default; after it, the legacy spelling is dropped when the instance's value differs from the class default, and only the canonical spelling works. Pre-existing shared behaviour of the constructor wrapper for every one of the ~110 alias-table entries — **not introduced here**. The three live call sites are safe because each starts from a default instance (`tests/test_queue_clear_gate.py:664`, `tests/test_maker_queue_bar.py:299-301`), and the closure of `replace()` call sites over scoring configs was checked. | II | Probe on `01157a3`: `replace(Picker(select_min_movement_usd=777.0), select_min_movement_usd=200.0).filter_live_vel_min_usd == 777.0`, while `filter_live_vel_min_usd=200.0` lands. Rule at `core_brain/config.py:209-218`; documented as Assumption 5 of the CodeRabbit plan and explicitly out of scope in the issue. | open | unassigned — needs its own ticket if the operator wants legacy-spelled `replace()` to keep winning |
| N2 | `filter_common_spread_max_cents` follows the repo's existing `_cents` convention, where the value is a price **fraction** (`bot_quote_price_band_min_cents = 0.10`, `bot_quote_spread_static_offset_cents = 0.020`), while `docs/ideas/variable-names-refactor.md:28-29` reads as literal whole cents. The requested name is kept as-is because a unit change is a value change. | II | `core_brain/config.py:425-430` (the derived property), `:284`, `:294` (the two existing `_cents`-as-fraction fields); `docs/ideas/variable-names-refactor.md:28-29` | open | unassigned — surface in the PR body for the operator's suffix call; no code change either way in #652 |

Deliberately **not** candidates (checked and cleared): the station brief's `select_velocity_gate` name,
which does not exist in the repo (the field is `select_velocity_gate_enabled`) — a brief typo, not a code
defect; and the stale `docs/ideas/variable-names-refactor.md` vocabulary, which the issue already rules out.
