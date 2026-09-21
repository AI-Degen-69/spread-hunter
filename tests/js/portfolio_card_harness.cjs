/* Drives renderBrokerPortfolioOverview against a stub DOM and prints what the
 * Portfolio Overview card would show. Used by tests/test_portfolio_card_basis.py.
 *
 * Reads one JSON payload {kpi, status} on argv[2] and writes the rendered
 * values as JSON on stdout.
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const elements = {};

// The chart harness deliberately exposes the chart containers so chart-series
// behavior can be asserted without starting a browser server.
const NULL_IDS = new Set();

function element(id) {
  if (!elements[id]) {
    const el = {
      id,
      textContent: '',
      innerHTML: '',
      className: '',
      title: '',
      style: {},
      classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
      dataset: {},
      _listeners: {},
      // Issue #259: the equity chart queries its own children and listens for
      // hover. Child stubs persist per selector so listeners stay attached.
      querySelector: (sel) => element(id + ' ' + String(sel)),
      querySelectorAll: () => [],
      appendChild() {},
      setAttribute() {},
      getAttribute: () => null,
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 230 }),
      addEventListener(type, fn) { el._listeners[type] = fn; },
    };
    elements[id] = el;
  }
  return elements[id];
}

global.document = {
  getElementById: (id) => (NULL_IDS.has(id) ? null : element(id)),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  body: { classList: { add() {}, remove() {}, toggle() {} } },
};
global.window = { addEventListener() {}, location: { href: '' } };
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
// app.js opens its SSE stream at load time, outside the module guard.
global.EventSource = function EventSource() {
  return { addEventListener() {}, close() {}, onerror: null, onmessage: null };
};
global.fetch = () => Promise.resolve({ ok: false, json: async () => ({}) });
global.setTimeout = global.setTimeout;

// Evaluated as CommonJS on purpose: package.json declares "type": "module",
// and app.js is a plain browser script that bootstraps itself unless a
// `module.exports` is present. Wrapping it hands it both a module object and
// the stub globals it renders against.
const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;
app.renderBrokerPortfolioOverview(input.kpi, input.status);

// Read the chart's deterministic series helper directly so the focused test
// can assert real close points, empty-state behavior, and the Current point.
const basis = app.portfolioEquity(input.kpi, input.status);
const chartSeries = app.buildBrokerEquitySeries(
  input.kpi,
  basis.startingCap,
  basis.totalVal,
  input.timeframe || 'ALL',
);

// Issue #259: sweep a synthetic hover across the chart and keep the first
// close-point tooltip (Start/Current carry no pnl, so they never match).
let tooltip_html = '';
const hoverSvg = element('broker-chart-svg-container')
  .querySelector('#broker-svg-chart');
if (hoverSvg && hoverSvg._listeners && hoverSvg._listeners.mousemove) {
  const rect = hoverSvg.getBoundingClientRect();
  for (let cx = 0; cx <= rect.width; cx += 10) {
    hoverSvg._listeners.mousemove({ clientX: cx });
    const html = element('broker-chart-tooltip').innerHTML || '';
    if (html.includes('Realized Spread')) { tooltip_html = html; break; }
  }
  if (!tooltip_html) tooltip_html = element('broker-chart-tooltip').innerHTML || '';
}

process.stdout.write(JSON.stringify({
  chart_total: basis.totalVal,
  chart_starting_capital: basis.startingCap,
  chart_series: chartSeries,
  chart_html: element('broker-chart-svg-container').innerHTML,
  tooltip_html,
  equity: element('broker-hero-equity').textContent,
  pnl: element('broker-pnl-amount').textContent,
  starting_capital: element('broker-starting-cap').textContent,
  cash: element('broker-kpi-cash').textContent,
  wallet_row_display: element('broker-venue-wallet-row').style.display,
  wallet: element('broker-venue-wallet').textContent,
  wallet_note: element('broker-venue-wallet-note').textContent,
}));
