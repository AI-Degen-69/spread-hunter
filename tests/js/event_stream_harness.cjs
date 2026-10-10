/* Harness: plain-English event stream without a browser.
 *
 * Loaded by tests/test_dashboard_server.py.
 * Feeds SSE events through connectSSE, then checks the TRADES tab shows only
 * trade rows, sentences carry market names with no codes, strings are
 * escaped, empty states match copy, and the details toggle sticks.
 * Prints one JSON line; the Python test asserts on it.
 */
'use strict';

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
    this.children = [];
    this.scrollTop = -1;
    this.classList = fakeClassList();
  }
  set innerHTML(v) { this._html = String(v); this.children = []; }
  get innerHTML() { return this._html; }
  addEventListener() {}
  querySelectorAll() { return []; }
  querySelector() { return null; }
  appendChild(c) { this.children.push(c); return c; }
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
  createElement(tag) { return new FakeEl(tag); },
  addEventListener() {},
  body: new FakeEl('body'),
};
global.window = { addEventListener() {}, matchMedia: () => ({ matches: false }) };
global.CONTROL_TOKEN = 'harness-token';

let sseInstance = null;
global.EventSource = function (url) {
  this.url = url;
  this.addEventListener = () => {};
  this.close = () => {};
  sseInstance = this;
};
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
  out.exportsOk = ['buildStreamSentence', 'isTradeEvent', 'streamMarketName',
    'setTickerFilter', 'setTickerShowDetails', 'connectSSE', 'translateEvent']
    .every((k) => typeof app[k] === 'function');

  const tickerEl = () => document.getElementById('event-ticker');
  const rows = () => tickerEl().children.map((c) => c.innerHTML);

  // Empty states first, on a fresh buffer.
  app.setTickerFilter('trades');
  out.emptyTrades = tickerEl().innerHTML;
  app.setTickerFilter('filter');
  out.emptyFilter = tickerEl().innerHTML;
  app.setTickerFilter('guardrail');
  out.emptyAlerts = tickerEl().innerHTML;
  app.setTickerFilter('all');
  out.emptyAll = tickerEl().innerHTML;

  // Feed one trade and one skip-only decide from different services.
  app.connectSSE();
  const send = (ev) => sseInstance.onmessage({ data: JSON.stringify(ev) });
  send({ ts: '2024-01-15T14:28:00Z', service: 'query', action: 'fill_recorded',
         market_slug: 'brazil-election',
         extra: { size: 3, price: 0.48, side: 'BUY', outcome: 'UP',
                  market_title: 'Brazil election', condition_id: '0xc1' } });
  send({ ts: '2024-01-15T14:29:00Z', service: 'decide', action: 'decide',
         market_slug: 'brazil-election', reason: 'spread too wide',
         extra: { intent_count: 0, condition_id: '0xc1', market_title: 'Brazil election' } });

  app.setTickerFilter('trades');
  out.tradesRows = rows();
  app.setTickerFilter('all');
  out.allRows = rows();
  out.scrolledToTop = tickerEl().scrollTop === 0;

  // Sentence builders across the inventoried actions.
  const ev = (action, extra, rest) => Object.assign(
    { ts: '2024-01-15T14:28:00Z', service: 'query', action, market_slug: 'x-y', extra }, rest || {});
  out.sentences = {
    fill: app.buildStreamSentence(ev('fill_recorded',
      { size: 3, price: 0.48, outcome: 'UP', market_title: 'Brazil election' })),
    exit: app.buildStreamSentence(ev('lifecycle_exited',
      { size: 4, fill_price: 0.44, outcome: 'DOWN', market_title: 'Brazil election' },
      { reason: 'grace expired' })),
    completed: app.buildStreamSentence(ev('lifecycle_completed',
      { ask: 0.49, market_title: 'Brazil election' })),
    mergeDone: app.buildStreamSentence(ev('merge_executed',
      { size: 5, market_title: 'Brazil election', relayer_state: 'STATE_EXECUTED' })),
    mergeFailed: app.buildStreamSentence(ev('merge_failed',
      { market_title: 'Brazil election' }, { reason: 'STATE_REVERTED' })),
    redeemUnknown: app.buildStreamSentence(ev('redeem_unknown', { condition_id: '0xc9' })),
    quoted: app.buildStreamSentence(ev('decide',
      { intent_count: 2, market_title: 'Brazil election',
        quotes: [{ side: 'UP', price: 0.48, size: 5 }, { side: 'DOWN', price: 0.49, size: 6 }] },
      { service: 'decide' })),
    skipped: app.buildStreamSentence(ev('decide',
      { intent_count: 0, market_title: 'Brazil election' },
      { service: 'decide', reason: 'spread too wide' })),
    submitPlaced: app.buildStreamSentence(ev('submit',
      { submitted: 2, market_title: 'Brazil election' }, { service: 'decide' })),
    unknown: app.buildStreamSentence(ev('frob_nicate', { condition_id: '0xzz' })),
  };

  // Trade markers are service-independent.
  out.tradeMarkers = {
    decideFill: app.isTradeEvent({ service: 'decide', action: 'fill_recorded', extra: {} }),
    queryExit: app.isTradeEvent({ service: 'query', action: 'lifecycle_exited', extra: {} }),
    rescue: app.isTradeEvent({ service: 'query', action: 'lifecycle_aged_out_rescue', extra: {} }),
    mergeAny: app.isTradeEvent({ service: 'query', action: 'merge_unknown', extra: {} }),
    submitPlaced: app.isTradeEvent({ service: 'decide', action: 'submit', extra: { submitted: 1 } }),
    submitEmpty: app.isTradeEvent({ service: 'decide', action: 'submit', extra: { submitted: 0 } }),
    decideSkip: app.isTradeEvent({ service: 'decide', action: 'decide', extra: { intent_count: 0 } }),
    waiting: app.isTradeEvent({ service: 'query', action: 'lifecycle_patient_wait', extra: {} }),
  };

  // Escaping: a hostile market title must not break out of the row.
  send({ ts: '2024-01-15T14:30:00Z', service: 'query', action: 'fill_recorded',
         market_slug: 'evil', extra: { size: 1, price: 0.5,
         market_title: '<script>alert(1)</script>' } });
  app.setTickerFilter('all');
  out.escapedRows = rows();

  // Details toggle: hidden by default, shown on toggle, survives rebuilds.
  app.setTickerShowDetails(false);
  app.setTickerFilter('trades');
  out.detailsOff = rows();
  app.setTickerShowDetails(true);
  out.detailsOn = rows();
  app.setTickerFilter('all');
  app.setTickerFilter('trades');
  out.detailsAfterRebuild = rows();
} catch (e) {
  out.error = String((e && e.stack) || e);
}

process.stdout.write(JSON.stringify(out));
