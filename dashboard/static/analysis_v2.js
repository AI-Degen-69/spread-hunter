/* analysis_v2.js — data binding for the Data Analysis sidebar page (#568).
 *
 * The markup lives in index.html under #analysis-v2; this file never reads
 * telemetry itself. The dashboard shell's existing GET /api/kpi poll owns the
 * single fetch and calls renderAnalysisV2(root, kpi) with the cached payload.
 * Nothing here writes to the venue, the order registry, or any worker state.
 *
 * Two layers, deliberately separate so both are testable from node:
 *   pure  — resolve / format / series->svg-path maths (no DOM)
 *   apply — walk the annotated DOM under `root` and paint
 */
'use strict';

/* ------------------------------------------------------------------ pure */

/* Dotted-path read over the /api/kpi payload. Missing -> null, never throw:
 * an unmeasured field must blank the cell, not blank the page. */
function resolve(obj, path) {
  if (!path) return null;
  let cur = obj;
  const parts = String(path).split('.');
  for (let i = 0; i < parts.length; i++) {
    if (cur == null || typeof cur !== 'object') return null;
    cur = cur[parts[i]];
  }
  return cur === undefined ? null : cur;
}

function num(v) {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

const SIGNED = { pcts: 1, usd: 1, cents: 1 };

/* fmt -> string. Two families, because the payload mixes them:
 *   pct / pct1   value is ALREADY a percentage (-6.76, 100.0)
 *   pctmul*      value is a 0..1 FRACTION (win_rate 0.6 -> 60.0%)
 * Sign convention matches the page: $ and ¢ always signed (direction is the
 * point), % and x signed only when negative. */
function format(value, fmt) {
  const n = num(value);
  if (n === null) return value === null || value === undefined ? '' : String(value);
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : (SIGNED[fmt] && n > 0 ? '+' : '');
  switch (fmt) {
    case 'pct':
      return sign + abs.toFixed(2) + '%';
    case 'pct1':
      return sign + abs.toFixed(1) + '%';
    case 'pcts':
      return sign + abs.toFixed(2) + '%';
    case 'pctmul':
      return sign + (abs * 100).toFixed(1) + '%';
    case 'pctmuls':
      return sign + (abs * 100).toFixed(2) + '%';
    case 'usd':
      return sign + '$' + abs.toFixed(2);
    case 'cents':
      return sign + abs.toFixed(2) + '¢';
    case 'x':
      return sign + abs.toFixed(2) + 'x';
    case 'num':
      return sign + abs.toFixed(2);
    case 'sec':
      return abs.toFixed(1) + 's';
    case 'int':
      return String(Math.round(abs));
    default:
      return String(value);
  }
}

/* Colour word for a numeric cell: the page's pos/neg/neu vocabulary. */
function signClass(value) {
  const n = num(value);
  if (n === null) return null;
  if (n > 0) return 'pos';
  if (n < 0) return 'neg';
  return 'neu';
}

/* Backend band word -> the design's badge class + label. */
const BAND_STYLE = {
  good: ['badge--good', 'Good'],
  bad: ['badge--bad', 'Bad'],
  average: ['badge--avg', 'Average'],
  neutral: ['badge--neu', 'Neutral'],
  unmeasured: ['badge--neu', 'Unmeasured'],
};

/* Prefix -> where that band string lives in the payload. The analytics
 * payloads already carry their own verdict bands; reuse them rather than
 * re-deriving a second opinion here. */
const BAND_ROOTS = {
  fp: 'statistical_analytics.fill_path_edge.bands',
  fr: 'statistical_analytics.rescue_friction.bands',
};

function bandStyle(band) {
  return BAND_STYLE[band] || BAND_STYLE.neutral;
}

/* Band word -> the {badge, badgeText} shape the gate refs use, so a payload
 * band and a computed gate verdict are interchangeable in the band pass. */
function badgeFrom(band) {
  const st = bandStyle(band);
  return { badge: st[0], badgeText: st[1] };
}

/* ---- decision gates: observed value + verdict, computed from the payload ---- */

const POWER_GATE = 120; // payload has no required_observations; data_analysis.js uses the same default
const DRAWDOWN_LIMIT_PCT = 5.0;
const MARKOUT_MIN_SAMPLES = 25;

function requiredObservations(kpi) {
  const ta = (kpi && kpi.trade_analytics) || {};
  const n = num(ta.required_observations);
  return n === null ? POWER_GATE : n;
}

function gateVerdicts(kpi) {
  const ta = kpi.trade_analytics || {};
  const stats = kpi.statistical_analytics || {};
  const n = num(ta.n_closes) || 0;
  const required = num(ta.required_observations) || POWER_GATE;
  const bell = stats.probability_bell || {};
  const mo = (stats.robustness && stats.robustness.markout && stats.robustness.markout.intervals) || [];
  const mo5 = mo.filter((x) => x.horizon === '5m')[0] || {};

  const ci90 = num(ta.ci90_lower_pct);
  const winRate = num(ta.win_rate);
  const dd = num(ta.max_drawdown_pct);
  const samples = num(kpi.markout_samples);
  const adverse = num(kpi.adverse_selection);

  return {
    band: {
      text: `${format((num(bell.sweet_spot_pct) || 0) / 100, 'pctmul')} in sweet spot`,
      cls: 'pos',
      badge: 'badge--active', badgeText: 'Active Rule',
    },
    edge_viability: {
      observed: ci90 === null ? 'unmeasured' : format(ci90, 'pcts'),
      cls: ci90 === null ? '' : signClass(ci90),
      badge: ci90 !== null && ci90 > 0 ? 'badge--go' : (n >= required ? 'badge--nogo' : 'badge--accum'),
      badgeText: ci90 !== null && ci90 > 0 ? 'Go' : (n >= required ? 'No-Go' : 'Accumulating'),
    },
    neutrality: {
      observed: winRate === null ? 'unmeasured' : format(winRate, 'pctmul'),
      cls: winRate === null ? '' : (winRate > 0.5 ? 'pos' : 'neg'),
      // An unmeasured win rate is Accumulating, never No-Go: a missing
      // sample is not a failed hypothesis.
      badge: winRate === null ? 'badge--accum' : (winRate > 0.5 ? 'badge--go' : 'badge--nogo'),
      badgeText: winRate === null ? 'Accumulating' : (winRate > 0.5 ? 'Go' : 'No-Go'),
    },
    power: {
      observed: `${n} / ${required} (${required > 0 ? Math.min(100, Math.round((n / required) * 100)) : 0}%)`,
      cls: n >= required ? 'pos' : 'warn',
      badge: n >= required ? 'badge--go' : 'badge--accum',
      badgeText: n >= required ? 'Go' : 'Accumulating',
    },
    markout: {
      observed: samples === null ? 'unmeasured' :
        `${samples} · ${format(num(mo5.markout_cents), 'cents')}/share`,
      cls: adverse === null ? '' : signClass(adverse),
      badge: samples === null || samples < MARKOUT_MIN_SAMPLES ? 'badge--accum' :
        (adverse >= 0 ? 'badge--go' : 'badge--nogo'),
      badgeText: samples === null || samples < MARKOUT_MIN_SAMPLES ? 'Accumulating' :
        (adverse >= 0 ? 'Go' : 'No-Go'),
    },
    drawdown_guard: {
      observed: dd === null ? 'unmeasured' : format(dd, 'pct'),
      cls: dd === null ? '' : (dd > DRAWDOWN_LIMIT_PCT ? 'neg' : 'pos'),
      badge: dd === null ? 'badge--accum' : (dd > DRAWDOWN_LIMIT_PCT ? 'badge--nogo' : 'badge--go'),
      badgeText: dd === null ? 'Accumulating' : (dd > DRAWDOWN_LIMIT_PCT ? 'No-Go' : 'Go'),
    },
  };
}

/* Composite cells the markup asks for by `_name`: built here so the HTML keeps
 * one hook per cell and the arithmetic stays in one place. */
function derived(kpi) {
  const ta = kpi.trade_analytics || {};
  const stats = kpi.statistical_analytics || {};
  const n = num(ta.n_closes) || 0;
  const wins = num(ta.wins) || 0;
  const losses = num(ta.losses) || 0;
  const required = num(ta.required_observations) || POWER_GATE;
  const ci = ta.mean_pnl_ci || {};
  const levels = ci.levels || [];
  const byLevel = {};
  levels.forEach((l) => { byLevel[l.level] = l; });
  const l90 = byLevel[90] || {};
  const l95 = byLevel[95] || {};
  const fp = stats.fill_path_edge || {};
  const fr = stats.rescue_friction || {};
  const dd = (stats.capital_risk && stats.capital_risk.drawdown_recovery) || {};
  const gates = gateVerdicts(kpi);
  const funnel = kpi.execution_funnel || {};
  const stages = funnel.stages || [];
  const drops = funnel.drop_off || [];
  const mo = (stats.robustness && stats.robustness.markout && stats.robustness.markout.intervals) || [];
  const mo5 = mo.filter((x) => x.horizon === '5m')[0] || {};

  const stageLabel = (s, unit) => {
    if (!s) return '—';
    const markets = num(s.markets);
    return `${s.legs} ${unit}${markets != null ? ` · ${markets} markets` : ''}`;
  };
  const dropLabel = (d, tail) => {
    if (!d) return '—';
    const lost = num(d.lost);
    const kept = num(d.retained_pct);
    const carried = lost === null ? '' :
      (lost > 0 ? ` · lost ${lost}` : lost < 0 ? ` · gained ${-lost}` : ' · held');
    return `${kept === null ? '—' : kept.toFixed(1) + '%'} ${tail}${carried}`;
  };

  return {
    _winloss: `${wins}W / ${losses}L`,
    _required: required,
    _ci90: `${format(l90.lower, 'usd')} → ${format(l90.upper, 'usd')}`,
    _ci95: `${format(l95.lower, 'usd')} → ${format(l95.upper, 'usd')}`,
    _ciVerdict: (() => {
      const depth = num(l95.negative_depth_usd);
      // No verdict (or no closes) is unmeasured -- never a claimed win.
      if (!ci.verdict || n === 0) return '⚠ unmeasured — no closes in this registry';
      if (ci.verdict !== 'spans_zero') return '✓ Band excludes a loss — edge is positive at 95%';
      if (depth === null) return '⚠ Band spans breakeven';
      return `⚠ Band includes a loss down to ${format(-Math.abs(depth), 'usd')} per close`;
    })(),
    _rescueTotal: (num(fp.merge_count) || 0) + (num(fp.rescue_count) || 0) + (num(fp.other_count) || 0),
    _mo5: num(mo5.markout_cents),
    _mo5n: num(mo5.samples),
    _ddRegained: `${num(dd.episodes) || 0} / ${num(dd.open) || 0}`,
    _ddRecovered: dd.deepest_recovered ? 'regained' : 'still open',
    _var: ta.var_95_usd === null || ta.var_95_usd === undefined ? 'unmeasured' : format(ta.var_95_usd, 'usd'),
    _cvar: ta.cvar_95_usd === null || ta.cvar_95_usd === undefined ? 'unmeasured' : format(ta.cvar_95_usd, 'usd'),
    _neu: 'Neutral',
    _gateBand: `<span class="${gates.band.cls}">$0.15–$0.85 (${gates.band.text})</span>`,
    _gatePower: `<span class="${gates.power.cls}">${gates.power.observed}</span>`,
    _gateMarkout: `<span class="${gates.markout.cls}">${gates.markout.observed}</span>`,
    _f0: stageLabel(stages[0], 'legs'),
    _f1: stageLabel(stages[1], 'legs'),
    _f2: stageLabel(stages[2], 'closes'),
    _f3: stageLabel(stages[3], 'legs'),
    _fdrop0: dropLabel(drops[0], 'carried on'),
    _fdrop1: dropLabel(drops[1], 'carried on'),
    _fdrop2: dropLabel(drops[2], 'merged'),
    _suffPct: `${required > 0 ? Math.min(100, Math.round((n / required) * 100)) : 0}% of the gate`,
    /* `_gates` is keyed the way the markup's data-band refs are written
     * (`gates.edge_viability`, `rb.sqn`, ...) so the band pass is a plain
     * two-level lookup with no per-ref special-casing. */
    _gates: {
      gates: gates,
      rb: {
        markout5: badgeFrom(mo5.band),
        sqn: badgeFrom((stats.robustness && stats.robustness.sqn && stats.robustness.sqn.band)),
      },
      cr: {
        skew: badgeFrom(stats.capital_risk && stats.capital_risk.pnl_skewness && stats.capital_risk.pnl_skewness.band),
        sortino: badgeFrom(stats.capital_risk && stats.capital_risk.sortino_ratio && stats.capital_risk.sortino_ratio.band),
      },
    },
  };
}

/* ---- chart series -> svg path maths (pure) ---- */

const CH = { w: 400, h: 200, pad: 8, base: 178 };

function chartScales(values, opts) {
  const o = opts || {};
  let lo = o.lo !== undefined ? o.lo : Math.min.apply(null, values);
  let hi = o.hi !== undefined ? o.hi : Math.max.apply(null, values);
  if (o.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (lo === hi) { lo -= 1; hi += 1; }
  const inner = CH.w - CH.pad * 2;
  return {
    x: (i, count) => CH.pad + (count <= 1 ? inner / 2 : (i / (count - 1)) * inner),
    y: (v) => {
      const t = (v - lo) / (hi - lo);
      return CH.base - t * (CH.base - CH.pad);
    },
    lo, hi,
  };
}

/* Histogram bars (equal-width bins) -> `<rect>` list. */
function barsFromBins(bins, field) {
  if (!bins || !bins.length) return '';
  const key = field || 'count';
  const values = bins.map((b) => num(b[key]) || 0);
  const max = Math.max.apply(null, values.concat([1]));
  const s = chartScales(values, { lo: 0, hi: max });
  const slot = (CH.w - CH.pad * 2) / bins.length;
  const bw = Math.max(2, slot * 0.72);
  return bins.map((b, i) => {
    const v = num(b[key]) || 0;
    if (v <= 0) return '';
    const y = s.y(v);
    return `<rect x="${(CH.pad + i * slot + (slot - bw) / 2).toFixed(2)}" y="${y.toFixed(2)}" width="${bw.toFixed(2)}" height="${(CH.base - y).toFixed(2)}" fill="#1E293B" stroke="#334155" stroke-width="0.5"/>`;
  }).join('');
}

/* Gaussian bell over the bin centres -> path. */
function bellPath(bins, mean, stdev) {
  if (!bins || !bins.length || mean === null || stdev === null || stdev === 0) return '';
  const centres = bins.map((b) => ((num(b.min) || 0) + (num(b.max) || 0)) / 2);
  const pdf = (x) => Math.exp(-0.5 * Math.pow((x - mean) / stdev, 2));
  const vals = pdf(mean);
  const s = chartScales(centres, {});
  const pts = centres.map((x, i) => [s.x(i, centres.length), s.y(pdf(x) / vals)]);
  return 'M' + pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' L');
}

/* Polyline through (i, value) -> path. */
function linePath(values) {
  if (!values || !values.length) return '';
  const s = chartScales(values, { zero: true });
  return 'M' + values.map((v, i) => `${s.x(i, values.length).toFixed(1)},${s.y(num(v) || 0).toFixed(1)}`).join(' L');
}

/* Band polygon between two series (fan charts). */
function bandPath(lo, hi) {
  if (!lo || !lo.length || !hi || !hi.length) return '';
  const s = chartScales(lo.concat(hi), { zero: true });
  const top = hi.map((v, i) => `${s.x(i, hi.length).toFixed(1)},${s.y(num(v) || 0).toFixed(1)}`);
  const bot = lo.map((v, i) => `${s.x(i, lo.length).toFixed(1)},${s.y(num(v) || 0).toFixed(1)}`).reverse();
  return 'M' + top.join(' L') + ' L' + bot.join(' L') + ' Z';
}

function svg(body, vb) {
  return `<svg viewBox="0 0 ${vb || '400 200'}" preserveAspectRatio="none">${body}</svg>`;
}

function gridlines() {
  return [CH.base, 130, 80].map((y, i) =>
    `<line x1="0" y1="${y}" x2="400" y2="${y}" stroke="${i === 0 ? '#1E293B' : '#172033'}" stroke-width="1"${i === 0 ? '' : ' stroke-dasharray="2 4"'}/>`).join('');
}

/* ------------------------------------------------------------------ apply */

const SIGN_WORDS = ['pos', 'neg', 'neu', 'warn'];

function setSign(el, value) {
  const cls = signClass(value);
  const keep = String(el.className).split(/\s+/).filter((c) => c && SIGN_WORDS.indexOf(c) === -1);
  if (cls) keep.push(cls);
  el.className = keep.join(' ');
}

function paintBinds(root, kpi, d) {
  const nodes = root.querySelectorAll('[data-bind]');
  for (let i = 0; i < nodes.length; i++) {
    const el = nodes[i];
    const path = el.getAttribute('data-bind');
    const fmt = el.getAttribute('data-fmt') || 'raw';
    let value;
    if (path.charAt(0) === '_') value = d[path];
    else value = resolve(kpi, path);
    if (value === null || value === undefined) continue; // leave the design's own placeholder
    if (fmt === 'html') el.innerHTML = String(value);
    else el.textContent = format(value, fmt);
    if (el.getAttribute('data-sign')) setSign(el, value);
  }

  const banded = root.querySelectorAll('[data-band]');
  for (let i = 0; i < banded.length; i++) {
    const el = banded[i];
    const ref = el.getAttribute('data-band');
    const parts = ref.split('.');
    let cls = null;
    let text = null;
    if (ref === '_neu') {
      cls = 'badge--neu';
      text = 'Neutral';
    } else if (BAND_ROOTS[parts[0]]) {
      // Band word comes straight from the analytics payload's own verdict.
      const band = resolve(kpi, BAND_ROOTS[parts[0]] + '.' + parts.slice(1).join('.'));
      if (band) { const st = bandStyle(band); cls = st[0]; text = st[1]; }
    } else {
      // Computed gate: its badge class and label are already decided.
      const g = d._gates[parts[0]];
      const gate = g && parts.length > 1 ? g[parts[1]] : null;
      if (gate) { cls = gate.badge; text = gate.badgeText; }
    }
    if (!cls) continue;
    const keep = String(el.className).split(/\s+/).filter((c) => c && c.indexOf('badge--') !== 0);
    keep.push(cls);
    el.className = keep.join(' ');
    el.textContent = text;
  }
}

/* Sufficiency table: the confidence-toggle init in initConfidenceToggle reads
 * data-r95/98/99, data-rem* and data-p* off each <tr>, so filling those
 * attributes is the whole integration — no second renderer to keep in sync. */
function paintSufficiency(root, kpi) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope) return;
  const statuses = (resolve(kpi, 'trade_analytics.sample_size_sufficiency.statuses') || {});
  const rows = scope.querySelectorAll('tr.ssr');
  for (let i = 0; i < rows.length; i++) {
    const key = rows[i].getAttribute('data-suff');
    const st = statuses[key];
    if (!st) continue;
    (st.levels || []).forEach((lv) => {
      const c = String(lv.confidence_pct);
      rows[i].dataset['r' + c] = String(lv.required_n);
      rows[i].dataset['rem' + c] = String(lv.remaining_n);
      rows[i].dataset['p' + c] = String(lv.progress_pct);
    });
    const cur = rows[i].querySelector('.cur');
    if (cur) cur.textContent = `${st.current_n} ${st.base === 'orders' ? 'orders' : 'closes'}`;
    const req = rows[i].querySelector('.req');
    if (req) req.textContent = String(st.levels && st.levels[0] ? st.levels[0].required_n : '—');
  }

  /* The KPI-3 progress bar reads the same 95% row the table defaults to. */
  const bar = scope.querySelector ? scope.querySelector('#suffBar')
    : (typeof document !== 'undefined' && document.getElementById
      ? document.getElementById('suffBar') : null);
  const lvl = (statuses.pnl_expectancy && statuses.pnl_expectancy.levels || [])
    .filter((l) => l.confidence_pct === 95)[0];
  if (bar && lvl) bar.style.width = String(Math.max(0, Math.min(100, lvl.progress_pct))) + '%';

  // Re-render the currently active confidence column after the swap.
  const active = scope.querySelector ? scope.querySelector('.conf-toggle .pill.active') : null;
  if (active && active.click) active.click();
}

