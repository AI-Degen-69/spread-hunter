/* Mounts the Tier 1 analytics row against a stub DOM and prints what each card
 * rendered, plus whether the row survives every analytics sub-view filter.
 * Used by tests/test_analytics_surface_mount.py.
 *
 * Reads {kpi} as JSON on argv[2].
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const noop = () => {};

/* A stub element that actually remembers what was written to it, so the test
 * can assert on rendered copy rather than on the call having been made. */
function stubElement(id) {
  return {
    id,
    textContent: '',
    innerHTML: '',
    className: '',
    hidden: false,
    style: {},
    dataset: {},
    classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    addEventListener: noop,
    querySelector: () => null,
    querySelectorAll: () => [],
    appendChild: noop,
    setAttribute: noop,
    getAttribute: () => null,
    closest: () => null,
    contains: () => true,
  };
}

const elements = {};
function element(id) {
  if (!elements[id]) elements[id] = stubElement(id);
  return elements[id];
}

// Every id the Tier 1 renderers and the view filter reach for.
for (const id of ['pnl-ci-readout', 'execution-funnel', 'sample-sufficiency-readout',
                  'card-sample-sufficiency', 'tier1-decision-row',
                  'quant-risk-deck', 'analytics-charts-matrix',
                  'card-sensitivity-simulator', 'analytics-gates',
                  'market-inspection-card']) {
  element(id);
}

global.document = {
  getElementById: (id) => element(id),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: noop,
  body: { classList: { add: noop, remove: noop, toggle: noop } },
  contains: () => true,
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
const errors = [];

try {
  app.renderPnlCiReadout(kpi.trade_analytics || {});
} catch (e) {
  errors.push(`pnl_ci: ${e && e.message}`);
}
try {
  app.renderExecutionFunnel(kpi);
} catch (e) {
  errors.push(`funnel: ${e && e.message}`);
}
try {
  app.renderSampleSufficiency(kpi.trade_analytics || {});
} catch (e) {
  errors.push(`sample_sufficiency: ${e && e.message}`);
}

/* Tier 1 is the row that decides whether we trade. Every sub-view filter has
 * to leave it alone -- a click on "Monte Carlo & VaR" must not blank the
 * go/no-go pair above it. */
const views = ['all', 'distributions', 'monte-carlo', 'markout', 'simulator', 'markets'];
const tier1Visibility = {};
for (const view of views) {
  try {
    app.applyStatsViewFilter(view);
  } catch (e) {
    errors.push(`filter ${view}: ${e && e.message}`);
  }
  tier1Visibility[view] = element('tier1-decision-row').style.display || '';
}

process.stdout.write(JSON.stringify({
  errors,
  pnl_ci_html: element('pnl-ci-readout').innerHTML,
  funnel_html: element('execution-funnel').innerHTML,
  sample_sufficiency_html: element('sample-sufficiency-readout').innerHTML,
  tier1_visibility: tier1Visibility,
}));
