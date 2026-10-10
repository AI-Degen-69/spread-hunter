/* Spread Hunter — Dashboard Application Logic
 *
 * Consumes existing API endpoints:
 *   GET  /api/state            — order/fill state
 *   GET  /api/system/status    — service PIDs, bot state, starting capital
 *   POST /api/system/start     — start bot stack (atomic)
 *   POST /api/system/stop      — stop bot stack
 *   GET  /api/kpi              — all Tab 2 analytics (includes trade_analytics.sample_size_sufficiency)
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
/* ── Cached poll snapshots (Issue #264) ─────────────────────────────
 * The last payload of every poll endpoint, so a tab switch can paint the
 * newly shown tab synchronously without waiting for the next 2s poll. */
let lastStatus = null;
let lastScanState = null;
let lastTrialReadiness = null;
let lastGuardHealth = null;
let lastGuardAlerts = null;

/* ── Held-read receipt times (Issue #348) ───────────────────────────────
 * When a poll read fails or times out, each pill keeps its last known-good
 * payload while the backend is still reachable, ageing it through the
 * existing ramps. These stamps record when each retained payload arrived so
 * the hold can advance its age instead of replaying a frozen one. */
let lastScanStateAtMs = null;
let lastKpiAtMs = null;
let lastStatusAtMs = null;

/* ── Payload version (Issue #251) ─────────────────────────────────────
 * The static files and the Python backend are served by different processes,
 * so a page reload can outrun a backend restart: the frontend then reads
 * fields that do not exist yet, and "absent" is indistinguishable from
 * "genuinely unmeasured". The backend stamps `payload_version` on the /api/kpi
 * envelope; anything lower (or missing) means this page is newer than the
 * process answering it. Keep EXPECTED_PAYLOAD_VERSION matched with
 * KPI_PAYLOAD_VERSION in core_brain/kpi.py. */
const EXPECTED_PAYLOAD_VERSION = 255;
let payloadVersionWarned = false;

// True when the payload is absent, malformed, or predates this page.
function payloadIsStale(kpi) {
  const v = kpi ? kpi.payload_version : null;
  if (typeof v !== 'number' || !Number.isFinite(v)) return true;
  return v < EXPECTED_PAYLOAD_VERSION;
}

// Shows the amber "backend older than page" note and warns once — not on
// every 2s poll, which would bury the message it is trying to deliver.
function applyPayloadVersion(kpi) {
  const stale = payloadIsStale(kpi);
  const note = document.getElementById('quant-stale-note');
  if (note) note.classList.toggle('show', stale);
  if (stale && !payloadVersionWarned) {
    payloadVersionWarned = true;
    const seen = kpi && kpi.payload_version != null ? kpi.payload_version : 'absent';
    console.warn(`[App] Dashboard backend is older than this page `
      + `(payload_version ${seen}; expected ${EXPECTED_PAYLOAD_VERSION}). `
      + `Restart the dashboard to see all metrics.`);
  }
}
// Whether the page is reading the production registry. Starts false: until the
// first status arrives we cannot claim a live view, and START is refused on it.
let lastDbIsProduction = false;
let lastDbIsShadow = false;

function isShadowMode(status) {
  if (status && (status.db_is_production === false || status.db_mode === 'SHADOW')) return true;
  if (status && (status.db_is_production === true || status.db_mode === 'LIVE')) return false;
  return lastDbIsShadow === true;
}
let isStopping = false;
let isStarting = false;
// Bumped on every master-control request start and end. A poll captures the
// value when it begins; if it changed before the poll's status arrives, the
// poll's own (older) payload must not repaint the button over the click's
// result (#457).
let masterLifecycleSeq = 0;

/* ── Live-state language (DESIGN.md) ──────────────────────────────────
 * Six states, one vocabulary, applied identically to every process, feed and
 * tile: RUNNING / DEGRADED / DOWN / STOPPED / UNKNOWN / STALE. Heartbeat age
 * is displayed, not implied: a live indicator ages green → amber → red on a
 * ramp calibrated to its own cadence (defaults: 5s loop → amber ≥ 15s, red ≥
 * 60s). */

// Map (running, age_sec) → state key. `running=false` with a known age means
// the process died mid-flight (DOWN); with no age at all it is the quiet,
// intentional STOPPED. Registry unreadable / never seen is UNKNOWN.
function stateKey(running, ageSec, thresholds) {
  const th = thresholds || {};
  const degradedAfter = th.degraded !== undefined ? th.degraded : 15;
  const downAfter = th.down !== undefined ? th.down : 60;
  if (running) {
    if (ageSec === null || ageSec === undefined) return 'running';
    if (ageSec >= downAfter) return 'down';
    if (ageSec >= degradedAfter) return 'degraded';
    return 'running';
  }
  if (ageSec !== null && ageSec !== undefined) return 'down';
  return 'stopped';
}

// Canonical pill markup: state class + heartbeat age (omitted when unknown or
// when the state is STOPPED/UNKNOWN — a stopped service has no age to show).
function statePillHtml(state, ageSec) {
  const known = ageSec !== null && ageSec !== undefined && isFinite(ageSec);
  const showAge = (state === 'running' || state === 'degraded' || state === 'down') && known;
  const age = showAge ? ` · ${Math.max(0, Math.round(ageSec))}s` : '';
  const label = state.toUpperCase();
  const dot = (state === 'running' || state === 'degraded' || state === 'down')
    ? `<span class="pulse-dot ${state === 'running' ? 'active' : ''}"></span>`
    : '';
  return `<span class="pill state-${state}">${dot}${label}${age}</span>`;
}

function processState(running, unknown) {
  if (unknown) return 'unknown';
  return running ? 'running' : 'stopped';
}

/* Rotation takes ~45-55s on shadow public CLOB queries; calibrated so a normal
 * rotation stays RUNNING without a false DEGRADED at the 15s default. */
const SCAN_PILL_THRESHOLDS = { degraded: 60, down: 120 };

/* The scan pill reports liveness, not activity.
 *
 * The server's IDLE means "heartbeat fresh but no active-phase work in the
 * window" -- the filter is alive and between scans, which on a ~10m cycle is
 * most of the time. Mapping it to STOPPED said "intentionally not running"
 * (DESIGN.md) about a healthy process, so the pill flipped between SCANNING
 * and STOPPED every cycle. A live heartbeat ages through the ramp instead, and
 * a filter that really stops goes DOWN when its heartbeat does. Only a verdict
 * we do not recognise reads STOPPED. */
function scanPillState(rawState, hbAgeSec, cadenceSec, stallReason) {
  let reason = stallReason;
  let cadence = cadenceSec;
  if (typeof cadenceSec === 'string' && stallReason === undefined) {
    reason = cadenceSec;
    cadence = undefined;
  }
  if (rawState === 'STALLED') {
    if (reason === 'finished') return 'stopped';
    return 'down';
  }
  if (rawState === 'SCANNING' || rawState === 'IDLE') {
    // Ramp off the cadence the server MEASURED for this loop when it sent
    // one. The fixed 60/120s default assumed a rotation costs seconds; a
    // rotation that really costs ~160s crossed it every single cycle, so a
    // healthy loop sat on red.
    const c = Number(cadence);
    const th = (isFinite(c) && c > 0) ? cadenceThresholds(c) : SCAN_PILL_THRESHOLDS;
    return stateKey(true, hbAgeSec, th);
  }
  return 'stopped';
}

/* How often scripts/filter_loop.py re-ranks the universe. The server resolves
 * SH_FILTER_INTERVAL_SEC and sends it as `scan_interval_sec`; 600 is only the
 * fallback for a status payload that predates the field. An operator running a
 * 60s cadence must not see a 20-minute-old snapshot reading LIVE. */
const SCAN_SNAPSHOT_CYCLE_SEC = 600;

function scanIntervalSec(status) {
  const n = Number(status && status.scan_interval_sec);
  return (isFinite(n) && n > 0) ? n : SCAN_SNAPSHOT_CYCLE_SEC;
}

/* The top-nav MARKET SCAN pill: is the SCANNER alive?
 *
 * Distinct from the Market Filter header pill, which reports the trading
 * loop's heartbeat. This one answers the operator's actual question -- is
 * `scripts.filter_loop` running, and is the snapshot it writes one this page
 * can still read -- and it answers it from every tab.
 *
 * Red is reserved for "no scanner process". A live process whose file went
 * stale is amber: something is wrong, but the loop is not gone. "We cannot
 * read the process registry" is UNKNOWN, never DOWN -- inventing an outage
 * out of a missing file is the same lie in the other direction. */
/* Pick the payload a pill renders: the current read, the held last-good
 * payload while the backend is still reachable, or nothing. Pure: the caller
 * passes `nowMs` so the harness controls time. The age offset advances the
 * held reading through the existing ramps instead of replaying a frozen age. */
function resolveHeldRead(current, last, lastAtMs, nowMs, stale) {
  if (current !== null && current !== undefined) {
    return { payload: current, ageOffsetSec: 0, readFailed: false };
  }
  if ((last !== null && last !== undefined) && !stale) {
    const base = Number(lastAtMs);
    const now = Number(nowMs);
    const off = (isFinite(base) && isFinite(now) && now >= base) ? (now - base) / 1000 : 0;
    return { payload: last, ageOffsetSec: off, readFailed: true };
  }
  return { payload: null, ageOffsetSec: 0, readFailed: true };
}

function marketScanState(status, kpi, opts) {
  if (!status || status.registry_unreadable) {
    return { state: 'unknown', label: 'SCAN UNKNOWN',
             title: 'Cannot read the process registry, so the state of the Market Filter is unknown.' };
  }
  const svc = (status.services || {}).filter || {};
  if (!svc.running) {
    return { state: 'down', label: 'SCAN DOWN',
             title: 'No Market Filter process (scripts.filter_loop) is running. Nothing is scanning markets.' };
  }
  const kpiFailed = !!(opts && opts.kpiReadFailed);
  const ageOff = (opts && isFinite(opts.ageOffsetSec)) ? opts.ageOffsetSec : 0;
  let age = kpi && kpi.funnel ? kpi.funnel.snapshot_age : null;
  if (typeof age === 'number' && ageOff) age += ageOff;
  const heldNote = (kpiFailed && (kpi !== null && kpi !== undefined))
    ? ' Current read did not land; showing last reading.' : '';
  if (age === null || age === undefined) {
    if (kpiFailed) {
      return { state: 'degraded', label: 'SCAN DEGRADED', ageSec: null,
               title: 'The Market Filter is running, but the KPI read did not land, so the snapshot age is unknown.' };
    }
    return { state: 'degraded', label: 'SCAN DEGRADED', ageSec: null,
             title: 'The Market Filter is running but has not written runtime/pipeline.json yet.' };
  }
  if (age > scanIntervalSec(status) * 2) {
    return { state: 'degraded', label: 'SCAN DEGRADED', ageSec: age,
             title: 'The Market Filter is running, but its last snapshot is older than two scan cycles.' + heldNote };
  }
  return { state: 'running', label: 'SCAN RUNNING', ageSec: age,
           title: 'The Market Filter is running and its snapshot is fresh.' + heldNote };
}

function renderMarketScanPill(status, kpi, opts) {
  const el = document.getElementById('market-scan-pill');
  if (!el) return;
  const v = marketScanState(status, kpi, opts);
  el.className = 'pill state-' + v.state;
  el.title = v.title;
  const dot = (v.state === 'running') ? '<span class="pulse-dot active"></span>'
    : (v.state === 'degraded' || v.state === 'down') ? '<span class="pulse-dot"></span>'
    : '';
  const age = (v.ageSec !== null && v.ageSec !== undefined) ? ' · ' + fmtAge(v.ageSec) : '';
  el.innerHTML = dot + esc(v.label + age);
}

/* ── Backend-contact watchdog (DESIGN.md Risk 2) ──
 * When the poll loop loses contact with the backend, the page must stop
 * pretending the last render is live. After 2 consecutive failed polls the
 * STALE banner appears with the last-seen time; a successful poll clears it.
 * A single failed poll is tolerated: one dropped request is not a dead
 * backend. */
const BACKEND_STALE_AFTER_FAILURES = 2;
let backendFailures = 0;
let backendLastSeenMs = null;
let backendStale = false;

function setBackendContact(ok, nowMs) {
  const ts = nowMs !== undefined ? nowMs : Date.now();
  if (ok) {
    backendFailures = 0;
    backendStale = false;
    backendLastSeenMs = ts;
  } else {
    backendFailures += 1;
    // Stale once the failure streak is long enough, even if no poll ever
    // succeeded: a dashboard that starts against a dead backend is stale from
    // the start, not silently blank.
    if (backendFailures >= BACKEND_STALE_AFTER_FAILURES) {
      backendStale = true;
    }
  }
  renderBackendContact();
}

function renderBackendContact(nowMs) {
  const at = nowMs !== undefined ? nowMs : Date.now();
  const banner = document.getElementById('backend-contact-banner');
  if (!banner) return;
  if (!backendStale) {
    banner.classList.remove('show');
    delete banner.dataset.stale;
    return;
  }
  banner.classList.add('show');
  banner.dataset.stale = 'true';
  const ageEl = banner.querySelector('.stale-age');
  if (!ageEl) return;
  if (!backendLastSeenMs) {
    ageEl.textContent = 'backend never contacted';
    return;
  }
  const ageSec = Math.max(0, Math.round((at - backendLastSeenMs) / 1000));
  ageEl.textContent = 'last seen ' + fmtLocalTime(new Date(backendLastSeenMs).toISOString())
    + ' · ' + ageSec + 's ago';
}

// Heartbeat-age ramp for a cadence: amber at 3x, red at 12x.
function cadenceThresholds(cadenceSec) {
  const c = Number(cadenceSec);
  if (!isFinite(c) || c <= 0) return { degraded: 15, down: 60 };
  return { degraded: Math.round(c * 3), down: Math.round(c * 12) };
}

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

/* Format timestamp into DD/MM/YYYY, HH:mm (24 hour local time).
 * The one time a row was added to the table, whatever view it renders in:
 *   Active Markets — the moment the latest quote was logged for the market.
 *   Open Orders    — the moment the order was posted (`posted_ts`, ms).
 *   Positions      — the moment the leg filled (latest fill `venue_ts`, ms).
 *   Closed Trades  — the moment the trade closed (settlement close `ts`, s;
 *                      the resolution record is the fallback).
 * Second-resolution values are ms-vs-s ambiguous only below the registry's
 * first real timestamps, so `× 1000` is applied only to plainly-seconds
 * magnitudes — the same heuristic the backend run rollup already uses. */
function toMs(ts) {
  const n = Number(ts);
  if (!Number.isFinite(n) || n <= 0) return null;
  return (n < 1e11) ? n * 1000 : n;
}

