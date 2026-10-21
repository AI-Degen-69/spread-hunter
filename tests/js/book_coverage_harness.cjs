/* Harness: render the shadow-run switcher and report each row's book coverage.
 *
 * Loaded by tests/test_run_card_book_coverage.py. `app.js` is evaluated with
 * stub globals so the page bootstrap stays asleep and only the switcher
 * renders.
 *
 * Prints one JSON line: a per-run row report (coverage text, tooltip) plus
 * the pure `bookMixNote` edge cases.
 */
'use strict';

const path = require('node:path');
const fs = require('fs');

// The other harnesses resolve app.js themselves so the script runs standalone;
// an explicit path still wins, so the pytest wrapper can pin the file it means.
const appJsPath = process.argv[2]
  || path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js');

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
    this.classList = fakeClassList();
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  addEventListener() {}
  querySelectorAll() { return []; }
  appendChild() {}
  setAttribute() {}
  getAttribute() { return null; }
  removeAttribute() {}
}

const elements = new Map();
global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeEl(id));
    return elements.get(id);
  },
  querySelectorAll() { return []; },
  createElement() { return new FakeEl('created'); },
  addEventListener() {},
  body: new FakeEl('body'),
};
global.window = {
  addEventListener() {},
  matchMedia: () => ({ matches: false }),
  location: { hostname: '127.0.0.1' },
};
global.CONTROL_TOKEN = 'harness-token';
global.EventSource = function () { return { addEventListener() {}, close() {} }; };
global.setInterval = () => 0;
global.alert = () => {};
global.prompt = () => null;
global.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '' });
global.localStorage = {
  _v: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
  setItem(k, v) { this._v[k] = String(v); },
};

const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource',
             fs.readFileSync(appJsPath, 'utf8'))(
  mod, mod.exports, global.document, global.window,
  global.localStorage, global.EventSource);
const app = mod.exports;

/* Three rehearsals, one row each:
 *   - one that read the socket and fell back sometimes
 *   - one that never reached the socket at all (REST only)
 *   - one whose heartbeat predates the field, so it recorded no mix
 */
const RUNS = [
  {
    run_id: 'shadow-stream',
    db_path: '/tmp/01_shadow_stream.db',
    running: true,
    finished: false,
    heartbeat_age_sec: 3,
    dash_port: 8801,
    is_active_db: true,
    book_source_mix: { stream: 1204, rest: 37, fallback: { not_connected: 12, stale: 25 } },
  },
  {
    run_id: 'shadow-rest-only',
    db_path: '/tmp/02_shadow_rest.db',
    running: true,
    finished: false,
    heartbeat_age_sec: 5,
    dash_port: null,
    is_active_db: false,
    book_source_mix: { stream: 0, rest: 480, fallback: {} },
  },
  {
    run_id: 'shadow-old',
    db_path: '/tmp/03_shadow_old.db',
    running: false,
    finished: true,
    heartbeat_age_sec: 120,
    dash_port: null,
    is_active_db: false,
    // No book_source_mix: a heartbeat written before the field existed.
  },
];

(async () => {
  app.renderDbMode({
    db_mode: 'SHADOW',
    db_is_production: false,
    db_path: '/tmp/01_shadow_stream.db',
    shadow_runs: RUNS,
  });
  await app.renderRunSwitcher();
  const html = document.getElementById('db-run-switcher').innerHTML;

  /* One run's row: from its own marker to the next row's opening tag. */
  function rowFor(runId) {
    const marker = `data-run-id="${runId}"`;
    const at = html.indexOf(marker);
    if (at === -1) return '';
    const start = html.lastIndexOf('<div class="db-run-row', at);
    const next = html.indexOf('<div class="db-run-row', at);
    return next === -1 ? html.slice(start) : html.slice(start, next);
  }

  const MIX_RE = /<div class="db-run-mix" title="([^"]*)">([^<]*)<\/div>/;

  const rows = RUNS.map(r => {
    const chunk = rowFor(r.run_id);
    const m = MIX_RE.exec(chunk);
    return {
      run_id: r.run_id,
      found: chunk !== '',
      hasMixLine: chunk.indexOf('db-run-mix') !== -1,
      text: m ? m[2] : '',
      title: m ? m[1] : '',
    };
  });

  /* The pure helper, for the cases a row cannot show: no mix at all, and a
   * mix that recorded zero reads (a run that read no books is not the same
   * thing as a run that recorded nothing). The last three are hand-edited
   * heartbeats: the file is not ours, so nothing junk may reach the page. */
  const notes = {
    noMix: app.bookMixNote(undefined),
    zeroMix: app.bookMixNote({ stream: 0, rest: 0 }),
    junkMix: app.bookMixNote('nonsense'),
    arrayMix: app.bookMixNote([]),
    stringFallback: app.bookMixNote({ stream: 5, rest: 1, fallback: '12' }),
    negativeMix: app.bookMixNote({ stream: -3, rest: 2 }),
  };

  process.stdout.write(JSON.stringify({ rows, notes }));
})();
