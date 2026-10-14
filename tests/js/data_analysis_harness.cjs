/* Drives the Data Analysis tab (#541) against a stub DOM and prints what each
 * block rendered plus the claim-ledger state. Used by tests/test_541_data_analysis.py.
 *
 * Reads the payload from the file on argv[2] (Windows argv limits forbid inline
 * JSON); optional action on argv[3:
 *   render        full renderDataAnalysis(kpi) (default)
 *   double-claim  claimBlock('da-quant') twice — must throw
 *   empty         renderDataAnalysis({}) — must not throw
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const action = process.argv[3] || 'render';
const noop = () => {};

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

const staticDir = path.resolve(__dirname, '..', '..', 'dashboard', 'static');
const source = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8')
  + '\n;\n'
  + fs.readFileSync(path.join(staticDir, 'data_analysis.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;

const out = { errors: [] };
try {
  if (action === 'double-claim') {
    app.resetStatLedger();
    app.claimBlock('da-quant');
    try {
      app.claimBlock('da-quant');
      out.doubleClaimThrew = false;
    } catch (e) {
      out.doubleClaimThrew = true;
      out.doubleClaimMessage = String((e && e.message) || e);
    }
  } else {
    const kpi = action === 'empty' ? {} : (input.kpi || {});
    app.renderDataAnalysis(kpi, {});
    out.claimed = app.claimedBlocks();
    out.registryBlocks = Array.from(
      new Set(Object.keys(app.STAT_REGISTRY).map((id) => app.STAT_REGISTRY[id].block)));
    const html = (id) => element(id).innerHTML;
    out.html = {
      headline: html('da-headline'),
      gates: html('da-gates'),
      capital: html('da-capital'),
      quant: html('da-quant'),
      dist: html('da-dist-container'),
      distFooter: html('da-dist-footer'),
      paircost: html('da-paircost-container'),
      mc: html('da-mc-container'),
      bell: html('da-bell-container'),
      markout: html('da-markout-container'),
    };
    out.samplePill = element('da-sample-count').textContent;
  }
} catch (e) {
  out.errors.push(`${action}: ${(e && e.stack) || e}`);
}

process.stdout.write(JSON.stringify(out));
