# Noticed but not touching — #624

Scope guard for #624. Every row: noticed code worth a future issue, left
unchanged by this plan. Status stays `open`, Resolved-by stays empty until a
follow-up issue claims the row.

| Noticed (owner: you, 2026-10-16) | Why out of scope | Status | Resolved-by |
|---|---|---|---|
| Refactor `HUNTER_*` env-stripping comprehension across `shadow_tournament.py` (filter, dash, shadow) and `tournament_dashboard_hub.py` into a shared helper | Refactoring working call-sites into a shared helper expands scope beyond worker isolation; follow-up agent instructions forbid changing dashboard/filter code | open | |
