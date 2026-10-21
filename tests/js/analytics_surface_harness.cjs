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
                  'card-sample-sufficiency', 'fill-path-edge-readout',
                  'rescue-friction-readout', 'stop-slippage-readout', 'queue-realism-readout',
                  'robustness-readout', 'capital-risk-readout',
                  'analytics-gates']) {
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
/* Called directly rather than through renderAnalyticsSurface: the full mount
 * wraps each isolated renderer in its own try/catch, which would swallow a
 * throw instead of reporting it here. */
try {
  app.renderFillPathEdge(kpi.statistical_analytics || {});
} catch (e) {
  errors.push(`fill_path_edge: ${e && e.message}`);
}
try {
  app.renderRescueFriction(kpi.statistical_analytics || {});
} catch (e) {
  errors.push(`rescue_friction: ${e && e.message}`);
}
try {
  app.renderStopSlippage(kpi.statistical_analytics || {});
} catch (e) {
  errors.push(`stop_slippage: ${e && e.message}`);
}
try {
  app.renderQueueRealism(kpi.statistical_analytics || {});
} catch (e) {
  errors.push(`queue_realism: ${e && e.message}`);
}
try {
  app.renderRobustness(kpi.statistical_analytics || {});
} catch (e) {
  errors.push(`robustness: ${e && e.message}`);
}
try {
  app.renderCapitalRisk(kpi.statistical_analytics || {});
} catch (e) {
  errors.push(`capital_risk: ${e && e.message}`);
}

process.stdout.write(JSON.stringify({
  errors,
  pnl_ci_html: element('pnl-ci-readout').innerHTML,
  funnel_html: element('execution-funnel').innerHTML,
  sample_sufficiency_html: element('sample-sufficiency-readout').innerHTML,
  fill_path_edge_html: element('fill-path-edge-readout').innerHTML,
  rescue_friction_html: element('rescue-friction-readout').innerHTML,
  stop_slippage_html: element('stop-slippage-readout').innerHTML,
  queue_realism_html: element('queue-realism-readout').innerHTML,
  robustness_html: element('robustness-readout').innerHTML,
  capital_risk_html: element('capital-risk-readout').innerHTML,
}));
