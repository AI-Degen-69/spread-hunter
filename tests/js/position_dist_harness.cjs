/* Renders the position-return distribution chart against a stub DOM and prints
 * the badge and footer text. Used by tests/test_statistical_analytics.py.
 *
 * Reads {statistical_analytics} as JSON on argv[2], plus an optional `metric`
 * (`pnl_pct` | `pnl_usd` | `spread_cost` | `outcome_prob`) and `ciLevel` (90 |
 * 95), so a test can render the arm and level it means to check rather than only
 * whichever the page happens to default to.
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const noop = () => {};
const elements = {};

function stubElement(id) {
  if (!elements[id]) {
    elements[id] = {
      id, textContent: '', innerHTML: '', className: '', title: '',
      style: {}, dataset: {},
      classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
      addEventListener: noop,
      querySelector: () => null, querySelectorAll: () => [],
      appendChild: noop, setAttribute: noop, getAttribute: () => null,
    };
  }
  return elements[id];
}

global.document = {
  getElementById: stubElement,
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

// The renderer's metric and CI level live as `let` bindings inside the
// evaluated source, so setting a property on the exports object never
// reaches them. The assignments are appended to the source itself, inside
// the same scope the render function reads.
let source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
if (input.metric != null) source += `\n;currentDistMetric = ${JSON.stringify(input.metric)};`;
if (input.ciLevel != null) source += `\n;currentCiLevel = ${input.ciLevel};`;

const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;

app.renderPositionDistributionChart(input.statistical_analytics || {});

process.stdout.write(JSON.stringify({
  badge: stubElement('dist-ci-badge').textContent,
  footer: stubElement('position-dist-footer').innerHTML.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(),
  banner: stubElement('dist-power-banner').innerHTML.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(),
  chartEmpty: /No closed positions recorded/.test(stubElement('position-dist-svg-container').innerHTML),
  // The drawn chart's own text, so a test can read the axis domain and the
  // shaded interval out of the SVG the renderer produced.
  svgText: stubElement('position-dist-svg-container').innerHTML.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(),
  // Counts the drawn envelope, not the gradient definition it references:
  // 1 when the renderer put a shaded band on the chart, 0 when it did not.
  ciZones: (stubElement('position-dist-svg-container').innerHTML.match(/url\(#ciZoneGrad\)/g) || []).length,
  // The raw markup, so a test can assert on elements (dot circles, curve
  // paths) that tag-stripping would erase.
  chart: stubElement('position-dist-svg-container').innerHTML,
}));
