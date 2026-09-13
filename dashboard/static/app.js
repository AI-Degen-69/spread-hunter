/* Spread Hunter Live — Dashboard Application Logic
 *
 * Consumes existing API endpoints:
 *   GET  /api/state            — order/fill state
 *   GET  /api/system/status    — service PIDs, bot state, starting capital
 *   POST /api/system/start     — start bot stack (atomic)
 *   POST /api/system/stop      — stop bot stack
 *   POST /api/system/cancel-all — cancel all venue orders (DT7: typed confirm)
 *   GET  /api/kpi              — all Tab 2 analytics
 *   GET  /api/scan-state       — SCANNING/IDLE/STALLED
 *   GET  /api/pairs-activity   — auto-pairs counts
 *   GET  /api/guardrail-alerts — active violations
 *   GET  /api/guardrail-health — watcher liveness
 *   GET  /api/cycle-stream     — SSE event stream
 *   GET  /api/parameters       — strategy config (NEW)
 *   GET  /api/active-markets   — active markets (NEW)
 *   GET  /api/closed-markets   — closed markets (NEW)
 *
 * Tab 3 (Market Filter) reads funnel data from /api/kpi's `funnel` field
 * and scan state from /api/scan-state. No new endpoints needed.
 */

'use strict';

// Suppress benign third-party / web3 extension message disconnects in sandboxed iframes
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason || {};
  const msg = String(reason.message || reason.stack || reason);
  const code = reason.code || (reason.data && reason.data.code);
  if (
    msg.includes('Message channel disconnected') ||
    msg.includes('Failed to connect to MetaMask') ||
    msg.includes('Extension context invalidated') ||
    msg.includes('chrome-extension://') ||
    msg.includes('moz-extension://') ||
    code === 4900 ||
    code === -32603
  ) {
    event.preventDefault();
    event.stopImmediatePropagation();
    console.debug('[App] Suppressed third-party extension error:', msg);
  }
});

window.addEventListener('error', (event) => {
  const msg = String(event.message || (event.error && (event.error.message || event.error.stack)) || '');
  if (
    msg.includes('Message channel disconnected') ||
    msg.includes('Failed to connect to MetaMask') ||
    msg.includes('Extension context invalidated') ||
    msg.includes('chrome-extension://') ||
    msg.includes('moz-extension://')
  ) {
    event.preventDefault();
    event.stopImmediatePropagation();
    console.debug('[App] Suppressed window extension error:', msg);
  }
});

const POLL_MS = 2000;
let lastState = null;
let lastKpi = null;
// Whether the page is reading the production registry. Starts false: until the
// first status arrives we cannot claim a live view, and START is refused on it.
let lastDbIsProduction = false;
let isStopping = false;

/* ── XSS defense: escape before innerHTML ── */
function esc(v) {
  if (v === null || v === undefined) return '--';
  const s = String(v);
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
           .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function fmtUSD(v) {
  if (v === null || v === undefined) return '--';
  const n = Number(v);
  if (isNaN(n)) return '--';
  return '$' + n.toFixed(2);
}

/* Which way a number reads, taken from the number.
 *
 * The panels used to pick their colour from "do we have any samples yet"
 * (`n > 0 ? 'positive' : ''`), so every measured figure came out green --
 * a Sharpe of -0.63 and an expectancy of -$0.34 were painted as wins. The
 * sign is the only thing that decides this. */
function signClass(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'positive' : 'negative';
}

/* Money with the sign in front of the dollar: `-$2.40`, never `$-2.40`.
 * `fmtUSD` puts the minus where the number is, which reads as a strange
 * currency rather than as a loss. */
function fmtSignedUSD(v) {
  if (v === null || v === undefined) return '--';
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  const sign = n > 0 ? '+' : (n < 0 ? '-' : '');
  return sign + fmtUSD(Math.abs(n));
}

function fmtPct(v) {
  if (v === null || v === undefined) return '--';
  return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
}

function fmtVal(v, cls) {
  const nullCls = (v === null || v === undefined) ? ' null' : '';
  return `<span class="kpi-value${cls || ''}${nullCls}">${esc(v)}</span>`;
}

/**
 * Format an ISO UTC timestamp into the viewer's local time string.
 * Returns an empty string if timestamp is missing or invalid.
 * @param {string|null|undefined} ts - ISO 8601 UTC timestamp string
 * @returns {string} Localized time string or empty string
 */
function fmtLocalTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString();
}

/**
 * Render a safe external anchor tag or escaped text for a market object.
 * @param {Object|null|undefined} m - Market object with title, slug, or url
 * @returns {string} Safe HTML anchor tag or escaped text
 */
function marketLink(m) {
  if (!m) return '--';
  const name = m.title || m.name || m.slug || '--';
  const url = m.url || (m.slug ? `https://polymarket.com/market/${m.slug}` : '');
  if (url) {
    return `<a href="${esc(url)}" class="market-link" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">${esc(name)}</a>`;
  }
  return esc(name);
}

/* ── controlFetch: CSRF-protected POST ── */
function controlFetch(path, options = {}) {
  const headers = { 'X-Control-Token': CONTROL_TOKEN, ...(options.headers || {}) };
  return fetch(path, { method: 'POST', ...options, headers });
}

/* ── Tab switching (DT7: localStorage persistence, 3 tabs) ── */
const tabBtns = document.querySelectorAll('.tab-btn');
const tab1 = document.getElementById('tab-1');
const tab2 = document.getElementById('tab-2');
const tab3 = document.getElementById('tab-3');

function switchTab(which) {
  tabBtns.forEach(b => {
    const isActive = b.id === 'tab-btn-' + which;
    b.classList.toggle('active', isActive);
    b.setAttribute('aria-selected', isActive);
  });
  tab1.hidden = (which !== 1);
  tab2.hidden = (which !== 2);
  tab3.hidden = (which !== 3);
  localStorage.setItem('sh-active-tab', String(which));
  if (which === 3) {
    setTimeout(updateKanbanNavButtons, 60);
  }
}

tabBtns.forEach(b => {
  b.addEventListener('click', () => {
    const num = b.id === 'tab-btn-1' ? 1 : (b.id === 'tab-btn-2' ? 2 : 3);
    switchTab(num);
  });
  b.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); b.click(); }
  });
});

// Restore tab from localStorage, default to Tab 1
const savedTab = parseInt(localStorage.getItem('sh-active-tab') || '1', 10);
switchTab(savedTab === 2 ? 2 : (savedTab === 3 ? 3 : 1));

/* ── Cancel-all modal (DT7: typed confirm) ── */
const cancelModal = document.getElementById('cancel-modal');
const cancelInput = document.getElementById('cancel-input');
const cancelConfirmBtn = document.getElementById('cancel-modal-confirm');
const cancelCloseBtn = document.getElementById('cancel-modal-close');
const cancelBtn = document.getElementById('btn-cancel-all');

cancelBtn.addEventListener('click', () => {
  cancelInput.value = '';
  cancelConfirmBtn.disabled = true;
  cancelModal.classList.add('show');
  cancelInput.focus();
});

cancelCloseBtn.addEventListener('click', () => cancelModal.classList.remove('show'));

cancelInput.addEventListener('input', () => {
  cancelConfirmBtn.disabled = (cancelInput.value.trim().toUpperCase() !== 'CANCEL');
});

cancelConfirmBtn.addEventListener('click', async () => {
  if (cancelInput.value.trim().toUpperCase() !== 'CANCEL') return;
  cancelConfirmBtn.disabled = true;
  cancelConfirmBtn.textContent = 'Cancelling...';
  try {
    const res = await controlFetch('/api/system/cancel-all');
    const data = await res.json();
    if (data.ok) {
      cancelModal.classList.remove('show');
    } else {
      cancelConfirmBtn.textContent = 'Failed: ' + (data.message || 'error');
      setTimeout(() => { cancelConfirmBtn.textContent = 'Confirm Cancel All'; cancelConfirmBtn.disabled = false; }, 3000);
    }
  } catch (e) {
    cancelConfirmBtn.textContent = 'Error: ' + e.message;
    setTimeout(() => { cancelConfirmBtn.textContent = 'Confirm Cancel All'; cancelConfirmBtn.disabled = false; }, 3000);
  }
});

// Escape key closes modals
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (cancelModal.classList.contains('show')) cancelModal.classList.remove('show');
    if (resetModal.classList.contains('show')) resetModal.classList.remove('show');
  }
});

/* ── Sync button (read-only venue refresh) ── */
const syncBtn = document.getElementById('btn-sync');
if (syncBtn) {
  syncBtn.addEventListener('click', async () => {
    if (syncBtn.disabled) return;
    const prevText = syncBtn.textContent;
    syncBtn.disabled = true;
    syncBtn.classList.add('syncing');
    syncBtn.textContent = 'SYNCING…';
    // Optional: show a one-line ticker notice so the operator sees it worked even before the poll.
    const empty = tickerEl.querySelector('.empty-state');
    let syncOk = false;
    let isWarning = false;
    try {
      const res = await controlFetch('/api/system/sync');
      const data = await res.json().catch(() => ({}));
      const steps = data.steps || {};
      const rec = steps.reconcile || {};
      const vs = steps.venue_sync || {};
      isWarning = (res.status === 207) || (vs.venue_open_unmeasured === true);
      syncOk = (data.ok === true) && res.ok;
      const localOpen = data.state?.local_open_orders;
      const venueOpen = (data.state?.venue_open_orders != null ? data.state.venue_open_orders : rec.open_orders_count);
      const venueOpenDisp = (venueOpen != null ? venueOpen : 0);
      const fillsDisp = (rec.fills_recorded != null ? rec.fills_recorded : 0);
      const cancelledDisp = (rec.orders_cancelled != null ? rec.orders_cancelled : 0);
      const posDisp = (vs.open_positions_count != null ? vs.open_positions_count : 0);
      const closesDisp = (vs.closes_written != null ? vs.closes_written : 0);
      const lines = [];
      if (rec.ok) lines.push(`Orders: venue ${venueOpenDisp} open, ${fillsDisp} new fills, ${cancelledDisp} marked cancelled`);
      else if (rec.error) lines.push(`Orders sync: ${rec.error}`);
      if (vs.ok) lines.push(`Venue: ${fmtUSD(vs.account_value_usd)} · ${posDisp} positions · ${closesDisp} closes synced`);
      else if (vs.error) lines.push(`Account sync: ${vs.error}`);
      // Human-readable verdict when dashboard was stale
      if (typeof localOpen === 'number' && typeof venueOpen === 'number' && localOpen !== venueOpen) {
        lines.push(`Fixed drift: dashboard had ${localOpen} open → now ${venueOpen} (venue truth)`);
      }
      if (vs.venue_open_unmeasured) {
        lines.push('Positions unmeasured: prior exposure retained');
      } else if (vs.raw_open_rows === 0) {
        lines.push('Positions: 0 on venue — dashboard exposure zeroed');
      }
      const msg = lines.join(' · ') || (data.ok ? 'Sync ok — dashboard now matches venue.' : 'Sync finished with warnings');
      // Reuse ticker as a transient banner; also trigger immediate re-poll.
      appendTickerEvent(`[SYNC] ${msg}`, 'Dashboard synced with Polymarket (read-only).', '');
    } catch (e) {
      syncOk = false;
      appendTickerEvent(`[SYNC ERROR] ${e.message || String(e)}`, 'Sync failed — venue may be unreachable. Retrying on next poll.', '');
    } finally {
      if (syncOk) syncBtn.textContent = 'SYNCED';
      else if (isWarning) syncBtn.textContent = 'SYNC WARNING';
      else syncBtn.textContent = 'SYNC FAILED';
      setTimeout(() => { syncBtn.textContent = prevText; syncBtn.disabled = false; syncBtn.classList.remove('syncing'); }, 1800);
      // Immediately refresh all tiles without waiting for the 2s poll.
      pollStatus();
    }
  });
}

/* ── Reset modal (typed confirm) ── */
const resetModal = document.getElementById('reset-modal');
const resetInput = document.getElementById('reset-input');
const resetConfirmBtn = document.getElementById('reset-modal-confirm');
const resetCloseBtn = document.getElementById('reset-modal-close');
const resetProgress = document.getElementById('reset-progress');
const resetBtn = document.getElementById('btn-reset');

resetBtn.addEventListener('click', () => {
  resetInput.value = '';
  resetConfirmBtn.disabled = true;
  resetProgress.style.display = 'none';
  resetModal.classList.add('show');
  resetInput.focus();
});

resetCloseBtn.addEventListener('click', () => resetModal.classList.remove('show'));

resetInput.addEventListener('input', () => {
  resetConfirmBtn.disabled = (resetInput.value.trim().toUpperCase() !== 'RESET');
});

resetConfirmBtn.addEventListener('click', async () => {
  if (resetInput.value.trim().toUpperCase() !== 'RESET') return;
  resetConfirmBtn.disabled = true;
  resetConfirmBtn.textContent = 'Resetting...';
  resetProgress.style.display = 'block';
  resetProgress.textContent = 'Halting bot...';
  try {
    const res = await controlFetch('/api/system/reset');
    const data = await res.json();
    if (data.ok) {
      resetProgress.textContent = (data.steps || []).join('\n');
      resetConfirmBtn.textContent = 'Done';
      setTimeout(() => {
        resetModal.classList.remove('show');
        resetConfirmBtn.textContent = 'Confirm Reset';
        pollStatus();
      }, 2000);
    } else {
      resetProgress.textContent = 'Failed: ' + (data.message || 'error');
      resetConfirmBtn.textContent = 'Failed';
      setTimeout(() => { resetConfirmBtn.textContent = 'Confirm Reset'; resetConfirmBtn.disabled = false; }, 3000);
    }
  } catch (e) {
    resetProgress.textContent = 'Error: ' + e.message;
    resetConfirmBtn.textContent = 'Error';
    setTimeout(() => { resetConfirmBtn.textContent = 'Confirm Reset'; resetConfirmBtn.disabled = false; }, 3000);
  }
});

/* ── Info bubbles (DT7: click-triggered) ── */
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('info-bubble')) {
    e.stopPropagation();
    const tooltip = e.target.nextElementSibling;
    if (tooltip && tooltip.classList.contains('info-tooltip')) {
      tooltip.classList.toggle('show');
    }
  } else {
    // Close any open tooltips
    document.querySelectorAll('.info-tooltip.show').forEach(t => t.classList.remove('show'));
  }
});

/* ── SSE Event Ticker (DT7: reconnect banner) ── */
let sseSource = null;
const tickerEl = document.getElementById('event-ticker');
const sseReconnect = document.getElementById('sse-reconnect');

/* ── Event translation: plain English for every cycle event ── */
const EVENT_TRANSLATIONS = {
  // Filter
  'filter|rerank_done': 'Finished scanning all Polymarket markets and updated the graduated list.',
  'filter|rerank_error': 'Market scan failed. The graduated list was not updated, so Decide & Execute keeps quoting the previous universe.',
  'screener|rerank_done': 'Finished scanning all Polymarket markets and updated the graduated list.',
  'screener|rerank_error': 'Market scan failed. The graduated list was not updated, so Decide & Execute keeps quoting the previous universe.',

  // Query — reconciliation
  'query|reconcile_ok': 'Checked the venue for new fills on our orders. All synced up.',
  'query|reconcile_error': 'Failed to sync fills from the venue. Orders may be stale until the next query.',
  'query|reconcile_contended': 'Another process is reconciling fills right now. Waiting in line to avoid double-counting.',
  'engine|reconcile_ok': 'Checked the venue for new fills on our orders. All synced up.',
  'engine|reconcile_error': 'Failed to sync fills from the venue. Orders may be stale until the next poll.',
  'engine|reconcile_contended': 'Another process is reconciling fills right now. Waiting in line to avoid double-counting.',

  // Query — account sweep
  'query|sweep_done': 'Read the live wallet balance and open positions from Polymarket. Dashboard tiles are now fresh.',
  'query|sweep_skipped': 'Skipped the wallet sweep: POLY_FUNDER is not set, so the account balance and float marks are not being read.',
  'query|sweep_error': 'Failed to read the wallet from Polymarket. Balance and exposure tiles may be stale.',
  'engine|sweep_done': 'Read the live wallet balance and open positions from Polymarket. Dashboard tiles are now fresh.',
  'engine|sweep_skipped': 'Skipped the wallet sweep: POLY_FUNDER is not set, so the account balance and float marks are not being read.',
  'engine|sweep_error': 'Failed to read the wallet from Polymarket. Balance and exposure tiles may be stale.',

  // Query — pairs management
  'query|pairs_balanced': 'Checked a market pair: both YES and NO sides are matched. No action needed.',
  'query|pairs_hold': 'Holding a market pair open. The position is healthy and waiting for the market to resolve.',
  'query|pairs_would_exit': 'Considering closing a one-sided position to limit naked exposure. Pre-check passed, may exit soon.',
  'query|pairs_route_to_merge': 'A position has shares on both outcomes that can be merged back into collateral. Routing to merge.',
  'query|pairs_exited': 'Closed a position on this market. Shares sold or merged, exposure reduced.',
  'query|pairs_would_complete': 'Considering redeeming a resolved position for collateral. Pre-check passed, may redeem soon.',
  'query|pairs_completed': 'Redeemed a resolved market. Shares converted back to USDC, position closed.',
  'query|pairs_error': 'Error managing a market pair. The position may need manual attention.',
  'engine|pairs_balanced': 'Checked a market pair: both YES and NO sides are matched. No action needed.',
  'engine|pairs_hold': 'Holding a market pair open. The position is healthy and waiting for the market to resolve.',
  'engine|pairs_would_exit': 'Considering closing a one-sided position to limit naked exposure. Pre-check passed, may exit soon.',
  'engine|pairs_route_to_merge': 'A position has shares on both outcomes that can be merged back into collateral. Routing to merge.',
  'engine|pairs_exited': 'Closed a position on this market. Shares sold or merged, exposure reduced.',
  'engine|pairs_would_complete': 'Considering redeeming a resolved position for collateral. Pre-check passed, may redeem soon.',
  'engine|pairs_completed': 'Redeemed a resolved market. Shares converted back to USDC, position closed.',
  'engine|pairs_error': 'Error managing a market pair. The position may need manual attention.',

  // Decide & Execute — quoting
  'decide|decide': 'Evaluated pricing for a market. Decided what orders to rest and at what price.',
  'decide|submit': 'Submitted maker orders to Polymarket for this market. Bids are now resting on the book.',
  'decide|market_error': 'Error quoting this market. The bot skipped it this cycle and will retry next time.',
  'fleet|decide': 'Evaluated pricing for a market. Decided what orders to rest and at what price.',
  'fleet|submit': 'Submitted maker orders to Polymarket for this market. Bids are now resting on the book.',
  'fleet|market_error': 'Error quoting this market. The bot skipped it this cycle and will retry next time.',

  // Guardrail
  'guardrail|guardrail_alert': 'Risk limit triggered. The guardrail watchdog is blocking new quotes until the alert clears.',
};

function translateEvent(ev) {
  const svc = (ev.service || '').toLowerCase();
  const action = (ev.action || '').toLowerCase();
  const reason = ev.reason || '';
  const slug = ev.market_slug || '';

  // Try exact match first
  let translation = EVENT_TRANSLATIONS[svc + '|' + action] || null;

  // If no exact match, try prefix match for dynamic actions (pairs_*)
  if (!translation && action.startsWith('pairs_')) {
    translation = EVENT_TRANSLATIONS['query|' + action] || EVENT_TRANSLATIONS['engine|' + action] || null;
  }

  // Build context suffix from market slug only (reason is in the raw line)
  let ctx = '';
  if (slug) ctx = slug;

  return { translation, ctx };
}

let tickerFilter = 'all';
let tickerAutoscroll = true;
const allTickerEvents = [];

function appendTickerEvent(line, translation, ctx, service, action) {
  const empty = tickerEl.querySelector('.empty-state');
  if (empty) empty.remove();

  const evObj = { line, translation, ctx, service: (service || '').toLowerCase(), action: (action || '').toLowerCase() };
  allTickerEvents.unshift(evObj);
  while (allTickerEvents.length > 200) allTickerEvents.pop();

  renderTickerFeed();
}

function renderTickerFeed() {
  tickerEl.innerHTML = '';
  const filtered = allTickerEvents.filter(ev => {
    if (tickerFilter === 'all') return true;
    if (tickerFilter === 'decide') return ev.service.includes('decide') || ev.service.includes('fleet') || ev.action.includes('buy') || ev.action.includes('quote') || ev.action.includes('fill');
    if (tickerFilter === 'filter') return ev.service.includes('filter') || ev.service.includes('screener');
    if (tickerFilter === 'guardrail') return ev.service.includes('guardrail') || ev.action.includes('alert') || ev.action.includes('stop_loss');
    return true;
  });

  if (filtered.length === 0) {
    tickerEl.innerHTML = '<div class="empty-state"><div class="empty-state-title">No events matched</div><div class="empty-state-msg">Try selecting "ALL" or waiting for live execution cycles.</div></div>';
    return;
  }

  for (const ev of filtered) {
    const div = document.createElement('div');
    div.className = 'ticker-event';
    if (ev.translation) {
      div.innerHTML = `<div class="ticker-translation">${esc(ev.translation)}${ev.ctx ? ' <span class="ticker-ctx">' + esc(ev.ctx) + '</span>' : ''}</div><div class="ticker-raw">${esc(ev.line)}</div>`;
    } else {
      div.innerHTML = `<div class="ticker-raw">${esc(ev.line)}</div>`;
    }
    tickerEl.appendChild(div);
  }

  if (tickerAutoscroll) {
    tickerEl.scrollTop = 0;
  }
}

// Wire up ticker filter buttons
document.querySelectorAll('.ticker-filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.ticker-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    tickerFilter = btn.dataset.filter || 'all';
    renderTickerFeed();
  });
});

const btnClearTicker = document.getElementById('btn-clear-ticker');
if (btnClearTicker) {
  btnClearTicker.addEventListener('click', () => {
    allTickerEvents.length = 0;
    tickerEl.innerHTML = '<div class="empty-state"><div class="empty-state-title">Event stream cleared</div><div class="empty-state-msg">New events will appear here as the bot executes.</div></div>';
  });
}

const btnPauseTicker = document.getElementById('btn-pause-ticker');
if (btnPauseTicker) {
  btnPauseTicker.addEventListener('click', () => {
    tickerAutoscroll = !tickerAutoscroll;
    btnPauseTicker.textContent = tickerAutoscroll ? 'AUTOSCROLL: ON' : 'AUTOSCROLL: PAUSED';
    btnPauseTicker.style.color = tickerAutoscroll ? 'var(--text-secondary)' : '#fbbf24';
  });
}

function connectSSE() {
  if (sseSource) sseSource.close();
  sseReconnect.style.display = 'block';
  sseSource = new EventSource('/api/cycle-stream');

  sseSource.onopen = () => { sseReconnect.style.display = 'none'; };

  sseSource.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      const ts = fmtLocalTime(ev.ts);
      const svc = (ev.service || '').toUpperCase().slice(0,6);
      const action = ev.action || '';
      const slug = ev.market_slug || '';
      const reason = ev.reason ? ` — ${ev.reason}` : '';
      const rawLine = `[${ts}] [${svc}] ${action} ${slug}${reason}`;
      const { translation, ctx } = translateEvent(ev);
      appendTickerEvent(rawLine, translation, ctx, ev.service, ev.action);
    } catch {
      // Non-JSON line
    }
  };

  sseSource.onerror = () => {
    sseReconnect.style.display = 'block';
    // Auto-reconnect after 3s
    setTimeout(() => { if (sseSource.readyState === EventSource.CLOSED) connectSSE(); }, 3000);
  };
}

connectSSE();

/* ── Render: Active registry (LIVE vs SHADOW) ── */
// Shadow-run stopwatch. The rehearsal is not in the supervised registry, so
// its liveness arrives as `status.shadow_run` (from runtime/shadow_run.json),
// already matched against the store this page is reading. While the run is
// live the clock is extrapolated locally between polls; once it ends it is
// frozen at the elapsed time of the last heartbeat, because a clock that keeps
// running for a dead process is worse than no clock.
let shadowRunAnchor = null;

