"""Regression: the run-id shadow db naming must be recognized as a shadow db.

The menu mints per-run shadow registries as ``NN_shadow_DD-MM_HH-mm.db``
(run-id naming). ``core_brain.kpi._is_pipeline_shadow_db`` decides whether
``report()`` sources its market funnel from the screener's ``pipeline.json``
or falls back to a near-empty runtime funnel. A prefix-only ``startswith(
"shadow_")`` check misses the ``01_shadow_...`` name, so the dashboard showed
zero screener markets despite a fully populated ``pipeline.json``.
"""

from core_brain.kpi import _is_pipeline_shadow_db


def test_recognizes_new_run_id_shadow_db():
    # The canonical shadow-run / statistical run db name (run-id naming).
    assert _is_pipeline_shadow_db("data/01_shadow_30-08_19-52.db")
    assert _is_pipeline_shadow_db("data/09_shadow_29-08_23-50.db")


def test_recognizes_legacy_shadow_db_names():
    # Older eras still produced by the flows / still present in data/.
    assert _is_pipeline_shadow_db("data/shadow_20260830_172714_shadow-0e2b4ee2cb94.db")
    assert _is_pipeline_shadow_db("data/30-08_00-45_shadow-01.db")


def test_recognizes_tournament_shadow_db():
    # Multi-arm tournament run databases (issue-based naming).
    assert _is_pipeline_shadow_db("data/371_tournament_04_aggressive_20261005-043814.db")
    assert _is_pipeline_shadow_db("data/371_tournament_01_control_20261005-043814.db")


def test_rejects_live_and_non_shadow_dbs():
    # The live registry and unrelated/test dbs must NOT be treated as shadow.
    assert not _is_pipeline_shadow_db("data/orders.db")
    assert not _is_pipeline_shadow_db("data/some_other.db")