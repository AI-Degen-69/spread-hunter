/* Input-lag harness (Issue #264): drives the real pollStatus with a stubbed
 * fetch, proving each poll repaints the header plus only the visible tab, and
 * that a tab switch paints synchronously from cache.
 *
 * Reads {scenario} as JSON on argv[2]. Element stubs are persistent per id so
 * hidden-flag flips and innerHTML writes are observable across calls.
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const scenario = input.scenario || 'hidden-tabs-skipped';

const noop = () => {};
function stubElement(id) {
  return {
    id,
    hidden: false,
    textContent: '',
    innerHTML: '',
    className: '',
    title: '',
    disabled: false,
    style: {},
    dataset: {},
    classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    addEventListener: noop,
    removeEventListener: noop,
    setAttribute: noop,
    getAttribute: () => null,
    removeAttribute: noop,
    querySelector: () => null,
    querySelectorAll: () => [],
    appendChild: noop,
    scrollIntoView: noop,
    click: noop,
  };
}

const elements = new Map();
function el(id) {
  if (!elements.has(id)) elements.set(id, stubElement(id));
  return elements.get(id);
}

const tabButtons = ['tab-btn-1', 'tab-btn-2', 'tab-btn-3'].map((id) => {
  const b = stubElement(id);
  b.id = id;
  elements.set(id, b);
  return b;
});

const store = {};
global.document = {
  getElementById: (id) => el(id),
  querySelector: () => null,
  querySelectorAll: (sel) => (sel === '.tab-btn' ? tabButtons : []),
  createElement: () => stubElement('created'),
  addEventListener: noop,
  body: stubElement('body'),
};
global.window = { addEventListener: noop, location: { href: '' } };
global.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
global.EventSource = function EventSource() {
  return { addEventListener: noop, close: noop, onerror: null, onmessage: null };
};

// Controllable rAF: callbacks queue until __flushRaf runs them. Lets the
// charts-deferred scenario prove the deferral is real, not just wired.
const rafQueue = [];
let rafId = 0;
global.requestAnimationFrame = (cb) => { rafQueue.push(cb); return ++rafId; };
global.__flushRaf = () => { const q = rafQueue.splice(0); q.forEach((cb) => cb(0)); return q.length; };

// One full poll snapshot, served per endpoint like the backend would.
// {fetchOk:false} simulates a dead backend: every endpoint answers unusable.
const fetchOk = input.fetchOk !== false;
const SNAPSHOT = {
  '/api/state': { orders: [], fills: [] },
  '/api/system/status': { bot_state: 'STOPPED', services: {} },
  '/api/kpi': {},
  '/api/scan-state': null,
  '/api/trial-readiness': null,
  '/api/guardrail-alerts': null,
  '/api/guardrail-health': null,
};
global.fetch = async (url) => (fetchOk
  ? { ok: true, json: async () => SNAPSHOT[url] ?? null }
  : { ok: false });

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;

function setTabs(t1Hidden, t2Hidden, t3Hidden) {
  el('tab-1').hidden = t1Hidden;
  el('tab-2').hidden = t2Hidden;
  el('tab-3').hidden = t3Hidden;
}

async function main() {
  let result;
  if (scenario === 'hidden-tabs-skipped') {
    // Arrange — only Tab 1 visible, like the operator sitting on LIVE OPERATIONS.
    setTabs(false, true, true);
    // Act — one real poll tick.
    await app.pollStatus();
    // Assert material: tab-1 targets painted, hidden-tab targets untouched.
    result = {
      tab1Head: el('orders-trades-head').innerHTML,
      kpiGrid: el('kpi-grid').innerHTML,
      marketBody: el('market-body').innerHTML,
      kanbanBoard: el('kanban-board').innerHTML,
      scanPill: el('market-scan-pill').innerHTML,
    };
  } else if (scenario === 'switch-paints-from-cache') {
    // Arrange — everything visible once so caches fill, then sit on Tab 1.
    setTabs(false, false, false);
    await app.pollStatus();
    app.switchTab(1);
    el('kpi-grid').innerHTML = '';
    el('market-body').innerHTML = '';
    el('kanban-board').innerHTML = '';
    // Act — operator clicks the Tab 2 button: must paint synchronously.
    app.switchTab(2);
    // Assert material.
    result = {
      tab1Hidden: el('tab-1').hidden,
      tab2Hidden: el('tab-2').hidden,
      tab3Hidden: el('tab-3').hidden,
      kpiGrid: el('kpi-grid').innerHTML,
      marketBody: el('market-body').innerHTML,
      kanbanBoard: el('kanban-board').innerHTML,
    };
  } else if (scenario === 'switch-tab3-double-click') {
    // Arrange — caches filled, operator on Tab 1.
    setTabs(false, false, false);
    await app.pollStatus();
    app.switchTab(1);
    el('kanban-board').innerHTML = '';
    el('kpi-grid').innerHTML = '';
    // Act — three rapid clicks: Tab 2, back to Tab 1, then Tab 3. Synchronous
    // toggles cannot queue, so only the last click's panel ends up painted.
    app.switchTab(2);
    app.switchTab(1);
    app.switchTab(3);
    // Assert material.
    result = {
      tab1Hidden: el('tab-1').hidden,
      tab2Hidden: el('tab-2').hidden,
      tab3Hidden: el('tab-3').hidden,
      kpiGrid: el('kpi-grid').innerHTML,
      kanbanBoard: el('kanban-board').innerHTML,
    };
  } else if (scenario === 'failed-poll') {
    // Arrange — dead backend (fetchOk:false passed on argv), all tabs visible.
    setTabs(false, false, false);
    // Act — one poll tick against failing endpoints must not throw.
    let crashed = null;
    try {
      await app.pollStatus();
      crashed = false;
    } catch (e) {
      crashed = String((e && e.message) || e);
    }
    // Assert material.
    result = {
      crashed,
      kpiGrid: el('kpi-grid').innerHTML,
      marketBody: el('market-body').innerHTML,
      kanbanBoard: el('kanban-board').innerHTML,
      scanPill: el('market-scan-pill').innerHTML,
    };
  } else if (scenario === 'charts-deferred') {
    // Arrange/Act — the deferral mechanism itself, no poll needed.
    let ran = 0;
    app.deferPaint(() => { ran++; });
    const queuedBeforeFlush = rafQueue.length;
    const ranBeforeFlush = ran;
    const flushed = global.__flushRaf();
    // And the no-rAF fallback (node without the stub) runs synchronously.
    global.requestAnimationFrame = undefined;
    let ranSync = 0;
    app.deferPaint(() => { ranSync++; });
    // Assert material.
    result = { queuedBeforeFlush, ranBeforeFlush, flushed, ranAfterFlush: ran, ranSync };
  } else {
    throw new Error('unknown scenario: ' + scenario);
  }
  process.stdout.write(JSON.stringify(result));
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