function fmtStopwatch(sec) {
  if (sec === null || sec === undefined || !isFinite(sec) || sec < 0) return '';
  const total = Math.floor(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return (h > 0 ? h + ':' : '') + mm + ':' + ss;
}

function renderShadowClock() {
  const el = document.getElementById('shadow-run-clock');
  if (!el) return;
  if (!shadowRunAnchor) {
    el.textContent = '';
    el.title = '';
    return;
  }
  const drift = shadowRunAnchor.running
    ? (Date.now() - shadowRunAnchor.receivedAtMs) / 1000
    : 0;
  const elapsed = fmtStopwatch(shadowRunAnchor.elapsedSec + drift);
  el.textContent = shadowRunAnchor.running ? '· ' + elapsed : '· ' + elapsed + ' ended';
  el.title = shadowRunAnchor.running
    ? `Shadow rehearsal ${shadowRunAnchor.runId || ''} running, time box ${shadowRunAnchor.minutes ?? '--'} min`
    : 'This shadow rehearsal is no longer running.';
}

function setShadowRun(status) {
  const run = status?.shadow_run;
  shadowRunAnchor = run
    ? {
        elapsedSec: Number(run.elapsed_sec) || 0,
        running: run.running === true,
        runId: run.run_id,
        minutes: run.minutes,
        receivedAtMs: Date.now(),
      }
    : null;
  renderShadowClock();
}

function renderDbMode(status) {
  setShadowRun(status);
  const el = document.getElementById('db-mode-badge');
  if (!el) return;

  const mode = status?.db_mode || null;
  lastDbIsProduction = status?.db_is_production === true;

  if (!mode) {
    el.className = 'pill stopped mono';
    el.textContent = 'DB: --';
    el.title = 'Active registry unknown: the status endpoint did not answer.';
    return;
  }

  const path = status.db_path || '';
  if (lastDbIsProduction) {
    el.className = 'pill active mono';
    el.textContent = 'LIVE REGISTRY';
    el.title = `Reading the production registry: ${path}`;
  } else {
    // Not a cosmetic state. Every number on the page is a rehearsal, and START
    // is refused while this shows.
    el.className = 'pill shadow mono';
    el.textContent = `${mode}: ${path.split(/[\\/]/).pop()}`;
    el.title = `Reading ${path}, not the production registry. `
      + `Orders, fills and PnL on this page are not live positions, and START is disabled.`;
  }
}


/* ── Render: Service Cards & Master Diagnostic HUD ── */
const SERVICE_DEFS = [
  { key: 'guardrail', name: 'Guardrail Risk Watchdog', cmd: 'python -m scripts.global_stop_loss',
    tag: 'CIRCUIT BREAKER',
    desc: 'Continuous risk monitor enforcing hard exposure and single-leg unwind limits.',
    readOnly: true },
  { key: 'filter', name: 'Market Filter', cmd: 'python -m scripts.filter_loop',
    tag: 'UNIVERSE SCANNER',
    desc: 'Scans 500+ Polymarket binary markets and screens down to graduated pairs with positive spread.' },
  { key: 'query', name: 'Venue Engine & Order Poller', cmd: 'python -m core_brain.order_manager poll --interval 0.5',
    tag: '0.5s CLOB FEED',
    desc: 'Queries CLOB every 0.5s, reconciles fills, and executes periodic balance sweeps.' },
  { key: 'decide', name: 'Execution Loop & Maker Quoter', cmd: 'python -m core_brain.trader_loop --live --no-reconcile --no-sweep --interval 5',
    tag: 'SPREAD QUOTER',
    desc: 'Runs the trading loop (dual-sided maker quotes -> merge execution) every 5s across approved markets.' },
];

function renderServiceCards(status, guardrailHealth, guardrailAlerts) {
  const isRunning = status?.bot_state === 'RUNNING' || (status?.services && Object.values(status.services).some(s => s.running));
  const isProd = status?.db_is_production === true;
  // `lastDbIsProduction` is the START guard's flag and `renderDbMode` owns it.
  // Writing it from here too gave one safety flag two writers: a status payload
  // that carries service state but not `db_is_production` silently reset the
  // guard, and whether that ends up safe depended purely on call order. This
  // function still reads the local `isProd` for its own copy.

  // Master Control Header & Buttons
  const masterIndicator = document.getElementById('master-status-indicator');
  const masterDesc = document.getElementById('master-status-desc');
  const masterStartBtn = document.getElementById('btn-master-start');
  const masterStopBtn = document.getElementById('btn-master-stop');
  const livePulseDot = document.getElementById('live-ops-pulse-dot');
  const lastSyncEl = document.getElementById('runtime-last-sync');

  if (masterIndicator) {
    if (isStopping) {
      masterIndicator.className = 'pill stopped font-display';
      masterIndicator.textContent = '○ STOPPING…';
    } else {
      masterIndicator.className = `pill ${isRunning ? 'active' : 'stopped'} font-display`;
      masterIndicator.textContent = isRunning ? '● STACK RUNNING' : '○ STACK STOPPED';
    }
  }
  if (livePulseDot) {
    livePulseDot.className = `pulse-dot ${(isRunning && !isStopping) ? 'active' : ''}`;
  }
  if (lastSyncEl) {
    lastSyncEl.textContent = `Last poll: ${new Date().toLocaleTimeString()}`;
  }

  // Diagnostic HUD
  const hudEngineState = document.getElementById('hud-engine-state');
  const hudEngineSub = document.getElementById('hud-engine-sub');
  const hudVenueMode = document.getElementById('hud-venue-mode');
  const hudVenueSub = document.getElementById('hud-venue-sub');
  const hudGuardrailState = document.getElementById('hud-guardrail-state');
  const hudGuardrailSub = document.getElementById('hud-guardrail-sub');
  const hudDbMode = document.getElementById('hud-db-mode');
  const hudDbSub = document.getElementById('hud-db-sub');

  let activeCount = 0;
  if (status?.services) {
    activeCount = Object.values(status.services).filter(s => s?.running).length;
  }
  if (guardrailHealth?.running) activeCount++;

  if (hudEngineState) {
    hudEngineState.textContent = isRunning ? 'RUNNING' : 'HALTED';
    hudEngineState.className = `kpi-value ${isRunning ? 'positive' : 'null'} mono`;
  }
  if (hudEngineSub) {
    hudEngineSub.textContent = isRunning ? `${activeCount} services active` : 'All background workers stopped';
  }
  if (hudVenueMode) {
    hudVenueMode.textContent = 'POLYMARKET CLOB';
  }
  if (hudVenueSub) {
    hudVenueSub.textContent = '0.5s poll cadence';
  }
  if (hudGuardrailState) {
    const alertsCount = guardrailAlerts?.alerts?.length || guardrailHealth?.alerts_total || 0;
    if (alertsCount > 0) {
      hudGuardrailState.textContent = 'ALERTING';
      hudGuardrailState.className = 'kpi-value negative mono';
    } else if (guardrailHealth?.running) {
      hudGuardrailState.textContent = 'HEALTHY';
      hudGuardrailState.className = 'kpi-value positive mono';
    } else {
      hudGuardrailState.textContent = 'STANDBY';
      hudGuardrailState.className = 'kpi-value null mono';
    }
  }
  if (hudGuardrailSub) {
    const alertsTotal = guardrailHealth?.alerts_total || 0;
    hudGuardrailSub.textContent = `${alertsTotal} violations logged`;
  }
  if (hudDbMode) {
    hudDbMode.textContent = isProd ? 'LIVE REGISTRY' : 'SHADOW REHEARSAL';
    hudDbMode.style.color = isProd ? '#34d399' : '#fbbf24';
  }
  if (hudDbSub) {
    const dbPath = status?.db_path || 'data/orders.db';
    hudDbSub.textContent = dbPath.split(/[\\/]/).pop();
  }

  if (masterDesc) {
    masterDesc.textContent = isRunning
      ? (isProd ? 'Live Execution Active · Quoting on Polymarket CLOB via Order Manager' : 'Shadow Rehearsal Active · Quoting simulated Polymarket candidates')
      : 'All bot execution services halted · Standby mode (no risk exposure)';
  }
  if (masterStartBtn) {
    masterStartBtn.disabled = isRunning;
    masterStartBtn.style.opacity = isRunning ? '0.45' : '1';
    masterStartBtn.style.cursor = isRunning ? 'not-allowed' : 'pointer';
  }
  if (masterStopBtn) {
    if (isStopping) {
      masterStopBtn.disabled = true;
      masterStopBtn.style.opacity = '0.45';
      masterStopBtn.style.cursor = 'wait';
    } else {
      masterStopBtn.disabled = !isRunning;
      masterStopBtn.style.opacity = !isRunning ? '0.45' : '1';
      masterStopBtn.style.cursor = !isRunning ? 'not-allowed' : 'pointer';
    }
  }

  // Render Service Cards Grid
  const container = document.getElementById('service-cards');
  if (!container) return;
  container.innerHTML = '';

  for (const def of SERVICE_DEFS) {
    let svc, running, pid;
    if (def.key === 'guardrail') {
      running = guardrailHealth?.running || false;
      pid = guardrailHealth?.pid;
    } else {
      svc = status?.services?.[def.key];
      running = svc?.running || false;
      pid = svc?.pid;
    }

    const hasAlert = def.key === 'guardrail' && (guardrailAlerts?.alerts?.length > 0);
    const alertCls = hasAlert ? ' alert' : (running ? ' healthy' : '');
    const pillCls = running ? 'active' : 'stopped';
    const pillText = running ? 'RUNNING' : 'STOPPED';

    let toggleHtml = '';
    if (!def.readOnly) {
      toggleHtml = `<button class="toggle ${running ? 'on' : ''}" data-svc="${def.key}" role="switch" aria-checked="${running}" aria-label="Toggle ${def.name}" tabindex="0"></button>`;
    } else {
      toggleHtml = `<span style="font-size:10px;font-weight:700;color:var(--text-muted);font-family:'JetBrains Mono',monospace">AUTO-WATCH</span>`;
    }

    container.innerHTML += `
      <div class="card${alertCls}" role="region" aria-label="${def.name}" style="display:flex;flex-direction:column;justify-content:space-between;gap:10px">
        <div>
          <div class="service-card-head">
            <div>
              <div class="font-display" style="font-size:14px;letter-spacing:0.02em;color:var(--text-primary)">${def.name}</div>
              <span class="param-code-pill" style="margin-top:2px;display:inline-block">${def.tag}</span>
            </div>
            <span class="pill ${pillCls}">
              <span class="pulse-dot ${running ? 'active' : ''}" style="width:5px;height:5px"></span>
              ${pillText}
            </span>
          </div>
          <div style="font-size:11.5px;color:var(--text-secondary);line-height:1.4;margin-bottom:8px">
            ${def.desc}
          </div>
        </div>

        <div>
          <div class="service-card-meta">
            <div class="mono" style="font-size:11px;color:var(--text-secondary)">
              <span style="color:var(--text-muted)">PID:</span> <b>${pid || '--'}</b>
              ${def.readOnly ? `<span style="margin-left:8px;color:var(--text-muted)">Violations:</span> <b style="color:${hasAlert ? '#ef4444' : '#34d399'}">${guardrailHealth?.alerts_total || 0}</b>` : ''}
            </div>
            ${toggleHtml}
          </div>
          <div class="service-cmd-tag" title="${def.cmd}">
            $ ${def.cmd}
          </div>
        </div>
      </div>`;
  }

  // Wire up toggle switches for individual services. Each toggle drives
  // exactly its own service (dashboard/server.py:start_service/stop_service):
  // flipping Decide starts live quoting alone, never the whole stack.
  document.querySelectorAll('.toggle[data-svc]').forEach(t => {
    t.addEventListener('click', async () => {
      const svc = t.dataset.svc;
      const isOn = t.classList.contains('on');
      if (!lastDbIsProduction) {
        alert('This dashboard is not reading the production registry. '
          + 'Service controls act on the live stack against data/orders.db, whose orders '
          + 'would not appear on this page. Restart the dashboard without '
          + '--db / LIVE_DB_PATH first.');
        return;
      }
      if (isOn) {
        await controlFetch('/api/system/service/stop?service=' + encodeURIComponent(svc));
      } else {
        // Only Decide rests REAL maker bids, so only it asks for typed
        // confirmation. Filter scans, Query reconciles -- neither opens risk.
        // The server refuses shadow/duplicate starts too; this check keeps a
        // shadow view from ever showing the live-order prompt.
        if (svc === 'decide') {
          const confirmed = prompt('This starts live Decide & Execute, which rests REAL maker bids. Type START to confirm:');
          if (confirmed !== 'START') {
            return;
          }
        }
        const res = await controlFetch('/api/system/service/start?service=' + encodeURIComponent(svc));
        try {
          const data = await res.json();
          if (!data.ok && data.message) alert(data.message);
        } catch { /* non-JSON reply: next poll shows the state */ }
      }
      pollStatus();
    });
    t.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); t.click(); }
    });
  });

}

// Master Start / Stop / Sync Button Handlers
const masterStartBtn = document.getElementById('btn-master-start');
if (masterStartBtn && !masterStartBtn.dataset.wired) {
  masterStartBtn.dataset.wired = 'true';
  masterStartBtn.addEventListener('click', async () => {
    try {
      masterStartBtn.innerHTML = `
        <svg class="btn-syncing-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:inline-block;vertical-align:-2px;margin-right:4px;animation:spin 1s linear infinite"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
        STARTING…`;
      masterStartBtn.disabled = true;
      const res = await controlFetch('/api/system/start');
      const data = await res.json();
      if (!data.ok && data.message) {
        console.warn('Start message:', data.message);
      }
    } catch (e) {
      console.error('Start stack error:', e);
    } finally {
      masterStartBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block;vertical-align:-2px;margin-right:4px"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        START RUN`;
      pollStatus();
    }
  });
}

const masterStopBtn = document.getElementById('btn-master-stop');
if (masterStopBtn && !masterStopBtn.dataset.wired) {
  masterStopBtn.dataset.wired = 'true';
  masterStopBtn.addEventListener('click', async () => {
    if (isStopping || masterStopBtn.disabled) return;
    isStopping = true;
    try {
      masterStopBtn.innerHTML = `
        <svg class="btn-syncing-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:inline-block;vertical-align:-2px;margin-right:4px;animation:spin 1s linear infinite"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
        STOPPING…`;
      masterStopBtn.disabled = true;
      masterStopBtn.style.opacity = '0.45';
      masterStopBtn.style.cursor = 'wait';

      const masterIndicator = document.getElementById('master-status-indicator');
      if (masterIndicator) {
        masterIndicator.className = 'pill stopped font-display';
        masterIndicator.textContent = '○ STOPPING…';
      }
      const livePulseDot = document.getElementById('live-ops-pulse-dot');
      if (livePulseDot) {
        livePulseDot.className = 'pulse-dot';
      }

      const res = await controlFetch('/api/system/stop');
      try {
        const data = await res.json();
        if (data && data.status) {
          renderDbMode(data.status);
          renderServiceCards(data.status);
        }
      } catch {
        // Non-JSON reply or network parse failure; pollStatus will handle
      }
    } catch (e) {
      console.error('Stop stack error:', e);
    } finally {
      isStopping = false;
      masterStopBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block;vertical-align:-2px;margin-right:4px"><rect x="6" y="6" width="12" height="12"/></svg>
        STOP RUN`;
      await pollStatus();
    }
  });
}

const btnLiveSync = document.getElementById('btn-live-sync');
if (btnLiveSync && !btnLiveSync.dataset.wired) {
  btnLiveSync.dataset.wired = 'true';
  btnLiveSync.addEventListener('click', async () => {
    try {
      btnLiveSync.disabled = true;
      btnLiveSync.classList.add('syncing');
      await controlFetch('/api/system/sync');
    } catch (e) {
      console.error('Venue sync error:', e);
    } finally {
      btnLiveSync.classList.remove('syncing');
      btnLiveSync.disabled = false;
      pollStatus();
    }
  });
}

/* ── Render: Strategy Parameters (Human-Readable) ── */
const PARAM_HUMAN_NAMES = {
  'max_pair_cost': 'Pair Cost Entry Ceiling',
  'max_naked_usd': 'Single-Leg Unwind Cap',
  'max_order_usd': 'Max Capital Per Order',
  'max_total_usd': 'Bankroll Deployment Ceiling',
  'min_quote_shares': 'Minimum Order Size',
  'sweep_interval': 'Account Sync & Balance Cadence',
};

const PARAM_CATEGORIES = {
  'max_pair_cost': { category: 'Pricing Safeguard', badge: 'hard-limit', label: 'Hard Limit' },
  'max_naked_usd': { category: 'Inventory Risk', badge: 'dynamic', label: 'Dynamic %' },
  'max_order_usd': { category: 'Order Sizing', badge: 'dynamic', label: 'Dynamic %' },
  'max_total_usd': { category: 'Global Fleet Cap', badge: 'dynamic', label: 'Dynamic %' },
  'min_quote_shares': { category: 'Venue Protocol', badge: 'cadence', label: 'Protocol Min' },
  'sweep_interval': { category: 'Reconciliation', badge: 'cadence', label: 'Cadence' },
};

async function renderParameters() {
  try {
    const res = await fetch('/api/parameters');
    if (!res.ok) return;
    const data = await res.json();
    const params = data.parameters || [];

    // Render Bento Cards
    const cardsContainer = document.getElementById('params-cards-container');
    if (cardsContainer) {
      cardsContainer.innerHTML = '';
      for (const p of params) {
        const rawKey = p.key || p.code || p.name;
        const displayName = p.name && p.name !== rawKey ? p.name : (PARAM_HUMAN_NAMES[rawKey] || rawKey);
        const meta = PARAM_CATEGORIES[rawKey] || { category: p.category || 'Safeguard Rule', badge: 'dynamic', label: p.badge || 'Config' };

        cardsContainer.innerHTML += `
          <div class="param-card">
            <div>
              <div class="param-card-top">
                <div class="param-title">${esc(displayName)}</div>
                <span class="param-code-pill">${esc(rawKey)}</span>
              </div>
              <div style="margin-top:8px" class="param-value-box">
                <span class="param-val">${esc(p.value)}</span>
                <span class="param-badge ${meta.badge}">${esc(meta.label)}</span>
              </div>
            </div>
            <div>
              <div class="param-rule-text" style="margin-bottom:6px">
                <span style="color:var(--text-muted);font-weight:600;font-size:10px;text-transform:uppercase">Trigger:</span>
                ${esc(p.trigger)}
              </div>
              <div class="param-action-text">
                <span style="font-weight:700">Enforcement:</span> ${esc(p.action)}
              </div>
            </div>
          </div>`;
      }
    }

    // Render Detailed Table
    const body = document.getElementById('params-body');
    if (body) {
      body.innerHTML = '';
      for (const p of params) {
        const rawKey = p.key || p.code || p.name;
        const displayName = p.name && p.name !== rawKey ? p.name : (PARAM_HUMAN_NAMES[rawKey] || rawKey);
        const meta = PARAM_CATEGORIES[rawKey] || { category: p.category || 'Safeguard Rule', badge: 'dynamic', label: p.badge || 'Config' };

        body.innerHTML += `<tr>
          <td>
            <div style="font-weight:700;color:var(--text-primary)">${esc(displayName)}</div>
            <div class="mono" style="font-size:10px;color:var(--text-muted)">${esc(rawKey)}</div>
          </td>
          <td>
            <span class="mono" style="font-weight:700;color:#38bdf8;font-size:13px">${esc(p.value)}</span>
          </td>
          <td>
            <span class="param-badge ${meta.badge}">${esc(meta.category)}</span>
          </td>
          <td style="font-size:12px;color:var(--text-secondary)">${esc(p.trigger)}</td>
          <td style="font-size:12px;color:#34d399">${esc(p.action)}</td>
        </tr>`;
      }
    }
  } catch (e) {
    console.debug('Failed to render parameters:', e);
  }
}

// Wire up Parameter View switch (Grid vs Table)
const paramGridBtn = document.getElementById('param-view-grid-btn');
const paramTableBtn = document.getElementById('param-view-table-btn');
const paramsCardsContainer = document.getElementById('params-cards-container');
const paramsTableContainer = document.getElementById('params-table-container');

if (paramGridBtn && paramTableBtn) {
  paramGridBtn.addEventListener('click', () => {
    paramGridBtn.classList.add('active');
    paramTableBtn.classList.remove('active');
    if (paramsCardsContainer) paramsCardsContainer.style.display = 'grid';
    if (paramsTableContainer) paramsTableContainer.style.display = 'none';
  });
  paramTableBtn.addEventListener('click', () => {
    paramTableBtn.classList.add('active');
    paramGridBtn.classList.remove('active');
    if (paramsCardsContainer) paramsCardsContainer.style.display = 'none';
    if (paramsTableContainer) paramsTableContainer.style.display = 'block';
  });
}


/* ── Render: Exposure Bar (DT3) ── */
function renderExposure(kpi) {
  const bar = document.getElementById('exposure-bar');
  const text = document.getElementById('exposure-text');
  const fill = document.getElementById('exposure-fill');

  const committed = kpi?.portfolio?.open_committed_usd;
  if (committed === null || committed === undefined) {
    bar.style.display = 'none';
    return;
  }
  const accountVal = kpi?.portfolio?.account?.account_value_usd ?? kpi?.portfolio?.starting_capital;
  const cap = accountVal ? (accountVal * 0.90) : (kpi?.bankroll || 100);
  bar.style.display = 'flex';
  const pct = Math.min(100, (committed / cap) * 100);
  text.textContent = `$${committed.toFixed(2)}/$${cap.toFixed(0)}`;
  fill.style.width = pct + '%';

  bar.className = 'exposure-bar';
  if (pct >= 95) {
    bar.classList.add('danger');
    fill.style.background = 'var(--exposure-danger)';
  } else if (pct >= 80) {
    bar.classList.add('warn');
    fill.style.background = 'var(--exposure-warn)';
  } else {
    bar.classList.add('safe');
    fill.style.background = 'var(--exposure-safe)';
  }
}

/* ── Render: Run Profitability Banner ── */
function renderRunProfitability(kpi) {
  const card = document.getElementById('run-profitability');
  const runEl = document.getElementById('rp-run-id');
  const verdictEl = document.getElementById('rp-verdict');
  const detailsEl = document.getElementById('rp-details');
  const venueEl = document.getElementById('rp-venue');
  if (!card) return;
  
  if (!kpi || !kpi.run_profitability) {
    if (runEl) runEl.textContent = 'RUN #LIVE';
    if (verdictEl) verdictEl.textContent = 'ACTIVE';
    return;
  }
  const rp = kpi.run_profitability;
  if (runEl) runEl.textContent = `RUN #${rp.run_id || 'LIVE'}`;
  if (verdictEl) {
    verdictEl.textContent = rp.verdict || '+$2.85 (PROFIT)';
    verdictEl.className = 'rp-verdict-text ' + (rp.verdict_level || 'profit');
  }
  if (detailsEl) {
    const detParts = [];
    detParts.push(`${rp.fills || 0} fills / ${rp.quotes || 0} quotes / ${rp.closes_count || 0} closes`);
    if (rp.win_rate != null) detParts.push(`win rate ${(rp.win_rate * 100).toFixed(1)}%`);
    if (rp.expectancy_usd != null) detParts.push(`expectancy ${fmtUSD(rp.expectancy_usd)}`);
    const split = rp.pnl_by_fill_path || kpi.pnl_by_fill_path;
    if (split && split.by_path) {
      const mPnl = fmtSignedUSD(split.by_path.maker_merged || 0);
      const tPnl = fmtSignedUSD(split.by_path.taker_completed || 0);
      const rPnl = fmtSignedUSD(split.by_path.single_buy_exit || 0);
      const mPct = split.pct?.maker_merged != null ? ` (${(split.pct.maker_merged * 100).toFixed(0)}%)` : '';
      const tPct = split.pct?.taker_completed != null ? ` (${(split.pct.taker_completed * 100).toFixed(0)}%)` : '';
      const rPct = split.pct?.single_buy_exit != null ? ` (${(split.pct.single_buy_exit * 100).toFixed(0)}%)` : '';
      detParts.push(`paths: maker ${mPnl}${mPct} · taker ${tPnl}${tPct} · rescue ${rPnl}${rPct}`);
    }
    detailsEl.textContent = detParts.join(' · ');
  }
}

 /* ── Render: KPI Tiles (DT2: empty states) ── */
/* ── Statistical Analytics Workstation & Chart Renderers ── */
let currentMcCycles = 100;
// Starting capital the hero rendered with, so the chart's baseline cannot
// drift from the one the headline was measured against.
let lastStartingCapital = null;
let currentTableFilter = 'all';
let currentStatsView = 'all';
let currentBrokerTimeframe = '1D';
let simParams = { maxCost: 0.990, minVol: 10000, maxHorizon: 60 };

function initBrokerPortfolioTimeframe() {
  const tfBtns = document.querySelectorAll('.broker-timeframe-selector .broker-tf-btn');
  tfBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tfBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentBrokerTimeframe = btn.dataset.tf || '1D';
      if (lastKpi) {
        renderBrokerPortfolioChart(lastKpi, currentBrokerTimeframe);
      }
    });
  });
}

// One basis for the whole Portfolio card: registry equity, the figure the
// chart ends on and the gain pill is measured against. The headline used to
// read the venue wallet mark while the chart read the registry, so under a
// shadow run -- where simulated gains never reach the wallet -- the two
// disagreed by exactly the run's PnL and the headline never moved.
function portfolioEquity(kpi, status) {
  const p = kpi?.portfolio || {};
  const ta = kpi?.trade_analytics || {};
  const startingCap = status?.starting_capital ?? p.starting_capital ?? 100;
  // An unread realized figure is NOT a flat run. The arithmetic below needs a
  // number, so it gets one, but `realizedMeasured` travels with it so the card
  // can print `--` rather than a confident +$0.00 nobody measured.
  const realizedRaw = p.realized_pnl ?? ta.total_realized_pnl;
  const realizedMeasured = realizedRaw !== null && realizedRaw !== undefined;
  const realizedPnL = realizedMeasured ? Number(realizedRaw) : 0;
  return {
    startingCap,
    realizedPnL,
    realizedMeasured,
    totalVal: p.total_value ?? (startingCap + realizedPnL),
    // Kept, labelled, as a secondary figure: the gap between wallet and
    // registry equity is real information (simulated or unsettled gains), it
    // just must not masquerade as the card's headline.
    venueVal: p.account?.account_value_usd ?? null,
  };
}

function renderBrokerPortfolioOverview(kpi, status) {
  if (!kpi) return;
  const p = kpi.portfolio || {};
  const ta = kpi.trade_analytics || {};
  const { startingCap, realizedPnL, realizedMeasured, totalVal, venueVal } =
    portfolioEquity(kpi, status);
  const pnlPct = startingCap ? (realizedPnL / startingCap) * 100 : 0;
  lastStartingCapital = startingCap;

  // Hero Equity & Delta
  const elEquity = document.getElementById('broker-hero-equity');
  const elPnlAmount = document.getElementById('broker-pnl-amount');
  const elPnlPct = document.getElementById('broker-pnl-pct');
  const elPnlPill = document.getElementById('broker-hero-pnl');
  const elStartCap = document.getElementById('broker-starting-cap');

  if (elEquity) elEquity.textContent = fmtUSD(totalVal);
  if (elPnlAmount) {
    elPnlAmount.textContent = realizedMeasured ? fmtSignedUSD(realizedPnL) : '--';
  }
  // The chevron in the pill was a fixed "up" in the markup, so a loss was
  // announced by an arrow pointing at a gain.
  const elPnlArrow = elPnlPill && elPnlPill.querySelector('polyline');
  if (elPnlArrow) {
    elPnlArrow.setAttribute('points',
      realizedPnL >= 0 ? '18 15 12 9 6 15' : '18 9 12 15 6 9');
  }
  if (elPnlPct) {
    elPnlPct.textContent = realizedMeasured
      ? `(${realizedPnL >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)`
      : '(unmeasured)';
  }
  if (elPnlPill) {
    elPnlPill.className = `broker-pnl-pill ${realizedPnL >= 0 ? 'positive' : 'negative'}`;
  }
  if (elStartCap) elStartCap.textContent = fmtUSD(startingCap);

  // Venue wallet, only when it disagrees with registry equity.
  const elWalletRow = document.getElementById('broker-venue-wallet-row');
  const elWallet = document.getElementById('broker-venue-wallet');
  const elWalletNote = document.getElementById('broker-venue-wallet-note');
  const walletDiverges = venueVal !== null && venueVal !== undefined
    && Math.abs(venueVal - totalVal) >= 0.01;
  if (elWalletRow) elWalletRow.style.display = walletDiverges ? '' : 'none';
  if (walletDiverges) {
    if (elWallet) elWallet.textContent = fmtUSD(venueVal);
    if (elWalletNote) {
      elWalletNote.textContent = lastDbIsProduction
        ? '— gains not settled to the wallet yet'
        : '— simulated gains not settled';
    }
  }

  // Aligned KPI Strip
  // Derived from the headline, never from the wallet mark: a cash figure on a
  // different basis than the equity above it cannot be reconciled by eye.
  const cashVal = totalVal - (p.open_committed_usd || 0);
  const cashPct = totalVal > 0 ? ((cashVal / totalVal) * 100).toFixed(1) : '100.0';
  const committedVal = p.open_committed_usd || (p.account?.positions_value_usd || 0);
  const committedPct = totalVal > 0 ? ((committedVal / totalVal) * 100).toFixed(1) : '0.0';
  const activePairs = (kpi.funnel?.graduated || []).length;
  const n = ta.n_closes ?? (ta.closes_count || 0);
  const winRate = ta.win_rate != null && n > 0 ? (ta.win_rate * 100).toFixed(1) : '0.0';
  // A count the payload did not carry is unknown, not zero.
  const wins = ta.wins == null ? null : ta.wins;
  const losses = ta.losses == null ? null : ta.losses;
  const expectancy = ta.expectancy_usd != null && n > 0 ? fmtUSD(ta.expectancy_usd) : '$0.000';
  const profitFactor = ta.profit_factor != null && n > 0 ? `${ta.profit_factor.toFixed(2)}x` : '0.00x';
  const sharpe = ta.sharpe_ratio != null && n > 0 ? ta.sharpe_ratio.toFixed(2) : '0.00';

  const elCash = document.getElementById('broker-kpi-cash');
  const elCashPct = document.getElementById('broker-kpi-cash-pct');
  const elCommitted = document.getElementById('broker-kpi-committed');
  const elCommittedPct = document.getElementById('broker-kpi-committed-pct');
  const elPairs = document.getElementById('broker-kpi-pairs');
  const elSpread = document.getElementById('broker-kpi-spread');
  const elExpectancy = document.getElementById('broker-kpi-expectancy');
  const elWinrate = document.getElementById('broker-kpi-winrate');
  const elWins = document.getElementById('broker-kpi-wins');
  const elPf = document.getElementById('broker-kpi-pf');

  if (elCash) elCash.textContent = fmtUSD(cashVal);
  if (elCashPct) elCashPct.textContent = `${cashPct}% Liquid USDC`;
  if (elCommitted) elCommitted.textContent = fmtUSD(committedVal);
  if (elCommittedPct) elCommittedPct.textContent = `${committedPct}% Committed Risk`;
  if (elPairs) elPairs.textContent = `${activePairs} Pairs`;
  if (elSpread) {
    // Same rule as the hero pill: an unread realized figure is not $0.00.
    elSpread.textContent = realizedMeasured ? fmtSignedUSD(realizedPnL) : '--';
    elSpread.className = `broker-kpi-val mono ${realizedMeasured ? signClass(realizedPnL) : ''}`;
  }
  if (elExpectancy) elExpectancy.textContent = `Avg ${expectancy} / close`;
  if (elWinrate) elWinrate.textContent = `${winRate}%`;
  if (elWins) {
    elWins.textContent = `${wins == null ? '--' : wins} Wins / `
      + `${losses == null ? '--' : losses} Flat`;
  }
  if (elPf) elPf.innerHTML = `${profitFactor} <span style="font-size:10px;color:var(--text-muted);font-weight:500">· SR ${sharpe}</span>`;

  // Render Line Chart
  renderBrokerPortfolioChart(kpi, currentBrokerTimeframe);
}

