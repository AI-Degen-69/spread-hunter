# SPEC.md — Issue #837: Cockpit "Worst Catalyst & Hypothesized Tweak" column

Branch: `i837/populate-worst-catalyst-tweak-columns` | Tier: **Standard** | Types: **Code (Backend/Logic + Design/UI) + UX/Copy**

## Goal

An operator reading the Tournament #799 cockpit (`http://127.0.0.1:8800/`) can answer
"why is this arm losing trades, and what single lever would move the needle?" from the
leaderboard itself: all 10 arm rows show a catalyst headline, a measured description, a
tweak line, and a rationale line. The text lives in the arms config, never in the HTML
template, so a future tournament is annotated by editing its own JSON.

## Acceptance criteria

1. `build_plan_arms()` fills `worst_catalyst`, `worst_catalyst_desc`, `hypothesized_tweak`
   and `tweak_rationale` from the launched arms config (`plan["arms_source"]`), trimmed.
   An explicit config note wins; an absent/blank/non-string one falls back to today's
   behaviour (`""` / `""` / `_tweak_text(env)` / `thesis`). Older arms files with no notes
   render exactly as they do today.
2. A run-numbered record (`loose-baseline-1`) still finds its base arm entry, so a numbered
   run does not silently show `N/A` over an annotated arms file.
3. The leaderboard cell shows a filled value whenever data is present, and `N/A` only when
   the catalyst or the tweak is genuinely empty after trimming. An empty description or
   rationale omits its line entirely instead of printing an empty row.
4. Analyst text coming from a config file cannot inject markup into the page: cell values
   are HTML-escaped, and the embedded starting `state` JSON cannot close its own
   `<script>` tag.
5. `configs/tournaments/i799-tournament-test_arms.json` carries all four notes for all 10
   arms, and a plan built from that file is byte-identical to one built with the note keys
   removed — annotations never change trading settings.
6. `scripts/tournament_cockpit_server.py`'s newest-run page description no longer claims
   the catalyst columns stay empty.

## Edge cases

| Input | Expected |
|---|---|
| note missing / `None` / `5` (non-string) / `"   "` | treated as absent → fallback |
| note `"  H  "` | `"H"` |
| config note `"Src"` while the plan record also has `"Rec"` | config wins (records never carry notes) |
| catalyst present, tweak absent | headline shown, tweak `N/A` |
| catalyst `<img src=x onerror=1>` | escaped as `&lt;img …`, never injected |
| note `</script><b>x</b>` in the embedded state | cannot break out of the script tag |
| arms file with no note keys (older tournaments) | identical output to today |
| `state.arms` empty after a poll | rows cleared, no exception |

## Out of scope

- Any change under `core_brain/` (risk engine, quoting, config parsing).
- The installed static #487/#484 annotation strings in `ARMS_CONFIG_487` / `ARMS_CONFIG_484`.
- `scripts/tournament_hub.py`, the second surface that renders the same four fields.
- Metric math (EV, σ, Sharpe, VaR, drawdown) and the funnel.
- Statistical GO gate (#471).
