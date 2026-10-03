"""One shared market-metadata resolver for KPI and registry state (issue #295).

Active Markets showed `Uncategorized` because `_resolve_market_meta` only read
the current top-20 `markets.json`, while `_market_identity` resolved titles a
second way and no category at all. Both now delegate here: feed row first,
universe row for departed markets, slug/title keywords last.
"""
from __future__ import annotations

import json

from core_brain import market_meta as mm

CID_LOL = "0xlol20260926"
CID_LULA = "0xlula2026election"
CID_CRYPTO = "0xcrypto1"
CID_SERIES = "0xseries1"
CID_TAG = "0xtag1"
CID_UNIVERSE = "0xuniverse1"
CID_BLANK = "0xblank1"

LOL_SLUG = "lol-kcb-wd-2026-09-26"
LULA_SLUG = ("will-luiz-incio-lula-da-silva-win-the-2026-"
              "brazilian-presidential-election")


def _write_feed(root, rows, universe_rows=None):
    run = root / "runtime"
    run.mkdir(parents=True, exist_ok=True)
    (run / "markets.json").write_text(json.dumps(rows), encoding="utf-8")
    if universe_rows is not None:
        (run / "market_universe.json").write_text(
            json.dumps({"ts": 0, "discovery": {}, "rows": universe_rows}),
            encoding="utf-8")


def test_slug_only_lol_market_resolves_to_esports(tmp_path):
    meta = mm.resolve_market_meta(
        CID_LOL,
        [{"condition_id": CID_LOL, "market_slug": LOL_SLUG}], [],
        root=tmp_path)

    assert meta["title"] == "Lol Kcb Wd 2026 09 26"
    assert meta["category"] == "E-Sports"
    assert meta["url"] == f"https://polymarket.com/market/{LOL_SLUG}"


def test_slug_only_election_market_resolves_to_politics(tmp_path):
    meta = mm.resolve_market_meta(
        CID_LULA,
        [{"condition_id": CID_LULA, "market_slug": LULA_SLUG}], [],
        root=tmp_path)

    assert meta["category"] == "Politics"


def test_venue_label_wins_over_keywords(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_CRYPTO, "slug": "btc-up",
        "title": "Will the president pump BTC before the election?",
        "category": "Crypto", "series_title": "", "market_group": "",
        "tags": [], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_CRYPTO, [], [], root=tmp_path)

    assert meta["category"] == "Crypto"


def test_blank_category_falls_back_to_series_verbatim(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_SERIES, "slug": "lol-match",
        "title": "LoL match", "category": "", "series_title": "League of Legends",
        "market_group": "", "tags": [], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_SERIES, [], [], root=tmp_path)

    assert meta["category"] == "League of Legends"


def test_blank_group_falls_back_before_keywords(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_SERIES, "slug": "some-match",
        "title": "Some match", "category": "", "series_title": "",
        "market_group": "Match Winner", "tags": [], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_SERIES, [], [], root=tmp_path)

    assert meta["category"] == "Match Winner"


def test_tag_only_row_resolves_to_first_tag(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "tagged", "title": "Tagged market",
        "category": "", "series_title": "", "market_group": "",
        "tags": ["Elections", "Brazil"], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)

    assert meta["category"] == "Elections"


def test_departed_market_resolves_from_the_universe(tmp_path):
    _write_feed(tmp_path, [], universe_rows=[{
        "cid": CID_UNIVERSE, "slug": "old-market", "title": "Old market",
        "category": "Tennis", "series_title": "", "market_group": "",
        "tags": [], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_UNIVERSE, [], [], root=tmp_path)

    assert meta["category"] == "Tennis"
    assert meta["title"] == "Old market"


def test_no_metadata_and_no_keyword_is_uncategorized(tmp_path):
    meta = mm.resolve_market_meta(CID_BLANK, [], [], root=tmp_path)

    assert meta["category"] == "Uncategorized"


def test_venue_category_wins_over_market_category(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_SERIES, "slug": "lol-match",
        "title": "LoL match", "category": "", "venue_category": "E-Sports",
        "series_title": "League of Legends", "market_group": "",
        "tags": [], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_SERIES, [], [], root=tmp_path)

    assert meta["category"] == "E-Sports"


def test_non_string_feed_fields_never_raise(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_BLANK, "slug": "weird-match",
        "title": "Weird match", "category": 123, "venue_category": None,
        "series_title": ["League of Legends"], "market_group": {},
        "tags": "nope", "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_BLANK, [], [], root=tmp_path)

    assert meta["category"] == "Uncategorized"


def test_esports_checked_before_politics():
    assert mm.classify_display_category(
        "Will he win the Dota election", "", "dota-election") == "E-Sports"
    assert mm.classify_display_category(
        "Senate race outcome", "", "senate-race") == "Politics"
    assert mm.classify_display_category(
        "Market A resolves up?", "", "market-a") is None


def test_feed_reads_cached_across_many_cids(tmp_path, monkeypatch):
    rows = [{"cid": f"0xcid_{i}", "slug": f"slug-{i}", "title": f"Title {i}",
             "category": "Crypto", "series_title": "", "market_group": "",
             "tags": [], "volume_24h": 1.0} for i in range(50)]
    _write_feed(tmp_path, rows)

    real_read = mm.Path.read_text
    read_count = 0

    def counting_read(self, *args, **kwargs):
        nonlocal read_count
        read_count += 1
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(mm.Path, "read_text", counting_read)

    for i in range(50):
        meta = mm.resolve_market_meta(f"0xcid_{i}", [], [], root=tmp_path)
        assert meta["title"] == f"Title {i}"
        assert meta["category"] == "Crypto"

    # Exactly 1 read for markets.json, 0 for universe because all 50 matched in markets.json
    assert read_count == 1


def test_rewritten_feed_file_picked_up_immediately(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_CRYPTO, "slug": "btc-up", "title": "Crypto 1",
        "category": "Crypto", "series_title": "", "market_group": "",
        "tags": [], "volume_24h": 1.0,
    }])
    meta1 = mm.resolve_market_meta(CID_CRYPTO, [], [], root=tmp_path)
    assert meta1["category"] == "Crypto"

    # Rewrite feed file
    _write_feed(tmp_path, [{
        "cid": CID_CRYPTO, "slug": "btc-up", "title": "Crypto 1",
        "category": "Macro", "series_title": "", "market_group": "",
        "tags": [], "volume_24h": 1.0,
    }])
    feed_file = tmp_path / "runtime" / "markets.json"
    new_mtime = feed_file.stat().st_mtime + 5.0
    import os
    os.utime(feed_file, (new_mtime, new_mtime))

    meta2 = mm.resolve_market_meta(CID_CRYPTO, [], [], root=tmp_path)
    assert meta2["category"] == "Macro"