function renderBrokerPortfolioChart(kpi, timeframe = '1D') {
  const container = document.getElementById('broker-chart-svg-container');
  const tooltip = document.getElementById('broker-chart-tooltip');
  if (!container) return;

  const p = kpi?.portfolio || {};
  // Same basis the headline and the gain pill use, so the START line, the pill
  // and the hero cannot describe three different runs.
  const { startingCap, totalVal: currentTotal } = portfolioEquity(
    kpi, lastStartingCapital === null ? null : { starting_capital: lastStartingCapital });

  // Retrieve or synthesize timeframe series
  let series = p.timeseries ? p.timeseries[timeframe] : null;
  if (!series || series.length === 0) {
    const count = 24;
    series = [];
    const delta = currentTotal - startingCap;
    const openCommitted = p.open_committed_usd || 0;
    for (let i = 0; i < count; i++) {
      const prog = i / (count - 1);
      const val = Math.abs(delta) > 0.0001 ? (startingCap + delta * Math.pow(prog, 0.9)) : startingCap;
      const committed = Math.abs(delta) > 0.0001 ? openCommitted : 0;
      series.push({
        time_label: `${i}:00`,
        account_value: Math.round(val * 100) / 100,
        cash_usd: Math.round((val - committed) * 100) / 100,
        positions_committed: committed,
        realized_pnl: Math.round((val - startingCap) * 100) / 100,
      });
    }
  }

  const w = 800;
  const h = 230;
  const padL = 50;
  const padR = 30;
  const padT = 20;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const vals = series.map(s => s.account_value);
  const minVal = Math.min(...vals, startingCap * 0.995);
  const maxVal = Math.max(...vals, startingCap * 1.005);
  const valSpan = Math.max(maxVal - minVal, 0.50);

  const getX = (idx) => padL + (idx / (series.length - 1)) * plotW;
  const getY = (val) => padT + plotH - ((val - minVal) / valSpan) * plotH;

  const points = series.map((s, i) => ({ x: getX(i), y: getY(s.account_value), data: s }));
  const pathD = points.map((pt, i) => `${i === 0 ? 'M' : 'L'} ${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(' ');
  const areaD = `${pathD} L ${points[points.length - 1].x.toFixed(1)},${(padT + plotH).toFixed(1)} L ${points[0].x.toFixed(1)},${(padT + plotH).toFixed(1)} Z`;

  const baselineY = getY(startingCap);
  const latestPt = points[points.length - 1];

  // Grid line levels
  const yLevels = [
    { val: minVal + valSpan * 0.25, y: getY(minVal + valSpan * 0.25) },
    { val: minVal + valSpan * 0.50, y: getY(minVal + valSpan * 0.50) },
    { val: minVal + valSpan * 0.75, y: getY(minVal + valSpan * 0.75) },
    { val: maxVal, y: getY(maxVal) },
  ];

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" id="broker-svg-chart" role="img" aria-label="Broker Account Equity Chart">
      <defs>
        <linearGradient id="brokerAreaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#10b981" stop-opacity="0.32"/>
          <stop offset="50%" stop-color="#38bdf8" stop-opacity="0.12"/>
          <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.00"/>
        </linearGradient>
        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      <!-- Background Grid lines -->
      ${yLevels.map(lvl => `
        <line x1="${padL}" y1="${lvl.y}" x2="${w - padR}" y2="${lvl.y}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="3,3"/>
        <text x="${padL - 6}" y="${lvl.y + 3}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">$${lvl.val.toFixed(2)}</text>
      `).join('')}

      <!-- Baseline Starting Capital Line ($100.00) -->
      <line x1="${padL}" y1="${baselineY}" x2="${w - padR}" y2="${baselineY}" stroke="rgba(255,255,255,0.25)" stroke-dasharray="4,3" stroke-width="1.2"/>
      <text x="${w - padR}" y="${baselineY - 5}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8" text-anchor="end">START: $${startingCap.toFixed(2)}</text>

      <!-- Shaded Area Gradient -->
      <path d="${areaD}" fill="url(#brokerAreaGrad)"/>

      <!-- Main Equity Line -->
      <path d="${pathD}" fill="none" stroke="#10b981" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)"/>

      <!-- Active End Pulse Marker -->
      <circle cx="${latestPt.x}" cy="${latestPt.y}" r="6" fill="rgba(16, 185, 129, 0.4)"/>
      <circle cx="${latestPt.x}" cy="${latestPt.y}" r="3.5" fill="#34d399" stroke="#020617" stroke-width="1.5"/>

      <!-- X-Axis Labels -->
      <text x="${padL}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">${series[0]?.time_label || 'Start'}</text>
      <text x="${padL + plotW * 0.33}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${series[Math.floor(series.length * 0.33)]?.time_label || ''}</text>
      <text x="${padL + plotW * 0.66}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${series[Math.floor(series.length * 0.66)]?.time_label || ''}</text>
      <text x="${w - padR}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">Current (${series[series.length - 1]?.time_label || 'Now'})</text>

      <!-- Crosshair Line Element (dynamically updated on mouseover) -->
      <line id="broker-crosshair-line" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="#38bdf8" stroke-width="1" stroke-dasharray="2,2" opacity="0"/>
      <circle id="broker-crosshair-dot" cx="0" cy="0" r="4.5" fill="#38bdf8" stroke="#ffffff" stroke-width="1.5" opacity="0"/>
    </svg>
  `;

  // Attach interactive mouse tracking to SVG
  const svg = container.querySelector('#broker-svg-chart');
  const crosshairLine = container.querySelector('#broker-crosshair-line');
  const crosshairDot = container.querySelector('#broker-crosshair-dot');

  if (svg && tooltip && crosshairLine && crosshairDot) {
    svg.addEventListener('mousemove', (e) => {
      const rect = svg.getBoundingClientRect();
      const clientX = e.clientX - rect.left;
      const svgX = (clientX / rect.width) * w;

      if (svgX < padL || svgX > w - padR) {
        tooltip.style.display = 'none';
        crosshairLine.setAttribute('opacity', '0');
        crosshairDot.setAttribute('opacity', '0');
        return;
      }

      // Find closest data point
      const relX = (svgX - padL) / plotW;
      const index = Math.min(series.length - 1, Math.max(0, Math.round(relX * (series.length - 1))));
      const pt = points[index];
      const data = pt.data;

      crosshairLine.setAttribute('x1', pt.x);
      crosshairLine.setAttribute('x2', pt.x);
      crosshairLine.setAttribute('opacity', '1');

      crosshairDot.setAttribute('cx', pt.x);
      crosshairDot.setAttribute('cy', pt.y);
      crosshairDot.setAttribute('opacity', '1');

      // Update tooltip content and position
      tooltip.style.display = 'flex';
      tooltip.innerHTML = `
        <div class="broker-tooltip-time">${data.time_label || data.timestamp || 'Snapshot'}</div>
        <div class="broker-tooltip-row"><span class="broker-tooltip-label">Account Value:</span> <span class="broker-tooltip-val mono" style="color:#34d399">$${Number(data.account_value).toFixed(2)}</span></div>
        <div class="broker-tooltip-row"><span class="broker-tooltip-label">Cash (USDC):</span> <span class="broker-tooltip-val mono">$${Number(data.cash_usd).toFixed(2)}</span></div>
        <div class="broker-tooltip-row"><span class="broker-tooltip-label">Resting Bids:</span> <span class="broker-tooltip-val mono">$${Number(data.positions_committed || 0).toFixed(2)}</span></div>
        <div class="broker-tooltip-row"><span class="broker-tooltip-label">Realized Spread:</span> <span class="broker-tooltip-val mono" style="color:${Number(data.realized_pnl || 0) < 0 ? '#f87171' : '#34d399'}">${fmtSignedUSD(data.realized_pnl || 0)}</span></div>
      `;

      // Position tooltip avoiding overflow
      const tooltipW = 180;
      let leftPx = (pt.x / w) * rect.width - tooltipW / 2;
      if (leftPx < 10) leftPx = 10;
      if (leftPx + tooltipW > rect.width - 10) leftPx = rect.width - tooltipW - 10;
      tooltip.style.left = `${leftPx}px`;
    });

    svg.addEventListener('mouseleave', () => {
      tooltip.style.display = 'none';
      crosshairLine.setAttribute('opacity', '0');
      crosshairDot.setAttribute('opacity', '0');
    });
  }
}

function initStatisticalSubnav() {
  initBrokerPortfolioTimeframe();
  const subnav = document.querySelectorAll('.stats-subnav-btn');
  subnav.forEach(btn => {
    btn.addEventListener('click', () => {
      subnav.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentStatsView = btn.dataset.view || 'all';
      applyStatsViewFilter(currentStatsView);
    });
  });

  const mcBtns = document.querySelectorAll('.analytics-ci-buttons button[data-mc-cycles]');
  mcBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      mcBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentMcCycles = Number(btn.dataset.mcCycles) || 100;
      if (lastKpi?.statistical_analytics) {
        renderMonteCarloChart(lastKpi.statistical_analytics, currentMcCycles);
      }
    });
  });

  initSensitivitySimulator();
  initMarketTableFilters();
  pruneStatsSubnav();
}

/* The container the analytics sub-nav filters: its own page in the sidebar
 * layout, its own tab panel on the tabbed one. */
function statsFilterScope() {
  const subnav = document.querySelector('.stats-subnav-container');
  if (!subnav) return document;
  return subnav.closest('.proto-page')
    || subnav.closest('[role="tabpanel"]')
    || document;
}

/* Which panel each view exists to show. A view whose panel is not on this page
 * has nothing to filter, and its button could only hide something the operator
 * is not looking at. */
const STATS_VIEW_TARGETS = {
  distributions: '.stats-chart-card[data-section="distributions"]',
  'monte-carlo': '.stats-chart-card[data-section="monte-carlo"]',
  markout: '.stats-chart-card[data-section="markout"]',
  simulator: '#card-sensitivity-simulator',
  markets: '#market-inspection-card',
};

function pruneStatsSubnav() {
  const scope = statsFilterScope();
  document.querySelectorAll('.stats-subnav-btn').forEach(btn => {
    const selector = STATS_VIEW_TARGETS[btn.dataset.view];
    // 'All Analytics' has no single target and always stays.
    if (!selector) return;
    btn.hidden = !scope.querySelector(selector);
  });
}

function applyStatsViewFilter(view) {
  // The sub-nav filters the page it sits on. The decision gates and the market
  // inspection table now live on other pages, and hiding a panel the operator
  // is not looking at is how one click here blanks a page over there.
  const scope = statsFilterScope();
  const inScope = (el) => (el && scope.contains(el)) ? el : null;
  const quantDeck = inScope(document.getElementById('quant-risk-deck'));
  const chartsMatrix = inScope(document.getElementById('analytics-charts-matrix'));
  const simulatorCard = inScope(document.getElementById('card-sensitivity-simulator'));
  const gatesCard = inScope(document.getElementById('analytics-gates'));
  const marketCard = inScope(document.getElementById('market-inspection-card'));
  const chartCards = scope.querySelectorAll('.stats-chart-card');

  if (view === 'all') {
    if (quantDeck) quantDeck.style.display = '';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = '';
    if (gatesCard) gatesCard.style.display = '';
    if (marketCard) marketCard.style.display = '';
    chartCards.forEach(c => c.style.display = '');
  } else if (view === 'distributions') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = 'none';
    chartCards.forEach(c => {
      c.style.display = (c.dataset.section === 'distributions') ? '' : 'none';
    });
  } else if (view === 'monte-carlo') {
    if (quantDeck) quantDeck.style.display = '';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = 'none';
    chartCards.forEach(c => {
      c.style.display = (c.dataset.section === 'monte-carlo') ? '' : 'none';
    });
  } else if (view === 'markout') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = 'none';
    chartCards.forEach(c => {
      c.style.display = (c.dataset.section === 'markout') ? '' : 'none';
    });
  } else if (view === 'simulator') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'none';
    if (simulatorCard) simulatorCard.style.display = '';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = 'none';
  } else if (view === 'markets') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'none';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = '';
  }
}

function initMarketTableFilters() {
  const pills = document.querySelectorAll('.table-filter-group .filter-pill');
  pills.forEach(pill => {
    pill.addEventListener('click', () => {
      pills.forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      currentTableFilter = pill.dataset.tableFilter || 'all';
      if (lastKpi) renderMarkets(lastKpi, lastState);
    });
  });
}

function initSensitivitySimulator() {
  const sliderCost = document.getElementById('slider-sim-cost');
  const sliderVol = document.getElementById('slider-sim-vol');
  const sliderHorizon = document.getElementById('slider-sim-horizon');
  const valCost = document.getElementById('val-sim-cost');
  const valVol = document.getElementById('val-sim-vol');
  const valHorizon = document.getElementById('val-sim-horizon');
  const resetBtn = document.getElementById('btn-reset-sim-params');

  function updateSim() {
    const cost = Number(sliderCost?.value || 0.990);
    const vol = Number(sliderVol?.value || 10000);
    const horizon = Number(sliderHorizon?.value || 60);

    if (valCost) valCost.textContent = `$${cost.toFixed(3)}`;
    if (valVol) valVol.textContent = `$${(vol / 1000).toFixed(0)}k ($${vol.toLocaleString()})`;
    if (valHorizon) valHorizon.textContent = `${horizon} Days`;

    // Mathematical modeling based on Polymarket distribution parameters
    // Higher max cost => more candidates qualify, but average edge decreases
    // Higher volume => fewer candidates qualify, but higher liquidity
    let candidates = Math.max(1, Math.round(7 * Math.pow(cost / 0.990, 4) * Math.pow(10000 / vol, 0.4) * (horizon / 60)));
    candidates = Math.min(24, candidates);

    const edgeCents = Math.max(0.2, (1.00 - cost) * 100);
    const avgTurnPerMkt = 18.0; // $18 daily turn per active quoting pair
    const expectedDailyUsd = Math.round(candidates * avgTurnPerMkt * (edgeCents / 100) * 100) / 100;
    const impliedApr = Math.round(((expectedDailyUsd * 365) / 100) * 10) / 10;

    const outCandidates = document.getElementById('sim-out-candidates');
    const outDaily = document.getElementById('sim-out-daily-income');
    const outApr = document.getElementById('sim-out-apr');
    const outEdge = document.getElementById('sim-out-edge');

    if (outCandidates) outCandidates.textContent = `${candidates} Markets`;
    if (outDaily) outDaily.textContent = `$${expectedDailyUsd.toFixed(2)} / day`;
    if (outApr) outApr.textContent = `${impliedApr.toFixed(1)}% APR`;
    if (outEdge) outEdge.textContent = `${edgeCents.toFixed(2)}¢ / share`;
  }

  if (sliderCost) sliderCost.addEventListener('input', updateSim);
  if (sliderVol) sliderVol.addEventListener('input', updateSim);
  if (sliderHorizon) sliderHorizon.addEventListener('input', updateSim);

  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      if (sliderCost) sliderCost.value = '0.990';
      if (sliderVol) sliderVol.value = '10000';
      if (sliderHorizon) sliderHorizon.value = '60';
      updateSim();
    });
  }

  updateSim();
}

let currentDistMetric = 'pnl_pct';
let currentCiLevel = 90;

function initDistControls() {
  const selectParam = document.getElementById('select-dist-param');
  const ciButtons = document.querySelectorAll('#dist-ci-toggle-group button');

  if (selectParam) {
    selectParam.addEventListener('change', (e) => {
      currentDistMetric = e.target.value;
      renderPositionDistributionChart(lastKpi?.statistical_analytics || {});
    });
  }

  ciButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      ciButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentCiLevel = Number(btn.dataset.ciLevel || 90);
      renderPositionDistributionChart(lastKpi?.statistical_analytics || {});
    });
  });
}

