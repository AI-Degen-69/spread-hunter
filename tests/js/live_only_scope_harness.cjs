/* Harness: render the service cards and report which ones carry the scope tag.
 *
 * Loaded by tests/test_live_only_scope_tag.py. `app.js` is evaluated with stub
 * globals so the page bootstrap stays asleep and only `renderServiceCards`
 * runs.
 *
 * Prints one JSON line: the container markup plus a per-service flag for the
 * LIVE ONLY pill.
 */
'use strict';

const fs = require('fs');

const appJsPath = process.argv[2];

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
}

const elements = new Map();
global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeEl(id));
    return elements.get(id);
  },
  querySelectorAll() { return []; },
  createElement() { return new FakeEl('created'); },
  addEventListener() {},
  body: new FakeEl('body'),
};
global.window = { addEventListener() {}, matchMedia: () => ({ matches: false }) };
global.CONTROL_TOKEN = 'harness-token';
global.EventSource = function () { return { addEventListener() {}, close() {} }; };
global.setInterval = () => 0;
global.alert = () => {};
global.prompt = () => null;
global.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '' });
global.localStorage = {
  _v: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
  setItem(k, v) { this._v[k] = String(v); },
};

const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource',
             fs.readFileSync(appJsPath, 'utf8'))(
  mod, mod.exports, global.document, global.window,
  global.localStorage, global.EventSource);
const app = mod.exports;

// A shadow rehearsal: the screener runs, the two live-stack services do not.
const SHADOW_STACK = {
  services: {
    filter: { running: true, pid: 26760 },
    query: { running: false },
    decide: { running: false },
  },
};

app.renderServiceCards(SHADOW_STACK, { running: true, pid: 19940, age_s: 5 }, null);
const html = document.getElementById('service-cards').innerHTML;

/* Each card opens with `aria-label="<service name>"`; slice on that boundary so
 * a pill is attributed to the card it actually sits in. */
function cardFor(name) {
  const start = html.indexOf(`aria-label="${name}"`);
  if (start === -1) return '';
  const next = html.indexOf('<div class="card', start + 1);
  return next === -1 ? html.slice(start) : html.slice(start, next);
}

const NAMES = {
  guardrail: 'Guardrail Risk Watchdog',
  filter: 'Market Filter',
  query: 'Venue Engine & Order Poller',
  decide: 'Execution Loop & Maker Quoter',
};

const scoped = {};
for (const [key, name] of Object.entries(NAMES)) {
  const card = cardFor(name);
  scoped[key] = {
    found: card !== '',
    hasScopePill: card.indexOf('svc-scope-pill') !== -1,
    hasLiveOnlyText: card.indexOf('>LIVE ONLY<') !== -1,
    mentionsShadowRun: card.indexOf('core_brain.shadow_run') !== -1,
    keepsOwnTag: card.indexOf('param-code-pill') !== -1,
  };
}

process.stdout.write(JSON.stringify({ scoped }));
