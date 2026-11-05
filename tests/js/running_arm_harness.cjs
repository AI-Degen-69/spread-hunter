/* Drives renderRunningArmCard against a stub DOM and prints what the RUNNING
 * ARM card would show. Used by tests/test_dashboard_server.py (#934).
 *
 * Reads a scenario name on argv[2], runs it, settles pending promises, and
 * writes the observed card state as JSON on stdout.
 */
const fs = require('fs');
const path = require('path');

const scenario = process.argv[2];
const elements = {};

function element(id) {
  if (!elements[id]) {
    const listeners = {};
    const el = {
      id,
      textContent: '',
      innerHTML: '',
      className: '',
      title: '',
      value: '',
      options: [],
      disabled: false,
      hidden: false,
      scrolled: false,
      clicked: false,
      style: {},
      classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
      dataset: {},
      _listeners: listeners,
      querySelector: (sel) => element(id + ' ' + String(sel)),
      querySelectorAll: () => [],
      appendChild() {},
      setAttribute() {},
      getAttribute: () => null,
      addEventListener(type, fn) { listeners[type] = fn; },
      dispatchEvent(ev) {
        const fn = listeners[ev && ev.type];
        if (fn) fn({ target: el });
      },
      click() {
        el.clicked = true;
        if (listeners.click) listeners.click();
      },
      scrollIntoView() { el.scrolled = true; },
    };
    elements[id] = el;
  }
  return elements[id];
}

// The Strategy navigation button the card action clicks through.
const strategyBtn = element('strategy-nav-btn');

global.document = {
  getElementById: (id) => element(id),
  querySelector: (sel) => (sel === '.proto-nav-btn[data-page="strategy"]' ? strategyBtn : null),
  querySelectorAll: () => [],
  createElement: () => element('created-' + Math.random()),
  addEventListener() {},
  body: { classList: { add() {}, remove() {}, toggle() {} } },
};
global.window = { addEventListener() {}, location: { href: '' } };
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
// app.js opens its SSE stream at load time, outside the module guard.
global.EventSource = function EventSource() {
  return { addEventListener() {}, close() {}, onerror: null, onmessage: null };
};
global.Event = function Event(type) { return { type }; };

// Per-URL fetch router. Arm-detail answers are configured per scenario;
// /api/parameters always resolves empty so renderParameters() returns early.
const fetchLog = [];
let armDetail = () => ({ ok: true, json: async () => ({ ok: false, message: 'no stub' }) });
global.fetch = (url) => {
  fetchLog.push(String(url));
  if (String(url).startsWith('/api/parameters')) {
    return Promise.resolve({
      ok: true, json: async () => ({ parameters: [], arm_context: null, arm_error: null }),
    });
  }
  if (String(url).startsWith('/api/profile/arm-detail')) {
    return armDetail(String(url));
  }
  return Promise.resolve({ ok: false, json: async () => ({}) });
};

// Evaluated as CommonJS on purpose: package.json declares "type": "module",
// and app.js is a plain browser script that bootstraps itself unless a
// `module.exports` is present.
const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;

const flush = async (ms = 30) => new Promise((r) => setTimeout(r, ms));
const armFetches = (name) => fetchLog.filter(
  (u) => u === '/api/profile/arm-detail?name=' + encodeURIComponent(name)).length;

function payload(name, overrides = {}) {
  return {
    ok: true,
    name,
    label: name + ' label',
    profile: name + ' profile',
    env: { A: '1', B: '2' },
    ...overrides,
  };
}

function card() {
  return {
    tag: element('running-arm-state').textContent,
    label: element('running-arm-label').textContent,
    profile: element('running-arm-profile').textContent,
    profile_hidden: !!element('running-arm-profile').hidden,
    env: element('running-arm-env').innerHTML,
    note: element('running-arm-note').textContent,
    details_disabled: !!element('running-arm-details-btn').disabled,
    params_disabled: !!element('running-arm-params-btn').disabled,
    arm_detail_fetches: fetchLog.filter((u) => u.startsWith('/api/profile/arm-detail')).length,
  };
}