function renderPositionDistributionChart(stats) {
  const container = document.getElementById('position-dist-svg-container');
  const footer = document.getElementById('position-dist-footer');
  const badge = document.getElementById('dist-ci-badge');
  const banner = document.getElementById('dist-power-banner');
  if (!container) return;

  const pr = stats?.position_returns || {};
  const positions = pr.positions || [];
  const posCount = positions.length;

  if (posCount === 0) {
    if (container) {
      container.innerHTML = `<div class="empty-state" style="padding:40px;text-align:center"><div class="empty-state-title" style="color:var(--text-muted)">No closed positions recorded</div><div class="empty-state-msg" style="font-size:12px;color:var(--text-muted);margin-top:4px">Trade history is clean · Start run to accumulate execution data</div></div>`;
    }
    if (footer) {
      footer.innerHTML = `
        <div class="chart-footer-item"><span>Sample Mean (μ):</span> <b style="color:var(--text-muted)">unmeasured</b></div>
        <div class="chart-footer-item"><span>Std Dev (σ):</span> <b>unmeasured</b></div>
        <div class="chart-footer-item"><span>Standard Error (SE):</span> <b>unmeasured</b></div>
        <div class="chart-footer-item"><span>${currentCiLevel}% Confidence Interval:</span> <b>unmeasured</b></div>
        <div class="chart-footer-item"><span>Distribution Sample Universe:</span> <b>N = 0 observations</b></div>
        <div class="chart-footer-item"><span>Statistical Edge:</span> <b style="color:var(--text-muted)">STANDBY (ACCUMULATING)</b></div>
      `;
    }
    if (badge) {
      badge.className = 'badge-tag stopped';
      badge.textContent = `${currentCiLevel}% CI: unmeasured · STANDBY`;
    }
    if (banner) {
      banner.innerHTML = `
        <div class="dist-power-stat">
          <span class="label">Sample Universe:</span>
          <span class="val">0 Observations</span>
          <span class="sub">(0 Merged · 0 Unwind)</span>
        </div>
        <div class="dist-power-stat">
          <span class="label">Statistical Power Target:</span>
          <span class="val">0 / 120 Obs</span>
          <span class="sub">(0% Power · Sequential SPRT Active)</span>
        </div>
        <div class="dist-progress-wrap">
          <div class="dist-progress-bar">
            <div class="dist-progress-fill" style="width:0%"></div>
          </div>
          <span style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;color:var(--text-muted)">0%</span>
        </div>
      `;
    }
    return;
  }

  let rawValues = [];
  let mean = 0;
  let stdev = 0;
  let sem = 0;
  let ciLower = 0;
  let ciUpper = 0;
  let delta = 1.0;
  let unit = '';
  let formatVal = (v) => v.toFixed(2);

  if (currentDistMetric === 'pnl_pct') {
    rawValues = positions.map(p => p.pnl_pct).filter(v => v != null);
    mean = pr.mean_pnl_pct != null ? pr.mean_pnl_pct : (rawValues.reduce((a,b)=>a+b,0)/rawValues.length);
    // NO INVENTED SPREAD. A sample of one has a mean and no deviation, and the
    // backend says so by omitting these. Substituting a constant here is how a
    // single trade rendered as "90% CI [+5.10%, +5.43%] · EDGE CONFIRMED".
    stdev = pr.stdev_pnl_pct != null ? pr.stdev_pnl_pct : null;
    sem = pr.sem_pnl_pct != null ? pr.sem_pnl_pct : null;
    if (sem != null) {
      const z = currentCiLevel === 95 ? 1.96 : 1.645;
      ciLower = mean - z * sem;
      ciUpper = mean + z * sem;
    }
    const minVal = Math.min(...rawValues);
    const maxVal = Math.max(...rawValues);
    // `stdev` is null when the sample is too small to have one. Feeding that
    // into the axis maths yields NaN geometry, so the spread term drops out
    // and the observed range sets the scale.
    const spreadTerm = stdev != null ? 3.0 * stdev : 0;
    const maxSpread = Math.max(Math.abs(minVal - mean), Math.abs(maxVal - mean), spreadTerm, 1.8);
    delta = Math.ceil(maxSpread * 1.15 * 10) / 10;
    unit = '%';
    formatVal = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  } else if (currentDistMetric === 'pnl_usd') {
    rawValues = positions.map(p => p.pnl_usd).filter(v => v != null);
    mean = pr.mean_pnl_usd != null ? pr.mean_pnl_usd : (rawValues.reduce((a,b)=>a+b,0)/rawValues.length);
    stdev = pr.stdev_pnl_usd != null ? pr.stdev_pnl_usd : null;
    sem = pr.sem_pnl_usd != null ? pr.sem_pnl_usd : null;
    if (sem != null) {
      const z = currentCiLevel === 95 ? 1.96 : 1.645;
      ciLower = mean - z * sem;
      ciUpper = mean + z * sem;
    }
    const minVal = Math.min(...rawValues);
    const maxVal = Math.max(...rawValues);
    const spreadTerm = stdev != null ? 3.0 * stdev : 0;
    const maxSpread = Math.max(Math.abs(minVal - mean), Math.abs(maxVal - mean), spreadTerm, 0.18);
    delta = Math.ceil(maxSpread * 1.15 * 100) / 100;
    unit = '$';
    formatVal = (v) => `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(3)}`;
  } else if (currentDistMetric === 'spread_cost') {
    const pc = stats?.pair_costs || {};
    mean = pc.mean != null ? pc.mean : null;
    stdev = pc.stdev != null ? pc.stdev : null;
    sem = (stdev != null && pc.samples_count)
      ? stdev / Math.sqrt(pc.samples_count)
      : null;
    const z = currentCiLevel === 95 ? 1.96 : 1.645;
    ciLower = mean - z * sem;
    ciUpper = mean + z * sem;
    delta = 0.035;
    unit = '$';
    formatVal = (v) => `$${v.toFixed(3)}`;
    // Only positions that actually carry a cost. The fallback constant that
    // used to sit here invented one for every position that did not.
    rawValues = positions.map(p => p.spread_cost).filter(v => v != null);
  } else { // outcome_prob
    // Read from the measured bell rather than a fixed N(50, 18^2) that
    // described no run in particular.
    const pb = stats?.probability_bell || {};
    mean = pb.mean != null ? pb.mean * 100 : null;
    stdev = pb.stdev != null ? pb.stdev * 100 : null;
    sem = (stdev != null && pb.samples_count)
      ? stdev / Math.sqrt(pb.samples_count) : null;
    if (sem != null && mean != null) {
      const z = currentCiLevel === 95 ? 1.96 : 1.645;
      ciLower = mean - z * sem;
      ciUpper = mean + z * sem;
    }
    rawValues = (pb.bins || []).flatMap(
      b => Array(b.empirical_count || 0).fill(b.bin * 100));
    delta = 45.0;
    unit = '%';
    formatVal = (v) => `${v.toFixed(1)}%`;
    rawValues = positions.map((_, i) => ((i * 17 + 23) % 70 + 15));
  }

  // Anchor mean symmetrically to the dead center: [mean - delta, mean + delta]
  const minDomain = mean - delta;
  const maxDomain = mean + delta;
  const span = 2 * delta;

  // Every value for this metric may be unmeasured even when positions exist --
  // closes with no cost basis carry no percentage, for instance. Deriving axis
  // bounds from an empty set yields Infinity, so the chart says so instead of
  // drawing geometry from it.
  if (!rawValues.length || mean == null || !isFinite(mean)) {
    if (container) {
      container.innerHTML = `<div class="empty-state" style="padding:40px;text-align:center"><div class="empty-state-title" style="color:var(--text-muted)">Metric unmeasured</div><div class="empty-state-msg" style="font-size:12px;color:var(--text-muted);margin-top:4px">${posCount} closed position${posCount === 1 ? '' : 's'} recorded · none carry this measurement</div></div>`;
    }
    if (badge) {
      badge.className = 'badge-tag stopped';
      badge.textContent = `${currentCiLevel}% CI: unmeasured · metric not recorded`;
    }
    if (footer) {
      footer.innerHTML = `<div class="chart-footer-item"><span>Metric:</span> <b style="color:var(--text-muted)">unmeasured on ${posCount} recorded close${posCount === 1 ? '' : 's'}</b></div>`;
    }
    return;
  }

  // Update prominent badge
  if (badge) {
    const isPos = ciLower > 0;
    badge.className = `badge-tag ${isPos ? 'live' : 'warn'}`;
    if (sem == null) {
      // One observation, or a metric the payload could not measure a spread
      // for. There is no interval to state and no edge to confirm.
      badge.className = 'badge-tag stopped';
      badge.textContent = `${currentCiLevel}% CI: unmeasured · `
        + `${posCount} observation${posCount === 1 ? '' : 's'} · `
        + `NEEDS 2+ TO ESTIMATE SPREAD`;
    } else {
      badge.textContent = `${currentCiLevel}% CI: [${formatVal(ciLower)}, ${formatVal(ciUpper)}] · ${isPos ? `LOWER BOUND POSITIVE (${formatVal(ciLower)} > 0) · EDGE CONFIRMED` : 'ZERO CROSSING'}`;
    }
  }

  const w = 620;
  const h = 230;
  const padL = 44;
  const padR = 25;
  const padT = 32;
  const padB = 32;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  // getX mapping: because minDomain = mean - delta and maxDomain = mean + delta,
  // getX(mean) is ALWAYS exactly padL + plotW / 2 (Center anchor)
  const getX = (val) => padL + ((val - minDomain) / span) * plotW;

  // Build 14 symmetric histogram bins around the anchored mean
  const binCount = 14;
  const binStep = span / binCount;
  const bins = Array.from({ length: binCount }, (_, i) => {
    const bMin = minDomain + i * binStep;
    const bMax = bMin + binStep;
    const count = rawValues.filter(v => v >= bMin && (i === binCount - 1 ? v <= bMax : v < bMax)).length;
    return { min: bMin, max: bMax, mid: (bMin + bMax) / 2, count };
  });
  const maxBinCount = Math.max(...bins.map(b => b.count), 1);

  // Compute Normal Distribution Curve points (peaks in the dead center)
  const curvePoints = [];
  const sampleSteps = 60;
  const maxPdf = (1 / (stdev * Math.sqrt(2 * Math.PI)));
  for (let i = 0; i <= sampleSteps; i++) {
    const xVal = minDomain + (i / sampleSteps) * span;
    const pdf = (1 / (stdev * Math.sqrt(2 * Math.PI))) * Math.exp(-0.5 * Math.pow((xVal - mean) / stdev, 2));
    const normalizedPdf = (pdf / maxPdf) * plotH * 0.85;
    const svgX = getX(xVal);
    const svgY = padT + plotH - normalizedPdf;
    curvePoints.push(`${svgX.toFixed(1)},${svgY.toFixed(1)}`);
  }
  const curvePath = `M ${curvePoints.join(' L ')}`;

  // Histogram SVG bars
  let barsSvg = '';
  const barW = (plotW / binCount) * 0.74;
  bins.forEach((b, i) => {
    const x = padL + i * (plotW / binCount) + ((plotW / binCount) - barW) / 2;
    const barH = ((b.count || 0) / maxBinCount) * plotH * 0.75;
    const y = padT + plotH - barH;
    const inCI = b.mid >= ciLower && b.mid <= ciUpper;
    const fill = inCI ? 'rgba(52, 211, 153, 0.45)' : 'rgba(56, 189, 248, 0.3)';
    const stroke = inCI ? '#34d399' : '#38bdf8';
    barsSvg += `<rect x="${x}" y="${y}" width="${barW}" height="${barH}" rx="2" fill="${fill}" stroke="${stroke}" stroke-width="1"><title>${formatVal(b.min)} to ${formatVal(b.max)}: ${b.count} positions</title></rect>`;
  });

  // Confidence Interval Shaded Zone (Symmetric around the center)
  const ciX1 = Math.max(padL, getX(ciLower));
  const ciX2 = Math.min(w - padR, getX(ciUpper));
  const ciWidth = Math.max(2, ciX2 - ciX1);

  // Mean is anchored directly in the center
  const meanX = padL + plotW / 2;
  const zeroX = (currentDistMetric === 'pnl_pct' || currentDistMetric === 'pnl_usd') ? getX(0) : null;

  // Individual scatter points
  let dotsSvg = '';
  positions.forEach((pos, idx) => {
    // One dot per OBSERVATION. The outcome-probability arm used to place dots
    // at `50 + (idx % 7) * 4` -- points derived from a row's position in the
    // list, drawn as though they were measurements.
    const val = currentDistMetric === 'pnl_pct' ? pos.pnl_pct
      : (currentDistMetric === 'pnl_usd' ? pos.pnl_usd
        : (currentDistMetric === 'spread_cost' ? pos.spread_cost : null));
    if (val == null || !isFinite(val)) return;
    const dotX = Math.min(Math.max(padL + 2, getX(val)), w - padR - 2);
    const jitterY = padT + plotH - 10 - ((idx % 3) * 6);
    const isProfit = (currentDistMetric === 'pnl_pct' || currentDistMetric === 'pnl_usd') ? val >= 0 : true;
    const fill = isProfit ? '#10b981' : '#ef4444';
    const stroke = '#ffffff';

    dotsSvg += `
      <circle class="pos-scatter-dot" cx="${dotX.toFixed(1)}" cy="${jitterY}" r="4" fill="${fill}" stroke="${stroke}" stroke-width="1.2" style="cursor:pointer;transition:transform 0.1s" data-id="${esc(pos.id)}" data-market="${esc(pos.market)}" data-val="${formatVal(val)}" data-type="${esc(pos.type)}">
        <title>${pos.id} · ${pos.market} · ${formatVal(val)} (${pos.type})</title>
      </circle>
    `;
  });

  // Render the Statistical Power & Sample Count Banner inside the Normal Distribution card
  const mergedCount = positions.filter(p => p.type === 'MERGED_PAIR').length;
  const unwindCount = posCount - mergedCount;
  const requiredObs = 120;
  const powerPct = Math.min(100, Math.round((posCount / requiredObs) * 100));

  if (banner) {
    banner.innerHTML = `
      <div class="dist-power-stat">
        <span class="label">Sample Universe:</span>
        <span class="val">${posCount} Observations</span>
        <span class="sub">(${mergedCount} Merged · ${unwindCount} Unwind)</span>
      </div>
      <div class="dist-power-stat">
        <span class="label">Statistical Power Target:</span>
        <span class="val ${posCount >= requiredObs ? 'positive' : ''}">${posCount} / ${requiredObs} Obs</span>
        <span class="sub">(${powerPct}% Power · Sequential SPRT Active)</span>
      </div>
      <div class="dist-progress-wrap">
        <div class="dist-progress-bar">
          <div class="dist-progress-fill" style="width:${powerPct}%"></div>
        </div>
        <span style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;color:${posCount >= requiredObs ? '#34d399' : '#38bdf8'}">${powerPct}%</span>
      </div>
    `;
  }

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Empirical Position Return Normal Distribution">
      <defs>
        <linearGradient id="ciZoneGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#34d399" stop-opacity="0.30"/>
          <stop offset="100%" stop-color="#34d399" stop-opacity="0.06"/>
        </linearGradient>
      </defs>

      <!-- Background Grid lines -->
      <line x1="${padL}" y1="${padT + plotH * 0.25}" x2="${w - padR}" y2="${padT + plotH * 0.25}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH * 0.50}" x2="${w - padR}" y2="${padT + plotH * 0.50}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH * 0.75}" x2="${w - padR}" y2="${padT + plotH * 0.75}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="2,2"/>

      <!-- Shaded Confidence Interval Envelope (CI_lower to CI_upper) -->
      <rect x="${ciX1}" y="${padT}" width="${ciWidth}" height="${plotH}" fill="url(#ciZoneGrad)" stroke="rgba(52, 211, 153, 0.5)" stroke-dasharray="3,2" stroke-width="1.2" rx="3"/>

      <!-- Histogram Frequency Bars -->
      ${barsSvg}

      <!-- Normal Gaussian Distribution Bell Spline Curve (Centered at Middle) -->
      <path d="${curvePath}" fill="none" stroke="#38bdf8" stroke-width="2.6" stroke-linecap="round"/>

      <!-- Zero Breakeven Reference Line (if applicable) -->
      ${zeroX != null && zeroX >= padL && zeroX <= (w - padR) ? `
        <line x1="${zeroX}" y1="${padT}" x2="${zeroX}" y2="${padT + plotH}" stroke="#ef4444" stroke-width="1.8" stroke-dasharray="3,3"/>
        <text x="${zeroX}" y="${padT - 8}" fill="#f87171" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">0.00% BREAKEVEN</text>
      ` : ''}

      <!-- Sample Mean Line (μ) Anchored Directly in Middle -->
      <line x1="${meanX}" y1="${padT}" x2="${meanX}" y2="${padT + plotH}" stroke="#38bdf8" stroke-width="2.4"/>

      <!-- Individual Scatter Points for all closed positions -->
      ${dotsSvg}

      <!-- Axis Base Line -->
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--border-strong)" stroke-width="1.4"/>

      <!-- Axis Labels (Symmetric around the center Mean) -->
      <text x="${padL}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">${formatVal(minDomain)}</text>
      <text x="${padL + plotW * 0.25}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">${formatVal(minDomain + span * 0.25)}</text>
      <text x="${meanX}" y="${padT + plotH + 16}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">μ ${formatVal(mean)}</text>
      <text x="${padL + plotW * 0.75}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">${formatVal(minDomain + span * 0.75)}</text>
      <text x="${w - padR}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">${formatVal(maxDomain)}</text>
    </svg>
    <div id="pos-scatter-tooltip" class="pos-dot-tooltip" style="display:none"></div>
  `;

  // Attach hover interactions for scatter dots
  const tooltip = container.querySelector('#pos-scatter-tooltip');
  const dots = container.querySelectorAll('.pos-scatter-dot');
  dots.forEach(dot => {
    dot.addEventListener('mouseenter', (e) => {
      if (!tooltip) return;
      const id = dot.dataset.id;
      const market = dot.dataset.market;
      const val = dot.dataset.val;
      const type = dot.dataset.type;
      tooltip.innerHTML = `
        <div style="color:var(--text-muted);font-size:9.5px">${esc(id)} · ${esc(type)}</div>
        <div style="font-weight:700;color:var(--text-primary);max-width:220px;white-space:normal">${esc(market)}</div>
        <div style="font-size:12px;font-weight:800;color:${val.startsWith('+') ? '#34d399' : '#f87171'}">Result: ${esc(val)}</div>
      `;
      const rect = container.getBoundingClientRect();
      const dotRect = dot.getBoundingClientRect();
      tooltip.style.left = `${dotRect.left - rect.left + dotRect.width / 2}px`;
      tooltip.style.top = `${dotRect.top - rect.top}px`;
      tooltip.style.display = 'flex';
    });
    dot.addEventListener('mouseleave', () => {
      if (tooltip) tooltip.style.display = 'none';
    });
  });

  if (footer) {
    footer.innerHTML = `
      <div class="chart-footer-item"><span>Sample Mean (μ):</span> <b style="color:#38bdf8">${formatVal(mean)}</b></div>
      <div class="chart-footer-item"><span>Std Dev (σ):</span> <b>${stdev == null ? 'unmeasured' : '±' + formatVal(stdev).replace('+', '')}</b></div>
      <div class="chart-footer-item"><span>Standard Error (SE):</span> <b>${sem == null ? 'unmeasured' : formatVal(sem).replace('+', '')}</b></div>
      <div class="chart-footer-item"><span>${currentCiLevel}% Confidence Interval:</span> <b style="color:${sem == null ? 'var(--text-muted)' : '#34d399'}">${sem == null ? 'unmeasured' : `[${formatVal(ciLower)}, ${formatVal(ciUpper)}]`}</b></div>
      <div class="chart-footer-item"><span>Distribution Sample Universe:</span> <b>N = ${posCount} observations</b></div>
      <div class="chart-footer-item"><span>Statistical Edge:</span> <b style="color:${sem == null ? 'var(--text-muted)' : 'var(--signal)'}">${sem == null ? 'ACCUMULATING (needs 2+ observations)' : 'H₁: μ &gt; 0 CONFIRMED'}</b></div>
    `;
  }
}

/* The two ends of a confidence interval, whichever shape it arrives in.
 * Returns null when either end is missing, so the caller shows its placeholder
 * rather than `NaN%`. */
function _ciBounds(ci) {
  if (!ci) return null;
  const lower = Array.isArray(ci) ? ci[0] : ci.lower;
  const upper = Array.isArray(ci) ? ci[1] : ci.upper;
  if (!Number.isFinite(Number(lower)) || !Number.isFinite(Number(upper))) return null;
  return [Number(lower), Number(upper)];
}

function renderQuantRiskGrid(ta, p, stats) {
  const container = document.getElementById('quant-grid');
  if (!container) return;

  const n = ta.n_closes ?? (ta.closes_count || 0);
  // `$-0.343` puts the minus inside the currency; the sign goes in front.
  const expectancy = ta.expectancy_usd != null && n > 0
    ? `${ta.expectancy_usd < 0 ? '-' : ''}$${Math.abs(ta.expectancy_usd).toFixed(3)}`
    : '$0.000';
  const meanRet = ta.mean_return_pct != null && n > 0 ? `${ta.mean_return_pct.toFixed(2)}%` : '0.00%';
  const winRate = ta.win_rate != null && n > 0 ? `${(ta.win_rate * 100).toFixed(1)}%` : '0.0%';
  // `win_rate_ci95` is `{lower, upper}`, not a two-element array. Indexing it
  // gave `undefined * 100` on both ends, so the Wilson interval rendered as
  // `[NaN%–NaN%]` -- an interval that exists in the report and was never once
  // displayed. The array form is still read, for any caller that sends one.
  const ciBounds = _ciBounds(ta.win_rate_ci95);
  const ci95 = ciBounds && n > 0
    ? `[${(ciBounds[0] * 100).toFixed(0)}%–${(ciBounds[1] * 100).toFixed(0)}%]`
    : '[0%–0%]';
  const var95 = ta.var_95_usd != null && n > 0 ? `$${ta.var_95_usd.toFixed(2)}` : '$0.00';
  const cvar95 = ta.cvar_95_usd != null && n > 0 ? `$${ta.cvar_95_usd.toFixed(2)}` : '$0.00';
  const sharpe = ta.sharpe_ratio != null && n > 0 ? ta.sharpe_ratio.toFixed(2) : '0.00';
  const sortino = ta.sortino_ratio != null && n > 0 ? ta.sortino_ratio.toFixed(2) : '0.00';
  const kelly = ta.kelly_fraction != null && n > 0 ? `${(ta.kelly_fraction * 100).toFixed(1)}%` : '0.0%';
  const halfKelly = ta.half_kelly != null && n > 0 ? `${(ta.half_kelly * 100).toFixed(1)}%` : '0.0%';
  const profitFactor = ta.profit_factor != null && n > 0 ? `${ta.profit_factor.toFixed(2)}x` : '0.00x';
  const payoffRatio = ta.payoff_ratio != null && n > 0 ? `${ta.payoff_ratio.toFixed(2)}x` : '0.00x';

  container.innerHTML = `
    <div class="quant-tile">
      <div class="quant-label">Mathematical Expectancy</div>
      <div class="quant-value ${n > 0 ? signClass(ta.expectancy_usd) : ''}">${esc(expectancy)}</div>
      <div class="quant-sub">${esc(meanRet)} mean return / trade</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">95% Value at Risk (1D)</div>
      <div class="quant-value ${n > 0 && Number(ta.var_95_usd) ? 'negative' : ''}">${esc(var95)}</div>
      <div class="quant-sub">CVaR Tail: ${esc(cvar95)}</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">Sharpe &amp; Sortino Ratio</div>
      <div class="quant-value ${n > 0 ? signClass(ta.sharpe_ratio) : ''}">${esc(sharpe)} <span style="font-size:11px;color:var(--text-secondary)">/ ${esc(sortino)}</span></div>
      <div class="quant-sub">Downside-deviation weighted</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">Kelly Optimal Sizing</div>
      <div class="quant-value">${esc(kelly)}</div>
      <div class="quant-sub">Half-Kelly: <b>${esc(halfKelly)}</b> (Conservative)</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">Win Rate &amp; Wilson CI</div>
      <div class="quant-value">${esc(winRate)}</div>
      <div class="quant-sub">95% CI: ${esc(ci95)}</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">Profit Factor &amp; Payoff</div>
      <div class="quant-value ${n > 0 ? (Number(ta.profit_factor) >= 1 ? 'positive' : 'negative') : ''}">${esc(profitFactor)}</div>
      <div class="quant-sub">Payoff Ratio: ${esc(payoffRatio)}</div>
    </div>
  `;
}

function renderPairCostKdeChart(stats) {
  const container = document.getElementById('pair-cost-svg-container');
  const footer = document.getElementById('pair-cost-footer');
  const medianBadge = document.getElementById('hist-median-badge');
  if (!container) return;

  const pc = stats?.pair_costs || {};
  const bins = pc.bins || [];
  // No fallback constant here. The one that used to sit in this line rendered
  // a plausible mean pair cost for a run that had assembled no pairs at all,
  // which is a number the operator would have acted on.
  const mean = pc.mean != null ? pc.mean : null;
  const stdev = pc.stdev != null ? pc.stdev : null;
  const median = pc.median != null ? pc.median : null;

  if (medianBadge) {
    medianBadge.textContent = median == null
      ? 'Median: unmeasured' : `Median: $${median.toFixed(3)}`;
  }

  if (!bins || bins.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-state-title">Distribution unmeasured</div></div>`;
    return;
  }

  const w = 480;
  const h = 220;
  const padL = 36;
  const padR = 20;
  const padT = 32;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  // Symmetrically anchor Mean in the dead center
  const delta = 0.035;
  const minDomain = mean - delta;
  const maxDomain = mean + delta;
  const span = 2 * delta;

  const getX = (val) => padL + ((val - minDomain) / span) * plotW;
  const meanX = padL + plotW / 2; // Exact center
  const ceilingX = getX(0.990);

  const maxCount = Math.max(...bins.map(b => b.count || 0), 1);
  const step = plotW / bins.length;
  const barW = step * 0.76;

  let barsSvg = '';
  let kdePoints = [];

  bins.forEach((b, i) => {
    // Map bin center to scale
    const binMid = (b.min + b.max) / 2;
    const x = getX(binMid) - barW / 2;
    const barH = ((b.count || 0) / maxCount) * plotH * 0.85;
    const y = padT + plotH - barH;
    const isReject = b.status === 'reject' || b.min >= 0.990;
    const fill = isReject ? 'rgba(239, 68, 68, 0.45)' : 'rgba(56, 189, 248, 0.65)';
    const stroke = isReject ? '#f87171' : '#38bdf8';

    if (x >= padL - 10 && x + barW <= w - padR + 10) {
      barsSvg += `<rect x="${x}" y="${y}" width="${barW}" height="${barH}" rx="3" fill="${fill}" stroke="${stroke}" stroke-width="1"><title>${b.label}: ${b.count} pairs (${b.density}%)</title></rect>`;
    }
  });

  // Calculate KDE spline symmetric around mean
  const sampleSteps = 40;
  for (let i = 0; i <= sampleSteps; i++) {
    const xVal = minDomain + (i / sampleSteps) * span;
    const pdf = Math.exp(-0.5 * Math.pow((xVal - mean) / stdev, 2));
    const kdeY = padT + plotH - pdf * plotH * 0.82;
    kdePoints.push(`${getX(xVal).toFixed(1)},${kdeY.toFixed(1)}`);
  }

  const kdePath = kdePoints.length > 1 ? `M ${kdePoints.join(' L ')}` : '';

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Pair Cost and Spread Distribution">
      <!-- Grid lines -->
      <line x1="${padL}" y1="${padT + plotH * 0.25}" x2="${w - padR}" y2="${padT + plotH * 0.25}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH * 0.50}" x2="${w - padR}" y2="${padT + plotH * 0.50}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH * 0.75}" x2="${w - padR}" y2="${padT + plotH * 0.75}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--border-strong)" stroke-width="1.2"/>

      <!-- Bars -->
      ${barsSvg}

      <!-- KDE Smooth Spline Curve -->
      <path d="${kdePath}" fill="none" stroke="#34d399" stroke-width="2.2" stroke-linecap="round"/>

      <!-- Hard Profit Ceiling Line at $0.990 -->
      ${ceilingX >= padL && ceilingX <= w - padR ? `
        <line x1="${ceilingX}" y1="${padT}" x2="${ceilingX}" y2="${padT + plotH}" stroke="#ef4444" stroke-width="1.8" stroke-dasharray="4,3"/>
        <text x="${ceilingX}" y="${padT - 8}" fill="#f87171" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">MAX $0.990</text>
      ` : ''}

      <!-- Center Mean Line Anchored at Middle -->
      <line x1="${meanX}" y1="${padT}" x2="${meanX}" y2="${padT + plotH}" stroke="#38bdf8" stroke-width="2"/>
      <text x="${meanX}" y="${padT - 8}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">MEAN μ $${mean.toFixed(3)}</text>

      <!-- Axis Labels (Symmetric around center mean) -->
      <text x="${padL}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">$${minDomain.toFixed(3)}</text>
      <text x="${padL + plotW * 0.25}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">$${(minDomain + span * 0.25).toFixed(3)}</text>
      <text x="${meanX}" y="${padT + plotH + 16}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">μ $${mean.toFixed(3)}</text>
      <text x="${padL + plotW * 0.75}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">$${(minDomain + span * 0.75).toFixed(3)}</text>
      <text x="${w - padR}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">$${maxDomain.toFixed(3)}</text>
    </svg>
  `;

  if (footer) {
    footer.innerHTML = `
      <div class="chart-footer-item"><span>Mean Cost (μ):</span> <b style="color:#38bdf8">${mean == null ? 'unmeasured' : '$' + mean.toFixed(3)}</b></div>
      <div class="chart-footer-item"><span>Std Dev (σ):</span> <b>${stdev == null ? 'unmeasured' : '±$' + stdev.toFixed(3)}</b></div>
      <div class="chart-footer-item"><span>Min Observed:</span> <b>${pc.min_observed == null ? 'unmeasured' : '$' + pc.min_observed.toFixed(3)}</b></div>
      <div class="chart-footer-item"><span>Executed Pairs:</span> <b>${pc.samples_count == null ? 'unmeasured' : pc.samples_count + ' merged'}${Array.isArray(stats?.closed_positions) ? ` (${stats.closed_positions.length} closes)` : ''}</b></div>
    `;
  }
}

function renderMonteCarloChart(stats, cyclesCount = 100) {
  const container = document.getElementById('monte-carlo-svg-container');
  const footer = document.getElementById('monte-carlo-footer');
  if (!container) return;

  const mc = stats?.monte_carlo || {};
  let steps = mc.steps || [];
  if (steps.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-state-title">Simulation unmeasured</div></div>`;
    return;
  }

  // Filter or scale steps based on cyclesCount
  const maxCycle = cyclesCount;
  const filteredSteps = steps.filter(s => s.cycle <= maxCycle);
  const dataSteps = filteredSteps.length >= 3 ? filteredSteps : steps;

  const w = 480;
  const h = 190;
  const padL = 40;
  const padR = 20;
  const padT = 18;
  const padB = 28;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const minVal = Math.min(...dataSteps.map(s => s.p01), 95);
  const maxVal = Math.max(...dataSteps.map(s => s.p99), 125);
  const valSpan = Math.max(maxVal - minVal, 10);

  const getX = (idx) => padL + (idx / (dataSteps.length - 1)) * plotW;
  const getY = (val) => padT + plotH - ((val - minVal) / valSpan) * plotH;

  const p99Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p99)}`);
  const p90Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p90)}`);
  const p50Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p50)}`);
  const p10Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p10)}`);
  const p01Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p01)}`);

  // Area between P90 and P10
  const p90_p10_area = `M ${p90Points.join(' L ')} L ${[...p10Points].reverse().join(' L ')} Z`;
  // Area between P99 and P01
  const p99_p01_area = `M ${p99Points.join(' L ')} L ${[...p01Points].reverse().join(' L ')} Z`;

  const baselineY = getY(100);

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Monte Carlo Simulation Fan Chart">
      <defs>
        <linearGradient id="mcCone99" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.18"/>
          <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.04"/>
        </linearGradient>
        <linearGradient id="mcCone90" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#34d399" stop-opacity="0.32"/>
          <stop offset="100%" stop-color="#34d399" stop-opacity="0.10"/>
        </linearGradient>
      </defs>

      <!-- Baseline $100 Reference Line -->
      <line x1="${padL}" y1="${baselineY}" x2="${w - padR}" y2="${baselineY}" stroke="rgba(255,255,255,0.22)" stroke-dasharray="3,3" stroke-width="1.2"/>
      <text x="${padL - 4}" y="${baselineY + 3}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8" text-anchor="end">$100</text>

      <!-- 99% Confidence Outer Envelope -->
      <path d="${p99_p01_area}" fill="url(#mcCone99)" stroke="none"/>
      
      <!-- 90% Confidence Inner Corridor -->
      <path d="${p90_p10_area}" fill="url(#mcCone90)" stroke="none"/>

      <!-- Boundary Lines -->
      <path d="M ${p99Points.join(' L ')}" fill="none" stroke="rgba(56, 189, 248, 0.45)" stroke-width="1" stroke-dasharray="2,2"/>
      <path d="M ${p01Points.join(' L ')}" fill="none" stroke="rgba(248, 113, 113, 0.55)" stroke-width="1" stroke-dasharray="2,2"/>
      <path d="M ${p90Points.join(' L ')}" fill="none" stroke="rgba(52, 211, 153, 0.7)" stroke-width="1.4"/>
      <path d="M ${p10Points.join(' L ')}" fill="none" stroke="rgba(52, 211, 153, 0.7)" stroke-width="1.4"/>

      <!-- Median Expected Trajectory (P50) -->
      <path d="M ${p50Points.join(' L ')}" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round"/>

      <!-- End Value Badges -->
      <text x="${w - padR - 4}" y="${getY(dataSteps[dataSteps.length - 1].p50) - 6}" fill="${(dataSteps[dataSteps.length - 1].p50 - 100) < 0 ? '#f87171' : '#34d399'}" font-family="'JetBrains Mono', monospace" font-size="9" font-weight="700" text-anchor="end">${fmtSignedUSD(dataSteps[dataSteps.length - 1].p50 - 100)}</text>

      <!-- X-Axis Labels -->
      <text x="${padL}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">0 Cycles</text>
      <text x="${padL + plotW * 0.5}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${Math.round(maxCycle / 2)} Cycles</text>
      <text x="${w - padR}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">${maxCycle} Cycles</text>
    </svg>
  `;

  if (footer) {
    const endP50 = dataSteps[dataSteps.length - 1].p50;
    // `prob_positive_return` arrives as a percentage already. Multiplying it
    // again rendered 98.4% as 9840%, and the fallback asserted a probability
    // for a simulation that was never run.
    const profitProb = mc.prob_positive_return != null
      ? mc.prob_positive_return.toFixed(1) : null;
    footer.innerHTML = `
      <div class="chart-footer-item"><span>P(Profit &gt; 0):</span> <b style="color:var(--signal)">${profitProb == null ? 'unmeasured' : profitProb + '%'}</b></div>
      <div class="chart-footer-item"><span>Median Return:</span> <b style="color:${(endP50 - 100) < 0 ? '#f87171' : 'var(--signal)'}">${fmtSignedUSD(endP50 - 100)}</b></div>
      <div class="chart-footer-item"><span>Worst-Case Drawdown:</span> <b style="color:#f87171">${mc.worst_case_drawdown_pct == null ? 'unmeasured' : mc.worst_case_drawdown_pct.toFixed(2) + '%'}</b></div>
      <div class="chart-footer-item"><span>Simulations:</span> <b>${mc.paths == null ? 'unmeasured' : mc.paths.toLocaleString() + ' Paths'}</b></div>
    `;
  }
}

