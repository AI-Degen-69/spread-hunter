/* Drives dashboard/static/command-palette.js against a stub DOM — the offline
 * pattern of tests/js/prototype_nav_harness.cjs. Reads one JSON scenario on
 * argv[2]: {fixture, actions}; prints one JSON observation line.
 *
 * fixture keys (all optional):
 *   paletteMarkup  false → no palette elements (init must stay inert)
 *   withPrototype  evaluate the real prototype.js + initPrototype (nav wiring)
 *   pages          [{page, label}] nav buttons + #page-* sections (default: the
 *                  six live rail pages)
 *   externalLink   add a .proto-nav-btn <a> without data-page (must be excluded)
 *   syncDisabled   #btn-sync disabled
 *   resetModalShown  #reset-modal carries .show
 *   opener         id of an element focused before the actions ('opener-btn')
 *   openerRemoved  that element is no longer connected
 *   stored         localStorage 'sh-proto-page' pre-set (start page)
 *   snapshots      {kpi, state} injected as lastState/lastKpi (appended to the
 *                  evaluated source — they are module-local in the real page)
 *   otSearch       add #ot-search-input with an input-event spy
 *
 * actions:
 *   {type:'key', key, ctrl?, meta?, shift?, target?}  target: friendly name
 *   {type:'click', target}   friendly name (bubbles like a real click)
 *   {type:'input', value}    set the palette search value + fire input
 *   {type:'snapshot', state?, kpi?}  swap the injected snapshots mid-run
 *   {type:'observe'}         push an observation snapshot
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const fixture = input.fixture || {};
const actions = input.actions || [];

const DEFAULT_PAGES = [
  { page: 'home', label: 'Dashboard' },
  { page: 'data-markets', label: 'Data & Markets' },
  { page: 'strategy', label: 'Strategy' },
  { page: 'trades', label: 'Trades & Positions' },
  { page: 'reports', label: 'Reports & Analytics' },
  { page: 'analysis', label: 'Data Analysis' },
];

/* ── element stub ── */
function makeEl(id, extra) {
  const attrs = {};
  const listeners = {};
  const classes = new Set();
  const el = {
    id: id || null,
    tagName: 'DIV',
    hidden: false,
    disabled: false,
    isConnected: true,
    isContentEditable: false,
    textContent: '',
    value: '',
    dataset: {},
    style: {},
    children: [],
    parentNode: null,
    classList: {
      add: (n) => classes.add(n),
      remove: (n) => classes.delete(n),
      contains: (n) => classes.has(n),
      toggle: (n, on) => { if (on) classes.add(n); else classes.delete(n); },
    },
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return (k in attrs) ? attrs[k] : null; },
    removeAttribute(k) { delete attrs[k]; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    dispatchEvent(ev) {
      if (ev.target === undefined || ev.target === null) ev.target = el;
      (listeners[ev.type] || []).slice().forEach(fn => fn(ev));
      return !ev.defaultPrevented;
    },
    appendChild(child) { el.children.push(child); child.parentNode = el; return child; },
    contains(target) {
      if (target === el) return true;
      return el.children.some(c => c.contains && c.contains(target));
    },
    focus() { doc.activeElement = el; fireFocusin(el); },
    select() { el.selected = true; },
    scrollIntoView() { el.scrolled = true; },
    click() { clickEl(el); },
    listeners,
    attrs,
  };
  Object.assign(el, extra || {});
  return el;
}

/* ── document / window ── */
let doc = null;
const els = {};
const sections = [];
const navButtons = [];
let windowListeners = {};
let docListeners = {};
let observations = [];
let kanbanKeys = [];
let resetEscapeKeys = [];
let syncClicks = 0;
let resetClicks = 0;
let fetchCalls = 0;
let otEvents = 0;

function fireFocusin(target) {
  (docListeners.focusin || []).slice().forEach(fn => fn({ type: 'focusin', target }));
}

function clickEl(el) {
  const ev = {
    type: 'click', target: el, defaultPrevented: false, propagationStopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
  };
  let node = el;
  const chain = [];
  while (node) { chain.push(node); node = node.parentNode; }
  chain.forEach(n => {
    if (!ev.propagationStopped) (n.listeners.click || []).slice().forEach(fn => fn(ev));
  });
}

function byName(name) {
  if (!name || name === 'body') return doc.body;
  return els[name] || null;
}

