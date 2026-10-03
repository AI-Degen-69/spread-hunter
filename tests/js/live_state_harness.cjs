/* Harness: drive the live-state language without a browser.
 *
 * Loaded by tests/test_live_state_language.py. Prints one JSON object with:
 *   - stateKey / cadenceThresholds / statePillHtml verdicts
 *   - the backend-contact watchdog verdict after a scripted failure sequence
 *
 * app.js expects a DOM-shaped global scope; the stubs here are the same shape
 * the other harnesses use.
 */
'use strict';

const path = require('node:path');
const fs = require('fs');
const appJsPath = path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js');
const script = process.argv[2] || 'all';

function fakeClassList() {
  const set = new Set();
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    contains: (c) => set.has(c),
    toggle: (c, force) => {
      if (force === undefined) { set.has(c) ? set.delete(c) : set.add(c); }
      else if (force) { set.add(c); } else { set.delete(c); }
      return set.has(c);
    },
  };
}

class FakeEl {
  constructor(tag) {
    this.tagName = tag;
    this.id = '';
    this._html = '';
    this._text = '';
    this.className = '';
    this.title = '';
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.classList = fakeClassList();
    this.attrs = {};
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  // Like a real DOM node, assigning textContent replaces the children, so a
  // later innerHTML read serializes the text (Issue #348 never-read case).
  set textContent(v) { this._text = String(v); this._html = String(v); }
  get textContent() { return this._text; }
  addEventListener() {}
  querySelector(sel) {
    if (sel === '.stale-age') {
      if (!this._ageEl) { this._ageEl = new FakeEl('span'); this._ageEl.className = 'stale-age'; }
      return this._ageEl;
    }
    return null;
  }
  querySelectorAll() { return []; }
  appendChild(c) { this.children.push(c); }
  insertBefore(c) { this.children.unshift(c); }
  removeChild(c) {
    const idx = this.children.indexOf(c);
    if (idx !== -1) this.children.splice(idx, 1);
  }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] !== undefined ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
}

const banner = new FakeEl('div');
banner.id = 'backend-contact-banner';
const elements = new Map([['backend-contact-banner', banner]]);

global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeEl('div'));
    return elements.get(id);
  },
  querySelectorAll() { return []; },
  querySelector() { return null; },
  createElement(tag) { return new FakeEl(tag); },
  addEventListener() {},
  body: new FakeEl('body'),
};
global.window = { addEventListener() {}, matchMedia: () => ({ matches: false }) };
const store = new Map();
global.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
global.CONTROL_TOKEN = 'harness-token';
global.fetch = () => Promise.reject(new Error('offline harness'));
// connectSSE() runs at load; a stub that never opens keeps the harness offline.
global.EventSource = class {
  constructor() { this.readyState = 0; }
  close() {}
  addEventListener() {}
};

// app.js is a browser script and package.json declares "type": "module", so
// `require` cannot load it. Read it and evaluate it as CommonJS with the stub
// globals this harness provides -- the same wrapping the other .cjs
// harnesses use.
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage',
             'EventSource', 'fetch', 'CONTROL_TOKEN',
             fs.readFileSync(appJsPath, 'utf8'))(
  mod, mod.exports, global.document, global.window,
  global.localStorage, global.EventSource, global.fetch, global.CONTROL_TOKEN);
const app = mod.exports;

function pillVerdicts() {
  return {
    running_fresh: app.statePillHtml('running', 3),
    degraded: app.statePillHtml('degraded', 22),
    down: app.statePillHtml('down', 74),
    stopped_no_age: app.statePillHtml('stopped'),
    stopped_with_age_drops_age: app.statePillHtml('stopped', 30),
  };
}

function keyVerdicts() {
  const t = app.cadenceThresholds(5);
  return {
    cadence_5s: t,
    cadence_0_5s: app.cadenceThresholds(0.5),
    cadence_garbage: app.cadenceThresholds(undefined),
    running_fresh: app.stateKey(true, 3),
    running_degraded: app.stateKey(true, 20),
    running_down: app.stateKey(true, 90),
    running_no_age: app.stateKey(true, null),
    not_running_no_age: app.stateKey(false, null),
    not_running_with_age: app.stateKey(false, 30),
    custom_thresholds: app.stateKey(true, 4, { degraded: 3, down: 9 }),
  };
}