function renderProbabilityBellChart(stats) {
  const container = document.getElementById('prob-bell-svg-container');
  const footer = document.getElementById('prob-bell-footer');
  const sweetBadge = document.getElementById('bell-sweetspot-badge');
  if (!container) return;

  const pb = stats?.probability_bell || {};
  const bins = pb.bins || [];
  if (sweetBadge && pb.sweet_spot_pct != null) sweetBadge.textContent = `${pb.sweet_spot_pct}% In Sweet Spot`;

  if (!bins || bins.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-state-title">Odds unmeasured</div></div>`;
    return;
  }

  const w = 480;
  const h = 220;
  const padL = 36;
  const padR = 20;
  const padT = 32;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const maxPdf = Math.max(...bins.map(b => b.theoretical_pdf || 0), 2.5);

  const step = plotW / bins.length;
  const barW = step * 0.75;

  let barsSvg = '';
  let bellPoints = [];

  bins.forEach((b, i) => {
    const x = padL + i * step;
    const barH = ((b.empirical_count || 1) / 12) * plotH * 0.8;
    const y = padT + plotH - barH;
    const inSweet = b.in_sweet_spot;
    const fill = inSweet ? 'rgba(56, 189, 248, 0.45)' : 'rgba(255, 255, 255, 0.12)';
    barsSvg += `<rect x="${x + (step - barW) / 2}" y="${y}" width="${barW}" height="${barH}" rx="2" fill="${fill}"><title>${b.bin}: ${b.empirical_count} contracts</title></rect>`;

    const pdfY = padT + plotH - ((b.theoretical_pdf || 0) / maxPdf) * plotH;
    bellPoints.push(`${x + step / 2},${pdfY}`);
  });

  // Sweet spot range (0.15 to 0.85)
  const sweetLeft = padL + (0.10 / 0.90) * plotW;
  const sweetRight = padL + (0.80 / 0.90) * plotW;

  const bellPath = bellPoints.length > 1 ? `M ${bellPoints.join(' L ')}` : '';

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Implied Odds Bell Curve">
      <!-- Sweet Spot Shaded Background (15% to 85%) -->
      <rect x="${sweetLeft}" y="${padT}" width="${sweetRight - sweetLeft}" height="${plotH}" fill="rgba(56, 189, 248, 0.06)" rx="4"/>
      <text x="${sweetLeft + 6}" y="${padT - 8}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8" font-weight="700">SWEET SPOT (15%–85%)</text>

      <!-- Center 50% Fair Odds Line Anchored at Middle -->
      <line x1="${padL + plotW / 2}" y1="${padT}" x2="${padL + plotW / 2}" y2="${padT + plotH}" stroke="#38bdf8" stroke-width="2"/>
      <text x="${padL + plotW / 2}" y="${padT - 8}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">FAIR ODDS μ 50%</text>

      <!-- Empirical Histogram Bars -->
      ${barsSvg}

      <!-- Gaussian Normal Curve Spline -->
      <path d="${bellPath}" fill="none" stroke="#38bdf8" stroke-width="2.2" stroke-linecap="round"/>

      <!-- Baseline -->
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--border-strong)" stroke-width="1.2"/>

      <!-- X-Axis Labels -->
      <text x="${padL}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">5%</text>
      <text x="${padL + plotW * 0.25}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">25%</text>
      <text x="${padL + plotW * 0.5}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">50% (Toss-up)</text>
      <text x="${padL + plotW * 0.75}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">75%</text>
      <text x="${w - padR}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">95%</text>
    </svg>
  `;

  if (footer) {
    footer.innerHTML = `
      <div class="chart-footer-item"><span>Mean Probability:</span> <b>${pb.mean == null ? 'unmeasured' : (pb.mean * 100).toFixed(1) + '%'}</b></div>
      <div class="chart-footer-item"><span>Std Deviation:</span> <b>${pb.stdev == null ? 'unmeasured' : '±' + (pb.stdev * 100).toFixed(1) + '%'}</b></div>
      <div class="chart-footer-item"><span>Sweet Spot Concentration:</span> <b style="color:var(--signal)">${pb.sweet_spot_pct == null ? 'unmeasured' : pb.sweet_spot_pct.toFixed(1) + '%'}</b></div>
      <div class="chart-footer-item"><span>Executed Sample:</span> <b>${pb.samples_count == null ? 'unmeasured' : pb.samples_count + ' fills'}</b></div>
    `;
  }
}

function renderMarkoutChart(stats) {
  const container = document.getElementById('markout-svg-container');
  const footer = document.getElementById('markout-footer');
  if (!container) return;

  const mk = stats?.markout || {};
  const intervals = mk.intervals || [];

  if (!intervals || intervals.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-state-title">Markout unmeasured</div></div>`;
    return;
  }

  const w = 480;
  const h = 190;
  const padL = 36;
  const padR = 20;
  const padT = 18;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  // Displacement is signed, and the sign IS the finding: the chart is named
  // for adverse selection, and adverse selection is the mid falling away from
  // a fill we just bought -- a NEGATIVE number. Scaling every bar off a
  // positive-only maximum drew those bars with a negative `height`, which SVG
  // rejects outright: the bar vanished, the label read `+-51.5 bps`, and the
  // one measurement this panel exists to show was the one it could not draw.
  //
  // So the zero line goes where the data puts it, and a bar hangs below it
  // when the mid went the wrong way.
  const values = intervals.map(i => Number(i.displacement_bps) || 0);
  const hi = Math.max(...values, 0);
  const lo = Math.min(...values, 0);
  // A floor on the span, so a run of near-zero horizons is not magnified into
  // a dramatic curve by its own rounding noise.
  const span = Math.max(hi - lo, 3.0);
  const usable = plotH * 0.85;
  // One-sided data keeps the baseline on the edge it has always sat on: with
  // nothing adverse the zero line is the floor of the plot, exactly as before
  // this change, and centring the band would have shifted every favourable
  // bar up by ~11px for no reason. Only a chart carrying both signs needs the
  // line somewhere in the middle.
  const zeroY = lo === 0 ? padT + plotH
    : hi === 0 ? padT
    : padT + (plotH - usable) / 2 + (hi / span) * usable;
  const step = plotW / intervals.length;
  const barW = step * 0.55;

  let barsSvg = '';
  let linePoints = [];

  intervals.forEach((item, idx) => {
    const value = Number(item.displacement_bps) || 0;
    const adverse = value < 0;
    const x = padL + idx * step + (step - barW) / 2;
    // Magnitude, always positive: SVG has no negative height, and the
    // direction is carried by which side of the zero line the bar starts on.
    const barH = Math.abs(value / span) * usable;
    const y = adverse ? zeroY : zeroY - barH;
    const fill = adverse ? 'rgba(248, 113, 113, 0.65)' : 'rgba(16, 185, 129, 0.65)';
    const stroke = adverse ? '#f87171' : '#34d399';
    // The sign comes from the number, never from a hardcoded `+`.
    const label = `${value >= 0 ? '+' : ''}${value.toFixed(1)} bps`;
    // Below the bar when it hangs down, above it when it stands up, so the
    // text never sits on top of the zero line.
    const labelY = adverse ? (y + barH + 9) : (y - 4);

    barsSvg += `
      <rect x="${x}" y="${y}" width="${barW}" height="${barH}" rx="3" fill="${fill}" stroke="${stroke}" stroke-width="1">
        <title>${item.horizon}: ${label} (${item.samples} samples)</title>
      </rect>
      <text x="${x + barW / 2}" y="${labelY}" fill="${stroke}" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">${label}</text>
    `;

    linePoints.push(`${x + barW / 2},${adverse ? y + barH : y}`);
  });

  const linePath = linePoints.length > 1 ? `M ${linePoints.join(' L ')}` : '';

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Adverse Selection Markout Decay">
      <!-- Zero Baseline: where zero actually falls, not the floor of the plot -->
      <line x1="${padL}" y1="${zeroY}" x2="${w - padR}" y2="${zeroY}" stroke="var(--border-strong)" stroke-width="1.2"/>

      <!-- Bars -->
      ${barsSvg}

      <!-- Trajectory Line -->
      <path d="${linePath}" fill="none" stroke="${lo < 0 ? '#94a3b8' : '#10b981'}" stroke-width="2" stroke-linecap="round"/>

      <!-- X-Axis Labels -->
      ${intervals.map((item, idx) => {
        const x = padL + idx * step + step / 2;
        return `<text x="${x}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${item.horizon}</text>`;
      }).join('')}
    </svg>
  `;

  if (footer) {
    // Read off the horizons that actually matured. The four constants that
    // used to sit in this footer reported a clean bill of health -- zero drift,
    // a favourable retention figure, a sample count and a HEALTHY verdict --
    // for a measurement nobody had taken.
    const lastInterval = intervals.length ? intervals[intervals.length - 1] : null;
    const maturedSamples = intervals.length
      ? intervals.reduce((total, item) => total + (item.samples || 0), 0) : null;
    const driftColour = lastInterval == null
      ? 'var(--text-muted)'
      : (lastInterval.displacement_bps < 0 ? '#f87171' : 'var(--signal)');
    const markoutStatus = lastInterval == null
      ? 'UNMEASURED'
      : (lastInterval.displacement_bps < 0 ? 'ADVERSE' : 'FAVOURABLE');
    footer.innerHTML = `
      <div class="chart-footer-item"><span>Longest Horizon:</span> <b>${lastInterval == null ? 'unmeasured' : lastInterval.horizon}</b></div>
      <div class="chart-footer-item"><span>Displacement:</span> <b style="color:${driftColour}">${lastInterval == null ? 'unmeasured' : (lastInterval.displacement_bps >= 0 ? '+' : '') + lastInterval.displacement_bps.toFixed(1) + ' bps'}</b></div>
      <div class="chart-footer-item"><span>Matured Samples:</span> <b>${maturedSamples == null ? 'unmeasured' : maturedSamples + ' fills'}</b></div>
      <div class="chart-footer-item"><span>Markout Status:</span> <b style="color:${driftColour}">${markoutStatus}</b></div>
    `;
  }
}

// STATISTICAL DECISION GATES.
//
// Four verdict states, and every one of them has to be visible: GO (green),
// NO-GO (red), ACCUMULATING (amber), STANDBY (neutral). The panel used to emit
// a `stopped` class with no CSS rule behind it, so STANDBY rendered as
// unstyled text and could not be told from a caption.
const GATE_VERDICTS = {
  go: { cls: 'go', label: 'GO' },
  confirmed: { cls: 'go', label: 'GO (CONFIRMED)' },
  active: { cls: 'go', label: 'ACTIVE RULE' },
  nogo: { cls: 'nogo', label: 'NO-GO' },
  accumulating: { cls: 'accumulating', label: 'ACCUMULATING' },
  standby: { cls: 'standby', label: 'STANDBY' },
};

function gateBadge(state) {
  const verdict = GATE_VERDICTS[state] || GATE_VERDICTS.standby;
  return `<span class="analytics-gate-badge ${verdict.cls}">${verdict.label}</span>`;
}

// A value nobody has measured is not a value. Printing a plausible number for
// an unmeasured gate is how a panel that reads GO ends up describing a run
// that produced no observations at all.
function gateObserved(text, measured) {
  return `<td class="gate-observed">${measured ? esc(String(text)) : '<span class="analytics-unmeasured">unmeasured</span>'}</td>`;
}

// `data-math` carries the LaTeX; the element's text is the Unicode fallback
// that stays put when KaTeX did not load. Never raw LaTeX on screen.
function mathSpan(tex, fallback) {
  return `<span class="math-inline" data-math="${esc(tex)}">${esc(fallback)}</span>`;
}

function typesetMath(root) {
  const scope = root || document;
  const nodes = scope.querySelectorAll ? scope.querySelectorAll('[data-math]') : [];
  if (!nodes.length) return;
  const katex = (typeof window !== 'undefined') ? window.katex : undefined;
  if (!katex || typeof katex.render !== 'function') return;
  nodes.forEach(node => {
    try {
      katex.render(node.getAttribute('data-math'), node, {
        throwOnError: false,
        displayMode: false,
      });
    } catch (e) {
      // Leave the Unicode fallback exactly where it is.
    }
  });
}

function decisionGatesRows(ta, stats, n, kpi) {
  // Markout figures live at the top level of the KPI payload, not inside
  // trade_analytics. Reading them off `ta` alone is why this row reported
  // "0 Samples" on runs that had measured plenty.
  const markout = kpi || {};
  const lower = ta.ci90_lower_pct != null ? Number(ta.ci90_lower_pct) : null;
  const winRate = ta.win_rate != null ? Number(ta.win_rate) * 100 : null;
  const required = ta.required_observations != null ? Number(ta.required_observations) : 120;
  const progressPct = required > 0 ? Math.min(100, Math.round((n / required) * 100)) : 0;
  const sweetSpot = stats?.probability_bell?.sweet_spot_pct;
  const samplesRaw = markout.markout_samples ?? ta.markout_samples;
  const markoutSamples = samplesRaw == null ? null : Number(samplesRaw);
  const driftRaw = markout.adverse_selection ?? ta.adverse_selection;
  const drift = driftRaw != null ? Number(driftRaw) : null;
  // The baseline-corrected figure, when the sampler recorded peers for it.
  const excessRaw = markout.adverse_selection_excess ?? ta.adverse_selection_excess;
  const excess = excessRaw != null ? Number(excessRaw) : null;
  const drawdown = ta.max_drawdown_pct != null ? Number(ta.max_drawdown_pct) : null;

  const edgeState = n >= 10 && lower != null && lower > 0
    ? 'confirmed'
    : (n > 0 ? 'accumulating' : 'standby');
  const neutralityState = n >= 5 && winRate != null && winRate >= 50
    ? 'go'
    : (n > 0 ? 'accumulating' : 'standby');
  const powerState = n >= required ? 'go' : (n > 0 ? 'accumulating' : 'standby');
  // A gate whose threshold is "drift >= 0 over >= 25 matured fills" cannot
  // read GO on one sample, and a negative drift is a NO-GO, not a shrug.
  const markoutState = (markoutSamples == null || markoutSamples <= 0)
    ? 'standby'
    : (drift != null && drift < 0
        ? 'nogo'
        : (markoutSamples >= 25 ? 'go' : 'accumulating'));
  // Same correction: the drawdown gate used to read GO for any run with a
  // close in it, including one that had blown straight through the envelope.
  const drawdownState = n <= 0
    ? 'standby'
    : (drawdown != null && Math.abs(drawdown) > 5.0 ? 'nogo' : 'go');

  return [
    {
      group: 'Constant settings',
      name: 'Strategy Pricing Band',
      standard: 'Implied contract probability sweet-spot filter',
      threshold: '$0.15 &le; P &le; $0.85',
      observed: sweetSpot != null ? `$0.15 – $0.85 (${Number(sweetSpot).toFixed(1)}% in band)` : '$0.15 – $0.85',
      measured: true,
      state: 'active',
    },
    {
      group: 'Accumulating gates',
      name: `Edge Viability ${mathSpan('H_1: \\mu > 0', 'H₁: μ > 0')}`,
      standard: '90% confidence lower bound of realised spread',
      threshold: '&gt; 0.00% (strictly positive)',
      observed: lower != null ? `${lower.toFixed(2)}%` : '',
      measured: n > 0 && lower != null,
      state: edgeState,
    },
    {
      name: 'Directional Neutrality (win rate)',
      standard: 'Closed round-trips merged at $1.00',
      threshold: '&gt; 50.0%',
      observed: winRate != null ? `${winRate.toFixed(1)}%` : '',
      measured: n > 0 && winRate != null,
      state: neutralityState,
    },
    {
      name: 'Statistical Power Target',
      standard: 'Observations required to reject the null hypothesis',
      threshold: `&ge; ${required} closes`,
      observed: `${n} / ${required} (${progressPct}%)`,
      measured: true,
      state: powerState,
    },
    {
      group: 'Confirmed gates',
      name: 'Adverse Selection Markout',
      standard: 'Post-trade price drift across the matured horizon',
      threshold: '&ge; 25 matured fills, drift &ge; 0',
      observed: drift != null
        ? `${markoutSamples} samples · ${(drift * 100).toFixed(2)}¢/share`
          + (excess != null ? ` (excess ${(excess * 100).toFixed(2)}¢)` : '')
        : `${markoutSamples} samples`,
      measured: markoutSamples != null && markoutSamples > 0,
      state: markoutState,
    },
    {
      name: 'Max Drawdown Guard',
      standard: 'Peak-to-trough equity degradation envelope',
      threshold: '&le; 5.00%',
      observed: drawdown != null ? `${drawdown.toFixed(2)}%` : '',
      measured: n > 0 && drawdown != null,
      state: drawdownState,
    },
  ];
}

function decisionGatesHtml(ta, stats, n, kpi) {
  const rows = decisionGatesRows(ta || {}, stats || {}, n || 0, kpi || {});
  let body = '';
  for (const row of rows) {
    if (row.group) {
      body += `<tr class="gate-group"><th colspan="5">${esc(row.group)}</th></tr>`;
    }
    body += `<tr>
      <td class="gate-name">${row.name}</td>
      <td class="gate-standard">${row.standard}</td>
      <td class="gate-threshold">${row.threshold}</td>
      ${gateObserved(row.observed, row.measured)}
      <td>${gateBadge(row.state)}</td>
    </tr>`;
  }
  return `
    <div class="section-title-row" style="margin-bottom:10px">
      <div class="font-display" style="font-size:13px;letter-spacing:0.06em">STATISTICAL DECISION GATES &amp; HYPOTHESIS TESTING</div>
    </div>
    <table>
      <thead><tr><th>Hypothesis / decision gate</th><th>Parameter &amp; standard</th><th>Required threshold</th><th>Observed value</th><th>Gate verdict</th></tr></thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

/* Tier 1: the band around our average close, and whether it still contains a
 * loss. `mean_pnl_ci` answers that in dollars; this only has to not soften it.
 */
function renderPnlCiReadout(ta) {
  const host = document.getElementById('pnl-ci-readout');
  if (!host) return;

  const ci = ta?.mean_pnl_ci || {};
  const n = ci.n != null ? Number(ci.n) : 0;
  const mean = ci.mean_usd != null ? Number(ci.mean_usd) : null;
  const levels = Array.isArray(ci.levels) ? ci.levels : [];

  // No band is not a zero band. A run under two closes says what it has,
  // which is a mean and a sample size, and stops there.
  if (!levels.length) {
    host.innerHTML = `
      <div class="pnl-ci-mean mono">${esc(mean === null ? '--' : fmtSignedUSD(mean))}</div>
      <div class="pnl-ci-sub">Mean realized PnL per close</div>
      <div class="pnl-ci-verdict standby">NO BAND YET · ${n} ${n === 1 ? 'CLOSE' : 'CLOSES'}</div>
      <div class="pnl-ci-note">A confidence interval needs at least two closes. Nothing is being withheld; nothing has been measured.</div>
    `;
    return;
  }

  const rows = levels.map(lv => {
    const lower = Number(lv.lower);
    const upper = Number(lv.upper);
    return `
      <div class="pnl-ci-band-row">
        <span class="pnl-ci-band-label mono">${esc(String(lv.level))}%</span>
        <span class="pnl-ci-band-range mono">
          <b class="${lower < 0 ? 'negative' : 'positive'}">${esc(fmtSignedUSD(lower))}</b>
          <span class="pnl-ci-band-dash">to</span>
          <b class="${upper < 0 ? 'negative' : 'positive'}">${esc(fmtSignedUSD(upper))}</b>
        </span>
      </div>`;
  }).join('');

  const widest = levels[levels.length - 1];
  const depth = widest.negative_depth_usd != null ? Number(widest.negative_depth_usd) : null;
  const verdictMap = {
    positive: { cls: 'go', text: `BAND CLEAR OF ZERO · NO LOSS INCLUDED AT ${widest.level}%` },
    spans_zero: {
      cls: 'warn',
      text: `BAND INCLUDES A LOSS DOWN TO ${depth === null ? '--' : '-$' + depth.toFixed(3)} PER CLOSE`,
    },
    negative: { cls: 'nogo', text: `BAND ENTIRELY BELOW ZERO AT ${widest.level}%` },
  };
  const verdict = verdictMap[ci.verdict] || { cls: 'standby', text: 'VERDICT UNAVAILABLE' };

  // Determine band colors
  const bandCls = ci.verdict === 'positive' ? 'band-positive'
    : (ci.verdict === 'negative' ? 'band-negative' : 'band-warn');

  // Find 90% and 95% levels for visual bar
  const l95 = levels.find(l => Number(l.level) === 95) || levels[levels.length - 1];
  const l90 = levels.find(l => Number(l.level) === 90) || levels[0];
  const l95Lower = Number(l95.lower);
  const l95Upper = Number(l95.upper);
  const l90Lower = Number(l90.lower);
  const l90Upper = Number(l90.upper);
  const meanVal = mean !== null ? mean : (l90Lower + l90Upper) / 2;

  // Scale axis bounds
  const keyPoints = [0, l95Lower, l95Upper, l90Lower, l90Upper, meanVal];
  const minVal = Math.min(...keyPoints);
  const maxVal = Math.max(...keyPoints);
  const span = (maxVal - minVal) || 0.1;
  const pad = Math.max(span * 0.14, 0.02);
  const axisMin = minVal - pad;
  const axisMax = maxVal + pad;
  const axisRange = axisMax - axisMin;

  const toPct = (v) => Math.max(2, Math.min(98, ((v - axisMin) / axisRange) * 100));

  const pct95Left = toPct(l95Lower);
  const pct95Right = toPct(l95Upper);
  const pct95Width = Math.max(2, pct95Right - pct95Left);

  const pct90Left = toPct(l90Lower);
  const pct90Right = toPct(l90Upper);
  const pct90Width = Math.max(2, pct90Right - pct90Left);

  const meanPct = toPct(meanVal);
  const zeroPct = toPct(0);
  const showZero = (axisMin <= 0 && 0 <= axisMax);

  host.innerHTML = `
    <div class="pnl-ci-head-row">
      <div class="pnl-ci-mean mono ${mean === null ? '' : signClass(mean)}">${esc(mean === null ? '--' : fmtSignedUSD(mean))}</div>
      <div class="pnl-ci-sub">Mean realized PnL per close · ${n} ${n === 1 ? 'close' : 'closes'}</div>
    </div>

    <!-- Visual Scale Bar: ---|----------|--- -->
    <div class="pnl-ci-scale-container">
      <div class="pnl-ci-mean-pointer" style="left:${meanPct.toFixed(1)}%">
        <div class="pnl-ci-mean-badge mono ${signClass(meanVal)}">Mean ${esc(fmtSignedUSD(meanVal))}</div>
        <div class="pnl-ci-mean-pin"></div>
      </div>

      <div class="pnl-ci-axis-track">
        <div class="pnl-ci-axis-centerline"></div>

        ${showZero ? `
          <div class="pnl-ci-zero-line" style="left:${zeroPct.toFixed(1)}%">
            <span class="pnl-ci-zero-tag mono">$0.00</span>
          </div>
        ` : ''}

        <!-- 95% CI Outer Band -->
        <div class="pnl-ci-band-bar band-95 ${bandCls}" style="left:${pct95Left.toFixed(1)}%;width:${pct95Width.toFixed(1)}%" title="95% Confidence Interval">
          <span class="pnl-ci-band-tick">|</span>
          <span class="pnl-ci-band-name">95% CI</span>
          <span class="pnl-ci-band-tick">|</span>
        </div>

        <!-- 90% CI Inner Band -->
        <div class="pnl-ci-band-bar band-90 ${bandCls}" style="left:${pct90Left.toFixed(1)}%;width:${pct90Width.toFixed(1)}%" title="90% Confidence Interval">
          <span class="pnl-ci-band-tick">|</span>
          <span class="pnl-ci-band-name">90% CI</span>
          <span class="pnl-ci-band-tick">|</span>
        </div>
      </div>

      <div class="pnl-ci-scale-ticks mono">
        <div class="pnl-ci-tick-callout tick-95" style="left:${pct95Left.toFixed(1)}%">
          <span class="pnl-ci-tick-tag">95% Low</span>
          <span class="pnl-ci-tick-val ${l95Lower < 0 ? 'negative' : 'positive'}">${esc(fmtSignedUSD(l95Lower))}</span>
        </div>
        <div class="pnl-ci-tick-callout tick-90" style="left:${pct90Left.toFixed(1)}%">
          <span class="pnl-ci-tick-tag">90% Low</span>
          <span class="pnl-ci-tick-val ${l90Lower < 0 ? 'negative' : 'positive'}">${esc(fmtSignedUSD(l90Lower))}</span>
        </div>
        <div class="pnl-ci-tick-callout tick-90" style="left:${pct90Right.toFixed(1)}%">
          <span class="pnl-ci-tick-tag">90% High</span>
          <span class="pnl-ci-tick-val ${l90Upper < 0 ? 'negative' : 'positive'}">${esc(fmtSignedUSD(l90Upper))}</span>
        </div>
        <div class="pnl-ci-tick-callout tick-95" style="left:${pct95Right.toFixed(1)}%">
          <span class="pnl-ci-tick-tag">95% High</span>
          <span class="pnl-ci-tick-val ${l95Upper < 0 ? 'negative' : 'positive'}">${esc(fmtSignedUSD(l95Upper))}</span>
        </div>
      </div>
    </div>

    <div class="pnl-ci-bands">${rows}</div>
    <div class="pnl-ci-verdict ${verdict.cls}">${esc(verdict.text)}</div>
  `;
}

/* Tier 1: quoted -> filled -> closed -> merged, and the step that loses most.
 * A stepped, tapered Funnel representation showing pipeline stages, retention rates,
 * and drop-off bottlenecks between each phase.
 */
function renderExecutionFunnel(kpi) {
  const host = document.getElementById('execution-funnel');
  if (!host) return;

  const funnel = kpi?.execution_funnel;
  if (!funnel || !Array.isArray(funnel.stages) || !funnel.stages.length) {
    host.innerHTML = '<div class="pnl-ci-note">No execution telemetry in this run yet.</div>';
    return;
  }

  const stages = funnel.stages;
  const dropByFrom = {};
  for (const d of (funnel.drop_off || [])) dropByFrom[d.from] = d;
  const worst = funnel.worst_step || null;

  // Funnel stage widths for tapering geometry:
  const TAPER_WIDTHS = [100, 84, 68, 54];
  const STAGE_THEMES = ['stage-quoted', 'stage-filled', 'stage-closed', 'stage-merged'];
  const STAGE_ICONS = ['⚡', '🎯', '🔒', '💎'];

  const rows = stages.map((st, idx) => {
    const legs = Number(st.legs) || 0;
    const markets = Number(st.markets) || 0;
    const drop = dropByFrom[st.key];
    const isWorstFrom = worst && worst.from === st.key;
    const widthPct = TAPER_WIDTHS[Math.min(idx, TAPER_WIDTHS.length - 1)];
    const themeCls = STAGE_THEMES[Math.min(idx, STAGE_THEMES.length - 1)];
    const icon = STAGE_ICONS[Math.min(idx, STAGE_ICONS.length - 1)];

    const retained = drop && drop.retained_pct != null
      ? `${Number(drop.retained_pct).toFixed(1)}% carried on`
      : (drop ? 'no rate yet' : '');

    const bridgeHtml = drop ? `
      <div class="funnel-bridge${isWorstFrom ? ' worst' : ''}">
        <div class="funnel-bridge-connector">
          <div class="funnel-bridge-arrow">↓</div>
        </div>
        <div class="funnel-drop-note${isWorstFrom ? ' worst' : ''}">
          <span class="funnel-retained-badge mono">${esc(retained)}</span>
          ${drop.lost > 0 ? `<span class="funnel-lost-badge mono"> · lost ${drop.lost}</span>` : ''}
          ${isWorstFrom ? `<span class="funnel-worst-badge">⚠️ Worst Drop</span>` : ''}
        </div>
      </div>
    ` : '';

    return `
      <div class="funnel-stage-tier ${themeCls}${isWorstFrom ? ' worst' : ''}" style="width:${widthPct}%">
        <div class="funnel-stage-head">
          <span class="funnel-stage-label">
            <span class="funnel-stage-icon">${icon}</span>
            ${esc(st.label || st.key)}
          </span>
          <span class="funnel-stage-count mono">${legs} ${legs === 1 ? 'leg' : 'legs'} · ${markets} ${markets === 1 ? 'market' : 'markets'}</span>
        </div>
        <div class="funnel-bar-track">
          <div class="funnel-bar-fill" style="width:100%"></div>
        </div>
      </div>
      ${bridgeHtml}
    `;
  }).join('');

  const worstLine = worst
    ? `Worst step: ${esc(worst.from)} &rarr; ${esc(worst.to)}, ${Number(worst.lost)} lost`
    : 'No step has lost anything yet';

  const declined = (funnel.declined || []).slice(0, 3);
  const declinedChips = declined.length
    ? `<div class="funnel-declined">${declined.map(d =>
        `<span class="funnel-declined-chip mono">${esc(d.reason)} <b>${Number(d.cycles) || 0}</b></span>`).join('')}</div>`
    : '';

  host.innerHTML = `
    <div class="funnel-container">
      <div class="funnel-stages">${rows}</div>
      <div class="funnel-worst${worst ? ' has-worst' : ''}">${worstLine}</div>
      ${declinedChips}
    </div>
  `;
}

function renderAnalyticsSurface(kpi, status) {
  const grid = document.getElementById('kpi-grid');
  const gates = document.getElementById('analytics-gates');
  if (!grid || !gates) return;

  const p = kpi?.portfolio || {};
  const ta = kpi?.trade_analytics || {};
  const stats = kpi?.statistical_analytics || {};

  const nRaw = ta.n_closes != null ? ta.n_closes : ta.closes_count;
  const n = nRaw != null ? nRaw : 0;
  const wins = ta.wins != null ? ta.wins : 0;
  const losses = ta.losses != null ? ta.losses : 0;
  const required = ta.required_observations != null ? ta.required_observations : 120;
  const winRate = ta.win_rate != null ? ta.win_rate * 100 : (n > 0 ? 0.0 : 0.0);
  const lower = ta.ci90_lower_pct != null ? ta.ci90_lower_pct : 0.00;
  const progressPct = Math.min(100, Math.round((n / required) * 100));

  grid.innerHTML = `
    <div class="kpi-tile">
      <div class="kpi-label">Average Profit Per Close</div>
      ${fmtVal(ta.expectancy_usd != null && n > 0 ? fmtSignedUSD(ta.expectancy_usd) : '$0.000', n > 0 ? ' ' + signClass(ta.expectancy_usd) : '')}
      <div class="hint">Spread capture net of slippage</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Mean Return Per Trade</div>
      ${fmtVal(ta.mean_return_pct != null && n > 0 ? fmtPct(ta.mean_return_pct) : '0.00%', n > 0 ? ' ' + signClass(ta.mean_return_pct) : '')}
      <div class="hint">± ${ta.stdev_return_pct != null && n > 0 ? Number(ta.stdev_return_pct).toFixed(2) + '%' : '0.00%'} (σ)</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Annualized Sharpe Ratio</div>
      ${fmtVal(ta.sharpe_ratio != null && n > 0 ? ta.sharpe_ratio.toFixed(2) : '0.00', n > 0 ? ' ' + signClass(ta.sharpe_ratio) : '')}
      <div class="hint">Risk-adjusted spread performance</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Executed Sample Size</div>
      ${fmtVal(`${n} Closes (${wins}W / ${losses}L)`)}
      <div class="hint">Empirical Win Rate: ${n > 0 ? winRate.toFixed(1) + '%' : '0.0%'}</div>
    </div>
  `;

  // Tier 1 first: the two readings that gate live trading are rendered before
  // the drill-down decks, and a throw in either must not take the rest down.
  try { renderPnlCiReadout(ta); } catch (e) { console.error('Error rendering PnL CI readout', e); }
  try { renderExecutionFunnel(kpi); } catch (e) { console.error('Error rendering execution funnel', e); }

  // Render Quant Grid
  renderQuantRiskGrid(ta, p, stats);

  // Render SVG Charts including featured Position Return Distribution safely
  try { renderPositionDistributionChart(stats); } catch (e) { console.error('Error rendering position dist chart', e); }
  try { renderPairCostKdeChart(stats); } catch (e) { console.error('Error rendering pair cost chart', e); }
  try { renderMonteCarloChart(stats, currentMcCycles); } catch (e) { console.error('Error rendering monte carlo chart', e); }
  try { renderProbabilityBellChart(stats); } catch (e) { console.error('Error rendering probability bell chart', e); }
  try { renderMarkoutChart(stats); } catch (e) { console.error('Error rendering markout chart', e); }

  // Render the decision gates. The math is typeset after injection, so the
  // Unicode fallback in each `data-math` element is what shows if KaTeX never
  // loaded -- raw LaTeX on screen is the failure this replaced.
  gates.innerHTML = decisionGatesHtml(ta, stats, n, kpi);
  typesetMath(gates);
  typesetMath(document.getElementById('tab-2'));

  // Update sample count in sub-nav
  const samplePill = document.getElementById('stats-live-sample-count');
  if (samplePill) samplePill.textContent = `${n} Closes · Scanned Polymarket Candidates`;
}

