"""The fleet dashboard page must actually PARSE.

Written after shipping a blank dashboard. The page had been "verified" by
checking that /api/state returned the right JSON and that the expected strings
were present in the HTML -- both of which passed while the page rendered
nothing at all. The cause was a duplicate `const bar` in the same function
scope: a SyntaxError, which aborts the entire <script> tag before a single
line runs. Every element stays empty and the browser logs nothing useful.

Neither an API check nor a string grep can catch that class of bug. Only
parsing the script can. This test does exactly that for the live fleet
dashboard. The archived kanban / single-bot page validation lived alongside
this on the now-moved ``archive/legacy-bot-8788`` branch; the kanban page
itself is no longer importable on this branch.
"""
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Only the live dashboard is rendered on this branch; the legacy "kanban"
# page lived in server/kanban.py and is preserved on the
# archive/legacy-bot-8788 branch (alongside the rest of the port-8788
# single-bot pipeline). Importing it here would point at the archive
# snapshot, which is not what this regression test is for.
from server.fleet_dash import PAGE as FLEET_PAGE  # noqa: E402
from server.spread_dash_html import DASHBOARD_HTML, LANDING_HTML  # noqa: E402

NODE = shutil.which("node")

# One page, one flatten, one parse: SyntaxError in any <script> renders a
# fully blank dashboard, not a degraded one.
PAGES = {"fleet": FLEET_PAGE, "spread": DASHBOARD_HTML, "landing": LANDING_HTML}


def _script_blocks(page: str | None = None) -> list[str]:
    return re.findall(r"<script>([\s\S]*?)</script>",
                      FLEET_PAGE if page is None else page)


def test_page_has_a_script_block():
    assert _script_blocks(), "the dashboard is inert without its <script>"


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("name", sorted(PAGES))
def test_dashboard_script_parses(tmp_path, name):
    """A parse error here is a fully blank page, not a degraded one."""
    for i, src in enumerate(_script_blocks(PAGES[name])):
        f = tmp_path / f"{name}{i}.js"
        # --check parses without executing, so no browser globals are needed.
        f.write_text(src, encoding="utf-8")
        r = subprocess.run([NODE, "--check", str(f)],
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"dashboard script block {i} does not parse -- the page will render "
            f"BLANK:\n{r.stderr}")


@pytest.mark.parametrize("name", sorted(PAGES))
def test_no_duplicate_top_level_consts_all_pages(name):
    for src in _script_blocks(PAGES[name]):
        names = re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=", src, re.M)
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"{name}: duplicate const declarations: {sorted(dupes)}"


def test_static_tailwind_replaces_the_cdn_runtime():
    """Session 52: the Tailwind Play CDN (~350KB render-blocking third-party
    JS, fetched from a public CDN on every cold load) is replaced by the
    pre-built minified stylesheet shipped in `server/_tailwind_css.py` and
    inlined into both pages. The CDN must never return -- re-adding it
    re-introduces the render-blocking dependency and breaks offline use."""
    from server._tailwind_css import TAILWIND_CSS
    for page in (DASHBOARD_HTML, LANDING_HTML):
        assert "cdn.tailwindcss.com" not in page
        assert TAILWIND_CSS in page


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
def test_generated_tailwind_css_is_fresh():
    """The committed stylesheet must equal a fresh build from the template's
    current class set. A utility class added to `spread_dash_html.py` without
    rebuilding silently renders UNSTYLED (there is no CDN runtime fallback), so
    staleness must fail here rather than pass the inlining-only check
    (coderabbit: stale CSS should fail, not just verify inlining).
    """
    from scripts.build_tailwind_css import build
    from server._tailwind_css import TAILWIND_CSS

    fresh = build()
    assert fresh == TAILWIND_CSS, (
        "server/_tailwind_css.py is stale -- run "
        "python -m scripts.build_tailwind_css from the repo root")


def test_settled_rows_harden_market_identifiers():
    """PR #22 review: a persisted market slug must never reach an inline
    handler, an unescaped attribute, or a raw link. The row carries
    data-market (attribute-escaped) and a delegated DOM listener, the link
    path is URL-encoded, and both formatted titles are HTML-escaped."""
    page = DASHBOARD_HTML
    assert 'data-market="${escAttr(g.market)}"' in page
    assert 'onclick="toggleMarketExpand(' not in page
    assert 'https://polymarket.com/event/${encodeURIComponent(g.market)}' in page
    assert '${esc(formatMarketTitle(g.market))}' in page
    assert '${esc(formatMarketTitle(x.market))}' in page
    assert 'function escAttr(' in page
    assert 'const row = e.target.closest("[data-market]");' in page


