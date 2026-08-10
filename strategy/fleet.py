"""One process, many markets.

Breadth beats depth here. Income is `rate * ours/(ours+theirs)` per market, so
piling capital into one book fights yourself over a fixed pot -- measured,
$3,000 into a single market returns ~1.0%/day while the same money spread over
20 markets returns ~5%/day, because each market is a separate pot with its own
competition.

Why one process rather than 20 copies of the old single-market bot: 20 markets x 2 books
polled every second is 40 requests/second against a public API. That gets
rate-limited, and the failure mode is silent -- the bots keep reporting uptime
while scoring nothing. Here markets are visited on a rotation with a fixed
request budget, so adding markets lengthens the sweep instead of raising the
request rate.

    python -m scripts.rank_markets --top 20    # choose markets first
    python -m strategy.fleet
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

from strategy import (gate, markout, resolve, rewards, store)
from strategy.allocate import (allocate_fundable, capital_scarcity, marginal,
                               shares_for, spread_capture_daily)
from strategy.config import load as load_cfg
from strategy.fills import QueueFillEngine
from strategy.net_config import load_net as load_bot_cfg
from strategy.quotes import Inventory
from strategy.sweep import (SweepContext, SweepOutcome,
                            _settle_startup_resolved, fleet_committed_cost,
                            sweep as run_sweep)
from strategy.stats import inventory_from_db

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
(ROOT / "logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(ROOT / "logs" / "fleet.log", encoding="utf-8"),
              logging.StreamHandler()])
log = logging.getLogger("fleet")

# Two book requests per market visit. This budget keeps us far under any sane
# public rate limit even at 40 markets.
REQ_PER_SEC = 2.0

# HEARTBEAT.
#
# The dashboard declares the fleet dead when it has not seen progress for
# STALE_AFTER_SEC (120s), and the only thing it could see was
# run/fleet_state.json -- written once per COMPLETE sweep. A healthy 20-market
# sweep is 50-70s, so a single slow venue pushed the write past two minutes and
# a fleet that was trading correctly reported STALE with figures the operator
# was told not to trust.
#
# The pulse separates "the loop is alive" from "a sweep finished". The trading
# loop stamps an in-memory `_Pulse` once per iteration -- roughly every second,
# before any network or DB work, so nothing in the body can skip it -- and a
# background thread copies that stamp to disk on a fixed cadence.
#
# The thread publishes the LOOP's timestamp, never its own. Writing time.time()
# would keep reporting a healthy fleet after the trading loop had wedged, which
# is the single failure this indicator exists to catch. `written_ts` is carried
# alongside purely so a reader can tell "the loop stopped" from "the whole
# process died".
PULSE_FILE = RUN / "fleet_pulse.json"
PULSE_WRITE_SEC = 10.0

# Cooldown before re-attempting a market whose metadata would not load. Long
# enough that a closed market stops costing a request per rotation, short enough
# that a market recovering from a venue blip is picked back up within a sweep or
# two.
RESOLVE_INTERVAL_SEC = 900.0


class _Pulse:
    """Last-known progress of the trading loop, shared with the writer thread.

    Small enough to guard with one lock: the loop holds it for the duration of
    three assignments, so the writer never waits measurably and the loop never
    waits on the writer's disk I/O -- which is the whole point of moving the
    write off the loop thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ts = time.time()
        self._iterations = 0
        self._sweeps = 0
        self._market = ""
        self._markets = 0
        # Wall time of the last COMPLETED sweep, measured here rather than
        # inferred by the dashboard. The page used to derive sweep duration
        # from the freshest per-market `_live` timestamp, which is not a sweep
        # duration at all: a market that fails to load never writes `_live`,
        # so that figure grew without bound and a fleet cycling every 21s was
        # reported as "a full sweep is taking 30m41s".
        self._sweep_start = time.time()
        self._sweep_sec: float | None = None

    def touch(self, market: str, markets: int) -> None:
        with self._lock:
            self._ts = time.time()
            self._iterations += 1
            self._market = market
            self._markets = markets

    def sweep_done(self) -> None:
        with self._lock:
            now = time.time()
            self._sweeps += 1
            self._sweep_sec = now - self._sweep_start
            self._sweep_start = now

    def idle(self) -> None:
        """Roll the in-progress sweep clock over an empty-universe pass.

        An empty fleet never completes a sweep, so without this
        `sweep_elapsed` would measure from process boot forever and the
        dashboard would report a healthy idle loop as "a full sweep is
        taking 49m". Each idle pass IS a completed pass -- over zero
        markets -- so roll the clock and keep the in-progress figure
        honest, but record no measured sweep: there was nothing to measure,
        and a fake `sweep_sec` would read as a healthy 1-second sweep when
        no market was ever visited.
        """
        with self._lock:
            self._sweep_start = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {"loop_ts": self._ts, "iterations": self._iterations,
                    "sweeps": self._sweeps, "market": self._market,
                    "markets": self._markets, "pid": os.getpid(),
                    # None until the first sweep completes: no observation is
                    # not a zero, and a fleet 10 markets into its first sweep
                    # must not publish a duration it has not measured.
                    "sweep_sec": self._sweep_sec,
                    # How long the sweep IN PROGRESS has been running. A sweep
                    # that wedges shows up here immediately instead of waiting
                    # for a completion that never arrives.
                    "sweep_elapsed": time.time() - self._sweep_start,
                    "written_ts": time.time()}


