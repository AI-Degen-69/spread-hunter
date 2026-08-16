"""Tradability and horizon gate (U6).

The fleet ran ~74 hours and produced 9 tape-backed fills and zero resolutions.
Neither is a strategy result: the ranker sorts by reward income per dollar of
capital, and that metric prefers a thin book by construction (see the
`rank_markets` docstring -- a $50/day market with a thin book outranks a
$300/day one). Thin books are thin because nobody trades them.

Measured on the 11.6h run of 2026-07-31: 20 markets produced 48 tape prints
between them and 9 of the 20 traded not once. Every market in the universe
resolved between September 2026 and 2027, so no run of any practical length
could observe a settlement.

Two filters the ranker never had:

  * VOLUME. A market that does not trade cannot fill a resting order, whatever
    its reward yield. Reward income is real, but it is a different strategy and
    must not be measured with fill-based instruments.
  * HORIZON. A market resolving in 2027 cannot contribute a settled P&L
    observation to a run measured in days.
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.rank_markets as rank_markets                    # noqa: E402
from scripts.rank_markets import (                            # noqa: E402
    GAMMA, MAX_DAYS_TO_RESOLVE, MIN_VOLUME_24H,
    days_to_resolve, gamma_spread_universe, gamma_volume,
    score_pool, tradable,
)
from strategy.config import load as load_cfg                  # noqa: E402


# --- the volume gate --------------------------------------------------------

def test_a_market_that_does_not_trade_is_rejected():
    """The 2026-07-31 universe, restated: no tape, no fills, at any yield."""
    ok, why = tradable(volume_24h=0.0, days=3.0)
    assert not ok
    assert "volume" in why


def test_a_thin_market_below_the_volume_floor_is_rejected():
    ok, why = tradable(volume_24h=MIN_VOLUME_24H - 1, days=3.0)
    assert not ok
    assert "volume" in why


def test_a_liquid_market_inside_the_horizon_is_accepted():
    ok, why = tradable(volume_24h=MIN_VOLUME_24H * 10, days=3.0)
    assert ok
    assert why == ""


# --- the horizon gate -------------------------------------------------------

def test_a_market_resolving_beyond_the_horizon_is_rejected():
    """"Will Canada's 2026 inflation be between 2.5% and 2.9%?" cannot settle
    inside a run, so it can never contribute a P&L observation."""
    ok, why = tradable(volume_24h=MIN_VOLUME_24H * 10,
                       days=MAX_DAYS_TO_RESOLVE + 1)
    assert not ok
    assert "horizon" in why


def test_an_already_expired_market_is_rejected():
    ok, why = tradable(volume_24h=MIN_VOLUME_24H * 10, days=-1.0)
    assert not ok
    assert "horizon" in why


def test_an_unknown_end_date_is_rejected_rather_than_assumed_near():
    """Unknown horizon is the long-dated case in disguise -- the universe that
    produced zero resolutions was entirely long-dated. Guessing 'near' would
    readmit exactly what this gate exists to exclude."""
    ok, why = tradable(volume_24h=MIN_VOLUME_24H * 10, days=None)
    assert not ok
    assert "horizon" in why


def test_an_unknown_volume_is_rejected_rather_than_assumed_liquid():
    ok, why = tradable(volume_24h=None, days=3.0)
    assert not ok
    assert "volume" in why


# --- horizon arithmetic -----------------------------------------------------

def test_days_to_resolve_reads_the_iso_end_date():
    # 2026-08-02T00:00:00Z is one day after 2026-08-01T00:00:00Z.
    d = days_to_resolve("2026-08-02T00:00:00Z", now_iso="2026-08-01T00:00:00Z")
    assert d == pytest.approx(1.0, abs=1e-6)


def test_days_to_resolve_is_none_when_the_venue_gives_no_end_date():
    assert days_to_resolve(None, now_iso="2026-08-01T00:00:00Z") is None
    assert days_to_resolve("", now_iso="2026-08-01T00:00:00Z") is None


class _StubResponse:
    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return self._rows


class _StubGamma:
    """A Gamma that caps a page at 100 rows however large a `limit` is asked for.

    That is the real endpoint's behaviour, measured 2026-08-02: limit=100, 250
    and 500 all returned exactly 100 rows.
    """

    CAP = 100

    def __init__(self, total, volume=1e9, volumes=None):
        self.total = total
        self.volume = volume
        self.volumes = volumes
        self.offsets = []

    def get(self, url, params=None, timeout=None):
        self.offsets.append(params["offset"])
        start = params["offset"]
        n = max(0, min(self.CAP, params["limit"], self.total - start))
        return _StubResponse([{
            "conditionId": f"0x{start + i:04x}",
            "volume24hr": (self.volumes[start + i]
                           if self.volumes is not None else self.volume),
            "enableOrderBook": True, "acceptingOrders": True,
            "clobRewards": None,
            "clobTokenIds": f'["{start + i}a", "{start + i}b"]',
            "spread": 0.01, "bestBid": 0.49, "bestAsk": 0.51,
            "endDate": "2026-08-03T00:00:00Z",
        } for i in range(n)])


class _StubVolumeGamma:
    """A gamma that serves volume rows keyed by condition_id and records
    every request, for the volume reader's chunking contract."""

    def __init__(self, volumes, fail_chunks=()):
        self.volumes = dict(volumes)
        self.fail_chunks = set(fail_chunks)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params)))
        chunk = params["condition_ids"]
        if tuple(chunk) in self.fail_chunks:
            raise AssertionError(f"simulated failure for chunk {chunk[:3]}...")
        # Strict like _StubClobSession: a test that forgets to set up a cid
        # fails loudly instead of silently reading 0.0.
        return _StubResponse([
            {"conditionId": cid, "volume24hr": self.volumes[cid]}
            for cid in chunk])


