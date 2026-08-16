# Frontier Quant & Strategy Research Questions — Spread Hunter

These questions address the core theoretical and execution-level mechanics of the Spread Hunter maker strategy on Polymarket.

Verdicts were assigned in Session 68 (2026-08-16) against `run/fleet.db`, and the deciding number is stated with each. Note the finding that reframes all four: **100% of realised profit comes from `merge` (+$926.85); the `sell` path has earned $0.00** across 376 closes. Questions about spread capture describe the path that does not earn.

---

### Question 1: Optimal Micro-Price Skew vs. Instant Post-Fill Hedging
> **Verdict:** LIVE — deciding number: +$0.0374/pair realised on 26,777 merged pairs versus −$2.67/event on 16 naked exits `[MEASURED]`.
>
> **Context:** In a binary prediction market $[0, 1]$ where taker fees are high ($1.75¢$ at mid) and maker fees are zero ($0.00$), a maker resting bids on both sides risks one-sided fills from informed flow.
>
> **Question:** Is it mathematically superior to quote **asymmetrically in real-time** based on inventory (skewing the resting price dynamically so the heavy leg is unfillable and the light leg is aggressively priced), OR to quote **neutrally on both sides** and execute an immediate taker cross/exit upon a single-leg fill (the pairs-only rule)? What is the optimal inventory threshold at which the maker should stop quoting the heavy side entirely?

---

### Question 2: Sim-to-Real Queue Decay & Adverse Selection Transfer Function
> **Verdict:** LIVE — deciding number: conditional on a >50% queue collapse the sim credits a fill at 0.3352% versus a 0.0836% baseline, a **4.01× surge** across 21,477 collapse ticks `[MEASURED]`. The median is not what keeps this open: median depth change over $\tau_{\text{post}}$ is 0.00 shares, so a median-based haircut would read as zero. The tail is the whole question, and it is unresolved.
>
> **Context:** Polymarket operates an off-chain central limit order book (CLOB) with WebSocket order flow and variable network/processing latency. In paper simulation, queue priority is estimated from observed trade tape prints and book depth.
>
> **Question:** What empirical transfer function, latency penalty, or haircut should be applied to queue-based paper fills to accurately account for:
> 1. Real-world cancellation race conditions when adverse news arrives?
> 2. FIFO queue position drops during order amendments?
> 3. Co-located / low-latency takers front-running retail cancellations?

---

### Question 3: Dynamic Hazard Rate & Inventory Penalty as $t \to T$ (Time-to-Resolution)
> **Verdict:** LIVE — deciding number: the 6-hour markout horizon has $N=0$ because no market in this universe survives that long `[MEASURED]`. Sports and esports contracts resolve inside 2–4 hours, so the terminal boundary is hours away, not months.
>
> **Context:** Unlike perpetual futures or equity equities where inventory can mean-revert indefinitely, binary options have a hard terminal boundary at $T$ where the asset jumps to exactly $\$1.00$ or $\$0.00$.
>
> **Question:** How should the inventory risk aversion parameter ($\gamma$), maximum holding horizon, and book-gating thresholds scale as time-to-resolution decays ($t \to T$)? Specifically, how should the model transition from continuous market making (Avellaneda-Stoikov / Guéant) to jump-diffusion jump-risk mitigation in the final 24 hours before market expiration?

---

### Question 4: Optimal Signal Fusion for Toxic Flow Detection
> **Verdict:** PARKED — deciding number: **zero sub-second book gaps across 762,911 samples**; the minimum observed inter-update interval is 1,194.5 ms `[MEASURED]`. A sub-50 ms circuit breaker cannot act on a book that cannot change faster than 1.19 s. PARKED rather than DEAD because the constraint is a property of this venue's sports and esports books, not of the strategy: the BTC 5-minute series updates at 630/s, so the question returns intact the moment a fast series enters the traded universe.
>
> **Context:** Adverse selection in binary markets often stems from informed flow reacting to external spot market moves (e.g., Binance BTC/ETH spot or fast sports data feeds).
>
> **Question:** What is the optimal architecture to fuse:
> 1. Order Flow Imbalance (OFI) on Polymarket,
> 2. Lead-lag cross-venue price momentum (Binance WebSocket feeds are commonly described as leading Polymarket by 100–300 ms — **this figure is an ESTIMATE carried from external commentary; it has not been measured on this venue pair, and no measurement is scheduled because the verdict above makes it moot**), and
> 3. Microstructure book-skew velocity,
>
> into a sub-50ms "Cancel-All" circuit breaker that pulls resting bids before toxic fills can execute?