def test_grouped_return_uses_aggregate_pnl_over_cost_basis():
    """PR #22 review: the grouped return must be 100 * total_pnl /
    total_cost_basis, not a mean of per-exit percentages -- the mean only
    matches when every exit shares the same cost basis."""
    page = DASHBOARD_HTML
    assert '100 * g.total_pnl / g.total_cost_basis' in page
    assert 'count_pct' not in page


def test_dashboard_auto_refreshes_every_15s():
    """Auto-refresh polls the four endpoints in place instead of rebuilding
    the page: a 15s interval drives refresh(), renderSummary() is the single
    summary renderer shared by boot and refresh, a busy guard stops polls
    from overlapping, and the refresh reuses the stateful renderers so open
    settled groups, pagination, and the active tab survive."""
    page = DASHBOARD_HTML
    assert "setInterval(refresh, 15000)" in page
    assert "async function refresh()" in page
    assert "function renderSummary(s)" in page
    assert "if (REFRESH_BUSY) return;" in page
    assert "if (st) renderSettled(st.settled, st.total_closes);" in page
    # The settled list can shrink between polls; the page index is clamped
    # so a refresh never strands the table on an empty page.
    assert "settledState.page = page;" in page


def test_phase2_terminal_aesthetic_applied():
    """Phase-2 visual upgrade: dark mesh gradient canvas, frosted-glass
    panel overrides (blur 12px, rgba(15,23,42,.65) fill, faint white
    hairlines), lifted secondary-label contrast (#94A3B8), Geist Mono as
    the strict data face with tabular numerals, gold reserved for the mid
    badge and the go-live call, and the hero KPI drop shadow."""
    page = DASHBOARD_HTML
    assert "linear-gradient(180deg,#080C14 0%,#0B111E 100%)" in page
    assert "backdrop-filter:blur(12px)" in page
    assert "rgba(15,23,42,.65)" in page
    assert "rgba(255,255,255,.07)" in page
    assert "#94A3B8" in page
    assert "Geist+Mono" in page
    assert "font-variant-numeric:tabular-nums" in page
    assert "text-[#FBBF24]" in page
    assert 'color:"#FBBF24"' in page
    assert "hero-shadow" in page


def test_phase3_risk_depth_tooltips_applied():
    """Phase-3 widgets: the naked-USD capacity bar (utilization bands with
    pulsing red at 80%+), micro-depth washes behind the price levels in the
    order-book strip, the gold mid marker with dark outline, and CSS tooltips
    on the statistical headers. The server exposes the two naked fields the
    bar needs."""
    page = DASHBOARD_HTML
    # Capacity bar: utilization bands + pulsing red high-exposure state.
    assert "Naked USD Exposure" in page
    assert "HIGH EXPOSURE" in page
    assert "warn-bar" in page
    assert "max_naked_usd" in page
    # Micro-depth washes behind the price levels + gold mid marker outline.
    assert "rgba(16,185,129,.13)" in page
    assert "rgba(239,68,68,.12)" in page
    assert "box-shadow:0 0 0 1px #090D16" in page
    # Tooltip system: icon, popover card, formula/gate phrasing.
    assert "function tip(key, body)" in page
    assert "tip-pop" in page
    assert "tip-ico" in page
    assert "Kish's effective sample size" in page
    assert "1.645" in page  # the 90% lower-bound formula
    assert "1.35" in page  # depth-band half-width sizing