def _cids(n):
    return [f"0x{i:04x}" for i in range(n)]


def test_gamma_volume_queries_in_chunks_of_twenty():
    """The candidate list is a few hundred long and the endpoint takes
    repeated `condition_ids`, so the reader must split it into chunks of 20
    with `limit` matching the chunk -- never the whole list at once."""
    cids = _cids(45)
    volumes = {cid: float(i) for i, cid in enumerate(cids)}
    g = _StubVolumeGamma(volumes)

    out = gamma_volume(g, cids)

    assert len(g.calls) == 3, "45 ids must split into 3 requests"
    assert [len(c["condition_ids"]) for _, c in g.calls] == [20, 20, 5]
    assert all(c["limit"] == len(c["condition_ids"]) for _, c in g.calls)
    assert all(url == GAMMA for url, _ in g.calls)
    assert out == volumes, "every id's reading must come back"


def test_gamma_volume_survives_a_failed_chunk():
    """One unreachable or malformed chunk must not abort the whole volume
    read -- the caller treats a missing volume as a failed candidate, and a
    partial map is strictly better than none."""
    cids = _cids(45)
    bad = tuple(cids[20:40])            # the second chunk fails
    g = _StubVolumeGamma({cid: 1.0 for cid in cids}, fail_chunks={bad})

    out = gamma_volume(g, cids)

    assert out == {cid: 1.0 for cid in cids[:20] + cids[40:]}, (
        "the failed chunk's ids are absent, the others intact")


def test_gamma_volume_reads_a_dict_wrapped_response():
    """Gamma can wrap rows in {"data": [...]}; a row without a conditionId
    is skipped, and a missing volume reads as zero rather than crashing."""
    seen = []

    class _Session:
        def get(self, url, params=None, timeout=None):
            seen.append((url, params["condition_ids"]))
            return _StubResponse({"data": [
                {"conditionId": "0x1", "volume24hr": "500.0"},
                {"conditionId": "0x2", "volume24hr": None},
                {"noId": True},
            ]})

    out = gamma_volume(_Session(), ["0x1", "0x2", "0x3"])
    assert out == {"0x1": 500.0, "0x2": 0.0}
    assert seen == [(GAMMA, ["0x1", "0x2", "0x3"])]


def test_gamma_volume_with_no_candidates_makes_no_requests():
    """An empty candidate list is an empty result and no network at all."""
    g = _StubVolumeGamma({})
    assert gamma_volume(g, []) == {}
    assert g.calls == []


