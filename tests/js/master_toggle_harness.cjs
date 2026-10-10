/* Harness: drive the dashboard's single master toggle (Issue #457).
 *
 * Loaded by tests/test_dashboard_server.py. `app.js` is evaluated with stub
 * globals before any real DOM exists, so its page bootstrap stays asleep and
 * only the handler under test runs.
 *
 * Prints one JSON line: what the click did across every toggle state.
 */
'use strict';

const appJsPath = process.argv[2];

const log = { fetches: [], ticker: [] };
let fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({}) });

// appendTickerEvent ends in renderTickerFeed, which paints the real ticker
// rows. In the stub that is noise; what matters is the LINE the backend's
// message produced, so capture it at the source.
global.__tickerLines = [];

function fakeClassList() {
  const set = new Set();
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    contains: (c) => set.has(c),
    toString: () => [...set].join(' '),
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
    this.disabled = false;
    this.attrs = {};
    this.classList = fakeClassList();
    this._click = null;
    this._emptyState = null;
  }
  set innerHTML(v) {
    this._html = String(v);
    if (this.id === 'event-ticker' && v) global.__tickerWrites.push(String(v));
  }
  get innerHTML() { return this._html; }
  addEventListener(ev, fn) { if (ev === 'click') this._click = fn; }
  querySelectorAll() { return []; }
  querySelector(sel) {
    return sel === '.empty-state' ? this._emptyState : null;
  }
  appendChild(child) {
    // renderTickerFeed appends one <div> per event whose innerHTML holds the
    // rendered line. Capture it here so the harness can assert the operator's
    // words, not just that a line was appended.
    if (this.id === 'event-ticker' && child && child.innerHTML) {
      global.__tickerWrites.push(child.innerHTML);
    }
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  async click() { if (this._click) await this._click(); }
}

const elements = new Map();

// renderTickerFeed rebuilds the feed's innerHTML from DOM rows that do not
// exist in the stub, so it would wipe whatever appendTickerEvent wrote. Record
// the lines on the element as they are set, before the re-render can clobber
// them, so the harness can assert what the operator would have seen.
// Every innerHTML write to the ticker is recorded globally. renderTickerFeed
// rewrites the feed from stub DOM that yields no rows, so the visible
// innerHTML is not a reliable witness -- a non-empty write here means
// appendTickerEvent ran and produced a line for the operator.
global.__tickerWrites = [];
global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeEl(id));
    return elements.get(id);
  },
  querySelectorAll(sel) {
    // renderTickerFeed looks for rows; none exist in the stub.
    return String(sel).indexOf('.toggle[data-svc]') !== -1 ? [] : [];
  },
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
global.fetch = async (p, opts) => {
  const path = String(p);
  const method = (opts && opts.method) || 'GET';
  if (method === 'POST') {
    log.fetches.push({ path, method });
    return fetchImpl(path, opts);
  }
  // GET /api/* : every status the poll asks for. Return the harness's stack.
  return { ok: true, status: 200, json: async () => currentStatus, text: async () => '' };
};

global.localStorage = {
  _v: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
  setItem(k, v) { this._v[k] = String(v); },
};

const fs = require('fs');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource',
             fs.readFileSync(appJsPath, 'utf8'))(
  mod, mod.exports, global.document, global.window,
  global.localStorage, global.EventSource);
const app = mod.exports;

let currentStatus = null;
const toggle = document.getElementById('btn-master-toggle');

function setStatus(status) {
  currentStatus = status;
  // renderServiceHeader is not exported; drive the paint through the same
  // entry point the page uses (renderServiceCards -> renderServiceHeader).
  app.renderServiceCards(status, null, null);
}

