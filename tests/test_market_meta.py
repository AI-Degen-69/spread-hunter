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
    assert meta["category"] == "League of Legends"
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


def test_series_title_is_never_a_category(tmp_path):
    # A series names the competition, not the category (#748). "League of
    # Legends" is a game the display rules can name, so it still reads as a
    # game from the title -- but a series the rules cannot name must not become
    # the label. One feed write, one mtime: the read cache is mtime-keyed.
    _write_feed(tmp_path, [
        {"cid": "0xseries1", "slug": "lol-match", "title": "LoL match",
         "category": "", "series_title": "League of Legends", "market_group": "",
         "tags": [], "volume_24h": 1.0},
        {"cid": "0xseries2", "slug": "some-match", "title": "Some match",
         "category": "", "series_title": "Match Winner", "market_group": "",
         "tags": [], "volume_24h": 1.0},
        {"cid": "0xseries3", "slug": "quiet-match", "title": "Quiet match",
         "category": "", "series_title": "Republican Party", "market_group": "",
         "tags": [], "volume_24h": 1.0},
    ])

    def cat(cid):
        return mm.resolve_market_meta(cid, [], [], root=tmp_path)["category"]

    assert cat("0xseries1") == "League of Legends"
    assert cat("0xseries2") == "Uncategorized"
    assert cat("0xseries3") == "Uncategorized"


def test_market_group_is_never_a_category(tmp_path):
    # gamma's `groupItemTitle` is a group/outcome name inside an event, which is
    # where `Republican Party`, `Data 2` and `Spread -3.5` came from (#748).
    groups = ["Match Winner", "Republican Party", "Aaron Ford (D)", "Data 2",
              "Spread -3.5"]
    _write_feed(tmp_path, [{
        "cid": f"0xgroup{i}", "slug": f"group-{i}",
        "title": "Some market", "category": "", "series_title": "",
        "market_group": group, "tags": [], "volume_24h": 1.0,
    } for i, group in enumerate(groups)])
    for i, group in enumerate(groups):
        meta = mm.resolve_market_meta(f"0xgroup{i}", [], [], root=tmp_path)

        assert meta["category"] == "Uncategorized", group


def test_tag_only_row_resolves_to_the_category_tag(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "tagged", "title": "Tagged market",
        "category": "", "series_title": "", "market_group": "",
        "tags": ["Crypto", "Bitcoin", "Hit Price"], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)

    assert meta["category"] == "Crypto"


def test_most_specific_venue_tag_wins(tmp_path):
    # Live tags on a real NBA market: five labels, one of them a category and
    # one of them the sport that contains it. The chip carries the game.
    tags = ["Sports", "NBA", "Games", "Basketball", "NBA Tip-Off"]
    _write_feed(tmp_path, [{
        "cid": "0xtag1", "slug": "pacers-vs-pelicans", "title": "Pacers vs. Pelicans",
        "category": "", "series_title": "", "market_group": "",
        "tags": tags, "volume_24h": 1.0,
    }, {
        # Tag order must not decide it either.
        "cid": "0xtag2", "slug": "pacers-vs-pelicans", "title": "Pacers vs. Pelicans",
        "category": "", "series_title": "", "market_group": "",
        "tags": list(reversed(tags)), "volume_24h": 1.0,
    }])

    assert mm.resolve_market_meta("0xtag1", [], [], root=tmp_path)["category"] == "Basketball"
    assert mm.resolve_market_meta("0xtag2", [], [], root=tmp_path)["category"] == "Basketball"


