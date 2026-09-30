# Run 08: what the aged-out rescue rehearsal proves — 2026-09-30

[Issue #311](https://github.com/AI-Degen-69/spread-hunter/issues/311) asked for
one run with 20–30 rescue closes so the #315 fix could be closed on organic
evidence. Runs 05/06/07 each ended with "no events": 07 logged 478 attempts,
0 catches, 0 closes. This run documents what 08 measured, why the 20–30 target
is not reachable in a 240-minute box, and what the evidence for closing #311
actually is.

**Result in one line: the fix fires end to end and zero aged-out settlements
are booked on every post-fix store — but the organic `aged_out_rescue` event
is a tail-of-a-tail occurrence whose natural rate (the fleet's measured 1–13
fills/day, 0–6 rescue exits/day) cannot populate a 4-hour rehearsal. The
20–30 target in #311 was written from a fill rate the market does not pay.**

---

## What ran

| | |
| --- | --- |
| Run | `shadow-08`, 240 minutes, interval 5 s |
| Store | `data/08_shadow_30-09.db` (fresh, own run id, own heartbeat/ring) |
| Book tape | `data/08_booktape_30-09.db`, same window |
| Dashboard | `http://127.0.0.1:8808` |
| Started | 2026-09-30 08:15 (after the #315/#316/#317/#318 build of 05:14) |
| Ends | ~12:15 the same day |
| Build | `8e96097` — includes `aged_out_verdict` + `rescue_aged_out_legs` (#315) |

## The picture

| measure | 05 (29-09, 240 m complete) | 07 (30-09, 240 m complete) | 08 (30-09, this run) |
| --- | --- | --- | --- |
| orders placed | 1,699 | 478 | 440+ |
| fills | **0** | **0** | **0** |
| rescue closes | 0 | 0 | 0 |
| aged-out settlements | **0** | **0** | **0** |

Fill-model context: the missions rest behind venue queue (`shadow_queue`
records 757–5,883 shares ahead at post time; one extreme read 187k), and the
fill model credits only tape-confirmed volume after that queue
(`core_brain/shadow_fills.py:credit_fills`). Zero fills means the model
correctly refused to credit a queue the tape never reached.

## Why zero fills is the market, not a bug

Checked directly against Gamma (public GET, same read the loop uses):

| market in the feed | gameStartTime (UTC) | endDate | venue state at 08:55 |
| --- | --- | --- | --- |
| atp-struff-tirante-2026-09-29 | 2026-09-30 05:45 | 2026-10-07 | open, accepting |
| atp-etcheve-tsitsip-2026-09-29 | 2026-09-30 05:30 | 2026-10-07 | open, accepting |
| wta-snigur-kawa-2026-09-30 | 2026-09-30 04:45 | 2026-10-07 | open, accepting |

The date in a tennis slug is the **tournament-day label, not the match day** —
these matches went live the morning of the run. The universe was healthy; the
same-window funnel (07:40) showed 124 scored with the usual audit trail. What
the box lacked is throughput: the fleet's own ledger rate is 1–13 fills/day
(shadow-01, the only store with organic volume), and 0–6 rescue exits/day. A
240-minute window therefore yields 0–2 fills and, past the 900 s discovery
window, effectively zero organic `aged_out_rescue` events — exactly what 05,
07 and 08 each measured.

## The endDate-as-tournament-end consequence (design note)

For tennis, Gamma's `endDate` is the **tournament's** end (a week out), so
`aged_out_verdict`'s lead window (`aged_out_rescue_lead_sec=900`) almost never
opens on a tennis leg. A tennis leg that ages out walks the ladder honestly:
`venue_closed` at match end, settlement books it — which is the designed,
fail-closed behaviour, and is exactly what #315's ladder intends. The markets
where `aged_out_rescue` fires organically are ones whose stated end ≈ the
event's end — the crypto 5m/15m series (the #312 kickoff case). If organic
firing demonstrations are ever wanted at scale, that is the universe to run
them on; see "What would actually produce 20–30 closes" below.

## Evidence bundle for #311

| acceptance criterion (#311) | evidence | store |
| --- | --- | --- |
| A leg older than the window is closed before market end | End-to-end firing recorded: 10 shares held 7,209 s, sold at 0.69 before market end, `reason=aged_out_rescue`, `classifier=aged_out_rescue` in the forensics report | `07_shadow_29-09.db` — **injected by the #315 verification probe** (`pair=pair-probe-exit`); not organically caught, labelled honestly here |
| Unreadable end time leaves the leg naked, never a guessed deadline | `aged_out_verdict` fail-closed ladder (`end_unknown` → retry), shipped and tested in #315; zero blind closes in every post-fix store | all |
| In-window legs keep the route order | Post-fix organic exits carry written in-window reasons (`grace_expired` 08:42 on shadow-01; `grace_expired` 00:27 on yesterday's fleet) — the #306 reason instrumentation flowing on the fixed build | `01_shadow_12-09_00-58.db`, `07_shadow_29-09.db` |
| Aged-out flag goes to zero on a fresh rehearsal | `rescue_exit_report.py`: `aged-out settlements: 0` on 05, 07, 08 (08's final read collected automatically at run end into `reports/311_08_final_evidence.txt`) | all three |
| No unmanaged single buy reaches settlement unseen | Since the fixed build went live (05:30), shadow-01 (the only store producing organic events) booked **0** `shadow_settlement` closes | `01_shadow_12-09_00-58.db` |

The honest gap: criterion 1's single organic-class demonstration is synthetic.
No amount of additional 240-minute shadow time fixes that — the event is rare
by construction whenever the in-window shields work, which is the desired
steady state. The mechanism is proven at the code level (focused suites) and
at the integration level (the probe), and the class it guards shows zero
members across every post-fix store.

## What would actually produce 20–30 rescue closes

A rehearsal on the crypto 5m/15m series, where `endDate` ≈ event end and the
lead window opens every cycle a leg survives past 900 s — the event rate there
is bounded by fills (dozens/day) rather than by rare aged-out tails. That is a
universe decision (`docs/agents/strategy.md`), not a bug fix, and it is the
only design that meets the original 20–30 bar without falsifying anything.

## Final numbers (collected at run end ~12:15)

See `reports/311_08_final_evidence.txt` (written by the read-only collector
`runtime/watch_311_collect.py` once the run's heartbeat reports `finished`).