// Backend-contact watchdog sequence, with a controllable clock.
function backendSequence() {
  let now = 1_000_000;
  const clock = () => now;
  const get = () => ({
    stale: app.backendStale,
    lastSeenMs: app.backendLastSeenMs,
  });

  // Cold start: the backend was never reachable.
  app.setBackendContact(false, clock());
  const coldOneFail = app.backendStale;
  app.setBackendContact(false, clock());
  const coldTwoFails = app.backendStale;
  const coldBanner = banner.classList.contains('show')
    ? banner.querySelector('.stale-age').textContent : null;

  // Warm sequence: contacted first, then lost.
  app.setBackendContact(true, clock());      // healthy poll
  const afterOk = get();
  now += 2000;
  app.setBackendContact(false, clock());     // first failure: tolerated
  const afterOneFail = get();
  now += 2000;
  app.setBackendContact(false, clock());     // second failure: stale
  const afterTwoFails = get();
  const bannerShownThen = banner.classList.contains('show');
  now += 4000;
  app.renderBackendContact(clock());         // banner keeps counting while stale
  const ageText = banner.querySelector('.stale-age').textContent;
  now += 2000;
  app.setBackendContact(true, clock());      // recovery clears the banner
  const afterRecovery = get();
  const bannerShownAfter = banner.classList.contains('show');

  return {
    coldStart: { afterOneFail: coldOneFail, afterTwoFails: coldTwoFails },
    coldStartBanner: coldBanner,
    afterOk, afterOneFail, afterTwoFails, afterRecovery,
    bannerShownThen, bannerShownAfter,
    staleAgeText: ageText,
  };
}

function guardrailHudVerdict() {
  const render = (health, alerts) => {
    app.renderServiceCards(
      { bot_state: 'STOPPED', services: {} },
      health,
      alerts,
    );
    return {
      state: document.getElementById('hud-guardrail-state').textContent,
      className: document.getElementById('hud-guardrail-pill').className,
    };
  };
  return {
    stale: render({ running: false, age_s: 90, alerts_total: 0 }, null),
    alerting: render(
      { running: true, age_s: 3, alerts_total: 1 },
      { alerts: [{ kind: 'TEST' }] },
    ),
    stopped: render({ running: false, age_s: null, alerts_total: 0 }, null),
  };
}

function scanPillVerdicts() {
  return {
    scanningFresh: app.scanPillState('SCANNING', 3),
    idleFresh: app.scanPillState('IDLE', 3),
    idleAging: app.scanPillState('IDLE', 75),
    idleLongGone: app.scanPillState('IDLE', 300),
    stalled: app.scanPillState('STALLED', 3),
    stalledFinished: app.scanPillState('STALLED', 3, null, 'finished'),
    stalledProcessGone: app.scanPillState('STALLED', 3, null, 'process_gone'),
    unrecognised: app.scanPillState('WAT', 3),
    missing: app.scanPillState(undefined, 3),
  };
}

