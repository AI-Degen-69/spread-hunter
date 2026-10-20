# 646 — noticed but not touching (Station III)

#646 builds one guarded reset for live merges, shadow merges, and stop exits,
plus the filter re-qualification gate. Anything below is outside that contract
and was left exactly as found.

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | Tiered REST poller and its exposure measurement (#621, closed as superseded by the WebSocket pivot). | The issue names it out of scope; the reset is a lifecycle safety requirement, not a polling feature. | you | with #621's successor | open |
| N2 | Second-leg repricing after a partial fill or partial exit. | The issue names it out of scope; the remainder is exited at the stop, never repriced. | you | future ticket | open |
| N3 | Stop-slippage measurement on hard-stop exits. | The issue names it out of scope. | you | future ticket | open |
| N4 | The #621 tier list itself (including reading `requalification_marks()` exclusion records into it). | Explicitly deferred in the issue and the plan: this ticket documents `requalification_marks()` as the contract but builds no tier list. If #621 lands first, it connects marked/excluded conditions itself. | you | with #621's successor | open |
| N5 | Partial exits on passes with no venue view (e.g. `risk_guardian` without a funder) keep the old close-timestamp skip. | Discovered mid-build (Station III): without a venue position the exit would size off fills the close already retired (#326 netting needs the venue gap). Managing those safely needs exit-sizing work, which is its own change. Passes with a view (poll with funder, shadow with `shadow_positions`) stay managed. | you | future ticket | open |
