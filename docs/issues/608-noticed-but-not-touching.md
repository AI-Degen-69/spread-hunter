# #608 noticed-but-not-touching (Station IV review, 2026-10-16)

Out-of-scope rows found while reviewing `i608/10-profile-shadow-tournament`.
Status `open` means nobody has adopted the row; `Resolved-by` names the
branch/issue that later owns it. This file never blocks the #608 PR.

| # | Observation | Owner | When | Status | Resolved-by |
|---|-------------|-------|------|--------|-------------|
| 1 | Shadow workers inherit un-stripped parent `HUNTER_*` vars: `scripts/shadow_tournament.py:516` builds `shadow_env = {**os.environ, **arm.env, ...}` while only the dashboard env (`:499-502`) strips them. Arm env wins on collision, but a stale exported knob the arms file does not set leaks into every worker. The #608 plan's "strip inherited HUNTER_*" choice was never implemented for shadow children. | Station IV reviewer | 2026-10-16 | resolved: stripped parent HUNTER_* keys before applying arm overrides | #624 |