function enginePillVerdicts() {
  const pillEl = new FakeEl('span');
  pillEl.id = 'scan-state-pill';
  const stateEl = new FakeEl('span');
  stateEl.id = 'scan-engine-state';
  const elsewhereEl = new FakeEl('span');
  elsewhereEl.id = 'scan-engine-elsewhere';
  elements.set('scan-state-pill', pillEl);
  elements.set('scan-engine-state', stateEl);
  elements.set('scan-engine-elsewhere', elsewhereEl);

  const render = (scanState) => {
    app.renderScanStatePill(scanState);
    return {
      pillClass: pillEl.className,
      pillTitle: pillEl.title,
      stateHtml: stateEl.innerHTML,
      elsewhereDisplay: elsewhereEl.style.display,
      elsewhereText: elsewhereEl.textContent,
      elsewhereTitle: elsewhereEl.title,
    };
  };

  const shadowFresh = {
    scan_state: 'SCANNING',
    seconds_since_heartbeat: 2.5,
    heartbeat_source: {
      kind: 'shadow',
      run_id: 'trial-01',
      db_path: 'data/trial_01.db',
      file: 'runtime/shadow_run_trial-01.json',
      pid: 1234,
    },
    stall_reason: null,
    other_live_runs: [],
  };

  const shadowFinished = {
    scan_state: 'STALLED',
    seconds_since_heartbeat: 120,
    heartbeat_source: {
      kind: 'shadow',
      run_id: 'trial-01',
      db_path: 'data/trial_01.db',
      file: 'runtime/shadow_run_trial-01.json',
      pid: 1234,
    },
    stall_reason: 'finished',
    other_live_runs: [],
  };

  const shadowDeadOtherLive = {
    scan_state: 'STALLED',
    seconds_since_heartbeat: 4800,
    stall_reason: 'process_gone',
    heartbeat_source: {
      kind: 'shadow',
      run_id: 'trial-01',
      db_path: 'data/trial_01.db',
      file: 'runtime/shadow_run_trial-01.json',
      pid: 1234,
    },
    other_live_runs: [
      {
        run_id: 'ladder-live',
        db_path: 'data/NN_shadow.db',
        heartbeat_file: 'runtime/shadow_run_ladder-live.json',
      },
    ],
  };

  return {
    reasons: {
      finished: app.engineReasonText('finished'),
      process_gone: app.engineReasonText('process_gone'),
      heartbeat_stale: app.engineReasonText('heartbeat_stale'),
      no_heartbeat: app.engineReasonText('no_heartbeat'),
      heartbeat_unreadable: app.engineReasonText('heartbeat_unreadable'),
    },
    provenance: {
      live: app.engineProvenanceText({
        heartbeat_source: {
          kind: 'live',
          file: 'runtime/live_poll_heartbeat.json',
          pid: 9999,
        },
      }),
      shadow: app.engineProvenanceText(shadowFresh),
      none: app.engineProvenanceText({}),
    },
    rendered: {
      shadowFresh: render(shadowFresh),
      shadowFinished: render(shadowFinished),
      shadowDeadOtherLive: render(shadowDeadOtherLive),
    },
  };
}

/* The top-nav MARKET SCAN pill: is the scanner PROCESS alive, and is the
 * dashboard reading a file it actually refreshed? */
function marketScanVerdicts() {
  const up = { services: { filter: { running: true } } };
  const down = { services: { filter: { running: false } } };
  const fresh = { funnel: { snapshot_age: 120 } };
  const stale = { funnel: { snapshot_age: 4000 } };
  const call = (st, kpi, opts) => {
    const v = app.marketScanState(st, kpi, opts);
    return { state: v.state, label: v.label, title: v.title };
  };
  return {
    upFresh: call(up, fresh),
    upStale: call(up, stale),
    upNoFile: call(up, {}),
    processDead: call(down, fresh),
    noStatus: call(null, fresh),
    registryUnreadable: call({ registry_unreadable: true, services: { filter: { running: true } } }, fresh),
    // Issue #348: a failed KPI read is not a missing snapshot file.
    kpiFailedNoData: call(up, null, { kpiReadFailed: true }),
    heldFresh: call(up, fresh, { kpiReadFailed: true, ageOffsetSec: 60 }),
    heldStale: call(up, fresh, { kpiReadFailed: true, ageOffsetSec: 2000 }),
  };
}

/* Pure held-read resolver outcomes, with a controllable clock. */
function heldResolveVerdicts() {
  const now = 1_000_000;
  app.setBackendContact(true, now);
  const cur = { a: 1 };
  const last = { a: 2 };
  const ser = (r) => ({ payload: r.payload, ageOffsetSec: r.ageOffsetSec, readFailed: r.readFailed });
  const currentWins = ser(app.resolveHeldRead(cur, last, now - 2000, now, app.backendStale));
  const held = ser(app.resolveHeldRead(null, last, now - 8000, now, app.backendStale));
  const neverRead = ser(app.resolveHeldRead(null, null, null, now, app.backendStale));
  // Two consecutive failures trip the watchdog: the hold must drop.
  app.setBackendContact(false, now);
  app.setBackendContact(false, now);
  const staleDropsHold = ser(app.resolveHeldRead(null, last, now - 8000, now, app.backendStale));
  return { currentWins, held, neverRead, staleDropsHold };
}

