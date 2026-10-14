/* Drives dashboard/static/analysis_v2.js against the real /api/kpi payload
 * shape and prints what got painted. Used by tests/test_analysis_v2_binding.py.
 *
 * Hooks are parsed out of the real analysis_v2.html, so a data-bind whose
 * payload path no longer exists is caught here rather than in the browser.
 *
 * Reads a JSON payload from argv[2]; action on argv[3]:
 *   render   full applyAll(kpi) against the stubbed DOM (default)
 *   empty    applyAll({}) -- must not throw and must paint nothing
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const action = process.argv[3] || 'render';
const noop = () => {};

const staticDir = path.resolve(__dirname, '..', '..', 'dashboard', 'static');
const html = fs.readFileSync(path.join(staticDir, 'analysis_v2.html'), 'utf8');

/* --- DOM stub: only the surface applyAll() touches --- */
function makeEl(tag) {
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    textContent: '',
    innerHTML: '',
    className: '',
    style: {},
    dataset: {},
    attrs: {},
    classList: {
      add: (c) => { if (!el.className.split(/\s+/).includes(c)) el.className = (el.className + ' ' + c).trim(); },
      remove: (c) => { el.className = el.className.split(/\s+/).filter((x) => x && x !== c).join(' '); },
      toggle: noop,
      contains: (c) => el.className.split(/\s+/).includes(c),
    },
    getAttribute: (k) => (k in el.attrs ? el.attrs[k] : null),
    setAttribute: (k, v) => { el.attrs[k] = String(v); },
    removeAttribute: (k) => { delete el.attrs[k]; },
    appendChild: noop,
    addEventListener: noop,
    click: noop,
  };
  return el;
}

/* Registry keyed by the selector the code actually queries. */
const bySel = new Map();
function reg(sel) {
  if (!bySel.has(sel)) bySel.set(sel, makeEl());
  return bySel.get(sel);
}
const nodeFor = new Map(); // selector -> [elements]
function qsa(sel) { return nodeFor.get(sel) || []; }
function qs(sel) { const l = qsa(sel); return l.length ? l[0] : null; }
reg('*').querySelector = qs;
reg('*').querySelectorAll = qsa;

/* Parse the real markup: every data-bind / data-band / data-suff element,
 * attached to the selectors analysis_v2.js queries. */
function attr(tag, name) {
  const m = tag.match(new RegExp(name + '="([^"]*)"'));
  return m ? m[1] : null;
}
function classOf(tag) {
  const m = tag.match(/class="([^"]*)"/);
  return m ? m[1] : '';
}

const bindNodes = [];
const bandNodes = [];
const ssrNodes = [];
const chartAreas = [];

html.replace(/<(article|div|span|b|i|td|tr|section)\b[^>]*>/g, (tag, kind) => {
  if (attr(tag, 'data-bind')) {
    const e = makeEl(kind);
    [['data-bind', attr(tag, 'data-bind')], ['data-fmt', attr(tag, 'data-fmt')],
     ['data-sign', attr(tag, 'data-sign')], ['id', attr(tag, 'id')]].forEach(([k, v]) => {
      if (v !== null) e.attrs[k] = v;
    });
    e.className = classOf(tag);
    bindNodes.push(e);
  }
  if (attr(tag, 'data-band')) {
    const e = makeEl(kind);
    e.attrs['data-band'] = attr(tag, 'data-band');
    e.className = classOf(tag);
    bandNodes.push(e);
  }
  if (attr(tag, 'data-suff')) {
    const e = makeEl('tr');
    e.attrs['data-suff'] = attr(tag, 'data-suff');
    ['r95', 'r98', 'r99', 'rem95', 'rem98', 'rem99', 'p95', 'p98', 'p99'].forEach((k) => {
      const v = attr(tag, 'data-' + k);
      if (v !== null) e.dataset[k] = v;
    });
    // The cells paintSufficiency() rewrites in place.
    e._cells = { '.cur': makeEl('td'), '.req': makeEl('td') };
    e.querySelector = (s) => e._cells[s] || null;
    e._cellsOut = e._cells;
    ssrNodes.push(e);
  }
  if (attr(tag, 'id') && /^ch[1-5]$/.test(attr(tag, 'id'))) {
    const card = makeEl('article');
    card.attrs.id = attr(tag, 'id');
    const kids = {
      '.chart-area': makeEl('div'),
      '.chart-badge': makeEl('span'),
      '.chart-stats': makeEl('div'),
      '.chart-foot': makeEl('div'),
    };
    card._children = kids;
    card.querySelector = (s) => kids[s] || null;
    chartAreas.push(card);
  }
  return tag;
});

