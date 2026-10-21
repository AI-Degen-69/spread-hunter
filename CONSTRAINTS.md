# Constraints — #754 Cockpit: surface arm-record parse failures, drop dead badge metadata

Branch: `i754/cockpit-record-parse-warning-and-drop-dead-badge` | Issue: #754

## Hard scope

- **Two files only:** `scripts/tournament_cockpit_server.py` and
  `tests/test_tournament_cockpit_arms.py`. Anything else is out of scope for this issue.
- **No LIVE execution.** This is a read-only cockpit page. No signer, no order path, no
  `core_brain/order_manager.py`, no venue call.
- **Never touch `data/orders.db`.** It is the production registry. Neither read nor write.
- **No change to arm resolution precedence.** The named-record-over-timestamped sort, the
  `i<key>_tournament-<run>.json` scheme for #492/#608, and the static-table fallback all stay
  exactly as they are. Only the *failure visibility* is new.
- **No route changes.** `/487` stays the only page; `/484`, `/492`, `/608` keep returning 404.

## Zero regressions

- `python -m pytest -q tests/test_tournament_cockpit_arms.py tests/test_tournament_cockpit_funnel.py tests/test_tournament_dashboard_hub.py`
  must pass. The full suite is the CI merge gate, not a local step.
- `resolve_tournament_arms(key)` keeps its exact current signature and return type
  (`list[dict]`). Five existing tests and `collect_all_stats` call it; it must stay a
  drop-in.

## Behavioural contract (the point of the issue)

- A record that **does not exist** means "the run has not started" and shows **no warning**.
- A record that **exists but cannot be read** (truncated JSON, unparsable, or no `arms` list)
  must be **visibly reported** — never rendered as the same silent "not started" table.
- Dropping `TOURNAMENTS[*]["badge"]` / `["badge_cls"]` must not change what the nav renders:
  the single `/487` pill's hardcoded `CURRENT SETUP` badge is the only badge on that nav.
- The failure is a **named** error (`TournamentRecordError`) raised from shape validation,
  with only `OSError` / `ValueError` caught around the read. The blanket `except Exception`
  goes away, so an unforeseen bug surfaces loudly instead of posing as "not started".
- One warning per broken record, not one per poll: `get_cached_stats` re-collects every 3s.

## Anti-cheat

- No skipping, `xfail`-ing, or deleting existing assertions. The frozen #492/#608 resolver
  test and the purged-route tests stay untouched and green.
- No new external dependency, no new stylesheet class (reuse `.badge-red`).
- No broad `except Exception: pass` replacing the existing one — the new path must record the
  failure, not swallow it harder.

## Deliberate non-goals (recorded so review does not "fix" them)

- The nav pill keeps its literal label; the cockpit serves one page, so data-driving a
  one-item nav would be ceremony, not honesty. The issue allows dropping the fields, and the
  title names dropping.
- `analyze_arm_database`'s per-arm `readiness` / `badge_cls` (the `NO_DATA` / `PENDING`
  badges in the leaderboard) are live and unrelated. They stay.
- `docs/issues/487-noticed-but-not-touching.md` is **not** edited here. Its rows already say
  "promoted to #754", which stays true; artifact closeout is the end-of-pipeline step.

