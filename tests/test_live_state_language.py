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
