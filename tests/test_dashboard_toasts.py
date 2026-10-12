"""Toast notifications for operator actions (Issue #478).

Every operator action (sync, start/stop, reset, errors) gets visible feedback
as a toast in the bottom-right corner instead of silence. The behaviors run
against the stub DOM in `tests/js/toast_harness.cjs`, so the assertions are on
rendered toasts (role, tone, text, count) rather than on a call having been
made.

Journeys under test:
1. As the Operator, each tone renders with its role (error -> alert, rest -> status).
2. As the Operator, at most 4 toasts show; the 5th evicts the oldest.
3. As the Operator, the dismiss button clears a toast and dismiss is idempotent.
4. As the Operator, toasts auto-dismiss but timeout: 0 stays until dismissed.
5. As the Operator, toasts never steal keyboard focus.
6. As the Operator, markup in a message reads as text (no HTML injection).
7. As the Operator, SYNC shows started -> success / warning / error.
8. As the Operator, the master toggle shows sent -> running / stopped / error,
   and names a service that needed forcing.
9. As the Operator, the reset modal shows finished / failed, Escape or Close
   cancels loudly, and Escape mid-reset says the run continues.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "toast_harness.cjs"
APP_JS = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "app.js"
INDEX_HTML = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "index.html"
STYLES_CSS = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "styles.css"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed on this host")

_result = None


def _run() -> dict:
    global _result
    if _result is None:
        proc = subprocess.run(
            [shutil.which("node"), str(HARNESS)],
            capture_output=True, text=True, check=True, encoding="utf-8",
            timeout=120,
        )
        _result = json.loads(proc.stdout)
    return _result


def _by_text(toasts, text):
    return [t for t in toasts if t["text"] == text]


# -- Roles & tones -----------------------------------------------------------

def test_error_toasts_alert_and_other_tones_report_status():
    out = _run()["roles"]
    by_cls = {t["cls"]: t for t in out}
    assert by_cls["toast toast-ok"]["role"] == "status"
    assert by_cls["toast toast-warn"]["role"] == "status"
    assert by_cls["toast toast-neutral"]["role"] == "status"
    assert by_cls["toast toast-error"]["role"] == "alert"


def test_every_toast_has_an_accessible_dismiss_button():
    for t in _run()["roles"]:
        assert t["hasClose"] is True
        assert t["closeLabel"] == "Dismiss notification"


# -- Stack & cap -------------------------------------------------------------

def test_fifth_toast_evicts_the_oldest_leaving_four():
    texts = [t["text"] for t in _run()["cap"]]
    assert texts == ["msg-2", "msg-3", "msg-4", "msg-5"]


# -- Dismiss -----------------------------------------------------------------

def test_close_button_dismisses_and_dismiss_is_idempotent():
    out = _run()["close"]
    assert out["before"] == 1
    assert out["afterClick"] == 0
    assert out["afterSecondDismiss"] == 0
    assert out["secondDismissThrew"] is False


# -- Auto-dismiss ------------------------------------------------------------

def test_timed_toast_dismisses_itself_but_timeout_zero_stays():
    out = _run()["auto"]
    assert out["visibleBeforeTimer"] == 1
    assert out["visibleAfterTimer"] == 0
    assert out["stickySurvives"] == 1


# -- Focus -------------------------------------------------------------------

def test_toasts_never_move_keyboard_focus():
    assert _run()["focus"]["focusCalls"] == 0


# -- Sanitization ------------------------------------------------------------

def test_markup_in_a_message_reads_as_text_not_html():
    out = _run()["sanitize"]
    assert out["textIsRaw"] is True
    assert out["innerHtmlUsed"] is False
    assert "IMG" not in out["tags"]


# -- SYNC --------------------------------------------------------------------

def test_sync_success_replaces_started_with_the_read_only_verdict():
    out = _run()["syncSuccess"]
    assert len(out["toasts"]) == 1
    toast = out["toasts"][0]
    assert toast["cls"] == "toast toast-ok"
    assert toast["text"] == "Dashboard synced with Polymarket (read-only)."
    assert out["btnText"] == "SYNCED"


def test_sync_warning_keeps_its_warn_tone_and_detail():
    out = _run()["syncWarn"]
    assert len(out["toasts"]) == 1
    toast = out["toasts"][0]
    assert toast["cls"] == "toast toast-warn"
    assert "Positions unmeasured" in toast["text"]
    assert out["btnText"] == "SYNC WARNING"


def test_sync_throw_reads_as_an_error_not_silence():
    out = _run()["syncError"]
    assert len(out["toasts"]) == 1
    toast = out["toasts"][0]
    assert toast["cls"] == "toast toast-error"
    assert "Sync failed" in toast["text"]
    assert out["btnText"] == "SYNC FAILED"


# -- Master toggle -----------------------------------------------------------

def test_master_start_shows_sent_then_running():
    texts = [(t["cls"], t["text"]) for t in _run()["masterStart"]["toasts"]]
    assert ("toast toast-neutral", "Start request sent") in texts
    assert ("toast toast-ok", "Bot is running.") in texts


def test_master_stop_shows_sent_then_stopped():
    texts = [(t["cls"], t["text"]) for t in _run()["masterStop"]["toasts"]]
    assert ("toast toast-neutral", "Stop request sent. Waiting for services to shut down.") in texts
    assert ("toast toast-ok", "Stack stopped") in texts


def test_master_refusal_reads_as_an_error():
    toasts = _run()["masterError"]["toasts"]
    errors = [t for t in toasts if t["cls"] == "toast toast-error"]
    assert len(errors) == 1
    assert "Refusing to start" in errors[0]["text"]


def test_master_partial_stop_warns_and_names_the_service():
    toasts = _run()["masterWarn"]["toasts"]
    warns = [t for t in toasts if t["cls"] == "toast toast-warn"]
    assert len(warns) == 1
    assert "decide" in warns[0]["text"] and "forced" in warns[0]["text"]


# -- Shadow rehearsal button (same master toggle, dataset.mode='shadow') -----

def test_shadow_start_shows_sent_then_ok_via_shadow_endpoint():
    out = _run()["shadowStart"]
    assert any("/api/system/shadow/start" in u for u in out["posts"])
    texts = [(t["cls"], t["text"]) for t in out["toasts"]]
    assert ("toast toast-neutral", "Start request sent") in texts
    launched = [t for t in out["toasts"] if t["cls"] == "toast toast-ok"]
    assert len(launched) == 1
    assert launched[0]["text"] == (
        "Shadow rehearsal harness-shadow launched on shadow.db"
        " for 10 min (PID 4242)."
        " Warming up — the pills turn green on the first heartbeat."
    )
    assert "Bot is running" not in launched[0]["text"]


def test_shadow_stop_refused_reads_as_an_error():
    out = _run()["shadowStopError"]
    assert any("/api/system/shadow/stop" in u for u in out["posts"])
    errors = [t for t in out["toasts"] if t["cls"] == "toast toast-error"]
    assert len(errors) == 1
    assert "No shadow rehearsal running" in errors[0]["text"]


def test_latched_shadow_launch_holds_starting_on_button_and_pill():
    out = _run()["shadowLatch"]
    assert out["pending"] is True
    assert "STARTING" in out["btnHtml"]
    assert out["btnDisabled"] is True
    assert out["btnBusy"] == "true"
    assert out["pillText"] == "SHADOW STARTING"
    assert "state-degraded" in out["pillClass"]


def test_reported_shadow_run_clears_latch_with_one_running_toast():
    out = _run()["shadowResolve"]
    assert out["result"] == "running"
    assert out["pendingAfter"] is False
    ok = [t for t in out["toasts"] if t["cls"] == "toast toast-ok"]
    assert len(ok) == 1
    assert ok[0]["text"] == "Shadow rehearsal harness-shadow is now running on shadow.db."
    # One-shot: a second poll with the same status must not double-toast.
    assert out["secondCall"] == "waiting"
    assert out["toastsAfterSecondCall"] == 1


def test_shadow_boot_timeout_clears_latch_with_a_retry_toast():
    out = _run()["shadowTimeout"]
    assert out["result"] == "timeout"
    assert out["pendingAfter"] is False
    errors = [t for t in out["toasts"] if t["cls"] == "toast toast-error"]
    assert len(errors) == 1
    assert "harness-shadow" in errors[0]["text"]
    assert "Press START SHADOW to retry" in errors[0]["text"]


# -- Reset modal -------------------------------------------------------------

def test_reset_cancel_via_close_says_nothing_changed():
    out = _run()["resetCancelClose"]
    assert out["modalOpen"] is True
    assert _by_text(out["toasts"], "Reset cancelled. Nothing was changed.")


def test_reset_cancel_via_escape_says_nothing_changed():
    out = _run()["resetCancelEscape"]
    assert _by_text(out["toasts"], "Reset cancelled. Nothing was changed.")


def test_reset_confirm_ok_points_at_the_steps():
    out = _run()["resetConfirmOk"]
    ok = [t for t in out["toasts"] if t["cls"] == "toast toast-ok"]
    assert len(ok) == 1
    assert ok[0]["text"] == "Reset finished. Check the reset steps for warnings."


def test_reset_confirm_refused_reads_as_an_error():
    out = _run()["resetConfirmFail"]
    errors = [t for t in out["toasts"] if t["cls"] == "toast toast-error"]
    assert len(errors) == 1
    assert errors[0]["text"] == "Failed: bot running"


def test_escape_mid_reset_says_the_run_continues():
    out = _run()["resetInFlightEscape"]
    assert _by_text(out["inFlightToasts"], "Dialog closed. The reset is still running.")


def test_escape_after_a_successful_reset_is_not_a_cancel():
    out = _run()["resetEscapeAfterSuccess"]
    assert out["modalOpen"] is False
    # Only the success toast: no "Reset cancelled. Nothing was changed."
    assert len(out["toasts"]) == 1
    assert out["toasts"][0]["cls"] == "toast toast-ok"


# -- Static contracts (no browser needed) ------------------------------------

def test_toast_container_mounts_before_the_reset_modal():
    html = INDEX_HTML.read_text(encoding="utf-8")
    container = html.index('id="toast-container"')
    modal = html.index('id="reset-modal"')
    assert container < modal
    assert 'aria-live="polite"' in html[container:container + 200]


def test_toast_styles_use_semantic_tokens_and_respect_reduced_motion():
    css = STYLES_CSS.read_text(encoding="utf-8")
    for token in ("--green-border", "--amber-border", "--red-border",
                  "--green-bg", "--amber-bg", "--red-bg",
                  "--bg-surface-raised", "--text-primary"):
        assert token in css
    assert ".toast-container" in css
    assert ".toast-close" in css
    assert "44px" in css
    assert "prefers-reduced-motion" in css


def test_toast_text_is_set_as_text_not_html():
    src = APP_JS.read_text(encoding="utf-8")
    assert "function showToast" in src
    assert "textContent" in src
    assert "showToast" in src[src.index("module.exports"):]
    toast_fn = src[src.index("function showToast"):src.index("function dismissResetModal")]
    assert "innerHTML" not in toast_fn


def test_dashboard_script_parses():
    proc = subprocess.run(["node", "--check", str(APP_JS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, f"app.js parse error: {proc.stderr}"