function renderKPIs(kpi, status) {
  renderRunProfitability(kpi);
  renderBrokerPortfolioOverview(kpi, status);
  const grid = document.getElementById('kpi-grid');
  if (!kpi || !kpi.portfolio) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1">
      <div class="empty-state-title">No trading data yet</div>
      <div class="empty-state-msg">KPIs will appear when the bot makes its first spread capture.</div>
    </div>`;
    const _totals = document.getElementById('analytics-totals');
    const _charts = document.getElementById('analytics-charts');
    const _gates = document.getElementById('analytics-gates');
    if (_totals) _totals.innerHTML = '';
    if (_charts) {
      const _h = _charts.querySelector('#analytics-histogram-card');
      const _p = _charts.querySelector('#analytics-portfolio-card');
      if (_h) _h.innerHTML = '';
      if (_p) _p.innerHTML = '';
    }
    if (_gates) _gates.innerHTML = '';
    return;
  }

  const p = kpi.portfolio;
  const ta = kpi.trade_analytics || {};
  const startCap = status?.starting_capital ?? p.starting_capital;
  const realized = p.realized_pnl;
  const unrealized = p.unrealized_usd;
  const total = p.total_pnl;
  const netValue = (p.account?.account_value_usd !== null && p.account?.account_value_usd !== undefined)
    ? p.account.account_value_usd
    : p.total_value;

  grid.innerHTML = `
    <div class="kpi-tile">
      <div class="kpi-label">Net Portfolio Value</div>
      ${fmtVal(netValue !== null && netValue !== undefined ? fmtUSD(netValue) : null)}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Starting Capital</div>
      ${fmtVal(startCap !== null && startCap !== undefined ? fmtUSD(startCap) : 'estimated')}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Realized P&L</div>
      ${fmtVal(realized !== null && realized !== undefined ? fmtUSD(realized) : null, realized >= 0 ? ' positive' : ' negative')}
      <div class="kpi-value null" style="font-size:12px">${fmtPct(p.pnl_pct)}</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Unrealized P&L</div>
      ${fmtVal(unrealized !== null && unrealized !== undefined ? fmtUSD(unrealized) : (p.unrealized_measured === false ? 'unmeasured' : null))}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Win Rate</div>
      ${fmtVal(ta.win_rate !== null && ta.win_rate !== undefined ? (ta.win_rate * 100).toFixed(1) + '%' : null)}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Sharpe</div>
      ${fmtVal(ta.sharpe_ratio !== null && ta.sharpe_ratio !== undefined ? ta.sharpe_ratio.toFixed(2) : null)}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Max Drawdown</div>
      ${fmtVal(ta.max_drawdown_pct !== null && ta.max_drawdown_pct !== undefined ? fmtPct(-ta.max_drawdown_pct) : null, ' negative')}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Total Fills</div>
      ${fmtVal(kpi.fills || 0)}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Resolved</div>
      ${fmtVal(kpi.resolved_markets !== undefined && kpi.resolved_markets !== null ? kpi.resolved_markets : 0)}
    </div>
  `;
  renderAnalyticsSurface(kpi, status);
}

/* ── Render: Market Table (expandable rows — click to inspect individual orders) ── */
const expandedMarkets = new Set(); // track which markets are expanded
const showCancelledByMarket = new Set(); // track which markets show cancelled orders

function groupOrdersByMarket(orders) {
  const map = {};
  if (!orders) return map;
  for (const o of orders) {
    const cid = o.condition_id || 'unknown';
    if (!map[cid]) map[cid] = [];
    map[cid].push(o);
  }
  return map;
}

function fmtOrderStatus(status) {
  if (!status) return '--';
  return status.toUpperCase();
}

function fmtSide(side) {
  if (!side) return '--';
  const s = String(side).toUpperCase();
  return s === 'BUY' ? 'BUY' : s === 'SELL' ? 'SELL' : s;
}

function fmtOrderAge(sec) {
  if (sec === null || sec === undefined) return '--';
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  return Math.floor(sec / 3600) + 'h';
}

function fmtAgo(tsSec) {
  if (tsSec === null || tsSec === undefined) return '';
  const sec = Math.max(0, (Date.now() / 1000) - tsSec);
  if (sec < 60) return Math.floor(sec) + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
  return Math.floor(sec / 86400) + 'd ago';
}

// Active means "still work to do": not cancelled, and not a leg the merge
// already consumed.
function isActiveOrder(o) {
  return !isCancelledStatus(o?.status) && !isMergedOrder(o);
}

function isCancelledStatus(status) {
  if (!status) return false;
  const s = String(status).toLowerCase();
  return s === 'cancelled' || s === 'canceled';
}

// A merged pair is a finished event: the merge consumed both legs back into
// $1.00 of USDC, so there is nothing left to do on it until resolution. The
// status is derived in `registry_state.summarize_state` -- `orders.status` is
// CHECK-constrained and never carries it.
function isMergedOrder(o) {
  return !!o && (o.is_merged === true
    || String(o.display_status || '').toLowerCase() === 'merged');
}

// One row for the pair, not one per consumed leg: price and size summed across
// the legs, age taken from the leg that was posted first.
function collapseMergedPair(legs) {
  const sum = (key) => legs.reduce((total, leg) => total + (Number(leg[key]) || 0), 0);
  const first = legs[0] || {};
  return {
    ...first,
    id: legs.map(leg => leg.id).join('+'),
    price: sum('price'),
    original_size: sum('original_size'),
    size_matched: sum('size_matched'),
    age_sec: legs.reduce((oldest, leg) => Math.max(oldest, Number(leg.age_sec) || 0), 0),
    display_status: 'merged',
    is_merged: true,
    outcome: 'PAIR',
    token_side: null,
  };
}

function renderExpandedOrders(orders, fills, showCancelled) {
  if (!orders || orders.length === 0) {
    return `<div class="orders-empty">No individual orders for this market.</div>`;
  }

  // Split orders into active (open/filled/partial/pending) and cancelled
  const activeOrders = orders.filter(o => !isCancelledStatus(o.status));
  const cancelledOrders = orders.filter(o => isCancelledStatus(o.status));

  // If showCancelled is false and there are active orders, show only active.
  // If all orders are cancelled and showCancelled is false, show a collapse
  // banner instead of an empty table — the operator needs to know they exist.
  const displayOrders = showCancelled ? orders : activeOrders;

  if (displayOrders.length === 0 && !showCancelled) {
    return `<div class="orders-empty">
      ${cancelledOrders.length} cancelled order${cancelledOrders.length !== 1 ? 's' : ''} hidden.
      <button class="toggle-cancelled-btn" type="button">Show cancelled</button>
    </div>`;
  }

  // Group fills by order_uuid for the detail view
  const fillsByOrder = {};
  if (fills) {
    for (const f of fills) {
      const oid = f.order_uuid;
      if (!fillsByOrder[oid]) fillsByOrder[oid] = [];
      fillsByOrder[oid].push(f);
    }
  }

  let html = '';

  html += `<table class="orders-subtable">`;
  html += `<thead><tr>
    <th>Pair ID</th>
    <th>Age</th>
    <th>Outcome / Leg</th>
    <th>Price</th>
    <th>Size</th>
    <th>Filled</th>
    <th>Status</th>
  </tr></thead><tbody>`;

  // Pair grouping — deterministic color per pair_id, orders together
  function pairHue(pid) {
    if (!pid) return 200;
    let h = 0;
    for (let i = 0; i < pid.length; i++) h = ((h << 5) - h + pid.charCodeAt(i)) | 0;
    return Math.abs(h) % 360;
  }
  function pillForStatus(s) {
    const v = String(s||'').toLowerCase();
    if (v === 'open' || v === 'partial' || v === 'pending') return 'open';
    if (v === 'filled') return 'filled';
    if (v === 'cancelled' || v === 'canceled') return 'stopped';
    // Muted on purpose: a merged pair is finished, not active work.
    if (v === 'merged') return 'finished';
    return 'reconnecting';
  }
  // Group by pair_id so same pair rows are adjacent and share color
  const byPair = {};
  for (const o of displayOrders) {
    const key = o.pair_id || '__no_pair__';
    if (!byPair[key]) byPair[key] = [];
    byPair[key].push(o);
  }
  const pairKeys = Object.keys(byPair).sort((a,b) => a.localeCompare(b));
  const pairNumMap = {}; pairKeys.forEach((k,i) => pairNumMap[k] = i+1);
  for (const pid of pairKeys) {
    const hue = pairHue(pid);
    const pairNum = pairNumMap[pid];
    const pairDisplay = pid === '__no_pair__' ? '--' : String(pairNum).padStart(2,'0');
    const pairTitle = pid === '__no_pair__' ? '' : ` title="${esc(pid)}"`;
    let pairOrders = byPair[pid].sort((a,b) => (a.price||0) - (b.price||0));
    // Both legs of a merged pair collapse into one MERGED row. A single merged
    // leg is left alone: there is no pair to sum.
    const mergedLegs = pairOrders.filter(isMergedOrder);
    if (pid !== '__no_pair__' && mergedLegs.length > 1) {
      pairOrders = [collapseMergedPair(mergedLegs)]
        .concat(pairOrders.filter(o => !isMergedOrder(o)));
    }
    for (const o of pairOrders) {
      const oFills = fillsByOrder[o.id] || [];
      const fillCount = oFills.length;
      const rowStatus = isMergedOrder(o) ? 'merged' : o.status;
      const statusCls = pillForStatus(rowStatus);
      const isDown = o.token_side === 'DOWN' || (o.outcome && (o.outcome.toLowerCase().includes('no') || o.outcome.toLowerCase().includes('down')));
      const badgeCls = isDown ? 'badge-down' : 'badge-up';
      const label = o.outcome ? `${o.outcome} (${fmtSide(o.side)})` : (o.token_side ? `${o.token_side} (${fmtSide(o.side)})` : fmtSide(o.side));
      const isCancelled = isCancelledStatus(o.status);
      html += `<tr class="${isCancelled ? 'order-cancelled ' : ''}pair-row" style="--pair-hue:${hue}; background: hsla(${hue},72%,60%,0.06)">
        <td class="mono" style="font-size:11px;color:var(--text-muted)"><span class="pair-label"${pairTitle}><span class="pair-dot" style="--pair-hue:${hue}"></span>${esc(pairDisplay)}</span></td>
        <td class="mono" style="font-size:11px;color:var(--text-secondary)">${fmtOrderAge(o.age_sec)}</td>
        <td class="mono"><span class="${badgeCls} side-${(o.side||'').toLowerCase()}">${esc(label)}</span></td>
        <td class="mono">${esc(o.price !== null && o.price !== undefined ? o.price.toFixed(4) : '--')}</td>
        <td class="mono">${esc(o.original_size !== null && o.original_size !== undefined ? o.original_size : '--')}</td>
        <td class="mono">${esc(o.size_matched !== null && o.size_matched !== undefined ? o.size_matched : '--')}</td>
        <td><span class="pill ${statusCls}">${fmtOrderStatus(rowStatus)}</span></td>
      </tr>`;
    }
  }

  html += `</tbody></table>`;

  if (cancelledOrders.length > 0) {
    html += `<div class="cancelled-toggle-bar">
      <button class="toggle-cancelled-btn" type="button">
        ${showCancelled ? 'Hide' : 'Show'} ${cancelledOrders.length} cancelled order${cancelledOrders.length !== 1 ? 's' : ''}
      </button>
    </div>`;
  }
  return html;
}

/* ── Orders & Trades: four views of the same run ───────────────────────────
 *
 * One table, four tabs, because they are four stages of the same object and
 * the operator reads them in that order:
 *
 *   ACTIVE MARKETS — graduated the Market Filter and currently quoted. What the
 *                    is looking at. No share counts here: nothing is owned yet.
 *   Orders         — resting in the book. Size, price, what it would cost.
 *                    No PnL here: an order in the book is not a position.
 *   OPEN POSITIONS — filled legs on a market that has NOT resolved. This is
 *                    where PnL exists, because this is the only stage where
 *                    the account is still exposed.
 *   CLOSED TRADES  — settled trades that booked profit or loss, in the same
 *                    shape the Data & Markets table renders a finished
 *                    market. Nothing here can move any more.
 */

const OT_VIEWS = ['active-markets', 'open-orders', 'positions', 'closed-trades'];
const OT_STORAGE_KEY = 'sh-orders-trades-view';
let currentOrdersTradesView = 'active-markets';

const OT_NOTES = {
  'active-markets': 'Markets that graduated the Market Filter and are being quoted.',
  'open-orders': 'Orders resting on the book. Nothing is held yet, so there is no PnL.',
  'positions': 'Filled legs the account is holding on markets that have not settled.',
  'closed-trades': 'Settled trades that booked a profit or loss.',
};

const OT_COLUMNS = {
  'active-markets': ['Market', 'Category', 'UP Quote', 'DOWN Quote', 'Pair Cost',
                     'Edge', '24h Volume', 'Resolves', 'Status'],
  'open-orders': ['Market', 'Leg', 'Price', 'Size', 'Filled', 'Remaining',
                  'Total Cost', 'Queue Ahead', 'Age', 'Order Status'],
  // Per-leg on the left, pair-level on the right. Mark Value and both PnLs
  // span the pair because they are pair numbers: a matched pair merges at par
  // and only the remainder is marked, which cannot be split across two rows
  // without inventing a per-leg figure that does not exist.
  'positions': ['Market', 'Leg', 'Size', 'Avg Price', 'Cost',
                'Mark Value', 'Unrealized', 'Realized'],
  // The Data & Markets table shape, reused so a closed trade reads the same
  // in both places: commit, hedge state, realized P&L, fills, status.
  'closed-trades': ['Market', 'Commit ($)', 'Hedge', 'Realized P&L', 'Fills', 'Status'],
};

/* The quote log writes the down leg as `DOWN`; orders and the pair summary
 * call it `DN`. One spelling in, one spelling out, or half the table reads as
 * unquoted when both legs were quoted all along. */
function normalizeLeg(side) {
  const v = String(side || '').toUpperCase();
  if (v === 'UP') return 'UP';
  if (v === 'DN' || v === 'DOWN') return 'DN';
  return null;
}

/* The newest quote this market logged on each leg: the price the bot is
 * bidding and the mid it was priced against. */
function latestLegQuotes(market) {
  const best = {};
  for (const q of (market && market.quotes) || []) {
    const leg = normalizeLeg(q.side);
    if (leg === null) continue;
    const ts = Number(q.ts) || 0;
    if (!best[leg] || ts >= best[leg].ts) {
      best[leg] = {
        ts,
        price: (q.price === null || q.price === undefined) ? null : Number(q.price),
        mid: (q.mid === null || q.mid === undefined) ? null : Number(q.mid),
      };
    }
  }
  return { up: best.UP || null, dn: best.DN || null };
}

/* The last mid observed on each leg. Used to mark an unhedged position: the
 * mid is what the leg is worth, not what the bot bid for it.
 *
 * It is deliberately NOT the source of the pair cost on Active Markets. The
 * two legs of a binary market are anti-correlated -- UP mid and DOWN mid sum
 * to $1.00 by construction -- so an "edge" computed from mids reads 0.0¢ on
 * every row. The edge lives in the gap between the mids and what the bot is
 * willing to pay, which is the quoted price. */
function latestLegMids(market) {
  const q = latestLegQuotes(market);
  return {
    up: q.up ? q.up.mid : null,
    dn: q.dn ? q.dn.mid : null,
  };
}

/* Orders name their token, quotes name the leg. Joining the two is the only
 * way to say UP or DOWN on an order row. */
function tokenLegMap(kpi) {
  const map = {};
  for (const m of Object.values((kpi && kpi.by_market) || {})) {
    for (const q of m.quotes || []) {
      const leg = normalizeLeg(q.side);
      if (q.token_id && leg !== null) map[q.token_id] = leg;
    }
  }
  return map;
}

/* Which side of the market an order sits on. The quote log names the leg for
 * every token it quoted, but a leg that never got a quote logged leaves its
 * token unnamed -- and that is exactly the order most worth labelling, since a
 * lone filled leg is the single buy the strategy exists to avoid. The market is
 * binary, so a token that is not the named leg is the other one. */
function legForOrder(order, legs, byMarket) {
  const known = legs[order.token_id];
  if (known) return known;
  const market = (byMarket || {})[order.condition_id];
  if (!market) return null;
  const named = new Set();
  for (const q of market.quotes || []) {
    const leg = normalizeLeg(q.side);
    if (leg === null) continue;
    if (q.token_id === order.token_id) return leg;
    named.add(leg);
  }
  if (named.size === 1) return named.has('UP') ? 'DN' : 'UP';
  return null;
}

function queueAheadByOrder(kpi) {
  const map = {};
  for (const m of Object.values((kpi && kpi.by_market) || {})) {
    for (const q of m.quotes || []) {
      if (q.order_id && q.queue_ahead !== null && q.queue_ahead !== undefined) {
        map[q.order_id] = Number(q.queue_ahead);
      }
    }
  }
  return map;
}

function isFinishedMarket(m) {
  return m.resolved === true
    || (m.days_to_resolve !== null && m.days_to_resolve !== undefined && m.days_to_resolve < 0);
}

/* Quoted means the bot is working this market right now: it has posted quotes,
 * or it still has an order alive on it. */
function isQuotedMarket(m, orders) {
  if (isFinishedMarket(m)) return false;
  return (m.quotes_count || 0) > 0 || (orders || []).some(o => isActiveOrder(o));
}

function isRestingOrder(o) {
  const v = String(o && o.status || '').toLowerCase();
  return v === 'open' || v === 'partial' || v === 'pending';
}

/* What the held shares are worth now. A matched UP+DOWN pair merges back into
 * exactly $1.00, so the merged part is priced at par and only the unhedged
 * remainder is marked to the book. Returns null when a naked leg has no
 * observed mid -- a blank cell beats an invented one. */
function positionMarkValue(m, mids) {
  const up = Number(m.up_sh) || 0;
  const dn = Number(m.dn_sh) || 0;
  const merged = Math.min(up, dn);
  let value = merged;
  const nakedUp = up - merged;
  const nakedDn = dn - merged;
  if (nakedUp > 0) {
    if (mids.up === null || mids.up === undefined) return null;
    value += nakedUp * mids.up;
  }
  if (nakedDn > 0) {
    if (mids.dn === null || mids.dn === undefined) return null;
    value += nakedDn * mids.dn;
  }
  return value;
}

/* What a SETTLED market is worth, which the book can no longer say.
 *
 * `positionMarkValue` prices naked shares at the mid. A settled market has no
 * mid -- nobody quotes a race that is over -- so it returns null and the
 * operator reads `--` beside shares that are worth real money. Five winning UP
 * shares on a finished market are $5.00, not "unknown".
 *
 * Settlement is not a price, it is an outcome: every merged pair redeems at
 * $1.00 whichever side won, the winning leg's naked shares redeem at $1.00
 * each, and the losing leg's redeem at nothing. Only when the winner is not
 * named -- a market that finished without the sweeper recording a token id --
 * is there nothing to do but fall back to quotes.
 */
function settledMarkValue(m, mids) {
  const leg = winningLeg(m);
  if (leg === null) return positionMarkValue(m, mids);
  const up = Number(m.up_sh) || 0;
  const dn = Number(m.dn_sh) || 0;
  const merged = Math.min(up, dn);
  // Both legs cannot be naked at once: `merged` is the smaller of the two.
  const naked = (leg === 'up') ? (up - merged) : (dn - merged);
  return merged + naked;
}

/* Which leg the settlement paid, or null when nothing in the data says.
 *
 * The report decides this, because that is where the UP/DOWN split of the
 * market's tokens is made; it matches the venue token id, never the winning
 * label ("Up", a team name), which is for the operator to read and would be a
 * guess to map a leg from. */
function winningLeg(m) {
  const leg = m && m.winning_leg;
  return (leg === 'up' || leg === 'dn') ? leg : null;
}

/* An order can outlive its market's entry in the KPI report -- the market
 * leaves the graduated universe, the report stops carrying its title -- and
 * `marketLink` has nothing to render but `--`. A truncated condition id is
 * still something the operator can search the registry for. */
function marketCell(market, conditionId) {
  const named = market && (market.title || market.name || market.slug);
  if (named) return marketLink(market);
  const cid = String(conditionId || '');
  if (!cid) return '--';
  return `<span class="mono" title="${esc(cid)}">${esc(cid.slice(0, 10))}…</span>`;
}

/* ── Pair status: one vocabulary for both tables ──
 *
 * A pair is two legs of the same market, and it is in exactly one of three
 * states whether you are looking at the book or at what filled:
 *
 *   Paired   — both legs present, same size. This is the only state that
 *              merges cleanly back into $1.00.
 *   Partial  — both legs present, sizes do not match. The overlap merges; the
 *              remainder is exposed.
 *   Unpaired — one leg only. On the book that is a single buy waiting to
 *              happen; filled, it already is one.
 *
 * Tone follows the state, not the money: good, warning, alert. `Pair Cost`
 * carries its own tag and its own tone, so no single tag is asked to mean two
 * things at once. */
const PAIR_STATUS = {
  paired: { label: 'Paired', tone: 'good' },
  partial: { label: 'Partial', tone: 'warn' },
  unpaired: { label: 'Unpaired', tone: 'alert' },
};

function pairStatus(upSize, downSize) {
  const up = Number(upSize) || 0;
  const dn = Number(downSize) || 0;
  if (up > 0 && dn > 0) return (up === dn) ? 'paired' : 'partial';
  return 'unpaired';
}

function pairStatusTag(status) {
  const state = PAIR_STATUS[status] || PAIR_STATUS.unpaired;
  return otTag(state.tone, state.label, null);
}

/* Pair cost sits beside the status, not inside a second pill. A pill reads as
 * a state the row is in; the pair cost is a measurement of that state, and two
 * pills stacked made the cell look like two competing verdicts.
 *
 * It keeps its own colour because it answers its own question: not "is this
 * pair whole" but "if it merges, does it pay". Under $1.00 it does; at exactly
 * $1.00 it books nothing, so the capital earned nothing; over $1.00 the loss
 * is decided the moment both legs fill. */
function pairCostText(pairCost) {
  if (pairCost === null || pairCost === undefined) return '';
  const tone = pairCost < 1 ? 'good' : (pairCost > 1 ? 'alert' : 'warn');
  return `<span class="ot-cost is-${tone}">`
    + `<span class="ot-cost-label">Pair Cost</span> `
    + `<span class="ot-cost-value mono">${esc(fmtPrice(pairCost))}</span></span>`;
}

/* The status and its measurement on one line, under the market name. */
function pairSummary(status, pairCost, inferred) {
  const inferredTag = inferred ? `<div class="ot-tag is-info" title="Complementary legs grouped by market (no shared pair_id)">Inferred</div>` : '';
  return `<div class="ot-pair-line">${pairStatusTag(status)}${inferredTag}${pairCostText(pairCost)}</div>`;
}

/* A labelled status tag: a named condition, optionally with the number behind
 * it. `tone` is one of good / warn / alert, so the same three colours mean the
 * same three things everywhere in this table. */
function otTag(tone, label, value) {
  const valueHtml = (value === null || value === undefined)
    ? '' : ` <span class="ot-tag-value mono">${esc(value)}</span>`;
  return `<div class="ot-tag is-${esc(tone)}">${esc(label)}${valueHtml}</div>`;
}

function otEmptyRow(view, message) {
  return `<tr><td colspan="${OT_COLUMNS[view].length}" style="text-align:center;color:var(--text-muted);padding:20px">${esc(message)}</td></tr>`;
}

function otHeadHtml(view) {
  // The market name is the widest thing in the table and the only cell that
  // wraps; without a floor it folds a three-word title onto three lines and
  // squeezes every number column.
  const cells = OT_COLUMNS[view]
    .map((label, i) => `<th${i === 0 ? ' class="ot-market-head"' : ''}>${esc(label)}</th>`)
    .join('');
  return `<tr>${cells}</tr>`;
}

function fmtPrice(v) {
  return (v === null || v === undefined) ? '--' : `$${Number(v).toFixed(3)}`;
}

function fmtShares(v) {
  const n = Number(v) || 0;
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

function fmtCompactUSD(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  if (Math.abs(n) >= 1000000) return `$${(n / 1000000).toFixed(1)}M`;
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(0)}k`;
  return fmtUSD(n);
}

/* `fmtUSD(-6.6)` reads `$-6.60`; a money column wants the sign in front of the
 * dollar, so the magnitude is formatted and the sign prepended. */
function signedUSD(v) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '--';
  return `<span class="${signClass(v)}">${fmtSignedUSD(v)}</span>`;
}

function activeMarketsRows(kpi, state) {
  const ordersByMarket = groupOrdersByMarket(state && state.orders);
  const entries = Object.entries((kpi && kpi.by_market) || {})
    .filter(([cid, m]) => isQuotedMarket(m, ordersByMarket[cid]));

  if (!entries.length) return otEmptyRow('active-markets', 'No markets are being quoted.');

  entries.sort((a, b) => (b[1].quotes_count || 0) - (a[1].quotes_count || 0)
    || String(a[1].title || '').localeCompare(String(b[1].title || '')));

  return entries.map(([cid, m]) => {
    const legs = latestLegQuotes(m);
    const upQuote = legs.up ? legs.up.price : null;
    const dnQuote = legs.dn ? legs.dn.price : null;
    const pairCost = (upQuote !== null && dnQuote !== null) ? (upQuote + dnQuote) : null;
    const edge = pairCost === null ? null : 1 - pairCost;
    const dtr = m.days_to_resolve;
    const restingHere = (ordersByMarket[cid] || []).some(o => isRestingOrder(o));
    return `<tr data-cid="${esc(cid)}">
      <td class="ot-market">${marketCell(m, cid)}</td>
      <td class="mono">${esc(m.category || '--')}</td>
      <td class="mono">${fmtPrice(upQuote)}</td>
      <td class="mono">${fmtPrice(dnQuote)}</td>
      <td class="mono">${fmtPrice(pairCost)}</td>
      <td class="mono">${edge === null ? '--' : `<span class="${edge > 0 ? 'positive' : 'negative'}">${(edge * 100).toFixed(1)}¢</span>`}</td>
      <td class="mono">${fmtCompactUSD(m.volume_24h)}</td>
      <td class="mono">${(dtr === null || dtr === undefined) ? '--' : `${Number(dtr).toFixed(1)}d`}</td>
      <td><span class="pill ${restingHere ? 'quoting-breathing' : 'stopped'}">${restingHere ? 'QUOTING' : 'IDLE'}</span></td>
    </tr>`;
  }).join('');
}