def test_summary_exposes_naked_risk_fields(monkeypatch, tmp_path):
    """api_summary must carry the fleet-wide naked-USD figure and the cap so
    the capacity bar has real numbers (computed in server/spread_dash.py, not
    faked in the template)."""
    # Seed a real temp DB so the pairs-EV leg of the summary is populated
    # (a missing DB reads as None, which the verdict panel treats as "not
    # measured" -- the tile contract needs an actual dict here).
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "dash.db"))
    from strategy import stats, store
    # `store` resolves its DB via CFG.db_path() (env-driven) while `stats`
    # uses a module-level path -- redirect both to the same temp file so the
    # summary's pairs-EV leg reads the seeded DB, not the live run/fleet.db.
    monkeypatch.setattr(stats, "DB", tmp_path / "dash.db")
    store.log_event(ts=999.0, market_slug="m0", condition_id="c0",
                    kind="QUOTING", reason="r", size=1.0)

    from server.spread_dash import CFG, app
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        s = c.get("/api/summary").json()
    assert "naked_usd" in s
    assert "max_naked_usd" in s
    # The contract is "the configured cap", not a magic number: the next
    # retune of strategy/config.py must not break this test (coderabbit).
    assert s["max_naked_usd"] == CFG.max_naked_usd
    # The pairs-rule EV and the pending exit-card ladder ride the same
    # summary -- the verdict panel's Exit Card tile reads them.
    assert "pairs_ev" in s and s["pairs_ev"] is not None
    card = s["pairs_ev"]["exit_card"]
    for key in ("n", "recorded", "pending", "no_markout", "no_fill",
                "no_column", "re_read_at"):
        assert key in card
    # The 15m fill-horizon ladder rides the same payload: the verdict tile
    # renders it under the exit card (Session 55).
    fh = s["pairs_ev"]["fill_horizon"]
    for key in ("n", "recorded", "pending", "no_markout", "no_column",
                "drift", "window_sec"):
        assert key in fh


def test_verdict_panel_has_exit_card_tile():
    """The pending exit-card re-read (Sessions 49-51) is visible at a glance:
    the verdict panel renders an Exit Card tile driven by
    s.pairs_ev.exit_card, so the operator sees exits-to-re-read (and whether
    the 15m mid counterfactual is being captured) without opening the report.
    """
    from server.spread_dash_html import DASHBOARD_HTML
    assert "Exit Card" in DASHBOARD_HTML
    assert "exit_card" in DASHBOARD_HTML
    assert "re_read_at" in DASHBOARD_HTML
    assert "python -m scripts.pairs_ev_report" in DASHBOARD_HTML
    # The tile also renders the 15m fill capture (Session 55) driven by
    # s.pairs_ev.fill_horizon.
    assert "fill_horizon" in DASHBOARD_HTML
    assert "15m capture:" in DASHBOARD_HTML


def test_markets_payload_carries_phase4_fields(monkeypatch):
    """api_markets must expose the raw classification inputs, the refusal
    code, the persisted lifecycle events, and a per-row telemetry anchor so
    the table can badge states truthfully instead of guessing. The fleet
    payload is stubbed: the test must not depend on live fleet state
    (coderabbit: /api/markets reads run/fleet_state.json, which CI lacks)."""
    from server import spread_dash as sd
    from starlette.testclient import TestClient

    row = {
        "slug": "atp-test-m", "title": "Test market",
        "committed": 50.0, "quotes": [], "unrealized_pnl": 1.25,
        "age": 30.0, "closed_pnl": 0.0, "closes": 0, "fills": 2,
        "gate": "NORMAL", "markout": None, "mid_up": 0.55,
        "up_bid": 0.54, "up_ask": 0.56, "our_up": 0.0,
        "our_dn_as_up": 0.0, "max_spread": 0.045,
        "paired": 20.0, "naked_sh": 0.0, "err": "", "why": "",
        "close_why": "", "merge_why": "",
        "events": [{"kind": "FILLED", "ts": 1.0, "reason": "tape"}],
    }

    def fake_cached(key, loader):
        if key == "fleet":
            return {"markets": [row], "now": time.time()}
        return loader()

    monkeypatch.setattr(sd, "_cached", fake_cached)
    with TestClient(sd.app) as c:
        m = c.get("/api/markets").json()
    assert "now" in m
    assert m["markets"]
    r = m["markets"][0]
    for key in ("paired", "naked_sh", "err", "why", "code", "events", "ts"):
        assert key in r, f"missing {key} in api_markets row"
    assert isinstance(r["events"], list)
    assert r["market"] == "atp-test-m"


