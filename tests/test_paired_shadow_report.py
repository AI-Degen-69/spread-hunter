from __future__ import annotations

import json
import sqlite3

import pytest

from core_brain.order_registry import CloseRecord, FillRecord, OrderRecord, OrderRegistry
from core_brain.paired_shadow import (
    ensure_paired_shadow_tables,
    record_paired_equity_mark,
    record_paired_market_admission,
    record_paired_market_visit,
    record_paired_order_attribution,
    record_paired_run_finish,
    record_paired_run_start,
    record_paired_snapshot,
)
from scripts.paired_depth_report import analyze_paired_depth


START = 1_700_000_000.0
END = START + 3600.0


def _order(registry, *, local_id, run_id, cid, token, price, shares, pair_id,
           posted_at, status="filled"):
    registry.create_order(OrderRecord(
        id=local_id, order_id=f"shadow-{local_id}", condition_id=cid,
        token_id=token, side="BUY", price=price, original_size=shares,
        status=status, posted_ts=int(posted_at * 1000),
        last_polled_ts=int(posted_at * 1000), pair_id=pair_id,
        run_id=run_id,
    ))


def _fill(registry, *, local_id, run_id, trade_id, shares, price, filled_at):
    registry.record_fill(FillRecord(
        trade_id=trade_id, order_uuid=local_id, size=shares, price=price,
        venue_ts=int(filled_at * 1000), recorded_ts=int(filled_at * 1000),
        run_id=run_id,
    ))