def _atomic_write_json(path: Path, data: Any, **dumps_kwargs: Any) -> None:
    """Write data to path atomically using a temporary file and replace."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, **dumps_kwargs), encoding="utf-8")
    tmp.replace(path)


def _write_pulse(pulse: _Pulse) -> None:
    """Publish the pulse atomically.

    Written to a temp file and renamed because the dashboard reads this on an
    unrelated schedule; a reader that catches a partial write would parse a
    truncated JSON object and report the fleet dead for exactly the reason the
    file exists to disprove.
    """
    _atomic_write_json(PULSE_FILE, pulse.snapshot())


def _pulse_writer(pulse: _Pulse, stop: threading.Event,
                  interval: float = PULSE_WRITE_SEC) -> None:
    """Copy the loop's pulse to disk every `interval` seconds until stopped."""
    while not stop.wait(interval):
        try:
            _write_pulse(pulse)
        except Exception as e:
            # A heartbeat that cannot be written is not worth taking the fleet
            # down for, and this thread has no other job -- try again next tick.
            log.warning("pulse write failed: %s: %s", type(e).__name__, e)


def _publish_state(states) -> None:
    """Serialise the fleet's current market set for the dashboard.

    The single site that writes `run/fleet_state.json`, called at the end of
    every completed sweep AND on the empty-universe transition. The empty case
    is the one this function exists for: an empty fleet never completes a
    sweep, so without a write here the dashboard keeps rendering whatever the
    previous run serialized -- dead markets frozen on their last 404 errors --
    until the universe refills.

    Written via a temp file and renamed because the dashboard reads this on
    its own schedule; json.loads on a half-written file throws and the page
    then reports the fleet as not running. Wrapped for the same reason every
    other sweep-end step is: a full disk must degrade the dashboard, not stop
    the trading loop before it can reach the next heartbeat.
    """
    try:
        f = RUN / "fleet_state.json"
        _atomic_write_json(f, [s.spec for s in states], default=str)
    except Exception as e:
        log.warning("fleet_state write failed: %s: %s",
                    type(e).__name__, e)


def _idle_empty(states, pulse: _Pulse, empty_logged: bool) -> bool:
    """One idle iteration of an empty universe, keeping the fleet alive.

    `states[i % 0]` would take the process down; idling keeps it and its
    heartbeat alive so the next re-rank can refill the fleet. The pulse still
    advances -- the loop IS running, it just has nothing to visit, and the
    empty market count on the pulse is what tells the operator which of the
    two it is.

    It also rolls the sweep clock (`pulse.idle`) on every pass: a pass over
    zero markets completes as trivially as one over six, and without the
    roll `sweep_elapsed` would measure from process boot forever -- a
    healthy idle fleet was being reported as "a full sweep is taking 49m".

    The warning and the state publish ride the same transition. At one
    iteration per second an unconditional write or warning fills the log (or
    the disk) faster than the re-rank interval that would clear it; once is
    enough because an empty set is empty forever until the universe refills.
    """
    pulse.touch("", 0)
    # Roll the sweep clock: a pass over zero markets completes as trivially
    # as a pass over six, and `sweep_elapsed` must not grow from boot while
    # the fleet idles -- a healthy empty loop was being reported as "a full
    # sweep is taking 49m" (see `_Pulse.idle`).
    pulse.idle()
    if not empty_logged:
        log.warning("no markets in fleet; waiting for re-rank")
        # The dashboard renders whatever the LAST completed sweep serialized,
        # and an empty fleet never completes a sweep. Publish `[]` on the
        # transition so the page clears the previous run's markets instead of
        # serving a file the fleet will never rewrite.
        _publish_state(states)
        empty_logged = True
    return empty_logged


def _gate_from_db(cid: str) -> str:
    """The persisted gate verdict, defaulting to NORMAL.

    Only EXITED is honoured. A stored NORMAL/WIDENED is treated as absent, so
    the state machine recomputes it from this run's markout instead of carrying
    a mid-graduation position across a restart it knows nothing about.

    Degrades to NORMAL on any failure -- a fresh DB, a table that predates this
    feature. That is the behaviour from before gate persistence existed, so a
    broken read costs us the old bug rather than the whole fleet.
    """
    try:
        return gate.EXITED if store.get_gate_state(cid) == gate.EXITED else gate.NORMAL
    except Exception as e:
        log.warning("gate rehydrate failed for %s: %s", cid[:10], e)
        return gate.NORMAL


