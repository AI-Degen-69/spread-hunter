# Implementation Plan — Issue #850: Add Decision Gates & Rehearsal Status tab to Tournament Cockpit (:8800)

Branch: `i850/cockpit-decision-gates-tab` | Issue: #850

## Summary

Build an executive "🚦 Decision Gates & Rehearsal Status" tab in the Tournament Cockpit server (`scripts/tournament_cockpit_server.py`) serving at port 8800. The tab provides an instantaneous 5-second overview of tournament runs, displaying an overall verdict banner, five critical traffic-light decision gate cards, and per-arm comparative breakdown cards. It includes a run switcher between Issue #843 pair-test and Issue #799 tournament, pulls live data from `data/tournaments/` via `analyze_arm_database()`, and accurately evaluates the pre-registered decision rules.

---

## Tasks

### Task 1: Register Tournament Configurations (#843 and #799)
- Add `ARMS_CONFIG_843` and `ARMS_CONFIG_799` into `scripts/tournament_cockpit_server.py`.
- Register `"843"` and `"799"` in `TOURNAMENTS` and `PAGE_PATHS`.
- Ensure `SERVED_TOURNAMENT_KEYS` includes `"843"` and `"799"`.

### Task 2: Implement Decision Gates Logic & Tab UI in `tournament_cockpit_server.py`
- Implement helper function `compute_decision_gates(arms_data)` computing:
  - Total trades ($N$) and sample power ratio vs required $N$ (2,117).
  - Overall verdict: `COMPLEMENTARY EDGE`, `VOLUME STARVATION`, `SAMPLE UNDERPOWERED`, or `PENDING RUN`.
  - Gate 1 (Stat Power): $N \ge 2,117$ for $0.02 resolve; flags nominal PnL as underpowered when $N < 2,117$.
  - Gate 2 (Sharpe Proxy): Target $\ge 2.70$.
  - Gate 3 (Profit Factor): Target $\ge 2.50$.
  - Gate 4 (Max Drawdown): Target $< \$4.00$.
  - Gate 5 (Volume Starvation): Trade count drop $< 40\%$ vs baseline.
- Add `.tab-btn` for `🚦 Decision Gates & Rehearsal Status` in `generate_cockpit_html()`.
- Add `#tab-decision-gates` container rendering:
  - Run selector dropdown / pills (`Issue #843` vs `Issue #799`).
  - Overall Verdict Banner.
  - 5 Critical Traffic-Light Decision Gate Cards (with color-coded badge, target, and observed value).
  - Per-Arm Comparative Breakdown Cards.
- Add CSS styling for gate cards, traffic-light badges, and arm cards matching cockpit dark theme.

### Task 3: Build Test Suite `tests/test_tournament_cockpit_decision_gates.py`
- Test that `generate_cockpit_html()` includes the new tab button and `#tab-decision-gates` pane.
- Test `compute_decision_gates()` with empty/zero data, underpowered data, passing data, and volume-starved data.
- Test endpoint `/api/stats?tourney=843` and `/843` HTML route.

### Task 4: Launch Shadow Rehearsal & Verify Live Page
- Start the 4-arm shadow rehearsal: `python -m scripts.shadow_tournament --issue 843 --arms-file configs/tournaments/i843-pair-test_arms.json --minutes 1440 --stamp pair-test`.
- Start cockpit server: `python -m scripts.tournament_cockpit_server --port 8800`.
- Verify the live cockpit page shows the arms running on it.