def test_non_category_tags_never_become_the_label(tmp_path):
    # Acceptance list from #748: a series, group or outcome name is never the
    # category. None of these tags names a category, so the row stays honest.
    noise = ["UCL Matchday", "Republican Party", "Aaron Ford (D)", "Data 2",
             "Spread -3.5", "Recurring", "Hide From New"]
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "noise-only", "title": "Some market",
        "category": "", "series_title": "", "market_group": "",
        "tags": noise, "volume_24h": 1.0,
    }, {
        # A series tag that does contain a sport token is a basketball market,
        # but the label is the sport -- never the series name itself.
        "cid": "0xtag2", "slug": "tip-off", "title": "Some market",
        "category": "", "series_title": "", "market_group": "",
        "tags": ["NBA Tip-Off"], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)
    tip_off = mm.resolve_market_meta("0xtag2", [], [], root=tmp_path)

    assert meta["category"] == "Uncategorized"
    assert tip_off["category"] == "Basketball"


def test_elections_tag_reads_as_politics(tmp_path):
    # The venue tags a Senate market `Elections`; on a row that carries only
    # that word, the label must still be the parent bucket.
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "brazil-election", "title": "Brazil votes",
        "category": "", "series_title": "", "market_group": "",
        "tags": ["Elections", "Brazil"], "volume_24h": 1.0,
    }])
    assert mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)["category"] == "Politics"


def test_venue_tags_beat_the_keyword_classifier(tmp_path):
    # A crypto market whose title mentions the election is still a crypto
    # market: the venue's own tag outranks a keyword guess.
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "btc-after-election",
        "title": "Will the election move BTC?", "category": "",
        "series_title": "", "market_group": "",
        "tags": ["Crypto", "Bitcoin"], "volume_24h": 1.0,
    }])
    assert mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)["category"] == "Crypto"


def test_venue_label_beats_the_tags(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "venue-label", "title": "Some market",
        "category": "", "venue_category": "Tennis", "series_title": "",
        "market_group": "", "tags": ["Basketball"], "volume_24h": 1.0,
    }])
    assert mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)["category"] == "Tennis"


def test_unrecognised_venue_label_falls_through_to_the_tags(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "venue-label", "title": "Some market",
        "category": "", "venue_category": "Match Winner", "series_title": "",
        "market_group": "", "tags": ["Tennis", "Sports"], "volume_24h": 1.0,
    }])
    assert mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)["category"] == "Tennis"


def test_a_political_keyword_tag_does_not_outrank_a_real_category(tmp_path):
    # `Senate` is a tag, not a shelf. Ranking it as Politics by keyword would
    # beat the explicit `Crypto` tag on the same market (#748).
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "senate-crypto", "title": "Some market",
        "category": "", "series_title": "", "market_group": "",
        "tags": ["Senate", "Crypto"], "volume_24h": 1.0,
    }])
    assert mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)["category"] == "Crypto"


def test_unrecognised_venue_label_falls_through_to_the_market_category(tmp_path):
    # `venue_category` can carry a group name while `category` holds the real
    # shelf; the second label must still be read (#748).
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "venue-label", "title": "Some market",
        "category": "Tennis", "venue_category": "Match Winner",
        "series_title": "", "market_group": "", "tags": [], "volume_24h": 1.0,
    }])
    assert mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)["category"] == "Tennis"