function paintCharts(root, kpi) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope) return;
  const stats = kpi.statistical_analytics || {};
  const ta = kpi.trade_analytics || {};
  const q = (sel) => (scope.querySelector ? scope.querySelector(sel)
    : (typeof document !== 'undefined' ? document.querySelector(sel) : null));
  const set = (id, body, vb) => {
    const area = q('#' + id + ' .chart-area');
    if (area) area.innerHTML = svg(body, vb);
  };
  const badge = (id, html, cls) => {
    const el = q('#' + id + ' .chart-badge');
    if (el) { el.innerHTML = html; if (cls) el.className = 'chart-badge ' + cls; }
  };
  const foot = (id, html) => {
    const el = q('#' + id + ' .chart-foot');
    if (el) el.innerHTML = html;
  };
  const statsLine = (id, html) => {
    const el = q('#' + id + ' .chart-stats');
    if (el) el.innerHTML = html;
  };

  /* 1 — return distribution: histogram of realised pnl_pct + fitted bell. */
  const pr = stats.position_returns || {};
  const pos = (pr.positions || []).map((p) => num(p.pnl_pct)).filter((v) => v !== null);
  if (pos.length) {
    const lo = Math.min.apply(null, pos), hi = Math.max.apply(null, pos);
    const bins = 13, width = (hi - lo) / bins || 1;
    const hist = [];
    for (let i = 0; i < bins; i++) hist.push({ count: 0 });
    pos.forEach((v) => {
      const idx = Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / width)));
      hist[idx].count += 1;
      if (hist[idx].min === undefined) { hist[idx].min = lo + idx * width; hist[idx].max = lo + (idx + 1) * width; }
    });
    const mean = num(pr.mean_pnl_pct), sd = num(pr.stdev_pnl_pct);
    const ci95 = ta.ci95_return_pct || {};
    set('ch1', gridlines() + barsFromBins(hist, 'count') +
      (bellPath(hist, mean, sd) ? `<path d="${bellPath(hist, mean, sd)}" fill="none" stroke="#38BDF8" stroke-width="2"/>` : '') +
      `<line x1="240" y1="14" x2="240" y2="180" stroke="#F9FAFB" stroke-width="1" stroke-dasharray="4 3"/>`);
    badge('ch1', `${ci95.lower !== undefined ? '95% CI: [' + format(ci95.lower, 'pcts') + ', ' + format(ci95.upper, 'pcts') + ']' : 'CI unmeasured'} · ${ci95.lower !== undefined && ci95.lower < 0 && ci95.upper > 0 ? 'ZERO CROSSING' : 'EXCLUDES ZERO'}`,
      ci95.lower !== undefined && ci95.lower < 0 && ci95.upper > 0 ? 'warn' : 'good');
    statsLine('ch1', `Sample universe: ${pos.length} · Mean ${format(mean, 'pct')} · σ ±${format(sd, 'pct')} · SE ${format(num(pr.sem_pnl_pct), 'pct')}`);
    foot('ch1', `<span class="k">µ <b>${format(mean, 'pct')}</b></span>` +
      `<span class="k">σ <b>±${format(sd, 'pct')}</b></span>` +
      `<span class="k">SE <b>${format(num(pr.sem_pnl_pct), 'pct')}</b></span>` +
      `<span class="k warn">95% CI <b>[${format(ci95.lower, 'pcts')}, ${format(ci95.upper, 'pcts')}]</b></span>`);
  }

  /* 2 — pair cost distribution. */
  const pc = stats.pair_costs || {};
  if ((pc.bins || []).length) {
    const dens = pc.bins.map((b) => num(b.density) || 0);
    const s = chartScales(dens, { lo: 0 });
    const pts = dens.map((v, i) => `${s.x(i, dens.length).toFixed(1)},${s.y(v).toFixed(1)}`);
    set('ch2', gridlines() +
      barsFromBins(pc.bins, 'density') +
      `<path d="M${pts.join(' L')}" fill="none" stroke="#34D399" stroke-width="2"/>`);
    badge('ch2', `Median: ${format(num(pc.median), 'usd')}`);
    statsLine('ch2', `Mean cost µ ${format(num(pc.mean), 'usd')} · Max ${format(num(pc.max), 'usd')} · Min ${format(num(pc.min_observed), 'usd')} · Executed pairs: ${num(kpi.pairs_under_1) || 0} under $1.00`);
    foot('ch2', `<span class="k">µ <b>${format(num(pc.mean), 'usd')}</b></span>` +
      `<span class="k">median <b>${format(num(pc.median), 'usd')}</b></span>` +
      `<span class="k">σ <b>${format(num(pc.stdev), 'usd')}</b></span>` +
      `<span class="k">samples <b>${num(pc.samples_count) || 0}</b></span>`);
  }

  /* 3 — Monte Carlo fan (p01-p99 band + p50 median). */
  const mc = stats.monte_carlo || {};
  const steps = mc.steps || [];
  if (steps.length) {
    const p01 = steps.map((s2) => num(s2.p01));
    const p99 = steps.map((s2) => num(s2.p99));
    const p50 = steps.map((s2) => num(s2.p50));
    const prob = num(mc.prob_positive_return);
    set('ch3', gridlines() +
      `<path d="${bandPath(p01, p99)}" fill="rgba(56,189,248,.10)" stroke="none"/>` +
      `<path d="${linePath(p50)}" fill="none" stroke="#38BDF8" stroke-width="2"/>` +
      `<path d="${linePath(p01)}" fill="none" stroke="#64748B" stroke-width="1" stroke-dasharray="3 3"/>`);
    badge('ch3', `P(Profit &gt; 0): ${format(prob, 'pct1')}`,
      prob !== null && prob >= 50 ? 'good' : 'warn'); // prob_positive_return is a PERCENT
    statsLine('ch3', `Simulations: ${num(mc.paths) || 0} paths · ${steps.length} cycles · Worst-case drawdown ${format(num(mc.worst_case_drawdown_pct), 'pct')} · Median return ${format(p50[p50.length - 1], 'usd')}`);
    foot('ch3', `<span class="k">paths <b>${num(mc.paths) || 0}</b></span>` +
      `<span class="k">samples <b>${num(mc.samples_count) || 0}</b></span>` +
      `<span class="k warn">worst DD <b>${format(num(mc.worst_case_drawdown_pct), 'pct')}</b></span>` +
      `<span class="k">P(profit) <b>${format(prob, 'pct1')}</b></span>`);
  }

  /* 4 — outcome probability bell. */
  const pb = stats.probability_bell || {};
  if ((pb.bins || []).length) {
    const pdf = pb.bins.map((b) => num(b.theoretical_pdf) || 0);
    const s = chartScales(pdf, { lo: 0 });
    const pts = pdf.map((v, i) => `${s.x(i, pdf.length).toFixed(1)},${s.y(v).toFixed(1)}`);
    const sweet = pb.bins.filter((b) => b.in_sweet_spot);
    set('ch4', gridlines() + barsFromBins(pb.bins, 'empirical_count') +
      `<path d="M${pts.join(' L')}" fill="none" stroke="#38BDF8" stroke-width="2"/>`);
    badge('ch4', `${format(num(pb.sweet_spot_pct), 'pct1')} In Sweet Spot`, 'good');
    statsLine('ch4', `Mean probability ${format(num(pb.mean), 'pctmul')} · σ ±${format(num(pb.stdev), 'pctmul')} · Sweet-spot concentration ${format(num(pb.sweet_spot_pct), 'pct1')} · Executed sample ${num(pb.samples_count) || 0} fills`);
    foot('ch4', `<span class="k">µ <b>${format(num(pb.mean), 'pctmul')}</b></span>` +
      `<span class="k">σ <b>±${format(num(pb.stdev), 'pctmul')}</b></span>` +
      `<span class="k">sweet bins <b>${sweet.length}</b></span>` +
      `<span class="k">samples <b>${num(pb.samples_count) || 0}</b></span>`);
  }

  /* 5 — markout by horizon (bps). */
  const mo = (stats.markout && stats.markout.intervals) || [];
  if (mo.length) {
    const vals = mo.map((m) => num(m.displacement_bps) || 0);
    const s = chartScales(vals, { zero: true });
    const slot = (800 - 16) / mo.length;
    const bars = mo.map((m, i) => {
      const v = num(m.displacement_bps) || 0;
      const y0 = s.y(0), y1 = s.y(v);
      const top = Math.min(y0, y1), hgt = Math.max(1, Math.abs(y1 - y0));
      const col = v >= 0 ? '#34D399' : '#F87171';
      return `<rect x="${(8 + i * slot + slot * 0.2).toFixed(1)}" y="${top.toFixed(1)}" width="${(slot * 0.6).toFixed(1)}" height="${hgt.toFixed(1)}" fill="${col}" opacity="0.85"/>` +
        `<text x="${(8 + i * slot + slot * 0.5).toFixed(1)}" y="${(v >= 0 ? top - 5 : top + hgt + 12).toFixed(1)}" fill="${col}" font-size="10" text-anchor="middle">${v >= 0 ? '+' : ''}${v.toFixed(1)}</text>` +
        `<text x="${(8 + i * slot + slot * 0.5).toFixed(1)}" y="194" fill="#64748B" font-size="10" text-anchor="middle">${m.horizon}</text>`;
    }).join('');
    set('ch5', `<line x1="0" y1="${s.y(0).toFixed(1)}" x2="800" y2="${s.y(0).toFixed(1)}" stroke="#1E293B" stroke-width="1"/>` + bars, '800 200');
    const worst = mo.reduce((a, b) => ((num(a.displacement_bps) || 0) < (num(b.displacement_bps) || 0) ? a : b), mo[0]);
    const allOk = vals.every((v) => v >= 0);
    badge('ch5', `Markout Status: ${allOk ? 'FAVOURABLE' : 'MIXED'}`, allOk ? 'good' : 'warn');
    statsLine('ch5', `Worst horizon ${worst.horizon} · Displacement ${format(num(worst.displacement_bps), 'pcts').replace('%', ' bps')} · Matured samples ${num(kpi.markout_samples) || 0} fills`);
    foot('ch5', mo.map((m) => `<span class="k">${m.horizon} <b>${(num(m.displacement_bps) || 0) >= 0 ? '+' : ''}${(num(m.displacement_bps) || 0).toFixed(1)} bps</b> (${m.samples})</span>`).join(''));
  }
}

