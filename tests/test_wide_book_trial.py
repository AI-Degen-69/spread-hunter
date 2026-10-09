"""The wide-book trial knob, and the refusal that keeps it off the money path.

`HUNTER_WIDE_BOOK_TRIAL` widens `max_book_spread`, which is not a preference
but the gate that refuses a book where a fill may leave a leg that cannot be
closed. The two config modules carry independent copies of the parse and the
refusal -- the ranker loads `scoring.config`, the fleet loads
`core_brain.config` -- so both are pinned here, together, to the same
behaviour.
"""
from __future__ import annotations

import importlib

import pytest

from core_brain import config as core_config
from core_brain import rehearsal
from scoring import config as scoring_config

BOTH = pytest.mark.parametrize(
    "mod", [core_config, scoring_config], ids=["core_brain", "scoring"])


# --- the refusal --------------------------------------------------------------


@BOTH
def test_trial_is_refused_outside_a_declared_rehearsal(mod):
    # The gate is what the PROCESS can do, not what is on the filesystem.
    with pytest.raises(ValueError) as e:
        mod.resolve_wide_book_trial("0.15")

    assert "declared itself unable to place an order" in str(e.value)


@BOTH
def test_trial_applies_inside_a_declared_rehearsal(mod):
    rehearsal.declare_rehearsal()

    assert mod.resolve_wide_book_trial("0.15") == 0.15


@BOTH
def test_a_key_on_disk_does_not_block_a_rehearsal(mod, monkeypatch):
    # `core_brain.shadow_run` cannot sign whatever sits in `.env`: it builds a
    # credential-free client behind a deny-by-default proxy. Refusing on the
    # key would block the trial exactly where it is safe -- and this machine's
    # own `.env` holds one, which is how the first version of this guard was
    # caught.
    rehearsal.declare_rehearsal()
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0xdeadbeef")

    assert mod.resolve_wide_book_trial("0.15") == 0.15


def test_shadow_run_declares_itself_a_rehearsal():
    # Without this the knob is unreachable from the one quoting entrypoint it
    # exists for, and the trial silently never applies.
    import inspect

    from core_brain import shadow_run

    src = inspect.getsource(shadow_run.main)

    assert "rehearsal.declare_rehearsal()" in src


# --- parsing ------------------------------------------------------------------


@BOTH
def test_unset_trial_is_none(mod):
    assert mod.resolve_wide_book_trial("") is None
    assert mod.resolve_wide_book_trial("   ") is None


@BOTH
def test_unset_trial_does_not_raise_outside_a_rehearsal(mod):
    # `load()` calls this on every start, so a refusal on the empty string
    # would take down a live trader that asked for nothing.
    assert not rehearsal.is_rehearsal()

    assert mod.resolve_wide_book_trial("") is None


@BOTH
def test_trial_refuses_a_value_past_the_bound(mod):
    # Thirty cents apart is not a wide book, it is an absent one.
    rehearsal.declare_rehearsal()

    with pytest.raises(ValueError) as e:
        mod.resolve_wide_book_trial("0.45")

    assert "outside" in str(e.value)


@BOTH
def test_trial_refuses_a_non_finite_value(mod):
    # NaN poisons every comparison it touches: a ceiling set to NaN reads as
    # enforced while permitting anything.
    rehearsal.declare_rehearsal()

    with pytest.raises(ValueError):
        mod.resolve_wide_book_trial("nan")


@BOTH
def test_trial_refuses_a_non_number(mod):
    rehearsal.declare_rehearsal()

    with pytest.raises(ValueError):
        mod.resolve_wide_book_trial("wide")


# --- what load() does with it -------------------------------------------------


def _load_with(mod, monkeypatch, **env):
    for name in ("HUNTER_MARKET", "HUNTER_MAX_SPREAD", "HUNTER_WIDE_BOOK_TRIAL"):
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(mod).load()


@BOTH
def test_load_moves_all_three_ceilings_together(mod, monkeypatch):
    # Any one of the three left at its shipped value makes the trial measure
    # nothing: the ranker never offers the market, `book_health` refuses to
    # quote it, or the reward window refuses the price the bid cap produces.
    rehearsal.declare_rehearsal()

    cfg = _load_with(mod, monkeypatch, HUNTER_WIDE_BOOK_TRIAL="0.15")

    assert cfg.wide_book_trial == 0.15
    assert cfg.max_book_spread == 0.15
    assert cfg.select_max_book_spread == 0.15
    assert cfg.max_spread_from_mid == 0.15


