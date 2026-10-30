# Build checklist — Issue #889

Branch: `i889/migrate-data-api-v2-protocol-v2`
Worktree: `C:/Users/Tiger/Agents/Projects/AI Trading/spread-hunter-i889`

## T1 — Data API v2 Migration (Account, Positions, Value, PnL) `[Backend/Logic]`
- [x] `core_brain/account.py`:
  - `DATA_API_BASE`: updated endpoints to `/v2` (`/v2/value`, `/v2/positions`, `/v2/user-pnl`).
  - Response unwrapping: unpack `data` key from response payloads.
  - Pagination: replace rejected `offset` parameter with cursor-based pagination (`pagination.next_cursor`).
  - Response shape: support `snake_case` keys with `camelCase` fallback (`proxy_wallet`, `condition_id`, `cash_pnl`).
  - Closed positions: query `/v2/positions?status=CLOSED` instead of retired `/closed-positions`.
  - User PnL: use official `/v2/user-pnl` endpoint.
- [x] `tests/test_account_marks.py`, `tests/test_account_kpi.py`: updated mock fixtures and verified green.

## T2 — Data API v2 Migration (Trades Tape, Markout, Single-Buy Saver, Venue Fees) `[Backend/Logic]`
- [x] `core_brain/markets.py`:
  - `TRADES_API`: update to `https://data-api.polymarket.com/v2/trades`.
  - Replace `market` with `condition` query param.
  - Replace `offset` arithmetic with cursor pagination (`cursor`).
  - Unpack `data` key; parse `transaction_hash` and `token_id` (snake_case).
- [x] `core_brain/markout.py`:
  - Update trades tape fetcher to `/v2/trades` with `condition` parameter and cursor pagination.
- [x] `core_brain/single_buy_saver.py`:
  - Update `fetch_positions` and `fetch_token_balances` to `/v2/positions` with cursor pagination.
- [x] `core_brain/venue_fees.py`:
  - Update `/activity` to `/v2/activity` with cursor pagination and `usdc_size`.
- [x] Targeted tests for markets, markout, single_buy_saver, and venue_fees verified green.

## T3 — Protocol V2 Market Ingestion & Resolution `[Backend/Logic]`
- [x] `core_brain/markets.py` (`_parse_market_row`) & `core_brain/pair_scanner.py`:
  - Check `market.get("version")`:
    - `"v2"`: extract outcome IDs from `positionIds` (array of decimal strings).
    - `"v1"`: extract from `clobTokenIds`.
- [x] `core_brain/market_resolution.py`:
  - Check `resolutionStatus` (`resolved`) for V2 markets alongside `umaResolutionStatus`.
- [x] Targeted tests in `tests/test_uma_resolution_gate.py`, `tests/test_market_resolution.py` verified green.

## T4 — Protocol V2 Order Signing, Allowances & Router Merge `[Backend/Contracts]`
- [x] Protocol V2 constants:
  - ExchangeV3 address: `0xe3333700cA9d93003F00f0F71f8515005F6c00Aa` (domain version `"3"`).
  - Router address: `0x12121212006e4CD160D18e3f00711DA5c3372600`.
  - PositionManager address: `0x006F54F7f9A22e0000CC2AB60031000000ae9fEF`.
  - Router ABI for `merge(bytes31 conditionId, uint256 amount)` (selector `0x5b63685e`).
- [x] `core_brain/merge_pairs.py`:
  - Add `encode_v2_merge` and route V2 markets to `ROUTER_V2_CONTRACT`.
- [x] `core_brain/order_manager.py`:
  - Support `version="v2"` in `merge(...)`.
- [x] Targeted checks in `tests/test_order_manager_merge.py` verified green.
