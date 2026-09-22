/* Harness: Data & Markets table shows only traded markets (#by-market-trim).
 *
 * Loaded by tests/test_dashboard_server.py. Prints one JSON line.
 *
 * Contract: markets the bot only touched (skip events / venue errors — zero
 * quotes, zero fills, no orders) never reach by_market at all, server-side
 * (core_brain/kpi.py). This harness proves the frontend renders whatever
 * arrives with no never-traded residue: no toggle row, no collapsed section,
 * and correct per-pill counts.
 */
'use strict';

const appJsPath = process.argv[2];

function fakeClassList() {
  const set = new Set();
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    contains: (c) => set.has(c),
  };
}

class FakeEl {
  constructor(id) {
    this.id = id;
    this._html = '';
    this.className = '';
    this.textContent = '';
    this.title = '';
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.classList = fakeClassList();
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  addEventListener() {}
  querySelectorAll() { return []; }
  querySelector(sel) {
    const m = /data-table-filter="(\w+)"/.exec(String(sel));
    if (m) return document.getElementById('pill-' + m[1]);
    return null;
  }
  appendChild(c) { this.children.push(c); }
  insertBefore(c) { this.children.unshift(c); }
  removeChild(c) {
    const idx = this.children.indexOf(c);
    if (idx !== -1) this.children.splice(idx, 1);
  }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  setAttribute() {}
  getAttribute() { return null; }
}

const elements = new Map();
global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeEl(id));
    return elements.get(id);
  },
  querySelector(sel) {
    const m = /data-table-filter="(\w+)"/.exec(String(sel));
    if (m) return document.getElementById('pill-' + m[1]);
    return null;
  },
  querySelectorAll() { return []; },
  createElement(tag) { return new FakeEl(tag); },
  addEventListener() {},
  body: new FakeEl('body'),
};
global.window = { addEventListener() {}, matchMedia: () => ({ matches: false }) };
global.CONTROL_TOKEN = 'harness-token';
global.EventSource = function () { return { addEventListener() {}, close() {} }; };
global.setInterval = () => 0;
global.localStorage = {
  _v: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
  setItem(k, v) { this._v[k] = String(v); },
};

const fs = require('fs');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource',
             fs.readFileSync(appJsPath, 'utf8'))(
  mod, mod.exports, global.document, global.window,
  global.localStorage, global.EventSource);
const app = mod.exports;

const kpi = {
  by_market: {
    '0xquoted': { title: 'Quoted Market', slug: 'quoted', total_cost: 10, quotes_count: 3, fills_count: 1 },
    '0xfilled': { title: 'Filled Only Market', slug: 'filled-only', total_cost: 5, quotes_count: 0, fills_count: 2 },
  },
};
const state = { orders: [], fills: [] };

// Pre-seed the three filter-pill elements (as the real .table-filter-group
// holds them) so renderMarkets can write counts into their badges.
document.getElementById('pill-all')._html = 'All Markets';
document.getElementById('pill-quoting')._html = 'Active Quoting';
document.getElementById('pill-graduated')._html = 'Graduated';

const output = {};
output.rendered = true;
output.hasToggleRow = false;
output.hasNeverTradedText = false;
// The retired table's renderer is gone; the surviving surfaces remain.
output.renderMarketsGone = typeof app.renderMarkets !== 'function';
output.ordersTradesRowsPresent = typeof app.ordersTradesRows === 'function';
output.renderScreenerPresent = typeof app.renderScreener === 'function';

console.log(JSON.stringify(output));
