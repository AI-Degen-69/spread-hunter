"""Naming contract (docs/agents/naming.md): one grammar per kind of artifact.

The repo accumulated four naming generations in the same families -- `stats_21-10_11-39_shadow-01.db`
(no year, does not sort), `371_tournament_03_aggressive_20261017-184837.db`,
`shadow-9667ed22d077.jsonl`, and ten explainers split between `471-...`,
`pr-554-...`, `entry-gate-job` and `skew_and_taker_cross_hebrew`. Each was
internally reasonable and none of them agreed. This file pins the two grammars
so the next writer inherits a rule instead of inventing a fifth.
"""
from __future__ import annotations

import datetime
import pathlib
import re

from core_brain.naming import STAMP_FORMAT, stamp
from core_brain.statistics_store import StatisticsStore

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"

#: `2026-10-23T08-47` -- ISO 8601 basic, colon replaced for Windows.
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}$")

#: G1 + G2 + plain slugs: optional date, optional id, kebab-case, known extension.
DOC_NAME_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}-)?(?:\d+-)?[a-z0-9]+(?:-[a-z0-9]+)*\.(?:md|html|json|txt|yaml)$"
)


def test_the_stamp_is_iso_and_sorts():
    assert STAMP_RE.match(stamp())
    assert STAMP_FORMAT == "%Y-%m-%dT%H-%M"

    # The old `%d-%m_%H-%M` stamp sorted 4 November before 21 October and
    # carried no year, so two different Octobers collided.
    october = stamp(datetime.datetime(2026, 10, 21, 9, 0))
    november = stamp(datetime.datetime(2026, 11, 4, 9, 0))
    assert october < november
    assert stamp(datetime.datetime(2026, 10, 21, 9, 0, 30)) == "2026-10-21T09-00"


def test_a_stats_store_name_follows_the_state_grammar(tmp_path):
    store = StatisticsStore.create(tmp_path, stamp(datetime.datetime(2026, 10, 23, 8, 47)), "shadow-01")
    assert store.path.name == "stats_shadow-01_2026-10-23T08-47.db"
    # `stats_` is load-bearing: data_retention.py classifies the rehearsal family by it.
    assert store.path.name.startswith("stats_")


def test_every_document_name_follows_a_grammar():
    offenders: list[str] = []
    for path in sorted(DOCS_DIR.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        if not DOC_NAME_RE.match(path.name):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert not offenders, (
        "doc names must be kebab-case, with an optional YYYY-MM-DD- or <id>- "
        "prefix (docs/agents/naming.md):\n" + "\n".join(offenders)
    )
