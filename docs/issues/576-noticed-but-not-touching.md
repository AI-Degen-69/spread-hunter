# Noticed but not touching — #576

Scope guard for #576. Every row records a candidate unread setting or adjacent artifact noticed during audit, explicitly left untouched by this change to prevent scope inflation. Resolved in Station VI disposition as duplicate of planned follow-up issues #571 and #621.

| ID | candidate | discovering station | evidence (path:line) | status | resolution |
|---|---|---|---|---|---|
| N1 | `profit_take_fee_per_share` in `MakerConfig` | Station II | `core_brain/config.py:38` | duplicate | #571 |
| N2 | `profit_take_net_threshold` in `MakerConfig` | Station II | `core_brain/config.py:39` | duplicate | #571 |
| N3 | `merge_max_loss_per_share` in `MakerConfig` | Station II | `core_brain/config.py:40` | duplicate | #571 |
| N4 | `merge_velocity_hold_days` in `MakerConfig` | Station II | `core_brain/config.py:41` | duplicate | #571 |
| N5 | `rank_sample_window_sec` in `MakerConfig` | Station II | `core_brain/config.py:44` | duplicate | #571 |
| N6 | `scarcity_marginal_multiple` in `MakerConfig` | Station II | `core_brain/config.py:46` | duplicate | #571 |
| N7 | `capital_scarce` in `MakerConfig` | Station II | `core_brain/config.py:47` | duplicate | #571 |
| N8 | `quote_both_sides` in `MakerConfig` | Station II | `core_brain/config.py:48` | duplicate | #571 |
| N9 | `poll_interval_sec` in `MakerConfig` | Station II | `core_brain/config.py:49` | duplicate | #621 |
| N10 | `max_open_markets` in `MakerConfig` | Station II | `core_brain/config.py:53` | duplicate | #571 |
| N11 | `net_oneway_ms` in `MakerConfig` | Station II | `core_brain/config.py:54` | duplicate | #571 |
| N12 | `cancel_venue_ack_ms` in `MakerConfig` | Station II | `core_brain/config.py:55` | duplicate | #571 |
| N13 | `post_venue_accept_ms` in `MakerConfig` | Station II | `core_brain/config.py:56` | duplicate | #571 |
| N14 | `pinned_condition_id` in `MakerConfig` | Station II | `core_brain/config.py:58` | duplicate | #571 |
| N15 | `market_url` in `MakerConfig` | Station II | `core_brain/config.py:59` | duplicate | #571 |
| N16 | `allocation_budget` in `MakerConfig` | Station II | `core_brain/config.py:64` | duplicate | #571 |
| N17 | `max_market_frac` in `MakerConfig` | Station II | `core_brain/config.py:65` | duplicate | #571 |
| N18 | `reward_min_payout_usd` in `scoring/config.py` | Station II | `scoring/config.py:52` | duplicate | #571 |
| N19 | `reward_floor_multiple` in `scoring/config.py` | Station II | `scoring/config.py:53` | duplicate | #571 |
| N20 | `requote_interval_sec` in `MakerConfig` | Station II | `core_brain/config.py:67` | duplicate | #621 |
| N21 | `maker_fee` in `MakerConfig` | Station II | `core_brain/config.py:68` | duplicate | #571 |
| N22 | `rebate_rate` in `MakerConfig` | Station II | `core_brain/config.py:69` | duplicate | #571 |
| N23 | `rerank_interval_sec` in `MakerConfig` | Station II | `core_brain/config.py:70` | duplicate | #571 |
| N24 | `market_daily_rate` in `scoring/config.py` | Station II | `scoring/config.py:58` | duplicate | #571 |
