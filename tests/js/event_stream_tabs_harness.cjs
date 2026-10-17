/* Harness: per-process log stream tabs, buffer isolation, and escaping without a browser.
 *
 * Loaded by tests/test_dashboard_server.py.
 * Verifies that:
 * 1. Feeding 500 shadow rehearsal events does NOT evict live run or filter loop events.
 * 2. Each family buffer caps at 200 items.
 * 3. The 'all' merged buffer caps at 200 items and preserves arrival order.
 * 4. Switching tabs renders the respective isolated buffers.
 * 5. Category filters (trades, filter, guardrail) operate within the active tab.
 * 6. Plain text log lines (rerank.log) with HTML/script characters are safely escaped.
 */
'use strict';

const appJsPath = process.argv[2];

function fakeClassList() {
  const set = new Set();
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    contains: (c) => set.has(c),
    toggle: (c, force) => {
      if (force === undefined) {
        if (set.has(c)) { set.delete(c); return false; }
        set.add(c); return true;
      }
      if (force) { set.add(c); return true; }
      set.delete(c); return false;
    },
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
    this.children = [];
    this.scrollTop = -1;
    this.classList = fakeClassList();
    this.options = [];
  }
  set innerHTML(v) { this._html = String(v); this.children = []; }
  get innerHTML() { return this._html; }
  addEventListener() {}
  querySelectorAll() { return []; }
  querySelector() { return null; }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this[k] = String(v); }
  getAttribute(k) { return this[k] || null; }
}

const elements = new Map();
global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeEl(id));
    return elements.get(id);
  },
  querySelectorAll() { return []; },
  createElement(tag) { return new FakeEl(tag); },
  addEventListener() {},
  body: new FakeEl('body'),
};
global.window = { addEventListener() {}, matchMedia: () => ({ matches: false }) };
global.CONTROL_TOKEN = 'harness-token';

let activeLogStreamSource = null;

class MockEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    if (url.includes('/api/log-stream')) {
      activeLogStreamSource = this;
    }
  }
  addEventListener(evt, fn) {
    this.listeners[evt] = this.listeners[evt] || [];
    this.listeners[evt].push(fn);
  }
  emit(evt, payload) {
    const list = this.listeners[evt] || [];
    for (const fn of list) {
      fn({ data: JSON.stringify(payload) });
    }
  }
  close() {
    this.listeners = {};
  }
}

global.EventSource = MockEventSource;
global.setInterval = () => 0;
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

const out = { exportsOk: false };

try {
  out.exportsOk = ['setTickerTab', 'setTickerFilter', 'tickerTabCounts', 'connectLogStream']
    .every((k) => typeof app[k] === 'function');

  const emitLogEvent = (payload) => {
    if (activeLogStreamSource) {
      activeLogStreamSource.emit('log', payload);
    }
  };

  // 1. Emit 1 live trading fill event
  emitLogEvent({
    source: 'ring',
    family: 'live',
    event: {
      ts: '2024-01-15T14:28:00Z',
      service: 'query',
      action: 'fill_recorded',
      market_slug: 'brazil-election',
      extra: {
        size: 3,
        price: 0.48,
        side: 'BUY',
        outcome: 'UP',
        market_title: 'Brazil election',
        condition_id: '0xc1',
      },
    },
  });

  // 2. Emit 500 shadow rehearsal events
  for (let i = 1; i <= 500; i++) {
    emitLogEvent({
      source: 'ring',
      family: 'shadow',
      event: {
        ts: '2024-01-15T14:29:00Z',
        service: 'decide',
        action: 'eval_market',
        market_slug: `market-rehearsal-${i}`,
        extra: { iteration: i },
      },
    });
  }

  // 3. Emit 10 filter events (9 normal, 1 hostile with XSS characters)
  for (let i = 1; i <= 9; i++) {
    emitLogEvent({
      source: 'text',
      family: 'filter',
      file: 'rerank.log',
      text: `Scan iteration ${i} completed in 42ms`,
    });
  }
  emitLogEvent({
    source: 'text',
    family: 'filter',
    file: 'rerank.log',
    text: "<script>alert('xss-filter')</script>",
  });

  // Verify tab buffer counts & isolation
  const counts = app.tickerTabCounts();
  out.counts = counts;
  out.liveRetained = counts.live === 1;
  out.shadowCapped = counts.shadow === 200;
  out.filterCount = counts.filter === 10;
  out.allCapped = counts.all === 200;

  const tickerEl = () => document.getElementById('event-ticker');
  const rows = () => tickerEl().children.map((c) => c.innerHTML);

  // Verify rendering by tab
  app.setTickerTab('live');
  out.liveRows = rows();

  app.setTickerTab('shadow');
  out.shadowRowCount = rows().length;

  app.setTickerTab('filter');
  out.filterRows = rows();
  out.filterEscaped = out.filterRows.some((r) => r.includes('&lt;script&gt;alert(') && !r.includes('<script>'));

  app.setTickerTab('all');
  out.allRowCount = rows().length;

  // Verify category filter within live tab
  app.setTickerTab('live');
  app.setTickerFilter('trades');
  out.liveTradesRows = rows();

  app.setTickerFilter('filter');
  out.liveFilterRows = rows(); // should be empty (0 rows)

  app.setTickerFilter('all');

} catch (err) {
  out.error = String(err && err.stack ? err.stack : err);
}

process.stdout.write(JSON.stringify(out));
