## Why

Three settings ended in `_CENTS` and did not all hold cents. `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=6`
meant six cents, while `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.10` meant ten cents. The bounds
enforced the difference correctly — the footgun was the name, and an analyst reading the suffix
alone was off by a factor of one hundred. Two of the three names now state the unit they actually
hold, and the two that genuinely hold whole cents now refuse fractions instead of truncating them.

The adverse-drop stop stays in dollars on purpose. The live champion runs it at 4.5¢
(`BOT_RISK_STOP_ADVERSE_DROP_USD=0.045` in `tests/test_champion_lane_parity.py`, `docs/live-lane-audit.md`
and the cockpit), and 4.5¢ has no integer-cent form. `stop_trigger` rounds the trigger up to a whole
cent, which makes "just use 4 or 5" look equivalent — it is not, once an entry average carries a
sub-cent fraction from partial fills at several prices:

```
entry   drop0.045  drop0.04  drop0.05
0.5     0.46       0.46       0.45
0.512   0.47       0.48       0.47     <- 0.04 diverges by a cent
0.53    0.49       0.49       0.48
0.47    0.43       0.43       0.42
0.618   0.58       0.58       0.57
0.33    0.29       0.29       0.28
```

Converting that key to integer cents would move a live stop, which the issue rules out in its own
Out-of-scope line ("Any runtime behavior change — the engine must keep applying the same numeric
values"). So #872 delivers the stated goal — a name that never lies about its unit — without moving
a single number. Seven more `_cents` fields still hold dollars; they are inventoried in
`docs/backlog/i872-currency-unit-inventory.md` with their real units, because four of them hold
sub-cent values and each needs its own decision rather than a sweep.

**Deprecation window.** The retired spellings still load, with one `DEPRECATED` warning per key per
process, because silently ignoring `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.10` would run the shipped
3¢ band while the operator reads 10¢ in their own `.env`.

Nothing here risks a pair over $1.00 or a single unmatched buy: no quoting, fill, merge or
settlement arithmetic changes, and no live venue command was run.

## Test output

```
$ python -m pytest -q tests/test_config.py tests/test_config_single_source.py tests/test_config_divergence_guard.py \
    tests/test_stop_loss_config.py tests/test_single_leg_lifecycle.py tests/test_dual_stop_loss.py \
    tests/test_risk_guardian.py tests/test_single_buy_saver.py tests/test_dynamic_offset.py \
    tests/test_completable_pair_gate.py tests/test_aged_out_rescue.py tests/test_champion_lane_parity.py \
    tests/test_shadow_tournament.py tests/test_tournament_cockpit_arms.py tests/test_tournament_arms_i843.py \
    tests/test_dashboard_server.py tests/test_trader_loop.py
1097 passed in 77.47s (0:01:17)
```

GitHub CI runs the full `python -m pytest -q` regression suite on Ubuntu and Windows.

## How to verify

Everything below is read-only. No orders are placed and the dashboard START button is never pressed.

**1. The defaults read clean.** In a shell with no `BOT_*` variables set:

```powershell
python -c "from core_brain.config import load; c=load(); print(c.bot_order_requote_dead_band_usd, c.bot_risk_stop_adverse_drop_usd, c.bot_quote_spread_offset_min_cents, c.bot_quote_spread_offset_max_cents)"
```

Expect exactly `0.03 0.05 1 20` and no `DEPRECATED` line. A failed check prints a `ValueError`
or a `DEPRECATED` warning on a default run.

**2. Your old `.env` line still works, and says so.**

```powershell
$env:BOT_RISK_STOP_ADVERSE_DROP_CENTS="0.045"; python -c "from core_brain.config import load; print(load().bot_risk_stop_adverse_drop_usd)"; Remove-Item Env:BOT_RISK_STOP_ADVERSE_DROP_CENTS
```

Expect `0.045` plus one `DEPRECATED: ... is retired; read as BOT_RISK_STOP_ADVERSE_DROP_USD=0.045 ...`
line. Then run the same command with `BOT_RISK_STOP_ADVERSE_DROP_USD` instead: same `0.045`, no
warning. A retired name that loads with no warning is the failure this blocks.

**3. A fraction under a cents key is refused, not quietly rounded.**

```powershell
$env:BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS="19.9"; python -c "from core_brain.config import load; load()"; Remove-Item Env:BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS
```

Expect `ValueError: BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS='19.9' must be whole cents, for example 6
means 6 cents`. Before this change the same line silently ran 19.

**4. The dashboard shows the same money it always did.** Start `python -m dashboard.server`, open
`http://127.0.0.1:8799`, and read the parameters panel in **Protection & Lifecycle**: Requote Dead
Band still reads `$0.03` and the adverse-drop stop card still reads `$0.05`, with the dynamic offset
ceiling at `20¢`. Do not press START. A failed check reads `$0.00` (the field name moved and a
reader missed it) or shows a card whose env badge is missing.

**5. The tournament cockpit still advertises the champion.** Open the cockpit page in the same
stack and read the champion snippet: it now says `BOT_RISK_STOP_ADVERSE_DROP_USD=0.045` and still
describes a 4.5¢ stop. If it showed 4¢ or 5¢, the stop distance moved and this change is wrong.

Closes #872