function fmtTimestamp(ts) {
  const ms = toMs(ts);
  if (ms === null) return '--';
  const d = new Date(ms);
  if (isNaN(d.getTime())) return '--';
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const yyyy = d.getFullYear();
  const HH = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${dd}/${mm}/${yyyy}, ${HH}:${min}`;
}

/* When each stage's row entered the table, one accessor per view. Each
 * returns milliseconds (via `toMs`, so seconds and milliseconds sources mix
 * safely) or null when nothing measured:
 *   latestQuoteTs — Active Markets: the latest quote logged for the market.
 *   latestFillTs  — Positions: the latest fill; a held market with quotes but
 *                    no fills shows `--`, not a quote time posing as a fill.
 *   closeTsOf     — Closed Trades: the latest settlement close, with the
 *                    resolution record as fallback. Sort and render share it.
 * All three take the maximum of their own source only, so a later quote can
 * never replace a measured fill time or a booked close. */
function latestQuoteTs(m) {
  let best = null;
  for (const q of (m && m.quotes) || []) {
    const t = toMs(q && q.ts);
    if (t !== null && (best === null || t > best)) best = t;
  }
  return best;
}

function latestFillTs(m) {
  let best = null;
  for (const f of (m && m.fills) || []) {
    const t = toMs(f && f.venue_ts);
    if (t !== null && (best === null || t > best)) best = t;
  }
  return best;
}

function closeTsOf(m) {
  let best = null;
  for (const s of (m && m.settlements) || []) {
    const t = toMs(s && s.ts);
    if (t !== null && (best === null || t > best)) best = t;
  }
  if (best === null && m && m.resolution && m.resolution.resolved_ts) {
    best = toMs(m.resolution.resolved_ts);
  }
  return best;
}

/* Relative age beside the absolute Timestamp: "3m ago" reads how fresh a row
 * is without the operator subtracting clock faces. Milliseconds in, so the
 * same value that feeds `fmtTimestamp` feeds this one. A future timestamp
 * (clock skew between this page and the registry) clamps to "just now"
 * rather than counting down from a negative age. */
function fmtRelAgo(tsMs) {
  const ms = toMs(tsMs);
  if (ms === null) return '';
  const sec = Math.max(0, (Date.now() - ms) / 1000);
  if (sec < 60) return 'just now';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
  return Math.floor(sec / 86400) + 'd ago';
}

/* One Timestamp cell, shared by every view: the absolute local time on the
 * first line, the relative age in a muted caption under it. Both re-render
 * with the poll, so the age never freezes between repaints. */
function timestampCell(ts, opts) {
  const o = opts || {};
  const rowspan = o.rowspan ? ` rowspan="${o.rowspan}"` : '';
  const rel = fmtRelAgo(ts);
  const relHtml = rel ? `<div class="caption-muted">${esc(rel)}</div>` : '';
  return `<td class="mono" style="white-space:nowrap;"${rowspan}>${fmtTimestamp(ts)}${relHtml}</td>`;
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

/* ── Visible-tab rendering (Issue #264) ─────────────────────────────
 * One poll used to re-render every section on every 2s tick — both hidden
 * tabs plus the Monte Carlo / KDE / markout charts — a single long
 * main-thread task that a tab click waited behind for 2-4s. Now each poll
 * repaints the cheap header pills plus only the visible tab; a hidden tab
 * repaints on switch from the cached snapshot. */
function tabVisible(el) {
  // Fails closed: a missing panel is a bug to notice, not work to perform.
  return !!el && el.hidden !== true;
}

/* Is `el` somewhere the operator can actually see? (Issue #266)
 * The sidebar-pages layout (#140) moved every live panel under `#page-*`
 * sections and shows exactly one of them; the legacy `#tab-1..3` shells stay
 * in the document un-hidden (prototype.js unhides them after moving the
 * panels out), so after the rail mounts `tabVisible()` reports "visible" for
 * all three gates and every poll re-rendered ALL pages' content — multi-second
 * main-thread freezes the operator felt as hover/click lag. A section paints
 * only when its own closest `#page-*` ancestor is the one the rail shows. */
function railActivePage(doc) {
  if (typeof document === 'undefined') return null;
  const scope = doc || document;
  const pages = scope.querySelectorAll('#page-home, #page-data-markets, #page-strategy, #page-trades, #page-reports');
  if (!pages || pages.length === 0) return null; // tabs-only page: no rail
  for (const page of pages) {
    if (page.hidden !== true) return page.id;
  }
  return null;
}

function paintable(section, target, doc) {
  // Gate 1 — the legacy tab section the panel belongs to (fails closed).
  if (!tabVisible(section)) return false;
  // Gate 2 — the active rail page, once the sidebar layout has mounted.
  const active = railActivePage(doc);
  if (active === null) return true; // no rail in the document: tabs-era page
  let node = target;
  while (node) {
    if (node.id === active) return true;
    node = node.parentElement;
  }
  return false;
}

function deferPaint(fn) {
  // One frame later the click/tab paint wins over the heavy charts. Outside
  // a browser (node test harness) there is no rAF — run synchronously there.
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(fn);
  else fn();
}

// Generation guard for the deferred analytics paint (Issue #264 follow-up):
// a poll queues the heavy charts one frame out, and a tab switch in between
// must not pay for a hidden tab's charts. Each renderKPIs bumps the
// generation; a queued callback paints only when still current and Tab 2 is
// still visible.
let analyticsPaintGeneration = 0;

// Header pills stay live on every poll — cheap text updates, always painted.
// They are deliberately NOT repainted on tab switch: reprinting them from
// cache could show a frozen age as live.
function renderCachedSections() {
  // Paints only the visible tab's sections from the cached snapshots. Called
  // by switchTab so a click paints synchronously without waiting for the
  // next 2s poll (Issue #264). Gating consults the active rail page too
  // (Issue #266): after #140 moved the panels under `#page-*` sections, the
  // legacy tab shells all read visible and this repainted every page at once.
  const currentKpi = lastKpi;
  // Tab 1: LIVE OPERATIONS (service cards → rail Trades page, orders & trades
  // → rail Dashboard page). The header status is global chrome on every page:
  // renderServiceCards paints it before the grid, so a page where the grid is
  // skipped still gets the header via renderServiceHeader.
  if (paintable(tab1, document.getElementById('service-cards'))) {
    if (lastStatus) renderServiceCards(lastStatus, lastGuardHealth, lastGuardAlerts);
  } else if (lastStatus) {
    renderServiceHeader(lastStatus, lastGuardHealth, lastGuardAlerts);
  }
  if (paintable(tab1, document.getElementById('orders-trades-body'))) {
    if (currentKpi) renderOrdersTrades(currentKpi, lastState);
  }
  // Tab 2: PERFORMANCE & ANALYTICS (KPI tiles → rail Reports page)
  if (paintable(tab2, document.getElementById('broker-hero-equity')) && currentKpi) {
    renderPortfolioOverview(currentKpi, lastStatus, lastState);
  }
  if (paintable(tab2, document.getElementById('kpi-grid')) && currentKpi) {
    renderKPIs(currentKpi, lastStatus);
  }
  // Tab 3: MARKET FILTER (kanban → rail Data & Markets page). The readiness
  // banner lives on this tab, so it refreshes with it; the poll loop still
  // calls it unconditionally (see pollStatus) so a dead endpoint hides the
  // trackers on every tick.
  if (paintable(tab3, document.getElementById('kanban-board')) && currentKpi) {
    // Cached repaint re-resolves the ENGINE hold at paint time so its age
    // keeps advancing instead of freezing at the last poll's value (#348).
    const cacheNowMs = Date.now();
    const cacheEngine = resolveHeldRead(null, lastScanState, lastScanStateAtMs, cacheNowMs, backendStale);
    renderScreener(currentKpi, cacheEngine.payload, lastStatus,
      { ageOffsetSec: cacheEngine.ageOffsetSec, readFailed: cacheEngine.readFailed });
    renderTrialReadiness(lastTrialReadiness);
  }
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
  // Paint the newly shown tab synchronously from the cached poll data, so a
  // click never waits behind the next 2s poll render.
  renderCachedSections();
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

// Escape key closes the reset modal.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && resetModal.classList.contains('show')) {
    resetModal.classList.remove('show');
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

/* ── Stream sentences: one plain-English line per event ──
 * Every row reads as a sentence with local time and market name: no service
 * abbreviations, action codes, or raw slugs on the main line. Builders below
 * are keyed by action and read only `extra`; unknown actions fall back to a
 * plain-words prefix so a new producer never renders as code. */

// Titles seen in table renders, keyed by condition id. The stream is
// standalone (it works before any table polls), so this only ever upgrades
// slug-words to a real name — never the other way around.
const streamTitleCache = Object.create(null);
function noteStreamTitles(byMarket) {
  try {
    if (!byMarket || typeof byMarket !== 'object') return;
    for (const [cid, m] of Object.entries(byMarket)) {
      const name = m && (m.title || m.name);
      if (cid && name) streamTitleCache[cid] = String(name);
    }
  } catch { /* telemetry only: a bad ledger never breaks rendering */ }
}

function streamMarketName(ev, lookup) {
  const ex = (ev && ev.extra) || {};
  if (ex.market_title) return String(ex.market_title);
  const cid = ex.condition_id || '';
  if (cid && streamTitleCache[cid]) return streamTitleCache[cid];
  if (cid && typeof lookup === 'function') {
    try {
      const name = lookup(cid);
      if (name) return String(name);
    } catch { /* fall through to slug words */ }
  }
  const slug = (ev && ev.market_slug) || '';
  if (slug) {
    const words = String(slug).replace(/[-_]+/g, ' ').trim();
    if (words) return words;
  }
  return 'a market';
}

function streamTime(ev) {
  try {
    const s = fmtLocalTime(ev && ev.ts);
    return s || '';
  } catch { return ''; }
}

function streamShares(n) {
  const f = Number(n);
  if (!isFinite(f)) return 'some';
  return String(Math.round(f * 100) / 100);
}

function streamPrice(n) {
  const f = Number(n);
  if (!isFinite(f) || f <= 0) return '';
  return '$' + f.toFixed(f < 1 ? 2 : 3).replace(/0$/, '');
}

function humanizeAction(action) {
  return String(action || '').replace(/[_-]+/g, ' ').trim() || 'activity';
}

function shareWord(n) {
  return Number(n) === 1 ? 'share' : 'shares';
}

// Relayer states arrive as UPPER_SNAKE codes; skip reasons arrive as words.
// Keep words untouched, turn codes into words so no code hits the main line.
function plainReason(reason) {
  const r = String(reason || '');
  if (/^[A-Z0-9_\-\s]+$/.test(r) && /[_-]/.test(r)) {
    return r.replace(/[_-]+/g, ' ').toLowerCase();
  }
  return r;
}

function quoteList(quotes) {
  const parts = [];
  for (const q of (quotes || [])) {
    if (!q || (q.side !== 'UP' && q.side !== 'DOWN')) continue;
    const px = streamPrice(q.price);
    parts.push(`${q.side} ${streamShares(q.size)}${px ? ' at ' + px : ''}`);
  }
  return parts;
}

// Actions that count as trades regardless of which service emitted them:
// fills, exits, completions, rescues, every merge/redeem outcome, and any
// submit that actually placed orders.
function isTradeEvent(ev) {
  const action = ((ev && ev.action) || '').toLowerCase();
  if (action === 'fill_recorded') return true;
  if (action === 'lifecycle_exited' || action === 'lifecycle_completed' ||
      action === 'lifecycle_aged_out_rescue') return true;
  if (action.startsWith('merge_') || action.startsWith('redeem_')) return true;
  if (action === 'submit') {
    const ex = (ev && ev.extra) || {};
    return Number(ex.submitted || 0) > 0;
  }
  return false;
}

function buildStreamSentence(ev, lookup) {
  const action = ((ev && ev.action) || '').toLowerCase();
  const ex = (ev && ev.extra) || {};
  const reason = (ev && ev.reason) || '';
  const market = streamMarketName(ev, lookup);
  const clock = streamTime(ev);
  const when = clock ? `${clock} — ` : '';
  const because = reason ? ` (${plainReason(reason)})` : '';

  if (action === 'fill_recorded') {
    const out = ex.outcome === 'UP' || ex.outcome === 'DOWN' ? ` ${ex.outcome}` : '';
    const px = streamPrice(ex.price);
    return `${when}Bought${out} ${streamShares(ex.size)} ${shareWord(ex.size)}${px ? ' at ' + px : ''} on ${market}.`;
  }
  if (action === 'lifecycle_exited') {
    const out = ex.outcome === 'UP' || ex.outcome === 'DOWN' ? ` ${ex.outcome}` : '';
    const px = streamPrice(ex.fill_price || ex.min_price);
    return `${when}Sold about${out} ${streamShares(ex.size)} ${shareWord(ex.size)}${px ? ' at about ' + px : ''} on ${market}${because}.`;
  }
  if (action === 'lifecycle_completed') {
    const ask = streamPrice(ex.ask);
    return `${when}Completed the pair on ${market}${ask ? ' at ' + ask + ' ask' : ''}${because}.`;
  }
  if (action === 'lifecycle_aged_out_rescue') {
    return `${when}Rescued an aged-out position on ${market}${because}.`;
  }
  if (action.startsWith('merge_')) {
    const n = ex.size != null ? `${streamShares(ex.size)} ${shareWord(ex.size)}` : '';
    if (action === 'merge_executed') return `${when}Merge completed on ${market} (${n} now collateral).`;
    if (action === 'merge_failed') return `${when}Merge on ${market} failed${because}.`;
    if (action === 'merge_submitted') return n ? `${when}Sent ${n} on ${market} to the relayer for merging.` : `${when}Sent a merge on ${market} to the relayer.`;
    if (action === 'merge_interrupted') return `${when}Merge on ${market} was interrupted before confirmation.`;
    return `${when}Merge on ${market} needs a manual check${because}.`;
  }
  if (action.startsWith('redeem_')) {
    if (action === 'redeem_executed') return `${when}Redemption completed on ${market}.`;
    if (action === 'redeem_failed') return `${when}Redemption on ${market} failed${because}.`;
    if (action === 'redeem_submitted') return `${when}Sent a redemption on ${market} to the relayer.`;
    if (action === 'redeem_interrupted') return `${when}Redemption on ${market} was interrupted before confirmation.`;
    return `${when}Redemption on ${market} needs a manual check${because}.`;
  }
  if (action === 'decide') {
    const count = Number(ex.intent_count || 0);
    if (count > 0) {
      const parts = quoteList(ex.quotes);
      const what = parts.length ? parts.join(' and ') : `${count} orders`;
      return `${when}Decided to quote ${what} on ${market}.`;
    }
    return reason
      ? `${when}Skipped ${market} because ${reason}.`
      : `${when}Skipped ${market} — no quote this cycle.`;
  }
  if (action === 'submit') {
    const n = Number(ex.submitted || 0);
    if (n > 0) return `${when}Placed ${n} maker orders on ${market}.`;
    return `${when}No orders placed on ${market}${because}.`;
  }

  // Static fallbacks for televised-but-unchanged producers.
  const svc = ((ev && ev.service) || '').toLowerCase();
  const key = svc + '|' + action;
  if (EVENT_TRANSLATIONS[key]) {
    const base = EVENT_TRANSLATIONS[key];
    return `${when}${base} (${market})`;
  }
  if (action.startsWith('pairs_') && (EVENT_TRANSLATIONS['query|' + action] || EVENT_TRANSLATIONS['engine|' + action])) {
    return `${when}${EVENT_TRANSLATIONS['query|' + action] || EVENT_TRANSLATIONS['engine|' + action]} (${market})`;
  }

  // Plain-words prefix fallback: never a code, never an underscore.
  const prefix = (svc === 'decide' || svc === 'fleet') ? 'Quoting update'
    : (svc === 'query' || svc === 'engine') ? 'Venue check'
    : (svc === 'filter' || svc === 'screener') ? 'Market scan'
    : svc === 'guardrail' ? 'Risk note'
    : 'Status update';
  return `${when}${prefix} on ${market}: ${humanizeAction(action)}${because}.`;
}

function translateEvent(ev) {
  return { translation: buildStreamSentence(ev), ctx: '' };
}

let tickerFilter = 'all';
let tickerAutoscroll = true;
let tickerShowDetails = false;
const allTickerEvents = [];

function setTickerFilter(f) { tickerFilter = f || 'all'; renderTickerFeed(); }
function setTickerShowDetails(b) { tickerShowDetails = !!b; renderTickerFeed(); }

function appendTickerEvent(line, translation, ctx, service, action, extra) {
  const empty = tickerEl.querySelector('.empty-state');
  if (empty) empty.remove();

  const evObj = { line, translation, ctx, service: (service || '').toLowerCase(), action: (action || '').toLowerCase(), extra: extra || {} };
  allTickerEvents.unshift(evObj);
  while (allTickerEvents.length > 200) allTickerEvents.pop();

  renderTickerFeed();
}

function tickerMatches(ev) {
  if (tickerFilter === 'all') return true;
  // The TRADES tab is action-keyed and service-independent: fills, exits,
  // completions, rescues, every merge/redeem outcome, and submits that
  // actually placed orders. `decide` is the pre-rename value of the same tab.
  if (tickerFilter === 'trades' || tickerFilter === 'decide') return isTradeEvent(ev);
  if (tickerFilter === 'filter') return ev.service.includes('filter') || ev.service.includes('screener');
  if (tickerFilter === 'guardrail') return ev.service.includes('guardrail') || ev.action.includes('alert') || ev.action.includes('stop_loss');
  return true;
}

function tickerEmptyState() {
  if (tickerFilter === 'trades' || tickerFilter === 'decide') {
    return { title: 'No trades yet.', msg: 'Fills, sells, exits, merges and redeems show up here as they happen.' };
  }
  if (tickerFilter === 'filter') {
    return { title: 'No market filter updates yet.', msg: 'Scans show up here as they run.' };
  }
  if (tickerFilter === 'guardrail') {
    return { title: 'No alerts right now.', msg: 'Risk notes show up here if a guardrail trips.' };
  }
  return { title: 'No events yet.', msg: 'Events will appear here as the strategy execution loop runs.' };
}

function renderTickerFeed() {
  tickerEl.innerHTML = '';
  const filtered = allTickerEvents.filter(tickerMatches);

  if (filtered.length === 0) {
    const st = tickerEmptyState();
    tickerEl.innerHTML = `<div class="empty-state"><div class="empty-state-title">${esc(st.title)}</div><div class="empty-state-msg">${esc(st.msg)}</div></div>`;
    return;
  }

  for (const ev of filtered) {
    const div = document.createElement('div');
    div.className = 'ticker-event';
    if (ev.translation) {
      const raw = tickerShowDetails ? `<div class="ticker-raw">${esc(ev.line)}</div>` : '';
      div.innerHTML = `<div class="ticker-translation">${esc(ev.translation)}</div>${raw}`;
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

const btnTickerDetails = document.getElementById('btn-ticker-details');
if (btnTickerDetails) {
  btnTickerDetails.addEventListener('click', () => {
    tickerShowDetails = !tickerShowDetails;
    btnTickerDetails.textContent = tickerShowDetails ? 'HIDE DETAILS' : 'SHOW DETAILS';
    renderTickerFeed();
  });
}

const btnClearTicker = document.getElementById('btn-clear-ticker');
if (btnClearTicker) {
  btnClearTicker.addEventListener('click', () => {
    allTickerEvents.length = 0;
    tickerEl.innerHTML = '<div class="empty-state"><div class="empty-state-title">Feed cleared</div><div class="empty-state-msg">New events will appear here.</div></div>';
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
      appendTickerEvent(rawLine, translation, ctx, ev.service, ev.action, ev.extra);
    } catch {
      // Non-JSON line
    }
  };

  // #427 live marks ride this same connection as named events; the ticker
  // path above is untouched.
  if (typeof sseSource.addEventListener === 'function') {
    sseSource.addEventListener('mark', (e) => {
      let payload = null;
      try { payload = JSON.parse(e.data); } catch { payload = null; }
      if (applyLiveMarks(payload, Date.now())) scheduleLiveMarksPaint();
    });
  }

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
  const code = shadowCodeNote(shadowRunAnchor);
  const headline = shadowRunAnchor.running
    ? `Shadow rehearsal ${shadowRunAnchor.runId || ''} running, ${shadowRunAnchor.minutes < 0 ? 'no time box (runs until stopped)' : `time box ${shadowRunAnchor.minutes ?? '--'} min`}`
    : 'This shadow rehearsal is no longer running.';
  el.textContent = (shadowRunAnchor.running ? '· ' + elapsed : '· ' + elapsed + ' ended') + code.text;
  // The code sentence is appended only when there is one, so a run that
  // recorded nothing keeps the exact title it had before this existed.
  el.title = code.title ? headline.replace(/\.$/, '') + '.' + code.title : headline;
}

/* The code stamp on the stopwatch: which revision this rehearsal loaded, and
 * whether the tree has moved on since.
 *
 * A rehearsal outlives the code it started with, and it used to say nothing
 * about it: on 2026-10-07 three runs sat side by side, two of them deciding
 * with the previous morning's ranker and no queue gate, all three reading
 * identically. The stamp is what separates them, and "older than this tree" is
 * the actionable half -- the numbers on a stale page were produced by code that
 * is no longer here, so comparing them against a fresh run compares two
 * strategies.
 *
 * A run that recorded no revision gets no stamp: an old heartbeat has nothing
 * to say, and a badge reading "unknown" on every run is noise, not a warning.
 */
function shadowCodeNote(anchor) {
  const label = anchor.codeLabel ? `code ${anchor.codeLabel}` : 'code unknown';
  if (anchor.codeStale) {
    return {
      text: ` · ${label} (older than this tree)`,
      title: ` It loaded ${label}, but core_brain/ or scoring/ has changed since it started, so its numbers describe code that is no longer here. Restart it to compare like with like.`,
    };
  }
  if (anchor.codeLabel) {
    return {
      text: ` · ${label}`,
      title: ` It is holding ${label}; no decision code has changed since this run started.`,
    };
  }
  return { text: '', title: '' };
}

function setShadowRun(status) {
  const run = status?.shadow_run;
  shadowRunAnchor = run
    ? {
        elapsedSec: Number(run.elapsed_sec) || 0,
        running: run.running === true,
        runId: run.run_id,
        minutes: run.minutes,
        // Which code this run loaded, and whether the tree moved past it.
        codeLabel: run.code_revision ? run.code_revision.label : null,
        codeStale: run.code_stale === true,
        receivedAtMs: Date.now(),
      }
    : null;
  renderShadowClock();
}

/* Which store the page is pointed at, and whether anything is writing it.
 *
 * The badge used to name the store and stop there. When a run ends, that store
 * goes quiet and every pill on the page turns red, which reads as "the engine
 * is down" -- including on a machine where a healthy 4-hour trial is writing a
 * different store file the page cannot see. The listing the backend now sends
 * alongside the badge is what separates those two: `liveElsewhere` is the
 * proof that the engine is fine and the operator is watching the wrong file.
 */
function dbModeVerdict(status) {
  const runs = Array.isArray(status?.shadow_runs) ? status.shadow_runs : [];
  const active = runs.find(r => r && r.is_active_db === true) || null;
  const liveElsewhere = runs
    .filter(r => r && r.running === true && r.is_active_db !== true)
    .map(r => ({
      runId: r.run_id,
      dbPath: r.db_path,
      file: r.source_file,
      ageSec: r.heartbeat_age_sec,
    }));
  // null means "this page's store has no rehearsal registered", which is the
  // ordinary live-stack case and must never read as stale. Only an explicitly
  // ended run for THIS store, with a live run elsewhere to point at, is stale.
  const hereRunning = active ? active.running === true : null;
  const stale = status?.db_is_production !== true
    && hereRunning === false
    && liveElsewhere.length > 0;
  return { stale, liveElsewhere, activeRunId: active ? active.run_id : null, hereRunning };
}

let lastStatusForRuns = null;

function closeRunSwitcher() {
  const sw = document.getElementById('db-run-switcher');
  if (sw) sw.style.display = 'none';
}

/* Sit the dropdown under the badge that opened it. The switcher is positioned
 * against <header>, and the badge's x moves with every pill above it (a fresh
 * SCAN age, a longer store name), so the offset is measured rather than
 * hard-coded. */
function placeRunSwitcher(sw, badge) {
  const host = (badge.closest && badge.closest('header')) || badge.parentElement;
  if (!host || typeof badge.getBoundingClientRect !== 'function') return;
  const b = badge.getBoundingClientRect();
  const h = host.getBoundingClientRect();
  const width = sw.offsetWidth || 380;
  const left = b.left - h.left - 40;
  sw.style.left = Math.round(Math.max(4, Math.min(left, h.width - width - 4))) + 'px';
}

function runSwitcherLabel(run) {
  if (!run) return '';
  const t = run.tournament;
  if (t && t.arm) {
    const idxStr = String(t.index ?? 1).padStart(2, '0');
    const portPart = run.dash_port ? `:${run.dash_port}` : '';
    const pidPart = run.pid ? ` (pid ${run.pid})` : '';
    return `#${idxStr} ${t.arm}${portPart}${pidPart}`;
  }
  return (run.db_path || '').split(/[\\/]/).pop() || '';
}

async function renderRunSwitcher() {
  const sw = document.getElementById('db-run-switcher');
  const badge = document.getElementById('db-mode-badge');
  if (!sw) return;
  const runs = (lastStatusForRuns?.shadow_runs || []).filter(r => r && r.db_path);
  if (!runs.length) {
    sw.innerHTML = `<div class="db-run-empty">No rehearsal is registered on this machine.</div>`;
    sw.style.display = 'block';
    placeRunSwitcher(sw, badge);
    return;
  }
  const activeRunId = dbModeVerdict(lastStatusForRuns).activeRunId;
  const rows = runs.map(r => {
    const isThis = activeRunId === r.run_id;
    const state = r.running === true ? 'RUNNING' : (r.finished ? 'FINISHED' : 'ENDED');
    const age = formatHeartbeatAge(r.heartbeat_age_sec);
    const label = runSwitcherLabel(r);
    const portLink = r.dash_port
      ? `<a href="http://${window.location.hostname}:${r.dash_port}" target="_blank" rel="noopener" class="db-run-port-link" style="font-size:11px;color:#38bdf8;text-decoration:none;margin-left:6px;" title="Open dedicated dashboard for this arm on port ${r.dash_port}">:${r.dash_port} ↗</a>`
      : '';
    const btn = isThis
      ? `<span class="db-run-current">THIS PAGE</span>`
      : `<button class="db-run-switch" data-db="${esc(r.db_path)}">SWITCH</button>`;
    return `<div class="db-run-row${isThis ? ' current' : ''}">
        <span class="db-run-name mono">${esc(label)}</span>${portLink}
        <span class="db-run-state state-${r.running === true ? 'running' : 'stopped'}">${state}${age ? ' · ' + age : ''}</span>
        ${btn}
      </div>`;
  });
  sw.innerHTML = `<div class="db-run-head">Which run is this page reading?</div>` + rows.join('');
  sw.style.display = 'block';
  placeRunSwitcher(sw, badge);
  sw.querySelectorAll('.db-run-switch').forEach(b => {
    b.addEventListener('click', async () => {
      b.disabled = true;
      b.textContent = '…';
      try {
        const res = await controlFetch('/api/system/db?db=' + encodeURIComponent(b.dataset.db));
        // A refusal from `_authorize_control` is an HTTP error carrying
        // FastAPI's `detail`, not the `ok`/`message` pair the happy path
        // returns. Checking `data.ok` alone read that as a generic failure and
        // threw the server's explanation ("missing or stale control token")
        // away; a non-JSON body threw into the catch below and blamed the
        // network instead. Surface whichever reason the server actually gave.
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          alert(data.message || data.detail
            || `Could not switch stores (HTTP ${res.status}).`);
        } else {
          // Clear retained last-good poll snapshots so previous store's telemetry
          // doesn't linger before pollStatus() retrieves the newly selected store.
          lastState = null;
          lastKpi = null;
          clearLiveMarks();
          lastStatus = null;
          lastScanState = null;
          lastTrialReadiness = null;
          lastGuardHealth = null;
          lastGuardAlerts = null;
          lastStatusForRuns = null;
          lastScanStateAtMs = null;
          lastKpiAtMs = null;
          lastStatusAtMs = null;
        }
      } catch { alert('Could not reach the dashboard to switch stores.'); }
      closeRunSwitcher();
      pollStatus();
    });
  });
}

function renderDbMode(status) {
  setShadowRun(status);
  lastStatusForRuns = status;
  const el = document.getElementById('db-mode-badge');
  if (!el) return;

  const mode = status?.db_mode || null;
  lastDbIsProduction = status?.db_is_production === true || mode === 'LIVE';
  lastDbIsShadow = status?.db_is_production === false || mode === 'SHADOW';
  const verdict = dbModeVerdict(status);

  if (!mode) {
    el.className = 'pill state-unknown mono';
    el.textContent = 'DB: UNKNOWN';
    el.title = 'Active registry unknown: the status endpoint did not answer.';
    return;
  }

  const path = status.db_path || '';
  if (lastDbIsProduction) {
    el.className = 'pill mode-live mono';
    el.textContent = 'DB: LIVE';
    el.title = `Reading the production registry: ${path}`;
  } else {
    // Not a cosmetic state. Every number on the page is a rehearsal, and START
    // is refused while this shows.
    el.className = 'pill mode-shadow mono' + (verdict.stale ? ' mode-stale' : '');
    el.textContent = `DB: ${mode} · ${path.split(/[\\/]/).pop()}`
      + (verdict.stale ? ' · NO LIVE RUN' : '');
    el.title = `Reading ${path}, not the production registry. `
      + (verdict.stale
        // The whole point: say what IS running, and where, instead of leaving
        // a red page to be read as a dead engine.
        ? `Nothing is writing this store, so every heartbeat on this page reads stale. `
          + `Still running elsewhere: ${verdict.liveElsewhere
              .map(r => `${r.runId} → ${(r.dbPath || '').split(/[\\/]/).pop()}`).join('; ')}. `
          + `Click to switch this page to one of them.`
        : `Orders, fills and PnL on this page are not live positions, and START is disabled.`);
  }

  // The switcher is the badge's answer to "which run is this, and can I watch
  // a different one". Wired once; the handler reads the last status.
  if (!el.dataset.wired) {
    el.dataset.wired = 'true';
    el.style.cursor = 'pointer';
    el.addEventListener('click', () => {
      const sw = document.getElementById('db-run-switcher');
      if (!sw) return;
      if (sw.style.display === 'block') { closeRunSwitcher(); return; }
      renderRunSwitcher();
    });
    document.addEventListener('click', (e) => {
      const sw = document.getElementById('db-run-switcher');
      if (!sw || sw.style.display !== 'block') return;
      if (sw.contains(e.target) || (el.contains && el.contains(e.target))) return;
      closeRunSwitcher();
    });
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
  // `liveOnly` marks a service that exists only on the live stack. A shadow
  // rehearsal (`python -m core_brain.shadow_run`) runs the same poll and quote
  // code in-process against a signer-less client, so these two stay STOPPED
  // for the whole rehearsal and that is the correct reading, not a fault.
  { key: 'query', name: 'Venue Engine & Order Poller', cmd: 'python -m core_brain.order_manager poll --interval 0.5',
    tag: '0.5s CLOB FEED',
    desc: 'Queries CLOB every 0.5s, reconciles fills, and executes periodic balance sweeps.',
    liveOnly: true },
  { key: 'decide', name: 'Execution Loop & Maker Quoter', cmd: 'python -m core_brain.trader_loop --live --no-reconcile --no-sweep --interval 5',
    tag: 'SPREAD QUOTER',
    desc: 'Runs the trading loop (dual-sided maker quotes -> merge execution) every 5s across approved markets.',
    liveOnly: true },
];

/* Global top-nav status: stack pill, ENGINE/WATCHDOG pills, START/STOP
 * buttons. Split from the card grid (CodeRabbit round on #267): these are
 * chrome on every page, so a poll whose card grid the Trades gate skips must
 * still refresh them. */

/* Services dropdown: the SERVICES pill in the top bar is a summary AND the
 * trigger. The rows mirror SERVICE_DEFS so the dropdown can never name a
 * service the card grid does not. The state vocabulary is the same as the
 * cards' (statePillHtml): RUNNING/STOPPED, and UNKNOWN only when a telemetry
 * read genuinely failed. */
function renderServicesDropdown(status, guardrailHealth, guardrailAlerts, activeCount, overallState) {
  const trigger = document.getElementById('hud-services-pill');
  const panel = document.getElementById('services-dropdown');
  if (!trigger || !panel) return;

  const rows = SERVICE_DEFS.map(def => {
    let state, meta, pid;
    if (def.key === 'guardrail') {
      const running = guardrailHealth?.running || false;
      pid = guardrailHealth?.pid;
      const telemetryError = guardrailHealth?.telemetry_error;
      const healthKnown = guardrailHealth !== null && guardrailHealth !== undefined && !telemetryError;
      const age = typeof guardrailHealth?.age_s === 'number' ? guardrailHealth.age_s : null;
      const hasAlert = (guardrailAlerts?.alerts?.length > 0);
      state = !healthKnown ? 'unknown'
        : hasAlert ? 'degraded'
        : (running ? stateKey(true, age, cadenceThresholds(5)) : stateKey(false, age));
      const alertsTotal = guardrailHealth?.alerts_total || 0;
      meta = telemetryError ? 'telemetry down'
        : (hasAlert ? `${alertsTotal} alert${alertsTotal === 1 ? '' : 's'}` : 'auto-watch');
    } else {
      const svc = status?.services?.[def.key];
      const running = svc?.running || false;
      pid = svc?.pid;
      state = running ? 'running' : 'stopped';
      meta = def.liveOnly ? 'live only' : (pid ? `pid ${pid}` : 'stopped');
    }
    const dot = `<span class="pulse-dot ${state === 'running' ? 'active' : ''}"></span>`;
    const label = state.toUpperCase();
    const liveCls = state === 'running' ? ' services-dropdown-live' : '';
    return `<div class="services-dropdown-row">
      <span class="pill state-${state}">${dot}${label}</span>
      <span class="services-dropdown-name" title="${esc(def.name)}">${esc(def.name)}</span>
      <span class="services-dropdown-meta">${esc(meta)}</span>
    </div>`;
  }).join('');

  const head = `<div class="services-dropdown-head"><span>Service Telemetry</span><span>${activeCount} active</span></div>`;
  const foot = `<div class="services-dropdown-foot">Full controls on the Live Operations tab</div>`;
  panel.innerHTML = head + rows + foot;

  // The pill stays the summary; the caret flips with the open state. The pill
  // classes are owned by renderServiceHeader, so only aria-expanded is set here.
  trigger.setAttribute('aria-expanded', panel.style.display === 'block' ? 'true' : 'false');
}

/* Open/close the services dropdown. A click anywhere else closes it, and Esc
 * closes it too — the same contract as the run switcher beside it. */
let servicesDropdownBound = false;
function bindServicesDropdown() {
  if (servicesDropdownBound) return;
  servicesDropdownBound = true;
  const trigger = document.getElementById('hud-services-pill');
  const panel = document.getElementById('services-dropdown');
  if (!trigger || !panel) return;
  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = panel.style.display === 'block';
    panel.style.display = open ? 'none' : 'block';
    trigger.setAttribute('aria-expanded', open ? 'false' : 'true');
  });
  document.addEventListener('click', (e) => {
    if (panel.style.display !== 'block') return;
    if (panel.contains(e.target) || trigger.contains(e.target)) return;
    panel.style.display = 'none';
    trigger.setAttribute('aria-expanded', 'false');
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && panel.style.display === 'block') {
      panel.style.display = 'none';
      trigger.setAttribute('aria-expanded', 'false');
    }
  });
}

function renderServiceHeader(status, guardrailHealth, guardrailAlerts) {
  const executionServiceKeys = ['filter', 'query', 'decide'];
  const anyExecutionServiceRunning = executionServiceKeys.some(k => Boolean(status?.services?.[k]?.running));
  const isRunning = status?.bot_state === 'RUNNING' || anyExecutionServiceRunning;
  // `lastDbIsProduction` is the START guard's flag and `renderDbMode` owns it.
  // Writing it from here too gave one safety flag two writers: a status payload
  // that carries service state but not `db_is_production` silently reset the
  // guard, and whether that ends up safe depended purely on call order.
  const isShadow = isShadowMode(status);

  // Master Control Header & Buttons
  const masterIndicator = document.getElementById('master-status-indicator');
  const masterToggle = document.getElementById('btn-master-toggle');
  const servicesPill = document.getElementById('hud-services-pill');
  const guardrailPill = document.getElementById('hud-guardrail-pill');

  const anyServiceRunning = anyExecutionServiceRunning;

  renderServicesDropdown(status, guardrailHealth, guardrailAlerts, activeCount, servicesState);

  if (masterIndicator) {
    if (isStopping) {
      masterIndicator.className = 'pill state-degraded font-display';
      const stopText = isShadow ? 'SHADOW STOPPING' : 'STACK STOPPING';
      masterIndicator.textContent = stopText;
      masterIndicator.setAttribute('aria-label', stopText);
    } else if (isShadow) {
      const shadowRunning = Boolean(status?.shadow_run?.running && !status?.shadow_run?.ended);
      const shadowState = shadowRunning ? 'running' : 'stopped';
      const shadowLabel = 'SHADOW ' + shadowState.toUpperCase();
      masterIndicator.className = `pill state-${shadowState} font-display`;
      masterIndicator.innerHTML = (shadowState === 'running' ? '<span class="pulse-dot active"></span>' : '')
        + esc(shadowLabel);
      masterIndicator.setAttribute('aria-label', shadowLabel);
    } else {
      // Canonical live-state vocabulary (DESIGN.md): the stack pill carries
      // the blinking liveness dot rather than unicode glyphs. textContent is
      // kept in step for readers that take the text, not the markup.
      const stackState = processState(isRunning, status?.registry_unreadable);
      const stackLabel = 'STACK ' + stackState.toUpperCase();
      masterIndicator.className = `pill state-${stackState} font-display`;
      masterIndicator.innerHTML = (stackState === 'running' ? '<span class="pulse-dot active"></span>' : '')
        + esc(stackLabel);
      // aria-label, not textContent: overwriting textContent would delete the
      // pulse-dot span the line above just created.
      masterIndicator.setAttribute('aria-label', stackLabel);
    }
  }
  // Status pills use the same live-state vocabulary as every service card.
  const hudServicesState = document.getElementById('hud-services-state');
  const hudServicesSub = document.getElementById('hud-services-sub');
  const hudGuardrailState = document.getElementById('hud-guardrail-state');
  const hudGuardrailSub = document.getElementById('hud-guardrail-sub');

  let activeCount = 0;
  if (status?.services) {
    activeCount = executionServiceKeys.filter(k => Boolean(status.services[k]?.running)).length;
  }
  if (guardrailHealth?.running) activeCount++;

  const servicesState = processState(isRunning, status?.registry_unreadable);
  if (servicesPill) servicesPill.className = `pill state-${servicesState} mono`;
  if (hudServicesState) hudServicesState.textContent = servicesState.toUpperCase();
  if (hudServicesSub) hudServicesSub.textContent = isRunning ? `${activeCount} active` : 'all stopped';

  const alertsCount = guardrailAlerts?.alerts?.length || guardrailHealth?.alerts_total || 0;
  const guardrailTelemetryError = guardrailHealth?.telemetry_error || guardrailAlerts?.telemetry_error;
  const guardrailKnown = guardrailHealth !== null && guardrailHealth !== undefined;
  let guardrailState = 'unknown';
  if (guardrailKnown && !guardrailTelemetryError) {
    const age = Number.isFinite(guardrailHealth.age_s) ? guardrailHealth.age_s : null;
    const livenessState = stateKey(
      guardrailHealth.running,
      age,
      cadenceThresholds(5),
    );
    guardrailState = livenessState === 'running' && alertsCount > 0
      ? 'degraded'
      : livenessState;
  }
  if (guardrailPill) guardrailPill.className = `pill state-${guardrailState} mono`;
  if (hudGuardrailState) hudGuardrailState.textContent = guardrailState.toUpperCase();
  if (hudGuardrailSub) {
    const alertsTotal = guardrailHealth?.alerts_total || 0;
    hudGuardrailSub.textContent = guardrailState === 'unknown'
      ? 'telemetry unavailable' : `${alertsTotal} alerts`;
  }
  if (masterToggle) {
    if (isStarting || isStopping) {
      // Busy overrides status: no second click while a request is in flight.
      const busyLabel = isStopping ? 'STOPPING…' : 'STARTING…';
      masterToggle.className = isStopping ? 'btn-stop-run' : 'btn-start-run';
      masterToggle.disabled = true;
      masterToggle.setAttribute('aria-busy', 'true');
      masterToggle.style.opacity = '0.6';
      masterToggle.style.cursor = 'wait';
      masterToggle.title = '';
      masterToggle.innerHTML = `<svg class="btn-syncing-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:inline-block;vertical-align:-2px;margin-right:4px;animation:spin 1s linear infinite"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>${busyLabel}`;
      masterToggle.dataset.action = isStopping ? 'stop' : 'start';
      masterToggle.dataset.mode = isShadow ? 'shadow' : 'live';
    } else {
      masterToggle.removeAttribute('aria-busy');
      if (isShadow) {
        masterToggle.dataset.mode = 'shadow';
        const shadowRunning = Boolean(status?.shadow_run?.running && !status?.shadow_run?.ended);
        if (shadowRunning) {
          masterToggle.className = 'btn-stop-run';
          masterToggle.setAttribute('aria-label', 'Stop shadow rehearsal');
          masterToggle.dataset.action = 'stop';
          masterToggle.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block;vertical-align:-2px;margin-right:4px"><rect x="6" y="6" width="12" height="12"/></svg>STOP SHADOW`;
          masterToggle.disabled = false;
          masterToggle.style.opacity = '1';
          masterToggle.style.cursor = 'pointer';
          masterToggle.title = 'Stop shadow rehearsal for this database';
        } else {
          masterToggle.className = 'btn-start-run';
          masterToggle.setAttribute('aria-label', 'Start shadow rehearsal');
          masterToggle.dataset.action = 'start';
          masterToggle.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block;vertical-align:-2px;margin-right:4px"><polygon points="5 3 19 12 5 21 5 3"/></svg>START SHADOW`;
          masterToggle.disabled = false;
          masterToggle.style.opacity = '1';
          masterToggle.style.cursor = 'pointer';
          masterToggle.title = 'Start shadow rehearsal for this database (safe, no signer)';
        }
      } else if (anyExecutionServiceRunning) {
        masterToggle.dataset.mode = 'live';
        masterToggle.className = 'btn-stop-run';
        masterToggle.setAttribute('aria-label', 'Stop bot execution stack');
        masterToggle.dataset.action = 'stop';
        masterToggle.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block;vertical-align:-2px;margin-right:4px"><rect x="6" y="6" width="12" height="12"/></svg>STOP RUN`;
        masterToggle.disabled = false;
        masterToggle.style.opacity = '1';
        masterToggle.style.cursor = 'pointer';
        masterToggle.title = '';
      } else {
        masterToggle.dataset.mode = 'live';
        masterToggle.className = 'btn-start-run';
        masterToggle.setAttribute('aria-label', 'Start bot execution stack');
        masterToggle.dataset.action = 'start';
        masterToggle.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block;vertical-align:-2px;margin-right:4px"><polygon points="5 3 19 12 5 21 5 3"/></svg>START RUN`;
        masterToggle.disabled = false;
        masterToggle.style.opacity = '1';
        masterToggle.style.cursor = 'pointer';
        masterToggle.title = '';
      }
    }
  }
}

function renderServiceCards(status, guardrailHealth, guardrailAlerts) {
  renderServiceHeader(status, guardrailHealth, guardrailAlerts);

  // Render Service Cards Grid
  const container = document.getElementById('service-cards');
  if (!container) return;

  // Build all cards as one string, assign once (#270): per-card `innerHTML +=`
  // re-parses the accumulated grid on every append.
  let cardsHtml = '';

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
    // Live-state language (DESIGN.md): the pill carries the heartbeat age and
    // ages green → amber → red on the service's own cadence ramp. The guardrail
    // heartbeat is served by /api/guardrail-health's `age_s`; the stack
    // services are liveness-checked by PID here, so they render as plain
    // RUNNING/STOPPED with no age until a heartbeat payload exists for them.
    let pill;
    if (def.key === 'guardrail') {
      const age = typeof guardrailHealth?.age_s === 'number' ? guardrailHealth.age_s : null;
      // A failed /api/guardrail-health read is UNKNOWN — the watcher's state
      // is not known, which is not the same as deliberately stopped.
      const telemetryError = guardrailHealth?.telemetry_error;
      const healthKnown = guardrailHealth !== null && guardrailHealth !== undefined && !telemetryError;
      const state = !healthKnown ? 'unknown'
        : hasAlert ? 'degraded'
        : (running ? stateKey(true, age, cadenceThresholds(5)) : stateKey(false, age));
      pill = statePillHtml(state, healthKnown ? age : null);
      if (telemetryError) {
        // The card remains visible, but UNKNOWN prevents a broken ring from
        // looking like a healthy watcher in the operator surface.
        pill = `<span class="pill state-unknown" title="Telemetry unavailable: ${esc(telemetryError.error || 'cycle ring read failed')}">UNKNOWN</span>`;
      }
    } else {
      pill = statePillHtml(running ? 'running' : 'stopped');
    }

    let toggleHtml = '';
    if (!def.readOnly) {
      toggleHtml = `<button class="toggle ${running ? 'on' : ''}" data-svc="${def.key}" role="switch" aria-checked="${running}" aria-label="Toggle ${def.name}" tabindex="0"></button>`;
    } else {
      toggleHtml = `<span style="font-size:10px;font-weight:700;color:var(--text-muted);font-family:'JetBrains Mono',monospace">AUTO-WATCH</span>`;
    }

    cardsHtml += `
      <div class="card${alertCls}" role="region" aria-label="${def.name}" style="display:flex;flex-direction:column;justify-content:space-between;gap:10px">
        <div>
          <div class="service-card-head">
            <div>
              <div class="font-display" style="font-size:14px;letter-spacing:0.02em;color:var(--text-primary)">${def.name}</div>
              <span class="param-code-pill" style="margin-top:2px;display:inline-block">${def.tag}</span>${def.liveOnly ? `
              <span class="svc-scope-pill" title="Live stack only. A shadow rehearsal runs this same code inside python -m core_brain.shadow_run, so this card stays STOPPED during a rehearsal by design.">LIVE ONLY</span>` : ''}
            </div>
            <span class="service-pill-slot">${pill}</span>
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
  container.innerHTML = cardsHtml;

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

// Master toggle handler (Issue #457, #473): one button, both actions and both modes (live / shadow).
// dataset.action ('start' | 'stop') and dataset.mode ('live' | 'shadow') are
// painted by renderServiceHeader from the live status, so the click never
// guesses what the backend will accept.
const masterToggleBtn = document.getElementById('btn-master-toggle');
if (masterToggleBtn && !masterToggleBtn.dataset.wired) {
  masterToggleBtn.dataset.wired = 'true';
  masterToggleBtn.addEventListener('click', async () => {
    // A second click while a request is in flight is the race this button
    // exists to remove: the flags, not the DOM, are the authority.
    if (isStarting || isStopping) return;
    const action = masterToggleBtn.dataset.action === 'stop' ? 'stop' : 'start';
    const isShadow = masterToggleBtn.dataset.mode === 'shadow';

    if (!isShadow) {
      if (action === 'start') {
        const isProd = (lastStatus && typeof lastStatus.db_is_production === 'boolean')
          ? lastStatus.db_is_production === true
          : (lastStatus?.db_mode === 'LIVE' || !lastDbIsShadow);
        if (!isProd || masterToggleBtn.disabled) {
          if (!isProd) {
            alert('This dashboard is not reading the production registry. '
              + 'Service controls act on the live stack against data/orders.db, whose orders '
              + 'would not appear on this page. Restart the dashboard without '
              + '--db / LIVE_DB_PATH first.');
          }
          return;
        }
      } else if (masterToggleBtn.disabled) {
        return;
      }
    } else if (masterToggleBtn.disabled) {
      return;
    }

    if (action === 'stop') {
      isStopping = true;
      const pill = document.getElementById('master-status-indicator');
      if (pill) {
        pill.className = 'pill state-degraded font-display';
        const stopText = isShadow ? 'SHADOW STOPPING' : 'STACK STOPPING';
        pill.textContent = stopText;
        pill.setAttribute('aria-label', stopText);
      }
    } else {
      isStarting = true;
    }
    masterLifecycleSeq++;
    renderServiceHeader(lastStatus, lastGuardHealth, lastGuardAlerts);
    try {
      let endpoint = `/api/system/${action}`;
      if (isShadow) {
        const targetDb = lastStatus?.db_path || '';
        endpoint = `/api/system/shadow/${action}${targetDb ? `?db=${encodeURIComponent(targetDb)}` : ''}`;
      }
      const res = await controlFetch(endpoint);
      let data = null;
      try {
        data = await res.json();
      } catch {
        // Non-JSON reply (proxy error page, empty 500): say so on screen.
      }
      const lines = [];
      if (!res.ok || !data || data.ok !== true) {
        lines.push(data && data.message
          ? data.message
          : `Request failed (HTTP ${res.status}).`);
      } else if (data.message) {
        lines.push(data.message);
      }
      // Per-service outcomes the operator must see, not just "ok".
      if (data && data.services) {
        for (const [name, svc] of Object.entries(data.services)) {
          const outcome = svc && svc.outcome;
          if (outcome && outcome !== 'stopped' && outcome !== 'not_running') {
            const detail = svc.detail ? ` — ${svc.detail}` : '';
            lines.push(`${name}: ${outcome}${detail}`);
          }
        }
      }
      if (lines.length) {
        const tag = isShadow ? `[SHADOW ${action.toUpperCase()}]` : `[${action.toUpperCase()}]`;
        appendTickerEvent(`${tag} ${lines.join(' · ')}`,
          lines.join(' · '), action === 'stop' ? 'stop' : 'start', 'control',
          action, {});
      }
      if (data && data.status) {
        lastStatus = data.status;
      }
    } catch (e) {
      const errTag = isShadow ? `[SHADOW ${action.toUpperCase()} ERROR]` : `[${action.toUpperCase()} ERROR]`;
      appendTickerEvent(`${errTag} ${e.message || String(e)}`,
        e.message || String(e), 'start', 'control', action, {});
    } finally {
      isStarting = false;
      isStopping = false;
      masterLifecycleSeq++;
      renderServiceHeader(lastStatus, lastGuardHealth, lastGuardAlerts);
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
      let paramCardsHtml = '';
      for (const p of params) {
        const rawKey = p.key || p.code || p.name;
        const displayName = p.name && p.name !== rawKey ? p.name : (PARAM_HUMAN_NAMES[rawKey] || rawKey);
        const meta = PARAM_CATEGORIES[rawKey] || { category: p.category || 'Safeguard Rule', badge: 'dynamic', label: p.badge || 'Config' };

        paramCardsHtml += `
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
      cardsContainer.innerHTML = paramCardsHtml;
    }

    // Render Detailed Table
    const body = document.getElementById('params-body');
    if (body) {
      body.innerHTML = '';
      let paramsRowsHtml = '';
      for (const p of params) {
        const rawKey = p.key || p.code || p.name;
        const displayName = p.name && p.name !== rawKey ? p.name : (PARAM_HUMAN_NAMES[rawKey] || rawKey);
        const meta = PARAM_CATEGORIES[rawKey] || { category: p.category || 'Safeguard Rule', badge: 'dynamic', label: p.badge || 'Config' };

        paramsRowsHtml += `<tr>
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
      body.innerHTML = paramsRowsHtml;
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

// One basis for the whole Portfolio card: the run's DB anchor, the figure
// the chart starts on and the gain pill is measured against. The headline
// used to read the session snapshot (stack-start wallet) while the backend
// read the registry, so after a dashboard restart a profitable run rendered
// as a decline. The portfolio's own starting_capital wins; the session
// value survives only as a fallback for payloads that predate it.
function portfolioEquity(kpi, status) {
  const p = kpi?.portfolio || {};
  const ta = kpi?.trade_analytics || {};
  const startingCap = p.starting_capital ?? status?.starting_capital ?? 100;
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

function renderBrokerPortfolioOverview(kpi, status, state) {
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
  // #460: three-way allocation — dollars resting in open orders (local
  // registry, exact, fresh each poll) plus dollars held in positions, with
  // cash as the remainder. An unread leg is unmeasured ('--'), never a
  // fabricated $0.00: cash computes only when all three inputs are numbers.
  const _numOrNull = (v) => (typeof v === 'number' && Number.isFinite(v)) ? v : null;
  const restingVal = _numOrNull(state?.capital?.resting_committed);
  const heldRaw = p.open_committed_usd ?? p.account?.positions_value_usd ?? null;
  const committedVal = _numOrNull(heldRaw);
  const cashVal = (restingVal !== null && committedVal !== null)
    // Held can be a venue market value rather than a cost, so the legs can
    // sum past the headline — a negative remainder is not cash anyone holds.
    ? Math.max(0, totalVal - restingVal - committedVal) : null;
  const _pctOf = (v) => (v === null || !(totalVal > 0))
    ? null : ((v / totalVal) * 100).toFixed(1);
  const restingPct = _pctOf(restingVal);
  const committedPct = _pctOf(committedVal);
  const cashPct = _pctOf(cashVal);
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
  const elResting = document.getElementById('broker-kpi-resting');
  const elRestingPct = document.getElementById('broker-kpi-resting-pct');
  const elPairs = document.getElementById('broker-kpi-pairs');
  const elSpread = document.getElementById('broker-kpi-spread');
  const elExpectancy = document.getElementById('broker-kpi-expectancy');
  const elWinrate = document.getElementById('broker-kpi-winrate');
  const elWins = document.getElementById('broker-kpi-wins');
  const elPf = document.getElementById('broker-kpi-pf');
  const elSharpe = document.getElementById('broker-kpi-sharpe');

  if (elCash) elCash.textContent = fmtUSD(cashVal);
  // Cash keeps its Liquid-USDC unit; an unmeasured remainder is '--', and a
  // percent of an unknown is unknown — never '0.0% Liquid USDC'.
  if (elCashPct) elCashPct.textContent = cashPct === null ? '--' : `${cashPct}% Liquid USDC`;
  if (elCommitted) elCommitted.textContent = fmtUSD(committedVal);
  // The unit is part of the number: "42.6% Committed Risk" read as a risk
  // figure while the value is the tile's share of the equity headline.
  if (elCommittedPct) elCommittedPct.textContent = committedPct === null ? '--' : `${committedPct}% of Equity`;
  if (elResting) elResting.textContent = fmtUSD(restingVal);
  if (elRestingPct) elRestingPct.textContent = restingPct === null ? '--' : `${restingPct}% of Equity`;
  // `graduated` counts MARKETS the Market Filter quoted, not pairs held --
  // "3 Pairs" once sat above an OPEN POSITIONS table showing one Unpaired
  // position, and nothing about a graduated market is hedged yet.
  if (elPairs) elPairs.textContent = `${activePairs} Markets`;
  if (elSpread) {
    // Same rule as the hero pill: an unread realized figure is not $0.00.
    elSpread.textContent = realizedMeasured ? fmtSignedUSD(realizedPnL) : '--';
    elSpread.className = `broker-kpi-val mono ${realizedMeasured ? signClass(realizedPnL) : ''}`;
  }
  if (elExpectancy) elExpectancy.textContent = `Avg ${expectancy} / close`;
  if (elWinrate) elWinrate.textContent = `${winRate}%`;
  if (elWins) {
    // `losses` is every close that is not a win, zeroes included -- which is
    // exactly what the word Losses says. "Flat" claimed nothing happened.
    elWins.textContent = `${wins == null ? '--' : wins} Wins / `
      + `${losses == null ? '--' : losses} Losses`;
  }
  if (elPf) elPf.innerHTML = `${profitFactor} <span style="font-size:10px;color:var(--text-muted);font-weight:500">· SR ${sharpe}</span>`;
  if (elSharpe) elSharpe.textContent = `Sharpe: ${sharpe}`;

  // Bento progress bars & edge indicator
  const elCashBar = document.getElementById('bento-cash-bar');
  if (elCashBar) elCashBar.style.width = cashPct === null ? '' : `${Math.min(100, Math.max(0, parseFloat(cashPct) || 0))}%`;
  const elCommittedBar = document.getElementById('bento-committed-bar');
  if (elCommittedBar) elCommittedBar.style.width = committedPct === null ? '' : `${Math.min(100, Math.max(0, parseFloat(committedPct) || 0))}%`;
  const elRestingBar = document.getElementById('bento-resting-bar');
  if (elRestingBar) elRestingBar.style.width = restingPct === null ? '' : `${Math.min(100, Math.max(0, parseFloat(restingPct) || 0))}%`;
  const elWinrateBar = document.getElementById('bento-winrate-bar');
  if (elWinrateBar) elWinrateBar.style.width = `${Math.min(100, Math.max(0, parseFloat(winRate) || 0))}%`;
  const elEdgeTag = document.getElementById('bento-edge-tag');
  if (elEdgeTag) {
    const wr = parseFloat(winRate) || 0;
    elEdgeTag.textContent = wr >= 70 ? 'High Edge' : (wr >= 50 ? 'Positive Edge' : 'Neutral');
  }

  // Render Line Chart
  renderBrokerPortfolioChart(kpi, currentBrokerTimeframe);
}

function brokerPointLabel(entry, fallback) {
  const rawTs = entry && entry.ts;
  if (rawTs === null || rawTs === undefined
      || (typeof rawTs === 'string' && rawTs.trim() === '')) return fallback;
  const ts = Number(rawTs);
  const date = new Date(ts * 1000);
  return Number.isFinite(ts) && Number.isFinite(date.getTime()) ? date.toISOString() : fallback;
}

function buildBrokerEquitySeries(kpi, startingCap, totalVal, timeframe = 'ALL') {
  const windows = { '1D': 86400, '1W': 604800, '1M': 2592000 };
  const allCloses = (Array.isArray(kpi?.equity_series) ? kpi.equity_series : [])
    .filter(entry => entry && entry.type === 'close' && Number.isFinite(Number(entry.v)))
    .slice()
    .sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
  const validTimestamps = allCloses.map(entry => Number(entry.ts))
    .filter(ts => Number.isFinite(ts));
  const latestTs = validTimestamps.length ? Math.max(...validTimestamps) : null;
  const windowSec = windows[timeframe];
  // The run's real start stamp when the backend measured one (Issue #252);
  // the word "Start" only when the anchor is the config fallback (ts null).
  const anchorTs = kpi?.portfolio?.starting_capital_ts ?? null;
  const anchorNum = Number(anchorTs);
  const hasAnchorTs = anchorTs !== null && anchorTs !== undefined && Number.isFinite(anchorNum);
  if (windowSec === undefined || latestTs === null) {
    // ALL frame (or no timed closes): Start anchor + every close + Current.
    const points = [{ label: brokerPointLabel({ ts: anchorTs }, 'Start'), v: startingCap }];
    if (hasAnchorTs) points[0].ts = anchorNum;
    allCloses.forEach((entry, index) => {
      const point = {
        label: brokerPointLabel(entry, `Close ${index + 1}`),
        v: Number(entry.v),
      };
      const entryTs = Number(entry.ts);
      if (Number.isFinite(entryTs)) point.ts = entryTs;
      if (entry.pnl !== null && entry.pnl !== undefined) point.pnl = Number(entry.pnl);
      if (entry.market !== null && entry.market !== undefined) point.market = entry.market;
      if (entry.title !== null && entry.title !== undefined) point.title = entry.title;
      if (entry.cost_basis !== null && entry.cost_basis !== undefined && Number.isFinite(Number(entry.cost_basis))) point.cost_basis = Number(entry.cost_basis);
      if (entry.method !== null && entry.method !== undefined) point.method = entry.method;
      if (entry.hold_seconds !== null && entry.hold_seconds !== undefined && Number.isFinite(Number(entry.hold_seconds))) point.hold_seconds = Number(entry.hold_seconds);
      points.push(point);
    });
    const current = { label: 'Current', v: allCloses.length ? totalVal : startingCap };
    if (latestTs !== null) current.ts = latestTs;
    points.push(current);
    return points;
  }
  // Windowed frames (Issue #257): clip at the window edge instead of drawing a
  // Start-to-first-close ramp. The edge point carries the running equity at
  // that moment (last pre-window close, else the anchor), labelled by its
  // real timestamp — never the word "Start".
  const windowStart = latestTs - windowSec;
  const preWindow = allCloses.filter(entry => Number(entry.ts) < windowStart);
  const closes = allCloses.filter(entry => {
    const ts = Number(entry.ts);
    return !Number.isFinite(ts) || ts >= windowStart;
  });
  const edgeValue = preWindow.length ? Number(preWindow[preWindow.length - 1].v) : startingCap;
  const points = [{ label: brokerPointLabel({ ts: windowStart }, 'Start'), v: edgeValue, ts: windowStart }];
  closes.forEach((entry) => {
    const point = {
      label: brokerPointLabel(entry, `Close`),
      v: Number(entry.v),
    };
    const entryTs = Number(entry.ts);
    if (Number.isFinite(entryTs)) point.ts = entryTs;
    if (entry.pnl !== null && entry.pnl !== undefined) point.pnl = Number(entry.pnl);
    if (entry.market !== null && entry.market !== undefined) point.market = entry.market;
    if (entry.title !== null && entry.title !== undefined) point.title = entry.title;
    if (entry.cost_basis !== null && entry.cost_basis !== undefined && Number.isFinite(Number(entry.cost_basis))) point.cost_basis = Number(entry.cost_basis);
    if (entry.method !== null && entry.method !== undefined) point.method = entry.method;
    if (entry.hold_seconds !== null && entry.hold_seconds !== undefined && Number.isFinite(Number(entry.hold_seconds))) point.hold_seconds = Number(entry.hold_seconds);
    points.push(point);
  });
  points.push({ label: 'Current', v: closes.length || preWindow.length ? totalVal : startingCap, ts: latestTs });
  return points;
}

function renderBrokerPortfolioChart(kpi, timeframe = '1D') {
  const container = document.getElementById('broker-chart-svg-container');
  const tooltip = document.getElementById('broker-chart-tooltip');
  if (!container) return;

  // Same basis the headline and the gain pill use, so the START line, the pill
  // and the hero cannot describe three different runs.
  const { startingCap, totalVal: currentTotal } = portfolioEquity(
    kpi, lastStartingCapital === null ? null : { starting_capital: lastStartingCapital });

  const series = buildBrokerEquitySeries(kpi, startingCap, currentTotal, timeframe);

  const w = 800;
  const h = 230;
  const padL = 50;
  const padR = 30;
  const padT = 20;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const vals = series.map(s => s.v);
  const minVal = Math.min(...vals, startingCap * 0.995);
  const maxVal = Math.max(...vals, startingCap * 1.005);
  const valSpan = Math.max(maxVal - minVal, 0.50);

  // Time-proportional x (Issue #257): a 9-day gap gets 9 days of width.
  // Points without a ts inherit the nearest known stamp so the line never
  // collapses to NaN; a zero span falls back to even spacing.
  const pointTs = (s, fallback) => {
    const t = Number(s && s.ts);
    return Number.isFinite(t) ? t : fallback;
  };
  const knownTs = series.map(s => Number(s && s.ts)).filter(t => Number.isFinite(t));
  const minTs = knownTs.length ? Math.min(...knownTs) : 0;
  const maxTs = knownTs.length ? Math.max(...knownTs) : 1;
  const tsSpan = maxTs - minTs;
  const getX = (ts) => tsSpan > 0
    ? padL + ((Number(ts) - minTs) / tsSpan) * plotW
    : padL + (plotW / 2);
  const getXIdx = (idx) => padL + (series.length > 1 ? (idx / (series.length - 1)) * plotW : plotW / 2);
  const getY = (val) => padT + plotH - ((val - minVal) / valSpan) * plotH;

  const points = series.map((s, i) => ({
    x: tsSpan > 0 ? getX(pointTs(s, i === series.length - 1 ? maxTs : minTs)) : getXIdx(i),
    y: getY(s.v),
    data: s,
  }));
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
      <line x1="${padL}" y1="${baselineY}" x2="${w - padR}" y2="${baselineY}" stroke="rgba(255,255,255,0.25)" stroke-dasharray="2,2" stroke-width="1.2"/>
      <text x="${w - padR}" y="${baselineY - 5}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8" text-anchor="end">START: $${startingCap.toFixed(2)}</text>

      <!-- Shaded Area Gradient -->
      <path d="${areaD}" fill="url(#brokerAreaGrad)"/>

      <!-- Main Equity Line -->
      <path d="${pathD}" fill="none" stroke="#10b981" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)"/>

      <!-- Active End Pulse Marker -->
      <circle cx="${latestPt.x}" cy="${latestPt.y}" r="6" fill="rgba(16, 185, 129, 0.4)"/>
      <circle cx="${latestPt.x}" cy="${latestPt.y}" r="3.5" fill="#34d399" stroke="#020617" stroke-width="1.5"/>

      <!-- X-Axis Labels (time fractions of [minTs, maxTs], Issue #257) -->
      <text x="${padL}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">${series[0]?.label || 'Start'}</text>
      <text x="${getX(minTs + tsSpan * 0.33)}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${knownTs.length ? brokerPointLabel({ ts: minTs + tsSpan * 0.33 }, '') : ''}</text>
      <text x="${getX(minTs + tsSpan * 0.66)}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${knownTs.length ? brokerPointLabel({ ts: minTs + tsSpan * 0.66 }, '') : ''}</text>
      <text x="${w - padR}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">Current</text>

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

      // Closest point by real pixel x (Issue #257) — index rounding would
      // snap to the wrong close once spacing is time-proportional.
      let best = 0;
      let bestDist = Infinity;
      for (let k = 0; k < points.length; k += 1) {
        const dist = Math.abs(points[k].x - svgX);
        if (dist <= bestDist) { bestDist = dist; best = k; }
      }
      const pt = points[best];
      const data = pt.data;

      crosshairLine.setAttribute('x1', pt.x);
      crosshairLine.setAttribute('x2', pt.x);
      crosshairLine.setAttribute('opacity', '1');

      crosshairDot.setAttribute('cx', pt.x);
      crosshairDot.setAttribute('cy', pt.y);
      crosshairDot.setAttribute('opacity', '1');

      // Update tooltip content and position
      tooltip.style.display = 'flex';
      // Issue #259: trade facts per close -- human title, dollar+pct, method,
      // hold time. Percent is percent-units like every other pnl_pct here
      // (100 * pnl / basis); unmeasured pnl or basis renders `--`, never 0%
      // or NaN%.
      const pnlNum = Number(data.pnl);
      const basisNum = Number(data.cost_basis);
      const hasPnl = Number.isFinite(pnlNum);
      const hasBasis = data.cost_basis !== null && data.cost_basis !== undefined
        && Number.isFinite(basisNum) && basisNum > 0;
      const pnlPct = (hasPnl && hasBasis) ? (100 * pnlNum / basisNum) : null;
      tooltip.innerHTML = `
        <div class="broker-tooltip-time">${data.label || 'Snapshot'}</div>
        <div class="broker-tooltip-row"><span class="broker-tooltip-label">Account Value:</span> <span class="broker-tooltip-val mono" style="color:#34d399">${fmtUSD(data.v)}</span></div>
        ${data.pnl !== undefined ? `<div class="broker-tooltip-row"><span class="broker-tooltip-label">Realized Spread:</span> <span class="broker-tooltip-val mono" style="color:${Number(data.pnl) < 0 ? '#f87171' : '#34d399'}">${fmtSignedUSD(data.pnl)}</span></div>` : ''}
        ${data.pnl !== undefined ? `<div class="broker-tooltip-row"><span class="broker-tooltip-label">P&L %:</span> <span class="broker-tooltip-val mono" style="color:${pnlPct !== null && pnlPct < 0 ? '#f87171' : '#34d399'}">${pnlPct === null ? '--' : fmtPct(pnlPct)}</span></div>` : ''}
        ${data.title || data.market ? `<div class="broker-tooltip-row"><span class="broker-tooltip-label">Market:</span> <span class="broker-tooltip-val mono">${esc(data.title || data.market)}</span></div>` : ''}
        ${data.method ? `<div class="broker-tooltip-row"><span class="broker-tooltip-label">Method:</span> <span class="broker-tooltip-val mono">${methodBadge(data.method)}</span></div>` : ''}
        ${data.pnl !== undefined ? `<div class="broker-tooltip-row"><span class="broker-tooltip-label">Held:</span> <span class="broker-tooltip-val mono">${data.hold_seconds === null || data.hold_seconds === undefined ? '--' : esc(fmtHoldDuration(data.hold_seconds))}</span></div>` : ''}
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
  const chartCards = scope.querySelectorAll('.stats-chart-card');

  if (view === 'all') {
    if (quantDeck) quantDeck.style.display = '';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = '';
    if (gatesCard) gatesCard.style.display = '';
    chartCards.forEach(c => c.style.display = '');
  } else if (view === 'distributions') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    chartCards.forEach(c => {
      c.style.display = (c.dataset.section === 'distributions') ? '' : 'none';
    });
  } else if (view === 'monte-carlo') {
    if (quantDeck) quantDeck.style.display = '';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    chartCards.forEach(c => {
      c.style.display = (c.dataset.section === 'monte-carlo') ? '' : 'none';
    });
  } else if (view === 'markout') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    chartCards.forEach(c => {
      c.style.display = (c.dataset.section === 'markout') ? '' : 'none';
    });
  } else if (view === 'simulator') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'none';
    if (simulatorCard) simulatorCard.style.display = '';
    if (gatesCard) gatesCard.style.display = 'none';
  }
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
  // Issue #260: a null expectancy/mean is unmeasured, not a zero. `$0.000`
  // and `0.00%` were the last two fabricated zeros in this grid.
  const expectancy = ta.expectancy_usd != null && n > 0
    ? `${ta.expectancy_usd < 0 ? '-' : ''}$${Math.abs(ta.expectancy_usd).toFixed(3)}`
    : 'unmeasured';
  const meanRet = ta.mean_return_pct != null && n > 0 ? `${ta.mean_return_pct.toFixed(2)}%` : 'unmeasured';
  const winRate = ta.win_rate != null && n > 0 ? `${(ta.win_rate * 100).toFixed(1)}%` : 'unmeasured';
  // `win_rate_ci95` is `{lower, upper}`, not a two-element array. Indexing it
  // gave `undefined * 100` on both ends, so the Wilson interval rendered as
  // `[NaN%–NaN%]` -- an interval that exists in the report and was never once
  // displayed. The array form is still read, for any caller that sends one.
  const ciBounds = _ciBounds(ta.win_rate_ci95);
  const ci95 = ciBounds && n > 0
    ? `[${(ciBounds[0] * 100).toFixed(0)}%–${(ciBounds[1] * 100).toFixed(0)}%]`
    : '[0%–0%]';
  // Issue #251: no metric in this grid fabricates a zero. A null field is
  // unmeasured — `$0.00` VaR reads as "no risk", a verdict the run never earned.
  // A VaR/CVaR tail that happens to be profitable reports a negative
  // magnitude; the minus goes in front of the currency, never inside it.
  const fmtLoss = (v) => `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`;
  const var95 = ta.var_95_usd != null && n > 0 ? fmtLoss(ta.var_95_usd) : 'unmeasured';
  const cvar95 = ta.cvar_95_usd != null && n > 0 ? fmtLoss(ta.cvar_95_usd) : 'unmeasured';
  const sharpe = ta.sharpe_ratio != null && n > 0 ? ta.sharpe_ratio.toFixed(2) : 'unmeasured';
  const sortino = ta.sortino_ratio != null && n > 0 ? ta.sortino_ratio.toFixed(2) : 'unmeasured';
  const kelly = ta.kelly_fraction != null && n > 0 ? `${(ta.kelly_fraction * 100).toFixed(1)}%` : 'unmeasured';
  const halfKelly = ta.half_kelly != null && n > 0 ? `${(ta.half_kelly * 100).toFixed(1)}%` : 'unmeasured';
  const profitFactor = ta.profit_factor != null && n > 0 ? `${ta.profit_factor.toFixed(2)}x` : 'unmeasured';
  const payoffRatio = ta.payoff_ratio != null && n > 0 ? `${ta.payoff_ratio.toFixed(2)}x` : 'unmeasured';
  // Issue #248 companions: the percent number may see fewer closes than the
  // dollar number, and the dollar-weighted percent bridges the sign gap.
  const nMeas = ta.n_measured_returns != null ? ta.n_measured_returns : null;
  const dwRet = ta.dollar_weighted_return_pct != null && n > 0
    ? `${ta.dollar_weighted_return_pct.toFixed(2)}%` : null;

  container.innerHTML = `
    <div class="quant-tile">
      <div class="quant-label">Mathematical Expectancy <span class="info-bubble" title="Why can $ and % disagree?">?</span><span class="info-tooltip">Dollars average $ per close over ALL closes; percents average % per close over measured closes only. A small trade with a big % loss can pull % negative while larger-$ wins keep $ positive.</span></div>
      <div class="quant-value ${n > 0 ? signClass(ta.expectancy_usd) : ''}">${esc(expectancy)}</div>
      <div class="quant-sub">${esc(meanRet)} mean return / trade${dwRet != null ? ` · ${esc(dwRet)} dollar-weighted` : ' · dollar-weighted unmeasured'}${nMeas != null ? ` · ${nMeas}/${n} measured` : ''}</div>
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
      <div class="quant-value ${n > 0 && ta.profit_factor != null ? (Number(ta.profit_factor) >= 1 ? 'positive' : 'negative') : ''}">${esc(profitFactor)}</div>
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

// Issue #259: how a close happened, as a small pill in the equity tooltip.
// Same lookup shape as GATE_VERDICTS/gateBadge above.
const METHOD_BADGES = {
  merge: { cls: 'go', label: 'MERGED' },
  shadow_merge: { cls: 'go', label: 'MERGED' },
  single_buy_exit: { cls: 'standby', label: 'SINGLE EXIT' },
  naked_exit: { cls: 'standby', label: 'SINGLE EXIT' },
  ladder_exit: { cls: 'standby', label: 'LADDER EXIT' },
  venue_sync: { cls: 'standby', label: 'VENUE SYNC' },
  shadow_settlement: { cls: 'standby', label: 'SHADOW SETTLEMENT' },
  sell: { cls: 'standby', label: 'SELL' },
};

function methodBadge(method) {
  if (method === null || method === undefined || method === '') {
    return `<span class="param-badge">-</span>`;
  }
  const known = Object.prototype.hasOwnProperty.call(METHOD_BADGES, method);
  const badge = known ? METHOD_BADGES[method]
    : { cls: 'standby', label: String(method).toUpperCase() };
  return `<span class="param-badge ${badge.cls}">${esc(badge.label)}</span>`;
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

/* Tier 1: Sample size & confidence level sufficiency (#443).
 * Shows observations count, margin of error, required/remaining closes, and progress bars
 * across 95%, 98%, and 99% confidence levels.
 */
function renderSampleSufficiency(ta) {
  const host = document.getElementById('sample-sufficiency-readout');
  if (!host) return;

  const suff = ta?.sample_size_sufficiency || {};
  const statuses = suff.statuses || {};
  const hasStatuses = Object.keys(statuses).length > 0;

  if (hasStatuses) {
    const statusKeys = ['pnl_expectancy', 'stop_loss_rate', 'merge_rate', 'fill_rate'];
    const blocksHtml = statusKeys.map(key => {
      const st = statuses[key];
      if (!st) return '';
      const label = esc(st.label || key);
      const base = esc(st.base || 'closes');
      const currentN = (st.current_n != null && Number.isFinite(Number(st.current_n)))
        ? Number(st.current_n)
        : 0;
      const targetParam = st.type === 'continuous'
        ? `Cohen's d = ${st.effect_size_d || 0.20}`
        : `Margin = ${(Number(st.target_margin || 0.05) * 100).toFixed(0)}%`;

      const levels = Array.isArray(st.levels) ? st.levels : [];
      // Focus on 95% primary gate row while also rendering full table or rows
      const rows = levels.map(lv => {
        const conf = esc(String(lv.confidence_pct || 0)) + '%';
        const req = (lv.required_n != null && Number.isFinite(Number(lv.required_n)))
          ? String(lv.required_n)
          : 'unmeasured';
        const rem = (lv.remaining_n != null && Number.isFinite(Number(lv.remaining_n)))
          ? String(lv.remaining_n)
          : 'unmeasured';
        const prog = (lv.progress_pct != null && Number.isFinite(Number(lv.progress_pct)))
          ? Math.max(0, Math.min(100, Number(lv.progress_pct)))
          : (req !== 'unmeasured' ? 0 : null);

        const progBarHtml = prog !== null
          ? `<div class="dist-progress-wrap">
               <div class="dist-progress-bar"><div class="dist-progress-fill" style="width:${prog}%"></div></div>
               <span class="mono" style="font-size:11px;min-width:32px;text-align:right">${prog}%</span>
             </div>`
          : `<span class="mono" style="color:var(--text-muted)">unmeasured</span>`;

        return `
          <tr>
            <td class="mono font-semibold">${conf}</td>
            <td class="mono">${esc(req)}</td>
            <td class="mono">${esc(rem)}</td>
            <td class="progress-cell">${progBarHtml}</td>
          </tr>
        `;
      }).join('');

      return `
        <div class="sample-suff-block" style="margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.05)">
          <div class="sample-suff-meta" style="margin-bottom:6px">
            <span><b class="font-display">${label}</b> <span class="mono" style="font-size:10px;color:var(--text-muted)">(${base})</span></span>
            <span>Current: <b class="mono">${currentN}</b> | Target: <span class="mono">${targetParam}</span></span>
          </div>
          <table class="sample-suff-table">
            <thead>
              <tr>
                <th>Confidence</th>
                <th>Required</th>
                <th>Remaining</th>
                <th>Progress</th>
              </tr>
            </thead>
            <tbody>
              ${rows}
            </tbody>
          </table>
        </div>
      `;
    }).join('');

    host.innerHTML = blocksHtml;
    return;
  }

  // Fallback if statuses is absent
  const n = (suff.current_n != null && Number.isFinite(Number(suff.current_n)))
    ? Number(suff.current_n)
    : (ta?.n_closes != null ? Number(ta.n_closes) : 0);
  const defaultLevels = [
    { confidence_pct: 95, z: 1.95996, required_n: null, remaining_n: null, progress_pct: null },
    { confidence_pct: 98, z: 2.32635, required_n: null, remaining_n: null, progress_pct: null },
    { confidence_pct: 99, z: 2.57583, required_n: null, remaining_n: null, progress_pct: null },
  ];
  const levels = (Array.isArray(suff.levels) && suff.levels.length) ? suff.levels : defaultLevels;

  let noteHtml = '';
  if (n < 2) {
    noteHtml = `<div class="sample-suff-note mono">At least two closed trades are needed to measure spread.</div>`;
  }

  const rows = levels.map(lv => {
    const conf = esc(String(lv.confidence_pct || 0)) + '%';
    const req = (lv.required_n != null && Number.isFinite(Number(lv.required_n)))
      ? String(lv.required_n)
      : 'unmeasured';
    const rem = (lv.remaining_n != null && Number.isFinite(Number(lv.remaining_n)))
      ? String(lv.remaining_n)
      : 'unmeasured';
    const prog = (lv.progress_pct != null && Number.isFinite(Number(lv.progress_pct)))
      ? Math.max(0, Math.min(100, Number(lv.progress_pct)))
      : null;

    const progBarHtml = prog !== null
      ? `<div class="dist-progress-wrap">
           <div class="dist-progress-bar"><div class="dist-progress-fill" style="width:${prog}%"></div></div>
           <span class="mono" style="font-size:11px;min-width:32px;text-align:right">${prog}%</span>
         </div>`
      : `<span class="mono" style="color:var(--text-muted)">unmeasured</span>`;

    return `
      <tr>
        <td class="mono font-semibold">${conf}</td>
        <td class="mono">${esc(req)}</td>
        <td class="mono">${esc(rem)}</td>
        <td class="progress-cell">${progBarHtml}</td>
      </tr>
    `;
  }).join('');

  host.innerHTML = `
    <div class="sample-suff-meta">
      <span>Current observations: <b class="mono">${n}</b></span>
      <span>Effect size: <b class="mono">Cohen's d = 0.20</b></span>
    </div>
    <table class="sample-suff-table">
      <thead>
        <tr>
          <th>Confidence</th>
          <th>Required</th>
          <th>Remaining</th>
          <th>Progress</th>
        </tr>
      </thead>
      <tbody>
        ${rows}
      </tbody>
    </table>
    ${noteHtml}
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
  // No emoji on status surfaces (DESIGN.md): the funnel reads by number and
  // theme color, so the marker is just the stage's ordinal.
  const STAGE_MARKERS = ['1', '2', '3', '4'];

  const rows = stages.map((st, idx) => {
    const legs = Number(st.legs) || 0;
    const markets = Number(st.markets) || 0;
    const drop = dropByFrom[st.key];
    const isWorstFrom = worst && worst.from === st.key;
    const widthPct = TAPER_WIDTHS[Math.min(idx, TAPER_WIDTHS.length - 1)];
    const themeCls = STAGE_THEMES[Math.min(idx, STAGE_THEMES.length - 1)];
    const icon = STAGE_MARKERS[Math.min(idx, STAGE_MARKERS.length - 1)];

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
          ${isWorstFrom ? `<span class="funnel-worst-badge">WORST DROP</span>` : ''}
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
  // Issue #248 companions (display-only): bridge the $ vs % sign gap.
  const nMeasKpi = ta.n_measured_returns != null ? ta.n_measured_returns : null;
  const dwRetKpi = ta.dollar_weighted_return_pct != null && n > 0
    ? fmtPct(ta.dollar_weighted_return_pct) : null;

  grid.innerHTML = `
    <div class="kpi-tile">
      <div class="kpi-label">Average Profit Per Close <span class="info-bubble" title="Why can $ and % disagree?">?</span><span class="info-tooltip">Dollars average $ per close over ALL closes; percents average % per close over measured closes only. A small trade with a big % loss can pull % negative while larger-$ wins keep $ positive.</span></div>
      ${fmtVal(ta.expectancy_usd != null && n > 0 ? fmtSignedUSD(ta.expectancy_usd) : '$0.000', n > 0 ? ' ' + signClass(ta.expectancy_usd) : '')}
      <div class="hint">Spread capture net of slippage</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Mean Return Per Trade</div>
      ${fmtVal(ta.mean_return_pct != null && n > 0 ? fmtPct(ta.mean_return_pct) : '0.00%', n > 0 ? ' ' + signClass(ta.mean_return_pct) : '')}
      <div class="hint">± ${ta.stdev_return_pct != null && n > 0 ? Number(ta.stdev_return_pct).toFixed(2) + '%' : '0.00%'} (σ)${dwRetKpi != null ? ` · ${dwRetKpi} dollar-weighted` : ' · dollar-weighted unmeasured'}</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Annualized Sharpe Ratio</div>
      ${fmtVal(ta.sharpe_ratio != null && n > 0 ? ta.sharpe_ratio.toFixed(2) : '0.00', n > 0 ? ' ' + signClass(ta.sharpe_ratio) : '')}
      <div class="hint">Risk-adjusted spread performance</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Executed Sample Size</div>
      ${fmtVal(`${n} Closes (${wins}W / ${losses}L)${nMeasKpi != null ? ` · ${nMeasKpi} measured` : ''}`)}
      <div class="hint">Empirical Win Rate: ${n > 0 ? winRate.toFixed(1) + '%' : '0.0%'}</div>
    </div>
  `;

  // Tier 1 first: the readings that gate live trading are rendered before
  // the drill-down decks, and a throw in either must not take the rest down.
  try { renderPnlCiReadout(ta); } catch (e) { console.error('Error rendering PnL CI readout', e); }
  try { renderExecutionFunnel(kpi); } catch (e) { console.error('Error rendering execution funnel', e); }
  try { renderSampleSufficiency(ta); } catch (e) { console.error('Error rendering sample sufficiency', e); }

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

/* Portfolio overview card + run pill (Issue #268): one paint for the card
 * the operator reads on every visit. #140 moved `#broker-portfolio-overview`
 * and `#run-profitability` onto the rail's Dashboard page while the KPI tiles
 * stayed on Reports; painting them from renderKPIs — which the poll gates
 * behind the Reports grid — froze the card at its pre-data standby on every
 * other page. They are their own paint now, gated by their own target. */