def test_event_title_travels_with_the_meta(tmp_path):
    # The Positions secondary line needs a real title, and `event_title` used to
    # be read and then dropped (#748).
    _write_feed(tmp_path, [{
        "cid": CID_TAG, "slug": "pacers-vs-pelicans", "title": "",
        "event_title": "Pacers vs. Pelicans", "category": "",
        "venue_category": "", "series_title": "", "market_group": "",
        "tags": ["Basketball"], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_TAG, [], [], root=tmp_path)

    assert meta["event_title"] == "Pacers vs. Pelicans"
    assert meta["title"] == "Pacers vs. Pelicans"
    assert meta["category"] == "Basketball"


def test_malformed_tags_never_raise(tmp_path):
    for tags in ("nope", {"a": 1}, ["", None, 5], [{"label": ""}, {"slug": 7}]):
        _write_feed(tmp_path, [{
            "cid": CID_BLANK, "slug": "weird-tags", "title": "Weird tags",
            "category": "", "series_title": "", "market_group": "",
            "tags": tags, "volume_24h": 1.0,
        }])
        meta = mm.resolve_market_meta(CID_BLANK, [], [], root=tmp_path)

        assert meta["category"] == "Uncategorized", tags


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


def test_venue_shorthand_canonicalizes_to_the_game(tmp_path):
    # The venue names the shelf ("ATP Tour", "CS2", "LoL"); the dashboard
    # must show the game itself ("Tennis", "Counter-Strike 2",
    # "League of Legends") so one game always reads as one category.
    cases = {
        "0xatp1": ("ATP Tour", "Tennis"),
        "0xcs1": ("CS2", "Counter-Strike 2"),
        "0xcanonical-lol": ("LoL", "League of Legends"),
    }
    # One feed write for all rows: the feed cache keys on file mtime, so
    # rapid successive rewrites can read back stale rows.
    _write_feed(tmp_path, [{
        "cid": cid, "slug": f"match-{cid}",
        "title": f"Some match {cid}", "category": "",
        "venue_category": venue, "series_title": "", "market_group": "",
        "tags": [], "volume_24h": 1.0,
    } for cid, (venue, _) in cases.items()])
    for cid, (_, want) in cases.items():
        meta = mm.resolve_market_meta(cid, [], [], root=tmp_path)

        assert meta["category"] == want


def test_unmapped_venue_label_passes_through_verbatim(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_CRYPTO, "slug": "btc-up-day",
        "title": "BTC up day", "category": "", "venue_category": "Crypto",
        "series_title": "", "market_group": "",
        "tags": [], "volume_24h": 1.0,
    }])
    meta = mm.resolve_market_meta(CID_CRYPTO, [], [], root=tmp_path)

    assert meta["category"] == "Crypto"


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
    # The blanket label survives only for a game this resolver cannot name.
    assert mm.classify_display_category(
        "Will he win the Dota election", "", "dota-election") == "Dota 2"
    assert mm.classify_display_category(
        "Senate race outcome", "", "senate-race") == "Politics"
    assert mm.classify_display_category(
        "Market A resolves up?", "", "market-a") is None


def test_esports_label_the_game_not_the_genre():
    # "E-Sports" names the genre and nothing else, the same way "Uncategorized"
    # named no category at all. A LoL market is a League of Legends market.
    cases = {
        "lol-kcb-wd-2026-09-26": "League of Legends",
        "Cs2 Major Bracket": "Counter-Strike 2",
        "Valorant Champions Winner": "Valorant",
        "Dota 2 International": "Dota 2",
        # No "2" token in the title, so the label must not invent one: the
        # sequel rule exists to separate OW2 markets, never to promote OW1.
        "Overwatch League Finals": "Overwatch",
        "Overwatch 2 world cup winner": "Overwatch 2",
        "Rainbow Six Siege Invitational": "Rainbow Six Siege",
        "Starcraft Ii Gsl Winner": "StarCraft II",
        "Rocket League World Championship": "Rocket League",
        "Apex Legends Global Winner": "Apex Legends",
        "Ea Sports Fc Final Match": "EA Sports FC",
        "Call Of Duty League Winner": "Call of Duty",
        "Pubg Global Championship": "PUBG",
        "Fortnite Champion Series": "Fortnite",
        "Hearthstone Masters Tour": "Hearthstone",
        "King Of Glory World Champion Cup": "Honor of Kings",
        "League Of Legends Worlds Winner": "League of Legends",
        "Counter Strike Global Offensive": "Counter-Strike",
        "Tekken 8 World Tour Finals": "Tekken",
        "Street Fighter 6 Capcom Cup": "Street Fighter",
        "Super Smash Bros Ultimate Winner": "Super Smash Bros.",
        "Chess World Championship Final": "Chess",
    }
    for slug, expected in cases.items():
        assert mm.classify_display_category("", "", slug) == expected, slug


