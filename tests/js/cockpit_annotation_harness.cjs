/* Runs the cockpit's generated inline script against a stub DOM and prints the
 * HTML its annotation cell (and one full leaderboard row) produced per case.
 * Used by tests/test_tournament_cockpit_arms.py.
 *
 * Reads JSON on stdin: {html, cells, rows}
 *   html  - the page text returned by generate_cockpit_html()
 *   cells - {case name: arm}  -> annotationCellHtml(arm)
 *   rows  - {case name: arm | null} -> renderAll() with that one arm (null = [])
 * Prints JSON on stdout: {cells, rows, emptyArms, errors}
 *   emptyArms - renderAll() driven with arms: [] -> {shown, text}: the page must
 *               put the empty-state placeholder on screen and not throw (#873).
 *
 * The page's own script is the unit under test, so nothing is re-implemented
 * here: the real script is extracted from the real page and executed.
 */
const fs = require('fs');
const vm = require('vm');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const noop = () => {};

/* A stub element that remembers what was written to it, so assertions are on
 * rendered markup rather than on a call having been made. */
function stubElement(id) {
  const el = {
    id,
    textContent: '',
    innerHTML: '',
    innerText: '',
    className: '',
    hidden: false,
    style: {},
    dataset: {},
    children: [],
    classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    addEventListener: noop,
    removeEventListener: noop,
    appendChild(child) { el.children.push(child); return child; },
    setAttribute: noop,
    getAttribute: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    contains: () => true,
    getContext: () => ({}),
  };
  return el;
}

const elements = {};
function element(id) {
  if (!elements[id]) elements[id] = stubElement(id);
  return elements[id];
}

const documentStub = {
  getElementById: (id) => element(id),
  createElement: (tag) => stubElement(`<${tag}>`),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: noop,
  body: stubElement('body'),
  contains: () => true,
};

class ChartStub {
  constructor() {}
  destroy() {}
  update() {}
}

const sandbox = {
  document: documentStub,
  window: { addEventListener: noop, location: { href: '' }, document: documentStub },
  localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
  Chart: ChartStub,
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  setInterval: () => 0,
  setTimeout: () => 0,
  clearInterval: noop,
  console: { log: noop, warn: noop, error: noop },
};

const errors = [];

/* Only the page's own bare <script> blocks; the Chart.js CDN tag has a src. */
const inlineScripts = [];
const SCRIPT_RE = /<script>([\s\S]*?)<\/script>/g;
let match;
while ((match = SCRIPT_RE.exec(String(input.html || ''))) !== null) {
  inlineScripts.push(match[1]);
}

/* The generated script keeps `state` in a lexical binding, so the harness has
 * to reach it from inside the same script text. */
const epilogue = `
;globalThis.__api = {
  annotationCellHtml: typeof annotationCellHtml === 'function' ? annotationCellHtml : null,
  renderAll: typeof renderAll === 'function' ? renderAll : null,
  setArms: function (arms) { state = Object.assign({}, state, { arms: arms }); },
};
`;

function boot() {
  if (inlineScripts.length === 0) {
    errors.push('page: no inline <script> block found');
    return null;
  }
  const source = inlineScripts.join('\n') + epilogue;
  vm.createContext(sandbox);
  try {
    vm.runInContext(source, sandbox, { filename: 'cockpit-inline.js' });
  } catch (e) {
    errors.push(`page: script threw on load: ${e && e.message}`);
    return null;
  }
  const api = sandbox.__api || {};
  if (!api.annotationCellHtml) errors.push('page: annotationCellHtml is not defined');
  if (!api.renderAll) errors.push('page: renderAll is not defined');
  return api;
}

const api = boot();

const cells = {};
for (const [name, arm] of Object.entries(input.cells || {})) {
  if (!api || !api.annotationCellHtml) {
    cells[name] = '';
    continue;
  }
  try {
    cells[name] = String(api.annotationCellHtml(arm));
  } catch (e) {
    cells[name] = '';
    errors.push(`cells.${name}: ${e && e.message}`);
  }
}

const rows = {};
for (const [name, arm] of Object.entries(input.rows || {})) {
  if (!api || !api.renderAll) {
    rows[name] = '';
    continue;
  }
  const tbody = element('leaderboard-tbody');
  tbody.children = [];
  tbody.innerHTML = '';
  try {
    api.setArms(arm === null ? [] : [arm]);
    api.renderAll();
    rows[name] = tbody.children.map((child) => String(child.innerHTML)).join('');
  } catch (e) {
    rows[name] = '';
    errors.push(`rows.${name}: ${e && e.message}`);
  }
}

/* Issue #873: zero arms is a valid poll (no tournament yet / arms file
 * missing). The page has to say so rather than die on the radar's ctrl.label. */
const emptyArms = { shown: false, text: '' };
if (api && api.renderAll) {
  const tbody = element('leaderboard-tbody');
  tbody.children = [];
  tbody.innerHTML = '';
  try {
    api.setArms([]);
    api.renderAll();
    const el = element('arms-empty-state');
    emptyArms.shown = el.style.display === 'block';
    emptyArms.text = String(el.textContent || el.innerText || '');
  } catch (e) {
    errors.push(`emptyArms: ${e && e.message}`);
  }
}

process.stdout.write(JSON.stringify({ cells, rows, emptyArms, errors }));
