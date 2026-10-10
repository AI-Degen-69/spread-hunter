/* Harness: drive the dashboard's service toggle without a browser.
 *
 * Loaded by tests/test_dashboard_server.py. `app.js` is required BEFORE any
 * `document` exists, so its page bootstrap stays asleep and only the handler
 * under test runs.
 *
 * Prints one JSON line: what the click did in a SHADOW view and in a LIVE one.
 */
'use strict';

const appJsPath = process.argv[2];

const log = { fetches: [], prompts: 0, alerts: 0, alertMsg: '' };
let promptReturn = null;

function fakeClassList() {
  const set = new Set();
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    contains: (c) => set.has(c),
  };
}

class FakeEl {
  constructor(id) {
    this.id = id;
    this._html = '';
    this.className = '';
    this.textContent = '';
    this.title = '';
    this.style = {};
    this.dataset = {};
    this.classList = fakeClassList();
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  addEventListener() {}
  querySelectorAll() { return []; }
  appendChild() {}
  setAttribute() {}
  getAttribute() { return null; }
  removeAttribute() {}
}

// One stopped, togglable service. `classList` has no `on`, so a click takes the
// start branch -- the live-order path this test is about.
const toggle = {
  dataset: { svc: 'decide' },
  classList: fakeClassList(),
  _click: null,
  addEventListener(ev, fn) { if (ev === 'click') this._click = fn; },
};

const elements = new Map();
global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeEl(id));
    return elements.get(id);
  },
  querySelectorAll(sel) {
    return String(sel).indexOf('.toggle[data-svc]') !== -1 ? [toggle] : [];
  },
  createElement() { return new FakeEl('created'); },
  addEventListener() {},
  body: new FakeEl('body'),
};
global.window = { addEventListener() {}, matchMedia: () => ({ matches: false }) };
global.CONTROL_TOKEN = 'harness-token';
global.EventSource = function () { return { addEventListener() {}, close() {} }; };
global.setInterval = () => 0;
global.alert = (m) => { log.alerts += 1; log.alertMsg = String(m); };
global.prompt = () => { log.prompts += 1; return promptReturn; };
global.fetch = async (p, opts) => {
  log.fetches.push({ path: String(p), method: (opts && opts.method) || 'GET' });
  return { ok: true, status: 200, json: async () => ({}), text: async () => '' };
};

global.localStorage = {
  _v: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
  setItem(k, v) { this._v[k] = String(v); },
};

// The page wires tabs, modals and the event stream at load. Required after the
// stubs exist so that wiring finds a DOM; the page bootstrap itself stays
// asleep, because `app.js` skips it when loaded as a module.
// app.js is a browser script and package.json declares "type": "module", so
// `require` cannot load it. Read it and evaluate it as CommonJS with the stub
// globals this harness provides -- the same wrapping the .cjs harnesses added
// later use.
const fs = require('fs');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource',
             fs.readFileSync(appJsPath, 'utf8'))(
  mod, mod.exports, global.document, global.window,
  global.localStorage, global.EventSource);
const app = mod.exports;


const STOPPED_STACK = {
  services: {
    filter: { running: false }, query: { running: false },
    decide: { running: false }, dash: { running: true },
  },
};

function snapshot() {
  return {
    prompts: log.prompts,
    alerts: log.alerts,
    alertMsg: log.alertMsg,
    starts: log.fetches.filter(
      (f) => f.method === 'POST' && f.path.indexOf('/api/system/service/start') !== -1).length,
    stops: log.fetches.filter(
      (f) => f.method === 'POST' && f.path.indexOf('/api/system/service/stop') !== -1).length,
    wholeStackStarts: log.fetches.filter(
      (f) => f.method === 'POST' && /(^|\/)api\/system\/start(\?|$)/.test(f.path)).length,
  };
}

function reset() {
  log.fetches.length = 0;
  log.prompts = 0;
  log.alerts = 0;
  log.alertMsg = '';
}

(async () => {
  // 1. SHADOW view: the click must not prompt and must not POST.
  app.renderDbMode({
    db_mode: 'SHADOW', db_path: 'data' + String.fromCharCode(92) + 'shadow.db',
    db_is_production: false,
  });
  app.renderServiceCards(STOPPED_STACK, null, null);
  await toggle._click();
  const shadow = snapshot();
  const badge = document.getElementById('db-mode-badge');
  const badgeText = badge.textContent;
  const badgeClass = badge.className;

  // 2. LIVE view, operator types START: the same click goes through, and
  // only to its own service endpoint -- never the whole-stack start.
  reset();
  promptReturn = 'START';
  app.renderDbMode({
    db_mode: 'LIVE', db_path: 'data/orders.db', db_is_production: true,
  });
  app.renderServiceCards(STOPPED_STACK, null, null);
  await toggle._click();
  const live = snapshot();

  // 3. LIVE view, Market Filter card: starts with no typed prompt.
  reset();
  toggle.dataset.svc = 'filter';
  app.renderServiceCards(STOPPED_STACK, null, null);
  await toggle._click();
  const liveFilter = snapshot();

  process.stdout.write(JSON.stringify({ shadow, live, liveFilter, badgeText, badgeClass }));
})();
