/* Harness: toast notifications for operator actions (Issue #478).
 *
 * Loaded by tests/test_dashboard_toasts.py. `app.js` is evaluated with stub
 * globals so its page bootstrap stays asleep and only `showToast` plus the
 * Sync / Master Toggle / Reset click handlers run.
 *
 * Prints one JSON line with a verdict per scenario. Timers are fake: the
 * harness captures setTimeout/clearTimeout before loading app.js and fires
 * them explicitly, so auto-dismiss is asserted without waiting seconds.
 * Focus is tracked: any element.focus() call increments __focusCalls.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const APP_JS = path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js');

/* ── Fake timers (installed before app.js loads) ── */
const realSetTimeout = setTimeout;
let timerSeq = 1;
let pendingTimers = new Map(); // id -> { fn, ms }
global.setTimeout = (fn, ms, ...args) => {
  const id = timerSeq++;
  pendingTimers.set(id, { fn: () => fn(...args), ms: Number(ms) || 0 });
  return id;
};
global.clearTimeout = (id) => { pendingTimers.delete(id); };
global.setInterval = () => 0;
global.clearInterval = () => {};

function runTimers(maxMs) {
  const due = [...pendingTimers.entries()]
    .filter(([, t]) => t.ms <= maxMs)
    .sort((a, b) => a[1].ms - b[1].ms);
  for (const [id, t] of due) {
    pendingTimers.delete(id);
    t.fn();
  }
  return due.length;
}

/* ── Focus tracking ── */
let focusCalls = 0;

/* ── Minimal DOM ── */
class MiniEl {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this.parentNode = null;
    this.className = '';
    this.textContent = '';
    this._html = '';
    this.value = '';
    this.type = '';
    this.title = '';
    this.style = {};
    this.dataset = {};
    this.disabled = false;
    this.hidden = false;
    this.attrs = {};
    this._listeners = {};
    const self = this;
    this.classList = {
      add: (c) => {
        const s = new Set(self.className.split(/\s+/).filter(Boolean));
        s.add(c);
        self.className = [...s].join(' ');
      },
      remove: (c) => {
        self.className = self.className.split(/\s+/).filter((x) => x && x !== c).join(' ');
      },
      contains: (c) => self.className.split(/\s+/).includes(c),
      toggle: (c, force) => {
        const has = self.className.split(/\s+/).includes(c);
        const on = force === undefined ? !has : !!force;
        if (on) self.classList.add(c);
        else self.classList.remove(c);
        return on;
      },
    };
  }
  get childNodes() { return this.children; }
  get firstChild() { return this.children[0] || null; }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  setAttribute(k, v) { this.attrs[String(k)] = String(v); }
  getAttribute(k) {
    const kk = String(k);
    return Object.prototype.hasOwnProperty.call(this.attrs, kk) ? this.attrs[kk] : null;
  }
  removeAttribute(k) { delete this.attrs[String(k)]; }
  addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); }
  removeEventListener() {}
  appendChild(c) {
    if (!c) return c;
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i === -1) throw new Error('NotFoundError: child not found');
    this.children.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  focus() { focusCalls++; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  async click(evt) {
    const fns = this._listeners.click || [];
    for (const fn of fns) {
      await fn(evt || { stopPropagation: () => {}, preventDefault: () => {}, target: this });
    }
  }
}

const elements = new Map();
function el(id) {
  id = String(id);
  if (!elements.has(id)) elements.set(id, new MiniEl('div', id));
  return elements.get(id);
}

const docListeners = {};
global.document = {
  getElementById: (id) => el(id),
  createElement: (tag) => new MiniEl(tag, ''),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); },
  removeEventListener: () => {},
  body: new MiniEl('body', 'body'),
};
global.window = { addEventListener: () => {}, matchMedia: () => ({ matches: false }) };
global.CONTROL_TOKEN = 'harness-token';
global.EventSource = function EventSource() {
  return { addEventListener: () => {}, close: () => {} };
};
global.alert = () => {};
global.prompt = () => null;
global.localStorage = {
  _v: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
  setItem(k, v) { this._v[k] = String(v); },
  removeItem(k) { delete this._v[k]; },
};

/* Fetch: GETs read nothing (pollStatus then renders nothing); POSTs are
 * scenario-programmable via __postImpl. */
const fetchLog = [];
let postImpl = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '' });
global.fetch = async (url, opts) => {
  const method = (opts && opts.method) || 'GET';
  fetchLog.push({ url: String(url), method });
  if (method === 'POST') return postImpl(String(url), opts || {});
  return { ok: false, status: 500, json: async () => { throw new Error('no json'); }, text: async () => '' };
};

