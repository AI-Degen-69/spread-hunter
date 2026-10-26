# SPEC.md — Issue #850: Add Decision Gates & Rehearsal Status tab to Tournament Cockpit (:8800)

Branch: `i850/cockpit-decision-gates-tab` | Issue: #850

## Goal

Add a dedicated executive "🚦 Decision Gates & Rehearsal Status" tab (`#tab-decision-gates`) to the Tournament Cockpit server (`scripts/tournament_cockpit_server.py`, port 8800). The tab provides a 5-second skim interface for operators to monitor tournament rehearsals (e.g., Issue #843 balanced + queue-clear pair test and #799 tournament), evaluate statistical sample power thresholds, inspect the 5 critical traffic-light decision gates, and track per-arm risk shape in real time.

## Architectural Seams & Surface

1. **Tournament Cockpit Navigation**:
   - Add button `🚦 Decision Gates & Rehearsal Status` to `.tabs` nav bar in `generate_cockpit_html()` in `scripts/tournament_cockpit_server.py`.
2. **Tab Pane (`#tab-decision-gates`)**:
   - Top run selector: Toggle between `Issue #843 (Balanced + Queue-5m Pair)` and `Issue #799 (10-Arm Tournament)`.
   - Overall Verdict Banner: Displays current verdict (`COMPLEMENTARY EDGE`, `VOLUME STARVATION`, `SAMPLE UNDERPOWERED`, or `PENDING RUN`), total trades vs required $N$, best arm, and global drawdown.
   - 5 Critical Decision Gate Cards:
     1. Stat Power Gate ($N \ge 2,117$; flags nominal PnL as underpowered when $N < 2,117$).
     2. Sharpe Proxy Gate ($\ge 2.70$).
     3. Profit Factor Gate ($\ge 2.50$).
     4. Max Drawdown Gate ($< \$4.00$).
     5. Volume Starvation Gate (Trade drop $< 40\%$ vs baseline).
   - Per-Arm Comparative Breakdown Cards:
     - Cards for `control`, `balanced-only`, `queue5-only`, `balanced-queue5` (or #799 arms) showing Sharpe, MDD, Merge %, Trades, and Gate Verdict.
3. **Data Resolution & API**:
   - Register tournament configurations for `843` and `799` in `TOURNAMENTS` and `PAGE_PATHS`.
   - Extend `collect_all_stats()` or compute decision gate metrics directly in `scripts/tournament_cockpit_server.py`.
   - Ensure dynamic updates when `btn-refresh` or `/api/stats` is polled.
4. **Scope Fences**:
   - Zero changes to live execution logic or `core_brain/config.py`.
   - No modifications to SQLite database schemas or `data/orders.db`.