/* The CI scale's ticks/labels carry data-ci, positioned by paintCiScale(). */
const ciScaleEl = makeEl('div');
ciScaleEl.attrs.id = 'ciScale';
const ciTicks = {}; // data-ci -> [tick els]
const ciLabs = {};  // data-ci -> [label els]
html.replace(/<(div|span)\b[^>]*data-ci="([^"]*)"[^>]*>/g, (tag, kind, key) => {
  const e = makeEl(kind);
  e.attrs['data-ci'] = key;
  e.className = classOf(tag);
  const bucket = /\btick\b/.test(e.className) ? ciTicks : ciLabs;
  (bucket[key] = bucket[key] || []).push(e);
  return tag;
});
Object.keys(ciTicks).forEach((k) => nodeFor.set(`.tick[data-ci="${k}"]`, ciTicks[k]));
Object.keys(ciLabs).forEach((k) => nodeFor.set(`.lab[data-ci="${k}"]`, ciLabs[k]));
reg('id:ciScale').querySelectorAll = qsa; // paintCiScale queries descendants

nodeFor.set('[data-bind]', bindNodes);
nodeFor.set('[data-band]', bandNodes);
nodeFor.set('tr.ssr', ssrNodes);
nodeFor.set('.conf-toggle .pill.active', makeEl('span'));

global.document = {
  getElementById: (id) => {
    const f = bindNodes.concat(bandNodes).filter((e) => e.attrs.id === id)[0];
    return f || reg('id:' + id);
  },
  querySelector: (sel) => {
    const m = /^#(ch[1-5]) (.+)$/.exec(sel);
    if (m) {
      const card = chartAreas.filter((c) => c.attrs.id === m[1])[0];
      if (card) return card.querySelector(m[2]);
    }
    return qs(sel);
  },
  querySelectorAll: qsa,
  createElement: makeEl,
  addEventListener: noop,
  body: { appendChild: noop, classList: { add: noop, remove: noop } },
  documentElement: { appendChild: noop },
};

const source = fs.readFileSync(path.join(staticDir, 'analysis_v2.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'fetch', source)(
  mod, mod.exports, global.document, { addEventListener: noop },
  () => Promise.reject(new Error('fetch is not exercised by the harness')));
const app = mod.exports;

const out = {
  errors: [],
  hookCount: bindNodes.length,
  painted: {},
  bands: {},
  charts: [],
  sufficiency: [],
};

try {
  const kpi = action === 'empty' ? {} : (input.kpi || {});
  app.applyAll(kpi);
  bindNodes.forEach((e) => {
    out.painted[e.attrs['data-bind']] = e.innerHTML || e.textContent;
  });
  bandNodes.forEach((e) => {
    out.bands[e.attrs['data-band']] = { cls: e.className, text: e.textContent };
  });
  /* Read the painted results AFTER applyAll, never before. */
  const ciScaleNode = document.getElementById('ciScale');
  out.ciScale = {
    display: ciScaleNode ? ciScaleNode.style.display : null,
    ticks: {},
    labels: {},
  };
  Object.keys(ciTicks).forEach((k) => {
    out.ciScale.ticks[k] = ciTicks[k].map((t) => t.style.left);
  });
  Object.keys(ciLabs).forEach((k) => {
    out.ciScale.labels[k] = ciLabs[k].map((l) => ({ left: l.style.left, text: l.textContent }));
  });
  chartAreas.forEach((c) => {
    out.charts.push({
      id: c.attrs.id,
      badge: c._children['.chart-badge'].innerHTML,
      stats: c._children['.chart-stats'].innerHTML,
      svg: c._children['.chart-area'].innerHTML,
      foot: c._children['.chart-foot'].innerHTML,
    });
  });
  ssrNodes.forEach((r) => {
    out.sufficiency.push({
      key: r.attrs['data-suff'],
      dataset: r.dataset,
      cur: r._cellsOut['.cur'].textContent,
      req: r._cellsOut['.req'].textContent,
    });
  });
  out.gates = JSON.parse(JSON.stringify(app.gateVerdicts(kpi)));
} catch (e) {
  out.errors.push(`${action}: ${(e && e.stack) || e}`);
}

process.stdout.write(JSON.stringify(out));