/* Load app.js as a module (bootstrap guarded by `typeof module`). */
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource',
  fs.readFileSync(APP_JS, 'utf8'))(
  mod, mod.exports, global.document, global.window,
  global.localStorage, global.EventSource);
const app = mod.exports;

/* ── Snapshot helpers ── */
function walk(node, fn) {
  fn(node);
  for (const c of node.children || []) walk(c, fn);
}

function snapToast(t) {
  let text = '';
  let hasClose = false;
  let closeLabel = null;
  let tags = [];
  let innerHtmlUsed = false;
  walk(t, (n) => {
    tags.push(n.tagName);
    if (n._html) innerHtmlUsed = true;
    const cls = (n.className || '').split(/\s+/);
    if (cls.includes('toast-text')) text += n.textContent;
    if (cls.includes('toast-close')) {
      hasClose = true;
      closeLabel = n.getAttribute('aria-label');
    }
  });
  return { cls: t.className, role: t.getAttribute('role'), text, hasClose, closeLabel, tags, innerHtmlUsed };
}

function snapToasts() {
  return el('toast-container').children.map(snapToast);
}

function findCloseBtn(t) {
  let found = null;
  walk(t, (n) => {
    if (!found && (n.className || '').split(/\s+/).includes('toast-close')) found = n;
  });
  return found;
}

async function flush(n) {
  for (let i = 0; i < (n || 12); i++) {
    await new Promise((r) => realSetTimeout(r, 0));
  }
}

function resetHarness() {
  const c = el('toast-container');
  while (c.children.length) c.removeChild(c.children[0]);
  pendingTimers = new Map();
  fetchLog.length = 0;
  app.clearPendingShadowStart();
  const sync = el('btn-sync');
  sync.disabled = false;
  sync.textContent = 'SYNC';
  sync.classList.remove('syncing');
  const modal = el('reset-modal');
  modal.classList.remove('show');
  const input = el('reset-input');
  input.value = '';
  const confirm = el('reset-modal-confirm');
  confirm.disabled = false;
  confirm.textContent = 'Confirm Reset';
  const prog = el('reset-progress');
  prog.style.display = 'none';
  prog.textContent = '';
}

function runningStatus() {
  return {
    bot_state: 'RUNNING', db_mode: 'LIVE', db_is_production: true, db_path: 'data/orders.db',
    services: {
      filter: { running: true }, query: { running: true },
      decide: { running: true }, dash: { running: true },
    },
  };
}
function stoppedStatus() {
  return {
    bot_state: 'STOPPED', db_mode: 'LIVE', db_is_production: true, db_path: 'data/orders.db',
    services: {
      filter: { running: false }, query: { running: false },
      decide: { running: false }, dash: { running: true },
    },
  };
}
function shadowStatus(running) {
  return {
    bot_state: running ? 'RUNNING' : 'STOPPED', db_mode: 'SHADOW',
    db_is_production: false, db_path: 'data/shadow.db',
    services: {
      filter: { running: false }, query: { running: false },
      decide: { running: false }, dash: { running: true },
    },
    shadow_run: running
      ? { running: true, ended: false, run_id: 'harness-shadow', minutes: 10, db_path: 'data/shadow.db' }
      : { running: false, ended: true, run_id: 'harness-shadow', minutes: 10, db_path: 'data/shadow.db' },
  };
}
function postUrls() {
  return fetchLog.filter((f) => f.method === 'POST').map((f) => f.url);
}

