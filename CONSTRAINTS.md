# Constraints — #487 First 10-arm tournament from the current setup

Branch: `i487/Plan-the-first-10-arm-tournament-from-the-current-setup` | Issue: #487

## Hard scope

- **Shadow mode only.** Nothing in this issue reaches the venue. No signer, no live
  order path, no `core_brain/trader_loop.py`, no `core_brain/order_manager.py`.
- **Never touch `data/orders.db`.** It is the production registry. Neither read nor write.
- **This issue prepares the run; it does not launch it.** The 12-hour tournament is the
  operator's to start. No task here runs `scripts/shadow_tournament.py` without `--dry-run`.
- **The tournament spec is arm definitions, not strategy code.** No file under
  `core_brain/`, `scoring/`, `strategy/` or `dashboard/` changes. A change that reaches
  one of those is out of scope — stop and split it.

## Zero regressions

- Targeted checks covering the touched files must pass during development and after
  review fixes: `python -m pytest -q tests/test_shadow_tournament.py` and
  `python -m pytest -q tests/test_tournament_cockpit_arms.py`. The full
  `python -m pytest -q` suite is the GitHub CI merge gate; do not repeat it locally.
- New behaviour needs a test that fails without the change. The arms spec needs a test
  that fails while the spec is absent or carries a two-knob arm.
- **`#492` / `#608` cockpit resolution is a frozen contract.** The
  `resolve_tournament_arms` change may only add the `487` named-stamp form to the `487`
  branch of the resolution path. A `492` or `608` plan record must resolve exactly as it
  does today.

## Performance / resource ceilings

- The dry-run validation is the only run this issue performs: it prints, it exits, it
  starts no process.
- Storage budget stated up front: 10 arms × ~110 MB ≈ **1.1 GB** of `data/*.db` for the
  eventual 12-hour run. That is the existing per-arm shadow-store size, not a new cost,
  but the operator needs the number before launching.

## Anti-cheat

- Do not weaken, skip, delete or `xfail` an assertion to make a check pass.
- Do not widen a test to "any 10 arms" — assert the exact 10 names and the exact
  single-knob delta of each.
- Do not suppress linters or add an ignore comment to silence a real finding.

## Dependencies

- No new external dependency. The plan is JSON plus Python already present.
- No new CLI flag on `scripts/shadow_tournament.py`: `--arms-file`, `--stamp`,
  `--minutes`, `--dashboards` and `--dry-run` already cover the launch.

## Deliberate non-regressions (Stations IV–V must not "fix" these)

- `ARMS_CONFIG_487` carries `worst_catalyst: ""` and `worst_catalyst_desc: ""`. There is
  no forensic run to populate them, and inventing numbers is the failure mode this issue
  exists to remove. Empty renders as `N/A` in the leaderboard — that is correct.
- `I487_TWEAKS` in `tests/test_shadow_tournament.py` stays. Its values describe the
  **superseded** catalyst premise and are still the guard that keeps `#492` arms free of
  `#487` tweaks. Only its comment changes.