@BOTH
def test_load_leaves_the_ceilings_alone_when_unset(mod, monkeypatch):
    cfg = _load_with(mod, monkeypatch)

    assert cfg.wide_book_trial is None
    assert cfg.max_book_spread == 0.06
    assert cfg.select_max_book_spread == 0.0205
    assert cfg.max_spread_from_mid == 0.045


@BOTH
def test_a_narrow_trial_never_tightens_the_reward_window(mod, monkeypatch):
    # The window is widened only if the trial is wider than it already is. A
    # trial NARROWER than 4.5c is a narrower BOOK ceiling, and must not
    # quietly shrink where we are allowed to quote.
    rehearsal.declare_rehearsal()

    cfg = _load_with(mod, monkeypatch, HUNTER_WIDE_BOOK_TRIAL="0.02")

    assert cfg.max_book_spread == 0.02
    assert cfg.max_spread_from_mid == 0.045


@BOTH
def test_load_refuses_outright_outside_a_rehearsal(mod, monkeypatch):
    # Refusing at config load stops the process before a client is built and
    # long before a share is bought.
    with pytest.raises(ValueError):
        _load_with(mod, monkeypatch, HUNTER_WIDE_BOOK_TRIAL="0.15")


@BOTH
def test_trial_widens_a_venue_supplied_window_rather_than_replacing_it(
        mod, monkeypatch):
    # `HUNTER_MAX_SPREAD` carries the venue's own reward window in cents. The
    # trial takes the wider of the two, so a venue window of 20c survives a
    # 15c trial.
    rehearsal.declare_rehearsal()

    cfg = _load_with(mod, monkeypatch, HUNTER_MARKET="0xabc",
                     HUNTER_MAX_SPREAD="20", HUNTER_WIDE_BOOK_TRIAL="0.15")

    assert cfg.max_book_spread == 0.15
    assert cfg.max_spread_from_mid == 0.20


# --- the gate it exists to move -----------------------------------------------


def test_book_health_refuses_a_wide_book_at_the_shipped_ceiling():
    from core_brain import risk

    cfg = core_config.MakerConfig()
    book = {"best_bid": 0.435, "best_ask": 0.565, "bids": {0.435: 5000.0}}

    health = risk.book_health(book, cfg)

    assert not health.ok
    assert "book too wide 13.0c > 6.0c" in health.reason


def test_book_health_admits_the_same_book_under_the_trial(monkeypatch):
    # The observed refusal in run 145 was exactly this book width. With the
    # trial in force the same book passes, which is the whole point.
    from core_brain import risk

    rehearsal.declare_rehearsal()
    monkeypatch.delenv("HUNTER_MARKET", raising=False)
    monkeypatch.setenv("HUNTER_WIDE_BOOK_TRIAL", "0.15")
    cfg = importlib.reload(core_config).load()
    book = {"best_bid": 0.435, "best_ask": 0.565, "bids": {0.435: 5000.0}}

    health = risk.book_health(book, cfg)

    assert health.ok, health.reason


# --- the ranker frames --------------------------------------------------------


def test_ranker_spread_bar_defaults_to_the_permanent_ceiling():
    # A non-positive trial is a mistake, not a signal: gate on the permanent
    # bar rather than on nothing.
    from scripts import filter_markets as fm

    assert fm._effective_spread_bar(None) == fm.MAX_BOOK_SPREAD
    assert fm._effective_spread_bar(0.0) == fm.MAX_BOOK_SPREAD
    assert fm._effective_spread_bar(-1.0) == fm.MAX_BOOK_SPREAD


def test_ranker_spread_bar_declares_a_rehearsal_and_widens():
    # The ranker places no orders, so the flag itself is the declaration.
    from scripts import filter_markets as fm

    assert not rehearsal.is_rehearsal()

    assert fm._effective_spread_bar(0.15) == 0.15
    assert rehearsal.is_rehearsal()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    """Serves the two books `evaluate` fetches, 13c wide on both legs."""

    # Both legs are 13c wide on purpose: the tests below assert which ceiling
    # the gate was handed, and a 13c book is refused at the shipped 6c and
    # admitted at the 15c trial. Do not narrow these without re-reading them.
    BID, ASK = 0.435, 0.565

    def get(self, url, params=None, timeout=None):
        return _FakeResponse({
            "bids": [{"price": str(self.BID), "size": "5000"}],
            "asks": [{"price": str(self.ASK), "size": "5000"}],
        })