class MarketState:
    """Per-market state -- everything that was module-level in the single loop."""

    def __init__(self, spec: dict, base_cfg):
        self.spec = spec
        self.cid = spec["cid"]
        self.title = spec["title"]
        # `daily`, `source` and `pot` are all set by refresh_pot, which the
        # re-rank calls again when the spec is rewritten. See its docstring.
        self.refresh_pot(spec, base_cfg)
        # Each market publishes its own reward window, minimum order size and
        # tick. Quoting under a market's min_size scores exactly zero, so these
        # are load-bearing, not cosmetic.
        self.cfg = replace(
            base_cfg,
            objective="rewards",
            min_quote_shares=int(spec["min_size"]),
            quote_shares=int(spec["shares"]),
            max_spread_from_mid=spec["max_spread"] / 100.0,
            price_tick=float(spec["tick"]),
            min_t_remaining_sec=0.0,
            market_title=spec["title"],
            market_daily_rate=spec["daily"],
        )
        self.market = None
        # Earliest time we may try loading this market again. A market that is
        # closed, or whose metadata endpoint is timing out, costs a full request
        # on EVERY visit while returning nothing -- twenty of those turn a 60s
        # sweep into a multi-minute one and the dashboard reports the fleet
        # dead. Retrying on a cooldown instead keeps a broken market cheap.
        self.market_retry_ts = 0.0
        # When the book-readiness gates (fetch / depth) began failing
        # consecutively, or None while they pass. Drives the confirmation
        # window in `_book_gate_confirmed` so a 1-2s venue blip does not cancel
        # quotes and stamp STALE/ERROR.
        self.book_gate_fail_since: float | None = None
        # Per-market allocator verdict from the last `reallocate` (why this
        # market is funded or not, with the numbers). None until the first
        # sweep has measured the market. Surfaced on the pipeline view.
        self.alloc_verdict: dict | None = None
        self.engine = QueueFillEngine()
        # Rehydrate from the fills table instead of starting at zero. Fills are
        # persisted, inventory was not, so every restart silently dropped the
        # position while the DB kept the fills -- the dashboard then reported
        # "no position" against 77 recorded fills. Open orders are NOT restored
        # (the venue would not have them after a restart either); only shares
        # already bought, which are the part that can still lose money.
        self.inv = inventory_from_db(self.cid)
        self.seen_trades: set = set()
        self.tape_primed = False
        # Pairs merged back to collateral this process. Session-scoped on
        # purpose: it feeds the pairing rate against fills observed in the same
        # window, and the durable record is the `closes` table.
        self.merged_shares = 0.0
        # U35 pairs-only rule. `last_fill_ts` is when the most recent fill
        # landed -- rebuilt from the fills ledger here so the 15-minute action
        # window survives a restart; `pair_rule_handled_ts` marks which fill
        # the rule already declared window-expired, so that event is recorded
        # once per fill rather than on every sweep while the window stays shut.
        self.last_fill_ts = getattr(self.inv, "last_fill_ts", None)
        self.pair_rule_handled_ts: float | None = None
        # Rolling (ts, theirs) observations. One snapshot sized the entire
        # fleet on 2026-07-29 and read a competing score of 35 for a market
        # that measured 3,727 live -- a 100x error, and the reason the
        # top-ranked market delivered $0.25/day against $18.96 projected.
        self.theirs_samples: list[tuple[float, float]] = []
        self.err = ""
        # Operator-facing event deduplication. A quote check runs every cycle,
        # but the dashboard should show a meaningful transition rather than a
        # new QUOTING row every two seconds.
        self.event_key = None
        self.event_ts = 0.0
        # Rehydrate an EXITED verdict, and only an EXITED verdict.
        #
        # This used to start every market at NORMAL on the argument that a
        # restart must not inherit a stale judgement. The argument is backwards
        # for this particular state. EXITED is not a guess -- it is the
        # conclusion of `markout_min_sample` fills proving the market takes
        # money off us, and a process restart is not new information about the
        # market. Starting fresh meant every restart re-entered every toxic
        # market and bought that same evidence a second time, which is the one
        # cost the gate exists to stop us paying twice.
        #
        # NORMAL and WIDENED are deliberately not restored: they are cheap to
        # recompute (one sample, at an offset that still earns rent) and both
        # are recoverable, so inheriting them buys nothing. EXITED is the
        # asymmetric one, and it is terminal by design -- `next_state` never
        # leaves it -- so there is no stale-verdict risk to trade off.
        self.gate = _gate_from_db(self.cid)
        self.markout: dict = {"verdict": "insufficient_sample",
                              "mean_per_share": None, "n": 0}

    def refresh_pot(self, spec: dict, base_cfg) -> None:
        """Re-read what this market pays from a freshly ranked spec.

        WHAT THIS MARKET PAYS, AND WHAT PAYS IT.

        `daily` is the reward pot, and it is 0 for every market publishing
        clobRewards: 0 -- which is most of the ones that trade. Those are paid
        in spread instead, so the pot is reconstructed from volume and book
        width. Three separate consumers need the same answer: the allocator
        sizes on it, the live-state income projection reports it, and the
        dashboard reads that.

        Called from `__init__` and again on every re-rank, because volume
        moves. A market surviving a re-rank keeps its MarketState object, so
        nothing else would re-read the spec -- and for a spread market the pot
        IS the volume estimate, so a stale one sizes against volume the market
        no longer has.

        Derived from config rather than read from the spec, so revising the
        capture assumption takes effect on the next sweep.
        """
        # MERGED IN PLACE, NEVER REBOUND.
        #
        # `main` builds `specs` and `states` from the SAME dict objects, writes
        # live telemetry through `st.spec["_live"]`, and serialises `specs` to
        # run/fleet_state.json. Rebinding `self.spec` to the fresh dict detached
        # a surviving market from that list: its later `_live` writes landed in
        # an object `specs` no longer referenced, so after the first re-rank the
        # dashboard file kept reporting the pre-re-rank snapshot forever.
        #
        # `_live` is excluded from the merge because the fresh spec off disk has
        # none, and copying that absence in would blank the current reading --
        # `visit` reads `prev_live` to decide the merge-velocity exception.
        if spec is not self.spec:
            self.spec.update({k: v for k, v in spec.items() if k != "_live"})
            # cfg carries spec-derived numbers too. Left stale they disagree
            # with the dict `reallocate` reads `min_size` from.
            self.cfg = replace(
                self.cfg,
                min_quote_shares=int(self.spec["min_size"]),
                max_spread_from_mid=self.spec["max_spread"] / 100.0,
                price_tick=float(self.spec["tick"]),
                market_title=self.spec["title"],
                market_daily_rate=self.spec["daily"])

        self.daily = self.spec["daily"]
        self.source = "rewards" if self.daily > 0 else "spread"
        self.pot = self.daily if self.daily > 0 else spread_capture_daily(
            float(self.spec.get("volume_24h") or 0.0),
            float(self.spec.get("spread")
                  or base_cfg.spread_capture_default_spread),
            base_cfg.spread_capture_frac)

    def observe_theirs(self, ts: float, theirs: float, window_sec: float) -> None:
        """Add one competitor-depth reading and drop anything past the window."""
        self.theirs_samples.append((ts, theirs))
        cutoff = ts - window_sec
        self.theirs_samples = [(t, v) for t, v in self.theirs_samples
                               if t >= cutoff]

    def avg_theirs(self) -> float | None:
        """Mean competing depth over the window, or None with no samples.

        None rather than 0.0: no observation is not an empty book, and an
        empty book is the single most attractive-looking input the allocator
        can receive. Guessing it would concentrate capital into exactly the
        markets we know least about.
        """
        if not self.theirs_samples:
            return None
        return sum(v for _, v in self.theirs_samples) / len(self.theirs_samples)