function dispatchKey(a) {
  const ev = {
    type: 'keydown', key: a.key,
    ctrlKey: !!a.ctrl, metaKey: !!a.meta, shiftKey: !!a.shift, altKey: false,
    target: byName(a.target) || doc.activeElement || doc.body,
    defaultPrevented: false, propagationStopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
  };
  // capture phase first (the palette's window listener), then document bubble
  // (the background spies stand in for app.js's document-level handlers).
  (windowListeners.keydown || []).filter(l => l.capture).forEach(l => l.fn(ev));
  if (!ev.propagationStopped) {
    (docListeners.keydown || []).forEach(fn => fn(ev));
  }
  lastKey = ev;
  return ev;
}

let lastKey = null;

function buildFixture() {
  // pages: nav buttons + #page-* sections
  const pages = fixture.pages || DEFAULT_PAGES;
  pages.forEach(p => {
    const section = makeEl('page-' + p.page);
    section.hidden = p.page !== 'home';
    sections.push(section);
    els['page-' + p.page] = section;
    const btn = makeEl('nav-' + p.page, {
      tagName: 'BUTTON',
      textContent: p.label,
      dataset: { page: p.page },
    });
    navButtons.push(btn);
  });
  if (fixture.externalLink) {
    navButtons.push(makeEl('nav-polymarket', {
      tagName: 'A',
      textContent: 'Polymarket',
      dataset: {},
    }));
  }

  const opener = fixture.opener ? makeEl(fixture.opener, { tagName: 'BUTTON' }) : null;
  if (opener) {
    els[fixture.opener] = opener;
    if (fixture.openerRemoved) opener.isConnected = false;
    doc.activeElement = opener;  // openPalette captures document.activeElement
  }

  if (fixture.syncDisabled === undefined ? false : fixture.syncDisabled) {
    els['btn-sync'] = makeEl('btn-sync', { tagName: 'BUTTON', textContent: 'SYNC VENUE', disabled: true });
  } else {
    els['btn-sync'] = makeEl('btn-sync', { tagName: 'BUTTON', textContent: 'SYNC VENUE' });
  }
  els['btn-sync'].addEventListener('click', () => { syncClicks += 1; });

  els['btn-reset'] = makeEl('btn-reset', { tagName: 'BUTTON', textContent: 'RESET' });
  els['btn-reset'].addEventListener('click', () => { resetClicks += 1; });

  const resetModal = makeEl('reset-modal');
  if (fixture.resetModalShown) resetModal.classList.add('show');
  els['reset-modal'] = resetModal;

  if (fixture.otSearch) {
    const ot = makeEl('ot-search-input', { tagName: 'INPUT' });
    ot.addEventListener('input', () => { otEvents += 1; });
    els['ot-search-input'] = ot;
  }

  const search = makeEl('palette-search', { tagName: 'INPUT', value: '' });
  els['palette-search'] = search;

  const textarea = makeEl('palette-textarea', { tagName: 'TEXTAREA' });
  els['palette-textarea'] = textarea;
  const contentEditable = makeEl('palette-editable', { isContentEditable: true });
  els['palette-editable'] = contentEditable;

  if (fixture.paletteMarkup !== false) {
    const overlay = makeEl('command-palette');
    overlay.hidden = true;
    els['command-palette'] = overlay;
    const modal = makeEl('palette-modal');
    overlay.appendChild(modal);
    modal.appendChild(search);
    const list = makeEl('palette-list');
    modal.appendChild(list);
    els['palette-list'] = list;
    els['palette-inner'] = modal;
  }
}

function buildDoc() {
  const store = {};
  if (fixture.stored) store['sh-proto-page'] = fixture.stored;
  const body = makeEl('body');
  const win = {
    localStorage: {
      getItem: (k) => (k in store && store[k] !== undefined) ? store[k] : null,
      setItem: (k, v) => { store[k] = String(v); },
    },
    location: { pathname: '/prototype' },
    addEventListener(type, fn, capture) {
      (windowListeners[type] = windowListeners[type] || []).push({ fn, capture: !!capture });
    },
  };
  doc = {
    body,
    activeElement: null,
    getElementById: (id) => els[id] || null,
    querySelectorAll: (sel) => {
      if (sel === '.proto-nav-btn') return navButtons;
      if (sel === '.proto-nav-btn[data-page]') return navButtons.filter(b => b.dataset.page);
      return [];
    },
    querySelector: () => null,
    createElement: (tag) => makeEl(null, { tagName: String(tag).toUpperCase() }),
    addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
  };
  // background spies: app.js's document-level handlers
  doc.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') kanbanKeys.push(ev.key);
    if (ev.key === 'Escape') resetEscapeKeys.push(ev.key);
  });
  global.fetch = function () { fetchCalls += 1; return Promise.resolve({ ok: true }); };
  global.window = win;
  global.document = doc;
  return win;
}