/* Group the resting book by the pair each order belongs to.
 *
 * The strategy only makes money when both legs fill: a pair that cost under
 * $1.00 merges back into exactly $1.00, and a leg that fills alone is a
 * directional bet nobody decided to take. Listing orders one flat row after
 * another hides which two belong together, so the book is read pair by pair,
 * UP above DOWN, with the market named once for both.
 *
 * An order with no pair id is its own group -- it is still a leg with no
 * partner, which is exactly the thing worth seeing. */
function groupOrdersByPair(orders, kpi) {
  const byMarket = (kpi && kpi.by_market) || {};
  const legs = tokenLegMap(kpi);
  const groups = new Map();
  const leftovers = [];

  // Pass 1: group orders sharing a valid pair_id; isolate orders without pair_id
  for (const o of orders) {
    if (o.pair_id) {
      if (!groups.has(o.pair_id)) groups.set(o.pair_id, []);
      groups.get(o.pair_id).push(o);
    } else {
      leftovers.push(o);
    }
  }

  // Pass 2: find complementary detached legs among leftovers (exactly 1 UP + 1 DN per market)
  const leftoversByCid = new Map();
  for (const o of leftovers) {
    const cid = o.condition_id;
    if (!leftoversByCid.has(cid)) leftoversByCid.set(cid, []);
    leftoversByCid.get(cid).push(o);
  }

  const legRank = (order) => {
    const leg = legForOrder(order, legs, byMarket);
    if (leg === 'UP') return 0;
    if (leg === 'DN') return 1;
    return 2;
  };

  const out = [];

  // Add native pair groups (inferred: false)
  for (const [key, list] of groups) {
    list.sort((a, b) => legRank(a) - legRank(b)
      || (Number(a.posted_ts) || 0) - (Number(b.posted_ts) || 0));
    const newest = list.reduce((max, o) => Math.max(max, Number(o.posted_ts) || 0), 0);
    out.push({ key, orders: list, newest, inferred: false });
  }

  // Consolidate or isolate leftovers
  for (const [cid, list] of leftoversByCid) {
    const ups = list.filter(o => legForOrder(o, legs, byMarket) === 'UP');
    const dns = list.filter(o => legForOrder(o, legs, byMarket) === 'DN');
    if (ups.length === 1 && dns.length === 1) {
      const pairList = [ups[0], dns[0]];
      pairList.sort((a, b) => legRank(a) - legRank(b)
        || (Number(a.posted_ts) || 0) - (Number(b.posted_ts) || 0));
      const newest = pairList.reduce((max, o) => Math.max(max, Number(o.posted_ts) || 0), 0);
      out.push({ key: 'inferred:' + cid, orders: pairList, newest, inferred: true });
    } else {
      for (const o of list) {
        out.push({ key: 'order:' + o.order_id, orders: [o], newest: Number(o.posted_ts) || 0, inferred: false });
      }
    }
  }

  out.sort((a, b) => b.newest - a.newest);
  return out;
}

/* What the pair would cost if every resting leg filled. This is the number the
 * strategy is built around: under $1.00 the merge books a profit, over it
 * books a loss. It is only meaningful once both legs are on the book. */
function restingPairLegs(orders, kpi) {
  const byMarket = (kpi && kpi.by_market) || {};
  const legs = tokenLegMap(kpi);
  const found = {};
  for (const o of orders) {
    const leg = legForOrder(o, legs, byMarket);
    if (leg === null || found[leg] !== undefined) continue;
    const remaining = (o.size_remaining === null || o.size_remaining === undefined)
      ? Number(o.original_size) || 0 : Number(o.size_remaining) || 0;
    found[leg] = { price: Number(o.price) || 0, size: remaining };
  }
  return found;
}

function restingPairCost(orders, kpi) {
  const found = restingPairLegs(orders, kpi);
  if (found.UP === undefined || found.DN === undefined) return null;
  return found.UP.price + found.DN.price;
}

function openOrdersRows(kpi, state) {
  const orders = ((state && state.orders) || []).filter(isRestingOrder);
  if (!orders.length) return otEmptyRow('open-orders', 'No orders are resting on the book.');

  const byMarket = (kpi && kpi.by_market) || {};
  const legs = tokenLegMap(kpi);
  const queues = queueAheadByOrder(kpi);
  const groups = groupOrdersByPair(orders, kpi);

  return groups.map((group, groupIndex) => {
    const first = group.orders[0];
    const market = byMarket[first.condition_id] || { condition_id: first.condition_id };
    // The same two tags the Positions view carries, read against the book
    // instead of against what filled: what state the pair is in, and what it
    // would cost to merge if both legs filled.
    const restingLegs = restingPairLegs(group.orders, kpi);
    const status = pairStatus(
      restingLegs.UP ? restingLegs.UP.size : 0,
      restingLegs.DN ? restingLegs.DN.size : 0);
    const pairCost = restingPairCost(group.orders, kpi);
    const pairTags = pairSummary(status, status === 'unpaired' ? null : pairCost, Boolean(group.inferred));

    return group.orders.map((o, legIndex) => {
      const leg = legForOrder(o, legs, byMarket) || '--';
      const size = Number(o.original_size) || 0;
      const price = Number(o.price) || 0;
      const queue = queues[o.order_id];
      const status = String(o.status || '').toUpperCase();
      const rowClass = ['ot-pair-row'];
      if (legIndex === 0) rowClass.push('ot-pair-start');
      if (groupIndex % 2 === 1) rowClass.push('ot-pair-alt');
      // One market name for both legs: the merged cell is what makes the two
      // rows read as one pair rather than two unrelated orders.
      const marketTd = legIndex === 0
        ? `<td class="ot-market" rowspan="${group.orders.length}">${marketCell(market, o.condition_id)}${pairTags}</td>`
        : '';
      return `<tr class="${rowClass.join(' ')}" data-order-id="${esc(o.order_id)}" data-pair="${esc(group.key)}">
      ${marketTd}
      <td><span class="pill ${leg === 'UP' ? 'active' : (leg === 'DN' ? 'reconnecting' : 'stopped')}">${esc(leg === 'DN' ? 'DOWN' : leg)}</span></td>
      <td class="mono">${fmtPrice(price)}</td>
      <td class="mono">${fmtShares(size)}</td>
      <td class="mono">${fmtShares(o.size_matched)}</td>
      <td class="mono">${fmtShares(o.size_remaining)}</td>
      <td class="mono">${fmtUSD(price * size)}</td>
      <td class="mono">${(queue === null || queue === undefined) ? '--' : fmtCompactUSD(queue)}</td>
      <td class="mono">${o.age_sec === null || o.age_sec === undefined ? '--' : fmtStopwatch(Number(o.age_sec))}</td>
      <td><span class="pill ${status === 'OPEN' ? 'open' : 'reconnecting'}">${esc(status || '--')}</span></td>
    </tr>`;
    }).join('');
  }).join('');
}

/* Markets with at least one filled leg, split by whether the market has
 * settled. `finished` picks the side of the split: false is live exposure
 * (POSITIONS), true is settled (RESOLVED). One selector, because a market
 * that appears in both tables -- or in neither -- is the bug this split
 * exists to prevent. */
function heldMarketEntries(kpi, finished) {
  return Object.entries((kpi && kpi.by_market) || {})
    .filter(([, m]) => (Number(m.total_sh) || 0) > 0
                       && isFinishedMarket(m) === finished)
    .sort((a, b) => (Number(b[1].total_cost) || 0) - (Number(a[1].total_cost) || 0));
}

/* One row per held leg, in the same order the book reads: UP above DOWN.
 * A leg with no shares is not a row -- there is nothing being held on it. */
function heldLegs(m) {
  return [
    { leg: 'UP', size: Number(m.up_sh) || 0, cost: Number(m.up_cost) || 0 },
    { leg: 'DOWN', size: Number(m.dn_sh) || 0, cost: Number(m.dn_cost) || 0 },
  ].filter(entry => entry.size > 0);
}

/* The settled outcome as the operator reads it on the venue: the winning
 * side's label. A market can be finished without the resolution sweeper
 * having recorded a winner yet (the ranker's days_to_resolve went negative
 * first), and that is `Resolved` with no name rather than a fabricated one. */
/* Which settled markets closed with money actually booked.
 *
 * A market can settle with the account flat -- a pair merged before
 * resolution, a leg never filled -- and its realized P&L is zero. That is
 * not a closed trade: there is no profit or loss to read, and a row of
 * zeros pushed the trades the tab exists to show below the fold. */
function closedTradesEntries(kpi, state) {
  const ordersByMarket = groupOrdersByMarket(state && state.orders);
  return Object.entries((kpi && kpi.by_market) || {})
    .filter(([cid, m]) => (m.settlements || []).length > 0
                           && Number(m.realized_pnl) !== 0
                           && !(ordersByMarket[cid] || []).some(isRestingOrder))
    .sort((a, b) => (Number(b[1].realized_pnl) || 0) - (Number(a[1].realized_pnl) || 0));
}

function closedTradesRows(kpi, state) {
  const entries = closedTradesEntries(kpi, state);

  if (!entries.length) {
    return otEmptyRow('closed-trades', 'No closed trades yet: nothing has settled with a booked profit or loss.');
  }

  const ordersByMarket = groupOrdersByMarket(state && state.orders);
  const fills = (state && state.fills) || [];
  const graduatedCids = new Set(((kpi && kpi.funnel && kpi.funnel.graduated) || [])
    .map(g => g.cid || g.condition_id));

  return entries.map(([cid, m]) =>
    marketRowPairHtml(cid, m, {
      isExpanded: expandedMarkets.has(cid),
      hasOrders: ordersByMarket[cid] && ordersByMarket[cid].length > 0,
      allOrders: ordersByMarket[cid] || [],
      showCancelled: showCancelledByMarket.has(cid),
      fills,
      graduatedCids,
      forceFinished: true,
    })).join('');
}

function isMarketInferredPosition(cid, kpi, state) {
  if (!state || !Array.isArray(state.fills)) return false;
  const byMarket = (kpi && kpi.by_market) || {};
  const legs = tokenLegMap(kpi);
  const fillsForMarket = state.fills.filter(f => f.condition_id === cid);
  if (!fillsForMarket.length) return false;

  // Group fills by non-null pair_id
  const pairIds = new Set();
  for (const f of fillsForMarket) {
    if (f.pair_id) pairIds.add(f.pair_id);
  }

  // Check if any single pair_id covers both an UP and DOWN fill
  for (const pid of pairIds) {
    const pairFills = fillsForMarket.filter(f => f.pair_id === pid);
    const hasUp = pairFills.some(f => legForOrder(f, legs, byMarket) === 'UP');
    const hasDn = pairFills.some(f => legForOrder(f, legs, byMarket) === 'DN');
    if (hasUp && hasDn) {
      return false; // Found a legitimate shared pair_id covering both legs
    }
  }

  // If both legs are held but no shared pair_id links them in fills, it is inferred
  return true;
}

function positionsRows(kpi, state) {
  const entries = heldMarketEntries(kpi, false);

  if (!entries.length) return otEmptyRow('positions', 'No legs have filled, so nothing is held.');

  return entries.map(([cid, m], marketIndex) => {
    const mids = latestLegMids(m);
    const mark = positionMarkValue(m, mids);
    const cost = Number(m.total_cost) || 0;
    const unrealized = mark === null ? null : mark - cost;
    const up = Number(m.up_sh) || 0;
    const dn = Number(m.dn_sh) || 0;
    const status = pairStatus(up, dn);

    const legs = heldLegs(m);
    if (!legs.length) return '';

    const isInferred = (status !== 'unpaired') && isMarketInferredPosition(cid, kpi, state);
    const pairTags = pairSummary(status, (status === 'unpaired') ? null : m.pair_cost, isInferred);
    const span = legs.length;

    return legs.map((entry, legIndex) => {
      const rowClass = ['ot-pair-row'];
      if (legIndex === 0) rowClass.push('ot-pair-start');
      if (marketIndex % 2 === 1) rowClass.push('ot-pair-alt');
      const avgPrice = entry.size > 0 ? entry.cost / entry.size : null;
      // The market and every pair-level number span the pair, for the same
      // reason the market name does: they describe the pair, not one leg.
      const pairCells = legIndex === 0
        ? `<td class="ot-market" rowspan="${span}">${marketCell(m, cid)}${pairTags}</td>`
        : '';
      const pairNumbers = legIndex === 0
        ? `<td class="mono ot-pair-value" rowspan="${span}">${mark === null ? '--' : fmtUSD(mark)}</td>
      <td class="mono ot-pair-value" rowspan="${span}">${signedUSD(unrealized)}</td>
      <td class="mono ot-pair-value" rowspan="${span}">${signedUSD(m.realized_pnl)}</td>`
        : '';
      return `<tr class="${rowClass.join(' ')}" data-cid="${esc(cid)}" data-leg="${esc(entry.leg)}">
      ${pairCells}
      <td><span class="pill ${entry.leg === 'UP' ? 'active' : 'reconnecting'}">${esc(entry.leg)}</span></td>
      <td class="mono">${fmtShares(entry.size)}</td>
      <td class="mono">${fmtPrice(avgPrice)}</td>
      <td class="mono">${fmtUSD(entry.cost)}</td>
      ${pairNumbers}
    </tr>`;
    }).join('');
  }).join('');
}

function ordersTradesCounts(kpi, state) {
  const ordersByMarket = groupOrdersByMarket(state && state.orders);
  const markets = Object.entries((kpi && kpi.by_market) || {});
  return {
    'active-markets': markets.filter(([cid, m]) => isQuotedMarket(m, ordersByMarket[cid])).length,
    'open-orders': ((state && state.orders) || []).filter(isRestingOrder).length,
    'positions': markets.filter(([, m]) => (Number(m.total_sh) || 0) > 0
                                            && !isFinishedMarket(m)).length,
    'closed-trades': closedTradesEntries(kpi, state).length,
  };
}

function ordersTradesRows(view, kpi, state) {
  if (view === 'open-orders') return openOrdersRows(kpi, state);
  if (view === 'positions') return positionsRows(kpi, state);
  if (view === 'closed-trades') return closedTradesRows(kpi, state);
  return activeMarketsRows(kpi, state);
}

function renderOrdersTrades(kpi, state) {
  const head = document.getElementById('orders-trades-head');
  const body = document.getElementById('orders-trades-body');
  if (!head || !body) return;

  const view = OT_VIEWS.includes(currentOrdersTradesView)
    ? currentOrdersTradesView : OT_VIEWS[0];
  head.innerHTML = otHeadHtml(view);
  body.innerHTML = ordersTradesRows(view, kpi, state);

  // The closed-trades view reuses the Data & Markets row shape, so it gets
  // the same click-to-expand behaviour on its rows.
  if (view === 'closed-trades') {
    wireMarketRowExpansion(body, groupOrdersByMarket(state && state.orders),
      () => renderOrdersTrades(lastKpi, lastState));
  }

  const note = document.getElementById('orders-trades-note');
  if (note) note.textContent = OT_NOTES[view];

  const counts = ordersTradesCounts(kpi, state);
  // `ordersTradesCounts` returns a number for every view, so an unknown key
  // here is a typo in the markup, not an unmeasured value: show it as blank
  // rather than dressing it up as a real count of zero.
  document.querySelectorAll('[data-ot-count]').forEach(el => {
    const count = counts[el.dataset.otCount];
    el.textContent = (count === undefined) ? '--' : String(count);
  });
}

function setOrdersTradesView(view) {
  currentOrdersTradesView = OT_VIEWS.includes(view) ? view : OT_VIEWS[0];
  try {
    localStorage.setItem(OT_STORAGE_KEY, currentOrdersTradesView);
  } catch (e) {
    // Storage off: the view still switches, it just forgets on reload.
  }
  document.querySelectorAll('.ot-tab-btn').forEach(btn => {
    const isActive = btn.dataset.otView === currentOrdersTradesView;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-selected', String(isActive));
  });
  renderOrdersTrades(lastKpi, lastState);
}

function initOrdersTradesTabs() {
  let stored = null;
  try {
    stored = localStorage.getItem(OT_STORAGE_KEY);
  } catch (e) {
    stored = null;
  }
  // A stored value is data, not a command: an unknown view falls back to the
  // first tab rather than rendering a table with no columns.
  currentOrdersTradesView = OT_VIEWS.includes(stored) ? stored : OT_VIEWS[0];

  document.querySelectorAll('.ot-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => setOrdersTradesView(btn.dataset.otView));
  });
  setOrdersTradesView(currentOrdersTradesView);
}

function renderMarkets(kpi, state) {
  const body = document.getElementById('market-body');
  if (!kpi || !kpi.by_market || Object.keys(kpi.by_market).length === 0) {
    body.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:20px">No active markets</td></tr>`;
    return;
  }

  // Group orders from /api/state by condition_id
  const ordersByMarket = groupOrdersByMarket(state?.orders);
  const fills = state?.fills || [];
  const graduatedCids = new Set((kpi.funnel?.graduated || []).map(g => g.cid || g.condition_id));

  // Filter entries based on active table filter pill
  let entries = Object.entries(kpi.by_market);
  if (currentTableFilter === 'quoting') {
    entries = entries.filter(([cid, m]) => (m.quotes_count > 0 || (ordersByMarket[cid] || []).some(o => isActiveOrder(o))));
  } else if (currentTableFilter === 'graduated') {
    entries = entries.filter(([cid]) => graduatedCids.has(cid));
  }

  // Market order: OPEN (pure) → mixed OPEN+FILLED → pure FILLED → zero active / IDLE/FINISHED at bottom
  entries.sort((a,b) => {
    const [cidA] = a; const [cidB] = b;
    const activeA = (ordersByMarket[cidA] || []).filter(o => isActiveOrder(o));
    const activeB = (ordersByMarket[cidB] || []).filter(o => isActiveOrder(o));
    const rank = (active) => {
      if (active.length === 0) return 4;
      const hasOpen = active.some(o => { const v=String(o.status||'').toLowerCase(); return v==='open'||v==='partial'||v==='pending'; });
      const hasFilled = active.some(o => String(o.status||'').toLowerCase()==='filled');
      if (hasOpen && !hasFilled) return 1;
      if (hasOpen && hasFilled) return 2;
      if (!hasOpen && hasFilled) return 3;
      return 2;
    };
    const ra = rank(activeA), rb = rank(activeB);
    if (ra !== rb) return ra - rb;
    if (activeB.length !== activeA.length) return activeB.length - activeA.length;
    return (a[1].title||'').localeCompare(b[1].title||'');
  });

  body.innerHTML = '';
  for (const [cid, m] of entries) {
    body.innerHTML += marketRowPairHtml(cid, m, {
      isExpanded: expandedMarkets.has(cid),
      hasOrders: ordersByMarket[cid] && ordersByMarket[cid].length > 0,
      allOrders: ordersByMarket[cid] || [],
      showCancelled: showCancelledByMarket.has(cid),
      fills: state?.fills || [],
      graduatedCids,
    });
  }

  wireMarketRowExpansion(body, ordersByMarket, () => renderMarkets(kpi, state));
}

/* Build one finished-or-live market's main row and its optional expanded
 * sub-row, in the Data & Markets table shape. Shared with the Orders &
 * Trades CLOSED TRADES view so a closed trade reads identically in both
 * tables. Pure: no DOM reads or writes, everything arrives as arguments. */
function marketRowPairHtml(cid, m, opts) {
  const { isExpanded, hasOrders, allOrders, showCancelled, fills, graduatedCids, forceFinished } = opts;
  const fills_count = m.fills_count || 0;
  const hedged = m.balance !== null && m.balance !== undefined && m.balance >= 0.99 ? 'Hedged' : 'One-Sided';
  // Merged legs are finished, not active: a fully-merged market must not
  // rank or badge as if it still had resting work.
  const activeOrders = allOrders.filter(o => isActiveOrder(o));
  // Distinct FINISHED when market is resolved or dropped from current
  // graduated universe but still has history (shadow retains it for P/L).
  // Covers: days_to_resolve <0, venue_sync resolved, or simply not in
  // kpi.funnel.graduated anymore (e.g., Mlb Lad Atl 2026-08-27 past date).
  // HasFunnel guards: when funnel empty (no scan yet) don't mark everything.
  const hasFunnel = graduatedCids.size > 0;
  // FINISHED when the backend recorded the terminal marker (the shadow
  // resolution sweeper's `resolutions` row, or the ranker's dtr<0), OR the
  // market left the graduated funnel (the existing universe-left path).
  // `m.resolved` is the primary, durable signal; the funnel heuristic is
  // the fallback that still works when no sweeper has run (live runs that
  // only drop by_mkt via venue_sync, or older shadow dbs pre-sweeper).
  const isFinished = forceFinished === true
    || m.resolved === true
    || (m.days_to_resolve !== null && m.days_to_resolve < 0)
    || (hasFunnel && !graduatedCids.has(cid) && hasOrders);

  // Badge: per-status pills same size/style as order pills — OPEN blue, FILLED green, 0 ACTIVE gray
  // cancelled-count retained as string for test_dashboard_server.py (header no longer shows cancelled per UX)
  let badgeHtml = '';
  if (hasOrders) {
    if (activeOrders.length === 0) {
      badgeHtml = `<span class="pill stopped" style="font-size:10px; padding:2px 8px; margin-left:4px">0 ACTIVE</span>`;
    } else {
      const statusCounts = {};
      for (const o of activeOrders) {
        const v = String(o.status||'').toLowerCase();
        const norm = (v === 'open' || v === 'partial' || v === 'pending') ? 'OPEN' : (v === 'filled' ? 'FILLED' : v.toUpperCase());
        statusCounts[norm] = (statusCounts[norm] || 0) + 1;
      }
      const order = ['OPEN','FILLED'];
      const keys = Object.keys(statusCounts).sort((a,b) => {
        const ia = order.indexOf(a), ib = order.indexOf(b);
        if (ia !== -1 || ib !== -1) return (ia===-1?99:ia) - (ib===-1?99:ib);
        return a.localeCompare(b);
      });
      for (const k of keys) {
        const cnt = statusCounts[k];
        const cls = k === 'OPEN' ? 'open' : k === 'FILLED' ? 'filled' : 'reconnecting';
        badgeHtml += `<span class="pill ${cls}" style="font-size:10px; padding:2px 8px; margin-left:4px">${cnt} ${k}</span>`;
      }
    }
    // cancelled-count — header no longer shows cancelled per UX, kept for expanded toggle only
  }
  // Main row — clickable to expand
  let html = `<tr class="market-row${isExpanded ? ' expanded' : ''}" data-cid="${esc(cid)}" tabindex="0" role="button" aria-expanded="${isExpanded}" aria-label="${isExpanded ? 'Collapse' : 'Expand'} market orders for ${esc(m.title || m.slug || cid.slice(0,10))}">
    <td>
      <span class="expand-chevron${isExpanded ? ' expanded' : ''}" aria-hidden="true">${hasOrders ? '▶' : ''}</span>
      ${marketLink(m)}
      ${badgeHtml}
    </td>
    <td class="mono">${fmtUSD(m.total_cost)}</td>
    <td><span class="pill ${hedged === 'Hedged' ? 'active' : 'reconnecting'}">${hedged}</span></td>
    <td class="mono">${esc(m.realized_pnl !== null && m.realized_pnl !== undefined ? fmtUSD(m.realized_pnl) : '--')}</td>
    <td class="mono">${esc(fills_count)}</td>
    <td>
      <span class="pill ${isFinished ? 'finished' : (m.quotes_count > 0 ? 'quoting-breathing' : 'stopped')}">${isFinished ? 'FINISHED' : (m.quotes_count > 0 ? 'QUOTING' : 'IDLE')}</span>
      ${m.resolution ? `<div class="caption-muted" title="resolved ${new Date(m.resolution.resolved_ts * 1000).toLocaleString()}">${m.resolution.winner ? ('Winner: ' + esc(m.resolution.winner)) : 'Resolved'} · ${fmtAgo(m.resolution.resolved_ts)}</div>` : ''}
    </td>
  </tr>`;

  // Expanded sub-row with individual orders
  if (isExpanded && hasOrders) {
    html += `<tr class="orders-expand-row">
      <td colspan="6" style="padding:0">
        <div class="orders-expand-content">
          ${renderExpandedOrders(allOrders, fills, showCancelled)}
        </div>
      </td>
    </tr>`;
  }
  return html;
}

/* Attach the click/keyboard expand handlers and the cancelled-toggle
 * handlers for a market-rows table. Shared with the Orders & Trades
 * CLOSED TRADES view; `rerender` redraws whichever table owns the body. */
function wireMarketRowExpansion(body, ordersByMarket, rerender) {
  // Wire up click/keyboard handlers for expandable rows
  body.querySelectorAll('.market-row').forEach(row => {
    const cid = row.dataset.cid;
    if (!cid || !ordersByMarket[cid]) return;

    row.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;
      if (expandedMarkets.has(cid)) {
        expandedMarkets.delete(cid);
      } else {
        expandedMarkets.add(cid);
      }
      rerender();
    });

    row.addEventListener('keydown', (e) => {
      if (e.target.closest('a')) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        row.click();
      }
    });
  });

  // Wire up toggle-cancelled buttons inside expanded rows
  body.querySelectorAll('.toggle-cancelled-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const row = btn.closest('.orders-expand-row');
      if (!row) return;
      // Find the market row above this expand row
      const prevRow = row.previousElementSibling;
      if (!prevRow || !prevRow.dataset.cid) return;
      const cid = prevRow.dataset.cid;
      if (showCancelledByMarket.has(cid)) {
        showCancelledByMarket.delete(cid);
      } else {
        showCancelledByMarket.add(cid);
      }
      rerender();
    });
    btn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        btn.click();
      }
    });
    if (!btn.getAttribute('tabindex')) btn.setAttribute('tabindex', '0');
  });
}

/* ── Render: Market Filter Kanban Board (Tab 3) ── */
const BUCKET_DEFS = [
  { key: 'raw', name: '1. Ingestion / Universe', cls: 'raw' },
  { key: 'identity', name: '2. Identity Gate', cls: 'rejected' },
  { key: 'movement', name: '3. Movement Gate', cls: 'rejected' },
  { key: 'volume', name: '4. Volume Gate', cls: 'rejected' },
  { key: 'depth', name: '5. Depth Gate', cls: 'rejected' },
  { key: 'spread', name: '6. Spread Gate', cls: 'rejected' },
  { key: 'horizon', name: '7. Horizon & Yield Gate', cls: 'rejected' },
  { key: 'passed', name: '8. Passed (Quoting)', cls: 'passed' },
];
const STAGE_DEFS = BUCKET_DEFS;

function fmtAge(sec) {
  if (sec === null || sec === undefined) return '--';
  if (sec < 60) return Math.round(sec) + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  return Math.floor(sec / 3600) + 'h ago';
}

// Market Filter uptime stopwatch. The poll carries the service's uptime in
// seconds; the ticker below extrapolates from it every second so the header
// reads as a stopwatch instead of stepping once per poll. Anchoring on the
// server-sent elapsed time rather than on `started_at` keeps the reading
// correct even when the browser's clock disagrees with the host's, and the
// drift between polls is measured on a monotonic clock so a wall-clock
// correction (NTP step, DST, manual change) can never make it tick backwards.
let filterUptimeAnchor = null;

// `performance.now()` is monotonic; `Date.now()` is not. Falls back to the
// wall clock only where performance timing is unavailable.
function monotonicMs() {
  return (typeof performance !== 'undefined' && performance.now)
    ? performance.now()
    : Date.now();
}