def test_paging_advances_by_rows_returned_not_by_the_limit_requested():
    """The endpoint caps a page below the requested limit, so stepping the
    offset by the REQUESTED size skips whatever the cap withheld.

    At the old per_page=250 the second request started at offset 250 while the
    first response had ended at 99 -- rows 100-249 were never fetched, and they
    exist. Unreachable in practice only because the volume floor usually stops
    the scan inside the first page, which is luck rather than design.
    """
    g = _StubGamma(total=1000)
    rows = gamma_spread_universe(g, pages=3, per_page=250)

    assert g.offsets == [0, 100, 200], (
        f"offsets must follow the rows actually returned, got {g.offsets}")
    assert len(rows) == 300, "no row may be skipped between pages"
    ids = [r["condition_id"] for r in rows]
    assert len(set(ids)) == len(ids), "a page must not be fetched twice"


def test_paging_stops_on_a_short_page_without_a_wasted_request():
    """A page smaller than the established size is the last one.

    Short is judged against the size the endpoint actually served on the first
    response, not against `per_page` -- comparing to the request is what made
    every capped response look like the end of the listing.
    """
    g = _StubGamma(total=150)
    rows = gamma_spread_universe(g, pages=5, per_page=100)
    assert g.offsets == [0, 100], "the 50-row page ends it; no empty tail fetch"
    assert len(rows) == 150


def test_an_oversized_limit_no_longer_ends_the_scan_after_one_page():
    """The original bug, stated directly.

    Gamma caps a page at 100 however large a limit is asked for, so at
    per_page=250 `len(rows) < per_page` held on every response and the loop
    broke after page 0. `pages` was never honoured and the scan never saw past
    the first 100 markets.
    """
    g = _StubGamma(total=1000)
    gamma_spread_universe(g, pages=2, per_page=250)
    assert len(g.offsets) == 2, (
        f"an oversized limit must not end the scan after one page: {g.offsets}")


def test_ordering_cutoff_stops_at_the_first_sub_floor_row():
    """The verified descending-volume sort makes the first market under the
    volume floor the end of the listing: nothing after it qualifies, so the
    scan stops without fetching another page and returns only the markets
    above the floor.
    """
    g = _StubGamma(total=10, volumes=[1e9] * 5 + [1e5] * 5)
    rows = gamma_spread_universe(g, pages=3, per_page=100)

    assert g.offsets == [0], "a clean floor cutoff must not fetch page 2"
    assert len(rows) == 5, "only the markets above the floor may be returned"


def test_an_inverted_page_does_not_silently_truncate_the_universe():
    """A qualifying market after a sub-floor one is an ordering regression.
    The scan must not trust the floor cutoff then: it keeps scanning, so the
    qualifying market is included instead of silently dropped.
    """
    g = _StubGamma(total=150, volumes=[1e5] + [1e9] * 149)
    rows = gamma_spread_universe(g, pages=3, per_page=100)

    ids = [r["condition_id"] for r in rows]
    assert "0x0001" in ids, "the market after the sub-floor row was dropped"
    assert g.offsets == [0, 100], (
        "the scan must continue past the floor cutoff after a violation")
    assert len(rows) == 149


def test_days_to_resolve_survives_an_end_date_with_no_timezone():
    """A naive venue timestamp must not abort the ranking run.

    `fromisoformat` returns a naive datetime when the string carries no offset
    and no Z. Subtracting an aware `now` from it raises TypeError, which the
    function's `except ValueError` does not catch -- and `evaluate` calls this
    inside a ThreadPoolExecutor worker, so ONE unqualified endDate from gamma
    took down the whole run. Venue times are UTC, so it must read the same as
    the offset-qualified form.
    """
    naive = days_to_resolve("2026-08-02T00:00:00", now_iso="2026-08-01T00:00:00Z")
    aware = days_to_resolve("2026-08-02T00:00:00Z", now_iso="2026-08-01T00:00:00Z")
    assert naive == aware == pytest.approx(1.0)


def test_days_to_resolve_handles_a_naive_now_as_well():
    """Both sides of the subtraction can arrive unqualified."""
    assert days_to_resolve("2026-08-02T00:00:00",
                           now_iso="2026-08-01T00:00:00") == pytest.approx(1.0)


