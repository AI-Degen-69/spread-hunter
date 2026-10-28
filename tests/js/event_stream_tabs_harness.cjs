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
  querySelector() { return null; },
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
  out.exportsOk = ['setTickerTab', 'setTickerFilter', 'tickerTabCounts', 'connectLogStream',
    'setTickerAutoscroll', 'tickerIsAutoscroll', 'tickerHandleScroll', 'updateTickerSubtitle']
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

  // Verify shadow-run picker population
  const pickerEl = document.getElementById('ticker-shadow-picker');
  if (typeof app.updateShadowRunPicker === 'function') {
    app.updateShadowRunPicker([{ run_id: 'shadow-run-xyz', running: true }]);
    out.pickerPopulated = Boolean(pickerEl && pickerEl.children && pickerEl.children.some(c => c.value === 'shadow-run-xyz'));
  } else {
    out.pickerPopulated = false;
  }

  // ── Component dropdown and combined filters ──
  app.setTickerTab('live');
  app.setTickerFilter('all');
  app.setTickerComponent('all');

  emitLogEvent({
    source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:32:00Z', service: 'user_stream',
             action: 'user_ws_connected', extra: {} },
  });
  emitLogEvent({
    source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:32:01Z', service: 'market_stream',
             action: 'market_stream_connected', extra: { tokens: ['t1', 't2'] } },
  });
  // Socket traffic is data, not connection health: a tick must not appear in
  // CONNECTIONS, or the chip becomes a firehose.
  emitLogEvent({
    source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:32:02Z', service: 'market_stream',
             action: 'market_stream_tick', extra: { books: 4, frames: 53 } },
  });
  emitLogEvent({
    source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:32:03Z', service: 'guardrail',
             action: 'stop_loss_triggered', reason: 'naked leg', extra: {} },
  });
  emitLogEvent({
    source: 'text', family: 'live', file: 'guard.log', level: 'ERROR',
    text: 'ERROR settlement sweep failed', message: 'Settlement sweep failed',
  });

  out.componentChoices = app.tickerComponentChoices();
  const compSel = document.getElementById('ticker-component-select');
  out.componentSelectValues = compSel.children.map((c) => c.value);

  // One component at a time: USER_WS keeps its connect row and drops the rest.
  app.setTickerComponent('USER_WS');
  out.componentRows = rows();
  out.componentFiltered = out.componentRows.length === 1
    && out.componentRows[0].includes('[USER_WS]');

  // Component AND category: QUERY plus TRADES is the fill, nothing else.
  app.setTickerComponent('QUERY');
  app.setTickerFilter('trades');
  out.combinedRows = rows();
  out.combinedFiltered = out.combinedRows.length === 1
    && out.combinedRows[0].includes('[QUERY]') && out.combinedRows[0].includes('[FILL]');

  // CONNECTIONS: both sockets, neither the trade nor the guardrail row.
  app.setTickerComponent('all');
  app.setTickerFilter('connections');
  out.connectionRows = rows();
  out.connectionsFiltered = out.connectionRows.length === 2
    && out.connectionRows.every((r) => r.includes('[USER_WS]') || r.includes('[MKT_WS]'));
  out.connectionExclusions = !app.tickerIsConnectionEvent({ service: 'market_stream', action: 'market_stream_tick' })
    && !app.tickerIsConnectionEvent({ service: 'user_stream', action: 'user_ws_trade' })
    // A stop-loss is an alert; a rerank pass is a filter scan. Neither is a
    // socket, and both used to repeat forever in this chip.
    && !app.tickerIsConnectionEvent({ service: 'guardrail', action: 'stop_loss_triggered' })
    && !app.tickerIsConnectionEvent({ service: 'engine', action: 'rerank_done' })
    && app.tickerIsConnectionEvent({ service: 'user_stream', action: 'user_ws_disconnected' })
    && app.tickerIsConnectionEvent({ service: 'market_stream', action: 'market_stream_subscribe' });

  // ALERTS: the guardrail row and the console ERROR, not the trade.
  app.setTickerFilter('guardrail');
  out.alertRows = rows();
  out.alertsFiltered = out.alertRows.length === 2
    && out.alertRows.every((r) => r.includes('[GUARD]'))
    && out.alertRows.some((r) => r.includes('Settlement sweep failed'))
    && out.alertRows.some((r) => r.includes('stop loss triggered'));

  // Empty feeds name the tab, the component, and the category they narrowed to.
  app.setTickerFilter('connections');
  app.setTickerTab('filter');
  const emptyScopedTab = document.getElementById('event-ticker').innerHTML;
  out.emptyScopedTab = emptyScopedTab.includes('No connection events yet.')
    && emptyScopedTab.includes('Showing filter loop · connections only.');

  // Switching tabs retires a component the new tab does not carry.
  app.setTickerTab('live');
  app.setTickerComponent('USER_WS');
  app.setTickerTab('filter');
  out.componentReset = app.tickerComponentChoices().length === 1
    && app.tickerComponentChoices()[0] === 'FILTER'
    && document.getElementById('ticker-component-select').value === 'all';

  // ── Screener scans on shadow stdout land in MARKET FILTER ──
  emitLogEvent({ source: 'text', family: 'shadow', file: 'shadow_run-abc.out.log',
    text: 'INFO tape-read brazil run=shadow cycle=8 status=empty qual_rows=0' });
  app.setTickerTab('shadow');
  app.setTickerComponent('all');
  app.setTickerFilter('filter');
  out.shadowScanRows = rows();
  out.shadowScanTagged = out.shadowScanRows.some((r) => r.includes('[FILTER]') && r.includes('[SCAN]'));
  app.setTickerFilter('all');

  // ── Reading mode: scrolling away stops the feed jumping to the top ──
  app.setTickerTab('live');
  app.setTickerFilter('all');
  app.setTickerComponent('all');
  app.setTickerAutoscroll(true);
  const tickerBox = document.getElementById('event-ticker');
  tickerBox.scrollTop = 64;
  app.tickerHandleScroll();
  out.autoPausedOnScroll = app.tickerIsAutoscroll() === false;

  emitLogEvent({
    source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:33:00Z', service: 'user_stream',
             action: 'user_ws_reconnecting', extra: { delay_sec: 2 } },
  });
  out.scrollHeldWhileReading = tickerBox.scrollTop === 64;

  app.setTickerAutoscroll(true);
  emitLogEvent({
    source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:33:01Z', service: 'user_stream',
             action: 'user_ws_connected', extra: {} },
  });
  out.scrollPinnedWhenFollowing = tickerBox.scrollTop === 0;
  out.pauseButtonLabel = document.getElementById('btn-pause-ticker').textContent;

  // ── A fill received on the user stream is a trade ──
  emitLogEvent({ source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:34:00Z', service: 'user_stream', action: 'user_fill_received',
             market_slug: 'brazil-election',
             extra: { outcome: 'UP', size: 2, market_title: 'Brazil election' } } });
  app.setTickerTab('live');
  app.setTickerComponent('all');
  app.setTickerAutoscroll(true);
  app.setTickerFilter('trades');
  out.fillInTrades = rows().some((r) => r.includes('Fill received'));

  // ── A single-leg transition is an alert ──
  emitLogEvent({ source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:34:01Z', service: 'decide', action: 'single_leg_transition',
             market_slug: 'brazil-election',
             extra: { prev_state: 'paired', new_state: 'single', market_title: 'Brazil election' } } });
  app.setTickerFilter('guardrail');
  out.transitionInAlerts = rows().some((r) => r.includes('ticker-translation warn') && r.includes('Single leg'));

  // ── CONNECTIONS is socket lifecycle only: a rerank pass is not a connection ──
  emitLogEvent({ source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:34:02Z', service: 'engine', action: 'rerank_done',
             extra: { duration_ms: 42 } } });
  app.setTickerFilter('connections');
  out.rerankNotConnection = !rows().some((r) => r.includes('Ranking pass'));

  // ── QUOTES: one distilled row per market, not two rows per cycle ──
  const decide = (slug, title, cid, up, down, at) => emitLogEvent({ source: 'ring', family: 'live',
    event: { ts: at, service: 'decide', action: 'decide', market_slug: slug,
             extra: { intent_count: 2, bot_market_title: title, condition_id: cid,
                      quotes: [{ side: 'UP', price: up, size: 5 }, { side: 'DOWN', price: down, size: 6 }] } } });
  const submit = (slug, n, at) => emitLogEvent({ source: 'ring', family: 'live',
    event: { ts: at, service: 'decide', action: 'submit', market_slug: slug,
             extra: { submitted: n, cancelled: 0 } } });
  // Brazil placed once, then re-quoted with nothing new: RESTING from history.
  decide('brazil-election', 'Brazil election', '0xbrazil', 0.48, 0.49, '2024-01-15T14:35:00Z');
  submit('brazil-election', 2, '2024-01-15T14:35:01Z');
  decide('brazil-election', 'Brazil election', '0xbrazil', 0.48, 0.49, '2024-01-15T14:35:02Z');
  submit('brazil-election', 0, '2024-01-15T14:35:03Z');
  // Canada just placed: PLACED.
  decide('canada-election', 'Canada election', '0xcanada', 0.51, 0.47, '2024-01-15T14:35:04Z');
  submit('canada-election', 2, '2024-01-15T14:35:05Z');
  // Argentina decided, and /api/state says one leg is resting there.
  decide('argentina-election', 'Argentina election', '0xarg', 0.3, 0.4, '2024-01-15T14:35:06Z');
  // Peru decided with nothing resting: PLANNED.
  decide('peru-election', 'Peru election', '0xperu', 0.2, 0.6, '2024-01-15T14:35:07Z');
  // The ring also re-decides markets the registry never quoted. Those carry no
  // legs, so they must not turn into "no legs quoted" rows beside the real book.
  emitLogEvent({ source: 'ring', family: 'live',
    event: { ts: '2024-01-15T14:35:08Z', service: 'decide', action: 'decide',
             market_slug: 'chile-election',
             extra: { intent_count: 0, bot_market_title: 'Chile election',
                      condition_id: '0xchile' } } });

  app.noteTickerQuoteState({
    orders: [
      { status: 'open', condition_id: '0xbrazil' },
      { status: 'open', condition_id: '0xbrazil' },
    ],
    // A fill the ring tail no longer reaches still belongs in TRADES.
    fills: [
      { trade_id: 'shadow-filled-1', side: 'BUY', size: 5, price: 0.49,
        recorded_ts: Date.UTC(2024, 0, 15, 14, 30, 0), condition_id: '0xnaked',
        pair_id: 'pair-naked' },
    ],
    pairs: [
      // Both legs resting, combined under a dollar.
      { pair_id: 'pair-brazil', condition_id: '0xbrazil', hedge_state: 'RESTING',
        combined_price: 0.97,
        market: { condition_id: '0xbrazil', title: 'Brazil election', slug: 'brazil-election' },
        orders: [
          { price: 0.45, original_size: 5, size_remaining: 5, status: 'open', age_sec: 240 },
          { price: 0.48, original_size: 5, size_remaining: 5, status: 'open', age_sec: 240 },
        ] },
      // Argentina: one leg resting on a pair that is still paired.
      { pair_id: 'pair-arg', condition_id: '0xarg', hedge_state: 'RESTING',
        combined_price: 0.70,
        market: { condition_id: '0xarg', title: 'Argentina election', slug: 'argentina-election' },
        orders: [{ price: 0.30, original_size: 6, status: 'open', age_sec: 60 }] },
      // Canada: the submit in the ring says two new orders went out.
      { pair_id: 'pair-canada', condition_id: '0xcanada', hedge_state: 'RESTING',
        combined_price: 0.98,
        market: { condition_id: '0xcanada', title: 'Canada election', slug: 'canada-election' },
        orders: [
          { price: 0.51, original_size: 5, status: 'open', age_sec: 30 },
          { price: 0.47, original_size: 5, status: 'open', age_sec: 30 },
        ] },
      // A naked pair: the failure mode, and it must outrank everything.
      { pair_id: 'pair-naked', condition_id: '0xnaked', hedge_state: 'NAKED',
        combined_price: 0.93,
        break_even: { break_even_price: 0.51, first_fill_price: 0.49 },
        market: { condition_id: '0xnaked', title: 'Tennis final', slug: 'tennis-final' },
        orders: [
          { price: 0.44, original_size: 5, size_remaining: 5, status: 'open', age_sec: 120 },
          // A filled leg keeps original_size but reports nothing remaining.
          { price: 0.49, original_size: 5, size_remaining: 0, status: 'filled', display_status: 'merged' },
        ] },
      // A fully merged pair is history, but its posted sizes still read right.
      { pair_id: 'pair-merged', condition_id: '0xmerged', hedge_state: 'MERGED',
        combined_price: 0.93,
        market: { condition_id: '0xmerged', title: 'Merged pair', slug: 'merged-pair' },
        orders: [
          { price: 0.40, original_size: 5, size_remaining: 0, status: 'filled', display_status: 'merged' },
          { price: 0.53, original_size: 5, size_remaining: 0, status: 'filled', display_status: 'merged' },
        ] },
      // Over a dollar: a booked loss has to say so in words.
      { pair_id: 'pair-over', condition_id: '0xover', hedge_state: 'RESTING',
        combined_price: 1.02,
        market: { condition_id: '0xover', title: 'Hockey overtime', slug: 'hockey-ot' },
        orders: [
          { price: 0.55, original_size: 5, status: 'open', age_sec: 900 },
          { price: 0.47, original_size: 5, status: 'open', age_sec: 900 },
        ] },
    ],
  });
  app.setTickerFilter('quotes');
  out.quoteRows = rows();
  out.quoteDistilled = out.quoteRows.length === 7;
  out.quotePlaced = out.quoteRows.some((r) => r.includes('[PLACED]') && r.includes('Canada election'));
  out.quoteResting = out.quoteRows.some((r) => r.includes('[RESTING]') && r.includes('Brazil election')
    && r.includes('0.45×5 + 0.48×5') && r.includes('= $0.97') && r.includes('+3¢'));
  out.quoteRestingFromState = out.quoteRows.some((r) => r.includes('[RESTING]')
    && r.includes('Argentina election') && r.includes('0.30×6'));
  out.quotePlanned = out.quoteRows.some((r) => r.includes('[PLANNED]') && r.includes('Peru election'));
  // A decide with no legs and no registry pair is churn, not a quote.
  out.quoteNoiseDropped = !out.quoteRows.some((r) => r.includes('Chile election'))
    && !out.quoteRows.some((r) => r.includes('no legs quoted'));
  // A naked leg is amber, says so, and sorts above every hedged pair.
  out.quoteNakedFirst = /ticker-translation warn".*?\[NAKED\]/.test(out.quoteRows[0] || '')
    && out.quoteRows[0].includes('break-even $0.51');
  out.quoteOverOneDollar = out.quoteRows.some((r) => r.includes('Hockey overtime')
    && r.includes('OVER $1'));
  // A filled leg must show the size that was posted, never `×0`.
  out.filledLegSize = out.quoteRows.some((r) => r.includes('[MERGED]')
    && r.includes('0.40×5 + 0.53×5') && !out.quoteRows.some((x) => x.includes('×0')));

  // TRADES reads the registry, so the early fill is there even though the ring
  // tail starts long after it; a ring row with the same id is not doubled.
  app.setTickerFilter('trades');
  const tradeRows = rows();
  out.tradesFromRegistry = tradeRows.some((r) => r.includes('[FILL]')
    && r.includes('BUY 5 @ $0.49') && r.includes('Tennis final'));
  out.tradesDeduped = tradeRows.filter((r) => r.includes('shadow-filled-1')).length === 0
    && tradeRows.length >= 1;

  // ALERTS names a naked pair as live risk, without waiting for its transition row.
  app.setTickerFilter('guardrail');
  const alertRows = rows();
  out.nakedInAlerts = alertRows.some((r) => r.includes('[NAKED LEG]')
    && r.includes('Tennis final'));

  // An empty registry and an empty ring leave the view empty, not blank.
  app.noteTickerQuoteState({ orders: [], pairs: [], fills: [] });
  app.setTickerFilter('quotes');
  app.setTickerTab('filter');
  out.emptyQuotes = document.getElementById('event-ticker').innerHTML.includes('No quotes yet.');
  app.setTickerTab('live');
  app.setTickerFilter('all');

  // ── An unavailable feed explains itself instead of showing a blank tab ──
  activeLogStreamSource.emit('source', {
    family: 'live', labels: ['cycle_events.jsonl'], available: false,
    reason: 'Cycle ring not found on disk', run_id: null,
  });
  app.setTickerTab('live');
  const unavailableHtml = document.getElementById('event-ticker').innerHTML;
  out.liveUnavailableText = unavailableHtml.includes('Live run feed unavailable.')
    && unavailableHtml.includes('Cycle ring not found on disk');
  out.liveSubtitle = document.getElementById('ticker-source-subtitle').textContent;

  // The missing feed outranks the category: TRADES on an unavailable live feed
  // must not pretend the stream is healthy and merely quiet.
  app.setTickerFilter('trades');
  out.liveUnavailableWithCategory = document.getElementById('event-ticker').innerHTML
    .includes('Live run feed unavailable.');
  app.setTickerFilter('all');

} catch (err) {
  out.error = String(err && err.stack ? err.stack : err);
}

process.stdout.write(JSON.stringify(out));
