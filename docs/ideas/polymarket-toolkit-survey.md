# Survey: `runesleo/polymarket-toolkit` (issue #72)

**Date:** 2026-08-31 · **Source:** `github.com/runesleo/polymarket-toolkit` (README, `docs/`,
`skills/`, `docs/templates/`, `docs/mcp.md`, `docs/executor.md`) · **Verdicts:** adopt / adapt / skip

Their repo is a **read-only research toolkit** for Polymarket: a Node 22+ CLI (`bin/pm`), a
zero-dependency TypeScript library, three AI skills, an MCP server exposing the CLI, and an
opt-in `executor/` sub-package that is the only thing able to place an order. We are a Python
execution engine. Nothing here is importable and nothing should be vendored — what travels is
**method**, and only where it answers a question our own code currently cannot.

The four findings worth acting on are at the bottom, with draft issue bodies. Everything else
is inventoried so the next person does not re-read their repo to find that out.

---

## The one finding that is not a nice-to-have

Their `docs/v2-ctf-ops-faq.md` lists six ways merge stops working after the V2 upgrade. Number
two is contract routing: **negRisk markets must merge through `NegRiskAdapter.mergePositions`;
standard markets call the CTF directly.** The wrong adapter reverts.

Our merge path targets the CTF contract unconditionally. The contract is chosen where the
batch call is built, never by the calldata encoder:

- [core_brain/merge_pairs.py:24](../../core_brain/merge_pairs.py) — `CTF_CONTRACT` is a module
  constant, and `build_redeem_typed_data` at
  [merge_pairs.py:169](../../core_brain/merge_pairs.py) hardcodes it as the batch call `target`.
  `encode_merge_positions` builds calldata only and picks no contract.
- [core_brain/order_manager.py:729](../../core_brain/order_manager.py),
  [:765](../../core_brain/order_manager.py), [:792](../../core_brain/order_manager.py) — every
  merge/redeem call site passes the same constant.
- `neg_risk` is carried on `LiveMarket` and *printed* — [order_manager.py:451](../../core_brain/order_manager.py)
  and [:2896](../../core_brain/order_manager.py) — and read nowhere else. A repo-wide grep for
  an adapter address returns nothing.

So a negRisk market that assembles a pair here has no working exit through merge. The pair
would sit until resolution, which is precisely the "assembled but cannot close" state the
strategy exists to avoid. This is the survey's one **adopt**-and-file-immediately item.

---

## Capability inventory

### CLI commands

| Their command | What it does | Verdict | Effort / risk | Our module |
| --- | --- | --- | --- | --- |
| `pm markout` | Execution quality: `(reference − fill) × direction`, in cents/share, using a **windowed VWAP** reference rather than the next print, **including passive fills**, and subtracting a **baseline** built from the same tokens over the same span minus this wallet | **adapt** | M / low | [core_brain/markout.py](../../core_brain/markout.py) |
| `pm fees` | Lifetime taker fees recovered from the residual in `usdcSize` (activity rows are not `size × price`); yields pre-fee → net conversion from one REST call | **adapt** | S / low | [core_brain/kpi.py:1536](../../core_brain/kpi.py) |
| `polymarket-pnl` skill | Audit-grade PnL by replaying BUY/SELL/REDEEM/MERGE/SPLIT/REBATE (+ optional REWARD/REFERRAL/CONVERSION) from the Data API; MAPE ~0.2% vs the official `/profit` endpoint | **adapt** | M / low | [core_brain/kpi.py](../../core_brain/kpi.py), [scripts/audit_settlement.py](../../scripts/audit_settlement.py) |
| `pm v2-check` | V2 CTF split/merge diagnostics: SDK/endpoint alignment, adapter routing, approvals, collateral recognition, environment drift | **adapt** | S / low | [core_brain/merge_pairs.py](../../core_brain/merge_pairs.py), [core_brain/order_manager.py](../../core_brain/order_manager.py) |
| `pm limits` | Prints the venue's documented rate-limit pacing (offline reference table) | **adapt** | S / low | [core_brain/venue.py](../../core_brain/venue.py) |
| `pm activity` | Paginates activity with an explicit ~4,000-row effective cap warning (beyond it, pages repeat) and a `pagination_incomplete` flag | **adapt** | S / low | any Data API reader, incl. [core_brain/account.py](../../core_brain/account.py) |
| `pm mix` | Maker/taker split, cross-checked against fees (takers are charged, makers are not) | **skip** | — | we know our own side from the registry |
| `pm profile` | Leaderboard PnL + open positions for an address | **skip** | — | that is our own wallet, already in the registry |
| `pm scan` / `pm markets` | Gamma market listing by volume and spread | **skip** | — | [scripts/filter_markets.py:190](../../scripts/filter_markets.py) already does this, with gates |
| `pm updown` | Crypto up/down event fields and resolution sources | **skip** | — | [scoring/markets.py](../../scoring/markets.py) resolves the 5-min series already |
| `pm lb` / `pm pnl-check` | Leaderboard snapshots and non-audit-grade PnL hints | **skip** | — | leaderboard rank is not a strategy input here |
| `pm brier` | Brier score over settled positions, entry price as forecast | **skip** | — | we are not forecasting; a merged pair has no directional view |
| `pm redeem` | Read-only redeem watchdog | **skip** | — | [core_brain/market_resolution.py](../../core_brain/market_resolution.py) sweeps resolutions and books settlement |