def test_days_to_resolve_is_negative_once_the_end_date_has_passed():
    d = days_to_resolve("2026-07-31T00:00:00Z", now_iso="2026-08-01T00:00:00Z")
    assert d < 0


# --- C3: one session per worker, never shared -------------------------------

def test_worker_session_is_stable_per_thread_and_distinct_across_threads():
    """`requests.Session` is documented as not thread-safe, so the ranking
    pool must never hand one session object to every worker. The lazy
    thread-local factory must return the SAME session on every call inside
    one thread (so keep-alive pooling survives) and DIFFERENT sessions
    across threads."""
    barrier = threading.Barrier(12)
    results = []

    def worker():
        barrier.wait()                     # all 12 alive before any reads
        first = rank_markets._worker_session()
        second = rank_markets._worker_session()
        results.append((threading.get_ident(), first, second))

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(a is b for _, a, b in results), (
        "each thread must reuse its own session, not mint a new one per call")
    assert len({id(a) for _, a, _ in results}) == 12, (
        "twelve workers must hold twelve distinct session objects")


def test_the_scoring_pool_creates_one_session_per_worker(monkeypatch):
    """The pool dispatch must route through the session factory, so a
    regression that hands every worker one shared session is caught: the
    factory is invoked once per job, and with one job per worker the run
    creates exactly as many sessions as workers -- never one shared object.
    Before the seam existed, `main` passed a single pooled session straight
    into every `evaluate` call; that shape makes the factory invisible and
    this test fails with `created == []`.

    The stub's `get` sleeps so all twelve workers are held alive inside the
    fetch while the jobs are submitted: the executor never guarantees one
    thread per job, and a worker that finishes instantly goes idle and gets
    reused, collapsing the session count. Sleeping makes the 12-worker
    count deterministic."""
    created = []
    barrier = threading.Barrier(12)

    class _CountingSession:
        def __init__(self):
            created.append(self)

        def get(self, *args, **kwargs):
            try:
                barrier.wait(timeout=2.0)    # hold all 12 workers alive until all dispatched
            except threading.BrokenBarrierError:
                pass
            raise AssertionError("no network in the scoring-pool test")

    monkeypatch.setattr(rank_markets.requests, "Session", _CountingSession)

    jobs = [(1.0, {"tokens": [{"token_id": f"{i}a"},
                               {"token_id": f"{i}b"}]},
             None, "rewards") for i in range(12)]
    out = score_pool(jobs, max_workers=12)

    assert len(created) == 12, (
        f"expected one session per worker, got {len(created)} "
        f"(a shared session would create exactly one)")
    assert len({id(s) for s in created}) == 12
    assert out == [], ("every job fetched through the failing stub and "
                       "evaluate fails closed to no verdict")


# --- DEPTH-GATE TRIAL (U32): the bar is injectable, never silently lowered -

class _StubClobSession:
    """A CLOB that serves one book per token, for the trial-bar gate test."""

    def __init__(self, books):
        self.books = books          # {token_id: {"bids": {...}, "asks": {...}}}

    def get(self, url, params=None, timeout=None):
        if "clob.polymarket.com/book" not in url:
            raise AssertionError(f"unexpected URL {url}")
        b = self.books[params["token_id"]]
        return _StubResponse({
            "bids": [{"price": str(p), "size": str(s)}
                     for p, s in b["bids"].items()],
            "asks": [{"price": str(p), "size": str(s)}
                     for p, s in b["asks"].items()],
        })