def _arm_db(
    path, *, arm: str, run_id: str, outcomes: dict[str, float],
    missing_cluster: bool = False, omit_terminal_market_mark: bool = False,
):
    registry = OrderRegistry(path, run_id=run_id)
    ensure_paired_shadow_tables(path)
    cutoff = 500.0 if arm == "control" else 250.0
    record_paired_run_start(
        path, run_id=run_id, arm=arm, cutoff_usd=cutoff,
        starting_bankroll_usd=100.0, started_at=START,
        planned_minutes=60.0,
    )

    for index, (family, pnl) in enumerate(outcomes.items()):
        cid = f"{arm}-{index}"
        snapshot = "snapshot-common"
        spec = {
            "cid": cid,
            "paired_depth_arm": arm,
            "paired_depth_cutoff_usd": cutoff,
            "paired_depth_snapshot_id": snapshot,
            "event_id": "" if missing_cluster else family,
            "event_slug": "",
            "event_title": family,
            "slug": cid,
        }
        if missing_cluster:
            # Low-level insertion creates intentionally unmeasurable data so
            # the report's refusal is tested independently of feed validation.
            record_paired_snapshot(
                path, run_id=run_id, snapshot_id=snapshot, observed_at=START + 1)
            record_paired_market_admission(
                path, run_id=run_id, condition_id=cid,
                snapshot_id=snapshot, arm=arm, cutoff_usd=cutoff,
                event_cluster_id="", event_cluster_source="missing",
                event_title=family, market_slug=cid, selected_at=START + 1,
            )
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """INSERT INTO shadow_paired_market_tokens
                       (run_id, condition_id, up_token_id, down_token_id, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (run_id, cid, f"{cid}-up", f"{cid}-down", START + 1),
                )
                conn.commit()
        else:
            record_paired_market_visit(
                path, run_id=run_id, arm=arm, cutoff_usd=cutoff,
                spec=spec, up_token_id=f"{cid}-up", down_token_id=f"{cid}-down",
                observed_at=START + 1,
            )

        # A completed one-share pair with per-leg fills and cost removal.
        pair_id = f"{run_id}-pair-{index}"
        up_id = f"{run_id}-up-order-{index}"
        down_id = f"{run_id}-down-order-{index}"
        _order(registry, local_id=up_id, run_id=run_id, cid=cid,
               token=f"{cid}-up", price=0.499, shares=1.0,
               pair_id=pair_id, posted_at=START + 2)
        _order(registry, local_id=down_id, run_id=run_id, cid=cid,
               token=f"{cid}-down", price=0.499, shares=1.0,
               pair_id=pair_id, posted_at=START + 2)
        if not missing_cluster:
            record_paired_order_attribution(
                path, run_id=run_id, local_id=up_id,
                condition_id=cid, pair_id=pair_id)
            record_paired_order_attribution(
                path, run_id=run_id, local_id=down_id,
                condition_id=cid, pair_id=pair_id)
        _fill(registry, local_id=up_id, run_id=run_id,
              trade_id=f"{up_id}-fill", shares=1.0, price=0.499,
              filled_at=START + 10)
        _fill(registry, local_id=down_id, run_id=run_id,
              trade_id=f"{down_id}-fill", shares=1.0, price=0.499,
              filled_at=START + 10)
        registry.log_close(CloseRecord(
            ts=START + 100.0, condition_id=cid, method="shadow_merge", shares=1.0,
            cost_basis=0.998, proceeds=1.0, realized_pnl=pnl,
            up_cost_removed=0.499, dn_cost_removed=0.499, run_id=run_id,
        ))

        # A separately attributed single-buy exit has a real fill-notional
        # denominator, so the registered loss-rate guard is actually measured.
        exit_id = f"{run_id}-exit-order-{index}"
        exit_pair = f"{pair_id}-exit"
        _order(registry, local_id=exit_id, run_id=run_id, cid=cid,
               token=f"{cid}-up", price=0.5, shares=1.0,
               pair_id=exit_pair, posted_at=START + 102)
        if not missing_cluster:
            record_paired_order_attribution(
                path, run_id=run_id, local_id=exit_id,
                condition_id=cid, pair_id=exit_pair)
        _fill(registry, local_id=exit_id, run_id=run_id,
              trade_id=f"{exit_id}-fill", shares=1.0, price=0.5,
              filled_at=START + 105)
        registry.log_close(CloseRecord(
            ts=START + 110.0, condition_id=cid, method="single_buy_exit", shares=1.0,
            cost_basis=0.5, proceeds=0.5, realized_pnl=0.0,
            up_price=0.5, up_cost_removed=0.5, dn_cost_removed=0.0,
            run_id=run_id,
        ))

        # A one-share UP position remains open through the endpoint; its bid
        # liquidation value is modeled as $0.40 against $0.40 cost basis.
        open_id = f"{run_id}-open-order-{index}"
        open_pair = f"{pair_id}-open"
        _order(registry, local_id=open_id, run_id=run_id, cid=cid,
               token=f"{cid}-up", price=0.4, shares=1.0,
               pair_id=open_pair, posted_at=START + 200)
        if not missing_cluster:
            record_paired_order_attribution(
                path, run_id=run_id, local_id=open_id,
                condition_id=cid, pair_id=open_pair)
        _fill(registry, local_id=open_id, run_id=run_id,
              trade_id=f"{open_id}-fill", shares=1.0, price=0.4,
              filled_at=START + 210)

        if not (omit_terminal_market_mark and index == 0):
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """INSERT INTO shadow_paired_market_marks
                       (run_id, ts, condition_id, event_cluster_id,
                        unrealized_pnl, committed_open_usd, valid, missing_reason)
                       VALUES (?, ?, ?, ?, 0.0, 0.4, 1, '')""",
                    (run_id, END, cid,
                     "" if missing_cluster else f"gamma-event:{family}"),
                )
                conn.commit()

    with sqlite3.connect(path) as conn:
        for ts, equity in (
            (START + 1800.0, 99.0),
            (END, 100.0 + sum(outcomes.values())),
        ):
            conn.execute(
                """INSERT INTO shadow_paired_equity_marks
                   (run_id, ts, equity_usd, realized_pnl, unrealized_pnl,
                    committed_open_usd, valid, missing_conditions_json)
                   VALUES (?, ?, ?, ?, 0.0, 0.4, 1, '[]')""",
                (run_id, ts, equity, equity - 100.0),
            )
        conn.commit()
    record_paired_run_finish(path, run_id=run_id, finished_at=END)
    return registry


def _report(
    control_db, treatment_db, *, pilot_paired_sigma_usd_per_cluster=None,
    max_mark_age_sec=3600,
):
    return analyze_paired_depth(
        control_db=control_db, treatment_db=treatment_db,
        control_run_id="control-run", treatment_run_id="treatment-run",
        bootstrap_replicates=2000, max_mark_age_sec=max_mark_age_sec,
        pilot_paired_sigma_usd_per_cluster=pilot_paired_sigma_usd_per_cluster,
    )


def _chi2_ppf_reference(df: int, probability: float) -> float:
    """Independent chi-square quantile for cross-checking the report's bound.

    Regularized lower incomplete gamma by power series (exact for x < a+1,
    which holds at the 25th percentile) with bisection. Written here on
    purpose, separately from scripts/paired_depth_report.py: a test that
    imports the code under test cannot catch a wrong formula in it.
    """
    import math

    def gamma_fn(a):
        if a == int(a):
            return float(math.factorial(int(a) - 1))
        m = int(a - 0.5)
        return (math.factorial(2 * m) * math.sqrt(math.pi)
                / (4.0 ** m * math.factorial(m)))

    def P(a, x):
        coef, total = 1.0 / a, 0.0
        for n in range(300):
            if n > 0:
                coef *= x / (a + n)
            total += coef
        return math.exp(-x) * (x ** a) * total / gamma_fn(a)

    a = df / 2.0
    lo, hi = 1e-12, float(df) + 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if P(a, mid / 2.0) < probability:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _two_arms(tmp_path, *, control=None, treatment=None, **kwargs):
    control_db = tmp_path / "control.db"
    treatment_db = tmp_path / "treatment.db"
    _arm_db(
        control_db, arm="control", run_id="control-run",
        outcomes=control or {"event:a": 0.002, "event:b": -0.001},
        **kwargs,
    )
    _arm_db(
        treatment_db, arm="treatment", run_id="treatment-run",
        outcomes=treatment or {"event:a": 0.005, "event:b": 0.003},
        **kwargs,
    )
    return control_db, treatment_db


def test_report_is_inconclusive_for_a_run_that_never_finished(tmp_path):
    """A killed shadow loop must not crash the analyzer.

    A loop that crashes or is stopped never reaches
    `record_paired_run_finish`, so its row keeps `status='running'` and
    `finished_at=NULL`. The operator then gets a report, not a TypeError out
    of `float(None)` -- the documented outcome for a run that cannot be
    measured is `inconclusive` plus the reason.
    """
    control_db, treatment_db = _two_arms(tmp_path)
    for path, run_id in ((control_db, "control-run"), (treatment_db, "treatment-run")):
        conn = sqlite3.connect(path)
        conn.execute(
            "UPDATE shadow_paired_runs SET status='running', finished_at=NULL "
            "WHERE run_id=?", (run_id,))
        conn.commit()
        conn.close()

    report = _report(control_db, treatment_db)

    assert report["measurement_status"] == "inconclusive"
    assert report["decision"] == "inconclusive"
    joined = " ".join(report["limitations"])
    assert "control run is not marked finished" in joined
    assert "treatment run is not marked finished" in joined
    # A zero-length window cannot support a measurement, and saying so is the
    # whole point of returning a report instead of raising.
    assert "no positive overlapping run window" in joined


def test_report_cluster_bootstrap_uses_uplift_sum_and_requires_pilot_variance(tmp_path):
    control_db, treatment_db = _two_arms(tmp_path)

    report = _report(control_db, treatment_db)

    assert report["clusters"]["count"] == 2
    assert report["metrics"]["uplift_usd_per_100_bankroll_per_100h"] == pytest.approx(0.7)
    assert report["clusters"]["paired_differences"] == pytest.approx([0.3, 0.4])
    low, high = report["metrics"]["paired_95pct_cluster_bootstrap_interval"]
    assert 0 < low <= high
    assert report["coverage"]["snapshot_ids_match"] is True
    assert report["power_analysis"]["status"] == "prior_missing"
    assert "independent paired pilot variance" in " ".join(report["limitations"])
    assert report["decision"] == "inconclusive"


def test_report_can_measure_a_powered_screen_but_retains_control_below_uplift_bar(tmp_path):
    control_db, treatment_db = _two_arms(tmp_path)

    report = _report(control_db, treatment_db, pilot_paired_sigma_usd_per_cluster=0.01)

    assert report["measurement_status"] == "measured"
    assert report["power_analysis"]["estimated_clusters_required"] == 2
    assert report["registered_checks"]["paired_95pct_interval_above_zero"] is True
    assert report["registered_checks"]["single_buy_exit_loss_rate_not_worse"] is True
    assert report["single_buy_exit_loss_rate"]["control"] == 0.0
    assert report["single_buy_exit_loss_rate"]["treatment"] == 0.0
    assert report["metrics"]["control_max_drawdown_usd_per_100_bankroll"] == pytest.approx(1.0)
    assert report["metrics"]["treatment_max_drawdown_usd_per_100_bankroll"] == pytest.approx(1.0)
    assert report["decision"] == "retain_control"


def test_report_refuses_verdict_when_event_family_attribution_is_missing(tmp_path):
    control_db, treatment_db = _two_arms(
        tmp_path, control={"event:a": 0.002}, treatment={"event:a": 0.005},
        missing_cluster=True,
    )

    report = _report(control_db, treatment_db, pilot_paired_sigma_usd_per_cluster=0.01)

    assert report["measurement_status"] == "inconclusive"
    assert "event-family attribution" in " ".join(report["limitations"])
    assert report["decision"] == "inconclusive"


def test_report_refuses_missing_terminal_market_mark(tmp_path):
    control_db, treatment_db = _two_arms(
        tmp_path, control={"event:a": 0.002}, treatment={"event:a": 0.005},
        omit_terminal_market_mark=True,
    )

    report = _report(control_db, treatment_db, pilot_paired_sigma_usd_per_cluster=0.01)

    assert report["measurement_status"] == "inconclusive"
    assert "market marks missing at window end" in " ".join(report["limitations"])


def test_report_fails_closed_on_stale_market_marks_and_equity_gaps(tmp_path):
    control_db, treatment_db = _two_arms(tmp_path)
    for path in (control_db, treatment_db):
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE shadow_paired_market_marks SET ts=?",
                (END - 30.0,),
            )
            conn.commit()

    report = _report(
        control_db, treatment_db, pilot_paired_sigma_usd_per_cluster=0.01,
        max_mark_age_sec=10,
    )

    assert report["measurement_status"] == "inconclusive"
    joined = " ".join(report["limitations"])
    assert "market marks missing at window end" in joined
    assert "equity mark" in joined


def test_report_refuses_a_snapshot_set_mismatch(tmp_path):
    control_db, treatment_db = _two_arms(tmp_path)
    record_paired_snapshot(
        treatment_db, run_id="treatment-run", snapshot_id="snapshot-unmatched",
        observed_at=START + 1800,
    )

    report = _report(control_db, treatment_db, pilot_paired_sigma_usd_per_cluster=0.01)

    assert report["measurement_status"] == "inconclusive"
    assert "did not observe the same feed snapshots" in " ".join(report["limitations"])


def test_report_publishes_the_conservative_sigma_upper_bound(tmp_path):
    """The pilot doc's selection rule, computed: with k clusters the report
    shows the 75% upper bound s*sqrt((k-1)/chi2_0.25(k-1)), and it is strictly
    larger than the sigma so the power calculation can only get harder."""
    import math

    control_db, treatment_db = _two_arms(tmp_path)
    sigma = 0.01

    report = _report(control_db, treatment_db, pilot_paired_sigma_usd_per_cluster=sigma)

    k = report["clusters"]["count"]
    assert k == 2
    bound = report["power_analysis"]["paired_sigma_75pct_upper_bound"]
    expected = sigma * math.sqrt(
        (k - 1) / _chi2_ppf_reference(k - 1, 0.25))
    assert bound == pytest.approx(expected, rel=1e-6)
    assert bound > sigma


def test_report_omits_the_sigma_bound_without_two_clusters(tmp_path):
    control_db, treatment_db = _two_arms(
        tmp_path, control={"event:a": 0.002}, treatment={"event:a": 0.005})

    report = _report(control_db, treatment_db, pilot_paired_sigma_usd_per_cluster=0.01)

    assert report["power_analysis"]["paired_sigma_75pct_upper_bound"] is None
    assert "fewer than two event-family clusters" in " ".join(report["limitations"])


def test_report_refuses_a_snapshot_only_one_arm_ever_observed(tmp_path):
    """A snapshot seen by one arm outside the common window is still a mismatch.

    The old window-intersection read let an arm trade a selection the other
    never saw, as long as the divergence fell before the second arm started --
    exactly the pairing the experiment exists to hold.
    """
    control_db, treatment_db = _two_arms(tmp_path)
    # Observed before the common window opens at START: the old check would
    # have ignored it entirely.
    record_paired_snapshot(
        control_db, run_id="control-run", snapshot_id="snapshot-early",
        observed_at=START - 60,
    )

    report = _report(control_db, treatment_db, pilot_paired_sigma_usd_per_cluster=0.01)

    assert report["measurement_status"] == "inconclusive"
    assert "did not observe the same feed snapshots" in " ".join(report["limitations"])
    assert report["coverage"]["unmatched_snapshots"] == ["snapshot-early"]


def test_report_is_read_only_and_cli_emits_json(tmp_path, capsys):
    control_db, treatment_db = _two_arms(tmp_path)
    import gc
    import hashlib

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    # The stores are written in WAL mode, so the main db file is not the whole
    # story: rows live in `-wal` until a checkpoint folds them in, and that
    # happens whenever a writer connection is collected -- which the report
    # itself never triggers, but an unrelated test's GC can. Force the
    # checkpoint first so this measures the report's writes, not the timing of
    # a finalizer. Without this the assertion is order-dependent: it passes in
    # isolation and fails whenever another test has already run.
    gc.collect()
    before = (digest(control_db), digest(treatment_db))
    assert before == (digest(control_db), digest(treatment_db))

    from scripts.paired_depth_report import main
    assert main([
        "--control-db", str(control_db), "--treatment-db", str(treatment_db),
        "--control-run-id", "control-run", "--treatment-run-id", "treatment-run",
        "--bootstrap-replicates", "1000", "--max-mark-age-sec", "3600",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["arms"]["control"]["run_id"] == "control-run"
    assert payload["arms"]["treatment"]["run_id"] == "treatment-run"
    assert payload["measurement_status"] == "inconclusive"
    assert (digest(control_db), digest(treatment_db)) == before


def test_market_visit_persists_tokens_and_refuses_missing_snapshot_before_order(tmp_path):
    path = tmp_path / "paired.db"
    registry = OrderRegistry(path, run_id="control-run")
    ensure_paired_shadow_tables(path)
    record_paired_run_start(
        path, run_id="control-run", arm="control", cutoff_usd=500.0,
        starting_bankroll_usd=100.0, started_at=START, planned_minutes=60.0,
    )
    with pytest.raises(RuntimeError, match="no snapshot"):
        record_paired_market_visit(
            path, run_id="control-run", arm="control", cutoff_usd=500.0,
            spec={"cid": "market-1", "paired_depth_arm": "control",
                  "paired_depth_cutoff_usd": 500.0, "event_id": "event-1"},
            up_token_id="up", down_token_id="down", observed_at=START + 1,
        )

    assert registry.get_all_orders() == []
    with sqlite3.connect(path) as conn:
        event = conn.execute(
            "SELECT kind FROM shadow_paired_feed_events WHERE run_id='control-run'"
        ).fetchone()
    assert event == ("missing_snapshot",)


def test_paired_equity_mark_replays_merge_and_values_open_inventory(tmp_path):
    path = tmp_path / "mark.db"
    run_id = "mark-run"
    registry = OrderRegistry(path, run_id=run_id)
    ensure_paired_shadow_tables(path)
    record_paired_run_start(
        path, run_id=run_id, arm="control", cutoff_usd=500.0,
        starting_bankroll_usd=100.0, started_at=START, planned_minutes=1.0,
    )
    spec = {
        "cid": "market", "paired_depth_arm": "control",
        "paired_depth_cutoff_usd": 500.0,
        "paired_depth_snapshot_id": "snapshot",
        "event_id": "family", "event_title": "family",
    }
    record_paired_market_visit(
        path, run_id=run_id, arm="control", cutoff_usd=500.0,
        spec=spec, up_token_id="up", down_token_id="down",
        observed_at=START + 1,
    )
    for side, token, price in (("up", "up", 0.45), ("down", "down", 0.45)):
        local_id = f"merge-{side}"
        _order(registry, local_id=local_id, run_id=run_id, cid="market",
               token=token, price=price, shares=1.0, pair_id="pair",
               posted_at=START + 2)
        record_paired_order_attribution(
            path, run_id=run_id, local_id=local_id,
            condition_id="market", pair_id="pair")
        _fill(registry, local_id=local_id, run_id=run_id,
              trade_id=f"{local_id}-fill", shares=1.0, price=price,
              filled_at=START + 3)
    registry.log_close(CloseRecord(
        ts=START + 4, condition_id="market", method="shadow_merge", shares=1.0,
        cost_basis=0.9, proceeds=1.0, realized_pnl=0.1,
        up_cost_removed=0.45, dn_cost_removed=0.45, run_id=run_id,
    ))
    open_id = "open-up"
    _order(registry, local_id=open_id, run_id=run_id, cid="market",
           token="up", price=0.4, shares=1.0, pair_id="open-pair",
           posted_at=START + 5)
    record_paired_order_attribution(
        path, run_id=run_id, local_id=open_id,
        condition_id="market", pair_id="open-pair")
    _fill(registry, local_id=open_id, run_id=run_id,
          trade_id="open-up-fill", shares=1.0, price=0.4,
          filled_at=START + 6)

    marked = record_paired_equity_mark(
        registry, path, run_id=run_id, starting_bankroll_usd=100.0,
        book_fn=lambda _host, _token: {"bids": {0.4: 1.0}},
        clob_host="https://example.invalid", ts=START + 7,
    )

    assert marked["valid"] is True
    assert marked["realized_pnl"] == pytest.approx(0.1)
    assert marked["unrealized_pnl"] == pytest.approx(0.0)
    assert marked["committed_open_usd"] == pytest.approx(0.4)
    assert marked["equity_usd"] == pytest.approx(100.1)

    registry.log_close(CloseRecord(
        ts=START + 8, condition_id="market", method="shadow_settlement",
        shares=1.0, cost_basis=0.4, proceeds=1.0, realized_pnl=0.6,
        run_id=run_id,
    ))
    settled = record_paired_equity_mark(
        registry, path, run_id=run_id, starting_bankroll_usd=100.0,
        book_fn=lambda _host, _token: {},
        clob_host="https://example.invalid", ts=START + 9,
    )
    assert settled["valid"] is True
    assert settled["unrealized_pnl"] == pytest.approx(0.0)
    assert settled["committed_open_usd"] == pytest.approx(0.0)
    assert settled["equity_usd"] == pytest.approx(100.7)


def test_paired_order_requires_feed_attribution_and_cleans_failed_registry_insert(
        tmp_path, monkeypatch):
    from core_brain.config import load
    from core_brain.paired_shadow import PairedShadowError
    from core_brain.quotes import QuoteIntent
    from core_brain.shadow_exec import record_submit

    path = tmp_path / "submit.db"
    run_id = "submit-run"
    registry = OrderRegistry(path, run_id=run_id)
    ensure_paired_shadow_tables(path)
    registry.paired_context = {
        "db_path": path, "run_id": run_id, "arm": "control", "cutoff_usd": 500.0,
    }
    intent = QuoteIntent(side="UP", token_id="up", price=0.4, size=1.0,
                         mid=0.5, edge_vs_mid=0.1)
    with pytest.raises(PairedShadowError, match="no paired admission"):
        record_submit(object(), registry, type("Market", (), {
            "condition_id": "market", "market_slug": "market",
        })(), [intent], load(), db_path=path,
                        book_fn=lambda _host, _token: {"bids": {}})
    assert registry.get_all_orders() == []

    record_paired_market_visit(
        path, run_id=run_id, arm="control", cutoff_usd=500.0,
        spec={"cid": "market", "paired_depth_arm": "control",
              "paired_depth_cutoff_usd": 500.0,
              "paired_depth_snapshot_id": "snapshot", "event_id": "family"},
        up_token_id="up", down_token_id="down", observed_at=START + 1,
    )

    def fail_create(_order):
        raise RuntimeError("registry insert failed")

    monkeypatch.setattr(registry, "create_order", fail_create)
    with pytest.raises(RuntimeError, match="registry insert failed"):
        record_submit(object(), registry, type("Market", (), {
            "condition_id": "market", "market_slug": "market",
        })(), [intent], load(), db_path=path,
                        book_fn=lambda _host, _token: {"bids": {}})
    with sqlite3.connect(path) as conn:
        orphan_count = conn.execute(
            "SELECT COUNT(*) FROM shadow_paired_orders WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    assert orphan_count == 0


def test_market_token_mapping_cannot_change_mid_run(tmp_path):
    from core_brain.paired_shadow import PairedShadowError

    path = tmp_path / "tokens.db"
    ensure_paired_shadow_tables(path)
    record_paired_run_start(
        path, run_id="token-run", arm="control", cutoff_usd=500.0,
        starting_bankroll_usd=100.0, started_at=START, planned_minutes=1.0,
    )
    spec = {
        "cid": "market", "paired_depth_arm": "control",
        "paired_depth_cutoff_usd": 500.0,
        "paired_depth_snapshot_id": "snapshot", "event_id": "family",
    }
    record_paired_market_visit(
        path, run_id="token-run", arm="control", cutoff_usd=500.0,
        spec=spec, up_token_id="up", down_token_id="down", observed_at=START + 1)
    with pytest.raises(PairedShadowError, match="changed its UP/DOWN token mapping"):
        record_paired_market_visit(
            path, run_id="token-run", arm="control", cutoff_usd=500.0,
            spec={**spec, "paired_depth_snapshot_id": "snapshot-2"},
            up_token_id="different-up", down_token_id="down", observed_at=START + 2)
    with sqlite3.connect(path) as conn:
        kinds = [row[0] for row in conn.execute(
            "SELECT kind FROM shadow_paired_feed_events WHERE run_id='token-run'")]
    assert kinds == ["changed_market_tokens"]