### Non-CLI surfaces

| Surface | What it is | Verdict | Why |
| --- | --- | --- | --- |
| `executor/` | Separate sub-package, own `package.json`. Four guards: a two-call façade (`postLimitOrder`, `cancelOrder`), **dry-run unless `EXECUTOR_LIVE=1`**, a **notional cap** (`EXECUTOR_MAX_USD`, default $10) **re-priced at post time**, and credential validation that fails closed naming the variable and never echoing values | **adapt** (one idea) | Our shadow guard and Dynamic Caps already cover three of four. The fourth — re-checking the cap **at post time**, against the price actually being posted — is worth confirming we do |
| `mcp/` | 12 read-only tools shelling out to `pm`; no keys, no orders, no local writes | **skip** | We have no agent-facing read surface to serve, and the dashboard already is one |
| `docs/templates/` | `backtest-report`, `handoff`, `live-gate-checklist`, `paper-checklist`, `platform-change-runbook` | **adapt** (two of five) | `platform-change-runbook` and `live-gate-checklist` map onto real gaps: we have no written procedure for a venue-side change, and no single pre-live checklist |
| `src/index.ts`, `examples/` | Zero-dependency TS helpers, numbered API examples | **skip** | Wrong language, and the endpoints are already wrapped here |
| `docs/crypto-updown-price-source.md`, `builder-attribution.md` | Resolution source for crypto up/down; builder attribution | **skip** | Neither touches how we quote or close |

---

## The four overlaps the issue asked to verify first

### 1. Markout methodology — **adapt**

**Theirs:** windowed VWAP reference, passive fills included, and a baseline of the same tokens
over the same span **minus this wallet**, so a market-wide drift is not read as our own adverse
selection. They name the limitation honestly: on short-dated markets, long windows leave most
fills unmeasured, so the mean describes surviving fills only.

**Ours:** [core_brain/markout.py](../../core_brain/markout.py) samples the **mid** at 300s, 1h,
6h and 15m after a fill and stores it in `markouts.mid_h0..mid_h3`
([order_registry.py:236](../../core_brain/order_registry.py)). No VWAP, no baseline subtraction.
Two consequences, both real:

- A market that drifted for reasons that have nothing to do with us reads as adverse selection.
  Our fills are overwhelmingly passive, so this is the common case, not the corner.
- The mid at exactly T+300s is one sample of a bouncing quantity; a windowed VWAP over
  [T+250s, T+350s] is the same measurement with less variance and no extra fetch pattern.

The baseline is the expensive half (it needs a market-wide tape for the same span). The VWAP
window is nearly free. Recommend both, in that order.

### 2. Audit-grade PnL as an independent cross-check — **adapt**