def load_specs() -> list[dict]:
    f = RUN / "markets.json"
    if not f.exists():
        raise SystemExit("run/markets.json missing -- run: "
                         "python -m scripts.rank_markets")
    return json.loads(f.read_text(encoding="utf-8"))


def specs_mtime() -> float:
    """When the ranker last rewrote the universe, or 0.0 if it never has.

    The adoption trigger. A timer alone cannot do this job: the fleet and the
    ranker start together, the fleet reads whatever markets.json is on disk at
    that instant, and the ranker's first write lands a minute or two later --
    so with a one-hour interval the fleet spends its first hour quoting the
    universe it was explicitly restarted to replace. Observed on 2026-08-04:
    a fresh file arrived 19 seconds after boot and all 20 markets still read
    "closed / not accepting orders".

    Returns 0.0 rather than raising: a missing file is already handled by the
    caller, and a stat that fails must not stop the loop.
    """
    try:
        return (RUN / "markets.json").stat().st_mtime
    except OSError:
        return 0.0


def fleet_naked_cost(states) -> float:
    """Dollars of unhedged inventory across the whole fleet.

    The unpaired leg is the only thing that can lose: a matched pair always
    pays $1. Cost is used rather than share count because $1 of exposure at
    0.85 and at 0.26 are the same amount of money at risk, while 100 shares of
    each are not.
    """
    total = 0.0
    for s in states:
        naked = abs(s.inv.up_shares - s.inv.down_shares)
        if naked <= 0:
            continue
        avg = (s.inv.avg("UP") if s.inv.up_shares > s.inv.down_shares
               else s.inv.avg("DOWN"))
        total += naked * (avg or 0.0)
    return total


def _fleet_posture(cfg) -> str:
    """The fleet's current brake position, from the same pool as the fallback.

    Two readings of one number, to two different questions. The fallback above
    asks "what is THIS market's state?" and must cap a borrowed verdict at
    WIDENED, because the pool is not evidence about a market that never
    matured a sample of its own. This asks "should the fleet be ADDING right
    now?", and the pool is precisely the right evidence for that -- it is the
    only reading that exists when, as on 2026-08-02, no individual market ever
    reaches a sample and the pooled mean is -4.75c/share.

    A separate one-line function rather than an inline call so the posture can
    be read in a test without standing up a sweep, and so the DB touch has one
    site. Nothing here is stored: `gate.fleet_posture` is a pure function of
    the reading, and the reading is taken fresh every sweep.
    """
    return gate.fleet_posture(
        markout.fleet_stats(cfg.markout_fleet_min_sample), cfg)


def _alloc_verdict(dollars: float, min_size: int, pot: float,
                   avg_theirs: float, k: float, floor: float) -> dict:
    """The allocator's verdict on one market, in the numbers that decided it.

    `dollars` is what `allocate_fundable` returned (0.0 = refused), `avg_theirs`
    is the measured competition score and `k` the per-share score used to
    convert it into competitor depth (`T = avg_theirs / k` dollars). The
    marginal return shown is on the NEXT dollar at the allocator's decision:
    for a funded market that is the marginal at its final size; for an
    unfunded one it is the first-dollar marginal, which is exactly the number
    that was compared to `floor` and found wanting.

    Pure, so the pipeline view can be pinned by a test without standing up a
    fleet sweep.
    """
    T = avg_theirs / k if k and k > 0 else float("inf")
    # T == inf means we score nothing against the book (k == 0): the first
    # dollar earns nothing, not NaN.
    first = 0.0 if T == float("inf") else marginal(0.0, pot, T) * 100.0
    thresh = floor * 100.0
    n = shares_for(dollars, min_size) if dollars > 0 else 0
    if pot <= 0:
        reason = "unpayable: no pot (spread/volume unmeasured)"
    elif n > 0:
        reason = f"funded {n} shares"
    elif dollars > 0:
        reason = (f"${dollars:.0f} allocated but under the "
                  f"{min_size}-share minimum")
    elif first < thresh:
        reason = f"unfunded: below {thresh:.2f}%/day floor"
    else:
        reason = (f"unfunded: first dollar clears {thresh:.2f}%/day but the "
                  f"{min_size}-share minimum cannot pay it")
    return {
        "funded": n > 0,
        "shares": n,
        "dollars": round(dollars, 2),
        "marginal_pct": round(
            0.0 if T == float("inf") else marginal(dollars, pot, T) * 100.0, 2),
        "first_marginal_pct": round(first, 2),
        "competition_avg": round(avg_theirs, 1),
        "pot_day": round(pot, 2),
        "threshold_pct": round(thresh, 2),
        "reason": reason,
    }