/* The CI scale's track spans the 95% band; every tick and label sits at
 * the payload's own value mapped onto that span. Unmeasured (no closes,
 * or a degenerate band) hides the scale outright — static geometry would
 * keep drawing the last payload's positions and lie about this one. */
function paintCiScale(root, kpi) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope) return;
  const q = (sel) => (scope.querySelector ? scope.querySelector(sel) : null);
  const qa = (sel) => (scope.querySelectorAll ? scope.querySelectorAll(sel) : []);
  const scale = (scope.getElementById ? scope.getElementById('ciScale')
    : null) || q('#ciScale');
  if (!scale) return;
  const ta = (kpi && kpi.trade_analytics) || {};
  const ci = ta.mean_pnl_ci || {};
  const byLevel = {};
  (ci.levels || []).forEach((l) => { byLevel[l.level] = l; });
  const l95 = byLevel[95] || {}, l90 = byLevel[90] || {};
  const lo = num(l95.lower), hi = num(l95.upper);
  const marks = {
    lo95: lo, lo90: num(l90.lower), mean: num(ta.expectancy_usd),
    be: 0, hi90: num(l90.upper), hi95: hi,
  };
  const measured = lo !== null && hi !== null && hi !== lo &&
    marks.lo90 !== null && marks.hi90 !== null;
  scale.style.display = measured ? '' : 'none';
  if (!measured) return;
  const pct = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  const labels = {
    lo95: `95% Low ${format(lo, 'usd')}`,
    lo90: `90% Low ${format(marks.lo90, 'usd')}`,
    mean: `Mean ${format(marks.mean, 'usd')}`,
    be: '$0.00 ▼',
    hi90: `90% High ${format(marks.hi90, 'usd')}`,
    hi95: `95% High ${format(marks.hi95, 'usd')}`,
  };
  Object.keys(marks).forEach((key) => {
    const at = pct(marks[key]).toFixed(1) + '%';
    qa(`.tick[data-ci="${key}"]`).forEach((t) => { t.style.left = at; });
    const labs = qa(`.lab[data-ci="${key}"]`);
    labs.forEach((l) => { l.style.left = at; });
    if (labs.length) labs[0].textContent = labels[key];
  });
}

