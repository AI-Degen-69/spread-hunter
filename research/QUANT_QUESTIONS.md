# Frontier Quant & Strategy Research Questions — Spread Hunter

These questions address the core theoretical and execution-level mechanics of the Spread Hunter maker strategy on Polymarket.

---

### Question 1: Optimal Micro-Price Skew vs. Instant Post-Fill Hedging
> **Context:** In a binary prediction market $[0, 1]$ where taker fees are high ($1.75¢$ at mid) and maker fees are zero ($0.00$), a maker resting bids on both sides risks one-sided fills from informed flow.
>
> **Question:** Is it mathematically superior to quote **asymmetrically in real-time** based on inventory (skewing the resting price dynamically so the heavy leg is unfillable and the light leg is aggressively priced), OR to quote **neutrally on both sides** and execute an immediate taker cross/exit upon a single-leg fill (the pairs-only rule)? What is the optimal inventory threshold at which the maker should stop quoting the heavy side entirely?

---

### Question 2: Sim-to-Real Queue Decay & Adverse Selection Transfer Function
> **Context:** Polymarket operates an off-chain central limit order book (CLOB) with WebSocket order flow and variable network/processing latency. In paper simulation, queue priority is estimated from observed trade tape prints and book depth.
>
> **Question:** What empirical transfer function, latency penalty, or haircut should be applied to queue-based paper fills to accurately account for:
> 1. Real-world cancellation race conditions when adverse news arrives?
> 2. FIFO queue position drops during order amendments?
> 3. Co-located / low-latency takers front-running retail cancellations?

---

### Question 3: Dynamic Hazard Rate & Inventory Penalty as $t \to T$ (Time-to-Resolution)
> **Context:** Unlike perpetual futures or equity equities where inventory can mean-revert indefinitely, binary options have a hard terminal boundary at $T$ where the asset jumps to exactly $\$1.00$ or $\$0.00$.
>
> **Question:** How should the inventory risk aversion parameter ($\gamma$), maximum holding horizon, and book-gating thresholds scale as time-to-resolution decays ($t \to T$)? Specifically, how should the model transition from continuous market making (Avellaneda-Stoikov / Guéant) to jump-diffusion jump-risk mitigation in the final 24 hours before market expiration?

---

### Question 4: Optimal Signal Fusion for Toxic Flow Detection
> **Context:** Adverse selection in binary markets often stems from informed flow reacting to external spot market moves (e.g., Binance BTC/ETH spot or fast sports data feeds).
>
> **Question:** What is the optimal architecture to fuse:
> 1. Order Flow Imbalance (OFI) on Polymarket,
> 2. Lead-lag cross-venue price momentum (e.g., Binance WebSocket feeds leading Polymarket by 100–300ms), and
> 3. Microstructure book-skew velocity,
>
> into a sub-50ms "Cancel-All" circuit breaker that pulls resting bids before toxic fills can execute?