def reallocate(states, base) -> dict:
    """Resize every market by marginal return instead of a flat 120 shares.

    Measured 2026-07-29, the flat size produced returns spanning 27.58%/day to
    0.28%/day on identical $115 stakes -- because income is pot x share, and
    share is set by the competition, not by the pot. Big pots are big precisely
    because makers crowd them.

    Two income sources, one water-fill. Reward markets are sized on their pot;
    markets paying no rewards at all are sized on expected spread capture, and
    only the reward ones are held to the $1.50 minimum-distribution floor.

    Runs only on markets that have reported a live share; a market we have not
    measured yet keeps its current size rather than being sized off a guess.
    Markets the allocator funds below their min_size get 0 and stop quoting,
    which is the intended outcome -- capital in a market returning 0.3%/day is
    worse than capital sitting idle.
    """
    obs = []
    # Measured markets with nothing behind them. Held separately from `obs` so
    # they cannot influence the water-fill, then merged into `dollars` at 0.0
    # so their size is actively driven to zero.
    unpayable: list[str] = []
    floor = base.reward_min_payout_usd * base.reward_floor_multiple
    for s in states:
        # SIZE OFF THE COMPETITION, NOT OFF OUR OWN ORDERS.
        #
        # `_live["share"]`, `["capital"]` and `["income"]` are all measured
        # from our resting orders, so all three read zero the moment a market
        # is defunded -- and this function used to consult exactly those. A
        # market defunded for earning nothing then reported nothing, was
        # skipped for having no share, and could never be funded again. One
        # way. Measured on the 13.4h run of 2026-07-30: samples scoring
        # anything decayed from 67/219 to 0/190, and the fleet posted its last
        # quote at T+8.1h while continuing to poll for another five hours.
        #
        # `theirs` is the input that survives, because it is scored over the
        # whole book whether or not we are in it -- Taylor Swift still
        # measured 1,504 while we quoted nothing at all. Averaged, not
        # instantaneous: one snapshot read 35 for a market that measured 3,727
        # and sized the entire fleet off it.
        avg_theirs = s.avg_theirs()
        if avg_theirs is None:
            continue        # never sampled -- keep its size rather than guess

        # A score converts to dollars through the per-share score, since a
        # pair costs ~$1 and N dollars therefore buys ~N shares a side. Any
        # reference capital returns the same competitor depth out of
        # competitor_depth() -- it cancels -- so this is a change of units,
        # not an assumption about size.
        k = rewards.score_per_share(s.cfg.max_spread_from_mid,
                                    s.cfg.reward_offset)
        ref = 100.0
        ours_ref = ref * k
        total = ours_ref + avg_theirs

        # A market that pays no rent still pays a spread, and `MarketState`
        # has already converted that into the same $/day pot, so everything
        # here -- income, marginal, the water-fill -- is unchanged. What does
        # NOT carry over is the $1.50 payout floor: it is the venue's minimum
        # reward DISTRIBUTION, and a spread market makes no distribution.
        # `source` is what tells allocate_fundable which rule applies.
        #
        # No reward pot and no measured volume leaves a zero pot. That is not
        # a cheap market, it is an unknown one, and unknown must not size as
        # zero-competition upside -- so it stays out of the water-fill.
        #
        # But it must NOT be dropped the way an unsampled market is. We have
        # measured this one; `avg_theirs` above proves it. Dropping it here
        # left its cid absent from `dollars`, and the loop below reads absence
        # as "never sampled" and keeps the previous size -- so a spread market
        # funded on an earlier sweep whose volume later read 0 went on quoting
        # its funded size with no pot behind it. That is the exact failure the
        # zeroing below exists to prevent. Recorded as unpayable instead.
        if s.pot <= 0:
            unpayable.append(s.cid)
            continue

        obs.append({"cid": s.cid, "daily": s.pot, "source": s.source,
                    "capital": ref,
                    "share": (ours_ref / total) if total > 0 else 1.0,
                    "min_dollars": float(s.spec["min_size"])})

    if not obs and not unpayable:
        return {}

    # REWARD ELIGIBILITY, applied inside the allocation rather than ahead of
    # it. Polymarket pays nothing below $1 per distribution, so a market
    # projecting under the floor is not a small earner -- it is committed
    # capital earning exactly zero, and 16 of 20 markets were in that state on
    # 2026-07-30 while the fleet funded every one.
    #
    # Judged at the size actually allocated, because income is monotone in
    # size: the same market that fails the floor at its 100-share minimum
    # clears it 3.6x over at the 600 shares the budget affords.
    #
    # Unfunded, NOT dropped: the market stays in `states` so its inventory is
    # still merged, marked out and reconciled. Removing it here would strand a
    # real position with nothing tending it.
    dollars = allocate_fundable(obs, base.allocation_budget,
                                base.marginal_return_floor, floor,
                                max_frac=base.max_market_frac)

    # Whether the BUDGET, rather than the floor, is what stopped the water-fill
    # while a market was still returning well above the floor. That is the only
    # condition under which holding a stagnant pair has a measurable
    # alternative use, and it is what licenses profit_take's relaxed threshold.
    # Computed once per sweep, on the same observation set the sizing used, so
    # the flag and the allocation cannot disagree.
    scarce = capital_scarcity(obs, dollars, base.allocation_budget,
                              base.marginal_return_floor,
                              base.scarcity_marginal_multiple)

    # Merged only now, AFTER scarcity: these markets were deliberately kept out
    # of the observation set, so they must not colour the budget-vs-floor
    # verdict either. Present at 0.0 the loop below reads as "measured and
    # refused" -- the same treatment a market failing the floor gets.
    # setdefault, not assignment, so a real allocator verdict always wins.
    for cid in unpayable:
        dollars.setdefault(cid, 0.0)

    out = {}
    for s in states:
        # Every market learns the fleet-wide flag, including the ones the
        # allocator did not fund this sweep -- an unfunded market is precisely
        # one whose locked capital we most want released.
        s.cfg = replace(s.cfg, capital_scarce=scarce)

        # Absent from `dollars` means never sampled, and only that -- a market
        # `allocate_fundable` refused is present at 0.0. The two need opposite
        # treatment: an unmeasured market keeps its current size (sizing it
        # off a guess is worse than leaving it alone), while a measured market
        # that cannot pay must be zeroed, or it keeps quoting its startup size
        # while earning nothing.
        #
        # Caught by the smoke run, not the tests: 17 markets kept quoting 120
        # shares each while only 4 were funded, so offers alone reached $2,108
        # against a $2,000 committed cap before a single share was bought.
        if s.cid not in dollars:
            # Never sampled this sweep: keep the previous verdict (or none).
            # Sizing off a guess is worse than leaving it alone, and the same
            # discipline applies to the verdict shown.
            continue
        n = shares_for(dollars[s.cid], int(s.spec["min_size"]))
        out[s.cid] = n
        # quote_shares drives size; min_quote_shares is the venue's scoring
        # floor and must not be raised above it, or we would quote below our
        # own threshold and score zero.
        s.cfg = replace(s.cfg, quote_shares=max(n, 0))
        # Every measured market learns why it was (or wasn't) funded, on the
        # same numbers the water-fill used -- recomputed here so the verdict
        # cannot drift from the decision.
        try:
            s.alloc_verdict = _alloc_verdict(
                dollars[s.cid], int(s.spec["min_size"]), s.pot,
                s.avg_theirs() or 0.0,
                rewards.score_per_share(s.cfg.max_spread_from_mid,
                                        s.cfg.reward_offset),
                base.marginal_return_floor)
        except Exception as e:
            log.warning("alloc verdict failed for %s: %s", s.title[:30], e)
    return out