function runningStack() {
  return {
    bot_state: 'RUNNING',
    services: {
      filter: { running: true }, query: { running: true }, decide: { running: true },
    },
  };
}
function partialStack() {
  return {
    bot_state: 'RUNNING',
    services: { filter: { running: false }, query: { running: true }, decide: { running: false } },
  };
}
function stoppedStack() {
  return {
    bot_state: 'STOPPED',
    services: { filter: { running: false }, query: { running: false }, decide: { running: false } },
  };
}
function unknownStack() {
  return { bot_state: 'UNKNOWN', registry_unreadable: true, services: {} };
}

function state() {
  return {
    className: toggle.className,
    text: toggle.innerHTML,
    ariaLabel: toggle.getAttribute('aria-label'),
    action: toggle.dataset.action,
    disabled: toggle.disabled,
    ariaBusy: toggle.getAttribute('aria-busy'),
    pill: (() => {
      const p = document.getElementById('master-status-indicator');
      return p.textContent;
    })(),
    ticker: (global.__tickerWrites || []).join(' | '),
  };
}

function reset() {
  log.fetches.length = 0;
  global.__tickerWrites = [];
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

(async () => {
  // ── RUNNING: STOP look ──
  reset();
  setStatus(runningStack());
  const running = state();

  // ── Partial RUNNING: still the STOP look (a gap is not "stopped") ──
  reset();
  setStatus(partialStack());
  const partial = state();

  // ── STOPPED: START look ──
  reset();
  setStatus(stoppedStack());
  const stopped = state();

  // ── UNKNOWN: START look, never a crash ──
  reset();
  setStatus(unknownStack());
  const unknown = state();

  // ── Null service entry: no crash ──
  reset();
  let nullEntry = null;
  try {
    setStatus({ bot_state: 'RUNNING', services: { filter: null, query: { running: true }, decide: null } });
    nullEntry = state();
  } catch (e) {
    nullEntry = { error: String(e) };
  }

  // ── Double click while RUNNING: exactly one POST, busy state shown ──
  reset();
  setStatus(runningStack());
  const gate = deferred();
  fetchImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({ ok: true, message: 'Stack stopped', status: stoppedStack() }),
  });
  // Hold the fetch open until both clicks have been attempted.
  let release;
  const held = new Promise((r) => { release = r; });
  fetchImpl = async () => {
    await held;
    return {
      ok: true, status: 200,
      json: async () => ({ ok: true, message: 'Stack stopped', services: {}, status: stoppedStack() }),
    };
  };
  const click1 = toggle.click();
  const busyMidFlight = state();
  const click2 = toggle.click();   // must be ignored
  release();
  await Promise.all([click1, click2]);
  const afterStop = state();
  const stopPosts = log.fetches.filter((f) => f.path === '/api/system/stop').length;

  // ── Refused START: the message reaches the ticker ──
  reset();
  setStatus(stoppedStack());
  // Settle any in-flight poll so lastStatus is the stopped stack before the
  // click, otherwise the trailing poll paints a stale RUNNING state.
  await new Promise((r) => setTimeout(r, 0));
  fetchImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({ ok: false, message: 'Refusing to start: process registry unreadable' }),
  });
  await toggle.click();
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  const refused = state();

  // ── Partial STOP: message + the survivor reach the ticker ──
  reset();
  setStatus(runningStack());
  fetchImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({
      ok: false,
      message: 'STOP incomplete: decide still running (PID 5001). Registry kept for retry.',
      services: { decide: { outcome: 'still_running', detail: 'killpg failed' } },
      status: runningStack(),
    }),
  });
  await toggle.click();
  const partialStop = state();

  // ── HTTP 500 with a non-JSON body: an error line, not a crash ──
  reset();
  setStatus(stoppedStack());
  fetchImpl = async () => ({ ok: false, status: 500, json: async () => { throw new Error('not json'); }, text: async () => '<html>' });
  await toggle.click();
  const httpFailure = state();

  process.stdout.write(JSON.stringify({
    running, partial, stopped, unknown, nullEntry,
    doubleClick: { posts: stopPosts, busyMidFlight, afterStop },
    refused, partialStop, httpFailure,
  }));
})();