def _trial_market():
    """A market whose YES-side top-3 bid depth is ~$770: refused at the
    permanent $1,000 bar, admitted under a $750 trial bar."""
    return {
        "condition_id": "0xtrial",
        "question": "Team A vs Team B",
        "market_slug": "mlb-trial-slate",
        "category": "Sports",
        "market_type": "Moneyline",
        "market_group": "",
        "series_title": "MLB",
        "event_title": "MLB",
        "tokens": [{"token_id": "0xaa"}, {"token_id": "0xbb"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "minimum_tick_size": 0.001,
        "end_date_iso": "2026-08-20T00:00:00Z",
        "_volume_24h": 1_000_000.0,
        "_spread": 0.02,
    }


def test_evaluate_gates_on_the_trial_depth_bar_when_passed():
    """The near-miss shape the controlled trial exists to adopt: ~$770 of
    top-3 bid depth is refused at the permanent $1,000 bar and admitted under
    a $750 trial bar. None (the normal path) must keep the permanent bar, so
    a trial can never leak into a normal rank by accident."""
    books = {
        "0xaa": {"bids": {0.49: 600, 0.48: 600, 0.47: 400},
                 "asks": {0.51: 600}},
        "0xbb": {"bids": {0.49: 3000, 0.48: 3000, 0.47: 3000},
                  "asks": {0.51: 600}},
    }
    s = _StubClobSession(books)
    m = _trial_market()

    refused = rank_markets.evaluate(s, 100.0, m, 1_000_000.0, "rewards")
    assert refused is not None and not refused["eligible"]
    assert "top-3 bid depth" in refused["reject_reason"]

    admitted = rank_markets.evaluate(s, 100.0, m, 1_000_000.0, "rewards",
                                     min_depth_usd=750.0)
    assert admitted is not None and admitted["eligible"]


def test_effective_depth_bar_resolution_cli_over_config_over_default(monkeypatch):
    """The bar a run gates on comes from --trial-depth first, then the config
    trial (env HUNTER_DEPTH_TRIAL_USD), then the permanent value; a
    non-positive trial is a mistake, not a signal, and falls back."""
    base = load_cfg()
    assert rank_markets._effective_depth_bar(None) == \
        base.select_min_top3_depth_usd

    class _Cfg:
        select_min_top3_depth_usd_trial = 750.0

    monkeypatch.setattr(rank_markets, "_CFG", _Cfg())
    assert rank_markets._effective_depth_bar(None) == 750.0
    assert rank_markets._effective_depth_bar(500.0) == 500.0   # CLI wins
    assert rank_markets._effective_depth_bar(0.0) == \
        base.select_min_top3_depth_usd                          # invalid -> permanent


def test_config_env_sets_the_trial_bar_without_touching_the_permanent_one(monkeypatch):
    monkeypatch.setenv("HUNTER_DEPTH_TRIAL_USD", "600")
    cfg = load_cfg()
    assert cfg.select_min_top3_depth_usd_trial == 600.0
    assert cfg.select_min_top3_depth_usd == 1000.0


def test_pipeline_snapshot_records_the_trial_bar(monkeypatch, tmp_path):
    """The staging contract: a trial run's snapshot says which bar it gated on
    and flags it as a trial, so the dashboard's funnel can never silently show
    a loosened gate as the standing contract. The permanent path records the
    permanent bar with no trial flag."""
    monkeypatch.setattr(rank_markets, "RUN", tmp_path)

    rank_markets._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census="", gates="", attempted=0, rejected=0,
        verdicts={}, depth_gate_usd=750.0, trial_depth_usd=750.0)
    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["depth_gate_usd"] == 750.0
    assert snap["trial_depth_usd"] == 750.0

    rank_markets._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census="", gates="", attempted=0, rejected=0,
        verdicts={}, depth_gate_usd=1000.0, trial_depth_usd=None)
    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["depth_gate_usd"] == 1000.0
    assert snap["trial_depth_usd"] is None


# --- VOLUME-GATE TRIAL (U36): the volume bar is injectable, same contract ---