def test_phase4_table_badges_filters_and_age_applied():
    """Phase-4 market table: the four action buckets with the exact brief
    color coding, the gate refusal code in micro-text under BLOCKED pills,
    lifecycle dots from persisted events, the quick-filter bar (category +
    state chips, instant, no reload), and per-row age badges ticked live with
    stale rows dimmed past 60s."""
    page = DASHBOARD_HTML
    # Buckets + exact brief colors (blue-950/400/800, emerald, amber, purple).
    assert "function classifyStatus(r)" in page
    assert "bg-[#172554] text-[#60A5FA] border-[#1E40AF]" in page
    assert "bg-[#022C22] text-[#34D399] border-[#065F46]" in page
    assert "bg-[#451A03] text-[#FBBF24] border-[#92400E]" in page
    assert "bg-[#3B0764] text-[#C084FC] border-[#6B21A8]" in page
    assert "RISK_GATE" in page
    # Lifecycle dots come from the persisted market_events telemetry.
    assert "function stateDots(events)" in page
    assert "reason_code" in page
    # Filter bar: category + state chips, clear, no-reload filtering.
    assert "function marketMatches(r, cls)" in page
    # "Has Active Inventory" matches on the inventory properties, NOT the
    # status bucket: classifyStatus sends an err/why market straight to
    # BLOCKED before checking inventory, so a blocked market that is still
    # carrying a position must stay visible under this filter.
    assert ('FILTERS.state === "HOLD"' in page
            and "return (r.paired || 0) > 0 || (r.naked_sh || 0) > 0" in page)
    assert 'cls.bucket === "FILLED" || cls.bucket === "MERGED"' not in page
    assert "data-fcat=" in page
    assert "data-fst=" in page
    assert "data-fclear" in page
    assert "No markets match the current filters" in page
    assert "Actively Quoting" in page and "Blocked by Risk" in page
    assert "Has Active Inventory" in page
    # Age badges tick live; stale rows dim.
    assert "function tickAgeBadges()" in page
    assert "data-age=" in page
    assert 'opacity = stale ? "0.6"' in page
    assert "STALE " in page


def test_split_flap_hinge_and_drawer_focus_trap_wired():
    """Design pass: the decision hinge renders its call as a split-flap
    instrument -- old letter halves flap away and new halves flip in only
    when the verdict word actually changes (hingeWordHtml + .flap layers),
    plain display type on first paint and under reduced motion -- and the
    market drawer traps Tab focus so it cannot wander behind the modal."""
    page = DASHBOARD_HTML
    assert "function hingeWordHtml(word, color)" in page
    assert "let HINGE_WORD" in page
    assert "flap.flipping" in page
    assert "flap-top-out" in page
    assert "flap-bot-in" in page
    assert "class=\"flap-gap\"" in page
    # Reduced motion: the JS renders plain text (no flap layers) and the CSS
    # stops the flap animations outright.
    assert "MOTION_OK && HINGE_WORD && HINGE_WORD !== word" in page
    assert "flap.flipping .flap-top .flap-o" in page
    assert "function trapDrawerFocus(e)" in page
    assert 'if (e.key === "Tab" && DRAWER_SLUG) trapDrawerFocus(e);' in page


def test_capital_since_inception_chart_replaces_hero_unrealized():
    """The Unrealized tiles in both heroes were replaced by a
    capital-since-inception panel -- a SHARED widget served from
    /capital.js, rendered by the dashboard from settledState.rows and by the
    landing from its own /api/settled fetch, on top of the starting
    bankroll. Open positions stay a separate ledger."""
    page = DASHBOARD_HTML
    landing = LANDING_HTML
    assert "Capital Since Inception" in page
    assert "Capital Since Inception" in landing
    assert 'id="capital-panel"' in page and 'id="capital-panel"' in landing
    assert ('renderCapitalPanel(document.getElementById("capital-panel"), '
            's, settledState.rows)' in page)
    assert ('renderCapitalPanel(document.getElementById("capital-panel"), '
            's, (st && st.settled) || [])' in landing)
    assert "Realized P&amp;L &mdash; capital since inception" in page
    assert "Realized P&amp;L and the capital curve it has built" in landing
    assert '<script src="/capital.js"></script>' in page
    assert '<script src="/capital.js"></script>' in landing
    assert 'font-family="Geist Mono"' in page
    # The old unrealized tiles are gone from both pages.
    assert 'data-kpi="hero_unrealized"' not in page
    assert "hero-unrealized" not in landing
    assert "Two independent valuations" not in landing


def test_capital_widget_served_and_parses(tmp_path):
    """The shared capital widget is served from /capital.js, parses as JS,
    and carries the view-toggle machinery (CAP_VIEW, data-capview,
    aria-pressed) plus the total-equity honesty note."""
    from server.spread_dash import app
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        r = c.get("/capital.js")
    assert r.status_code == 200
    src = r.text
    assert "function capitalSeries(rows, bankroll, marks, floatNow)" in src
    assert "function capitalChartSvg(ser)" in src
    assert 'data-kpi="capital_now"' in src
    assert "let CAP_VIEW = \"realized\"" in src
    assert 'data-capview="${id}"' in src
    assert 'aria-label="Capital view"' in src
    assert 'aria-pressed="${CAP_VIEW === id}"' in src
    assert "float_history" in src  # the Total view reads per-sweep marks here
    assert "recorded once per sweep" in src  # marks exist -> true historical series
    assert "No per-sweep float marks recorded yet" in src  # no marks -> fallback note
    assert "Total equity since inception" in src
    assert "No closes recorded yet" in src
    assert "not marked to market here" in src
    if NODE:
        f = tmp_path / "capital.js"
        f.write_text(src, encoding="utf-8")
        r = subprocess.run([NODE, "--check", str(f)],
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"capital.js does not parse -- a SyntaxError here breaks the "
            f"landing AND the dashboard:\n{r.stderr}")


