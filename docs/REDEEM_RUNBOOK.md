# Go-Live Runbook: Gasless Position Redemption via Polymarket Relayer

Operational guide for executing gasless redemptions of resolved conditional tokens held in the Polymarket Deposit Wallet.

---

## 1. Preconditions

The following environment variables must be present in `.env` (or exported in the execution environment):

| Variable | Description | Example / Required Value |
|---|---|---|
| `POLY_PRIVATE_KEY` | Hex private key of the signing EOA | `0x...` |
| `POLY_FUNDER` | Polymarket Deposit Wallet address holding conditional tokens | `0xBa7c21Ac8968983e90BEcB989fe978889FEC266b` |
| `RELAYER_API_KEY` | Polymarket Builder Relayer API Key | `<api_key_secret>` |
| `RELAYER_API_KEY_ADDRESS` | Address associated with the Relayer API Key | `0xD2C7F5514580184d32C70F6FEA95B69C5Cd72fa0` |
| `RELAYER_URL` | Polymarket Relayer endpoint URL | `https://relayer-v2.polymarket.com` |
| `POLYGON_RPC` | *(Optional)* Custom Polygon RPC endpoint override | e.g. `https://polygon-bor-rpc.publicnode.com` |

### Critical Invariant
`RELAYER_API_KEY_ADDRESS` **must exactly match** the Ethereum address derived from `POLY_PRIVATE_KEY`.
The Relayer validates that the API key header corresponds to the address signing the EIP-712 transaction.

---

## 2. Step 1: Dry Run

Run redemption in dry-run mode by providing the target market's `condition_id`:

```bash
python -m strategy.live_exec redeem <condition_id>
```

Example command:
```bash
python -m strategy.live_exec redeem 0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f
```

### Output Interpretation

| Field | Meaning | Action / Interpretation |
|---|---|---|
| `action` | Target action | Must display `REDEEM (gasless via Polymarket Relayer)`. |
| `target_ctf` | Contract receiving call | Must be ConditionalTokens: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`. |
| `safe_funder` | Deposit Wallet | Must match `POLY_FUNDER` (`0xBa7c21Ac8968983e90BEcB989fe978889FEC266b`). |
| `signer_eoa` | Signing EOA | Must match `RELAYER_API_KEY_ADDRESS` (`0xD2C7F5514580184d32C70F6FEA95B69C5Cd72fa0`). |
| `condition_id` | Target market condition | Must match the target market condition hash. |
| `resolved` | Resolution status | **`yes`**: Market is resolved on-chain (`payoutDenominator > 0`). Proceed to inspect payload preview.<br>**`no`**: Market is not resolved yet (`payoutDenominator == 0`). Do not submit live; wait for oracle settlement.<br>**`unknown (RPC unreachable)`**: All Polygon RPCs timed out or failed. Check network connectivity or specify `POLYGON_RPC`. |
| `collateral` | Collateral token | Bridged USDC.e: `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`. |
| `index_sets` | Outcome collection index | Default `[1, 2]` covers binary Yes and No outcome slots. |
| `encoded_call` | ABI-encoded calldata | Begins `0x01b7037c...` with length 458 hex characters. |

---

## 3. Step 2: Inspect Payload Preview

In dry-run mode, inspect the generated `submit_payload_preview` JSON structure. Verify all checklist items below:

```json
{
  "type": "WALLET",
  "from": "0xD2C7F5514580184d32C70F6FEA95B69C5Cd72fa0",
  "to": "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07",
  "nonce": "0",
  "signature": "0x0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
  "depositWalletParams": {
    "depositWallet": "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b",
    "deadline": "1771234567",
    "calls": [
      {
        "target": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
        "value": "0",
        "data": "0x01b7037c..."
      }
    ]
  }
}
```

### Pre-Authorization Checklist

1. **`from` Address**: Must be our signing EOA (`0xD2C7F5514580184d32C70F6FEA95B69C5Cd72fa0`). Must never be any address returned by the relayer parameter endpoint.
2. **`to` Address**: Must be DepositWalletFactory (`0x00000000000Fb5C9ADea0298D729A0CB3823Cc07`).
3. **`depositWallet`**: Must be our Deposit Wallet address (`0xBa7c21Ac8968983e90BEcB989fe978889FEC266b`).
4. **`value`**: Must be string `"0"`.
5. **`data`**: Must be 458 characters in length and start with `0x01b7037c` (`redeemPositions` method selector).
6. **Wire Types**: `nonce`, `deadline`, and `value` must be string literals in the JSON payload.
7. **`metadata` field**: Must be completely absent from the payload.
8. *Note on preview placeholders*: In dry-run preview, `nonce` is displayed as `"0"` and `signature` as 65 zero bytes (`0x` followed by 130 hex zeros, 132 chars total). These are populated dynamically with live relayer nonce and ECDSA signature during live submission.

---

## 4. Step 3: Live Submission

When the dry-run output and payload preview are verified, execute the live redemption:

```bash
python -m strategy.live_exec redeem <condition_id> --live
```

### Execution Flow
1. Verifies on-chain resolution via Polygon RPC failover pool (`payoutDenominator > 0`).
2. Fetches next valid user transaction nonce from Relayer (`GET /v1/account/transactions/params?address={signer}&type=WALLET`).
3. Constructs EIP-712 `DepositWalletBatch` typed data structure with current epoch deadline (+600s, `REDEEM_DEADLINE_SECONDS = 600`).
4. Signs EIP-712 typed data with `POLY_PRIVATE_KEY`.
5. Submits signed transaction payload to Relayer endpoint `POST /submit` (Note: `/v1/submit` returns 404 and must not be used).
6. Prints Relayer transaction hash and initial status.

> **Nonce Behavior:** A rejected or invalid submission does **not** consume a transaction nonce on the relayer. The nonce counter advances only upon successful transaction acceptance.

---

## 5. Post-Submission Verification

1. **Polygonscan Transaction Confirmation:**
   Lookup the returned transaction hash:
   `https://polygonscan.com/tx/<transactionHash>`
   Verify that `redeemPositions` executed successfully on ConditionalTokens (`0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`) and collateral was credited to `POLY_FUNDER`.