def test_unnameable_esport_keeps_the_genre_label():
    # A row that is plainly esports but names no game this resolver knows still
    # has to group somewhere honest; it falls back to the genre, not a blank.
    assert mm.classify_display_category(
        "Esports Finals Winner", "", "esports-finals-winner") == "E-Sports"
    assert mm.classify_display_category(
        "Who wins the pro gaming title", "", "pro-gaming-title") == "E-Sports"



def test_sports_map_to_their_real_name():
    # A detection token becomes the sport's real name, not the league acronym:
    # ATP/WTA are tennis circuits, not a sport of their own (#501).
    cases = {
        "Atp Cuenin Valient 2026 10 12": "Tennis",
        "Wta Mcnally Starodu 2026 10 12": "Tennis",
        "Will the Lakers win the NBA finals?": "Basketball",
        "Chiefs vs 49ers super bowl winner": "Football",
        "Football game tonight": "Football",
        "Yankees world series 2026": "Baseball",
        "Stanley cup favorite 2026": "Hockey",
        "Premier League title race": "Soccer",
        "FIFA world cup winner 2026": "Soccer",
        "PGA masters champion": "Golf",
        "F1 drivers champion 2026": "Racing",
        "Nascar daytona 500 winner": "Racing",
        "UFC 300 main event winner": "MMA",
        "Canelo boxing title fight": "Boxing",
        "Muay Thai championship bout": "Muay Thai",
        "IPL cricket final": "Cricket",
        "Six nations rugby winner": "Rugby",
    }
    for title, expected in cases.items():
        assert mm.classify_display_category(title, "", "") == expected, title


def test_sports_never_mislabel_non_sports():
    # Politics and crypto stay politics/crypto; a sport regex must not swallow
    # them just because a generic word appears.
    assert mm.classify_display_category("US-Iran ceasefire continues", "", "") is None
    assert mm.classify_display_category("Bitcoin above 100k by december", "", "") is None
    assert mm.classify_display_category("Presidential election 2028", "", "") == "Politics"


def test_muay_thai_wins_over_generic_combat():
    # Muay Thai is checked before MMA/Boxing so a Thai-boxing market keeps its
    # own label instead of collapsing into "MMA".
    assert mm.classify_display_category("Muay Thai title fight", "", "") == "Muay Thai"
    assert mm.classify_display_category("Thai boxing grand prix", "", "") == "Muay Thai"


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
        "category": "Tennis", "series_title": "", "market_group": "",
        "tags": [], "volume_24h": 1.0,
    }])
    feed_file = tmp_path / "runtime" / "markets.json"
    new_mtime = feed_file.stat().st_mtime + 5.0
    import os
    os.utime(feed_file, (new_mtime, new_mtime))

    meta2 = mm.resolve_market_meta(CID_CRYPTO, [], [], root=tmp_path)
    assert meta2["category"] == "Tennis"


def test_screening_fields_resolved_from_feed(tmp_path):
    _write_feed(tmp_path, [{
        "cid": CID_CRYPTO, "slug": "btc-up", "title": "Crypto 1",
        "category": "Crypto", "series_title": "", "market_group": "",
        "tags": [], "volume_24h": 50000.0, "days_to_resolve": 5.5,
        "movement_usd": 12000.0, "movement_window_sec": 1800.0,
        "yes_depth_usd": 3000.0, "no_depth_usd": 4500.0,
    }])
    meta = mm.resolve_market_meta(CID_CRYPTO, [], [], root=tmp_path)
    assert meta["volume_24h"] == 50000.0
    assert meta["days_to_resolve"] == 5.5
    assert meta["movement_usd"] == 12000.0
    assert meta["movement_window_sec"] == 1800.0
    assert meta["top3_bid_depth"] == 3000.0
    assert meta["yes_depth_usd"] == 3000.0
    assert meta["no_depth_usd"] == 4500.0