def visit(st: MarketState, bot_cfg, now: float,
          fleet_naked_usd: float = 0.0, committed_usd: float = 0.0,
          states=None, fleet_posture: str = gate.NORMAL,
          resolved_cids: frozenset[str] = frozenset()) -> SweepOutcome:
    """One poll of one market, behind the sweep module's one interface.

    Backward-compatible alias for `sweep.sweep` (issue #12): the fleet
    loop calls the sweep module directly now, and this thin wrapper keeps
    the pre-#12 call sites -- the tests -- working. It builds a
    `SweepContext` from the old positional signature and hands the sweep
    over, returning its outcome.
    """
    return run_sweep(st, SweepContext(
        bot_cfg=bot_cfg, now=now, fleet_naked_usd=fleet_naked_usd,
        committed_usd=committed_usd, states=states,
        fleet_posture=fleet_posture, resolved_cids=resolved_cids))

def _maybe_resolve(bot_cfg, last: float, now: float) -> float:
    """Run the settlement pass at most once per RESOLVE_INTERVAL_SEC.

    Returns the deadline to carry forward. The deadline advances even when the
    pass fails: a persistently unreachable venue would otherwise be retried on
    every iteration of the trading loop -- once per second, against dozens of
    markets -- instead of once per interval.
    """
    if now - last < RESOLVE_INTERVAL_SEC:
        return last
    try:
        n = resolve.resolve_finished(bot_cfg.clob_host)
        if n:
            log.info("RESOLVE %d market(s) settled", n)
    except Exception as e:
        # `resolve_finished` already swallows per-market failures; this catches
        # only a failure of the pass itself, which must not stop the fleet.
        log.warning("resolution pass failed: %s: %s", type(e).__name__, e)
    return now