/* Issue #348: a failed scan-state read holds the last verdict and ages it. */
function engineHoldVerdicts() {
  const pillEl = new FakeEl('span');
  pillEl.id = 'scan-state-pill';
  const stateEl = new FakeEl('span');
  stateEl.id = 'scan-engine-state';
  const elsewhereEl = new FakeEl('span');
  elsewhereEl.id = 'scan-engine-elsewhere';
  elements.set('scan-state-pill', pillEl);
  elements.set('scan-engine-state', stateEl);
  elements.set('scan-engine-elsewhere', elsewhereEl);

  const render = (scanState, opts) => {
    app.renderScanStatePill(scanState, opts);
    return {
      pillClass: pillEl.className,
      stateHtml: stateEl.innerHTML,
      pillTitle: pillEl.title,
    };
  };

  const fresh = {
    scan_state: 'SCANNING',
    seconds_since_heartbeat: 2.5,
    heartbeat_source: {
      kind: 'shadow',
      run_id: 'trial-01',
      db_path: 'data/trial_01.db',
      file: 'runtime/shadow_run_trial-01.json',
      pid: 1234,
    },
    stall_reason: null,
    other_live_runs: [],
  };

  return {
    fresh: render(fresh),
    held: render(fresh, { ageOffsetSec: 8, readFailed: true }),
    heldPastThreshold: render(fresh, { ageOffsetSec: 200, readFailed: true }),
    neverRead: render(null),
  };
}

/* The TRIAL READY banner: the verdict, and nothing while there is none. */
function trialBannerVerdicts() {
  const el = document.getElementById('trial-ready-banner');
  const show = (r) => { app.renderTrialReadiness(r); return el.style.display; };
  return {
    failedFetch: show(null),
    notReady: show({ trial_ready: false, ready_gates: [], depth: {}, volume: {} }),
    readyNoGates: show({ trial_ready: true, ready_gates: [] }),
    ready: show({ trial_ready: true, ready_gates: ['depth'] }),
    readyText: el.textContent,
  };
}

/* The DB badge's store verdict: is the pointed store being written at all,
 * and if not, is something else live that the operator could switch to? */
function dbModeVerdicts() {
  const call = (st) => {
    const v = app.dbModeVerdict(st);
    return {
      stale: v.stale,
      activeRunId: v.activeRunId,
      hereRunning: v.hereRunning,
      liveElsewhere: v.liveElsewhere.map(r => r.runId),
    };
  };
  const dead = { run_id: 'shadow-01', db_path: '/d/01_shadow.db', running: false, is_active_db: true, heartbeat_age_sec: 4800 };
  const live = { run_id: 'ladder-live', db_path: '/d/NN_shadow.db', running: true, is_active_db: false, heartbeat_age_sec: 2 };
  return {
    // The reported case: pointed at a store whose run died, live run elsewhere.
    deadHereLiveElsewhere: call({ db_is_production: false, shadow_runs: [dead, live] }),
    // This store's run is alive: nothing to warn about.
    liveHere: call({ db_is_production: false, shadow_runs: [{ ...dead, running: true, run_id: 'shadow-01' }] }),
    // Dead here and nothing running anywhere: a genuinely dead engine.
    deadEverywhere: call({ db_is_production: false, shadow_runs: [dead] }),
    // No rehearsal registered (the ordinary live-stack page).
    noRuns: call({ db_is_production: false, shadow_runs: [] }),
    // An older backend that sends no field at all must not read as stale.
    noField: call({ db_is_production: false }),
    // The production registry is never "stale store".
    production: call({ db_is_production: true, shadow_runs: [dead, live] }),
  };
}

let out;
if (script === 'dbmode') out = dbModeVerdicts();
else if (script === 'trialbanner') out = trialBannerVerdicts();
else if (script === 'marketscan') out = marketScanVerdicts();
else if (script === 'heldresolve') out = heldResolveVerdicts();
else if (script === 'enginehold') out = engineHoldVerdicts();
else if (script === 'scanpill') out = scanPillVerdicts();
else if (script === 'enginepill') out = enginePillVerdicts();
else if (script === 'pills') out = pillVerdicts();
else if (script === 'keys') out = keyVerdicts();
else if (script === 'backend') out = backendSequence();
else out = {
  pills: pillVerdicts(),
  keys: keyVerdicts(),
  backend: backendSequence(),
  scanpill: scanPillVerdicts(),
  enginepill: enginePillVerdicts(),
  guardrailHud: guardrailHudVerdict(),
  marketscan: marketScanVerdicts(),
  heldresolve: heldResolveVerdicts(),
  enginehold: engineHoldVerdicts(),
  trialbanner: trialBannerVerdicts(),
  dbmode: dbModeVerdicts(),
};

process.stdout.write(JSON.stringify(out));
