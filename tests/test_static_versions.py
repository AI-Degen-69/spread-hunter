"""The static-asset ?v= gate (scripts/ci/check_static_versions.py)."""
from __future__ import annotations

import pytest

from scripts.ci.check_static_versions import asset_versions, check_changes

INDEX_V9 = '<script src="/static/app.js?v=9"></script>'
INDEX_V10 = '<script src="/static/app.js?v=10"></script>'
INDEX_UNVERSIONED = '<script src="/static/neon-app.js"></script>'


def test_bumped_version_passes():
    assert check_changes(["app.js"], {"index.html": INDEX_V9},
                         {"index.html": INDEX_V10}) == []


def test_unchanged_version_fails():
    violations = check_changes(["app.js"], {"index.html": INDEX_V9},
                               {"index.html": INDEX_V9})

    assert len(violations) == 1
    assert "app.js" in violations[0] and "?v=9" in violations[0]


def test_unversioned_reference_fails():
    violations = check_changes(["neon-app.js"], {"index.html": INDEX_UNVERSIONED},
                               {"index.html": INDEX_UNVERSIONED})

    assert len(violations) == 1
    assert "neon-app.js" in violations[0]


def test_newly_versioned_asset_passes():
    assert check_changes(["neon-app.js"], {"index.html": INDEX_UNVERSIONED},
                         {"index.html": '<script src="/static/neon-app.js?v=1">'
                                        "</script>"}) == []


def test_removed_reference_passes():
    assert check_changes(["app.js"], {"index.html": INDEX_V9},
                         {"index.html": "<!-- app retired -->"}) == []


def test_unreferenced_asset_passes():
    assert check_changes(["orphan.js"], {"index.html": INDEX_V9},
                         {"index.html": INDEX_V9}) == []


def test_missing_html_content_passes():
    assert check_changes(["app.js"], {}, {}) == []


def test_asset_versions_parses_all_refs():
    assert asset_versions(INDEX_V9 + INDEX_UNVERSIONED) == {
        "app.js": 9, "neon-app.js": None}


def test_asset_versions_empty_without_refs():
    assert asset_versions("<p>no assets here</p>") == {}
    assert asset_versions(None) == {}


@pytest.mark.parametrize("html", ["", "<!-- nothing -->"])
def test_no_matching_ref_passes(html):
    assert check_changes(["app.js"], {"index.html": html},
                         {"index.html": html}) == []