function statusFor(arm, extra = {}) {
  const profile = { name: 'prof-1', preset: 'p', running: true, source: 'shadow', ...extra };
  if (arm !== undefined) profile.tournament_arm = arm;
  return { active_profile: profile };
}

async function main() {
  switch (scenario) {
    case 'label-profile-overrides': {
      armDetail = () => Promise.resolve({ ok: true, json: async () => payload('armA') });
      app.renderRunningArmCard(statusFor('armA'));
      await flush();
      return { ...card() };
    }
    case 'baseline-when-env-empty': {
      armDetail = () => Promise.resolve({
        ok: true, json: async () => payload('armB', { env: {} }),
      });
      app.renderRunningArmCard(statusFor('armB', { running: false }));
      await flush();
      return { ...card() };
    }
    case 'fallback-profile-name-without-arm': {
      app.renderRunningArmCard(statusFor(undefined, { running: true }));
      await flush();
      return { ...card() };
    }
    case 'without-profile-shows-no-run': {
      app.renderRunningArmCard({});
      await flush();
      return { ...card() };
    }
    case 'paints-without-kpi-portfolio': {
      armDetail = () => Promise.resolve({ ok: true, json: async () => payload('armA') });
      app.renderPortfolioOverview({}, statusFor('armA'), null);
      await flush();
      return { ...card() };
    }
    case 'fetches-once-per-arm-change': {
      armDetail = () => Promise.resolve({ ok: true, json: async () => payload('armA') });
      const sA = statusFor('armA');
      app.renderRunningArmCard(sA);
      app.renderRunningArmCard(sA);
      app.renderRunningArmCard(sA);
      await flush();
      armDetail = (url) => Promise.resolve({
        ok: true, json: async () => payload(url.includes('armB') ? 'armB' : 'armA'),
      });
      app.renderRunningArmCard(statusFor('armB'));
      await flush();
      // Cache reuse: returning to armA must not fetch it again (#947).
      app.renderRunningArmCard(sA);
      await flush();
      return { armA: armFetches('armA'), armB: armFetches('armB'), label: element('running-arm-label').textContent };
    }
    case 'failed-fetch-keeps-previous': {
      armDetail = (url) => (url.includes('armB')
        ? Promise.resolve({ ok: true, json: async () => ({ ok: false, message: 'boom' }) })
        : Promise.resolve({ ok: true, json: async () => payload('armA') }));
      app.renderRunningArmCard(statusFor('armA'));
      await flush();
      app.renderRunningArmCard(statusFor('armB'));
      await flush();
      return { ...card(), armA: armFetches('armA'), armB: armFetches('armB') };
    }
    case 'newer-arm-wins-over-late-response': {
      let resolveA = null;
      armDetail = (url) => {
        if (url.includes('armA')) return new Promise((res) => { resolveA = res; });
        return Promise.resolve({ ok: true, json: async () => payload('armB') });
      };
      app.renderRunningArmCard(statusFor('armA'));
      app.renderRunningArmCard(statusFor('armB'));
      await flush();
      resolveA({ ok: true, json: async () => payload('armA') });
      await flush();
      return { ...card() };
    }
    case 'null-status-keeps-previous': {
      armDetail = () => Promise.resolve({ ok: true, json: async () => payload('armA') });
      app.renderRunningArmCard(statusFor('armA'));
      await flush();
      const before = card();
      app.renderRunningArmCard(null);
      await flush();
      return { before, after: card() };
    }
    case 'env-values-are-escaped': {
      armDetail = () => Promise.resolve({
        ok: true, json: async () => payload('armA', { env: { X: '<script>' } }),
      });
      app.renderRunningArmCard(statusFor('armA'));
      await flush();
      return { ...card() };
    }
    case 'details-action-opens-arm-detail-modal': {
      armDetail = () => Promise.resolve({ ok: true, json: async () => payload('armA') });
      app.renderRunningArmCard(statusFor('armA'));
      await flush();
      const before = armFetches('armA');
      element('running-arm-details-btn').click();
      await flush();
      return {
        before,
        after: armFetches('armA'),
        modal_display: element('arm-detail-modal').style.display,
        modal_content: element('arm-detail-modal .arm-detail-content').innerHTML,
      };
    }
    case 'full-parameters-selects-arm-and-scrolls': {
      armDetail = () => Promise.resolve({ ok: true, json: async () => payload('armA') });
      const select = element('param-arm-select');
      select.options = [{ value: 'p1|other' }, { value: 'p1|armA' }, { value: 'p2|armA' }];
      select.value = 'p1|other';
      app.renderRunningArmCard(statusFor('armA'));
      await flush();
      element('running-arm-params-btn').click();
      await flush();
      return {
        select_value: select.value,
        param_fetch: fetchLog.filter((u) => u.startsWith('/api/parameters?')),
        strategy_clicked: strategyBtn.clicked,
        panel_scrolled: element('params-panel').scrolled,
        note: element('running-arm-note').textContent,
      };
    }
    case 'failed-fetch-note-persists-across-polls': {
      armDetail = (url) => (url.includes('armB')
        ? Promise.resolve({ ok: true, json: async () => ({ ok: false, message: 'boom' }) })
        : Promise.resolve({ ok: true, json: async () => payload('armA') }));
      app.renderRunningArmCard(statusFor('armA'));
      await flush();
      const sB = statusFor('armB');
      app.renderRunningArmCard(sB);
      await flush();
      // The next poll: no new request, so the failure note must survive (#947).
      app.renderRunningArmCard(sB);
      await flush();
      return { ...card(), armB: armFetches('armB') };
    }
    case 'stale-label-after-no-arm-uses-placeholder': {
      armDetail = (url) => {
        if (url.includes('armC')) return new Promise(() => {});
        return Promise.resolve({ ok: true, json: async () => payload('armA') });
      };
      app.renderRunningArmCard(statusFor('armA'));
      await flush();
      app.renderRunningArmCard(statusFor(undefined, { running: false }));
      // A new uncached arm after a cleared card is a first paint (#947).
      app.renderRunningArmCard(statusFor('armC'));
      await flush();
      return { ...card() };
    }
    case 'catalog-miss-note-persists-across-polls': {
      armDetail = () => Promise.resolve({ ok: true, json: async () => payload('armA') });
      const select = element('param-arm-select');
      select.options = [{ value: 'p1|other' }];
      select.value = 'p1|other';
      const sA = statusFor('armA');
      app.renderRunningArmCard(sA);
      await flush();
      element('running-arm-params-btn').click();
      await flush();
      // The next poll repaints from cache: the miss note must survive (#947).
      app.renderRunningArmCard(sA);
      await flush();
      return { ...card() };
    }
    case 'full-parameters-notes-missing-catalog-entry': {
      armDetail = () => Promise.resolve({ ok: true, json: async () => payload('armZ') });
      const select = element('param-arm-select');
      select.options = [{ value: 'p1|other' }];
      select.value = 'p1|other';
      app.renderRunningArmCard(statusFor('armZ'));
      await flush();
      element('running-arm-params-btn').click();
      await flush();
      return {
        select_value: select.value,
        strategy_clicked: strategyBtn.clicked,
        panel_scrolled: element('params-panel').scrolled,
        note: element('running-arm-note').textContent,
      };
    }
    default:
      throw new Error('unknown scenario: ' + scenario);
  }
}

main().then(
  (out) => process.stdout.write(JSON.stringify(out)),
  (err) => { process.stderr.write(String(err && err.stack || err)); process.exit(1); },
);
