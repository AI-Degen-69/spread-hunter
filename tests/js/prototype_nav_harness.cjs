/* Drives the sidebar prototype's navigation against a stub DOM and prints
 * which page is visible after a sequence of clicks. Used by
 * tests/test_sidebar_prototype.py.
 *
 * Reads {clicks: [pageName...], stored: <page|null>, storedPin: <string|null>,
 * clickPin: <int>} as JSON on argv[2].
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const noop = () => {};

const PAGES = ['home', 'data-markets', 'strategy', 'trades', 'analysis'];

const sections = {};
const buttons = [];
for (const page of PAGES) {
  sections['page-' + page] = { id: 'page-' + page, hidden: page !== 'home' };
  const attrs = {};
  buttons.push({
    dataset: { page },
    attrs,
    handlers: [],
    addEventListener(_evt, fn) { this.handlers.push(fn); },
    setAttribute(name, value) { attrs[name] = value; },
    removeAttribute(name) { delete attrs[name]; },
    getAttribute(name) { return name in attrs ? attrs[name] : null; },
    click() { this.handlers.forEach(fn => fn()); },
  });
}

function stubButton(id) {
  const attrs = {};
  return {
    id,
    handlers: [],
    addEventListener(_evt, fn) { this.handlers.push(fn); },
    setAttribute(name, value) { attrs[name] = value; },
    getAttribute(name) { return name in attrs ? attrs[name] : null; },
    click() { this.handlers.forEach(fn => fn()); },
  };
}

const toggle = stubButton('proto-nav-toggle');
const pin = stubButton('proto-rail-pin');
const stubs = { 'proto-nav-toggle': toggle, 'proto-rail-pin': pin };

const bodyClasses = new Set();
const doc = {
  getElementById: (id) => (stubs[id] || sections[id] || null),
  querySelectorAll: (sel) => (sel === '.proto-nav-btn' ? buttons : []),
  body: {
    classList: {
      toggle(name, on) { if (on) bodyClasses.add(name); else bodyClasses.delete(name); },
      contains: (name) => bodyClasses.has(name),
    },
  },
};

/* One key-value store, not one slot: the rail persists its pinned state
 * beside the chosen page, and a shared slot would let one overwrite the
 * other and hide exactly the bug these tests exist to catch. */
const store = {
  'sh-proto-page': input.stored === undefined ? null : input.stored,
  'sh-proto-rail-pinned': input.storedPin === undefined ? null : input.storedPin,
};
global.document = doc;
global.window = {
  localStorage: {
    getItem: (key) => (key in store && store[key] !== undefined ? store[key] : null),
    setItem: (key, value) => { store[key] = value; },
  },
};

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'prototype.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', source)(
  mod, mod.exports, doc, global.window);
const proto = mod.exports;

proto.initPrototype(doc);
for (const page of (input.clicks || [])) {
  const button = buttons.find(b => b.dataset.page === page);
  if (!button) throw new Error('no nav button for ' + page);
  button.click();
}
if (input.toggleNav) toggle.click();
for (let i = 0; i < (input.clickPin || 0); i += 1) pin.click();

process.stdout.write(JSON.stringify({
  visible: PAGES.filter(p => sections['page-' + p].hidden === false),
  current: buttons.filter(b => b.getAttribute('aria-current') === 'page').map(b => b.dataset.page),
  stored: store['sh-proto-page'],
  stored_pin: store['sh-proto-rail-pinned'],
  nav_open: bodyClasses.has('proto-nav-open'),
  toggle_expanded: toggle.getAttribute('aria-expanded'),
  rail_pinned: bodyClasses.has('proto-rail-pinned'),
  pin_pressed: pin.getAttribute('aria-pressed'),
}));
