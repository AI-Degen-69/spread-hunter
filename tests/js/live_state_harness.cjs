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
    this.className = '';
    this.textContent = '';
    this.title = '';
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.classList = fakeClassList();
    this.attrs = {};
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
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

let out;
if (script === 'pills') out = pillVerdicts();
else if (script === 'keys') out = keyVerdicts();
else if (script === 'backend') out = backendSequence();
else out = { pills: pillVerdicts(), keys: keyVerdicts(), backend: backendSequence() };

process.stdout.write(JSON.stringify(out));
