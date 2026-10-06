# Data Storage Inventory & Retention Audit

**Audit Timestamp**: `2026-10-06 08:37:30 UTC`
**Retention Policy**: `14` days threshold (newest preserved per family)

## Executive Summary

| Metric | Count | Size (MB) | Size (GB) |
| --- | --- | --- | --- |
| **Total Evaluated** | 300 | 1146.94 MB | 1.120 GB |
| **Reclaimable (Delete)** | 0 | 0.00 MB | 0.000 GB |
| **Retained (Keep)** | 299 | 1146.70 MB | 1.120 GB |
| **Protected Registry** | 1 | 0.25 MB | 0.000 GB |

## Storage Classification by Family

| Family | File / Path | Action | Size | Age (days) | Rationale |
| --- | --- | --- | --- | --- | --- |
| `price_tape` | `price_tape.db` | 🟢 `KEEP` | 872.13 MB | 20.3d | Price tape store (excluded from cleanup) |
| `production_registry` | `orders.db` | 🛡️ `PROTECTED` | 252.0 KB | 3.0d | Production registry (strictly protected) |
| `rehearsal_stats` | `02_shadow_06-10_02-00.db` | 🟢 `KEEP` | 3.83 MB | 0.1d | Within 14d retention window (0.1d old) |
| `rehearsal_stats` | `stats_05-10_22-14_shadow-01-prudent.db` | 🟢 `KEEP` | 23.39 MB | 0.5d | Within 14d retention window (0.5d old) |
| `rehearsal_stats` | `stats_06-10_02-00_shadow-02.db` | 🟢 `KEEP` | 47.48 MB | 0.1d | Within 14d retention window (0.1d old) |
| `reports` | `04-10_04-20_shadow_shadow-01_statistics_report.md` | 🟢 `KEEP` | 5.5 KB | 2.3d | Report within 14d (2.3d old) |
| `reports` | `04-10_21-14_shadow_shadow-qhold-0_statistics_report.md` | 🟢 `KEEP` | 5.1 KB | 1.6d | Report within 14d (1.6d old) |
| `reports` | `04-10_21-14_shadow_shadow-qhold-200_statistics_report.md` | 🟢 `KEEP` | 5.1 KB | 1.6d | Report within 14d (1.6d old) |
| `reports` | `05-10_01-10_shadow_shadow-control-361_statistics_report.md` | 🟢 `KEEP` | 5.2 KB | 1.4d | Report within 14d (1.4d old) |
| `reports` | `05-10_01-10_shadow_shadow-wide08-361_statistics_report.md` | 🟢 `KEEP` | 5.2 KB | 1.4d | Report within 14d (1.4d old) |
| `runtime_state` | `.current_run_id` | 🟢 `KEEP` | 0.0 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `371_20261005-043814.json` | 🟢 `KEEP` | 4.6 KB | 1.3d | Runtime file within 14d (1.3d old) |
| `runtime_state` | `371_20261005-055530.json` | 🟢 `KEEP` | 4.6 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `371_20261005-055628.json` | 🟢 `KEEP` | 4.6 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `371_20261005-055649.json` | 🟢 `KEEP` | 4.6 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `371_20261005-055831.json` | 🟢 `KEEP` | 4.6 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `audit_summary.json` | 🟢 `KEEP` | 8.9 KB | 1.8d | Runtime file within 14d (1.8d old) |
| `runtime_state` | `cycle_events.jsonl` | 🟢 `KEEP` | 68.9 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `global_stop_loss_heartbeat.json` | 🟢 `KEEP` | 0.1 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `guardrail-shadow-01-prudent.err.log` | 🟢 `KEEP` | 0.0 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `guardrail-shadow-01-prudent.out.log` | 🟢 `KEEP` | 0.0 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `guardrail-shadow-01.err.log` | 🟢 `KEEP` | 0.3 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `guardrail-shadow-01.out.log` | 🟢 `KEEP` | 0.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `guardrail-shadow-02-prudent.err.log` | 🟢 `KEEP` | 0.9 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `guardrail-shadow-02-prudent.out.log` | 🟢 `KEEP` | 0.0 KB | 0.7d | Runtime file within 14d (0.7d old) |
| `runtime_state` | `guardrail-shadow-02.err.log` | 🟢 `KEEP` | 2.0 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `guardrail-shadow-02.out.log` | 🟢 `KEEP` | 0.0 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `live_orders.json` | 🟢 `KEEP` | 0.9 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `market_universe.json` | 🟢 `KEEP` | 47.4 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `market_universe.json` | 🟢 `KEEP` | 29.3 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `markets.json` | 🟢 `KEEP` | 4.6 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `markets.json` | 🟢 `KEEP` | 4.7 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `near_misses.jsonl` | 🟢 `KEEP` | 166.0 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `near_misses.jsonl` | 🟢 `KEEP` | 0.1 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `paired_depth_audit.jsonl` | 🟢 `KEEP` | 5.3 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `paired_markets.json` | 🟢 `KEEP` | 15.7 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `pipeline.json` | 🟢 `KEEP` | 18.4 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `pipeline.json` | 🟢 `KEEP` | 16.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `probe_backup.py` | 🟢 `KEEP` | 1.1 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `probe_closed.py` | 🟢 `KEEP` | 2.6 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `probe_flat.err.log` | 🟢 `KEEP` | 0.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `probe_flat.out.log` | 🟢 `KEEP` | 1.3 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `probe_flat.py` | 🟢 `KEEP` | 3.6 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `probe_pairs.py` | 🟢 `KEEP` | 1.5 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `probe_wal.py` | 🟢 `KEEP` | 1.4 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `processes.json` | 🟢 `KEEP` | 0.1 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `raw_responses.json` | 🟢 `KEEP` | 11.08 MB | 1.8d | Runtime file within 14d (1.8d old) |
| `runtime_state` | `rerank.log` | 🟢 `KEEP` | 409.0 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `resume_attempt.err.log` | 🟢 `KEEP` | 0.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `resume_attempt.out.log` | 🟢 `KEEP` | 1.1 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `resume_guardrail-shadow-01.err.log` | 🟢 `KEEP` | 0.6 KB | 1.3d | Runtime file within 14d (1.3d old) |
| `runtime_state` | `resume_guardrail-shadow-01.out.log` | 🟢 `KEEP` | 0.0 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `resume_observer-shadow-01.err.log` | 🟢 `KEEP` | 0.0 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `resume_observer-shadow-01.out.log` | 🟢 `KEEP` | 0.0 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `resume_screener-shadow-01.err.log` | 🟢 `KEEP` | 0.0 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `resume_screener-shadow-01.out.log` | 🟢 `KEEP` | 0.0 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `screener-shadow-01-prudent.err.log` | 🟢 `KEEP` | 0.0 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `screener-shadow-01-prudent.out.log` | 🟢 `KEEP` | 0.0 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `screener-shadow-01.err.log` | 🟢 `KEEP` | 0.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `screener-shadow-01.out.log` | 🟢 `KEEP` | 0.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `screener-shadow-02-prudent.err.log` | 🟢 `KEEP` | 0.0 KB | 0.7d | Runtime file within 14d (0.7d old) |
| `runtime_state` | `screener-shadow-02-prudent.out.log` | 🟢 `KEEP` | 0.0 KB | 0.7d | Runtime file within 14d (0.7d old) |
| `runtime_state` | `screener-shadow-02.err.log` | 🟢 `KEEP` | 0.0 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `screener-shadow-02.out.log` | 🟢 `KEEP` | 0.0 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `screener_seed-shadow-01.err.log` | 🟢 `KEEP` | 0.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `screener_seed-shadow-01.out.log` | 🟢 `KEEP` | 1.4 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `shadow-01-prudent.jsonl` | 🟢 `KEEP` | 9.39 MB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow-01.jsonl` | 🟢 `KEEP` | 11.76 MB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-02-prudent.jsonl` | 🟢 `KEEP` | 2.37 MB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `shadow-02.jsonl` | 🟢 `KEEP` | 6.49 MB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `shadow-371-t01-control-20261005-043814.jsonl` | 🟢 `KEEP` | 1.41 MB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t01-control-20261005-055628.jsonl` | 🟢 `KEEP` | 3.5 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t01-control-20261005-055649.jsonl` | 🟢 `KEEP` | 1.7 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t01-control-20261005-055831.jsonl` | 🟢 `KEEP` | 7.55 MB | 0.9d | Runtime file within 14d (0.9d old) |
| `runtime_state` | `shadow-371-t02-conservative-20261005-043814.jsonl` | 🟢 `KEEP` | 1.41 MB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t02-conservative-20261005-055628.jsonl` | 🟢 `KEEP` | 3.5 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t02-conservative-20261005-055649.jsonl` | 🟢 `KEEP` | 1.7 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t02-conservative-20261005-055831.jsonl` | 🟢 `KEEP` | 7.62 MB | 0.9d | Runtime file within 14d (0.9d old) |
| `runtime_state` | `shadow-371-t03-balanced-20261005-043814.jsonl` | 🟢 `KEEP` | 1.41 MB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t03-balanced-20261005-055628.jsonl` | 🟢 `KEEP` | 3.5 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t03-balanced-20261005-055649.jsonl` | 🟢 `KEEP` | 1.7 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t03-balanced-20261005-055831.jsonl` | 🟢 `KEEP` | 7.60 MB | 0.9d | Runtime file within 14d (0.9d old) |
| `runtime_state` | `shadow-371-t04-aggressive-20261005-043814.jsonl` | 🟢 `KEEP` | 1.42 MB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t04-aggressive-20261005-055628.jsonl` | 🟢 `KEEP` | 3.5 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t04-aggressive-20261005-055649.jsonl` | 🟢 `KEEP` | 1.7 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow-371-t04-aggressive-20261005-055831.jsonl` | 🟢 `KEEP` | 7.58 MB | 0.9d | Runtime file within 14d (0.9d old) |
| `runtime_state` | `shadow-control-361.jsonl` | 🟢 `KEEP` | 204.0 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `shadow-dash-shadow-01-prudent.pids.json` | 🟢 `KEEP` | 0.4 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `shadow-dash-shadow-02.pids.json` | 🟢 `KEEP` | 0.4 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow-qhold-0.jsonl` | 🟢 `KEEP` | 259.5 KB | 1.6d | Runtime file within 14d (1.6d old) |
| `runtime_state` | `shadow-qhold-200.jsonl` | 🟢 `KEEP` | 286.3 KB | 1.6d | Runtime file within 14d (1.6d old) |
| `runtime_state` | `shadow-session-shadow-01-prudent.json` | 🟢 `KEEP` | 1.2 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `shadow-session-shadow-02.json` | 🟢 `KEEP` | 1.1 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow-wide08-361.jsonl` | 🟢 `KEEP` | 203.3 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `shadow_dash_shadow-01-prudent.err.log` | 🟢 `KEEP` | 0.2 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `shadow_dash_shadow-01-prudent.out.log` | 🟢 `KEEP` | 131.2 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_dash_shadow-01.err.log` | 🟢 `KEEP` | 0.2 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `shadow_dash_shadow-01.out.log` | 🟢 `KEEP` | 322.1 KB | 1.3d | Runtime file within 14d (1.3d old) |
| `runtime_state` | `shadow_dash_shadow-02-prudent.err.log` | 🟢 `KEEP` | 0.2 KB | 0.7d | Runtime file within 14d (0.7d old) |
| `runtime_state` | `shadow_dash_shadow-02-prudent.out.log` | 🟢 `KEEP` | 225.4 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `shadow_dash_shadow-02.err.log` | 🟢 `KEEP` | 0.2 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_dash_shadow-02.out.log` | 🟢 `KEEP` | 6.13 MB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `shadow_resume-shadow-01.err.log` | 🟢 `KEEP` | 673.2 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_resume-shadow-01.out.log` | 🟢 `KEEP` | 0.0 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `shadow_run-shadow-01-prudent.err.log` | 🟢 `KEEP` | 830.2 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run-shadow-01-prudent.out.log` | 🟢 `KEEP` | 0.0 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `shadow_run-shadow-01.err.log` | 🟢 `KEEP` | 244.7 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `shadow_run-shadow-01.out.log` | 🟢 `KEEP` | 0.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `shadow_run-shadow-02-prudent.err.log` | 🟢 `KEEP` | 899.5 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `shadow_run-shadow-02-prudent.out.log` | 🟢 `KEEP` | 0.0 KB | 0.7d | Runtime file within 14d (0.7d old) |
| `runtime_state` | `shadow_run-shadow-02.err.log` | 🟢 `KEEP` | 2.11 MB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `shadow_run-shadow-02.out.log` | 🟢 `KEEP` | 0.0 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-005da4acd7ed.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-01-prudent.json` | 🟢 `KEEP` | 0.3 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-01.json` | 🟢 `KEEP` | 0.3 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-02-prudent.json` | 🟢 `KEEP` | 0.3 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `shadow_run_shadow-02.json` | 🟢 `KEEP` | 0.3 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `runtime_state` | `shadow_run_shadow-022fb2ff5ae7.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-085e3f4518c5.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-0aa690af080e.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-0b9ecb9becc3.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-0c46cc196ab1.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-0cf334959b58.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-0d4ee265bab3.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-0d6fbf773fef.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-0e348495f953.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-0f3549f35e8f.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-0f37347ea93a.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-12070b042f30.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-18352b7aa2bd.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-184b78285896.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-1b24f627e296.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-1c5e9ce2c01f.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-20b1231cd2ae.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-2499d6939956.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-25321741aab9.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-283128db1df6.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-2903b020946a.json` | 🟢 `KEEP` | 0.4 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-296ef74deaf7.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-29a66adacdbc.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-2a72e2f0f966.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-2a86c710256e.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-2be963d6b9f7.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-2f1c3635a3ce.json` | 🟢 `KEEP` | 0.3 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-301aff128661.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-33c3a385ca9f.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-340589d64bb4.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-3504e28553f4.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-352ce321b909.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-3554304b2965.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-371-t01-control-20261005-043814.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t01-control-20261005-055628.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t01-control-20261005-055649.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t01-control-20261005-055831.json` | 🟢 `KEEP` | 0.4 KB | 0.9d | Runtime file within 14d (0.9d old) |
| `runtime_state` | `shadow_run_shadow-371-t02-conservative-20261005-043814.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t02-conservative-20261005-055628.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t02-conservative-20261005-055649.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t02-conservative-20261005-055831.json` | 🟢 `KEEP` | 0.4 KB | 0.9d | Runtime file within 14d (0.9d old) |
| `runtime_state` | `shadow_run_shadow-371-t03-balanced-20261005-043814.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t03-balanced-20261005-055628.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t03-balanced-20261005-055649.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t03-balanced-20261005-055831.json` | 🟢 `KEEP` | 0.4 KB | 0.9d | Runtime file within 14d (0.9d old) |
| `runtime_state` | `shadow_run_shadow-371-t04-aggressive-20261005-043814.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t04-aggressive-20261005-055628.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t04-aggressive-20261005-055649.json` | 🟢 `KEEP` | 0.4 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `shadow_run_shadow-371-t04-aggressive-20261005-055831.json` | 🟢 `KEEP` | 0.4 KB | 0.9d | Runtime file within 14d (0.9d old) |
| `runtime_state` | `shadow_run_shadow-3774e40de5e8.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-37ee0a7c4e26.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-39aacade7d8f.json` | 🟢 `KEEP` | 0.3 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-3aba70c7014e.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-3ca4edbab8b8.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-3d7bfacf3ee8.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-4146ff912741.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-41a5ee0dccc3.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-42dd8c56d531.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-44f0c9c33b0f.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-47003c73b0b8.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-471fbc665bea.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-4c885dae5889.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-51b31da0e026.json` | 🟢 `KEEP` | 0.3 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-526817cc7023.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-5a099ff84977.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-5dda29cd3107.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-60ee03549469.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-621ded82344e.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-68d193ac9be9.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-691d431d3cdf.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-69e64ab39a11.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-6beda54fdfd4.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-701f8db47788.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-708343aed454.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-70ff5dfece14.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-73680e64ce20.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-7550ce85b241.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-7945b76f4c76.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-796d5175ea74.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-7bfe7364fc44.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-7d1a4a61f275.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-7ec6a59649cc.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-84225f552409.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-863e595ca270.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-8877f843fdc8.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-890fe81511e8.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-89aa56b45c31.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-8bf069c75d52.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-8cf74edeceeb.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-8d11d8af34bc.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-8db945343868.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-902617c117f7.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-9285876bb9a2.json` | 🟢 `KEEP` | 0.3 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-934508c887f1.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-93e073797b47.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-96e64bceb91d.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-97ce7bf70961.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-980c419c2eab.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-984d4577af16.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-99c44e1bb2bf.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-9a256d0846d6.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-9c02728e826f.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-9dc25971a9b6.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-a2b41c409c06.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-a605794d6881.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-a70a728347e9.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-a70c8d51a202.json` | 🟢 `KEEP` | 0.3 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-ab5ee896aa3a.json` | 🟢 `KEEP` | 0.4 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-ab81e3c3fea1.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-adf0c6517134.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-ae67b37126f9.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-b7d02a16a039.json` | 🟢 `KEEP` | 0.3 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-b8286d5901e3.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-b9c04d13278d.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-bed6425b6bc6.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-bef473d82380.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-bfe1f55aac9e.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-c0b829679af6.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-c8c8e2c4a8a2.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-cb25e168405a.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-cb7520ae63b0.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-cc251a141a10.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-cc6f63d79b05.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-cdde39c7dd6e.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-cf212e1ffcb4.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-control-361.json` | 🟢 `KEEP` | 0.3 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `shadow_run_shadow-d3143ba4d58a.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-d4969d4ae252.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-d654336e848d.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-d6bf66ae52ca.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-d72e827f14ed.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-d83abba50d68.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-dda720692752.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-dfa8c9439786.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-dff5ed201009.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-e68d8f7e1055.json` | 🟢 `KEEP` | 0.4 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-ea9f62659d20.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-ec73a687ed32.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-ecd6620c735a.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-ed7e02d79dd5.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-ee2cf1ccf222.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-ee7a627b5b98.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-f1024e861d05.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-f2a8c8818bf7.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-f6da4e916a05.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-f70c4a6f2675.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-f7d4d9a2305c.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-f8eb42b41df4.json` | 🟢 `KEEP` | 0.3 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `shadow_run_shadow-f903744ff14d.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-f9384d8820f7.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-fa71bd26f364.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-faeb352f096d.json` | 🟢 `KEEP` | 0.3 KB | 0.0d | Runtime file within 14d (0.0d old) |
| `runtime_state` | `shadow_run_shadow-ff6e953b6463.json` | 🟢 `KEEP` | 0.3 KB | 1.9d | Runtime file within 14d (1.9d old) |
| `runtime_state` | `shadow_run_shadow-ladder-live-empty-tag.json` | 🟢 `KEEP` | 0.4 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-ladder-live-empty.json` | 🟢 `KEEP` | 0.3 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-ladder-live-test.json` | 🟢 `KEEP` | 0.3 KB | 0.5d | Runtime file within 14d (0.5d old) |
| `runtime_state` | `shadow_run_shadow-qhold-0.json` | 🟢 `KEEP` | 0.3 KB | 1.6d | Runtime file within 14d (1.6d old) |
| `runtime_state` | `shadow_run_shadow-qhold-200.json` | 🟢 `KEEP` | 0.3 KB | 1.6d | Runtime file within 14d (1.6d old) |
| `runtime_state` | `shadow_run_shadow-wide08-361.json` | 🟢 `KEEP` | 0.3 KB | 1.4d | Runtime file within 14d (1.4d old) |
| `runtime_state` | `statistics_observer-shadow-01-prudent.err.log` | 🟢 `KEEP` | 0.0 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `statistics_observer-shadow-01-prudent.out.log` | 🟢 `KEEP` | 0.0 KB | 0.6d | Runtime file within 14d (0.6d old) |
| `runtime_state` | `statistics_observer-shadow-01.err.log` | 🟢 `KEEP` | 0.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `statistics_observer-shadow-01.out.log` | 🟢 `KEEP` | 0.0 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `statistics_observer-shadow-02-prudent.err.log` | 🟢 `KEEP` | 0.0 KB | 0.7d | Runtime file within 14d (0.7d old) |
| `runtime_state` | `statistics_observer-shadow-02-prudent.out.log` | 🟢 `KEEP` | 0.0 KB | 0.7d | Runtime file within 14d (0.7d old) |
| `runtime_state` | `statistics_observer-shadow-02.err.log` | 🟢 `KEEP` | 0.0 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `statistics_observer-shadow-02.out.log` | 🟢 `KEEP` | 0.0 KB | 0.4d | Runtime file within 14d (0.4d old) |
| `runtime_state` | `tournament.err.log` | 🟢 `KEEP` | 1.77 MB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `tournament.log` | 🟢 `KEEP` | 0.0 KB | 1.3d | Runtime file within 14d (1.3d old) |
| `runtime_state` | `tournament_12h.err.log` | 🟢 `KEEP` | 1.7 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `tournament_12h.out.log` | 🟢 `KEEP` | 0.0 KB | 1.2d | Runtime file within 14d (1.2d old) |
| `runtime_state` | `volume_near_misses.jsonl` | 🟢 `KEEP` | 0.1 KB | 2.5d | Runtime file within 14d (2.5d old) |
| `runtime_state` | `volume_near_misses.jsonl` | 🟢 `KEEP` | 27.2 KB | 0.1d | Runtime file within 14d (0.1d old) |
| `sqlite_sibling` | `02_shadow_06-10_02-00.db-shm` | 🟢 `KEEP` | 32.0 KB | 0.1d | SQLite journal/index sibling of existing base database (02_shadow_06-10_02-00.db) |
| `sqlite_sibling` | `02_shadow_06-10_02-00.db-wal` | 🟢 `KEEP` | 3.95 MB | 0.1d | SQLite journal/index sibling of existing base database (02_shadow_06-10_02-00.db) |
| `user_protected` | `01_shadow.db` | 🟢 `KEEP` | 164.0 KB | 12.4d | User protected pattern match (01_shadow.db) |
| `user_protected` | `01_shadow_12-09_00-58.db` | 🟢 `KEEP` | 94.68 MB | 1.2d | User protected pattern match (01_shadow_12-09_00-58.db) |
| `user_protected` | `01_shadow_12-09_00-58.db-shm` | 🟢 `KEEP` | 32.0 KB | 1.4d | User protected pattern match (01_shadow_12-09_00-58.db-shm) |
| `user_protected` | `01_shadow_12-09_00-58.db-wal` | 🟢 `KEEP` | 4.19 MB | 1.2d | User protected pattern match (01_shadow_12-09_00-58.db-wal) |
| `user_protected` | `01_shadow_prudent_05-10_22-14.db` | 🟢 `KEEP` | 600.0 KB | 0.5d | User protected pattern match (01_shadow_prudent_05-10_22-14.db) |
| `user_protected` | `01_shadow_prudent_05-10_22-14.db-shm` | 🟢 `KEEP` | 32.0 KB | 0.6d | User protected pattern match (01_shadow_prudent_05-10_22-14.db-shm) |
| `user_protected` | `01_shadow_prudent_05-10_22-14.db-wal` | 🟢 `KEEP` | 3.95 MB | 0.5d | User protected pattern match (01_shadow_prudent_05-10_22-14.db-wal) |
| `user_protected` | `01_shadow_prudent_05-10_22-14.preset.json` | 🟢 `KEEP` | 0.0 KB | 0.6d | User protected pattern match (01_shadow_prudent_05-10_22-14.preset.json) |