def test_summary_exposes_float_history():
    """api_summary carries the float-mark series (fleet-side float_marks,
    downsampled server-side), so the shared Total equity widget can time-merge
    it with the settled closes instead of shifting by today's float. Also
    carries the fleet payload's load time so the "Data as of" tile reports
    data freshness, not response time."""
    from server.spread_dash import app
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        s = c.get("/api/summary").json()
    assert "float_history" in s
    assert isinstance(s["float_history"], list)
    assert "fleet_ts" in s
    assert s["fleet_ts"] is None or isinstance(s["fleet_ts"], float)
    if s["float_history"]:
        h = s["float_history"][0]
        assert set(h) == {"ts", "unrealized_usd", "committed_open_usd",
                          "naked_usd"}


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_capital_widget_float_marks_math(tmp_path):
    """The Total view time-merges closes with per-sweep float marks: each
    recorded mark is a point at its own ts (bankroll + realized-so-far + the
    float that was open then), marks before the first close and after the
    last close are real points, the Realized view ignores marks entirely, and
    no marks falls back to shifting the whole curve by today's float."""
    from server.spread_dash import app
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        src = c.get("/capital.js").text
    harness = """
// --- realized view: closes only, marks contribute no points ---
CAP_VIEW = "realized";
let r = capitalSeries([{ts:100, pnl:10}, {ts:200, pnl:5}], 1000, [
  {ts:50, unrealized_usd:20}, {ts:150, unrealized_usd:30}, {ts:250, unrealized_usd:10}
], 0);
let rp = r.pts.map(p => p.ts + ":" + p.v).join(",");
if (rp !== "100:1010,200:1015") throw new Error("realized pts: " + rp);

// --- total view: marks time-merge with closes ---
CAP_VIEW = "total";
let t = capitalSeries([{ts:100, pnl:10}, {ts:200, pnl:5}], 1000, [
  {ts:50, unrealized_usd:20}, {ts:150, unrealized_usd:30}, {ts:250, unrealized_usd:10}
], 0);
let tp = t.pts.map(p => p.ts + ":" + p.v).join(",");
if (tp !== "50:1020,100:1030,150:1040,200:1045,250:1025") {
  throw new Error("total pts: " + tp);
}

// --- no marks: fall back to shifting the whole curve by today's float ---
let f = capitalSeries([{ts:100, pnl:10}], 1000, [], 7);
if (f.pts.length !== 1 || f.pts[0].v !== 1017) {
  throw new Error("fallback pts: " + JSON.stringify(f.pts));
}
"""
    f = tmp_path / "float_marks_math.js"
    f.write_text("global.document = { addEventListener(){} };\n" + src + "\n"
                 + harness, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True)
    assert r.returncode == 0, f"float-marks widget math failed:\n{r.stderr}"


def test_cached_single_flight():
    """Concurrent misses on the same cache key run the loader exactly once;
    the waiting requests receive the same value, and a failed load clears
    the in-flight marker so the next request retries instead of deadlocking
    (coderabbit: prevent duplicate cold-cache loads)."""
    from server import spread_dash as sd

    key = "t_single_flight"
    sd._DASH_CACHE.pop(key, None)
    sd._DASH_LOADING.pop(key, None)
    calls = []

    def loader():
        calls.append(time.time())
        time.sleep(0.2)
        return {"loaded": len(calls)}

    # A barrier lines every thread up at the same instant so the race window
    # (all five observing the miss before any installs a marker) is actually
    # exercised -- with the check-and-install split across critical sections
    # this test catches duplicate loads, not just relies on scheduling.
    barrier = threading.Barrier(5)
    results = []

    def worker():
        barrier.wait()
        results.append(sd._cached(key, loader))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1, f"loader ran {len(calls)} times for one key"
    assert all(r == {"loaded": 1} for r in results)

    # A failed load clears the marker; the next request loads fresh.
    sd._DASH_CACHE.pop(key, None)
    sd._DASH_LOADING.pop(key, None)
    calls.clear()

    def bad():
        calls.append("bad")
        raise RuntimeError("boom")

    try:
        sd._cached(key, bad)
    except RuntimeError:
        pass
    else:
        raise AssertionError("loader failure should propagate")

    def good():
        calls.append("good")
        return {"ok": True}

    # The failure cleared the marker, so the next request loads fresh rather
    # than deadlocking on a never-set event.
    assert sd._cached(key, good) == {"ok": True}
    assert calls == ["bad", "good"]  # failed once, retried exactly once