function applyAll(root, kpi) {
  if (!kpi) return;
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope) return;
  const d = derived(kpi);
  paintBinds(scope, kpi, d);
  paintSufficiency(scope, kpi);
  paintCharts(scope, kpi);
  paintCiScale(scope, kpi);
}

/* Confidence toggle: one wiring pass over the pills under `root`. Idempotent
 * (re-runs on every poll repaint would stack a listener per paint), so the
 * shell calls it once after mount and never again. */
function initConfidenceToggle(root) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope || !scope.querySelectorAll) return;
  const pills = scope.querySelectorAll('.conf-toggle .pill');
  const rows = scope.querySelectorAll('tr.ssr');
  if (!pills || !pills.forEach || pills.length === 0) return;
  if (pills[0].dataset && pills[0].dataset.av2wired) return;
  pills.forEach(function (p) {
    if (p.dataset) p.dataset.av2wired = '1';
    p.addEventListener('click', function () {
      pills.forEach(function (x) { x.classList.remove('active'); });
      p.classList.add('active');
      const c = p.dataset.conf;
      rows.forEach(function (r) {
        r.querySelector('.req').textContent = r.dataset['r' + c];
        r.querySelector('.rem').textContent = r.dataset['rem' + c];
        const pct = r.dataset['p' + c];
        r.querySelector('.pfill').style.width = pct + '%';
        r.querySelector('.pct').textContent = pct + '%';
      });
    });
  });
}

/* The single shell entry point: paint the cached poll payload into #analysis-v2.
 * Guards on the section so a missing node costs one skipped paint, not the
 * whole batch. `init` runs the one-time toggle wiring; plain `render` is the
 * per-poll repaint. */
function renderAnalysisV2(root, kpi) {
  const scope = root
    || (typeof document !== 'undefined' && document.getElementById
      ? document.getElementById('analysis-v2') : null);
  if (!scope || !kpi) return;
  applyAll(scope, kpi);
}

/* ------------------------------------------------------------------ boot */

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    resolve, format, signClass, bandStyle, gateVerdicts, derived,
    chartScales, barsFromBins, bellPath, linePath, bandPath, applyAll,
    renderAnalysisV2, initConfidenceToggle,
  };
}