(async () => {
  const out = {};

  /* 1. Roles & tones */
  resetHarness();
  app.showToast('ok message', { tone: 'ok', timeout: 0 });
  app.showToast('warn message', { tone: 'warn', timeout: 0 });
  app.showToast('error message', { tone: 'error', timeout: 0 });
  app.showToast('neutral message', { timeout: 0 });
  out.roles = snapToasts();

  /* 2. Stack cap: 5th toast evicts the oldest, 4 stay visible */
  resetHarness();
  for (let i = 1; i <= 5; i++) app.showToast('msg-' + i, { tone: 'neutral', timeout: 0 });
  out.cap = snapToasts();

  /* 3. Close button dismisses; handle.dismiss() is idempotent */
  resetHarness();
  const h = app.showToast('dismiss me', { tone: 'ok', timeout: 0 });
  const before = snapToasts().length;
  await findCloseBtn(el('toast-container').children[0]).click();
  const afterClick = snapToasts().length;
  let secondDismissThrew = false;
  try { h.dismiss(); } catch (e) { secondDismissThrew = true; }
  out.close = { before, afterClick, afterSecondDismiss: snapToasts().length, secondDismissThrew };

  /* 4. Auto-dismiss fires; timeout: 0 never auto-dismisses */
  resetHarness();
  app.showToast('temporary', { tone: 'neutral', timeout: 50 });
  const visibleBeforeTimer = snapToasts().length;
  runTimers(50);
  const visibleAfterTimer = snapToasts().length;
  app.showToast('sticky', { tone: 'neutral', timeout: 0 });
  runTimers(60000);
  const stickySurvives = snapToasts().length;
  out.auto = { visibleBeforeTimer, visibleAfterTimer, stickySurvives };

  /* 5. Toasts never move focus */
  resetHarness();
  focusCalls = 0;
  app.showToast('focus check', { tone: 'ok', timeout: 50 });
  runTimers(50);
  const hh = app.showToast('focus check 2', { tone: 'error', timeout: 0 });
  await findCloseBtn(el('toast-container').children[0]).click();
  hh.dismiss();
  out.focus = { focusCalls };

  /* 6. Sanitization: markup is text, innerHTML is never used */
  resetHarness();
  const evil = '<img src=x onerror="alert(1)"><b>bold</b>';
  app.showToast(evil, { tone: 'neutral', timeout: 0 });
  const s = snapToasts()[0];
  out.sanitize = {
    text: s.text, textIsRaw: s.text === evil,
    innerHtmlUsed: s.innerHtmlUsed, tags: s.tags,
  };

  /* 7a. SYNC success: started toast is replaced by the ok result */
  resetHarness();
  postImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({
      ok: true,
      steps: {
        reconcile: { ok: true, open_orders_count: 2, fills_recorded: 1, orders_cancelled: 0 },
        venue_sync: { ok: true, account_value_usd: 100.5, open_positions_count: 0, closes_written: 0, raw_open_rows: 0 },
      },
      state: { local_open_orders: 2, venue_open_orders: 2 },
    }),
  });
  await el('btn-sync').click();
  await flush();
  out.syncSuccess = { toasts: snapToasts(), btnText: el('btn-sync').textContent };

  /* 7b. SYNC warning (HTTP 207 / unmeasured venue) */
  resetHarness();
  postImpl = async () => ({
    ok: true, status: 207,
    json: async () => ({
      ok: true,
      steps: {
        reconcile: { ok: true, open_orders_count: 1, fills_recorded: 0, orders_cancelled: 0 },
        venue_sync: { ok: true, venue_open_unmeasured: true },
      },
      state: {},
    }),
  });
  await el('btn-sync').click();
  await flush();
  out.syncWarn = { toasts: snapToasts(), btnText: el('btn-sync').textContent };

  /* 7c. SYNC throw (venue unreachable) */
  resetHarness();
  postImpl = async () => { throw new Error('boom'); };
  await el('btn-sync').click();
  await flush();
  out.syncError = { toasts: snapToasts(), btnText: el('btn-sync').textContent };

  /* 8a. Master toggle START: sent + running */
  resetHarness();
  const master = el('btn-master-toggle');
  master.dataset.action = 'start';
  delete master.dataset.mode;
  master.disabled = false;
  postImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({ ok: true, message: 'Stack started', status: runningStatus(), services: {} }),
  });
  await master.click();
  await flush();
  out.masterStart = { toasts: snapToasts() };

  /* 8b. Master toggle STOP: sent + stopped */
  resetHarness();
  master.dataset.action = 'stop';
  postImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({ ok: true, message: 'Stack stopped', status: stoppedStatus(), services: {} }),
  });
  await master.click();
  await flush();
  out.masterStop = { toasts: snapToasts() };

  /* 8c. Master toggle refused: error toast */
  resetHarness();
  master.dataset.action = 'start';
  postImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({ ok: false, message: 'Refusing to start: process registry unreadable' }),
  });
  await master.click();
  await flush();
  out.masterError = { toasts: snapToasts() };

  /* 8d. Master toggle partial stop: warn toast names the survivor */
  resetHarness();
  master.dataset.action = 'stop';
  postImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({
      ok: true,
      message: 'Stop accepted',
      services: { decide: { outcome: 'forced', detail: 'killpg failed, retried' } },
      status: runningStatus(),
    }),
  });
  await master.click();
  await flush();
  out.masterWarn = { toasts: snapToasts() };

  /* 8e. START SHADOW: same sent/result toasts via the shadow endpoint */
  resetHarness();
  master.dataset.action = 'start';
  master.dataset.mode = 'shadow';
  master.disabled = false;
  postImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({ ok: true, message: 'Shadow rehearsal started', pid: 4242, status: shadowStatus(true), services: {} }),
  });
  await master.click();
  await flush();
  out.shadowStart = { toasts: snapToasts(), posts: postUrls() };

  /* 8f. STOP SHADOW refused: error toast via the shadow endpoint */
  resetHarness();
  master.dataset.action = 'stop';
  master.dataset.mode = 'shadow';
  master.disabled = false;
  postImpl = async () => ({
    ok: true, status: 200,
    json: async () => ({ ok: false, message: 'No shadow rehearsal running for this database' }),
  });
  await master.click();
  await flush();
  out.shadowStopError = { toasts: snapToasts(), posts: postUrls() };

  /* 8g. Latched launch: button holds STARTING… and the pill reads STARTING */
  resetHarness();
  app.noteShadowStartLaunched({ runId: 'harness-shadow', dbName: 'shadow.db', minutes: 10, pid: 4242 });
  app.renderServiceHeader(shadowStatus(false), null, null);
  const latchBtn = el('btn-master-toggle');
  const latchPill = el('master-status-indicator');
  out.shadowLatch = {
    btnHtml: latchBtn.innerHTML,
    btnDisabled: latchBtn.disabled,
    btnBusy: latchBtn.getAttribute('aria-busy'),
    pillText: latchPill.textContent,
    pillClass: latchPill.className,
    pending: app.isShadowStartPending(),
  };

  /* 8h. The run reports back: latch clears with a "now running" toast, once */
  resetHarness();
  app.noteShadowStartLaunched({ runId: 'harness-shadow', dbName: 'shadow.db', minutes: 10, pid: 4242 });
  const resolveResult = app.checkPendingShadowStart(shadowStatus(true));
  out.shadowResolve = {
    result: resolveResult,
    toasts: snapToasts(),
    pendingAfter: app.isShadowStartPending(),
    secondCall: app.checkPendingShadowStart(shadowStatus(true)),
    toastsAfterSecondCall: snapToasts().length,
  };

  /* 8i. Boot window lapses with no run: error toast, latch clears */
  resetHarness();
  app.noteShadowStartLaunched({
    runId: 'harness-shadow', dbName: 'shadow.db', minutes: 10, pid: 4242,
    startedAt: Date.now() - 200000,
  });
  const timeoutResult = app.checkPendingShadowStart(shadowStatus(false));
  out.shadowTimeout = {
    result: timeoutResult,
    toasts: snapToasts(),
    pendingAfter: app.isShadowStartPending(),
  };

  /* 9a. Reset cancelled via Close button */
  resetHarness();
  await el('btn-reset').click();
  const modalOpen = el('reset-modal').classList.contains('show');
  await el('reset-modal-close').click();
  out.resetCancelClose = { modalOpen, toasts: snapToasts() };

  /* 9b. Reset cancelled via Escape */
  resetHarness();
  await el('btn-reset').click();
  for (const fn of docListeners.keydown || []) await fn({ key: 'Escape' });
  out.resetCancelEscape = { toasts: snapToasts() };

  /* 9c. Reset confirm ok */
  resetHarness();
  await el('btn-reset').click();
  el('reset-input').value = 'RESET';
  postImpl = async () => ({
    ok: true, status: 200, json: async () => ({ ok: true, steps: ['halted', 'archived'] }),
  });
  await el('reset-modal-confirm').click();
  await flush();
  out.resetConfirmOk = { toasts: snapToasts() };

  /* 9d. Reset confirm refused */
  resetHarness();
  await el('btn-reset').click();
  el('reset-input').value = 'RESET';
  postImpl = async () => ({
    ok: true, status: 200, json: async () => ({ ok: false, message: 'bot running' }),
  });
  await el('reset-modal-confirm').click();
  await flush();
  out.resetConfirmFail = { toasts: snapToasts() };

  /* 9e. Escape while the reset is still running names the in-flight run */
  resetHarness();
  await el('btn-reset').click();
  el('reset-input').value = 'RESET';
  let release;
  const held = new Promise((r) => { release = r; });
  postImpl = async () => {
    await held;
    return { ok: true, status: 200, json: async () => ({ ok: true, steps: ['halted'] }) };
  };
  const confirmP = el('reset-modal-confirm').click();
  await flush(2);
  for (const fn of docListeners.keydown || []) await fn({ key: 'Escape' });
  const inFlightToasts = snapToasts();
  release();
  await confirmP;
  await flush();
  out.resetInFlightEscape = { inFlightToasts, finalToasts: snapToasts() };

  process.stdout.write(JSON.stringify(out));
})().catch((e) => {
  process.stderr.write('harness failed: ' + (e && e.stack || e));
  process.exit(1);
});
