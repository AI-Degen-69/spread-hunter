/* Drives the Orders & Trades table's row builders against a stub DOM and
 * prints the rendered HTML per view plus the tab counts. Used by
 * tests/test_orders_trades_table.py.
 *
 * Reads {kpi, state, view} as JSON on argv[2].
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);

const noop = () => {};
function stubElement(id) {
  return {
    id,
    textContent: '',
    innerHTML: '',
    className: '',
    title: '',
    style: {},
    dataset: {},
    classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    addEventListener: noop,
    querySelector: () => null,
    querySelectorAll: () => [],
    appendChild: noop,
    setAttribute: noop,
    getAttribute: () => null,
  };
}

global.document = {
  getElementById: (id) => stubElement(id),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: noop,
  body: { classList: { add: noop, remove: noop, toggle: noop } },
};
global.window = { addEventListener: noop, location: { href: '' } };
global.localStorage = { getItem: () => null, setItem: noop, removeItem: noop };
global.EventSource = function EventSource() {
  return { addEventListener: noop, close: noop, onerror: null, onmessage: null };
};

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;

const kpi = input.kpi || {};
const state = input.state || {};
const view = input.view || 'active-markets';
const sort = input.sort || null;
const html = app.ordersTradesRows(view, kpi, state, sort);

process.stdout.write(JSON.stringify({
  view,
  sort,
  columns: app.OT_COLUMNS[view],
  head: app.otHeadHtml(view, sort),
  html,
  rows: (html.match(/<tr /g) || []).length,
  counts: app.ordersTradesCounts(kpi, state),
  views: app.OT_VIEWS,
}));
