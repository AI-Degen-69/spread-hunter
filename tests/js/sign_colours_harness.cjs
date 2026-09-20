/* Drives the panels that colour a number by its sign, against a stub DOM, and
 * prints the class, the text and the arrow each one produced.
 * Used by tests/test_negative_values_read_as_losses.py.
 *
 * Reads one JSON payload {kpi, status, statistical_analytics, cycles} on
 * argv[2] and writes the rendered values as JSON on stdout.
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const elements = {};

// The charts need a real SVG surface; returning null makes the chart renderers
// bail at their own guard, which is what we want for the card assertions.
const NULL_IDS = new Set(['broker-chart-svg-container', 'broker-chart-tooltip']);

// The hero pill holds the direction chevron, and the renderer reaches it with
// `querySelector('polyline')`. A stub that always returns null would make the
// arrow untestable.
function stubPolyline() {
  const attrs = { points: '18 15 12 9 6 15' };
  return {
    setAttribute(name, value) { attrs[name] = value; },
    getAttribute(name) { return attrs[name]; },
  };
}

function element(id) {
  if (!elements[id]) {
    const polyline = id === 'broker-hero-pnl' ? stubPolyline() : null;
    // A real classList: the stale-backend note is shown/hidden by class, so a
    // stub that swallows toggle() would make "the note appeared" untestable.
    const classes = new Set();
    elements[id] = {
      id,
      textContent: '',
      innerHTML: '',
      className: '',
      title: '',
      style: {},
      classList: {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        contains: (c) => classes.has(c),
        toggle: (c, force) => {
          const on = force === undefined ? !classes.has(c) : !!force;
          if (on) classes.add(c); else classes.delete(c);
          return on;
        },
      },
      _classes: classes,
      dataset: {},
      polyline,
      querySelector: (sel) => (sel === 'polyline' ? polyline : null),
      querySelectorAll: () => [],
      appendChild() {},
      setAttribute() {},
      getAttribute: () => null,
      addEventListener() {},
    };
  }
  return elements[id];
}

global.document = {
  getElementById: (id) => (NULL_IDS.has(id) ? null : element(id)),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  body: { classList: { add() {}, remove() {}, toggle() {} } },
};
global.window = { addEventListener() {}, location: { href: '' } };
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.EventSource = function EventSource() {
  return { addEventListener() {}, close() {}, onerror: null, onmessage: null };
};

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;

const kpi = input.kpi || {};
const stats = input.statistical_analytics
  || (kpi.statistical_analytics || {});

app.renderBrokerPortfolioOverview(kpi, input.status || {});
app.renderQuantRiskGrid(
  stats.trade_analytics || {}, kpi.portfolio || {}, stats);
if (input.cycles) app.renderMonteCarloChart(stats, input.cycles);

// Issue #251: the stale-backend check normally runs inside pollStatus(), which
// a harness never starts. Drive the same exported function directly and count
// the warnings it emits, so "one warning, not a flood" is measurable.
const warns = [];
const realWarn = console.warn;
console.warn = (...args) => warns.push(args.join(' '));
if ('payload_version' in input) {
  app.applyPayloadVersion({ payload_version: input.payload_version });
}
console.warn = realWarn;

const pill = element('broker-hero-pnl');
const spread = element('broker-kpi-spread');
const quant = element('quant-grid').innerHTML;
const mcSvg = element('monte-carlo-svg-container').innerHTML;

/* The sub-line under the tile with this label. */
function quantSub(label) {
  const at = quant.indexOf(label);
  if (at < 0) return null;
  const m = quant.slice(at).match(/<div class="quant-sub">([\s\S]*?)<\/div>/);
  return m ? m[1].replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim() : null;
}

/* The value text of the tile with this label. */
function quantValue(label) {
  const at = quant.indexOf(label);
  if (at < 0) return null;
  const m = quant.slice(at).match(/<div class="quant-value[^"]*">([\s\S]*?)<\/div>/);
  return m ? m[1].replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim() : null;
}

/* The class the tile with this label was rendered with. */
function quantClass(label) {
  const at = quant.indexOf(label);
  if (at < 0) return null;
  const after = quant.slice(at);
  const m = after.match(/<div class="quant-value([^"]*)"/);
  return m ? m[1].trim() : null;
}

process.stdout.write(JSON.stringify({
  pnl_text: pill && element('broker-pnl-amount').textContent,
  pnl_pct: element('broker-pnl-pct').textContent,
  pnl_pill_class: pill.className,
  pnl_arrow: pill.polyline ? pill.polyline.getAttribute('points') : null,
  spread_text: spread.textContent,
  spread_class: spread.className,
  quant_expectancy: quantClass('Mathematical Expectancy'),
  quant_sharpe: quantClass('Sharpe &amp; Sortino Ratio'),
  quant_profit_factor: quantClass('Profit Factor &amp; Payoff'),
  quant_win_rate: quantClass('Win Rate &amp; Wilson CI'),
  quant_ci: quantSub('Win Rate &amp; Wilson CI'),
  quant_expectancy_text: quantValue('Mathematical Expectancy'),
  quant_var: quantValue('95% Value at Risk (1D)'),
  quant_cvar: quantSub('95% Value at Risk (1D)'),
  quant_kelly: quantValue('Kelly Optimal Sizing'),
  quant_kelly_sub: quantSub('Kelly Optimal Sizing'),
  quant_payoff: quantSub('Profit Factor &amp; Payoff'),
  stale_note_shown: element('quant-stale-note').classList.contains('show'),
  stale_warn_count: warns.length,
  stale_warn_text: warns.length ? warns[warns.length - 1] : null,
  mc_end_label: (mcSvg.match(/font-weight="700"[^>]*>([^<]*)</) || [])[1] || null,
  mc_end_x: parseFloat((mcSvg.match(/<text x="([\d.]+)"[^>]*font-size="9"/) || [])[1]),
  mc_end_anchor: /font-size="9"[^>]*text-anchor="end"/.test(mcSvg),
  mc_footer: element('monte-carlo-footer').innerHTML
    .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(),
}));
