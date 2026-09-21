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

// One full poll snapshot, served per endpoint like the backend would.
const SNAPSHOT = {
  '/api/state': { orders: [], fills: [] },
  '/api/system/status': { bot_state: 'STOPPED', services: {} },
  '/api/kpi': {},
  '/api/scan-state': null,
  '/api/trial-readiness': null,
  '/api/guardrail-alerts': null,
  '/api/guardrail-health': null,
};
global.fetch = async (url) => ({
  ok: true,
  json: async () => SNAPSHOT[url] ?? null,
});

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
  } else {
    throw new Error('unknown scenario: ' + scenario);
  }
  process.stdout.write(JSON.stringify(result));
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