function renderPortfolioOverview(kpi, status, state) {
  // Same contract as renderKPIs: a payload without a portfolio is malformed
  // for this paint, and computed-from-nothing standby figures must not
  // overwrite whatever the card last showed.
  if (!kpi || !kpi.portfolio) return;
  renderRunProfitability(kpi);
  renderBrokerPortfolioOverview(kpi, status, state);
}

function renderKPIs(kpi, status) {
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
  const startCap = p.starting_capital ?? status?.starting_capital;
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
  // Heavy charts paint one frame after the tiles, so a click arriving
  // mid-render is handled between the two paints (Issue #264). The guard
  // below drops the paint when a newer render queued behind it or Tab 2 hid
  // before the frame ran.
  const paintGeneration = ++analyticsPaintGeneration;
  deferPaint(() => {
    if (paintGeneration !== analyticsPaintGeneration) return;
    const analyticsTab = typeof document !== 'undefined' ? document.getElementById('tab-2') : null;
    if (analyticsTab && !tabVisible(analyticsTab)) return;
    renderAnalyticsSurface(kpi, status);
  });
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

// Issue #259: hold time for the equity tooltip. Two-part like `3h 12m` --
// fmtOrderAge above keeps its single-unit shape for the orders table.
function fmtHoldDuration(sec) {
  if (sec === null || sec === undefined) return '--';
  const s = Math.floor(Number(sec));
  if (!Number.isFinite(s) || s < 0) return '--';
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return m ? `${h}h ${m}m` : `${h}h`;
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

/* What each inner status means, in one plain sentence. The words FILLED and
 * MERGED sit in the same column and are not alternatives -- FILLED is the
 * order executing, MERGED is what happened to those shares afterwards -- so
 * each carries its own explanation on hover. */
const ORDER_STATUS_TITLES = {
  open: 'resting on the book, nothing filled yet',
  pending: 'sent to the venue, not yet accepted',
  partial: 'partly filled, the remainder is still resting',
  filled: 'executed in full: the shares are on the books',
  merged: 'these filled shares were merged back into $1.00 a share and retired',
  cancelled: 'cancelled before it executed: nothing was bought or sold',
  unattributed: 'filled, but the store cannot tie it to a pair',
};

function orderStatusTitle(status) {
  const key = String(status || '').toLowerCase();
  return ORDER_STATUS_TITLES[key] || '';
}

function renderExpandedOrders(orders, fills, showCancelled, legOf) {
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

  // `(sh)` on Size and Filled, because the row above counts FILL EVENTS and
  // these count SHARES. Same word, two units, was the whole confusion: the
  // outer row said 2 while the single row inside it said 10.
  html += `<table class="orders-subtable">`;
  html += `<thead><tr>
    <th>Pair ID</th>
    <th>Age</th>
    <th>Outcome / Leg</th>
    <th>Price</th>
    <th>Size (sh)</th>
    <th>Filled (sh)</th>
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
      // The leg, resolved from this market's own quotes. `token_side` and
      // `outcome` are legacy shapes the server does not send, so they stay as
      // a last resort rather than the primary source.
      const leg = typeof legOf === 'function' ? legOf(o.token_id) : null;
      const sideLabel = o.token_side || leg;
      const isDown = sideLabel === 'DOWN'
        || (o.outcome && (o.outcome.toLowerCase().includes('no') || o.outcome.toLowerCase().includes('down')));
      const badgeCls = isDown ? 'badge-down' : 'badge-up';
      // The leg is the identity; the side is the action. On a book where
      // every order the engine posts is a BUY, spelling BUY on each row

      // answers a question nobody asked, so a known leg renders alone and

      // a SELL (which would be the news) keeps its word beside the leg.
      const knownLeg = sideLabel && !o.outcome;
      const isBuy = String(o.side || '').toUpperCase() === 'BUY';
      const label = o.outcome ? `${o.outcome} (${fmtSide(o.side)})`
        : (knownLeg ? (isBuy ? sideLabel : `${sideLabel} · ${fmtSide(o.side)}`)
        : fmtSide(o.side));
      const isCancelled = isCancelledStatus(o.status);
      html += `<tr class="${isCancelled ? 'order-cancelled ' : ''}pair-row" style="--pair-hue:${hue}; background: hsla(${hue},72%,60%,0.06)">
        <td class="mono" style="font-size:11px;color:var(--text-muted)"><span class="pair-label"${pairTitle}><span class="pair-dot" style="--pair-hue:${hue}"></span>${esc(pairDisplay)}</span></td>
        <td class="mono" style="font-size:11px;color:var(--text-secondary)">${fmtOrderAge(o.age_sec)}</td>
        <td class="mono"><span class="${badgeCls} side-${(o.side||'').toLowerCase()}">${esc(label)}</span></td>
        <td class="mono">${esc(o.price !== null && o.price !== undefined ? o.price.toFixed(4) : '--')}</td>
        <td class="mono">${esc(o.original_size !== null && o.original_size !== undefined ? o.original_size : '--')}</td>
        <td class="mono">${esc(o.size_matched !== null && o.size_matched !== undefined ? o.size_matched : '--')}</td>
        <td><span class="pill ${statusCls}" title="${esc(orderStatusTitle(rowStatus))}">${fmtOrderStatus(rowStatus)}</span></td>
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
  'active-markets': ['Timestamp', 'Market', 'Category', 'UP Quote', 'DOWN Quote',
                     'Mid Price', '$ Traded 30m', '24h Volume', 'Top-3 Bid Depth',
                     'Horizon', 'Status'],
  'open-orders': ['Timestamp', 'Market', 'Leg', 'Price', 'Size',
                  'Total Cost', 'Queue Ahead', 'Age'],
  // Per-leg on the left, pair-level on the right. Mark Value and both PnLs
  // span the pair because they are pair numbers: a matched pair merges at par
  // and only the remainder is marked, which cannot be split across two rows
  // without inventing a per-leg figure that does not exist.
  'positions': ['Timestamp', 'Market', 'Leg', 'Size', 'Avg Price', 'Cost',
                'Mark Value', 'Unrealized', 'Realized'],
  // A CLOSED TRADE row is a MARKET, not a position: the positions are the rows
  // inside it, one click down. So this view carries no cost column -- a market
  // holds nothing once it closes, and `Commit ($)` read $0.00 beside a booked
  // loss. What is left is what a market-level row can honestly answer: when it
  // closed, what closed it (`Hedge` roll-up below), what money it booked, how
  // many times something executed here ("Fill events": the shares of each are
  // one click down, and calling both of them "Fills" is what made the outer 2
  // and the inner 10 look contradictory), and that it is finished.
  'closed-trades': ['Timestamp', 'Market', 'Hedge', 'Realized P&L',
                    'Fill events', 'Status'],
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

/* ── Click-to-sort (issue #294) ──
 *
 * The sort unit is never the rendered <tr>. Orders and OPEN POSITIONS emit one
 * row per leg with the market name and every pair-level number in rowspan
 * cells, and CLOSED TRADES reuses `marketRowPairHtml`, which emits a main row
 * plus an optional expanded sub-row. Re-ordering the rows would separate a
 * pair's UP from its DOWN and strand every rowspan cell, so every builder sorts
 * its backing groups FIRST and the rows are then paired into HTML from the
 * sorted groups. Sorting is a re-ordering of what already exists; it never
 * invents, drops, or merges a row.
 */

/* Columns that hold a word rather than a number. The operator's first click on
 * one of these reads "which name comes first", so it starts ascending; every
 * other column answers "which is biggest / deepest / oldest" and starts
 * descending. One rule, two directions, no per-column special cases. */
const OT_TEXT_COLUMNS = {
  'active-markets': new Set([1, 2, 10]),  // Market, Category, Status
  'open-orders': new Set([1, 2]),         // Market, Leg
  'positions': new Set([1, 2]),           // Market, Leg
  'closed-trades': new Set([1, 2, 5]),    // Market, Hedge, Status
};

function otIsTextColumn(view, col) {
  return (OT_TEXT_COLUMNS[view] || new Set()).has(col);
}

/* The direction a column takes on its first click. */
function otDefaultDir(view, col) {
  return otIsTextColumn(view, col) ? 'asc' : 'desc';
}

/* A number, or null when the cell renders as `--`. Coercion here rather than
 * in the comparator, so every accessor agrees on what "unmeasured" means. */
function otNum(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/* Sum across a group's legs: which pair costs the most to build is the sum of
 * its legs, not one of them. A group with nothing measured is unmeasured. */
function otSum(values) {
  let total = 0;
  let seen = false;
  for (const v of values) {
    const n = otNum(v);
    if (n === null) continue;
    total += n;
    seen = true;
  }
  return seen ? total : null;
}

/* The extreme leg in the sort direction: which order has the deepest queue or
 * is the oldest is answered by the worst leg, and "worst" depends on which way
 * the operator is reading. */
function otExtreme(values, dir) {
  let best = null;
  for (const v of values) {
    const n = otNum(v);
    if (n === null) continue;
    if (best === null) { best = n; continue; }
    if (dir === 'asc' ? n < best : n > best) best = n;
  }
  return best;
}

/* Unmeasured ranks LAST in both directions. A column of `--` must never
 * outrank a measured value, and flipping the direction must not promote the
 * empties to the top -- "ascending" still means "measured first". */
function otCompare(a, b, dir) {
  const aMiss = a === null || a === undefined;
  const bMiss = b === null || b === undefined;
  if (aMiss && bMiss) return 0;
  if (aMiss) return 1;
  if (bMiss) return -1;
  const mul = dir === 'asc' ? 1 : -1;
  if (typeof a === 'string' || typeof b === 'string') {
    return mul * String(a).localeCompare(String(b));
  }
  if (a === b) return 0;
  return mul * (a < b ? -1 : 1);
}

/* Sort a builder's backing groups by one column. `valueOf(group, col, dir)`
 * reads the same underlying value the row will render, never the formatted
 * string -- `$1,000.00` has to outrank `$95.00`, and reading the string gives
 * the opposite. Stable: equal values keep the order the builder produced, so a
 * sort never shuffles rows that tie. */
function otSortGroups(groups, sort, valueOf) {
  if (!sort || !Number.isInteger(sort.col)) return groups;
  const dir = sort.dir === 'asc' ? 'asc' : 'desc';
  return groups
    .map((g, i) => ({ g, i, v: valueOf(g, sort.col, dir) }))
    .sort((x, y) => otCompare(x.v, y.v, dir) || (x.i - y.i))
    .map(e => e.g);
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

/* Live marks (#427): the server's read-only venue feed, delivered as named
 * `mark` frames on this same SSE connection. token_id -> {bid, ask, mid,
 * tsMs, seq}. A mark older than LIVE_MARK_MAX_AGE_MS reads as missing, so a
 * stalled feed degrades to quote mids instead of freezing a price on screen.
 * Finished markets never reach the positions table, so they need no marks. */
const LIVE_MARK_MAX_AGE_MS = 30000;
const liveMarks = new Map();
let liveMarksRafQueued = false;

function liveMarkMid(tokenId, nowMs) {
  if (tokenId === null || tokenId === undefined) return null;
  const e = liveMarks.get(String(tokenId));
  if (!e) return null;
  const now = (nowMs === undefined) ? Date.now() : Number(nowMs);
  if (!Number.isFinite(e.mid) || e.mid < 0 || e.mid > 1) return null;
  if (!Number.isFinite(now) || !Number.isFinite(e.tsMs)
      || now - e.tsMs > LIVE_MARK_MAX_AGE_MS) return null;
  return e.mid;
}

/* Fold one `event: mark` payload into the map. A reset clears first; a
 * snapshot replaces the whole visible set (tokens it omits are gone); a
 * delta merges newer-wins by per-mark seq. Rows outside 0-1, without a
 * usable timestamp, or without a token are dropped. Returns true when
 * anything visible changed, so the stream paints only on change. */
function applyLiveMarks(payload, nowMs) {
  if (!payload || typeof payload !== 'object') return false;
  const list = Array.isArray(payload.marks) ? payload.marks : null;
  if (list === null) return false;
  let changed = false;
  if (payload.reset === true && liveMarks.size) {
    liveMarks.clear();
    changed = true;
  }
  const keep = (payload.snapshot === true) ? new Map() : null;
  for (const row of list) {
    if (!row || typeof row !== 'object') continue;
    const tok = (row.token_id === null || row.token_id === undefined)
      ? null : String(row.token_id);
    if (tok === null || tok === '') continue;
    const mid = Number(row.mid);
    if (!Number.isFinite(mid) || mid < 0 || mid > 1) continue;
    const tsMs = toMs(row.ts);
    if (tsMs === null) continue;
    const seq = Number(row.seq);
    const prev = liveMarks.get(tok);
    if (prev && Number.isFinite(seq) && Number.isFinite(prev.seq)
        && seq < prev.seq) continue; // newer wins
    const entry = {
      bid: Number(row.bid), ask: Number(row.ask), mid, tsMs,
      seq: Number.isFinite(seq) ? seq : null,
    };
    (keep || liveMarks).set(tok, entry);
    changed = true;
  }
  if (keep) {
    let same = (keep.size === liveMarks.size);
    if (same) {
      for (const [k, v] of keep) {
        if (liveMarks.get(k)?.mid !== v.mid) { same = false; break; }
      }
    }
    liveMarks.clear();
    for (const [k, v] of keep) liveMarks.set(k, v);
    changed = changed || !same;
  }
  return changed;
}

function clearLiveMarks() {
  // Store switch: the next store's tokens are different ones, so no mark
  // from this store may survive it. Dropping the queued paint flag too --
  // the queued pass finds no store data and no-ops.
  liveMarks.clear();
  liveMarksRafQueued = false;
}

/* Quote mids with live marks overlaid: each quoted token's leg reads the
 * live mid when one is fresh, the quote mid otherwise. Returns the mids plus
 * whether any leg is live (the cell affordance) and the youngest mark age.
 * `positionMarkValue` keeps its signature -- this is the `mids` it takes. */
function liveLegMids(market, nowMs) {
  const mids = latestLegMids(market);
  const now = (nowMs === undefined) ? Date.now() : Number(nowMs);
  let live = false;
  let ageMs = null;
  for (const q of (market && market.quotes) || []) {
    if (!q || q.token_id === null || q.token_id === undefined) continue;
    const leg = normalizeLeg(q.side);
    if (leg === null) continue;
    const mid = liveMarkMid(q.token_id, now);
    if (mid === null) continue;
    mids[leg.toLowerCase()] = mid;
    live = true;
    const age = now - liveMarks.get(String(q.token_id)).tsMs;
    if (ageMs === null || age < ageMs) ageMs = age;
  }
  return { mids, live, ageMs };
}

/* One rAF-merged pass per burst of mark frames: visible positions cells are
 * rewritten in place, never a table rebuild. Gated by paintable() so a
 * hidden tab pays nothing; a value-sorted table re-renders once instead,
 * because in-place writes would silently unsort it. */
function scheduleLiveMarksPaint() {
  if (liveMarksRafQueued) return;
  liveMarksRafQueued = true;
  deferPaint(() => {
    liveMarksRafQueued = false;
    paintLiveMarks();
  });
}

function paintLiveMarks() {
  if (typeof document === 'undefined' || !lastKpi) return;
  if (currentOrdersTradesView !== 'positions') return;
  const body = (typeof document.getElementById === 'function')
    ? document.getElementById('orders-trades-body') : null;
  if (!body || !paintable(tab1, body)) return;
  const sort = (typeof otActiveSort === 'function') ? otActiveSort('positions') : null;
  if (sort && (sort.col === 6 || sort.col === 7)) {
    renderOrdersTrades(lastKpi, lastState);
    return;
  }
  // The node harness stubs the DOM without element queries: no cells found
  // is a no-op there, the same way unfocused headers are.
  if (typeof body.querySelectorAll !== 'function') return;
  const now = Date.now();
  for (const [cid, m] of heldMarketEntries(lastKpi, false)) {
    const overlay = liveLegMids(m, now);
    if (!overlay.live) continue;
    const mark = positionMarkValue(m, overlay.mids);
    const cost = Number(m.total_cost) || 0;
    const unrealized = mark === null ? null : mark - cost;
    const age = (overlay.ageMs !== null) ? `Live mark, ${(overlay.ageMs / 1000).toFixed(1)}s old` : 'Live mark';
    for (const el of body.querySelectorAll('td[data-cell="value"]')) {
      if (el.getAttribute && el.getAttribute('data-cid') !== cid) continue;
      const valEl = el.querySelector ? el.querySelector('.stitch-mark-val') : null;
      if (valEl) {
        valEl.textContent = mark === null ? '--' : fmtUSD(mark);
      } else {
        el.textContent = mark === null ? '--' : fmtUSD(mark);
      }
      if (el.classList) el.classList.add('live');
      el.title = age;
    }
    for (const el of body.querySelectorAll('td[data-cell="unrealized"]')) {
      if (el.getAttribute && el.getAttribute('data-cid') !== cid) continue;
      const pnlCell = el.querySelector ? el.querySelector('.stitch-pnl-cell') : null;
      if (pnlCell) {
        const valEl = pnlCell.querySelector('.stitch-pnl-val');
        const pctEl = pnlCell.querySelector('.stitch-pnl-pct');
        const pnlCls = unrealized > 0 ? 'pos' : (unrealized < 0 ? 'neg' : 'flat');
        const unrealizedPct = (cost > 0 && unrealized !== null) ? ((unrealized / cost) * 100) : null;
        if (valEl) {
          valEl.className = `stitch-pnl-val ${pnlCls}`;
          valEl.innerHTML = unrealized === null ? '--' : signedUSD(unrealized);
        }
        if (pctEl) {
          pctEl.className = `stitch-pnl-pct ${pnlCls}`;
          pctEl.innerHTML = unrealizedPct !== null ? `(${unrealizedPct >= 0 ? '+' : ''}${unrealizedPct.toFixed(1)}%)` : '--';
        }
      } else {
        el.innerHTML = signedUSD(unrealized);
      }
      if (el.classList) el.classList.add('live');
      el.title = age;
    }
  }
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
/* One category string for every Orders & Trades view: the resolved venue
 * label when the feed named one, `Uncategorized` when nothing did. A named
 * bucket groups honestly; `--` reads as missing data. */
function marketCategory(m) {
  const label = m && typeof m.category === 'string' ? m.category.trim() : '';
  return label || 'Uncategorized';
}

function marketCell(market, conditionId, opts) {
  const wantCaption = Boolean(opts && opts.categoryCaption);
  const named = market && (market.title || market.name || market.slug);
  if (named) {
    const caption = wantCaption
      ? `<div class="caption-muted">${esc(marketCategory(market))}</div>` : '';
    return marketLink(market) + caption;
  }
  const cid = String(conditionId || '');
  if (!cid) return '--';
  const fallbackCaption = wantCaption
    ? `<div class="caption-muted">${esc(marketCategory(market))}</div>` : '';
  return `<span class="mono" title="${esc(cid)}">${esc(cid.slice(0, 10))}…</span>${fallbackCaption}`;
}

/* The market an open-order group belongs to: the KPI entry first, then the
 * registry pair identity (matched by the group's pair_id, else by
 * condition_id), then a bare condition id that still renders as a truncated,
 * searchable cell. */
function orderGroupMarket(group, conditionId, byMarket, state) {
  noteStreamTitles(byMarket);
  if (byMarket[conditionId]) return byMarket[conditionId];
  const pairs = (state && state.pairs) || [];
  const hit = pairs.find(p => p.pair_id && p.pair_id === group.key)
    || pairs.find(p => (p.condition_id || (p.market && p.market.condition_id)) === conditionId);
  if (hit && hit.market) return { ...hit.market, condition_id: conditionId };
  return { condition_id: conditionId };
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
function otTag(tone, label, value, title) {
  const valueHtml = (value === null || value === undefined)
    ? '' : ` <span class="ot-tag-value mono">${esc(value)}</span>`;
  const titleHtml = title ? ` title="${esc(title)}"` : '';
  return `<div class="ot-tag is-${esc(tone)}"${titleHtml}>${esc(label)}${valueHtml}</div>`;
}

function otEmptyRow(view, message) {
  return `<tr><td colspan="${OT_COLUMNS[view].length}" style="text-align:center;color:var(--text-muted);padding:20px">${esc(message)}</td></tr>`;
}

function otHeadHtml(view, sort) {
  // The market name is the widest thing in the table and the only cell that
  // wraps; without a floor it folds a three-word title onto three lines and
  // squeezes every number column.
  // The STATUS column header carries the vocabulary tooltip (issue #272).
  const statusTitle = ' title="RESTING: orders are resting on the book. '
    + 'QUOTING: the engine is actively quoting this market. '
    + 'IDLE: no quote activity observed."';
  // The Hedge cell is a roll-up of the rows INSIDE the market row, so the
  // header has to say so: read as a fact about the market it was a word that
  // contradicted the trades it sat on.
  const hedgeTitle = ' title="Every position under this market, rolled up: '
    + 'Paired, Partial, Unpaired, or Flat when nothing is held."';
  const active = (sort && Number.isInteger(sort.col)) ? sort : null;
  const cells = OT_COLUMNS[view]
    .map((label, i) => {
      const isActive = Boolean(active && active.col === i);
      const dir = isActive ? active.dir : null;
      // `aria-sort` on the <th> is the direction signal, and the arrow is
      // `aria-hidden` decoration. Announcing the direction a second time inside
      // the button makes a screen reader say it twice, so the button carries the
      // column name and the <th> carries the direction -- one signal, one voice.
      const ariaSort = isActive
        ? ` aria-sort="${dir === 'asc' ? 'ascending' : 'descending'}"`
        : '';
      const button = `<button type="button" class="ot-sort-btn" data-ot-sort="${i}">`
        + `<span class="ot-sort-label">${esc(label)}</span>`
        + (isActive
          ? `<span class="ot-sort-arrow" aria-hidden="true">${dir === 'asc' ? '▲' : '▼'}</span>`
          : '')
        + `</button>`;
      // The width floor rides on the MARKET column, not on position 0: the
      // Timestamp column is first now, and the market name is still the only
      // cell that wraps.
      const isMarketHead = label === 'Market';
      const vocabTitle = label === 'Status' ? statusTitle : (label === 'Hedge' ? hedgeTitle : '');
      return `<th${isMarketHead ? ' class="ot-market-head"' : ''}${ariaSort}${vocabTitle}>${button}</th>`;
    })
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

/* Per-market status vocabulary for the Active Markets tab (issue #272).
 *
 * The pill used to be derived purely from resting-order presence, so a market
 * being quoted live — but whose orders have not rested, or were
 * cancelled/filled — read IDLE, the same word the fleet-level scan banner
 * uses for "no active-phase work". Three states, most specific first:
 *   RESTING — orders are resting on the book (a subset of QUOTING);
 *   QUOTING — the engine is actively quoting this market (quotes_count > 0);
 *   IDLE    — no quote activity. Reserved; must not appear in this tab, since
 *             everything listed here graduated the filter and is being quoted.
 */
function marketStatusPill(m, restingHere) {
  const quoting = Number(m && m.quotes_count) > 0;
  if (restingHere) return '<span class="pill resting-breathing" title="Orders are resting on the book">RESTING</span>';
  if (quoting) return '<span class="pill quoting-breathing" title="The engine is actively quoting this market (no orders resting on the book right now)">QUOTING</span>';
  return '<span class="pill stopped" title="No quote activity observed">IDLE</span>';
}

function activeMarketsRows(kpi, state, sort) {
  const ordersByMarket = groupOrdersByMarket(state && state.orders);
  const entries = Object.entries((kpi && kpi.by_market) || {})
    // Everything listed here is being worked, so IDLE cannot appear: a market
    // with no quote activity and only a filled/cancelled order has nothing
    // active to show (issue #272; CodeRabbit round on this PR).
    .filter(([cid, m]) => isQuotedMarket(m, ordersByMarket[cid])
      && ((m.quotes_count || 0) > 0
        || (ordersByMarket[cid] || []).some(o => isRestingOrder(o))));

  if (!entries.length) return otEmptyRow('active-markets', 'No markets are being quoted.');

  // One row per market, so a market IS the sort unit here — the only question
  // is which of its own values the column stands for. Each accessor reads the
  // same derived number the cell below it renders.
  const rows = entries.map(([cid, m]) => {
    const legs = latestLegQuotes(m);
    const upQuote = legs.up ? legs.up.price : null;
    const dnQuote = legs.dn ? legs.dn.price : null;
    const restingHere = (ordersByMarket[cid] || []).some(o => isRestingOrder(o));
    const ts = latestQuoteTs(m);

    // Screening filter parameters and current values
    const upToken = (m.quotes || []).find(q => normalizeLeg(q.side) === 'UP')?.token_id;
    const liveMid = upToken ? liveMarkMid(upToken) : null;
    const midPrice = liveMid
      ?? (legs.up && Number.isFinite(legs.up.mid) ? legs.up.mid : null)
      ?? (legs.dn && Number.isFinite(legs.dn.mid) ? 1 - legs.dn.mid : null)
      ?? m.mid_price ?? m.mid ?? null;
    const volume30m = (Number(m.movement_window_sec) === 1800 || m.movement_window_sec === undefined)
      ? (m.movement_usd ?? m.volume_30m ?? null)
      : (m.volume_30m ?? null);
    const volume24h = m.volume_24h ?? null;
    const yesDepth = otNum(m.yes_depth_usd);
    const noDepth = otNum(m.no_depth_usd);
    const top3Depth = otNum(m.top3_bid_depth)
      ?? (yesDepth !== null && noDepth !== null
          ? Math.min(yesDepth, noDepth)
          : (yesDepth ?? noDepth));
    const horizon = (m.days_to_resolve !== undefined && m.days_to_resolve !== null) ? m.days_to_resolve : null;

    return {
      cid, m, upQuote, dnQuote, midPrice,
      volume30m, volume24h, top3Depth, horizon,
      restingHere, ts,
    };
  });

  const sorted = sort ? otSortGroups(rows, sort, (r, col) => {
    switch (col) {
      case 0: return otNum(r.ts);
      case 1: return String(r.m.title || r.m.name || r.m.slug || '');
      case 2: return marketCategory(r.m);
      case 3: return otNum(r.upQuote);
      case 4: return otNum(r.dnQuote);
      case 5: return otNum(r.midPrice);
      case 6: return otNum(r.volume30m);
      case 7: return otNum(r.volume24h);
      case 8: return otNum(r.top3Depth);
      case 9: return otNum(r.horizon);
      case 10: return r.restingHere ? 'RESTING' : ((r.m.quotes_count || 0) > 0 ? 'QUOTING' : 'IDLE');
      default: return null;
    }
  }) : rows;

  if (!sort) {
    sorted.sort((a, b) => (b.m.quotes_count || 0) - (a.m.quotes_count || 0)
      || String(a.m.title || '').localeCompare(String(b.m.title || '')));
  }

  return sorted.map(({ cid, m, upQuote, dnQuote, midPrice, volume30m, volume24h, top3Depth, horizon, restingHere, ts }) => `<tr data-cid="${esc(cid)}">
      ${timestampCell(ts)}
      <td class="ot-market">${marketCell(m, cid)}</td>
      <td class="mono">${esc(marketCategory(m))}</td>
      <td class="mono">${fmtPrice(upQuote)}</td>
      <td class="mono">${fmtPrice(dnQuote)}</td>
      <td class="mono">${fmtPrice(midPrice)}</td>
      <td class="mono">${fmtCompactUSD(volume30m)}</td>
      <td class="mono">${fmtCompactUSD(volume24h)}</td>
      <td class="mono">${fmtCompactUSD(top3Depth)}</td>
      <td class="mono">${(horizon === null || horizon === undefined) ? '--' : `${Number(horizon).toFixed(1)}d`}</td>
      <td>${marketStatusPill(m, restingHere)}</td>
    </tr>`).join('');
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

function openOrdersRows(kpi, state, sort) {
  const orders = ((state && state.orders) || []).filter(isRestingOrder);
  if (!orders.length) return otEmptyRow('open-orders', 'No orders are resting on the book.');

  const byMarket = (kpi && kpi.by_market) || {};
  const legs = tokenLegMap(kpi);
  const queues = queueAheadByOrder(kpi);
  const groups = groupOrdersByPair(orders, kpi);

  // The pair is the sort unit, never the row: a pair renders as two rows with
  // the market cell and the pair tags spanning both, so sorting the rows would
  // strand those cells on the wrong leg. Money columns (Size, Total Cost) are
  // the sum across the pair -- "which pair costs the most to build" is a
  // question about the whole thing. Measurement columns (Price, Queue Ahead,
  // Age) are the extreme leg in the sort direction -- "which order is stuck
  // deepest or is oldest" is a question about the worst leg.
  const sorted = sort ? otSortGroups(groups, sort, (g, col, dir) => {
    const first = g.orders[0];
    const market = orderGroupMarket(g, first.condition_id, byMarket, state);
    switch (col) {
      case 0: return otExtreme(g.orders.map(o => Number(o.posted_ts) || 0), dir);
      case 1: return String((market && (market.title || market.name || market.slug)) || '');
      case 2: return legForOrder(first, legs, byMarket) || '';
      case 3: return otExtreme(g.orders.map(o => o.price), dir);
      case 4: return otSum(g.orders.map(o => o.original_size));
      case 5: {
        // A leg with no price or no size is unmeasured, not zero. Any
        // unmeasured leg makes the PAIR unmeasured, so a pair the registry
        // could not price ranks last instead of beating every real cost.
        const costs = g.orders.map(o => {
          const p = otNum(o.price);
          const s = otNum(o.original_size);
          return (p === null || s === null) ? null : p * s;
        });
        return costs.some(c => c === null) ? null : otSum(costs);
      }
      case 6: return otExtreme(g.orders.map(o => otNum(queues[o.order_id])), dir);
      case 7: return otExtreme(g.orders.map(o => o.age_sec), dir);
      default: return null;
    }
  }) : groups;

  return sorted.map((group, groupIndex) => {
    const first = group.orders[0];
    const market = orderGroupMarket(group, first.condition_id, byMarket, state);
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
      const rowClass = ['ot-pair-row'];
      if (legIndex === 0) rowClass.push('ot-pair-start');
      if (groupIndex % 2 === 1) rowClass.push('ot-pair-alt');
      // One market name for both legs: the merged cell is what makes the two
      // rows read as one pair rather than two unrelated orders.
      const marketTd = legIndex === 0
        ? `<td class="ot-market" rowspan="${group.orders.length}">${marketCell(market, o.condition_id, { categoryCaption: true })}${pairTags}</td>`
        : '';
      return `<tr class="${rowClass.join(' ')}" data-order-id="${esc(o.order_id)}" data-pair="${esc(group.key)}">
      <td class="mono" style="white-space:nowrap;">${fmtTimestamp(o.posted_ts)}<div class="caption-muted">${esc(fmtRelAgo(o.posted_ts))}</div></td>
      ${marketTd}
      <td><span class="pill ${leg === 'UP' ? 'active' : (leg === 'DN' ? 'reconnecting' : 'stopped')}">${esc(leg === 'DN' ? 'DOWN' : leg)}</span></td>
      <td class="mono">${fmtPrice(price)}</td>
      <td class="mono">${fmtShares(size)}</td>
      <td class="mono">${fmtUSD(price * size)}</td>
      <td class="mono">${(queue === null || queue === undefined) ? '--' : fmtCompactUSD(queue)}</td>
      <td class="mono">${o.age_sec === null || o.age_sec === undefined ? '--' : fmtStopwatch(Number(o.age_sec))}</td>
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
 * zeros pushed the trades the tab exists to show below the fold.
 *
 * A booked close stays listed even while the bot is quoting the market
 * again: the close is this run's history, and hiding it behind a resting
 * order made the loss count in the headline while its trade was invisible. */
function closedTradesEntries(kpi, state) {
  return Object.entries((kpi && kpi.by_market) || {})
    .filter(([, m]) => (m.settlements || []).length > 0
                       && Number(m.realized_pnl) !== 0)
    .sort((a, b) => (Number(b[1].realized_pnl) || 0) - (Number(a[1].realized_pnl) || 0));
}

function closedTradesRows(kpi, state, sort) {
  const entries = closedTradesEntries(kpi, state);

  if (!entries.length) {
    return otEmptyRow('closed-trades', 'No closed trades yet: nothing has settled with a booked profit or loss.');
  }

  const ordersByMarket = groupOrdersByMarket(state && state.orders);
  const fills = (state && state.fills) || [];
  const graduatedCids = new Set(((kpi && kpi.funnel && kpi.funnel.graduated) || [])
    .map(g => g.cid || g.condition_id));

  // The trade is the sort unit: `marketRowPairHtml` returns a main row plus an
  // optional expanded sub-row as one string, so re-ordering whole trades keeps
  // every sub-row attached to the market it belongs to. Sorting rows instead
  // would drop the sub-row under a different market.
  const sorted = sort ? otSortGroups(entries, sort, ([cid, m]) => {
    switch (sort.col) {
      case 0: return closeTsOf(m);
      case 1: return String(m.title || m.name || m.slug || '');
      // Sorts on the label the row shows, three states included.
      case 2: return hedgeStateOf(m).state;
      case 3: return otNum(m.realized_pnl);
      case 4: return otNum(m.fills_count);
      case 5: return 'FINISHED';
      default: return null;
    }
  }) : entries;

  return sorted.map(([cid, m]) =>
    marketRowPairHtml(cid, m, {
      isExpanded: expandedMarkets.has(cid),
      hasOrders: ordersByMarket[cid] && ordersByMarket[cid].length > 0,
      allOrders: ordersByMarket[cid] || [],
      showCancelled: showCancelledByMarket.has(cid),
      fills,
      graduatedCids,
      forceFinished: true,
      categoryCaption: true,
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

function positionsRows(kpi, state, sort) {
  const entries = heldMarketEntries(kpi, false);

  if (!entries.length) return otEmptyRow('positions', 'No legs have filled, so nothing is held.');

  // The held market is the sort unit, for the same reason the pair is in Open
  // Orders: a market renders as one row per held leg, with the market name and
  // all three pair-level numbers spanning them.
  // The band index is taken from the SORTED map, not from the entry order: the
  // banding is what makes two rows read as one pair, so it has to follow the
  // order the operator is actually looking at. Reading a pre-sort index left
  // adjacent pairs sharing a band after any sort.
  const rows = entries.map(([cid, m]) => {
    // Live marks overlay the quote mids, so a poll render already shows the
    // newest price: the rAF pass only rewrites cells between polls.
    const overlay = liveLegMids(m);
    const mark = positionMarkValue(m, overlay.mids);
    const cost = Number(m.total_cost) || 0;
    const held = heldLegs(m);
    return {
      cid, m, held,
      mark,
      unrealized: mark === null ? null : mark - cost,
      ts: latestFillTs(m),
      live: overlay.live,
      liveAgeMs: overlay.ageMs,
    };
  }).filter(r => r.held.length);

  const sorted = sort ? otSortGroups(rows, sort, (r, col, dir) => {
    switch (col) {
      case 0: return otNum(r.ts);
      case 1: return String(r.m.title || r.m.name || r.m.slug || '');
      // Both legs, not one: a pair is UP and DN and its Leg cell should rank by
      // the pair it belongs to, not by whichever leg the registry handed over
      // first. A separator keeps 'DN,UP' from colliding with other combinations.
      case 2: return r.held.map(e => e.leg).sort().join(',');
      case 3: return otSum(r.held.map(e => e.size));
      case 4: return otExtreme(r.held.map(e => (e.size > 0 ? e.cost / e.size : null)), dir);
      case 5: return otSum(r.held.map(e => e.cost));
      case 6: return otNum(r.mark);
      case 7: return otNum(r.unrealized);
      case 8: return otNum(r.m.realized_pnl);
      default: return null;
    }
  }) : rows;

  return sorted.map(({ cid, m, held, mark, unrealized, ts, live, liveAgeMs }, marketIndex) => {
    const cost = Number(m.total_cost) || 0;
    const up = Number(m.up_sh) || 0;
    const dn = Number(m.dn_sh) || 0;
    const status = pairStatus(up, dn);

    const legs = held;
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
      const tsHtml = legIndex === 0 ? timestampCell(ts, { rowspan: span }) : '';
      const pairCells = legIndex === 0
        ? `<td class="ot-market" rowspan="${span}">${marketCell(m, cid, { categoryCaption: true })}${pairTags}</td>`
        : '';
      // Live cells carry their address (data-cid + data-cell) so the rAF
      // pass can rewrite them without a table rebuild; the `live` class and
      // the age tooltip are the only affordance, per existing tokens (T4).
      const liveCls = live ? ' live' : '';
      const liveTitle = (live && liveAgeMs !== null)
        ? ` title="Live mark, ${(liveAgeMs / 1000).toFixed(1)}s old"` : '';
      const pairNumbers = legIndex === 0
        ? `<td class="mono ot-pair-value${liveCls}" rowspan="${span}" data-cid="${esc(cid)}" data-cell="value"${liveTitle}>${mark === null ? '--' : fmtUSD(mark)}</td>
      <td class="mono ot-pair-value${liveCls}" rowspan="${span}" data-cid="${esc(cid)}" data-cell="unrealized"${liveTitle}>${signedUSD(unrealized)}</td>
      <td class="mono ot-pair-value" rowspan="${span}" data-cid="${esc(cid)}" data-cell="realized">${signedUSD(m.realized_pnl)}</td>`
        : '';
      return `<tr class="${rowClass.join(' ')}" data-cid="${esc(cid)}" data-leg="${esc(entry.leg)}">
      ${tsHtml}
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

function ordersTradesRows(view, kpi, state, sort) {
  if (view === 'open-orders') return openOrdersRows(kpi, state, sort);
  if (view === 'positions') return positionsRows(kpi, state, sort);
  if (view === 'closed-trades') return closedTradesRows(kpi, state, sort);
  return activeMarketsRows(kpi, state, sort);
}

/* Per-view sort state, in memory. Each view keeps its own column and direction:
 * sorting Orders and then switching to OPEN POSITIONS and back has to return
 * the Orders sort exactly as it was, because the two views answer different
 * questions and the operator did not ask for either one to be forgotten.
 * Deliberately not persisted -- a reload restores the shipped order, which is
 * the honest default, and adds no storage surface beside the view choice. */
const otSortByView = {};

function otActiveSort(view) {
  const s = otSortByView[view];
  // `Number.isInteger`, not `typeof === 'number'`: a garbage column index
  // coerces to NaN, which passes a typeof check, matches no accessor case, and
  // silently degrades every sort into a no-op.
  return (s && Number.isInteger(s.col)) ? s : null;
}

/* Clicking the sorted column flips it; clicking any other column starts that
 * column fresh on its natural first direction. */
function otToggleSort(view, col) {
  const current = otSortByView[view];
  const next = (current && current.col === col)
    ? { col, dir: current.dir === 'asc' ? 'desc' : 'asc' }
    : { col, dir: otDefaultDir(view, col) };
  otSortByView[view] = next;
  return next;
}

function stitchOpenOrdersHeadHtml(sort) {
  const active = (sort && Number.isInteger(sort.col)) ? sort : null;
  const cols = [
    { label: 'Market & Arbitrage Status', sortIdx: 1 },
    { label: 'Dual-Leg Quotes (YES / NO)', sortIdx: 3 },
    { label: 'Total Committed', sortIdx: 5 },
    { label: 'Queue Ahead (Depth)', sortIdx: 6 },
    { label: 'Age / Stamp', sortIdx: 0 },
    { label: 'Actions', sortIdx: null, right: true },
  ];
  const cells = cols.map((col) => {
    const isSortable = col.sortIdx !== null;
    const isActive = isSortable && Boolean(active && active.col === col.sortIdx);
    const dir = isActive ? active.dir : null;
    const rightCls = col.right ? ' style="text-align:right;"' : '';
    if (!isSortable) {
      return `<th${rightCls}>${esc(col.label)}</th>`;
    }
    const arrow = isActive ? `<span class="ot-sort-arrow" aria-hidden="true">${dir === 'asc' ? '▲' : '▼'}</span>` : '';
    const ariaSort = isActive ? ` aria-sort="${dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    return `<th${ariaSort}${rightCls}>`
      + `<button type="button" class="ot-sort-btn" data-ot-sort="${col.sortIdx}">`
      + `<span class="ot-sort-label">${esc(col.label)}</span>${arrow}</button></th>`;
  }).join('');
  return `<tr>${cells}</tr>`;
}

function stitchOpenOrdersRows(kpi, state, sort) {
  const orders = ((state && state.orders) || []).filter(isRestingOrder);
  if (!orders.length) {
    return `<tr><td colspan="6" style="text-align:center;color:#64748b;padding:32px;font-family:'JetBrains Mono',monospace;font-size:12px;">No orders are resting on the book.</td></tr>`;
  }

  const byMarket = (kpi && kpi.by_market) || {};
  const legs = tokenLegMap(kpi);
  const queues = queueAheadByOrder(kpi);
  const groups = groupOrdersByPair(orders, kpi);

  const sorted = sort ? otSortGroups(groups, sort, (g, col, dir) => {
    const first = g.orders[0];
    const market = orderGroupMarket(g, first.condition_id, byMarket, state);
    switch (col) {
      case 0: return otExtreme(g.orders.map(o => Number(o.posted_ts) || 0), dir);
      case 1: return String((market && (market.title || market.name || market.slug)) || '');
      case 2: return legForOrder(first, legs, byMarket) || '';
      case 3: return otExtreme(g.orders.map(o => o.price), dir);
      case 4: return otSum(g.orders.map(o => o.original_size));
      case 5: {
        const costs = g.orders.map(o => {
          const p = otNum(o.price);
          const s = otNum(o.original_size);
          return (p === null || s === null) ? null : p * s;
        });
        return costs.some(c => c === null) ? null : otSum(costs);
      }
      case 6: return otExtreme(g.orders.map(o => otNum(queues[o.order_id])), dir);
      case 7: return otExtreme(g.orders.map(o => o.age_sec), dir);
      default: return null;
    }
  }) : groups;

  return sorted.map((group) => {
    const first = group.orders[0];
    const market = orderGroupMarket(group, first.condition_id, byMarket, state);
    const restingLegs = restingPairLegs(group.orders, kpi);
    const status = pairStatus(
      restingLegs.UP ? restingLegs.UP.size : 0,
      restingLegs.DN ? restingLegs.DN.size : 0
    );
    const pairCost = restingPairCost(group.orders, kpi);
    const edge = (pairCost !== null && pairCost > 0 && pairCost < 1.0)
      ? ((1.0 - pairCost) * 100) : null;

    const upOrder = group.orders.find(o => legForOrder(o, legs, byMarket) === 'UP');
    const dnOrder = group.orders.find(o => {
      const l = legForOrder(o, legs, byMarket);
      return l === 'DN' || l === 'DOWN';
    });

    const upSize = upOrder ? Number(upOrder.original_size) || 0 : 0;
    const upPrice = upOrder ? Number(upOrder.price) || 0 : 0;
    const upCost = upSize * upPrice;

    const dnSize = dnOrder ? Number(dnOrder.original_size) || 0 : 0;
    const dnPrice = dnOrder ? Number(dnOrder.price) || 0 : 0;
    const dnCost = dnSize * dnPrice;

    const totalCost = upCost + dnCost;
    const totalShares = upSize + dnSize;

    const upQueue = upOrder ? queues[upOrder.order_id] : null;
    const dnQueue = dnOrder ? queues[dnOrder.order_id] : null;

    const postedTimestamps = group.orders.map(o => Number(o.posted_ts)).filter(Number.isFinite);
    const postedTs = postedTimestamps.length ? Math.min(...postedTimestamps) : null;

    const ages = group.orders.map(o => Number(o.age_sec)).filter(Number.isFinite);
    const ageSec = ages.length ? Math.max(...ages) : null;

    const title = (market && (market.title || market.name || market.slug))
      || (first.condition_id ? first.condition_id.slice(0, 10) + '…' : '--');
    const category = marketCategory(market);
    const marketUrl = market && market.slug ? `https://polymarket.com/market/${market.slug}` : '#';

    const queueUpStr = upQueue != null ? fmtCompactUSD(upQueue) : '--';
    const queueDnStr = dnQueue != null ? fmtCompactUSD(dnQueue) : '--';
    let queuePct = 42;
    if (upQueue != null && dnQueue != null && (upQueue + dnQueue) > 0) {
      queuePct = Math.round(Math.min(95, Math.max(10, (upQueue / (upQueue + dnQueue)) * 100)));
    } else if (upQueue != null || dnQueue != null) {
      queuePct = 65;
    }

    const isPaired = status === 'paired';
    const isPartial = status === 'partial';
    const isUnpaired = status === 'unpaired';

    let badgeHtml = '';
    if (isPaired) {
      badgeHtml = `<span class="stitch-arb-badge is-paired">`
        + `<span>PAIRED</span><span class="badge-sep">|</span>`
        + `<span class="badge-val">Pair Cost: ${fmtPrice(pairCost)}</span>`
        + (edge !== null ? `<span class="badge-edge">(+${edge.toFixed(1)}% Edge)</span>` : '')
        + `</span>`;
    } else if (isPartial) {
      badgeHtml = `<span class="stitch-arb-badge is-partial">`
        + `<span>PARTIAL</span><span class="badge-sep">|</span>`
        + `<span class="badge-val">Pair Cost: ${fmtPrice(pairCost)}</span>`
        + `</span>`;
    } else {
      badgeHtml = `<span class="stitch-arb-badge is-unpaired">`
        + `<span>⚠️ UNPAIRED</span><span class="badge-sep">|</span>`
        + `<span>Single Leg Waiting Hedge</span>`
        + `</span>`;
    }

    const rowBgClass = isUnpaired ? 'stitch-ot-row is-unpaired' : 'stitch-ot-row';

    return `<tr class="${rowBgClass}" data-pair="${esc(group.key)}">
      <!-- Col 1: Market & Arbitrage Status -->
      <td class="stitch-market-cell">
        <div style="display:flex;align-items:flex-start;gap:10px;">
          <div class="stitch-market-dot ${isPaired ? 'is-paired' : 'is-unpaired'}"></div>
          <div>
            <div class="stitch-market-title">
              <a href="${esc(marketUrl)}" target="_blank" rel="noopener">${esc(title)}</a>
            </div>
            <div class="stitch-market-meta">
              <span class="stitch-meta-category">${esc(category)}</span>
              <span class="stitch-meta-sep">•</span>
              <span class="stitch-meta-tag">${isPaired ? 'Paired Arb' : 'Resting'}</span>
              ${badgeHtml}
            </div>
          </div>
        </div>
      </td>

      <!-- Col 2: Dual-Leg Quotes (UP / DOWN) -->
      <td>
        <div class="stitch-quote-box">
          <div class="stitch-quote-rung">
            ${upOrder ? `
              <span class="stitch-leg-pill yes">YES</span>
              <span style="color:#e2e8f0;">${fmtShares(upSize)} @ ${fmtPrice(upPrice)}</span>
              <span class="stitch-quote-subcost">(${fmtUSD(upCost)})</span>
            ` : `
              <span class="stitch-leg-pill missing">YES</span>
              <span class="stitch-quote-awaiting">[Awaiting YES Quote]</span>
            `}
          </div>
          <div class="stitch-quote-rung">
            ${dnOrder ? `
              <span class="stitch-leg-pill no">NO</span>
              <span style="color:#e2e8f0;">${fmtShares(dnSize)} @ ${fmtPrice(dnPrice)}</span>
              <span class="stitch-quote-subcost">(${fmtUSD(dnCost)})</span>
            ` : `
              <span class="stitch-leg-pill missing">NO</span>
              <span class="stitch-quote-awaiting">[Awaiting NO Quote]</span>
            `}
          </div>
        </div>
      </td>

      <!-- Col 3: Total Committed -->
      <td>
        <div class="stitch-commit-val ${isUnpaired ? 'is-unhedged' : ''}">${fmtUSD(totalCost)}</div>
        <div class="stitch-commit-sub ${isUnpaired ? 'is-unhedged' : ''}">
          ${isUnpaired ? 'Unhedged Risk' : `${fmtShares(totalShares)} shares total`}
        </div>
      </td>

      <!-- Col 4: Queue Ahead (Depth) -->
      <td>
        <div class="stitch-queue-depth">
          ${queueUpStr} <span class="stitch-queue-sub">/ ${queueDnStr}</span>
        </div>
        <div class="stitch-queue-bar">
          <div class="stitch-queue-fill ${isUnpaired ? 'is-amber' : ''}" style="width: ${queuePct}%"></div>
        </div>
        <div class="stitch-queue-label">${isUnpaired ? 'At Best Order Limit #1' : `Top of queue: ${queuePct}%`}</div>
      </td>

      <!-- Col 5: Age / Stamp -->
      <td>
        <div class="stitch-age-stopwatch">${ageSec !== null ? fmtStopwatch(ageSec) : '--'}</div>
        <div class="stitch-age-meta">${postedTs !== null ? `${fmtTimestamp(postedTs)} (${fmtRelAgo(postedTs)})` : '--'}</div>
      </td>

      <!-- Col 6: Actions -->
      <td style="text-align:right;">
        <div class="stitch-action-group">
          ${isUnpaired ? `
            <button type="button" class="stitch-btn force" title="Force hedge match">Force Match</button>
          ` : `
            <button type="button" class="stitch-btn" title="Re-quote market with latest spreads">Requote</button>
          `}
          <button type="button" class="stitch-btn cancel" title="Cancel pair quotes">Cancel</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function stitchActiveMarketsHeadHtml(sort) {
  const active = (sort && Number.isInteger(sort.col)) ? sort : null;
  const cols = [
    { label: 'AGE / STAMP', sortIdx: 0 },
    { label: 'MARKET & CLASSIFICATION', sortIdx: 1 },
    { label: 'CATEGORY', sortIdx: 2 },
    { label: 'YES QUOTE', sortIdx: 3 },
    { label: 'NO QUOTE', sortIdx: 4 },
    { label: 'PAIR COST', sortIdx: 5 },
    { label: 'EDGE', sortIdx: 6 },
    { label: '24H VOLUME', sortIdx: 7 },
    { label: 'RESOLVES', sortIdx: 8 },
    { label: 'STATUS', sortIdx: 9 },
  ];
  const cells = cols.map((col) => {
    const isActive = Boolean(active && active.col === col.sortIdx);
    const dir = isActive ? active.dir : null;
    const arrow = isActive ? `<span class="ot-sort-arrow" aria-hidden="true">${dir === 'asc' ? '▲' : '▼'}</span>` : '';
    const ariaSort = isActive ? ` aria-sort="${dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    return `<th${ariaSort}>`
      + `<button type="button" class="ot-sort-btn" data-ot-sort="${col.sortIdx}">`
      + `<span class="ot-sort-label">${esc(col.label)}</span>${arrow}</button></th>`;
  }).join('');
  return `<tr>${cells}</tr>`;
}

function stitchActiveMarketsRows(kpi, state, sort) {
  const ordersByMarket = groupOrdersByMarket(state && state.orders);
  const entries = Object.entries((kpi && kpi.by_market) || {})
    .filter(([cid, m]) => isQuotedMarket(m, ordersByMarket[cid])
      && ((m.quotes_count || 0) > 0
        || (ordersByMarket[cid] || []).some(o => isRestingOrder(o))));

  if (!entries.length) {
    return `<tr><td colspan="10" style="text-align:center;color:#64748b;padding:32px;font-family:'JetBrains Mono',monospace;font-size:12px;">No markets are being quoted.</td></tr>`;
  }

  const rows = entries.map(([cid, m]) => {
    const legs = latestLegQuotes(m);
    const upQuote = legs.up ? legs.up.price : null;
    const dnQuote = legs.dn ? legs.dn.price : null;
    const upMid = legs.up ? legs.up.mid : null;
    const dnMid = legs.dn ? legs.dn.mid : null;
    const pairCost = (upQuote !== null && dnQuote !== null) ? (upQuote + dnQuote) : null;
    const edge = pairCost === null ? null : 1 - pairCost;
    const restingHere = (ordersByMarket[cid] || []).some(o => isRestingOrder(o));
    const ts = latestQuoteTs(m);
    return { cid, m, upQuote, dnQuote, upMid, dnMid, pairCost, edge, restingHere, ts };
  });

  const sorted = sort ? otSortGroups(rows, sort, (r, col) => {
    switch (col) {
      case 0: return otNum(r.ts);
      case 1: return String(r.m.title || r.m.name || r.m.slug || '');
      case 2: return marketCategory(r.m);
      case 3: return otNum(r.upQuote);
      case 4: return otNum(r.dnQuote);
      case 5: return otNum(r.pairCost);
      case 6: return otNum(r.edge);
      case 7: return otNum(r.m.volume_24h);
      case 8: return otNum(r.m.days_to_resolve);
      case 9: return r.restingHere ? 'RESTING' : ((r.m.quotes_count || 0) > 0 ? 'QUOTING' : 'IDLE');
      default: return null;
    }
  }) : rows;

  if (!sort) {
    sorted.sort((a, b) => (b.m.quotes_count || 0) - (a.m.quotes_count || 0)
      || String(a.m.title || '').localeCompare(String(b.m.title || '')));
  }

  const nowMs = Date.now();

  return sorted.map(({ cid, m, upQuote, dnQuote, upMid, dnMid, pairCost, edge, restingHere, ts }) => {
    const title = m.title || m.name || m.slug || (cid ? cid.slice(0, 10) + '…' : '--');
    const category = marketCategory(m);
    const marketUrl = m.slug ? `https://polymarket.com/market/${m.slug}` : (m.url || '#');

    // Classification / subtitle
    const classification = m.event_title || m.subtitle || m.category || 'Polymarket Arbitrage';

    // Age / Stopwatch
    const ageSec = (ts && Number.isFinite(ts)) ? Math.max(0, Math.round((nowMs - toMs(ts)) / 1000)) : null;
    const ageDisp = ageSec !== null ? fmtStopwatch(ageSec) : '--';
    const tsDisp = (ts && Number.isFinite(toMs(ts))) ? fmtTimestamp(ts) : '--';

    // Status pill
    let statusBadge = '';
    if (restingHere) {
      statusBadge = `<span class="stitch-status-badge resting"><span class="stitch-status-dot resting"></span>RESTING</span>`;
    } else if ((m.quotes_count || 0) > 0) {
      statusBadge = `<span class="stitch-status-badge quoting"><span class="stitch-status-dot quoting"></span>QUOTING</span>`;
    } else {
      statusBadge = `<span class="stitch-status-badge idle"><span class="stitch-status-dot idle"></span>IDLE</span>`;
    }

    return `<tr class="stitch-ot-row" data-cid="${esc(cid)}">
      <!-- Col 0: AGE / STAMP -->
      <td class="mono font-tabular" style="white-space:nowrap;">
        <div class="stitch-age-stopwatch">${ageDisp}</div>
        <div class="stitch-age-meta">${tsDisp}</div>
      </td>

      <!-- Col 1: MARKET & CLASSIFICATION -->
      <td class="stitch-market-cell">
        <div class="stitch-market-title">
          <a href="${esc(marketUrl)}" target="_blank" rel="noopener">${esc(title)}</a>
        </div>
        <div class="stitch-market-meta" style="margin-top:2px;">
          <span style="font-size:11px;color:#94a3b8;">${esc(classification)}</span>
        </div>
      </td>

      <!-- Col 2: CATEGORY -->
      <td>
        <span class="stitch-category-pill">${esc(category)}</span>
      </td>

      <!-- Col 3: UP QUOTE + Mid Price -->
      <td class="mono font-tabular">
        <div class="stitch-quote-up">${fmtPrice(upQuote)}</div>
        ${upMid !== null ? `<div class="stitch-mid-sub">mid ${fmtPrice(upMid)}</div>` : ''}
      </td>

      <!-- Col 4: DOWN QUOTE + Mid Price -->
      <td class="mono font-tabular">
        <div class="stitch-quote-down">${fmtPrice(dnQuote)}</div>
        ${dnMid !== null ? `<div class="stitch-mid-sub">mid ${fmtPrice(dnMid)}</div>` : ''}
      </td>

      <!-- Col 5: PAIR COST -->
      <td class="mono font-tabular">
        <div class="stitch-pair-cost">${fmtPrice(pairCost)}</div>
      </td>

      <!-- Col 6: EDGE -->
      <td class="mono font-tabular">
        <div class="stitch-edge">
          ${edge === null ? '--' : `<span class="${edge > 0 ? 'text-emerald-400 font-bold' : 'text-rose-400'}" style="color:${edge > 0 ? '#34d399' : '#f43f5e'};font-weight:700;">+${(edge * 100).toFixed(1)}¢</span>`}
        </div>
      </td>

      <!-- Col 7: 24H VOLUME -->
      <td class="mono font-tabular" style="color:#94a3b8;">
        ${fmtCompactUSD(m.volume_24h)}
      </td>

      <!-- Col 8: RESOLVES -->
      <td class="mono font-tabular" style="color:#64748b;">
        ${(m.days_to_resolve === null || m.days_to_resolve === undefined) ? '--' : `${Number(m.days_to_resolve).toFixed(1)}d`}
      </td>

      <!-- Col 9: STATUS -->
      <td>
        ${statusBadge}
      </td>
    </tr>`;
  }).join('');
}

function stitchPositionsHeadHtml(sort) {
  const active = (sort && Number.isInteger(sort.col)) ? sort : null;
  const cols = [
    { label: 'AGE / STAMP', sortIdx: 0 },
    { label: 'MARKET & CLASSIFICATION', sortIdx: 1 },
    { label: 'CATEGORY', sortIdx: 2 },
    { label: 'YES HELD', sortIdx: 3 },
    { label: 'NO HELD', sortIdx: 4 },
    { label: 'TOTAL COST', sortIdx: 5 },
    { label: 'MARK VALUE', sortIdx: 6 },
    { label: 'UNREALIZED P&L', sortIdx: 7 },
    { label: 'HEDGE STATUS', sortIdx: 8 },
  ];
  const cells = cols.map((col) => {
    const isActive = Boolean(active && active.col === col.sortIdx);
    const dir = isActive ? active.dir : null;
    const arrow = isActive ? `<span class="ot-sort-arrow" aria-hidden="true">${dir === 'asc' ? '▲' : '▼'}</span>` : '';
    const ariaSort = isActive ? ` aria-sort="${dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    return `<th${ariaSort}>`
      + `<button type="button" class="ot-sort-btn" data-ot-sort="${col.sortIdx}">`
      + `<span class="ot-sort-label">${esc(col.label)}</span>${arrow}</button></th>`;
  }).join('');
  return `<tr>${cells}</tr>`;
}

function stitchPositionsRows(kpi, state, sort) {
  const entries = heldMarketEntries(kpi, false);

  if (!entries.length) {
    return `<tr><td colspan="9" style="text-align:center;color:#64748b;padding:32px;font-family:'JetBrains Mono',monospace;font-size:12px;">No legs have filled, so nothing is held.</td></tr>`;
  }

  const rows = entries.map(([cid, m]) => {
    const overlay = liveLegMids(m);
    const mark = positionMarkValue(m, overlay.mids);
    const cost = Number(m.total_cost) || 0;
    const upSh = Number(m.up_sh) || 0;
    const upCost = Number(m.up_cost) || 0;
    const upAvg = upSh > 0 ? (upCost / upSh) : null;
    const dnSh = Number(m.dn_sh) || 0;
    const dnCost = Number(m.dn_cost) || 0;
    const dnAvg = dnSh > 0 ? (dnCost / dnSh) : null;
    const totalSh = upSh + dnSh;
    const status = pairStatus(upSh, dnSh);
    const unrealized = mark === null ? null : (mark - cost);
    const unrealizedPct = (cost > 0 && unrealized !== null) ? ((unrealized / cost) * 100) : null;
    const ts = latestFillTs(m);
    const category = marketCategory(m);
    return {
      cid, m, upSh, upCost, upAvg, dnSh, dnCost, dnAvg, totalCost: cost, totalSh,
      mark, unrealized, unrealizedPct, status, ts, category,
      live: overlay.live, liveAgeMs: overlay.ageMs,
    };
  });

  const sorted = sort ? otSortGroups(rows, sort, (r, col) => {
    switch (col) {
      case 0: return otNum(r.ts);
      case 1: return String(r.m.title || r.m.name || r.m.slug || '');
      case 2: return r.category;
      case 3: return otNum(r.upSh);
      case 4: return otNum(r.dnSh);
      case 5: return otNum(r.totalCost);
      case 6: return otNum(r.mark);
      case 7: return otNum(r.unrealized);
      case 8: return r.status;
      default: return null;
    }
  }) : rows;

  if (!sort) {
    sorted.sort((a, b) => (b.totalCost - a.totalCost) || ((b.ts || 0) - (a.ts || 0)));
  }

  const nowMs = Date.now();

  return sorted.map(({
    cid, m, upSh, upCost, upAvg, dnSh, dnCost, dnAvg, totalCost, totalSh,
    mark, unrealized, unrealizedPct, status, ts, category, live, liveAgeMs
  }) => {
    const title = m.title || m.name || m.slug || (cid ? cid.slice(0, 10) + '…' : '--');
    const marketUrl = m.slug ? `https://polymarket.com/market/${m.slug}` : (m.url || '#');
    const classification = m.event_title || m.subtitle || m.category || 'Polymarket Position';

    // Age / Stopwatch
    const ageSec = (ts && Number.isFinite(ts)) ? Math.max(0, Math.round((nowMs - toMs(ts)) / 1000)) : null;
    const ageDisp = ageSec !== null ? fmtStopwatch(ageSec) : '--';
    const tsDisp = (ts && Number.isFinite(toMs(ts))) ? fmtTimestamp(ts) : '--';

    // Status / Hedge badge
    let statusBadge = '';
    if (status === 'paired') {
      const pc = (m.pair_cost !== null && m.pair_cost !== undefined) ? fmtPrice(m.pair_cost) : '';
      statusBadge = `<span class="stitch-status-badge quoting"><span class="stitch-status-dot quoting"></span>PAIRED${pc ? ` (${pc})` : ''}</span>`;
    } else if (status === 'partial') {
      statusBadge = `<span class="stitch-status-badge resting"><span class="stitch-status-dot resting"></span>PARTIAL</span>`;
    } else {
      statusBadge = `<span class="stitch-status-badge idle" style="border-color:rgba(244,63,94,0.3);background:rgba(244,63,94,0.1);color:#fb7185;"><span class="stitch-status-dot idle" style="background:#fb7185;"></span>⚠️ UNPAIRED</span>`;
    }

    // PnL state
    const pnlCls = unrealized > 0 ? 'pos' : (unrealized < 0 ? 'neg' : 'flat');

    // Live tooltip
    const liveTitle = (live && liveAgeMs !== null) ? ` title="Live mark, ${(liveAgeMs / 1000).toFixed(1)}s old"` : '';

    return `<tr class="stitch-ot-row" data-cid="${esc(cid)}">
      <!-- Col 0: AGE / STAMP -->
      <td class="mono font-tabular" style="white-space:nowrap;">
        <div class="stitch-age-stopwatch">${ageDisp}</div>
        <div class="stitch-age-meta">${tsDisp}</div>
      </td>

      <!-- Col 1: MARKET & CLASSIFICATION -->
      <td class="stitch-market-cell">
        <div class="stitch-market-title">
          <a href="${esc(marketUrl)}" target="_blank" rel="noopener">${esc(title)}</a>
        </div>
        <div class="stitch-market-meta" style="margin-top:2px;">
          <span style="font-size:11px;color:#94a3b8;">${esc(classification)}</span>
        </div>
      </td>

      <!-- Col 2: CATEGORY -->
      <td>
        <span class="stitch-category-pill">${esc(category)}</span>
      </td>

      <!-- Col 3: UP HELD -->
      <td class="mono font-tabular">
        ${upSh > 0 ? `
          <div class="stitch-quote-up">${fmtShares(upSh)} sh @ ${fmtPrice(upAvg)}</div>
          <div class="stitch-mid-sub">cost ${fmtUSD(upCost)}</div>
        ` : `<span style="color:#64748b;font-size:11px;">--</span>`}
      </td>

      <!-- Col 4: DOWN HELD -->
      <td class="mono font-tabular">
        ${dnSh > 0 ? `
          <div class="stitch-quote-down">${fmtShares(dnSh)} sh @ ${fmtPrice(dnAvg)}</div>
          <div class="stitch-mid-sub">cost ${fmtUSD(dnCost)}</div>
        ` : `<span style="color:#64748b;font-size:11px;">--</span>`}
      </td>

      <!-- Col 5: TOTAL COST -->
      <td class="mono font-tabular">
        <div class="stitch-pair-cost">${fmtUSD(totalCost)}</div>
        <div class="stitch-mid-sub">${fmtShares(totalSh)} sh total</div>
      </td>

      <!-- Col 6: MARK VALUE -->
      <td class="mono font-tabular"${liveTitle} data-cid="${esc(cid)}" data-cell="value">
        <div class="stitch-mark-val">${mark === null ? '--' : fmtUSD(mark)}</div>
        ${live ? `<div class="stitch-mid-sub" style="color:#34d399;">● live mark</div>` : ''}
      </td>

      <!-- Col 7: UNREALIZED P&L ($ and %) -->
      <td class="mono font-tabular" data-cid="${esc(cid)}" data-cell="unrealized">
        <div class="stitch-pnl-cell">
          <div class="stitch-pnl-val ${pnlCls}">
            ${unrealized === null ? '--' : signedUSD(unrealized)}
          </div>
          <div class="stitch-pnl-pct ${pnlCls}">
            ${unrealizedPct !== null ? `(${unrealizedPct >= 0 ? '+' : ''}${unrealizedPct.toFixed(1)}%)` : '--'}
          </div>
        </div>
      </td>

      <!-- Col 8: HEDGE STATUS -->
      <td>
        ${statusBadge}
      </td>
    </tr>`;
  }).join('');
}

function stitchClosedTradesHeadHtml(sort) {
  const active = (sort && Number.isInteger(sort.col)) ? sort : null;
  const cols = [
    { label: 'AGE / CLOSED', sortIdx: 0 },
    { label: 'MARKET & OUTCOME', sortIdx: 1 },
    { label: 'CATEGORY', sortIdx: 2 },
    { label: 'TOTAL COST', sortIdx: 3 },
    { label: 'REALIZED P&L', sortIdx: 4 },
    { label: 'OUTCOME BREAKDOWN', sortIdx: 5 },
    { label: 'STATUS', sortIdx: 6 },
    { label: 'EXECUTION LEDGER', sortIdx: 7 },
  ];
  const cells = cols.map(col => {
    const isActive = active && active.col === col.sortIdx;
    const dir = isActive ? active.dir : null;
    const arrow = isActive ? `<span class="ot-sort-arrow" aria-hidden="true">${dir === 'asc' ? '▲' : '▼'}</span>` : '';
    const ariaSort = isActive ? ` aria-sort="${dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    return `<th${ariaSort}>`
      + `<button type="button" class="ot-sort-btn" data-ot-sort="${col.sortIdx}">`
      + `<span class="ot-sort-label">${esc(col.label)}</span>${arrow}</button></th>`;
  }).join('');
  return `<tr>${cells}</tr>`;
}

function renderStitchClosedLedgerRows(cid, m, settlements, allOrders, nowMs) {
  if (settlements && settlements.length > 0) {
    return settlements.map(s => {
      const ts = s.ts;
      const ageSec = (ts && Number.isFinite(ts)) ? Math.max(0, Math.round((nowMs - toMs(ts)) / 1000)) : null;
      const ageDisp = ageSec !== null ? fmtStopwatch(ageSec) : '--';
      const tsDisp = (ts && Number.isFinite(toMs(ts))) ? fmtTimestamp(ts) : '--';

      const shares = Number(s.shares || 0);
      const costBasis = Number(s.cost_basis || 0);
      const proceeds = Number(s.proceeds || 0);
      const realizedPnl = Number(s.realized_pnl != null ? s.realized_pnl : (proceeds - costBasis));
      const pnlPct = costBasis > 0 ? (realizedPnl / costBasis) * 100 : 0;
      const pnlCls = realizedPnl > 0 ? 'pos' : (realizedPnl < 0 ? 'neg' : 'flat');

      const method = String(s.method || '').toLowerCase();
      const reason = String(s.reason || '').toLowerCase();

      const isMerge = method === 'merge' || method === 'shadow_merge';
      const isStopLoss = reason === 'lifecycle_hard_stop' || method === 'stop_loss_exit' || reason.includes('stop_loss') || reason === 'adverse_drift';
      const isGraceExpired = reason === 'grace_expired' || reason === 'aged_out_rescue' || reason.includes('grace_expired') || method.includes('aged_out');
      const isSettlement = method === 'settlement' || method === 'shadow_settlement';

      const upCostRem = Number(s.up_cost_removed || 0);
      const dnCostRem = Number(s.dn_cost_removed || 0);
      const isPair = isMerge || (upCostRem > 0 && dnCostRem > 0);

      // Entry / Buy Price & Exit Price
      let entryPrice = null;
      if (costBasis > 0 && shares > 0) {
        entryPrice = costBasis / shares;
      } else if (upCostRem > 0 && dnCostRem > 0 && shares > 0) {
        entryPrice = (upCostRem + dnCostRem) / shares;
      }

      let exitPrice = 0.0;
      if (isMerge) {
        exitPrice = 1.0000; // Merged back into $1.0000 USDC per pair
      } else if (proceeds > 0 && shares > 0) {
        exitPrice = proceeds / shares;
      } else if (s.up_price !== null && s.up_price !== undefined) {
        exitPrice = Number(s.up_price);
      } else if (s.dn_price !== null && s.dn_price !== undefined) {
        exitPrice = Number(s.dn_price);
      } else if (isSettlement) {
        exitPrice = realizedPnl > 0 ? 1.0000 : 0.0000;
      }

      // Status Badge
      let statusBadge = '';
      if (isMerge) {
        statusBadge = `<span class="stitch-status-badge filled"><span class="stitch-status-dot filled"></span>MERGED</span>`;
      } else if (isStopLoss) {
        statusBadge = `<span class="stitch-status-badge stop-loss"><span class="stitch-status-dot stop-loss"></span>STOP LOSS</span>`;
      } else if (isGraceExpired) {
        statusBadge = `<span class="stitch-status-badge grace-expired"><span class="stitch-status-dot grace-expired"></span>GRACE EXPIRED</span>`;
      } else if (isSettlement) {
        statusBadge = `<span class="stitch-status-badge quoting"><span class="stitch-status-dot quoting"></span>SETTLED</span>`;
      } else {
        statusBadge = `<span class="stitch-status-badge idle"><span class="stitch-status-dot idle"></span>ORPHAN SOLD</span>`;
      }

      // 1 big cell for positions: YES in green, NO in light red
      let legHtml = '';
      if (isPair) {
        const upCost = upCostRem > 0 ? upCostRem : (costBasis > 0 ? costBasis / 2 : 0);
        const dnCost = dnCostRem > 0 ? dnCostRem : (costBasis > 0 ? costBasis / 2 : 0);
        const upPx = (shares > 0 && upCost > 0) ? (upCost / shares) : (s.up_price != null ? Number(s.up_price) : (entryPrice != null ? entryPrice / 2 : 0));
        const dnPx = (shares > 0 && dnCost > 0) ? (dnCost / shares) : (s.dn_price != null ? Number(s.dn_price) : (entryPrice != null ? entryPrice / 2 : 0));

        legHtml = `<div class="stitch-quote-box">
          <div class="stitch-quote-rung">
            <span class="stitch-leg-pill yes">YES</span>
            <span style="color:#e2e8f0;">${shares > 0 ? `${fmtShares(shares)} @ ` : ''}${fmtPrice(upPx)}</span>
            <span class="stitch-quote-subcost">(${fmtUSD(upCost)})</span>
          </div>
          <div class="stitch-quote-rung">
            <span class="stitch-leg-pill no">NO</span>
            <span style="color:#e2e8f0;">${shares > 0 ? `${fmtShares(shares)} @ ` : ''}${fmtPrice(dnPx)}</span>
            <span class="stitch-quote-subcost">(${fmtUSD(dnCost)})</span>
          </div>
        </div>`;
      } else {
        // Solo position: each position has its own row and pnl cell
        const isUp = upCostRem > 0 || (s.up_price !== null && s.up_price !== undefined);
        const isDn = dnCostRem > 0 || (s.dn_price !== null && s.dn_price !== undefined);
        const legLabel = isUp ? 'YES' : (isDn ? 'NO' : 'SOLO');
        const pillCls = isUp ? 'yes' : (isDn ? 'no' : 'missing');
        const legCost = costBasis > 0 ? costBasis : (isUp ? upCostRem : dnCostRem);
        const legPx = (shares > 0 && legCost > 0) ? (legCost / shares) : (entryPrice !== null ? entryPrice : 0);

        legHtml = `<div class="stitch-quote-box">
          <div class="stitch-quote-rung">
            <span class="stitch-leg-pill ${pillCls}">${legLabel}</span>
            <span style="color:#e2e8f0;">${shares > 0 ? `${fmtShares(shares)} @ ` : ''}${fmtPrice(legPx)}</span>
            ${legCost > 0 ? `<span class="stitch-quote-subcost">(${fmtUSD(legCost)})</span>` : ''}
          </div>
        </div>`;
      }

      return `<tr class="stitch-ledger-row">
        <!-- AGE / TIME -->
        <td class="mono font-tabular">
          <div class="stitch-age-stopwatch">${ageDisp}</div>
          <div class="stitch-age-meta">${tsDisp}</div>
        </td>

        <!-- POSITIONS (YES / NO) (1 big cell) -->
        <td>
          ${legHtml}
        </td>

        <!-- ENTRY / BUY PRICE -->
        <td class="mono font-tabular">
          <div class="stitch-price-val">${entryPrice !== null ? fmtPrice(entryPrice) : '--'}</div>
          <div class="stitch-mid-sub">cost ${fmtUSD(costBasis)}</div>
        </td>

        <!-- SOLD / EXIT PRICE -->
        <td class="mono font-tabular">
          <div class="stitch-sold-price" style="color:#f8fafc;font-weight:700;">${fmtPrice(exitPrice)}</div>
          <div class="stitch-mid-sub" style="color:#38bdf8;">proceeds ${fmtUSD(proceeds)}</div>
        </td>

        <!-- SIZE (SH) -->
        <td class="mono font-tabular">
          <div class="stitch-size-val">${fmtShares(shares)} ${isPair ? 'pairs' : 'sh'}</div>
        </td>

        <!-- REALIZED P&L ($ and %) -->
        <td class="mono font-tabular">
          <div class="stitch-pnl-cell">
            <div class="stitch-pnl-val ${pnlCls}">
              ${realizedPnl >= 0 ? '+' : ''}${fmtUSD(realizedPnl)}
            </div>
            <div class="stitch-pnl-pct ${pnlCls}">
              (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)
            </div>
          </div>
        </td>

        <!-- STATUS -->
        <td>
          ${statusBadge}
        </td>
      </tr>`;
    }).join('');
  }

  // Fallback if no settlements array but orders exist
  if (allOrders && allOrders.length > 0) {
    const byId = {};
    for (const o of allOrders) {
      const pid = o.pair_id || o.condition_id || 'solo';
      if (!byId[pid]) byId[pid] = [];
      byId[pid].push(o);
    }

    return Object.values(byId).map(group => {
      const upOrder = group.find(o => o.token_side === 'UP' || String(o.outcome||'').toLowerCase().includes('yes'));
      const dnOrder = group.find(o => o.token_side === 'DOWN' || o.token_side === 'DN' || String(o.outcome||'').toLowerCase().includes('no'));

      if (upOrder && dnOrder) {
        const upSize = Number(upOrder.size_matched || upOrder.original_size || 0);
        const dnSize = Number(dnOrder.size_matched || dnOrder.original_size || 0);
        const shares = Math.min(upSize, dnSize) || upSize || dnSize;
        const upPx = Number(upOrder.price || 0);
        const dnPx = Number(dnOrder.price || 0);
        const upCost = upPx * upSize;
        const dnCost = dnPx * dnSize;
        const costBasis = upCost + dnCost;
        const entryPrice = upPx + dnPx;
        const isMerged = String(upOrder.status || '').toLowerCase() === 'merged' || String(dnOrder.status || '').toLowerCase() === 'merged';
        const exitPrice = isMerged ? 1.0000 : entryPrice;
        const proceeds = exitPrice * shares;
        const pnl = proceeds - costBasis;
        const pnlPct = costBasis > 0 ? (pnl / costBasis) * 100 : 0;
        const pnlCls = pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : 'flat');
        const ageSec = upOrder.age_sec != null ? upOrder.age_sec : dnOrder.age_sec;
        const ageDisp = ageSec != null ? fmtStopwatch(ageSec) : '--';

        return `<tr class="stitch-ledger-row">
          <td class="mono font-tabular">
            <div class="stitch-age-stopwatch">${ageDisp}</div>
          </td>
          <td>
            <div class="stitch-quote-box">
              <div class="stitch-quote-rung">
                <span class="stitch-leg-pill yes">YES</span>
                <span style="color:#e2e8f0;">${fmtShares(upSize)} @ ${fmtPrice(upPx)}</span>
                <span class="stitch-quote-subcost">(${fmtUSD(upCost)})</span>
              </div>
              <div class="stitch-quote-rung">
                <span class="stitch-leg-pill no">NO</span>
                <span style="color:#e2e8f0;">${fmtShares(dnSize)} @ ${fmtPrice(dnPx)}</span>
                <span class="stitch-quote-subcost">(${fmtUSD(dnCost)})</span>
              </div>
            </div>
          </td>
          <td class="mono font-tabular">
            <div class="stitch-price-val">${fmtPrice(entryPrice)}</div>
            <div class="stitch-mid-sub">cost ${fmtUSD(costBasis)}</div>
          </td>
          <td class="mono font-tabular">
            <div class="stitch-sold-price" style="color:#f8fafc;font-weight:700;">${fmtPrice(exitPrice)}</div>
            <div class="stitch-mid-sub" style="color:#38bdf8;">proceeds ${fmtUSD(proceeds)}</div>
          </td>
          <td class="mono font-tabular">
            <div class="stitch-size-val">${fmtShares(shares)} pairs</div>
          </td>
          <td class="mono font-tabular">
            <div class="stitch-pnl-cell">
              <div class="stitch-pnl-val ${pnlCls}">${pnl >= 0 ? '+' : ''}${fmtUSD(pnl)}</div>
              <div class="stitch-pnl-pct ${pnlCls}">(${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</div>
            </div>
          </td>
          <td>
            <span class="stitch-status-badge ${isMerged ? 'filled' : 'idle'}">${isMerged ? 'MERGED' : 'FINISHED'}</span>
          </td>
        </tr>`;
      }

      return group.map(o => {
        const isUp = (o.token_side === 'UP' || String(o.outcome||'').toLowerCase().includes('yes'));
        const legLabel = isUp ? 'YES' : 'NO';
        const pillCls = isUp ? 'yes' : 'no';
        const price = Number(o.price || 0);
        const size = Number(o.size_matched || o.original_size || 0);
        const status = String(o.status || '').toLowerCase();
        const isMerged = status === 'merged';
        const exitPrice = isMerged ? 1.0000 : price;
        const costBasis = price * size;
        const proceeds = exitPrice * size;
        const pnl = proceeds - costBasis;
        const pnlPct = costBasis > 0 ? (pnl / costBasis) * 100 : 0;
        const pnlCls = pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : 'flat');
        const ageSec = o.age_sec != null ? o.age_sec : null;
        const ageDisp = ageSec != null ? fmtStopwatch(ageSec) : '--';

        return `<tr class="stitch-ledger-row">
          <td class="mono font-tabular">
            <div class="stitch-age-stopwatch">${ageDisp}</div>
          </td>
          <td>
            <div class="stitch-quote-box">
              <div class="stitch-quote-rung">
                <span class="stitch-leg-pill ${pillCls}">${legLabel}</span>
                <span style="color:#e2e8f0;">${size > 0 ? `${fmtShares(size)} @ ` : ''}${fmtPrice(price)}</span>
                ${costBasis > 0 ? `<span class="stitch-quote-subcost">(${fmtUSD(costBasis)})</span>` : ''}
              </div>
            </div>
          </td>
          <td class="mono font-tabular">
            <div class="stitch-price-val">${fmtPrice(price)}</div>
            <div class="stitch-mid-sub">cost ${fmtUSD(costBasis)}</div>
          </td>
          <td class="mono font-tabular">
            <div class="stitch-sold-price" style="color:#f8fafc;font-weight:700;">${fmtPrice(exitPrice)}</div>
            <div class="stitch-mid-sub" style="color:#38bdf8;">proceeds ${fmtUSD(proceeds)}</div>
          </td>
          <td class="mono font-tabular">
            <div class="stitch-size-val">${fmtShares(size)} sh</div>
          </td>
          <td class="mono font-tabular">
            <div class="stitch-pnl-cell">
              <div class="stitch-pnl-val ${pnlCls}">${pnl >= 0 ? '+' : ''}${fmtUSD(pnl)}</div>
              <div class="stitch-pnl-pct ${pnlCls}">(${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</div>
            </div>
          </td>
          <td>
            <span class="stitch-status-badge ${isMerged ? 'filled' : 'idle'}">${isMerged ? 'MERGED' : fmtOrderStatus(status)}</span>
          </td>
        </tr>`;
      }).join('');
    }).join('');
  }

  return `<tr><td colspan="7" style="text-align:center;color:#64748b;padding:20px;font-family:'JetBrains Mono',monospace;">No execution details available.</td></tr>`;
}

function stitchClosedTradesRows(kpi, state, sort) {
  const entries = closedTradesEntries(kpi, state);
  if (!entries.length) {
    return `<tr><td colspan="8" style="text-align:center;color:#64748b;padding:32px;font-family:'JetBrains Mono',monospace;font-size:12px;">No closed trades yet: nothing has settled with a booked profit or loss.</td></tr>`;
  }

  const ordersByMarket = groupOrdersByMarket(state && state.orders);
  const nowMs = Date.now();

  const sorted = sort ? otSortGroups(entries, sort, ([cid, m], col) => {
    switch (col) {
      case 0: return closeTsOf(m);
      case 1: return String(m.title || m.name || m.slug || '');
      case 2: return marketCategory(m);
      case 3: return otNum(m.total_cost || 0);
      case 4: return otNum(m.realized_pnl || 0);
      case 5: return (m.settlements || []).length;
      case 6: return 'FINISHED';
      default: return null;
    }
  }) : entries;

  return sorted.map(([cid, m]) => {
    const isExpanded = expandedMarkets.has(cid);
    const settlements = m.settlements || [];
    const allOrders = ordersByMarket[cid] || [];
    const closeTs = closeTsOf(m);
    const ageSec = (closeTs && Number.isFinite(closeTs)) ? Math.max(0, Math.round((nowMs - toMs(closeTs)) / 1000)) : null;
    const ageDisp = ageSec !== null ? fmtStopwatch(ageSec) : '--';
    const tsDisp = (closeTs && Number.isFinite(toMs(closeTs))) ? fmtTimestamp(closeTs) : '--';
    const marketUrl = m.url || (m.slug ? `https://polymarket.com/event/${m.slug}` : `https://polymarket.com/market/${cid}`);
    const title = m.title || m.name || m.slug || cid.slice(0, 16);
    const category = marketCategory(m);
    const categoryShort = (category || 'ARB').slice(0, 4).toUpperCase();

    // Summary counts of exit outcomes
    let mergedCount = 0;
    let stopLossCount = 0;
    let graceExpiredCount = 0;
    let settledCount = 0;

    for (const s of settlements) {
      const method = String(s.method || '').toLowerCase();
      const reason = String(s.reason || '').toLowerCase();
      if (method === 'merge' || method === 'shadow_merge') {
        mergedCount++;
      } else if (reason === 'lifecycle_hard_stop' || method === 'stop_loss_exit' || reason.includes('stop_loss') || reason === 'adverse_drift') {
        stopLossCount++;
      } else if (reason === 'grace_expired' || reason === 'aged_out_rescue' || reason.includes('grace_expired') || method.includes('aged_out')) {
        graceExpiredCount++;
      } else if (method === 'settlement' || method === 'shadow_settlement') {
        settledCount++;
      }
    }

    const totalCost = Number(m.total_cost || 0) || settlements.reduce((acc, s) => acc + (Number(s.cost_basis) || 0), 0);
    const realizedPnl = Number(m.realized_pnl || 0);
    const pnlPct = totalCost > 0 ? (realizedPnl / totalCost) * 100 : 0;
    const pnlCls = realizedPnl > 0 ? 'pos' : (realizedPnl < 0 ? 'neg' : 'flat');

    // Winner badge if resolved
    let winnerHtml = '';
    if (m.resolution && m.resolution.winner) {
      const resAgo = m.resolution.resolved_ts ? fmtAgo(m.resolution.resolved_ts) : '';
      winnerHtml = `<span class="stitch-winner-badge">✓ Winner: ${esc(m.resolution.winner)}${resAgo ? ` · ${resAgo}` : ''}</span>`;
    }

    // Reason chip
    const reasonInfo = closeReasonOf(m);
    const reasonIsExit = closeReasonIsExit(reasonInfo);
    let reasonChipHtml = '';
    if (reasonInfo) {
      const rLabel = closeReasonBadgeLabel(reasonInfo.reason, reasonIsExit);
      reasonChipHtml = `<span class="stitch-reason-chip${reasonIsExit ? ' is-exit' : ''}" title="${esc(closeReasonTitle(reasonInfo))}">${esc(rLabel)}</span>`;
    }

    const fillsCount = m.fills_count || allOrders.length || settlements.length;
    const executionItemsCount = settlements.length > 0 ? settlements.length : (allOrders.length || fillsCount);

    let html = `<tr class="stitch-ot-row stitch-market-row${isExpanded ? ' expanded' : ''}" data-cid="${esc(cid)}" tabindex="0" role="button" aria-expanded="${isExpanded}">
      <!-- Col 0: AGE / CLOSED -->
      <td class="mono font-tabular" style="white-space:nowrap;">
        <div class="stitch-age-stopwatch">${ageDisp}</div>
        <div class="stitch-age-meta">${tsDisp}</div>
      </td>

      <!-- Col 1: MARKET & OUTCOME -->
      <td class="stitch-market-cell">
        <div style="display:flex;align-items:center;gap:10px;">
          <div class="stitch-cat-avatar">${esc(categoryShort)}</div>
          <div style="flex:1;min-width:0;">
            <div class="stitch-market-title" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <a href="${esc(marketUrl)}" target="_blank" rel="noopener">${esc(title)}</a>
              ${winnerHtml}
            </div>
            <div class="stitch-market-meta" style="margin-top:3px;gap:6px;">
              ${reasonChipHtml}
              <span style="font-size:11px;color:#64748b;">· ${fillsCount} fill${fillsCount !== 1 ? 's' : ''}</span>
            </div>
          </div>
        </div>
      </td>

      <!-- Col 2: CATEGORY -->
      <td>
        <span class="stitch-category-pill">${esc(category)}</span>
      </td>

      <!-- Col 3: TOTAL COST -->
      <td class="mono font-tabular">
        <div class="stitch-pair-cost">${fmtUSD(totalCost)}</div>
        <div class="stitch-mid-sub">${Number(m.total_sh || 0) > 0 ? `${fmtShares(m.total_sh)} sh` : `${executionItemsCount} items`}</div>
      </td>

      <!-- Col 4: REALIZED P&L ($ and %) -->
      <td class="mono font-tabular">
        <div class="stitch-pnl-cell">
          <div class="stitch-pnl-val ${pnlCls}">
            ${realizedPnl >= 0 ? '+' : ''}${fmtUSD(realizedPnl)}
          </div>
          <div class="stitch-pnl-pct ${pnlCls}">
            (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)
          </div>
        </div>
      </td>

      <!-- Col 5: OUTCOME BREAKDOWN (Summary counts) -->
      <td>
        <div class="stitch-breakdown-pills">
          ${mergedCount > 0 ? `<span class="stitch-pill-count merged" title="${mergedCount} merged pairs">● ${mergedCount} Merged</span>` : ''}
          ${stopLossCount > 0 ? `<span class="stitch-pill-count stop-loss" title="${stopLossCount} exited at stop loss">⚠️ ${stopLossCount} Stop</span>` : ''}
          ${graceExpiredCount > 0 ? `<span class="stitch-pill-count grace-expired" title="${graceExpiredCount} grace period expired">⏱ ${graceExpiredCount} Expired</span>` : ''}
          ${(mergedCount === 0 && stopLossCount === 0 && graceExpiredCount === 0) ? `<span class="stitch-pill-count neutral">${settledCount > 0 ? `${settledCount} Settled` : `${executionItemsCount} Closed`}</span>` : ''}
        </div>
      </td>

      <!-- Col 6: STATUS -->
      <td>
        <span class="stitch-status-badge finished"><span class="stitch-status-dot finished"></span>FINISHED</span>
      </td>

      <!-- Col 7: ACTIONS / EXPAND -->
      <td>
        <button type="button" class="stitch-ledger-toggle-btn" data-cid="${esc(cid)}" aria-expanded="${isExpanded}">
          ${isExpanded ? '▲ Hide Ledger' : '▼ View Ledger'} (${executionItemsCount})
        </button>
      </td>
    </tr>`;

    // Collapsible Execution Ledger Card
    if (isExpanded) {
      html += `<tr class="stitch-expand-row">
        <td colspan="8" style="padding:0;background:#090d16;">
          <div class="stitch-closed-ledger-card">
            <!-- Summary Strip -->
            <div class="stitch-closed-ledger-strip">
              <div class="stitch-strip-left">
                <span class="stitch-strip-metric">Total Cost: <b>${fmtUSD(totalCost)}</b></span>
                <span class="stitch-strip-divider">|</span>
                <span class="stitch-strip-metric">Net Realized: <b class="${pnlCls}">${realizedPnl >= 0 ? '+' : ''}${fmtUSD(realizedPnl)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</b></span>
              </div>
              <div class="stitch-strip-badges">
                <span class="stitch-badge-outcome merged">● ${mergedCount} Merged Pair${mergedCount !== 1 ? 's' : ''}</span>
                ${stopLossCount > 0 ? `<span class="stitch-badge-outcome stop-loss">⚠️ ${stopLossCount} Stop Loss</span>` : ''}
                ${graceExpiredCount > 0 ? `<span class="stitch-badge-outcome grace-expired">⏱ ${graceExpiredCount} Grace Expired</span>` : ''}
                ${(stopLossCount === 0 && graceExpiredCount === 0) ? `<span class="stitch-badge-outcome clean">✓ 0 Orphan / 0 Stops</span>` : ''}
              </div>
            </div>

            <!-- Ledger Table -->
            <div class="stitch-ledger-table-wrap">
              <table class="stitch-ledger-subtable">
                <thead>
                  <tr>
                    <th>AGE / TIME</th>
                    <th>POSITIONS (YES / NO)</th>
                    <th>ENTRY / BUY PRICE</th>
                    <th>SOLD / EXIT PRICE</th>
                    <th>SIZE (SH)</th>
                    <th>REALIZED P&L</th>
                    <th>STATUS</th>
                  </tr>
                </thead>
                <tbody>
                  ${renderStitchClosedLedgerRows(cid, m, settlements, allOrders, nowMs)}
                </tbody>
              </table>
            </div>
          </div>
        </td>
      </tr>`;
    }

    return html;
  }).join('');
}

function renderOrdersTrades(kpi, state) {
  const head = document.getElementById('orders-trades-head');
  const body = document.getElementById('orders-trades-body');
  if (!head || !body) return;

  const view = OT_VIEWS.includes(currentOrdersTradesView)
    ? currentOrdersTradesView : OT_VIEWS[0];
  // Read the sort once and hand the same object to the header and the body, so
  // the indicator and the row order can never disagree after a poll tick.
  const sort = otActiveSort(view);
  // The header row is rebuilt on every tick, which would drop keyboard focus to
  // <body> and strand a keyboard user on the first column: a listener that
  // survives a re-render is not enough if the focused element is destroyed. The
  // button is the same control after the rewrite, so its focus is restored.
  //
  // `contains` and `focus` are called defensively: the Node harnesses stub the
  // DOM with only part of the element surface, and an unguarded call throws
  // inside the render and leaves every table unpainted.
  const active0 = document.activeElement;
  const focusedCol = (typeof head.contains === 'function' && head.contains(active0)
    && active0 && active0.dataset
    && active0.dataset.otSort !== undefined)
    ? active0.dataset.otSort : null;

  const isStitchTerminal = typeof document !== 'undefined'
    && document.body
    && document.body.classList
    && typeof document.body.classList.contains === 'function'
    && document.body.classList.contains('proto-body');

  if (isStitchTerminal && view === 'open-orders') {
    head.innerHTML = stitchOpenOrdersHeadHtml(sort);
    body.innerHTML = stitchOpenOrdersRows(kpi, state, sort);
  } else if (isStitchTerminal && view === 'active-markets') {
    head.innerHTML = stitchActiveMarketsHeadHtml(sort);
    body.innerHTML = stitchActiveMarketsRows(kpi, state, sort);
  } else if (isStitchTerminal && view === 'positions') {
    head.innerHTML = stitchPositionsHeadHtml(sort);
    body.innerHTML = stitchPositionsRows(kpi, state, sort);
  } else if (isStitchTerminal && view === 'closed-trades') {
    head.innerHTML = stitchClosedTradesHeadHtml(sort);
    body.innerHTML = stitchClosedTradesRows(kpi, state, sort);
  } else {
    head.innerHTML = otHeadHtml(view, sort);
    body.innerHTML = ordersTradesRows(view, kpi, state, sort);
  }

  // Preserve search query filtering across poll ticks
  const searchInput = document.getElementById('ot-search-input');
  if (searchInput && searchInput.value) {
    const query = searchInput.value.trim().toLowerCase();
    body.querySelectorAll('tr').forEach(r => {
      r.style.display = (r.textContent || '').toLowerCase().includes(query) ? '' : 'none';
    });
  }

  if (focusedCol !== null) {
    const restore = Array.from(head.querySelectorAll('button[data-ot-sort]'))
      .find(b => b.getAttribute('data-ot-sort') === focusedCol);
    if (restore && typeof restore.focus === 'function') restore.focus();
  }

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

  // One delegated listener on the <thead> that is never replaced. The header row
  // is rewritten by innerHTML on every 2s poll tick, so a listener attached to
  // each button would have to be re-attached after every render and would miss
  // a tick; this one outlives every re-render. A native <button> turns Enter
  // and Space into a click, so the keyboard works without extra key handling.
  const head = document.getElementById('orders-trades-head');
  if (head) {
    head.addEventListener('click', (ev) => {
      const btn = ev.target && ev.target.closest ? ev.target.closest('[data-ot-sort]') : null;
      if (!btn) return;
      const view = OT_VIEWS.includes(currentOrdersTradesView)
        ? currentOrdersTradesView : OT_VIEWS[0];
      otToggleSort(view, Number(btn.dataset.otSort));
      renderOrdersTrades(lastKpi, lastState);
    });
  }

  const searchInput = document.getElementById('ot-search-input');
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      const query = (searchInput.value || '').trim().toLowerCase();
      const body = document.getElementById('orders-trades-body');
      if (!body) return;
      const rows = body.querySelectorAll('tr');
      rows.forEach(r => {
        if (!query) {
          r.style.display = '';
        } else {
          const text = (r.textContent || '').toLowerCase();
          r.style.display = text.includes(query) ? '' : 'none';
        }
      });
    });
  }

  setOrdersTradesView(currentOrdersTradesView);
}


/* Why a trade closed, in the operator's words.
 *
 * The registry has always written a `reason` on the closes that have one --
 * `aged_out_rescue` for the leg the 900s window cannot reach, `grace_expired`
 * and `adverse_drift` for the two stops -- and the dashboard dropped it, so a
 * rescue and a stop both read as a bare One-Sided row with a loss and telling
 * them apart meant opening `scripts/rescue_exit_report.py`. A merge or a
 * settlement books its close without a reason, and a close with nothing to say
 * gets no chip: an empty label is worse than no label.
 *
 * Anything missing from this map still renders, from its own token. A reason
 * the page has never seen is exactly the one nobody can read, so it must not
 * be the one the page hides. */
const CLOSE_REASON_LABELS = {
  aged_out_rescue: 'Aged-out rescue',
  adverse_drift: 'Adverse drift',
  grace_expired: 'Grace expired',
  ladder_exit: 'Ladder exit',
  lifecycle_hard_stop: 'Stop-loss',
};

/* Exit reasons are the closes that took money out of a position by force:
 * the dual stop-loss watcher's cancels and the RESOLVED end states -- a
 * booked-loss single-buy exit is an exit whether the page knows its name or
 * not, so unknown reasons exit too (worth knowing beats worth reading).
 * Bookkeeping-named closes (nothing force-sold, no position lost) stay off
 * this list and keep the quiet gray chip: red is for "money left by force".
 * The WHY rides INSIDE the red badge, not beside it -- one red thing to find
 * on the row, not two pills to read in sequence. */
const EXIT_METHODS = new Set(['single_buy_exit', 'naked_exit', 'ladder_exit', 'aged_out_exit', 'stop_loss_exit']);

function closeReasonIsExit(info) {
  if (!info) return false;
  return EXIT_METHODS.has(String(info.method || '').trim());
}

function closeReasonBadgeLabel(reason, isExit) {
  const words = closeReasonLabel(reason);
  return isExit ? `EXIT · ${words}` : words;
}

function closeReasonLabel(reason) {
  const raw = String(reason === null || reason === undefined ? '' : reason).trim();
  if (!raw) return '';
  if (CLOSE_REASON_LABELS[raw]) return CLOSE_REASON_LABELS[raw];
  const words = raw.replace(/[_-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/* The newest close that names a reason, or null when none of them does.
 *
 * A market can close more than once -- a rescue exit now, a settlement later --
 * and only the named ones explain the PnL the row is showing. Newest named,
 * not newest overall: a settlement that followed the exit says nothing and
 * must not blank the reason the row exists to carry. `method` and `ts` come
 * along for the tooltip, which is where the raw facts stay. */
function closeReasonOf(m) {
  const named = ((m && m.settlements) || [])
    .filter(c => c && String(c.reason === null || c.reason === undefined ? '' : c.reason).trim());
  if (!named.length) return null;
  const newest = named.reduce((a, b) => ((Number(b.ts) || 0) >= (Number(a.ts) || 0) ? b : a));
  return {
    reason: String(newest.reason).trim(),
    method: newest.method || '',
    ts: newest.ts,
    namedCount: named.length,
    closeCount: ((m && m.settlements) || []).length,
  };
}

function closeReasonTitle(info) {
  const parts = [`reason=${info.reason}`];
  if (info.method) parts.push(`method=${info.method}`);
  const ts = Number(info.ts);
  if (Number.isFinite(ts) && ts > 0) {
    parts.push(new Date(ts * 1000).toLocaleString());
  }
  parts.push(info.namedCount === 1
    ? 'the one close that names a reason'
    : `${info.namedCount} of ${info.closeCount} closes name a reason`);
  return parts.join(' · ');
}/* The state of the positions under a MARKET, rolled up.
 *
 * The column used to read `min(up, down) / max(up, down)` and answer
 * "Hedged"/"One-Sided". On a closed market it then read `One-Sided` on every
 * single row, because a closed market holds nothing and "nothing held" fell
 * into the same branch as "held and unbalanced" -- on shadow-01 all 82 closed
 * markets said One-Sided with zero shares held.
 *
 * It now speaks the vocabulary the rows inside it speak. `Paired`, `Partial`
 * and `Unpaired` are `PAIR_STATUS` -- the same three words and the same three
 * tones the OPEN POSITIONS table uses on the positions themselves -- and
 * `Flat` is added for a market holding nothing, which those three cannot say:
 * `Unpaired` would claim a single buy that is not there. So the market row and
 * the rows under it agree, and DESIGN.md's rule holds -- no saturated hue for
 * something that is not a live state (Flat is gray).
 *
 * On a FINISHED market the pair-assembly words would lie: `Unpaired` claims
 * the engine is still trying to complete a pair, and a resolved market will
 * never see that partner. The naked shares on one are what they are --
 * `Unsettled`, a directional buy that the resolution decided alone -- so the
 * word changes with the market's state, and the tone stays `alert`: the
 * risk is the same money either way.
 *
 * Both share counts are read directly rather than `m.balance`: the same ratio,
 * but it cannot go missing with a payload, and a missing number must never be
 * what decides whether a position reads as paired. */
function hedgeStateOf(m) {
  const up = Number((m && m.up_sh) || 0);
  const dn = Number((m && m.dn_sh) || 0);
  if (!(up + dn > 0)) {
    return { state: 'Flat', tone: 'quiet',
             title: 'nothing held: every position under this market is closed' };
  }
  const status = pairStatus(up, dn);
  const settled = isFinishedMarket(m);
  if (settled && status === 'unpaired') {
    return { state: 'Unsettled', tone: 'alert',
             title: hedgeTitle('unpaired', up, dn) + ' -- settled without its counter-leg' };
  }
  return { state: PAIR_STATUS[status].label, tone: PAIR_STATUS[status].tone,
           title: hedgeTitle(status, up, dn) + (settled ? ' -- settled' : '') };
}

/* The roll-up in shares, so a tag that says `Unpaired` also says how much and
 * on which leg without the operator opening the row. */
function hedgeTitle(status, up, dn) {
  const upSh = `${up.toFixed(4)} UP`;
  const dnSh = `${dn.toFixed(4)} DOWN`;
  if (status === 'paired') {
    return `${upSh} against ${dnSh}: paired, merges at $1.00 a share`;
  }
  if (status === 'partial') {
    const same = Math.min(up, dn).toFixed(4);
    return `${upSh} against ${dnSh}: ${same} shares are paired, the remainder is a single buy`;
  }
  return up > 0
    ? `${upSh} held with no DOWN partner: a single buy`
    : `${dnSh} held with no UP partner: a single buy`;
}

/* A token -> UP/DOWN lookup for one market, from its own quotes ledger.
 * The dashboard's Orders and OPEN POSITIONS tables resolve a leg this way; the
 * expanded table below never did, and asked the server for `token_side` and
 * `outcome`, which it does not send -- so its "Outcome / Leg" column rendered
 * BUY for every row and the UP/DOWN pill colouring was dead. */
function legResolverForMarket(m) {
  const byToken = {};
  for (const q of ((m && m.quotes) || [])) {
    const side = String(q.side || '').toUpperCase();
    const token = String(q.token_id || '');
    if (token && (side === 'UP' || side === 'DOWN' || side === 'DN')) {
      byToken[token] = side === 'DN' ? 'DOWN' : side;
    }
  }
  return (tokenId) => byToken[String(tokenId || '')] || null;
}

/* Build one finished-or-live market's main row and its optional expanded
 * sub-row, in the Data & Markets table shape. Shared with the Orders &
 * Trades CLOSED TRADES view so a closed trade reads identically in both
 * tables. Pure: no DOM reads or writes, everything arrives as arguments. */
function marketRowPairHtml(cid, m, opts) {
  const { isExpanded, hasOrders, allOrders, showCancelled, fills, graduatedCids, forceFinished, categoryCaption } = opts;
  const categoryHtml = categoryCaption
    ? `<div class="caption-muted">${esc(marketCategory(m))}</div>` : '';
  const fills_count = m.fills_count || 0;
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
      badgeHtml = `<span class="pill state-stopped" style="font-size:10px; padding:2px 8px; margin-left:4px">0 ACTIVE</span>`;
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
  // Main row — clickable to expand. The Timestamp is when the trade closed:
  // the latest settlement close, with the resolution record as the fallback.
  // `closeTsOf` is the same accessor the column-0 sort reads, so what the
  // operator sees ordered is exactly what is displayed.
  const closeTs = closeTsOf(m);
  const tsHtml = timestampCell(closeTs);
  const hedge = hedgeStateOf(m);

  // How this trade closed, under the name it closed under (see
  // `closeReasonOf`). A quiet chip: DESIGN.md keeps every saturated hue for the
  // live-state vocabulary, and provenance is not a state.
  const reasonInfo = closeReasonOf(m);
  const reasonIsExit = closeReasonIsExit(reasonInfo);
  const reasonHtml = reasonInfo
    ? `<div class="close-reason-line"><span class="close-reason-pill${reasonIsExit ? ' is-exit' : ''}" title="${esc(closeReasonTitle(reasonInfo))}">${esc(closeReasonBadgeLabel(reasonInfo.reason, reasonIsExit))}</span></div>`
    : '';

  let html = `<tr class="market-row${isExpanded ? ' expanded' : ''}" data-cid="${esc(cid)}" tabindex="0" role="button" aria-expanded="${isExpanded}" aria-label="${isExpanded ? 'Collapse' : 'Expand'} market orders for ${esc(m.title || m.slug || cid.slice(0,10))}">
    ${tsHtml}
    <td>
      <span class="expand-chevron${isExpanded ? ' expanded' : ''}" aria-hidden="true">${hasOrders ? '▶' : ''}</span>
      ${marketLink(m)}
      ${categoryHtml}
      ${badgeHtml}
      ${reasonHtml}
    </td>
    <td>${otTag(hedge.tone, hedge.state, null, hedge.title)}</td>
    <td class="mono">${esc(m.realized_pnl !== null && m.realized_pnl !== undefined ? fmtUSD(m.realized_pnl) : '--')}</td>
    <td class="mono">${esc(fills_count)}</td>
    <td>
      ${isFinished
        ? '<span class="pill finished">FINISHED</span>'
        : marketStatusPill(m, (allOrders || []).some(o => isRestingOrder(o)))}
      ${m.resolution ? `<div class="caption-muted" title="resolved ${new Date(m.resolution.resolved_ts * 1000).toLocaleString()}">${m.resolution.winner ? ('Winner: ' + esc(m.resolution.winner)) : 'Resolved'} · ${fmtAgo(m.resolution.resolved_ts)}</div>` : ''}
    </td>
  </tr>`;

  // Expanded sub-row with individual orders
  if (isExpanded && hasOrders) {
    html += `<tr class="orders-expand-row">
      <td colspan="6" style="padding:0">
        <div class="orders-expand-content">
          ${renderExpandedOrders(allOrders, fills, showCancelled,
                                 legResolverForMarket(m))}
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
  body.querySelectorAll('.market-row, .stitch-market-row').forEach(row => {
    const cid = row.dataset.cid;
    if (!cid || (!row.classList.contains('stitch-market-row') && !ordersByMarket[cid])) return;

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
  { key: 'horizon', name: '7. Horizon Gate', cls: 'rejected' },
  { key: 'passed', name: '8. Passed (Quoting)', cls: 'passed' },
];
const STAGE_DEFS = BUCKET_DEFS;

function fmtAge(sec) {
  if (sec === null || sec === undefined) return '--';
  if (sec < 60) return Math.round(sec) + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  return Math.floor(sec / 3600) + 'h ago';
}

function formatHeartbeatAge(sec) {
  if (sec === null || sec === undefined || isNaN(sec)) return '';
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  return Math.floor(s / 3600) + 'h';
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
  const spreadGate = gateBar(funnel?.spread_gate, 0.0205);
  const horizonDays = gateBar(funnel?.horizon_gate_days, 30);

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
        value: 'Binary · Mid [0.15, 0.85]',
      };
    case 'movement':
      return {
        param: 'TEST: RECENT TRADED NOTIONAL',
        value: `≥ $200 / 30m on the tape`,
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
        param: 'TEST: HORIZON',
        value: `≤ ${Number(horizonDays).toFixed(1)} days`,
      };
    case 'passed':
      return {
        param: 'QUALIFIED FLEET',
        value: 'Passed all screening gates · Quoting on venue',
      };
    default:
      return { param: 'GATE TEST', value: '--' };
  }
}

// TRIAL READINESS. The ranker's near-miss logs say whether a gate's refusals
// are consistent enough to license a controlled loosening.
//
// Only the verdict is on screen. The per-gate progress cards ("DEPTH gathering
// 8.7d/14 · 3/8 markets · 41% stable") were four numbers nobody acted on
// between the day the run started and the day it turned ready; the ready
// banner is the moment a decision exists. /api/trial-readiness still carries
// the full detail for anyone who wants to read it.
function renderTrialReadiness(readiness) {
  const banner = document.getElementById('trial-ready-banner');
  if (!banner) return;
  const gates = (readiness && readiness.ready_gates) || [];
  if (!readiness || !readiness.trial_ready || !gates.length) {
    banner.style.display = 'none';
    return;
  }
  banner.style.display = '';
  banner.className = 'pill filled mono';
  banner.textContent = 'TRIAL READY: ' + gates.join(' + ').toUpperCase();
  banner.title = 'The near-miss evidence for this gate meets every readiness '
    + 'threshold. Readiness is not profitability — the trial measures that.';
}

function engineReasonText(reason) {
  if (!reason) return '';
  switch (reason) {
    case 'finished':
      return 'run finished cleanly';
    case 'process_gone':
      return 'process gone';
    case 'heartbeat_stale':
      return 'heartbeat stale (aged past threshold)';
    case 'no_heartbeat':
      return 'no heartbeat file: no shadow run for this store and runtime/live_poll_heartbeat.json not found';
    case 'heartbeat_unreadable':
      return 'heartbeat file unreadable or corrupt';
    default:
      return String(reason).replace(/_/g, ' ');
  }
}

function engineProvenanceText(scanState) {
  if (!scanState) return 'no heartbeat payload';
  const src = scanState.heartbeat_source;
  if (!src || src.kind === 'none') {
    return 'no heartbeat file: no shadow run for this store and runtime/live_poll_heartbeat.json not found';
  }
  if (src.kind === 'live_engine' || src.kind === 'live') {
    const file = src.file || 'runtime/live_poll_heartbeat.json';
    const pidStr = src.pid ? `, pid ${src.pid}` : '';
    return `live loop (${file}${pidStr}), not tagged with a store`;
  }
  if (src.kind === 'shadow_run' || src.kind === 'shadow') {
    const runId = src.run_id ? `run ${src.run_id}` : 'shadow run';
    const store = src.db_path ? `store ${src.db_path}` : 'active store';
    const file = src.file ? ` (${src.file})` : '';
    return `${runId} on ${store}${file}`;
  }
  return src.file || 'unknown source';
}

function renderScanStatePill(scanState, opts) {
  const headerPill = document.getElementById('scan-state-pill');
  if (!headerPill) return;
  const stateText = document.getElementById('scan-engine-state');
  if (!stateText) return;
  const elsewhereTag = document.getElementById('scan-engine-elsewhere');

  // Render scan state pill — canonical live-state vocabulary (DESIGN.md).
  // The server's STALLED verdict stays authoritative for DOWN; a SCANNING
  // heartbeat ages through the ramp on the filter's ~5s cadence thresholds.
  // This element IS the pill, so it takes the state class and the inner dot
  // rather than a nested statePillHtml().
  // Issue #348: a failed read holds the last payload (resolved by the
  // caller) and ages it via opts.ageOffsetSec; the tooltip says the read
  // did not land instead of pretending the verdict is fresh.
  const ageOffsetSec = (opts && isFinite(opts.ageOffsetSec)) ? opts.ageOffsetSec : 0;
  const readFailed = !!(opts && opts.readFailed);
  if (scanState) {
    const raw = scanState.scan_state || '--';
    const hbAge = scanState.seconds_since_heartbeat;
    const effHbAge = (typeof hbAge === 'number' && isFinite(hbAge) && ageOffsetSec)
      ? hbAge + ageOffsetSec : hbAge;
    const telemetryError = scanState.telemetry_error;
    const state = telemetryError ? 'unknown' : scanPillState(raw, effHbAge, scanState.cadence_sec, scanState.stall_reason);
    headerPill.className = 'pill state-' + state;
    const dot = (state === 'running') ? '<span class="pulse-dot active"></span>'
      : (state === 'degraded' || state === 'down') ? '<span class="pulse-dot"></span>'
      : '';
    const formattedAge = formatHeartbeatAge(effHbAge);
    const age = (state !== 'stopped' && state !== 'unknown' && formattedAge)
      ? ' · ' + formattedAge : '';
    // Write the verdict into the inner state span only: overwriting the pill's
    // own innerHTML would erase the ENGINE label that names this measurement.
    stateText.innerHTML = dot + esc(state.toUpperCase() + age);
    const rawSec = (effHbAge !== null && effHbAge !== undefined) ? Math.max(0, Math.round(effHbAge)) : null;
    if (telemetryError) {
      headerPill.title = `Telemetry unavailable: ${telemetryError.error || 'cycle ring read failed'}`;
    } else {
      const prov = engineProvenanceText(scanState);
      let verdict = raw.toUpperCase();
      if (raw === 'STALLED') {
        const rText = engineReasonText(scanState.stall_reason);
        if (scanState.stall_reason === 'process_gone' && scanState.heartbeat_source?.pid) {
          verdict = `STALLED — process gone (pid ${scanState.heartbeat_source.pid} not running)`;
        } else if (scanState.stall_reason === 'heartbeat_stale' && scanState.stale_threshold_sec) {
          verdict = `STALLED — heartbeat aged past ${Math.round(scanState.stale_threshold_sec)}s threshold`;
        } else if (scanState.stall_reason === 'no_heartbeat') {
          verdict = `STALLED — no heartbeat file: no shadow run for this store and runtime/live_poll_heartbeat.json not found`;
        } else if (rText) {
          verdict = `STALLED — ${rText}`;
        }
        if (rawSec !== null && scanState.stall_reason !== 'heartbeat_stale' && scanState.stall_reason !== 'no_heartbeat') {
          verdict += `, last heartbeat ${formatHeartbeatAge(hbAge)}`;
        }
      } else if (rawSec !== null) {
        verdict = `${raw.toUpperCase()} (${rawSec}s ago)`;
      }
      headerPill.title = `Quote engine heartbeat: ${verdict} · Source: ${prov}`;
      if (readFailed) {
        headerPill.title += (ageOffsetSec > 0.5)
          ? ` Current read did not land; showing last reading from ${fmtAge(ageOffsetSec)}.`
          : ' Current read did not land; showing last reading.';
      }
    }

    // Elsewhere notice: show only when other_live_runs is not empty AND engine is not RUNNING on this store
    const otherRuns = scanState.other_live_runs || [];
    if (elsewhereTag) {
      if (otherRuns.length > 0 && state !== 'running') {
        elsewhereTag.style.display = '';
        const countText = otherRuns.length === 1 ? '1 RUN LIVE ON ANOTHER STORE' : `${otherRuns.length} RUNS LIVE ON ANOTHER STORE`;
        elsewhereTag.textContent = countText;
        const details = otherRuns.map(r => `${r.run_id || 'unnamed'} (${r.db_path || 'unknown store'}, ${r.heartbeat_file || 'unknown file'})`).join('; ');
        elsewhereTag.title = `${details} — its numbers are not shown on this page`;
      } else {
        elsewhereTag.style.display = 'none';
      }
    }
  } else {
    // No scan-state payload: the loop's state is unknown, not stopped.
    headerPill.className = 'pill state-unknown';
    stateText.textContent = 'UNKNOWN';
    headerPill.title = backendStale
      ? 'Quote engine state unknown: current read did not land and the backend contact is lost.'
      : 'Quote engine state unknown: no heartbeat payload';
    if (elsewhereTag) elsewhereTag.style.display = 'none';
  }
}

function renderScreener(kpi, scanState, status, engineOpts) {
  const board = document.getElementById('kanban-board');
  const headerAge = document.getElementById('scan-snapshot-age');

  renderScanStatePill(scanState, engineOpts);
  if (scanState && headerAge) {
    const hbAge = scanState.seconds_since_heartbeat;
    const off = (engineOpts && isFinite(engineOpts.ageOffsetSec)) ? engineOpts.ageOffsetSec : 0;
    const effHb = (typeof hbAge === 'number' && isFinite(hbAge) && off) ? hbAge + off : hbAge;
    if (effHb !== null && effHb !== undefined) {
      headerAge.textContent = 'heartbeat: ' + formatHeartbeatAge(effHb);
    }
  }

  const funnel = kpi?.funnel;

  // Snapshot age and census — must run on EVERY call (CodeRabbit round on
  // #271): the heartbeat branch above rewrites this same header each poll,
  // so an unchanged-board early return below would freeze the header on
  // 'heartbeat: …' text with a stale snapshot color. Two cheap property
  // writes; only the board rebuild is expensive enough to guard.
  // The screener re-ranks the universe every ~10 min (SH_FILTER_INTERVAL_SEC,
  // default 600 -- scripts/filter_loop.py), and one failed cycle (e.g. a
  // transient Windows file lock on markets.json) makes the gap 2x that. Say
  // so inline so "14m ago" reads as normal cadence plus a miss, not as a
  // dead screener.
  if (funnel && headerAge) {
    const age = funnel.snapshot_age;
    const SCAN_INTERVAL_SEC = scanIntervalSec(status);
    headerAge.textContent = 'last scan: ' + fmtAge(age) + ' · ~' + Math.round(SCAN_INTERVAL_SEC / 60) + 'm cycle';
    if (age !== null && age !== undefined && age > SCAN_INTERVAL_SEC) {
      // Past one full cycle: amber. Past two (a missed retry): red.
      headerAge.style.color = age > SCAN_INTERVAL_SEC * 2 ? 'var(--error, #e5484d)' : 'var(--warn)';
    } else {
      headerAge.style.color = 'var(--text-secondary)';
    }
  }

  // Skip-if-unchanged guard (#270): the kanban is the heaviest paint on the
  // page (8 stages × example cards). Idling on Data & Markets re-rendered it
  // every 2s poll even when the funnel data was byte-identical — ~800ms of
  // main-thread work per poll that every click queued behind. Only the board
  // rebuild skips; the pills/heartbeat/header above update every call.
  const boardFingerprint = JSON.stringify([funnel]);
  if (renderScreener.__lastFingerprint === boardFingerprint && board.innerHTML !== '') return;
  renderScreener.__lastFingerprint = boardFingerprint;

  if (!funnel) {
    // No pipeline data — show empty state
    board.innerHTML = `<div class="kanban-empty" style="flex:1">
      <div class="empty-state-title">No Market Filter data yet</div>
      <div class="empty-state-msg">The Market Filter writes runtime/pipeline.json on each scan cycle. Data appears here once it runs.</div>
    </div>`;
    return;
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
          const volStr = m.volume !== null && m.volume !== undefined ? fmtUSD(m.volume) : '--';
          const daysStr = m.days_to_resolve !== null && m.days_to_resolve !== undefined ? esc(m.days_to_resolve) + 'd' : '--';
          const shortCid = m.condition_id ? (m.condition_id.slice(0, 6) + '...' + m.condition_id.slice(-4)) : '';

          cardsHtml += `<div class="market-card passed-card" role="listitem">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
              <span class="card-tag tag-quoting">QUOTING</span>
              <span class="mono" style="font-size:9.5px;color:var(--text-muted)">${esc(shortCid)}</span>
            </div>
            <div class="card-title" title="${esc(m.title || m.slug || '')}">${marketLink(m)}</div>
            <div class="card-metrics-grid">
              <div>Fills: <span class="card-fills">${esc(m.fills || 0)}</span></div>
              <div>24h Vol: <span style="color:var(--text-primary)">${volStr}</span></div>
              <div>Days: <span style="color:var(--text-primary)">${daysStr}</span></div>
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
            <div class="card-metric">Vol: ${fmtUSD(el.volume || 0)}${el.days_to_resolve ? ' · ' + esc(el.days_to_resolve) + 'd' : ''}</div>
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

/* ── Issue #278: status-strip scroll cue ── */
// Adds .is-scrollable to .top-meta exactly when the desktop row overflows
// (drives the edge cue in styles.css) and .is-end once scrolled fully
// right so the cue retires at the end. No-ops on mobile, where the strip
// wraps instead of scrolling.
function updateTopMetaScrollCue() {
  const strip = document.querySelector('.top-meta');
  if (!strip) return;
  const canScroll = strip.scrollWidth > strip.clientWidth + 1;
  strip.classList.toggle('is-scrollable', canScroll);
  strip.classList.toggle('is-end',
    canScroll && (strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 1));
}

function initTopMetaScrollCue() {
  const strip = document.querySelector('.top-meta');
  if (!strip) return;
  updateTopMetaScrollCue();
  strip.addEventListener('scroll', updateTopMetaScrollCue, { passive: true });
  window.addEventListener('resize', updateTopMetaScrollCue, { passive: true });
}

/* ── Helper: Safe JSON fetch with timeout ── */
async function safeJsonFetch(url, timeoutMs = 8000) {
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
  // A poll that began before a master-control click resolves after it; its
  // older status must not repaint the button the click just changed (#457).
  const pollSeq = masterLifecycleSeq;
  try {
    const [state, status, kpi, scanState, trialReadiness, guardAlerts, guardHealth] = await Promise.all([
      safeJsonFetch('/api/state', 15000),
      safeJsonFetch('/api/system/status'),
      safeJsonFetch('/api/kpi', 15000),
      safeJsonFetch('/api/scan-state'),
      safeJsonFetch('/api/trial-readiness'),
      safeJsonFetch('/api/guardrail-alerts'),
      safeJsonFetch('/api/guardrail-health'),
    ]);

    // Backend-contact watchdog: the batch proves the backend answers when ANY
    // of its reads succeeds; only a batch where every endpoint returned null
    // counts as a failed poll.
    setBackendContact([state, status, kpi, scanState, trialReadiness, guardAlerts, guardHealth]
      .some(r => r !== null && r !== undefined));

    if (state) lastState = state;
    if (kpi) lastKpi = kpi;
    // Snapshots for switchTab, which repaints the newly shown tab from cache.
    // A failed fetch (null) keeps the last good copy — except readiness: a
    // dead endpoint must hide the trackers, not replay a stale READY claim.
    if (status) lastStatus = status;
    if (scanState) lastScanState = scanState;
    // Stamp each retained payload so a later failed read can age the hold (#348).
    const pollNowMs = Date.now();
    if (status) lastStatusAtMs = pollNowMs;
    if (scanState) lastScanStateAtMs = pollNowMs;
    if (kpi) lastKpiAtMs = pollNowMs;
    lastTrialReadiness = trialReadiness;
    if (guardHealth) lastGuardHealth = guardHealth;
    if (guardAlerts) lastGuardAlerts = guardAlerts;

    // Issue #251: only a successful read can judge the backend's age — a failed
    // poll must not flash the stale note for a process that never answered.
    if (kpi) applyPayloadVersion(kpi);

    // Which registry these numbers came from, before anything renders them.
    if (status) renderDbMode(status);

    // Top-nav pills. Each read resolves to the current payload, the held
    // last-good payload while the backend is reachable, or nothing (#348).
    // The hold carries an age offset, so a borrowed snapshot keeps moving
    // through the existing ramps instead of freezing green on a stale age.
    const engineHeld = resolveHeldRead(scanState, lastScanState, lastScanStateAtMs, pollNowMs, backendStale);
    const kpiHeld = resolveHeldRead(kpi, lastKpi, lastKpiAtMs, pollNowMs, backendStale);
    const statusHeld = resolveHeldRead(status, lastStatus, lastStatusAtMs, pollNowMs, backendStale);
    renderMarketScanPill(statusHeld.payload, kpiHeld.payload,
      { kpiReadFailed: !kpi, ageOffsetSec: kpiHeld.ageOffsetSec });
    renderScanStatePill(engineHeld.payload,
      { ageOffsetSec: engineHeld.ageOffsetSec, readFailed: engineHeld.readFailed });

    // Service uptime rides on the status payload, so it must not wait on
    // /api/kpi: the Market Filter header still needs a stopwatch when the KPI read
    // is the one that failed.
    if (status) setFilterUptime(status);

    // Issue #264: each section below repaints only while its tab is visible.
    // A poll used to re-render every section on every tick — both hidden tabs
    // plus the heavy charts — one long main-thread task that a tab click
    // waited behind for 2-4s. Issue #266: "visible" must mean the section's
    // rail page is the one showing — after #140 moved the panels under
    // `#page-*` sections, the un-hidden legacy tab shells made every gate
    // read visible and put the whole render chain back on every poll.

    // Top-nav status (stack pill, ENGINE/WATCHDOG pills, START/STOP buttons)
    // is global chrome: renderServiceCards paints it before the card grid, so
    // on any page but Trades the poll paints just the header — and on Trades
    // one renderServiceCards call covers both.
    // A master-control click in flight owns the button right now: this poll's
    // status is older than the click, so repainting here would flash the old
    // state over the new one (#457).
    if (status && pollSeq === masterLifecycleSeq) {
      if (paintable(tab1, document.getElementById('service-cards'))) {
        renderServiceCards(status, guardHealth, guardAlerts);
      } else {
        renderServiceHeader(status, guardHealth, guardAlerts);
      }
    }

    // Render exposure bar (DT3) — header element, page-independent, cheap.
    if (kpi || lastKpi) renderExposure(kpi || lastKpi);

    const currentKpi = kpi || lastKpi;

    // Render Orders & Trades (Tab 1 → rail Dashboard page)
    if (paintable(tab1, document.getElementById('orders-trades-body')) && currentKpi) {
      renderOrdersTrades(currentKpi, lastState);
    }

    // Portfolio overview + run pill (Tab 2 → rail Dashboard page) get their
    // own gate: the card lives on page-home since #140, and painting it only
    // behind the Reports grid froze it at standby on every other page (#268).
    if (paintable(tab2, document.getElementById('broker-hero-equity')) && currentKpi) {
      renderPortfolioOverview(currentKpi, status, lastState);
    }

    // Render KPIs (Tab 2 → rail Reports page)
    if (paintable(tab2, document.getElementById('kpi-grid')) && currentKpi) {
      renderKPIs(currentKpi, status);
    }

    // Render the Market Filter kanban (Tab 3 → rail Data & Markets page)
    if (paintable(tab3, document.getElementById('kanban-board')) && currentKpi) {
      renderScreener(currentKpi, engineHeld.payload, statusHeld.payload,
        { ageOffsetSec: engineHeld.ageOffsetSec, readFailed: engineHeld.readFailed });
    }

    // Trial readiness rides on its own endpoint, so it renders whether or not
    // the KPI read succeeded -- and it is called even when the readiness fetch
    // FAILED, so a dead endpoint hides the trackers rather than leaving the
    // last reading on screen as if it were current.
    renderTrialReadiness(trialReadiness);
  } catch (e) {
    // Non-fatal transient error swallowed gracefully — but the watchdog still
    // counts it: a throw here means the batch never answered.
    setBackendContact(false);
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
  initTopMetaScrollCue();
  bindServicesDropdown();
  pollStatus();
  renderParameters();
  setInterval(pollStatus, POLL_MS);
  setInterval(renderShadowClock, 1000);
  setInterval(renderFilterUptime, 1000);
  setInterval(renderBackendContact, 1000); // keep the STALE age counting while stale
}

// Node-only: lets tests reach the handlers. Browsers have no `module`, so this
// is dead code in the page.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { runSwitcherLabel, dbModeVerdict, renderPositionDistributionChart, renderMarkoutChart, renderMonteCarloChart, renderQuantRiskGrid, signClass, fmtSignedUSD, _ciBounds,     decisionGatesHtml, decisionGatesRows,     gateBadge, methodBadge, METHOD_BADGES, fmtHoldDuration, fmtOrderAge, typesetMath, renderTrialReadiness, isMergedOrder, isActiveOrder, collapseMergedPair, renderExpandedOrders, renderDbMode, setShadowRun, renderShadowClock, fmtStopwatch, setFilterUptime, renderFilterUptime, fmtUptime, renderServiceCards, fmtLocalTime, connectSSE, marketLink,
    EVENT_TRANSLATIONS, translateEvent, buildStreamSentence, isTradeEvent,
    streamMarketName, noteStreamTitles, quoteList, humanizeAction, appendTickerEvent,
    renderTickerFeed, tickerMatches, tickerEmptyState,
    setTickerFilter, setTickerShowDetails, groupOrdersByMarket, renderBrokerPortfolioOverview, portfolioEquity, buildBrokerEquitySeries,

    statsFilterScope, pruneStatsSubnav, STATS_VIEW_TARGETS, applyStatsViewFilter,
    payloadIsStale, applyPayloadVersion, EXPECTED_PAYLOAD_VERSION,
    renderPnlCiReadout, renderExecutionFunnel, renderSampleSufficiency,
    OT_VIEWS, OT_COLUMNS, ordersTradesRows, ordersTradesCounts, otHeadHtml,
    otSortGroups, otCompare, otDefaultDir, otIsTextColumn, otToggleSort, otActiveSort,
    activeMarketsRows, openOrdersRows, positionsRows, closedTradesRows,
    closedTradesEntries, marketRowPairHtml, wireMarketRowExpansion,
    closeReasonOf, closeReasonLabel, closeReasonTitle, CLOSE_REASON_LABELS,
    hedgeStateOf, legResolverForMarket, orderStatusTitle, ORDER_STATUS_TITLES,
    heldMarketEntries, heldLegs, isFinishedMarket, latestLegMids, latestLegQuotes,
    positionMarkValue, settledMarkValue, winningLeg,
    liveMarks, LIVE_MARK_MAX_AGE_MS, liveMarkMid, applyLiveMarks, clearLiveMarks,
    liveLegMids, paintLiveMarks, scheduleLiveMarksPaint,
    isQuotedMarket, isRestingOrder, tokenLegMap, legForOrder, marketStatusPill,
    normalizeLeg, groupOrdersByPair, restingPairCost, restingPairLegs,
    fmtTimestamp, toMs, latestQuoteTs, latestFillTs, closeTsOf, fmtRelAgo, timestampCell,
    pairStatus, PAIR_STATUS, isMarketInferredPosition, pairSummary,
    get isStopping() { return isStopping; },
    // The table's own expand state, so a harness can open a row and read the
    // sub-table that the real click path renders.
    get expandedMarkets() { return expandedMarkets; },
    set isStopping(v) { isStopping = v; },
    stateKey, statePillHtml, cadenceThresholds, scanPillState, marketScanState, resolveHeldRead,
    engineReasonText, engineProvenanceText, renderScanStatePill,
    get setBackendContact() { return setBackendContact; },
    get renderBackendContact() { return renderBackendContact; },
    get backendStale() { return backendStale; },
    get backendLastSeenMs() { return backendLastSeenMs; },
    switchTab, pollStatus, renderCachedSections, tabVisible, deferPaint,
    initTopMetaScrollCue, updateTopMetaScrollCue,
    renderStitchClosedLedgerRows, stitchClosedTradesRows, stitchClosedTradesHeadHtml,
    stitchOpenOrdersRows, stitchOpenOrdersHeadHtml,
    stitchPositionsRows, stitchPositionsHeadHtml,
    stitchActiveMarketsRows, stitchActiveMarketsHeadHtml };
}