function snapshot() {
  const overlay = els['command-palette'] || null;
  const search = els['palette-search'] || null;
  const list = els['palette-list'] || null;
  const rows = list ? list.children.filter(c => c.attrs.role === 'option').map(c => ({
    label: c.dataset.label || c.textContent,
    sub: c.dataset.sub || null,
    id: c.id,
    entryId: c.dataset.entryId || null,
    selected: c.attrs['aria-selected'] === 'true',
    disabled: c.attrs['aria-disabled'] === 'true',
  })) : [];
  const current = navButtons.find(b => b.getAttribute('aria-current') === 'page');
  return {
    threw: null,
    open: !!overlay && !overlay.hidden,
    show: !!overlay && overlay.classList.contains('show'),
    active: doc.activeElement ? (doc.activeElement.id || (doc.activeElement === doc.body ? 'body' : 'unknown')) : null,
    inputValue: search ? search.value : null,
    keyPrevented: lastKey ? lastKey.defaultPrevented : null,
    rows,
    activeDescendant: search ? search.getAttribute('aria-activedescendant') : null,
    expanded: search ? search.getAttribute('aria-expanded') : null,
    stored: global.window.localStorage.getItem('sh-proto-page'),
    currentPage: current ? current.dataset.page : null,
    visiblePages: sections.filter(s => !s.hidden).map(s => s.id),
    syncClicks, resetClicks, fetchCalls,
    kanbanKeys, resetEscapeKeys,
    otValue: els['ot-search-input'] ? els['ot-search-input'].value : null,
    otEvents,
    otFocused: els['ot-search-input'] ? doc.activeElement === els['ot-search-input'] : null,
  };
}

function observe() {
  // snapshot() carries no self-reference, so pushing it into observations
  // stays JSON-serializable.
  return Object.assign(snapshot(), { observations });
}

function run() {
  const win = buildDoc();
  buildFixture();
  const repo = path.resolve(__dirname, '..', '..');
  let paletteSrc = '';
  try {
    paletteSrc = fs.readFileSync(path.join(repo, 'dashboard', 'static', 'command-palette.js'), 'utf8');
  } catch (err) {
    process.stdout.write(JSON.stringify({ threw: 'command-palette.js not readable: ' + err.message }));
    return;
  }
  // Snapshots are module-local in the real page (app.js top-level `let`); the
  // harness appends them to the evaluated source so the closure sees them.
  // Merge (not replace) so a partial poll keeps the other half of the state.
  if (fixture.snapshots) {
    paletteSrc += '\n;var lastState = ' + JSON.stringify(fixture.snapshots.state || null) + ';'
      + 'var lastKpi = ' + JSON.stringify(fixture.snapshots.kpi || null) + ';'
      + 'globalThis.__setSnapshot = function (state, kpi) {'
      + ' lastState = Object.assign({}, lastState || {}, state || {});'
      + ' lastKpi = Object.assign({}, lastKpi || {}, kpi || {}); };';
  }
  let protoMod = { exports: {} };
  if (fixture.withPrototype) {
    const protoSrc = fs.readFileSync(path.join(repo, 'dashboard', 'static', 'prototype.js'), 'utf8');
    new Function('module', 'exports', 'document', 'window', protoSrc)(protoMod, protoMod.exports, doc, win);
    protoMod.exports.initPrototype(doc);
  }
  let palette = null;
  try {
    const mod = { exports: {} };
    new Function('module', 'exports', 'document', 'window', paletteSrc)(mod, mod.exports, doc, win);
    palette = mod.exports;
    palette.initPalette(doc);
    (actions || []).forEach(a => {
      if (a.type === 'key') dispatchKey(a);
      else if (a.type === 'click') { const el = byName(a.target); if (el) clickEl(el); }
      else if (a.type === 'input') {
        const search = els['palette-search'];
        if (search) { search.value = a.value; search.dispatchEvent({ type: 'input' }); }
      } else if (a.type === 'snapshot') {
        if (globalThis.__setSnapshot) {
          globalThis.__setSnapshot(
            'state' in a ? a.state : (fixture.snapshots && fixture.snapshots.state),
            'kpi' in a ? a.kpi : (fixture.snapshots && fixture.snapshots.kpi));
        }
      } else if (a.type === 'observe') observations.push(snapshot());
    });
    process.stdout.write(JSON.stringify(observe()));
  } catch (err) {
    process.stdout.write(JSON.stringify(Object.assign(observe(), { threw: String(err && err.message || err) })));
  }
}

run();