def _wide_market():
    return {
        "condition_id": "0xabc",
        "question": "Team A vs Team B",
        "market_slug": "mlb-a-b",
        "tokens": [{"token_id": "tok_up"}, {"token_id": "tok_dn"}],
        "rewards": {"max_spread": 4.5, "min_size": 5},
    }


def _spread_bar_reaching_the_predicate(monkeypatch, **kwargs):
    """Run `evaluate` against a 13c book and report the bar the gate saw."""
    from scripts import filter_markets as fm

    seen = {}

    def spy(books, depth_bar, spread_bar):
        seen["spread"] = spread_bar
        return False, "stopped at the spy"

    monkeypatch.setattr(fm, "pair_books_allowed", spy)
    fm.evaluate(_FakeSession(), 1.0, _wide_market(),
                volume_24h=500_000.0, source="spread", **kwargs)
    return seen.get("spread")


def test_evaluate_gates_on_the_injected_spread_bar(monkeypatch):
    # The bar has to arrive at the PREDICATE, not just at the entrypoint --
    # every frame from the CLI down, or the flag is decoration.
    assert _spread_bar_reaching_the_predicate(monkeypatch, max_spread=0.15) == 0.15


def test_evaluate_falls_back_to_the_permanent_ceiling(monkeypatch):
    from scripts import filter_markets as fm

    got = _spread_bar_reaching_the_predicate(monkeypatch)

    assert got == fm.MAX_BOOK_SPREAD


def test_score_pool_forwards_the_spread_bar_to_evaluate(monkeypatch):
    # Frame 3 of the same path. `main` resolves the bar and hands it here.
    from scripts import filter_markets as fm

    seen = {}

    def spy(session, rate, m, volume_24h=None, source="rewards", **kw):
        seen.update(kw)
        return None

    monkeypatch.setattr(fm, "evaluate", spy)
    fm.score_pool([(1.0, _wide_market(), 500_000.0, "spread")],
                  session_factory=lambda: _FakeSession(),
                  max_workers=1, max_spread=0.15)

    assert seen.get("max_spread") == 0.15


@pytest.fixture(autouse=True)
def _clean_rehearsal_flag():
    """Every test starts and ends with the flag clear.

    The flag is process-global and nothing in the running system clears it, so
    a test that declared a rehearsal would otherwise license the trial for
    every test after it -- including the ones asserting that it is refused.
    """
    rehearsal.reset_for_test()
    yield
    rehearsal.reset_for_test()


@pytest.fixture(autouse=True)
def _restore_config_modules():
    """Reload both config modules from a clean environment after each test.

    `load()` reads `os.environ` at call time but these tests reload the module
    itself, so a test that left a widened ceiling in a module-level constant
    would leak into everything after it -- including `scripts.filter_markets`,
    which snapshots `select_max_book_spread` at import.
    """
    yield
    importlib.reload(core_config)
    importlib.reload(scoring_config)


def test_pipeline_snapshot_records_the_ceiling_actually_in_force(tmp_path,
                                                                monkeypatch):
    # A trial run that reports the PERMANENT ceiling shows the dashboard funnel
    # a widened safety gate as if it were the standing contract -- the exact
    # failure the depth and volume trials added parameters to prevent.
    import json

    from scripts import filter_markets as fm

    monkeypatch.setattr(fm, "RUN", tmp_path)
    fm._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census={}, gates={}, attempted=0, rejected=0,
        spread_gate=0.15, trial_spread=0.15)

    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))

    assert snap["spread_gate"] == 0.15
    assert snap["trial_spread"] == 0.15


def test_pipeline_snapshot_falls_back_to_the_permanent_ceiling(tmp_path,
                                                               monkeypatch):
    import json

    from scripts import filter_markets as fm

    monkeypatch.setattr(fm, "RUN", tmp_path)
    fm._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census={}, gates={}, attempted=0, rejected=0)

    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))

    assert snap["spread_gate"] == fm.MAX_BOOK_SPREAD
    assert snap["trial_spread"] is None