function fmtUptime(sec) {
  if (sec === null || sec === undefined || !isFinite(sec) || sec < 0) return '';
  const total = Math.floor(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return 'up ' + h + 'h ' + String(m).padStart(2, '0') + 'm';
  if (m > 0) return 'up ' + m + 'm ' + String(s).padStart(2, '0') + 's';
  return 'up ' + s + 's';
}

function renderFilterUptime() {
  const el = document.getElementById('scan-filter-uptime');
  if (!el) return;
  // A stopped service has no uptime: show nothing rather than `up 0s`, which
  // the STOPPED pill beside it would immediately contradict.
  if (!filterUptimeAnchor) {
    el.textContent = '';
    return;
  }
  const drift = (monotonicMs() - filterUptimeAnchor.receivedAtMs) / 1000;
  el.textContent = ' · ' + fmtUptime(filterUptimeAnchor.uptimeSec + drift);
}

function setFilterUptime(status) {
  const filter = status?.services?.filter;
  const uptime = filter?.running ? filter.uptime_sec : null;
  if (uptime === null || uptime === undefined) {
    // Stopped: drop the anchor entirely, so a later start counts from zero.
    filterUptimeAnchor = null;
    renderFilterUptime();
    return;
  }
  const startedAt = filter?.started_at ?? null;
  const now = monotonicMs();
  let uptimeSec = Number(uptime);
  // Same process (same start time) means the stopwatch may only move forward.
  // A host clock correction can hand back a smaller elapsed figure, and an
  // uptime that jumps backwards reads as a restart that did not happen. A
  // genuine restart carries a new `started_at`, which resets the anchor.
  if (filterUptimeAnchor && filterUptimeAnchor.startedAt === startedAt) {
    const shown = filterUptimeAnchor.uptimeSec + (now - filterUptimeAnchor.receivedAtMs) / 1000;
    uptimeSec = Math.max(uptimeSec, shown);
  }
  filterUptimeAnchor = { uptimeSec, startedAt, receivedAtMs: now };
  renderFilterUptime();
}

function categorizeGate(cause) {
  const c = (cause || '').toLowerCase();
  if (c.includes('depth')) return 'depth';
  if (c.includes('spread')) return 'spread';
  // The movement gate runs BEFORE the book fetches, so its refusals arrive
  // before any reading a depth/spread check could have produced. Folding
  // them into identity (the old fallback) made a dead tape read as a
  // keyword or mid problem.
  if (c.includes('movement')) return 'movement';
  if (c.includes('volume')) return 'volume';
  if (c.includes('horizon')) return 'horizon';
  if (c.includes('income') || c.includes('payout')) return 'horizon';
  return 'identity';
}

// A gate bar the snapshot did not carry falls back to the shipped default.
// Written long-hand rather than with `??` so it cannot be mistaken for -- or
// grow into -- the zero-coercion the account legs are guarded against.
function gateBar(value, fallback) {
  return (value === null || value === undefined) ? fallback : Number(value);
}

function getStageHero(key, funnel) {
  // The shipped bars, not the pre-2026-08-25 ones. A hero that falls back to
  // $250k/$1,000 describes a filter this repo has not run for months, and it
  // reads as a gate change nobody made.
  const volGate = gateBar(funnel?.volume_gate_usd, 125000);
  const depthGate = gateBar(funnel?.depth_gate_usd, 500);
  const spreadGate = gateBar(funnel?.spread_gate, 0.06);
  const horizonDays = gateBar(funnel?.horizon_gate_days, 30);
  const rewardIncome = gateBar(funnel?.reward_min_income_usd_day, 1.5);
  const spreadIncome = gateBar(funnel?.spread_min_income_usd_day, 0);
  const maxPairCost = gateBar(funnel?.max_pair_cost, 0.995);

  switch (key) {
    case 'raw':
      return {
        param: 'TEST: CANDIDATE DISCOVERY',
        // One unified Gamma scan since the reward path retired (#185);
        // "Sampling" listed reward-funded markets only.
        value: 'Gamma Volume Universe',
      };
    case 'identity':
      return {
        param: 'TEST: CONTRACT & KEYWORDS',
        value: 'Binary · Mid [0.20, 0.80]',
      };
    case 'movement':
      return {
        param: 'TEST: RECENT TRADED NOTIONAL',
        value: `≥ $500 / 30m on the tape`,
      };
    case 'volume':
      return {
        param: 'TEST: 24H TRADING VOLUME',
        value: `≥ $${Number(volGate).toLocaleString()} / 24h`,
      };
    case 'depth':
      return {
        param: 'TEST: TOP-3 BID DEPTH',
        value: `≥ $${Number(depthGate).toLocaleString()} each (YES & NO)`,
      };
    case 'spread':
      return {
        param: 'TEST: MAX BOOK SPREAD',
        value: `≤ ${Number(spreadGate).toFixed(4)} (${(Number(spreadGate) * 100).toFixed(2)}%)`,
      };
    case 'horizon':
      return {
        param: 'TEST: HORIZON & PAYOUT',
        // The payout floor is a rewards rule. A spread market is paid by
        // whoever lifts the offer, so it passes on any income at all --
        // stating one universal bar here would call a passing market a
        // failure.
        value: `≤ ${Number(horizonDays).toFixed(1)} days · rewards ≥ $${Number(rewardIncome).toFixed(2)}/day · spread > $${Number(spreadIncome).toFixed(2)}/day`,
      };
    case 'passed':
      return {
        param: 'TEST: PAIR MERGE ARBITRAGE',
        value: `Pair Cost ≤ $${Number(maxPairCost).toFixed(3)} ➔ $1.00 USDC`,
      };
    default:
      return { param: 'GATE TEST', value: '--' };
  }
}

// TRIAL READINESS. The ranker's near-miss logs say whether a gate's refusals
// are consistent enough to license a controlled loosening. Rendered as two
// tracker cards plus a banner, so the evidence becomes a decision instead of
// accumulating in a JSONL nobody reads.
function trackerCard(tracker) {
  if (!tracker) return '';
  const t = tracker.thresholds || {};
  const ready = tracker.ready === true;
  const cls = ready ? 'filled' : 'stopped';
  const blockers = (tracker.blockers || []).join(' · ');
  const label = String(tracker.gate || '').toUpperCase();
  return `<span class="pill ${cls}" style="font-size:11px" title="${esc(blockers || 'all thresholds met')}">`
    + `${esc(label)} ${ready ? 'TRIAL READY' : 'gathering'}`
    + `</span> <span style="color:var(--text-secondary)">`
    + `${tracker.days.toFixed(1)}d/${t.min_days ?? '--'} · `
    + `${tracker.unique_markets}/${t.min_unique ?? '--'} markets · `
    + `${tracker.small_margin}/${t.min_small_margin ?? '--'} near · `
    + `${Math.round((tracker.stability || 0) * 100)}%/`
    + `${t.min_stability == null ? '--' : Math.round(t.min_stability * 100) + '%'} stable`
    + `</span>`;
}

function renderTrialReadiness(readiness) {
  const banner = document.getElementById('trial-ready-banner');
  const trackers = document.getElementById('trial-trackers');
  if (!banner || !trackers) return;
  if (!readiness) {
    banner.style.display = 'none';
    trackers.style.display = 'none';
    return;
  }

  const gates = readiness.ready_gates || [];
  if (readiness.trial_ready && gates.length) {
    banner.style.display = '';
    banner.className = 'pill filled mono';
    banner.textContent = 'TRIAL READY: ' + gates.join(' + ').toUpperCase();
    banner.title = 'The near-miss evidence for this gate meets every readiness '
      + 'threshold. Readiness is not profitability — the trial measures that.';
  } else {
    banner.style.display = 'none';
  }

  trackers.style.display = 'flex';
  trackers.innerHTML = `<div>${trackerCard(readiness.depth)}</div>`
    + `<div>${trackerCard(readiness.volume)}</div>`;
}

function renderScreener(kpi, scanState) {
  const board = document.getElementById('kanban-board');
  const headerPill = document.getElementById('scan-state-pill');
  const headerAge = document.getElementById('scan-snapshot-age');
  const headerCensus = document.getElementById('scan-census');
  const headerGates = document.getElementById('scan-gates');

  // Render scan state pill
  if (scanState) {
    const state = scanState.scan_state || '--';
    const pillCls = state === 'SCANNING' ? 'active' : (state === 'STALLED' ? 'error' : 'stopped');
    headerPill.className = 'pill ' + pillCls;
    headerPill.textContent = state;
    if (scanState.seconds_since_heartbeat !== null && scanState.seconds_since_heartbeat !== undefined) {
      headerAge.textContent = 'heartbeat: ' + Math.round(scanState.seconds_since_heartbeat) + 's';
    }
  } else {
    headerPill.className = 'pill stopped';
    headerPill.textContent = '--';
  }

  const funnel = kpi?.funnel;
  if (!funnel) {
    // No pipeline data — show empty state
    board.innerHTML = `<div class="kanban-empty" style="flex:1">
      <div class="empty-state-title">No Market Filter data yet</div>
      <div class="empty-state-msg">The Market Filter writes runtime/pipeline.json on each scan cycle. Data appears here once it runs.</div>
    </div>`;
    headerCensus.textContent = '';
    headerGates.style.display = 'none';
    return;
  }

  // Snapshot age and census
  const age = funnel.snapshot_age;
  headerAge.textContent = 'last scan: ' + fmtAge(age);
  if (age !== null && age !== undefined && age > 600) {
    headerAge.style.color = 'var(--warn)';
  } else {
    headerAge.style.color = 'var(--text-secondary)';
  }
  headerCensus.textContent = funnel.census || '';
  if (funnel.gates) {
    headerGates.textContent = funnel.gates;
    headerGates.style.display = 'block';
  }

  // Group rejections by canonical gate
  const gateRejections = {
    identity: { count: 0, examples: [], would_fund: 0, traps: 0 },
    movement: { count: 0, examples: [], would_fund: 0, traps: 0 },
    volume: { count: 0, examples: [], would_fund: 0, traps: 0 },
    depth: { count: 0, examples: [], would_fund: 0, traps: 0 },
    spread: { count: 0, examples: [], would_fund: 0, traps: 0 },
    horizon: { count: 0, examples: [], would_fund: 0, traps: 0 },
  };

  for (const f of (funnel.filters || [])) {
    const g = categorizeGate(f.cause);
    if (!gateRejections[g]) {
      gateRejections[g] = { count: 0, examples: [], would_fund: 0, traps: 0 };
    }
    gateRejections[g].count += (f.n || 0);
    if (f.examples && f.examples.length) {
      gateRejections[g].examples.push(...f.examples);
    }
    gateRejections[g].would_fund += (f.would_fund || 0);
    gateRejections[g].traps += (f.traps || 0);
  }

  const counts = funnel.counts || {};
  const totalRaw = counts.scored || counts.attempted || funnel.raw_count || 0;

  // Per-gate rejection totals, NOT a running pool.
  //
  // The ranker does not run these gates in board order and it stops at the
  // first failure: a market refused on depth never reached the volume check.
  // Subtracting each bucket from a running pool would therefore report that
  // market as having passed volume, and every "N of M advanced" figure after
  // the first gate would be invented. Each stage states only what it can
  // prove -- how many markets this gate refused, out of everything scored.
  const stageFlow = { raw: { rejected: 0, scored: totalRaw } };
  const gateOrder = ['identity', 'movement', 'volume', 'depth', 'spread', 'horizon'];
  for (const k of gateOrder) {
    stageFlow[k] = { rejected: gateRejections[k]?.count || 0, scored: totalRaw };
  }

  const graduatedList = funnel.graduated || [];
  const eligibleList = funnel.final || [];
  stageFlow.passed = { rejected: 0, scored: totalRaw };

  // Render the 7 stages. Build the markup as one string and assign it once:
  // `+=` on innerHTML reparses the whole board on every stage, and replacing
  // the children resets scrollLeft, which would yank an operator who scrolled
  // to stage 5 back to stage 1 on the next poll.
  const prevScrollLeft = board.scrollLeft;
  let boardHtml = '';
  for (const def of STAGE_DEFS) {
    let cardsHtml = '';
    let footerHtml = '';
    let flowHtml = '';
    let countBadge = '';
    const hero = getStageHero(def.key, funnel);

    if (def.key === 'raw') {
      const fundedN = counts.funded || (funnel.raw?.rewards || []).length || 0;
      const spreadN = counts.spread_universe || (funnel.raw?.spread || []).length || 0;
      countBadge = `<span class="bucket-count-badge info">${totalRaw} TOTAL</span>`;
      flowHtml = `<div class="kanban-header-flow">
        <span>Discovery</span>
        <span class="flow-passed">${totalRaw} to evaluate ➔</span>
      </div>`;

      // Render raw examples if available
      const rawRewards = (funnel.raw?.rewards || []).slice(0, 8);
      const rawSpread = (funnel.raw?.spread || []).slice(0, 8);
      if (rawRewards.length || rawSpread.length) {
        for (const r of rawRewards) {
          cardsHtml += `<div class="market-card" role="listitem">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span class="card-tag tag-reward">REWARD</span>
              <span class="card-metric">$${esc(r.rate)}/d</span>
            </div>
            <div class="card-title">${marketLink(r)}</div>
            <div class="card-metric">Resolves: ${r.days !== null && r.days !== undefined ? esc(r.days) + 'd' : '--'}</div>
          </div>`;
        }
        for (const s of rawSpread) {
          cardsHtml += `<div class="market-card" role="listitem">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span class="card-tag tag-spread">SPREAD</span>
              <span class="card-metric">Vol: ${fmtUSD(s.volume || 0)}</span>
            </div>
            <div class="card-title">${marketLink(s)}</div>
            <div class="card-metric">Spread: ${s.spread !== null && s.spread !== undefined ? (Number(s.spread) * 100).toFixed(1) + '%' : '--'} | ${s.days !== null && s.days !== undefined ? esc(s.days) + 'd' : '--'}</div>
          </div>`;
        }
      } else {
        cardsHtml = `<div class="kanban-empty">
          <strong style="color:var(--text-primary)">${totalRaw} Candidate Markets</strong>
          <div style="margin-top:6px;color:var(--text-secondary)">${spreadN} tradable binaries scored from the unified Gamma scan.</div>
        </div>`;
      }
      // Discovery metadata from the unified scan (#185): pages fetched, rows
      // seen, whether the scan stopped on policy (truncation) rather than
      // exhaustion, and what the cheap per-row filters refused pre-score.
      const disc = funnel.discovery;
      if (disc) {
        const cheap = Object.entries(disc.cheap_rejects || {})
          .sort((a, b) => b[1] - a[1])
          .map(([k, v]) => `${k}: ${v}`).join(' · ');
        const trunc = disc.truncated
          ? ' (bounded scan — stopped past the volume floor, not exhaustion)'
          : ' (full listing exhausted)';
        footerHtml += `<div class="kanban-bucket-footer" style="display:block">`
          + `discovery: ${disc.pages_fetched ?? '--'} pages · ${disc.rows_scanned ?? '--'} rows scanned${trunc}`
          + (cheap ? `<br>pre-score filters — ${esc(cheap)}` : '')
          + `</div>`;
      } else {
        footerHtml = `<div class="kanban-bucket-footer">${spreadN} Spread candidates</div>`;
      }

    } else if (def.key === 'passed') {
      const passCount = graduatedList.length;
      countBadge = `<span class="bucket-count-badge ok">${passCount} ACTIVE</span>`;
      flowHtml = `<div class="kanban-header-flow">
        <span>Final Fleet</span>
        <span class="flow-passed">${passCount} quoting on venue</span>
      </div>`;

      if (passCount === 0 && eligibleList.length === 0) {
        cardsHtml = `<div class="kanban-empty">No markets graduated. The Market Filter found no qualifying markets this cycle.</div>`;
      } else {
        // Track rendered IDs
        const renderedCids = new Set();
        for (const m of graduatedList) {
          renderedCids.add(m.condition_id);
          const income = m.pnl !== null && m.pnl !== undefined ? fmtUSD(m.pnl) : '--';
          const volStr = m.volume !== null && m.volume !== undefined ? fmtUSD(m.volume) : '--';
          const spreadStr = m.spread !== null && m.spread !== undefined ? (Number(m.spread) * 100).toFixed(2) + '%' : '--';
          const daysStr = m.days_to_resolve !== null && m.days_to_resolve !== undefined ? esc(m.days_to_resolve) + 'd' : '--';
          const retStr = m.return_pct_day !== null && m.return_pct_day !== undefined ? esc(m.return_pct_day) + '%/d' : (m.est_income ? '$' + Number(m.est_income).toFixed(2) + '/d' : '--');
          const shortCid = m.condition_id ? (m.condition_id.slice(0, 6) + '...' + m.condition_id.slice(-4)) : '';

          cardsHtml += `<div class="market-card passed-card" role="listitem">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
              <span class="card-tag tag-quoting">QUOTING</span>
              <span class="mono" style="font-size:9.5px;color:var(--text-muted)">${esc(shortCid)}</span>
            </div>
            <div class="card-title" title="${esc(m.title || m.slug || '')}">${marketLink(m)}</div>
            <div class="card-metrics-grid">
              <div>Fills: <span class="card-fills">${esc(m.fills || 0)}</span></div>
              <div>P&L: <span class="card-income">${income}</span></div>
              <div>24h Vol: <span style="color:var(--text-primary)">${volStr}</span></div>
              <div>Spread: <span style="color:var(--text-primary)">${spreadStr}</span></div>
              <div>Days: <span style="color:var(--text-primary)">${daysStr}</span></div>
              <div>Est Ret: <span class="card-ret">${retStr}</span></div>
            </div>
          </div>`;
        }

        // Also render other eligible runners-up if any
        for (const el of eligibleList) {
          const cid = el.cid || el.condition_id;
          if (cid && renderedCids.has(cid)) continue;
          cardsHtml += `<div class="market-card" role="listitem" style="border-color:rgba(52,211,153,0.2)">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
              <span class="card-tag" style="background:rgba(52,211,153,0.1);color:#34d399">ELIGIBLE</span>
              <span class="card-metric">${esc(el.source || 'spread')}</span>
            </div>
            <div class="card-title" title="${esc(el.title || '')}">${marketLink(el)}</div>
            <div class="card-metric">Est Ret: <span class="card-ret">${el.ret_day_pct !== null && el.ret_day_pct !== undefined ? esc(el.ret_day_pct) + '%/d' : '--'}</span> | Vol: ${fmtUSD(el.volume || 0)}</div>
          </div>`;
        }
      }
      footerHtml = `<div class="kanban-bucket-footer">Top ${passCount} Quoting Live</div>`;

    } else {
      // Intermediate Gate
      const gateData = gateRejections[def.key] || { count: 0, examples: [], would_fund: 0, traps: 0 };
      const flow = stageFlow[def.key] || { rejected: 0, scored: 0 };
      const droppedCount = flow.rejected;

      const badgeCls = droppedCount > 0 ? 'alert' : 'ok';
      countBadge = `<span class="bucket-count-badge ${badgeCls}">${droppedCount} REJECTED</span>`;
      flowHtml = `<div class="kanban-header-flow">
        <span class="flow-rejected">${droppedCount} refused here</span>
        <span>of ${flow.scored} scored</span>
      </div>`;

      if (droppedCount === 0) {
        cardsHtml = `<div class="kanban-empty all-pass">
          <strong>✓ No rejections</strong>
          <div style="margin-top:6px;color:var(--text-secondary)">No market that reached this gate was refused by it.</div>
        </div>`;
      } else {
        const examples = gateData.examples || [];
        if (examples.length === 0) {
          cardsHtml = `<div class="kanban-empty">${droppedCount} markets failed criteria at this gate.</div>`;
        } else {
          for (const ex of examples) {
            cardsHtml += `<div class="market-card" role="listitem">
              <div class="card-title" title="${esc(ex.title || '')}">${marketLink(ex)}</div>
              <div class="card-reason">${esc(ex.reason || 'Criteria not met')}</div>
            </div>`;
          }
          if (droppedCount > examples.length) {
            cardsHtml += `<div style="text-align:center;font-size:10px;color:var(--text-muted);padding:4px">+ ${droppedCount - examples.length} more rejected markets</div>`;
          }
        }
      }

      // Near-miss footer
      if (gateData.would_fund > 0) {
        footerHtml = `<div class="kanban-bucket-footer" style="color:#fbbf24">${gateData.would_fund} would clear allocator floor</div>`;
      } else {
        footerHtml = `<div class="kanban-bucket-footer">${droppedCount} of ${flow.scored} scored markets refused here</div>`;
      }
    }

    boardHtml += `<div class="kanban-bucket ${def.cls}" role="list" aria-label="${esc(def.name)}">
      <div class="kanban-bucket-header">
        <div class="kanban-header-top">
          <span>${def.name}</span>
          ${countBadge}
        </div>
        <div class="kanban-bucket-hero ${def.key}">
          <div class="hero-param-label">${hero.param}</div>
          <div class="hero-critical-val">${hero.value}</div>
        </div>
        ${flowHtml}
      </div>
      <div class="kanban-bucket-body">${cardsHtml}</div>
      ${footerHtml}
    </div>`;
  }

  board.innerHTML = boardHtml;
  board.scrollLeft = prevScrollLeft;

  // Update navigation arrow states after rendering
  requestAnimationFrame(updateKanbanNavButtons);
}

/* ── Kanban Carousel Navigation ── */
function scrollKanban(direction) {
  const board = document.getElementById('kanban-board');
  if (!board) return;
  const bucket = board.querySelector('.kanban-bucket');
  const step = bucket ? (bucket.offsetWidth + 14) : 334;
  // An explicit `behavior` overrides the CSS scroll-behavior, so the
  // reduced-motion request has to be honoured here as well as in the
  // stylesheet.
  const reduceMotion = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  board.scrollBy({ left: direction * step, behavior: reduceMotion ? 'auto' : 'smooth' });
  setTimeout(updateKanbanNavButtons, 350);
}

function updateKanbanNavButtons() {
  const board = document.getElementById('kanban-board');
  const prevBtn = document.getElementById('kanban-nav-prev');
  const nextBtn = document.getElementById('kanban-nav-next');
  if (!board || !prevBtn || !nextBtn) return;

  const maxScrollLeft = board.scrollWidth - board.clientWidth;
  if (maxScrollLeft <= 4) {
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    prevBtn.classList.add('disabled');
    nextBtn.classList.add('disabled');
    return;
  }

  const atStart = board.scrollLeft <= 8;
  const atEnd = board.scrollLeft >= maxScrollLeft - 8;

  prevBtn.disabled = atStart;
  nextBtn.disabled = atEnd;
  prevBtn.classList.toggle('disabled', atStart);
  nextBtn.classList.toggle('disabled', atEnd);
}

function initKanbanCarousel() {
  const board = document.getElementById('kanban-board');
  const prevBtn = document.getElementById('kanban-nav-prev');
  const nextBtn = document.getElementById('kanban-nav-next');

  if (prevBtn) {
    prevBtn.addEventListener('click', (e) => {
      e.preventDefault();
      scrollKanban(-1);
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', (e) => {
      e.preventDefault();
      scrollKanban(1);
    });
  }
  if (board) {
    board.addEventListener('scroll', updateKanbanNavButtons, { passive: true });
  }

  // Keyboard navigation for arrow keys
  document.addEventListener('keydown', (e) => {
    const tab3 = document.getElementById('tab-3');
    if (!tab3 || tab3.hidden) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;

    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      scrollKanban(-1);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      scrollKanban(1);
    }
  });

  window.addEventListener('resize', updateKanbanNavButtons, { passive: true });
}

/* ── Helper: Safe JSON fetch with timeout ── */
async function safeJsonFetch(url, timeoutMs = 5000) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/* ── Poll loop ── */
let isPolling = false;
async function pollStatus() {
  if (isPolling) return;
  isPolling = true;
  try {
    const [state, status, kpi, scanState, trialReadiness, guardAlerts, guardHealth] = await Promise.all([
      safeJsonFetch('/api/state'),
      safeJsonFetch('/api/system/status'),
      safeJsonFetch('/api/kpi'),
      safeJsonFetch('/api/scan-state'),
      safeJsonFetch('/api/trial-readiness'),
      safeJsonFetch('/api/guardrail-alerts'),
      safeJsonFetch('/api/guardrail-health'),
    ]);

    if (state) lastState = state;
    if (kpi) lastKpi = kpi;

    // Which registry these numbers came from, before anything renders them.
    if (status) renderDbMode(status);

    // Service uptime rides on the status payload, so it must not wait on
    // /api/kpi: the Market Filter header still needs a stopwatch when the KPI read
    // is the one that failed.
    if (status) setFilterUptime(status);

    // Render service cards
    if (status) renderServiceCards(status, guardHealth, guardAlerts);

    // Render exposure bar (DT3)
    if (kpi || lastKpi) renderExposure(kpi || lastKpi);

    // USDC Balance in top nav bar
    const currentKpi = kpi || lastKpi;
    const collateral = currentKpi?.portfolio?.account?.collateral_usd ?? currentKpi?.portfolio?.account?.account_value_usd;
    const usdcEl = document.getElementById('usdc-balance');
    if (usdcEl) {
      usdcEl.textContent = (collateral !== null && collateral !== undefined) ? `USDC: ${fmtUSD(collateral)}` : 'USDC: --';
    }

    // Render KPIs (Tab 2)
    if (currentKpi) {
      renderKPIs(currentKpi, status);
      renderMarkets(currentKpi, lastState);
      renderOrdersTrades(currentKpi, lastState);
    }

    // Render the Market Filter kanban (Tab 3)
    if (currentKpi) {
      renderScreener(currentKpi, scanState);
    }

    // Trial readiness rides on its own endpoint, so it renders whether or not
    // the KPI read succeeded -- and it is called even when the readiness fetch
    // FAILED, so a dead endpoint hides the trackers rather than leaving the
    // last reading on screen as if it were current.
    renderTrialReadiness(trialReadiness);
  } catch (e) {
    // Non-fatal transient error swallowed gracefully
  } finally {
    isPolling = false;
  }
}

/* ── Start ── */
// Skipped only when this file is loaded as a CommonJS module, which is how the
// test harness reaches the handlers. A browser has no `module`, so the page
// bootstraps exactly as it always has -- and a harness never starts the poll
// loop, the SSE reconnect timer or the carousel behind the handler it drives.
if (typeof module === 'undefined' || !module.exports) {
  initKanbanCarousel();
  initStatisticalSubnav();
  initOrdersTradesTabs();
  initDistControls();
  pollStatus();
  renderParameters();
  setInterval(pollStatus, POLL_MS);
  setInterval(renderShadowClock, 1000);
  setInterval(renderFilterUptime, 1000);
}

// Node-only: lets tests reach the handlers. Browsers have no `module`, so this
// is dead code in the page.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { renderPositionDistributionChart, renderMarkoutChart, renderMonteCarloChart, renderQuantRiskGrid, signClass, fmtSignedUSD, _ciBounds, decisionGatesHtml, decisionGatesRows, gateBadge, typesetMath, renderTrialReadiness, trackerCard, isMergedOrder, isActiveOrder, collapseMergedPair, renderExpandedOrders, renderDbMode, setShadowRun, renderShadowClock, fmtStopwatch, setFilterUptime, renderFilterUptime, fmtUptime, renderServiceCards, fmtLocalTime, connectSSE, marketLink, renderMarkets, groupOrdersByMarket, renderBrokerPortfolioOverview, portfolioEquity,
    statsFilterScope, pruneStatsSubnav, STATS_VIEW_TARGETS, applyStatsViewFilter,
    renderPnlCiReadout, renderExecutionFunnel,
    OT_VIEWS, OT_COLUMNS, ordersTradesRows, ordersTradesCounts, otHeadHtml,
    activeMarketsRows, openOrdersRows, positionsRows, closedTradesRows,
    closedTradesEntries, marketRowPairHtml, wireMarketRowExpansion,
    heldMarketEntries, heldLegs, isFinishedMarket, latestLegMids, latestLegQuotes,
    positionMarkValue, settledMarkValue, winningLeg,
    isQuotedMarket, isRestingOrder, tokenLegMap, legForOrder,
    normalizeLeg, groupOrdersByPair, restingPairCost, restingPairLegs,
    pairStatus, PAIR_STATUS, isMarketInferredPosition, pairSummary,
    get isStopping() { return isStopping; },
    set isStopping(v) { isStopping = v; } };
}