2. **Verify Nonce Increment:**
   Query the **signing EOA** — not the deposit wallet. The deposit wallet's WALLET-type nonce is always `0`; the counter that advances belongs to the address derived from `POLY_PRIVATE_KEY`.

   ```bash
   python -c "import os,urllib.request,json; u='https://relayer-v2.polymarket.com/v1/account/transactions/params?address='+os.environ['RELAYER_API_KEY_ADDRESS']+'&type=WALLET'; r=urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0','RELAYER_API_KEY':os.environ['RELAYER_API_KEY'],'RELAYER_API_KEY_ADDRESS':os.environ['RELAYER_API_KEY_ADDRESS']}); print(urllib.request.urlopen(r).read().decode())"
   ```

   Expect the `nonce` field, a **string**, to read one higher than before submission. The `address` field in the response is a rotating relayer worker and carries no meaning here — ignore it.

   *(Reference: this counter was observed at `121` before the reference redemption and `122` after).*

3. **Verify Balance:**
   Check updated USDC.e balance:
   ```bash
   python -m strategy.live_exec balance
   ```

---

## 6. Failure Modes & Troubleshooting

| Abort Message / Error | Root Cause | Resolution |
|---|---|---|
| `Condition <id> is not resolved yet (payoutDenominator == 0).` | The market has not been resolved by the oracle on-chain. | **Hard abort.** Do not force redemption. Wait until market resolution is finalized on Polymarket. |
| `Cannot determine resolution status for <id>: all RPC endpoints failed. The market may well be resolved. Retry, or pass --skip-resolution-check to bypass.` | All configured Polygon RPC endpoints timed out or failed to respond. | Verify internet connection or specify a working endpoint via `POLYGON_RPC`. If resolution is verified externally on Polygonscan, pass `--skip-resolution-check` to bypass the RPC guard. *(Note: `--skip-resolution-check` will still hard-abort if any RPC reports `payoutDenominator == 0`).* |
| `Failed to fetch nonce from relayer: <exc>` | Network error or invalid API key / authentication header when calling `/v1/account/transactions/params`. | Check `RELAYER_API_KEY`, `RELAYER_API_KEY_ADDRESS`, and `RELAYER_URL` in `.env`. Ensure API key is active. |
| `eth_call to CTF contract 0x4D97... returned empty data. The contract address may be wrong or the RPC may be on the wrong chain.` | The RPC answered `0x` to `eth_call`, indicating the contract address does not exist on that chain or reverted immediately. | Check RPC URL and chain ID (must be Polygon Mainnet, chain ID 137). Verify `CTF_CONTRACT` address. |
| `RELAYER_API_KEY and RELAYER_API_KEY_ADDRESS must be set in .env for gasless live redemption.` | Missing relayer credentials in execution environment. | Export `RELAYER_API_KEY` and `RELAYER_API_KEY_ADDRESS` in `.env`. |

---

## 7. Provenance & Reference Appendix

- **Live Reference Transaction:** `0x66bc709b1a1d515d813e9d191a84b8863d8f2a251e1698a85d452152c7602135` (Block 92098496 on Polygon). Confirms `DepositWalletFactory.executeBatch` execution pattern for gasless redemptions.
- **Relayer Client Specification:** Schema matches `@polymarket/builder-relayer-client@0.0.10` `dist/types.d.ts:147-154` (`DepositWalletBatchRequest`).
- **Relayer Worker Pool Separation:** The `address` field returned in the `/v1/account/transactions/params` response is a rotating internal relayer pool worker (e.g. `0x6987f531981c95fc998ab20c0935154e9f509a87`). It is **never** our user account and must never be used in transaction signing or payload construction.
