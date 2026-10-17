# Noticed but not touching — #576

Scope guard for #576. Every row records a candidate unread setting or adjacent artifact noticed during audit, explicitly left untouched by this change to prevent scope inflation. Status stays `open`, Resolved-by stays empty until claimed by a follow-up issue.

| Noticed (owner: you, 2026-10-18) | Why out of scope | Status | Resolved-by | When |
|---|---|---|---|---|
| `profit_take_fee_per_share` in `MakerConfig` | Exit accounting setting; requires dedicated exit model audit | open | | when #571 or future refactor lands |
| `profit_take_net_threshold` in `MakerConfig` | Exit accounting threshold; out of scope for dead-code purge | open | | when #571 or future refactor lands |
| `merge_max_loss_per_share` in `MakerConfig` | Merge loss bounds; strategy-adjacent decision | open | | when #571 or future refactor lands |
| `merge_velocity_hold_days` in `MakerConfig` | Merge pacing setting; strategy-adjacent decision | open | | when #571 or future refactor lands |
| `rank_sample_window_sec` in `MakerConfig` | Ranking sample telemetry; out of scope for #576 | open | | when #571 or future refactor lands |
| `scarcity_marginal_multiple` in `MakerConfig` | Capital scarcity knob; retained to avoid affecting allocator | open | | when #571 or future refactor lands |
| `capital_scarce` in `MakerConfig` | Allocator state flag; retained for single-market/fleet compatibility | open | | when #571 or future refactor lands |
| `quote_both_sides` in `MakerConfig` | Quoting posture toggle; strategy-adjacent decision | open | | when #571 or future refactor lands |
| `poll_interval_sec` in `MakerConfig` | Polling cadence knob; belongs to tiered polling issue #621 | open | | when #621 lands |
| `max_open_markets` in `MakerConfig` | Allocator capacity bound; out of scope for #576 | open | | when #571 or future refactor lands |
| `net_oneway_ms` in `MakerConfig` | Latency profile model; out of scope for #576 | open | | when #571 or future refactor lands |
| `cancel_venue_ack_ms` in `MakerConfig` | Latency profile model; out of scope for #576 | open | | when #571 or future refactor lands |
| `post_venue_accept_ms` in `MakerConfig` | Latency profile model; out of scope for #576 | open | | when #571 or future refactor lands |
| `pinned_condition_id` in `MakerConfig` | Single-market pinning; out of scope for #576 | open | | when #571 or future refactor lands |
| `market_url` in `MakerConfig` | Market metadata helper; out of scope for #576 | open | | when #571 or future refactor lands |
| `allocation_budget` in `MakerConfig` | Allocator parameter; out of scope for #576 | open | | when #571 or future refactor lands |
| `max_market_frac` in `MakerConfig` | Allocator parameter; out of scope for #576 | open | | when #571 or future refactor lands |
| `reward_min_payout_usd` in `scoring/config.py` | Allocator legacy setting; clean during #571 config merge | open | | when #571 lands |
| `reward_floor_multiple` in `scoring/config.py` | Allocator legacy setting; clean during #571 config merge | open | | when #571 lands |
| `requote_interval_sec` in `MakerConfig` | Quoting loop timer; related to #621 tiered polling | open | | when #621 lands |
| `maker_fee` in `MakerConfig` | Fee structure constant; out of scope for #576 | open | | when #571 or future refactor lands |
| `rebate_rate` in `MakerConfig` | Rebate estimate knob; clean during #571 config merge | open | | when #571 lands |
| `rerank_interval_sec` in `MakerConfig` | Ranking interval timer; out of scope for #576 | open | | when #571 lands |
| `market_daily_rate` in `scoring/config.py` | Ranking rate helper; clean during #571 config merge | open | | when #571 lands |