Nothing currently checks our KPI numbers against the venue. [kpi.py](../../core_brain/kpi.py)
computes everything from our own registry, so a systematic registry error is invisible by
construction — every number would agree with every other number and all of them would be wrong.

Their cashflow replay is the check: same wallet, entirely independent path (Data API activity),
reconciles to the official `/profit` endpoint at ~0.2% MAPE. Their structural insight is the
useful part — **official PnL is pre-fee, cashflow replay is post-fee, and the gap should be
`lifetime taker fees − maker rebates`**. That is a falsifiable statement about our own books.

Caveats they document and we would inherit: `MERGE`/`SPLIT` need `sortDirection=ASC` or rows go
missing silently; the Data API has a ~10k offset ceiling; negRisk `CONVERSION` rows can leave an
unexplained gap even at zero fees.

### 3. V2 CTF ops guidance vs our merge flow — **adopt**

Covered above. Beyond the adapter routing, their FAQ names two more checks worth having as a
diagnostic rather than as a surprise: `setApprovalForAll` for **both** adapter and exchange after
any wallet migration, and the collateral token the merge returns being one the bot recognises
(V2 may hand back wrapped pUSD).

### 4. Executor safety ladder vs Dynamic Caps — mostly **skip**, one **adapt**

Their ladder and ours line up better than expected:

| Their guard | Ours |
| --- | --- |
| dry-run unless `EXECUTOR_LIVE=1` | inverted here — LIVE is the default and `--no-live` is the dry run ([AGENTS.md](../../AGENTS.md)) — but the shadow path cannot sign at all, which is stronger |
| notional cap, default $10 | `max_order_usd` = 25% of live portfolio value, floored at the venue minimum ([config.py](../../core_brain/config.py) `derive_dynamic_caps`) |
| credential validation fails closed, echoes only the variable name | `.env` validation and the shadow guard's refusal to open the production registry |
| two-call façade | not applicable; our engine needs the full order lifecycle |

The one idea worth taking: they re-price the cap **at post time**, so a payload built earlier
cannot be posted under a stale price. Worth a confirming test rather than a redesign.

### 5. Fee accounting — **adapt**

[config.py:458](../../core_brain/config.py) carries `fee_rate = 0.07` **as a taker-completion
cost input** — the rebate-estimate rationale this line used to carry is obsolete, no rebate
income is estimated; `maker_fee` is 0.0 because `takerOnly=true` means our passive fills are not charged.
[kpi.py:1536](../../core_brain/kpi.py) sums `taker_fees_paid` from the `fee` column on close
rows — that is whatever we recorded, not what the venue charged. Their residual method
(`usdcSize` minus `size × price`) recovers the charged fee per fill from a read we can make
without a signer, which is exactly what a cross-check needs. Note their dating: fees exist only
since late June 2026 and stepped 0.03 → 0.05 → 0.07.

### 6. Rate-limit pacing — **adapt** (documentation only)

[core_brain/venue.py](../../core_brain/venue.py) handles the two things that actually bite (a
browser User-Agent, since the WAF 403s the SDK default, and one cached client per
funder/sig-type/host so a CLI run does not re-derive API keys). What we do not have is a written
table of the venue's documented limits, which is what `pm limits` is: an offline reference. Cheap
to write down, and it makes the next pacing decision an arithmetic problem instead of a guess.

---

## Ranked shortlist

1. **negRisk merge routing** — correctness, on the money path. A negRisk pair currently has no
   working merge exit.
2. **Independent PnL cross-check** — the only proposed change that can catch a systematic
   registry error, because it does not read the registry.
3. **Markout VWAP window + baseline** — makes the adverse-selection number mean what the
   dashboard already claims it means.
4. **Fee recovery from `usdcSize`** — turns `taker_fees_paid` from "what we wrote down" into
   "what the venue charged", and it is the reconciling term for item 2.

Below the line: the rate-limit reference table and the two operational templates
(`platform-change-runbook`, `live-gate-checklist`) — worth doing, no urgency.

---

## Draft follow-up issue bodies

### A. Route negRisk merges through the NegRisk adapter

