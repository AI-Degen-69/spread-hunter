/* Drives the live-marks positions surface (#427) against a stub DOM and
 * prints the rendered positions HTML plus the mark-map state. Used by
 * tests/test_positions_live_marks.py.
 *
 * Reads argv[2] as JSON:
 *   { kpi, state, nowMs, emit:[markPayload...], apply:[{payload,nowMs}...],
 *     clear: bool }
 * `emit` payloads travel through the real SSE `mark` listener connectSSE()
 * registered at load (proving the wiring); `apply` calls applyLiveMarks()
 * directly for edge cases. Date.now is pinned to nowMs so mark ages are
 * deterministic. `clear` runs clearLiveMarks() last (the store-switch path).
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const FIXED_NOW = Number(input.nowMs) || Date.now();

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

// Recording EventSource: captures the `mark` listener connectSSE registers.
const listeners = {};
const instances = [];
global.EventSource = function EventSource() {
  const inst = {
    close: noop,
    onmessage: null,
    onerror: null,
    readyState: 1,
    addEventListener: (type, fn) => {
      (listeners[type] = listeners[type] || []).push(fn);
    },
  };
  instances.push(inst);
  return inst;
};

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;

// Pin the clock before any mark flows: the SSE handler stamps Date.now().
Date.now = () => FIXED_NOW;

const emitted = (listeners.mark || []).length > 0;
for (const payload of (input.emit || [])) {
  for (const fn of (listeners.mark || [])) fn({ data: JSON.stringify(payload) });
}
const applied = [];
for (const step of (input.apply || [])) {
  applied.push(app.applyLiveMarks(step.payload, Number(step.nowMs) || FIXED_NOW));
}
if (input.clear) app.clearLiveMarks();

const html = app.ordersTradesRows('positions', input.kpi || {}, input.state || {}, null);

process.stdout.write(JSON.stringify({
  html,
  markListener: emitted,
  liveSize: app.liveMarks.size,
  live: [...app.liveMarks.entries()].map(([tok, e]) => ({ token: tok, mid: e.mid, seq: e.seq })),
  applied,
}));