def main() -> None:
    RUN.mkdir(exist_ok=True)
    base = load_cfg()
    bot_cfg = load_bot_cfg()
    specs = load_specs()
    states = [MarketState(s, base) for s in specs]
    log.info("fleet starting | %d markets | $%.0f/day funded | offset %.1fc",
             len(states), sum(s["daily"] for s in specs), 100 * base.reward_offset)

    # Start the heartbeat before the first visit, and publish once immediately:
    # a dashboard polled during startup should see a fresh pulse rather than a
    # missing file for the first PULSE_WRITE_SEC.
    pulse = _Pulse()
    stop_pulse = threading.Event()
    threading.Thread(target=_pulse_writer, args=(pulse, stop_pulse),
                     name="fleet-pulse", daemon=True).start()
    try:
        _write_pulse(pulse)
    except Exception as e:
        log.warning("initial pulse write failed: %s: %s", type(e).__name__, e)

    gap = 2.0 / REQ_PER_SEC
    i = 0
    last_rerank = time.time()
    # The universe we are actually trading, dated. Adoption is driven by this
    # changing rather than by the interval alone -- see `specs_mtime`.
    specs_mtime_seen = specs_mtime()
    # Deliberately 0.0 rather than `now`: a fleet that has just restarted is
    # exactly when the unresolved set is most likely to contain markets that
    # closed while it was down, and waiting a full interval to notice keeps
    # that capital committed for another 15 minutes.
    last_resolve = 0.0
    # Same "deliberately not `now`" reasoning as `last_resolve`: loaded before
    # the loop starts so a fleet holding positions resolved while it was down
    # skips their books on its very first visit, not after a 15-minute wait.
    try:
        resolved_cids = frozenset(store.resolved_cids())
    except Exception as e:
        log.warning("initial resolved-cid load failed: %s: %s",
                    type(e).__name__, e)
        resolved_cids = frozenset()
    # STARTUP SETTLE PASS (U11). `MarketState.__init__` rebuilt inventory from
    # the fills ledger, which never learns about resolutions -- so a market
    # that settled while the fleet was down restarted holding phantom shares
    # that counted as committed capital. The first visit would settle it, but
    # only after its turn in the rotation, and a re-rank that drops the market
    # before that turn retains it forever on the phantom position. Settle now,
    # before the first visit, so the process starts with the truth.
    try:
        settled, freed = settle_startup_resolved(states, resolved_cids,
                                                time.time())
        if settled:
            log.info("STARTUP SETTLE %d market(s) already resolved; "
                     "released $%.2f committed", settled, freed)
    except Exception as e:
        log.warning("startup settle pass failed: %s: %s",
                    type(e).__name__, e)
    empty_logged = False
    # THE FLEET BRAKE, held across visits and re-read once per sweep. Per-sweep
    # rather than per-visit because it is one aggregate over the whole markout
    # table -- the exposure totals beside it are recomputed per visit because a
    # fill two seconds ago already changed them, while a pooled mean over
    # dozens of markets does not move within one rotation. NORMAL until the
    # first sweep boundary, which is the same answer a thin pool gives anyway.
    posture = gate.NORMAL
    while True:
        # PERIODIC RE-RANK. run/markets.json was written 2026-07-29 01:39 and
        # the fleet ran against it for a day and a half while competitors
        # arrived and reward rates changed underneath it.
        #
        # Re-picking the candidate set means scoring hundreds of books, which
        # does not belong inside the trading loop -- `scripts/rank_markets`
        # owns that and writes the file. What happens here is adopting the
        # result: any market the ranker has since added is picked up, and any
        # market it dropped is retained if it still holds inventory, because
        # dropping a live position strands it with nothing to merge or
        # reconcile it.
        now_ts = time.time()

        # SETTLEMENT. Runs before the re-rank so that a market closing this
        # cycle is recorded resolved before the ranker is allowed to drop it:
        # `unresolved()` is keyed off fills, not off the current market set,
        # but recording it first keeps the two views consistent within a cycle.
        resolve_ran_at = last_resolve
        last_resolve = _maybe_resolve(bot_cfg, last_resolve, now_ts)
        if last_resolve != resolve_ran_at:
            try:
                resolved_cids = frozenset(store.resolved_cids())
            except Exception as e:
                log.warning("resolved-cid refresh failed: %s: %s",
                            type(e).__name__, e)

        # ADOPT WHEN THE FILE CHANGES, NOT ONLY WHEN THE TIMER FIRES. The
        # ranker's write IS the event; the interval is now just a floor that
        # keeps the old behaviour if the file is rewritten with identical
        # content. Waiting for the timer meant a fleet restarted specifically
        # to pick up a fresh universe traded the stale one for a full hour.
        mtime = specs_mtime()
        if (now_ts - last_rerank >= base.rerank_interval_sec
                or (mtime and mtime != specs_mtime_seen)):
            if mtime != specs_mtime_seen:
                log.info("RERANK adopting markets.json rewritten %.0fs ago",
                         max(0.0, now_ts - mtime))
            last_rerank = now_ts
            try:
                fresh = {s["cid"]: s for s in load_specs()}
                # Marked seen only once it PARSED. A read that caught the
                # ranker mid-write raises here, and recording the mtime before
                # that would retire the trigger for a file we never adopted --
                # the fleet would then wait out the full interval holding the
                # universe it was trying to replace.
                specs_mtime_seen = mtime
                known = {s.cid for s in states}
                by_cid = {s.cid: s for s in states}
                for cid, spec in fresh.items():
                    if cid not in known:
                        states.append(MarketState(spec, base))
                        log.info("RERANK + %s", spec["title"][:40])
                    else:
                        # A SURVIVING MARKET KEEPS ITS OBJECT, SO ITS POT MUST
                        # BE RE-READ EXPLICITLY. For a spread market the pot IS
                        # the volume estimate, and `__init__` computed it once
                        # from the spec present at process start. A market whose
                        # 24h volume halved went on sizing and reporting against
                        # the volume it had hours ago, for the life of the
                        # process -- which is exactly what the periodic re-rank
                        # exists to prevent.
                        by_cid[cid].refresh_pot(spec, base)
                held = [s for s in states if s.cid not in fresh
                        and (s.inv.up_shares or s.inv.down_shares)]
                dropped = [s for s in states
                           if s.cid not in fresh and s not in held]
                if dropped:
                    for s in dropped:
                        log.info("RERANK - %s", s.title[:40])
                    states = [s for s in states if s not in dropped]
                if held:
                    log.info("RERANK %d dropped market(s) retained: still "
                             "holding inventory", len(held))
            except Exception as e:
                # A stale market set is survivable; a dead fleet is not.
                log.warning("rerank failed, keeping current markets: %s: %s",
                            type(e).__name__, e)

        if not states:
            empty_logged = _idle_empty(states, pulse, empty_logged)
            time.sleep(gap)
            continue
        if empty_logged:
            log.info("markets restored: %d in fleet", len(states))
            empty_logged = False

        if i % len(states) == 0:
            # Re-read at the sweep boundary so every market in a rotation is
            # judged against the same pooled reading. Half a sweep on one
            # posture and half on another is the fleet disagreeing with itself
            # about whether it is braking.
            #
            # Wrapped for the same reason every other periodic step in this
            # loop is: a failed read must degrade to the PREVIOUS posture, not
            # stop the fleet. Falling back to NORMAL instead would quietly lift
            # a live halt on a transient DB error, which is the one direction
            # this must never fail in.
            prev_posture = posture
            try:
                posture = _fleet_posture(base)
            except Exception as e:
                log.warning("fleet posture read failed, holding %s: %s: %s",
                            posture, type(e).__name__, e)
            # Logged on the TRANSITION only, exactly like GATE EXIT above: at
            # one line per sweep an unconditional log would bury the moment the
            # fleet actually stopped adding. Both directions are logged --
            # resuming is as operationally significant as halting, and unlike
            # EXITED this one does resume.
            if posture != prev_posture:
                log.info("FLEET POSTURE %s -> %s (pooled markout)",
                         prev_posture, posture)

        st = states[i % len(states)]
        i += 1
        # THE HEARTBEAT TOUCHPOINT. Stamped before the visit, not after, so a
        # market that throws, times out or returns early still counts as a live
        # cycle -- the loop's health is whether it is cycling, not whether this
        # particular venue answered.
        pulse.touch(st.title[:40], len(states))
        try:
            # Both totals are recomputed per visit rather than per sweep: a
            # fill in the market visited two seconds ago has already changed
            # them, and a cap evaluated against a stale total is a cap that
            # lets the overshoot through.
            visit(st, bot_cfg, time.time(), fleet_naked_cost(states),
                  fleet_committed_cost(states), states, posture,
                  resolved_cids)
        except Exception as e:
            log.warning("%s: %s", st.title[:30], e)
            st.err = str(e)

        if i % len(states) == 0:
            live = [s for s in states if s.spec.get("_live", {}).get("ours", 0) > 0]
            inc = sum(s.spec.get("_live", {}).get("income", 0) for s in states)
            cap = sum(s.spec.get("_live", {}).get("capital", 0) for s in states)
            # Resize once per full sweep, when every market has a fresh share
            # reading. Doing it mid-sweep would size half the fleet off this
            # cycle's data and half off the last one's.
            # Wrapped because a sizing bug must never stop data collection.
            # Unwrapped, a ZeroDivisionError in the allocator killed the whole
            # fleet mid-sweep on 2026-07-29 and nothing was collected for 3.5
            # hours. Quoting at the previous size is an acceptable degraded
            # mode; being dead is not.
            try:
                sizes = reallocate(states, base)
            except Exception as e:
                log.warning("reallocate failed, keeping previous sizes: %s: %s",
                            type(e).__name__, e)
                sizes = {}
            funded = sum(1 for n in sizes.values() if n > 0)
            # The verified ratio rides on the sweep line because it is the one
            # number that decides what happens after Phase A, and a figure that
            # lives only in the database is a figure nobody reads. `--` means
            # nothing observed yet, deliberately not 0% -- an idle fleet has
            # not measured anything.
            try:
                vr = store.verified_ratio()
                vr_txt = ("--" if vr["ratio"] is None
                          else f"{100 * vr['ratio']:.1f}%")
                fills_txt = f"{vr['verified_fills']}v/{vr['unverified_fills']}u"
            except Exception as e:
                vr_txt, fills_txt = "err", str(type(e).__name__)
            # `capital` is offers only; `committed` is every dollar out the
            # door. The pair is logged together on purpose -- reading the
            # first without the second is how a 0.256%/day return got reported
            # as 1.80%/day for a day and a half.
            committed = fleet_committed_cost(states)
            # Sample the projection so it can be integrated over time. The
            # instantaneous figure swings by an order of magnitude within an
            # hour as markets are funded and defunded, so a single reading is
            # only whichever moment the reader happened to look at.
            try:
                store.log_income_sample(time.time(), inc, committed)
            except Exception as e:
                log.warning("income sample failed: %s", e)
            log.info("sweep | %d/%d scoring | est $%.2f/day | offers $%.0f "
                     "| committed $%.0f/%.0f | naked $%.0f | funded %d/%d "
                     "| verified %s (%s)",
                     len(live), len(states), inc, cap,
                     committed, base.max_committed_usd,
                     fleet_naked_cost(states), funded, len(states),
                     vr_txt, fills_txt)
            pulse.sweep_done()
            # SERIALISE FROM `states`, NOT FROM `specs`. `specs` is the list
            # read at startup and it was never kept in step with the re-rank:
            # a market the ranker ADDED got a MarketState but no entry here, so
            # its live telemetry never reached the dashboard, and a market the
            # ranker DROPPED kept its final `_live` in the file forever. Reading
            # the states list makes the file exactly the set being traded.
            _publish_state(states)
        time.sleep(gap)


if __name__ == "__main__":
    main()
