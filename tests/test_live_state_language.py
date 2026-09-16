"""The live-state language (DESIGN.md): one watchdog vocabulary everywhere.

Six states — RUNNING / DEGRADED / DOWN / STOPPED / UNKNOWN / STALE — applied
identically to every process, feed and tile. Heartbeat age is displayed, not
implied, and the page must announce that IT lost contact with the backend
instead of silently re-showing the last good numbers (broken-looking beats
lying).

The JS side is exercised through a Node harness (the same pattern the other
dashboard behaviours use); the Python side asserts the served document carries
the banner element and the canonical state classes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "dashboard" / "static"
APP_JS = _STATIC / "app.js"
HARNESS = Path(__file__).resolve().parent / "js" / "live_state_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _harness(script: str) -> dict:
    out = subprocess.run([shutil.which("node"), str(HARNESS), script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


# ── stateKey: the (running, age) → state mapping ───────────────────────────

@requires_node
def test_a_fresh_heartbeat_is_running():
    assert _harness("keys")["running_fresh"] == "running"


@requires_node
def test_the_default_ramp_ages_a_5s_loop_at_15s_and_60s():
    keys = _harness("keys")
    # Arrange — the documented default: amber at 3x a 5s cadence, red at 12x.
    assert keys["running_degraded"] == "degraded"   # 20s: past 15, under 60
    assert keys["running_down"] == "down"           # 90s: past 60


@requires_node
def test_thresholds_scale_with_the_service_cadence():
    keys = _harness("keys")
    # A 0.5s poller degrades at ~2s; a service with no cadence gets the default.
    assert keys["cadence_0_5s"] == {"degraded": 2, "down": 6}
    assert keys["cadence_garbage"] == {"degraded": 15, "down": 60}


@requires_node
def test_a_live_process_with_no_age_yet_still_reads_running():
    # The first poll after START has no heartbeat age; that is running, not unknown.
    assert _harness("keys")["running_no_age"] == "running"


@requires_node
def test_a_dead_process_with_a_known_age_is_down_and_a_quiet_stop_is_stopped():
    keys = _harness("keys")
    # A death (age known, process gone) is an alarm; an intentional stop is gray.
    assert keys["not_running_with_age"] == "down"
    assert keys["not_running_no_age"] == "stopped"


@requires_node
def test_callers_can_override_the_ramp():
    # A service with its own cadence passes its own thresholds.
    assert _harness("keys")["custom_thresholds"] == "degraded"


# ── statePillHtml: the canonical pill ──────────────────────────────────────

@requires_node
def test_the_pill_carries_the_state_class_and_the_age():
    pills = _harness("pills")
    assert 'class="pill state-running"' in pills["running_fresh"]
    assert "RUNNING" in pills["running_fresh"]
    assert "· 3s" in pills["running_fresh"]
    assert 'class="pill state-degraded"' in pills["degraded"]
    assert "· 22s" in pills["degraded"]
    assert 'class="pill state-down"' in pills["down"]


@requires_node
def test_the_running_pill_blinks_and_the_dead_ones_do_not():
    pills = _harness("pills")
    # The 1 Hz blink rides on pulse-dot active; degraded/down show a static dot.
    assert 'pulse-dot active' in pills["running_fresh"]
    assert 'pulse-dot active' not in pills["degraded"]
    assert 'pulse-dot' in pills["degraded"] and 'pulse-dot' in pills["down"]


@requires_node
def test_stopped_and_unknown_pills_carry_no_age():
    # A stopped service has no heartbeat to age; showing one would be fiction.
    pills = _harness("pills")
    assert "STOPPED" in pills["stopped_no_age"]
    assert "· 30s" not in pills["stopped_with_age_drops_age"]


@requires_node
def test_the_css_defines_every_state_class_the_js_can_emit():
    # A pill rendered into a class the stylesheet never heard of is an
    # invisible state — the whole point of the language is that it shows.
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")
    for state in ("running", "degraded", "down", "stopped", "unknown"):
        assert f".pill.state-{state}" in css, f"styles.css never styles state-{state}"


@requires_node
def test_the_blink_is_the_only_liveness_animation_and_honors_reduced_motion():
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")
    assert "@keyframes hb-blink" in css
    assert "steps(1, end) infinite" in css
    # The blink's reduced-motion guard renders the dot static instead of
    # removing it: a reduced-motion operator still reads the state color.
    blink_idx = css.find("animation: hb-blink")
    guard_idx = css.find(".pulse-dot.active { animation: none; }")
    assert blink_idx != -1 and guard_idx != -1 and guard_idx > blink_idx


# ── Backend-contact watchdog ───────────────────────────────────────────────

@requires_node
def test_one_failed_poll_is_tolerated_but_two_announce_stale():
    backend = _harness("backend")
    assert backend["afterOk"]["stale"] is False
    assert backend["afterOneFail"]["stale"] is False
    assert backend["afterTwoFails"]["stale"] is True


@requires_node
def test_a_dashboard_that_never_reached_the_backend_go_stale_too():
    # Review finding (PR #219): `backendLastSeenMs` starts null, so the old
    # guard meant a page opened against a dead backend stayed silently blank
    # instead of announcing it. Two failures from a cold start must go stale.
    backend = _harness("backend")
    assert backend["coldStart"]["afterOneFail"] is False
    assert backend["coldStart"]["afterTwoFails"] is True
    assert backend["coldStartBanner"] == "backend never contacted"


@requires_node
def test_the_banner_shows_and_clears_with_contact():
    backend = _harness("backend")
    assert backend["bannerShownThen"] is True
    assert backend["bannerShownAfter"] is False


@requires_node
def test_the_stale_age_counts_from_the_last_seen_poll():
    backend = _harness("backend")
    # last seen at t=1_000_000; the render ran at t=1_008_000 → 8s ago.
    assert "8s ago" in backend["staleAgeText"]
    assert "last seen" in backend["staleAgeText"]


@requires_node
def test_recovery_forges_a_new_last_seen():
    backend = _harness("backend")
    # After recovery, the anchor moved to the recovery tick, not the first poll.
    assert backend["afterRecovery"]["lastSeenMs"] == 1_010_000


# ── The served document carries the surfaces ───────────────────────────────

def test_the_stale_banner_exists_in_the_markup():
    # The watchdog has nothing to reveal if index.html never declared the banner.
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="backend-contact-banner"' in html
    assert "backend-stale-banner" in html
    assert 'class="stale-age' in html


def test_the_poll_loop_feeds_the_watchdog():
    # Both the success path and the catch path must report contact, or a
    # thrown batch would never age the page to stale. The success check reads
    # the WHOLE batch: a poll where only the trial-readiness endpoint answered
    # is still contact.
    js = APP_JS.read_text(encoding="utf-8")
    assert "setBackendContact([state, status, kpi, scanState, trialReadiness, guardAlerts, guardHealth]" in js
    assert ".some(r => r !== null && r !== undefined));" in js
    assert "setBackendContact(false);" in js


def test_the_master_indicator_uses_the_canonical_state_classes():
    js = APP_JS.read_text(encoding="utf-8")
    assert "state-running" in js
    assert "state-stopped" in js


def test_the_service_cards_render_the_canonical_pill():
    js = APP_JS.read_text(encoding="utf-8")
    assert "statePillHtml(" in js
    # The guardrail card ages on the watcher's ~5s heartbeat cadence.
    assert "cadenceThresholds(5)" in js


def test_the_design_system_is_declared_to_every_agent():
    # CLAUDE.md routes future work at DESIGN.md; DESIGN.md is the source of truth.
    claude = (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "DESIGN.md" in claude
    design = (_ROOT / "DESIGN.md").read_text(encoding="utf-8")
    assert "Live-State Language" in design
    for state in ("RUNNING", "DEGRADED", "DOWN", "STOPPED", "UNKNOWN", "STALE"):
        assert state in design


# ── the Market Filter's scan pill: liveness, not activity ──────────────────
#
# The server's IDLE means "heartbeat fresh but no active-phase work in the
# window" -- the filter is alive and between scans, which on a ~10m cycle is
# most of the time. Rendering that as STOPPED told the operator a healthy
# filter was "intentionally not running", and the pill flipped between
# SCANNING and STOPPED every cycle.

@requires_node
def test_an_idle_filter_with_a_fresh_heartbeat_reads_running():
    assert _harness("scanpill")["idleFresh"] == "running"


@requires_node
def test_a_scanning_filter_with_a_fresh_heartbeat_reads_running():
    assert _harness("scanpill")["scanningFresh"] == "running"


@requires_node
def test_an_idle_filter_ages_through_the_ramp_like_any_other_service():
    verdicts = _harness("scanpill")
    assert (verdicts["idleAging"], verdicts["idleLongGone"]) == ("degraded", "down")


@requires_node
def test_a_stalled_filter_reads_down():
    assert _harness("scanpill")["stalled"] == "down"


@requires_node
def test_an_unrecognised_verdict_still_reads_stopped():
    verdicts = _harness("scanpill")
    assert (verdicts["unrecognised"], verdicts["missing"]) == ("stopped", "stopped")


# ── the top-nav MARKET SCAN pill: the scanner PROCESS, on every page ───────
#
# The Data & Markets header pill reports the TRADING loop's heartbeat. Sitting
# beside "last scan: 3m ago" it read as "the market scan is down" whenever a
# slow rotation aged the heartbeat, which is a different process entirely.
# This pill answers the question that was actually being asked: is
# `scripts.filter_loop` alive, and is the snapshot it writes one the dashboard
# can still read? It lives in the top nav so the answer travels across tabs.

@requires_node
def test_a_live_scanner_with_a_fresh_snapshot_is_green():
    v = _harness("marketscan")["upFresh"]
    assert (v["state"], v["label"]) == ("running", "SCAN LIVE")


@requires_node
def test_no_scanner_process_is_red_whatever_the_file_says():
    # The operator's question: red means "no filter loop is running". A fresh
    # file left behind by a dead process must not paint it green.
    v = _harness("marketscan")["processDead"]
    assert (v["state"], v["label"]) == ("down", "SCAN DOWN")


@requires_node
def test_a_live_scanner_whose_snapshot_went_stale_is_amber_not_red():
    # The process is up, so it is not DOWN; the file it should be refreshing
    # is older than two full cycles, so it is not healthy either.
    v = _harness("marketscan")["upStale"]
    assert (v["state"], v["label"]) == ("degraded", "SCAN STALE")


@requires_node
def test_a_scanner_that_has_not_written_a_snapshot_yet_is_amber():
    v = _harness("marketscan")["upNoFile"]
    assert (v["state"], v["label"]) == ("degraded", "SCAN NO DATA")


@requires_node
def test_an_unreadable_or_missing_status_is_unknown_not_down():
    # "We cannot tell" is its own state; calling it DOWN invents an outage.
    verdicts = _harness("marketscan")
    assert verdicts["noStatus"]["state"] == "unknown"
    assert verdicts["registryUnreadable"]["state"] == "unknown"


def test_the_scan_pill_lives_in_the_top_nav_bar():
    # A pill only in the Market Filter tab cannot be seen from the other tabs,
    # which is where the operator was when they asked.
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    header = html.split("<header>", 1)[1].split("</header>", 1)[0]
    assert 'id="market-scan-pill"' in header


def test_the_poll_loop_drives_the_top_nav_scan_pill():
    # Isolate pollStatus first: a bare `"renderMarketScanPill(" in js` is
    # satisfied by the function's own declaration, so it would pass even if
    # nothing ever called it.
    js = APP_JS.read_text(encoding="utf-8")
    poll = js.split("async function pollStatus()", 1)[1].split(chr(10) + "async function ", 1)[0]
    assert "renderMarketScanPill(status, kpi)" in poll


# ── the Market Filter header pill says whose heartbeat it is ───────────────
#
# `#scan-state-pill` reads the TRADING loop's heartbeat (runtime/shadow_run.json
# during a rehearsal, runtime/live_poll_heartbeat.json otherwise). Unlabelled and
# sat beside "last scan: 3m ago", the operator read it as the market scan and
# concluded the scanner was disconnected. The scanner now has its own pill in the
# top nav, so this one must name the process it actually measures.

def test_the_header_pill_is_labelled_for_the_loop_it_measures():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    header = html.split('id="screener-header"', 1)[1].split("</section>", 1)[0]
    assert "TRADING LOOP" in header


def test_the_header_pill_names_its_heartbeat_file():
    # "Which file is this number from" has to be answerable from the page.
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    header = html.split('id="screener-header"', 1)[1].split("</section>", 1)[0]
    assert "shadow_run.json" in header
    assert "live_poll_heartbeat.json" in header


def test_the_header_pill_does_not_claim_to_be_the_market_scan():
    # The top-nav pill owns that claim now; two pills answering the same
    # question with different numbers is how this started.
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    header = html.split('id="screener-header"', 1)[1].split("</section>", 1)[0]
    assert "SCAN LIVE" not in header


# ── the Market Filter header is gate copy, not a dashboard ─────────────────
#
# Owner 2026-09-16: the census line, the gate line and the two readiness
# trackers ("DEPTH gathering 8.7d/14 · 3/8 markets · ...") are reference copy
# that never changes a decision at a glance. Removed; the kanban below already
# shows what each gate refused.

def test_the_screener_header_carries_no_gate_copy():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    header = html.split('id="screener-header"', 1)[1].split("</section>", 1)[0]
    assert 'id="scan-census"' not in header
    assert 'id="scan-gates"' not in header


def test_the_readiness_trackers_are_gone_but_the_ready_banner_stays():
    # "Gathering, 3 of 8 markets" is progress nobody acts on; "TRIAL READY" is
    # a decision, and it is rare. Keep the second, drop the first.
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    assert 'id="trial-trackers"' not in html
    assert 'id="trial-ready-banner"' in html
    assert "function trackerCard" not in js, "dead renderer left behind"
