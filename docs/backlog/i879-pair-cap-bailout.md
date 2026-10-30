# #879 pair-cap bail-out entry lockout — out-of-scope notes

Status: resolved
Resolved-by: PR #880 (squash-merged as `cf71630a`)
Owner: agent
When: this round

The shipped change closes a bailed-out market to fresh entries by writing the
mark a hard stop already writes: `PAIR_CAP_BAILOUT_BLOCK_REASON =
"hard_stop_pair_cap_bailout"`, written only when the exit actually sold the leg
(`bailout["action"] == "exited"`). The `hard_stop` prefix is what gives it the
#796 lockout floor, the timed release and the publication gate without a second
cooldown sitting next to them.

Verified on `main`, not just on the branch: `core_brain/single_buy_saver.py`
carries `_block_fresh_entries_after_cap_bailout`, and
`tests/test_single_buy_saver.py` covers the four behaviours below.

## 1. One lockout window, two exits

Owner: operator, informational. When: tuning `bot_risk_hard_stop_entry_lockout_sec`.

A cap bail-out and a circuit stop now share the same 900 s window. Moving that
number moves both exits together. If a run ever needs the cap bail-out to hold a
market longer or shorter than a stop does, a dedicated knob is the change — do
not fork the constant in code.

## 2. The block is per market, not per pair

Owner: agent, only if a run shows a real conflict. When: the first run where a
legitimate second pair wants the same market inside the window.

`mark_hard_stop_block` keys on `condition_id`, so for the next 15 minutes a
*new* pair on that market is refused too. Surviving managed rows keep their
hedges and merges — `_enforce_entry_gate` drops fresh submits only. That is the
intent (the market, not the leg, is what walked away), but it is a behaviour
change for a market that also has a healthy pair on it.

## 3. A failed block write reopens the market

Owner: agent, if it ever fires in a live run. When: a real `cap-bailout entry
block failed` warning in the logs.

The write is best-effort by design: the exit already booked, and a raising block
must not turn a booked close into an error the caller retries. There is no
in-memory fallback, so a repository write failure means the market is quotable
again on the next rotation — exactly the #879 shape.

## 4. No dashboard surface names this exit or its block

Owner: agent. When: a UI issue that shows per-pair lifecycle reasons.

Same gap recorded under #797 item 4: `route=pair_cap_bailout` and
`reason=pair_cap_hedge_unreachable` are persisted, but no panel enumerates
single-leg exit routes or shows a market as `awaiting_requalification` because
of a bail-out. The block is observable only through the lifecycle row.
