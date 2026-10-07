/* Feeds a sequence of /api/system/status payloads to setFilterUptime and prints
 * what the SCREENER header would read after each one. Used by
 * tests/test_service_uptime.py.
 *
 * Reads a JSON array of status payloads on argv[2].
 */
const fs = require('fs');
const path = require('path');

const payloads = JSON.parse(process.argv[2]);

const noop = () => {};
const elements = {};
function stubElement(id) {
  if (!elements[id]) {
    elements[id] = {
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
// app.js opens its SSE stream at load time, outside the module guard.
global.EventSource = function EventSource() {
  return { addEventListener: noop, close: noop, onerror: null, onmessage: null };
};
// Pinned so the between-poll drift is zero and the assertions describe the
// payloads, not how long node took to start.
global.performance = { now: () => 1000 };

// Evaluated as CommonJS on purpose: package.json declares "type": "module", and
// app.js is a plain browser script that bootstraps itself unless a
// `module.exports` is present.
const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', 'performance', source)(
  mod, mod.exports, global.document, global.window, global.localStorage,
  global.EventSource, global.performance);
const app = mod.exports;

const readings = payloads.map((status) => {
  app.setFilterUptime(status);
  return stubElement('scan-filter-uptime').textContent;
});
const shadowTitles = payloads.filter((status) => status.shadow_run).map((status) => {
  app.setShadowRun(status);
  return stubElement('shadow-run-clock').title;
});
// The pill's own text carries the code stamp, so a test can assert what an
// operator reads without parsing the title.
const shadowTexts = payloads.filter((status) => status.shadow_run).map((status) => {
  app.setShadowRun(status);
  return stubElement('shadow-run-clock').textContent;
});
process.stdout.write(JSON.stringify({
  readings, shadow_titles: shadowTitles, shadow_texts: shadowTexts,
}));