def test_data_change_cues_and_market_drawer_are_wired():
    """Phase-1 interactions: financial figures flash on change (data-kpi /
    data-v), hero numbers roll up (data-rollup), status cells fade+scale in
    (data-state), the data-health dot pulses (health-pulse), and clicking a
    market row opens the right-edge drawer (data-drawer + openDrawer) whose
    content is rendered from data already in memory."""
    page = DASHBOARD_HTML
    assert "function animateChanges()" in page
    assert "function animateNumber(" in page
    assert "data-kpi=\"hero_realized\"" in page
    assert "data-rollup" in page
    assert "data-state=\"${escAttr(r.market)}\"" in page
    assert "health-pulse" in page
    assert "data-drawer=\"${escAttr(r.market)}\"" in page
    assert "function openDrawer(slug)" in page
    assert "function closeDrawer()" in page
    assert "function renderDrawer()" in page
    assert 'id="drawer"' in page
    assert "translate-x-full" in page
    assert "rgba(34,197,94,.2)" in page
    # Drawer wiring must not regress the PR #22 hardening: the settled
    # rows still expand inline, never via the drawer.
    assert "toggleMarketExpand(row.getAttribute(\"data-market\"))" in page


def test_scan_page_has_no_fleet_view():
    """The 8801 page IS the market scan now: the fleet view, its table
    renderers, and the view switcher are gone, and the funnel is the whole
    page. The fleet page lives on 8800 (server.spread_dash)."""
    assert 'id="view-fleet"' not in FLEET_PAGE
    assert 'id="viewFleet"' not in FLEET_PAGE
    assert 'id="viewScan"' not in FLEET_PAGE
    assert 'function tick()' not in FLEET_PAGE
    assert 'function ladder(m)' not in FLEET_PAGE
    assert 'id="settledTbl"' not in FLEET_PAGE
    assert 'Fleet Naked Risk' not in FLEET_PAGE
    assert 'id="heroValue"' not in FLEET_PAGE
    assert 'tick(); setInterval(tick,4000);' not in FLEET_PAGE


def test_no_duplicate_top_level_consts():
    """Top-level duplicate `const` in the same script block.

    The blank-dashboard bug class is a SyntaxError that aborts the entire
    ``<script>`` tag and leaves every element empty. Two `const NAME` lines
    at the top level of one script is the input the engine rejects. This
    regex only catches top-level duplicates (no leading whitespace);
    duplicates inside separate function bodies are legal in JS and are
    caught by ``test_dashboard_script_parses`` (node --check will reject
    any actual scope violation).
    """
    for src in _script_blocks(PAGES["fleet"]):
        names = re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=", src, re.M)
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"duplicate const declarations: {sorted(dupes)}"


def test_market_pipeline_view_is_wired_in():
    """The market-scan funnel is the entire 8801 page: the four-lane board,
    the census strip, the near-miss trackers, and the JS that renders them
    from /api/pipeline -- the whole point of the view is seeing the funnel
    live, so a board skeleton without its renderer is the blank-page bug
    class."""
    assert 'id="view-pipeline"' in FLEET_PAGE
    assert 'id="laneRaw"' in FLEET_PAGE
    assert 'id="laneFilter"' in FLEET_PAGE
    assert 'id="laneFinal"' in FLEET_PAGE
    assert 'id="laneGrad"' in FLEET_PAGE
    assert 'id="pipeStrip"' in FLEET_PAGE
    assert 'function pipeLane(' in FLEET_PAGE
    assert 'function pipeFilter(snap)' in FLEET_PAGE
    assert 'function pipeGrad(s)' in FLEET_PAGE
    assert 'async function tickPipeline()' in FLEET_PAGE
    assert "fetch('/api/pipeline'" in FLEET_PAGE
    assert 'setInterval(tickPipeline,10000)' in FLEET_PAGE
    assert "● FLEET ALIVE" in FLEET_PAGE