def _volume_trial_market():
    """A market with $210k/24h volume (under the permanent $250k bar, over a
    $200k trial bar) and deep-enough books on BOTH sides to clear the depth
    gate -- the measured shape of the volume would-fund population
    ($235-242k, real books, just under the bar)."""
    return {
        "condition_id": "0xvol",
        "question": "Team C vs Team D",
        "market_slug": "tennis-volume-trial",
        "category": "Sports",
        "market_type": "Moneyline",
        "market_group": "",
        "series_title": "ATP",
        "event_title": "ATP",
        "tokens": [{"token_id": "0xcc"}, {"token_id": "0xdd"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "minimum_tick_size": 0.001,
        "end_date_iso": "2026-08-25T00:00:00Z",
        "_volume_24h": 210_000.0,
        "_spread": 0.02,
    }


def test_tradable_gates_on_the_trial_volume_bar_when_passed():
    ok, why = tradable(volume_24h=210_000.0, days=3.0)
    assert not ok and "24h volume" in why
    ok2, why2 = tradable(volume_24h=210_000.0, days=3.0,
                         min_volume_usd=200_000.0)
    assert ok2 and not why2


def test_evaluate_gates_on_the_trial_volume_bar_when_passed():
    """The volume would-fund shape: $210k/24h is refused at the permanent
    $250k bar and admitted under a $200k trial bar. None (the normal path)
    keeps the permanent bar, so a trial can never leak into a normal rank."""
    books = {
        "0xcc": {"bids": {0.49: 3000, 0.48: 3000, 0.47: 3000},
                 "asks": {0.51: 3000}},
        "0xdd": {"bids": {0.49: 3000, 0.48: 3000, 0.47: 3000},
                 "asks": {0.51: 3000}},
    }
    s = _StubClobSession(books)
    m = _volume_trial_market()

    refused = rank_markets.evaluate(s, 500.0, m, 210_000.0, "rewards")
    assert refused is not None and not refused["eligible"]
    assert "24h volume" in refused["reject_reason"]

    admitted = rank_markets.evaluate(s, 500.0, m, 210_000.0, "rewards",
                                     min_volume_usd=200_000.0)
    assert admitted is not None and admitted["eligible"]


def test_effective_volume_bar_resolution_cli_over_config_over_default(monkeypatch):
    """Same precedence as the depth bar: --trial-volume first, then the config
    trial (env HUNTER_VOLUME_TRIAL_USD), then the permanent value; a
    non-positive trial is a mistake, not a signal, and falls back."""
    base = load_cfg()
    assert rank_markets._effective_volume_bar(None) == \
        base.select_min_volume_24h_usd

    class _Cfg:
        select_min_volume_24h_usd_trial = 200_000.0

    monkeypatch.setattr(rank_markets, "_CFG", _Cfg())
    assert rank_markets._effective_volume_bar(None) == 200_000.0
    assert rank_markets._effective_volume_bar(150_000.0) == 150_000.0   # CLI wins
    assert rank_markets._effective_volume_bar(0.0) == \
        base.select_min_volume_24h_usd                                  # invalid -> permanent


def test_config_env_sets_the_volume_trial_bar_without_touching_the_permanent_one(monkeypatch):
    monkeypatch.setenv("HUNTER_VOLUME_TRIAL_USD", "200000")
    cfg = load_cfg()
    assert cfg.select_min_volume_24h_usd_trial == 200_000.0
    assert cfg.select_min_volume_24h_usd == 250_000.0


def test_config_env_overrides_the_allocator_marginal_floor(monkeypatch):
    """U36f: the 2%/day marginal-return floor defunded the entire eligible
    universe (all 7 real-book markets measure 0.04-1.84%/day first-dollar), so
    the operator re-armed it lower via HUNTER_MARGINAL_FLOOR. The env must
    override the permanent floor without touching anything else."""
    monkeypatch.setenv("HUNTER_MARGINAL_FLOOR", "0.005")
    cfg = load_cfg()
    assert cfg.marginal_return_floor == 0.005

    monkeypatch.delenv("HUNTER_MARGINAL_FLOOR", raising=False)
    assert load_cfg().marginal_return_floor == 0.02  # permanent default intact


def test_pipeline_snapshot_records_the_volume_trial_bar(monkeypatch, tmp_path):
    """Same staging contract as depth: a trial run's snapshot says which
    volume bar it gated on and flags it as a trial."""
    monkeypatch.setattr(rank_markets, "RUN", tmp_path)
    rank_markets._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census="", gates="", attempted=0, rejected=0,
        verdicts={}, volume_gate_usd=200_000.0, trial_volume_usd=200_000.0)
    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["volume_gate_usd"] == 200_000.0
    assert snap["trial_volume_usd"] == 200_000.0


# --- the script and the fleet must not drift --------------------------------

def test_the_script_gate_and_the_fleet_gate_agree():
    """Same drift risk the payout floor has: a ranker that admits markets the
    fleet would refuse writes a universe the fleet cannot quote."""
    base = load_cfg()
    assert MIN_VOLUME_24H == base.select_min_volume_24h_usd
    assert MAX_DAYS_TO_RESOLVE == base.select_max_days_to_resolve
