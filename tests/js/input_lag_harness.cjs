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
  querySelectorAll: (sel) => {
    if (sel === '.tab-btn') return tabButtons;
    // Sidebar-pages layout (#140): the five rail pages, only when a scenario
    // mounts the rail. Scenarios without it exercise the tabs-era fallback.
    if (global.__railMounted && typeof sel === 'string'
        && sel.startsWith('#page-home,')) return RAIL_PAGES.map((p) => el('page-' + p));
    return [];
  },
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
// The 'failed-after-cache' scenario flips fetch dead mid-run via setFetchOk.
let fetchLive = input.fetchOk !== false;
const SNAPSHOT = {
  '/api/state': { orders: [], fills: [] },
  '/api/system/status': { bot_state: 'STOPPED', services: {} },
  '/api/kpi': { portfolio: { account: { account_value_usd: 100 } } },
  '/api/scan-state': null,
  '/api/trial-readiness': { trial_ready: true, ready_gates: ['gate-a'] },
  '/api/guardrail-alerts': null,
  '/api/guardrail-health': null,
};
global.fetch = async (url) => (fetchLive
  ? { ok: true, json: async () => SNAPSHOT[url] ?? null }
  : { ok: false });
global.__setFetchOk = (v) => { fetchLive = v !== false; };


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

/* Sidebar-pages layout (#95/#140): the live panels live under `#page-*`
 * sections and `prototype.js` shows exactly one of them. `page-*` stubs
 * auto-create through el() on first touch, so `hidden` flips are observable
 * like the real DOM's. */
const RAIL_PAGES = ['home', 'data-markets', 'strategy', 'trades', 'reports'];
function setRailPage(target) {
  for (const page of RAIL_PAGES) el('page-' + page).hidden = (page !== target);
}
function railVisible() {
  return RAIL_PAGES.filter((p) => el('page-' + p).hidden === false);
}

// Where each rendered-into panel moved when the rail mounted (prototype.js
// PAGE_LAYOUT). paintable() walks real parents; stubs need the same shape.
const PANEL_PAGE = {
  'service-cards': 'trades',
  'orders-trades-head': 'home',
  'orders-trades-body': 'home',
  'kpi-grid': 'reports',
  'market-body': 'data-markets',
  'kanban-board': 'data-markets',
  'broker-hero-equity': 'home',
};
for (const [panelId, page] of Object.entries(PANEL_PAGE)) {
  el(panelId).parentElement = el('page-' + page);
}

async function main() {
  let result;
  if (scenario === 'rail-pages-skipped') {
    // Arrange — the real post-#140 layout: the rail mounted and shows the
    // Trades page (service cards + event ticker); the legacy tab shells sit
    // un-hidden and empty, exactly as prototype.js leaves them.
    global.__railMounted = true;
    setTabs(false, true, true);
    setRailPage('trades');
    // Act — one real poll tick.
    await app.pollStatus();
    // Assert material — the Trades page's sections painted, every other rail
    // page's targets untouched, header pills live.
    result = {
      serviceCards: el('service-cards').innerHTML,
      masterIndicator: el('master-status-indicator').innerHTML,
      brokerHero: el('broker-hero-equity').textContent,
      ordersHead: el('orders-trades-head').innerHTML,
      kpiGrid: el('kpi-grid').innerHTML,
      marketBody: el('market-body').innerHTML,
      kanbanBoard: el('kanban-board').innerHTML,
      scanPill: el('market-scan-pill').innerHTML,
    };
  } else if (scenario === 'rail-skips-offpage-renders') {
    // Arrange — the operator sits on the rail's Dashboard (home) while the
    // legacy tab shells all read visible, exactly as prototype.js leaves them.
    // Act — one poll tick: the gating must consult the active rail page, so
    // sections living on other pages are skipped even though the legacy tab
    // gates all claim to be visible.
    global.__railMounted = true;
    setTabs(false, false, false);
    setRailPage('home');
    await app.pollStatus();
    // Assert material — Home's own Orders & Trades painted; the service cards
    // (Trades), KPI tiles (Reports), markets + kanban (Data & Markets) were
    // skipped. Header stays live — INCLUDING the master/engine/watchdog pills
    // renderServiceCards paints: they live in the top nav on every page, so
    // they must update even on a poll whose card grid the gate skipped.
    result = {
      serviceCards: el('service-cards').innerHTML,
      masterIndicator: el('master-status-indicator').innerHTML,
      brokerHero: el('broker-hero-equity').textContent,
      ordersHead: el('orders-trades-head').innerHTML,
      kpiGrid: el('kpi-grid').innerHTML,
      marketBody: el('market-body').innerHTML,
      kanbanBoard: el('kanban-board').innerHTML,
      scanPill: el('market-scan-pill').innerHTML,
    };
  } else if (scenario === 'hidden-tabs-skipped') {
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
  } else if (scenario === 'failed-after-cache') {
    // Arrange — one good poll fills the caches while every tab is visible.
    setTabs(false, false, false);
    await app.pollStatus();
    global.__flushRaf();
    const kpiBefore = el('kpi-grid').innerHTML;
    // Act — backend dies; next poll must not throw or blank cached paints.
    global.__setFetchOk(false);
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
      kpiBefore,
      kpiAfter: el('kpi-grid').innerHTML,
      readinessDisplay: el('trial-ready-banner').style.display,
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