> **Summary.** The merge and redeem submission path targets `CTF_CONTRACT` unconditionally —
> `build_redeem_typed_data` and the three call sites in `order_manager.py` that build the batch
> call. (`encode_merge_positions` only encodes calldata; it selects no contract.) Polymarket
> routes negRisk markets through a NegRisk adapter; calling the CTF directly reverts, so a
> negRisk pair assembled here has no merge exit.
>
> **Scope.** Read `neg_risk` (already on `LiveMarket`) where the batch call's `target` is
> chosen, and route to the adapter for negRisk markets, CTF otherwise. The calldata encoder
> stays as it is. Add the adapter address as a named constant beside
> `CTF_CONTRACT`, verify the approvals each path needs, and refuse — loudly — to attempt a merge
> whose routing cannot be determined, rather than sending a call that will revert.
> Out of scope: split, redemption of resolved markets, any change to when we merge.
>
> **Acceptance.** A negRisk market's merge builds against the adapter and a standard market's
> against the CTF, both covered by tests that fail if the constant is swapped; an unknown
> `neg_risk` value refuses instead of guessing.

### B. Cross-check registry PnL against a venue-side cashflow replay

> **Summary.** Every KPI number comes from our own registry, so a systematic registry error is
> invisible. Reconstruct PnL independently from the Data API activity feed and report the gap.
>
> **Scope.** A read-only script that replays BUY/SELL/REDEEM/MERGE/SPLIT/REBATE for our funder
> and compares against `kpi.report()`. `sortDirection=ASC` on MERGE/SPLIT (DESC silently drops
> rows), an explicit `pagination_incomplete` flag at the offset ceiling, and the expected gap
> stated as `lifetime taker fees − maker rebates` rather than as zero. Report only — no change
> to what the dashboard reads.
>
> **Acceptance.** The script prints registry PnL, replayed PnL, the gap, and whether the gap is
> explained by fees; a deliberately corrupted test registry produces a gap the script flags.

### C. Markout: windowed VWAP reference and a market baseline

> **Summary.** `markout.py` samples a single mid at each horizon, so quote bounce enters the
> measurement as noise and market-wide drift enters it as our adverse selection.
>
> **Scope.** Replace the point mid with a VWAP over a window around each horizon, then subtract
> a baseline built from the same tokens over the same span excluding our own fills. Keep the
> existing horizons and the existing "never block the loop, leave NULLs" contract. Store the
> baseline-corrected figure beside the raw one — never in place of it.
>
> **Acceptance.** A synthetic tape where the whole market drifts and our fills do not underperform
> it reports ~0 excess; the raw mid-based figure still reports the drift; both are stored.

### D. Recover charged taker fees from `usdcSize`

> **Summary.** `taker_fees_paid` sums a column we wrote ourselves. The venue's activity rows
> carry the charged fee implicitly: `usdcSize` is not `size × price`, and the residual is the fee.
>
> **Scope.** Recover per-fill fees from the residual, expose lifetime fees, and use them as the
> reconciling term in issue B. Note the fee history (from late June 2026, stepping
> 0.03 → 0.05 → 0.07) so a backfill over older activity does not assume today's rate.
>
> **Acceptance.** Recovered fees match a hand-computed fixture; a zero-fee (pre-June) row
> recovers 0.00 rather than a rounding artefact.

---

## What was deliberately not taken

- **Their code.** Wrong runtime (Node/TypeScript), and the issue rules out importing or
  vendoring it. Everything above is method.
- **Trader-profiling surfaces** (`profile`, `lb`, `brier`, `mix`). They answer "how good is this
  wallet at forecasting" — a question a merge-arbitrage engine with no directional view does not
  ask about itself, and cannot act on about anyone else.
- **Their market scanners.** `pm scan` / `pm markets` list what Gamma returns;
  [scripts/filter_markets.py](../../scripts/filter_markets.py) already reads the same endpoint
  and then applies volume, depth, spread, horizon, pre-start and movement gates on top.
- **The MCP server.** It exists to give an agent a read-only view of the CLI. Our equivalent is
  the dashboard, which reads the registry directly.
