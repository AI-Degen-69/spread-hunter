/* Data Analysis tab — the #541 canonical statistics page.
 *
 * Tab 2 stays frozen as the legacy page. Everything here renders into the
 * `#da-*` containers of the tab-4 panel, reusing the shared renderers from
 * app.js through their host-id / ids params (defaults keep the legacy tab
 * byte-identical). Numbers are never recomputed here: this module claims
 * registry ids and hands the same payload objects to the same producers.
 *
 * Conventions enforced by tests (tests/test_541_parity_ledger.py + T4 coverage):
 * - STAT_REGISTRY maps every rendered statistic to its ONE producer, block,
 *   and unit. `refOf` marks a contextual restatement (gate rows, sub-lines):
 *   traceable, never a second placement.
 * - claimBlock() throws when a block renders twice in one pass.
 * - Every card title carries its unit ($, c, %, bps, s, x) or is `bare`.
 */
'use strict';

/* id -> {producer (payload path), block (da host), section, unit} */
const STAT_REGISTRY = {
  /* Headline (own markup below) */
  'headline.mean_return_pct': { producer: 'trade_analytics.mean_return_pct', block: 'da-headline', section: 'overview', unit: '%' },
  'headline.n_closes': { producer: 'trade_analytics.n_closes', block: 'da-headline', section: 'overview', unit: 'bare' },
  'headline.required_observations': { producer: 'trade_analytics.required_observations', block: 'da-headline', section: 'overview', unit: 'bare' },
  /* PnL CI band card (producer: renderPnlCiReadout <- trade_analytics.mean_pnl_ci) */
  'pnl.mean_usd': { producer: 'trade_analytics.mean_pnl_ci.mean_usd', block: 'da-pnl-ci', section: 'pnl', unit: '$' },
  'pnl.levels': { producer: 'trade_analytics.mean_pnl_ci.levels', block: 'da-pnl-ci', section: 'pnl', unit: '$' },
  'pnl.verdict': { producer: 'trade_analytics.mean_pnl_ci.verdict', block: 'da-pnl-ci', section: 'pnl', unit: 'bare' },
  /* Sample sufficiency (producer: renderSampleSufficiency) */
  'sufficiency.statuses': { producer: 'trade_analytics.sample_size_sufficiency.statuses', block: 'da-sufficiency', section: 'sufficiency', unit: 'bare' },
  /* Execution funnel (producer: renderExecutionFunnel) */
  'funnel.worst_step': { producer: 'kpi.execution_funnel', block: 'da-funnel', section: 'funnel', unit: 'bare' },
  /* Decision gates (producer: decisionGatesHtml; observed values RESTATE the
     canonical cards below with their thresholds — registered as refs) */
  'gates.edge_viability': { producer: 'trade_analytics.ci90_lower_pct', block: 'da-gates', section: 'gates', unit: '%', refOf: 'robustness.ci90 (inclusive population; recorded finding)' },
  'gates.neutrality': { producer: 'trade_analytics.win_rate', block: 'da-gates', section: 'gates', unit: '%', refOf: 'quant.win_rate' },
  'gates.power_target': { producer: 'trade_analytics.required_observations', block: 'da-gates', section: 'gates', unit: 'bare', refOf: 'headline.required_observations' },
  'gates.markout': { producer: 'markout_samples + adverse_selection', block: 'da-gates', section: 'gates', unit: 'c', refOf: 'charts.markout' },
  'gates.drawdown_guard': { producer: 'trade_analytics.max_drawdown_pct', block: 'da-gates', section: 'gates', unit: '% (equity curve)' },
  /* Fill-path edge (producer: renderFillPathEdge) */
  'fillpath.merge_expectancy': { producer: 'statistical_analytics.fill_path.merge_expectancy_usd', block: 'da-fillpath', section: 'fillpath', unit: '$' },
  'fillpath.rescue_expectancy': { producer: 'statistical_analytics.fill_path.rescue_expectancy_usd', block: 'da-fillpath', section: 'fillpath', unit: '$' },
  'fillpath.rescue_rate': { producer: 'statistical_analytics.fill_path.rescue_rate', block: 'da-fillpath', section: 'fillpath', unit: '%' },
  'fillpath.break_even_rescue_rate': { producer: 'statistical_analytics.fill_path.break_even_rescue_rate', block: 'da-fillpath', section: 'fillpath', unit: '%' },
  'fillpath.net_expectancy': { producer: 'statistical_analytics.fill_path.net_expectancy_usd', block: 'da-fillpath', section: 'fillpath', unit: '$' },
  /* Rescue friction (producer: renderRescueFriction) */
  'rescue.sled_cents': { producer: 'statistical_analytics.friction.sled', block: 'da-rescue', section: 'rescue', unit: 'c' },
  'rescue.latency_drag_cents': { producer: 'statistical_analytics.friction.latency', block: 'da-rescue', section: 'rescue', unit: 'c' },
  'rescue.execution_drag_cents': { producer: 'statistical_analytics.friction.execution', block: 'da-rescue', section: 'rescue', unit: 'c' },
  'rescue.taker_cost_ratio': { producer: 'statistical_analytics.friction.tcdr', block: 'da-rescue', section: 'rescue', unit: '%' },
  'rescue.overshoot': { producer: 'statistical_analytics.friction.overshoot', block: 'da-rescue', section: 'rescue', unit: 'x' },
  /* Queue realism (producer: renderQueueRealism) */
  'queue.depth_cents': { producer: 'statistical_analytics.queue.depth', block: 'da-queue', section: 'queue', unit: 'c' },
  'queue.wait_sec': { producer: 'statistical_analytics.queue.wait', block: 'da-queue', section: 'queue', unit: 's' },
  /* Robustness (producer: renderRobustness) */
  'robustness.mae': { producer: 'statistical_analytics.mae', block: 'da-robustness', section: 'robustness', unit: 'c' },
  'robustness.ci95': { producer: 'statistical_analytics.mean_ci', block: 'da-robustness', section: 'robustness', unit: 'c' },
  'robustness.sqn': { producer: 'statistical_analytics.sqn', block: 'da-robustness', section: 'robustness', unit: 'bare' },
  /* Capital risk (producer: renderCapitalRisk; the recovery walk reads the
     VENUE ACCOUNT-value curve — labels on this tab say so) */
  'capital.deepest_dd': { producer: 'statistical_analytics.recovery.deepest_usd', block: 'da-capital', section: 'capital', unit: '$ (account curve)' },
  'capital.recovery_waits': { producer: 'statistical_analytics.recovery.lengths', block: 'da-capital', section: 'capital', unit: 'bare (marks)' },
  'capital.skewness': { producer: 'statistical_analytics.skew', block: 'da-capital', section: 'capital', unit: 'bare' },
  'capital.sortino': { producer: 'statistical_analytics.sortino.sortino_ratio', block: 'da-capital', section: 'capital', unit: 'bare' },
  /* Quant grid (producer: renderQuantRiskGrid) */
  'quant.expectancy': { producer: 'trade_analytics.expectancy_usd', block: 'da-quant', section: 'quant', unit: '$' },
  'quant.var95': { producer: 'trade_analytics.var_95_usd', block: 'da-quant', section: 'quant', unit: '$' },
  'quant.sharpe': { producer: 'trade_analytics.sharpe_ratio', block: 'da-quant', section: 'quant', unit: 'bare' },
  'quant.sortino_context': { producer: 'trade_analytics.sortino_ratio', block: 'da-quant', section: 'quant', unit: 'bare', refOf: 'capital.sortino' },
  'quant.kelly': { producer: 'trade_analytics.kelly_fraction', block: 'da-quant', section: 'quant', unit: '%' },
  'quant.win_rate': { producer: 'trade_analytics.win_rate + win_rate_ci95', block: 'da-quant', section: 'quant', unit: '%' },
  'quant.profit_factor': { producer: 'trade_analytics.profit_factor + payoff_ratio', block: 'da-quant', section: 'quant', unit: 'x' },
  /* Charts (producers: render*Chart <- statistical_analytics sections) */
  'charts.position_dist': { producer: 'statistical_analytics.position_returns', block: 'da-dist-container', section: 'charts', unit: '$' },
  'charts.pair_cost': { producer: 'statistical_analytics.pair_costs', block: 'da-paircost-container', section: 'charts', unit: '$' },
  'charts.monte_carlo': { producer: 'statistical_analytics.monte_carlo', block: 'da-mc-container', section: 'charts', unit: '$' },
  'charts.prob_bell': { producer: 'statistical_analytics.probability_bell', block: 'da-bell-container', section: 'charts', unit: '%' },
  'charts.markout': { producer: 'statistical_analytics.markouts', block: 'da-markout-container', section: 'charts', unit: 'c' },
};

/* Claim ledger: a block claimed twice in one pass is a second placement. */
let _daClaimedBlocks = null;

function resetStatLedger() {
  _daClaimedBlocks = new Set();
}

function claimBlock(block) {
  if (!_daClaimedBlocks) resetStatLedger();
  const ids = Object.keys(STAT_REGISTRY).filter((id) => STAT_REGISTRY[id].block === block);
  if (!ids.length) throw new Error('#541 registry: block has no stats: ' + block);
  if (_daClaimedBlocks.has(block)) throw new Error('#541 double-claim: block rendered twice: ' + block);
  _daClaimedBlocks.add(block);
  return ids;
}

/* The tab's single read of the sample-size requirement. */
function requiredDAObservations(ta) {
  return ta.required_observations != null ? Number(ta.required_observations) : 120;
}

function renderDAHeadline(kpi, n, required, winRate) {
  const ta = kpi.trade_analytics || {};
  const host = document.getElementById('da-headline');
  const pill = document.getElementById('da-sample-count');
  if (pill) pill.textContent = `${n} Closes · Scanned Polymarket Candidates`;
  if (!host) return;
  const nMeas = ta.n_measured_returns != null ? ta.n_measured_returns : null;
  const dwRet = ta.dollar_weighted_return_pct != null && n > 0
    ? fmtPct(ta.dollar_weighted_return_pct) : null;
  const progressPct = required > 0 ? Math.min(100, Math.round((n / required) * 100)) : 0;
  host.innerHTML = `
    <div class="da-tile">
      <div class="da-label">Mean Return Per Trade (%)</div>
      <div class="da-value mono ${n > 0 && ta.mean_return_pct != null ? signClass(ta.mean_return_pct) : ''}">${esc(ta.mean_return_pct != null && n > 0 ? fmtPct(ta.mean_return_pct) : 'unmeasured')}</div>
      <div class="da-hint">± ${ta.stdev_return_pct != null && n > 0 ? Number(ta.stdev_return_pct).toFixed(2) + '%' : 'unmeasured'} (σ)${dwRet != null ? ` · ${dwRet} dollar-weighted` : ' · dollar-weighted unmeasured'}</div>
    </div>
    <div class="da-tile">
      <div class="da-label">Executed Sample Size</div>
      <div class="da-value mono">${esc(`${n} Closes (${n > 0 ? (ta.wins != null ? ta.wins : 0) : 0}W / ${n > 0 ? (ta.losses != null ? ta.losses : 0) : 0}L)${nMeas != null ? ` · ${nMeas} measured` : ''}`)}</div>
      <div class="da-hint">Empirical Win Rate: ${n > 0 ? winRate.toFixed(1) + '%' : '0.0%'}</div>
    </div>
    <div class="da-tile">
      <div class="da-label">Sample Sufficiency Progress</div>
      <div class="da-value mono">${esc(`${n} / ${required} closes`)}</div>
      <div class="da-hint">${esc(progressPct + '% of the observations the gate needs')}</div>
    </div>`;
}

function renderDataAnalysis(kpi, status) {
  const surface = document.getElementById('da-surface');
  if (!surface || !kpi) return;
  resetStatLedger();

  const ta = kpi.trade_analytics || {};
  const stats = kpi.statistical_analytics || {};
  const p = kpi.portfolio || {};
  const nRaw = ta.n_closes != null ? ta.n_closes : ta.closes_count;
  const n = nRaw != null ? nRaw : 0;
  const required = requiredDAObservations(ta);
  const winRate = ta.win_rate != null ? ta.win_rate * 100 : 0.0;

  claimBlock('da-headline');
  renderDAHeadline(kpi, n, required, winRate);

  claimBlock('da-pnl-ci');
  renderPnlCiReadout(ta, 'da-pnl-ci');
  claimBlock('da-sufficiency');
  renderSampleSufficiency(ta, 'da-sufficiency');
  claimBlock('da-funnel');
  renderExecutionFunnel(kpi, 'da-funnel');

  claimBlock('da-gates');
  const gatesHost = document.getElementById('da-gates');
  if (gatesHost) {
    gatesHost.innerHTML = decisionGatesHtml(ta, stats, n, kpi);
    if (typeof typesetMath === 'function') typesetMath(gatesHost);
  }

  claimBlock('da-fillpath');
  renderFillPathEdge(stats, 'da-fillpath');
  claimBlock('da-rescue');
  renderRescueFriction(stats, 'da-rescue');
  claimBlock('da-queue');
  renderQueueRealism(stats, 'da-queue');
  claimBlock('da-robustness');
  renderRobustness(stats, 'da-robustness');
  claimBlock('da-capital');
  renderCapitalRisk(stats, 'da-capital', { curveName: 'account curve' });
  claimBlock('da-quant');
  renderQuantRiskGrid(ta, p, stats, 'da-quant');

  claimBlock('da-dist-container');
  renderPositionDistributionChart(stats, {
    container: 'da-dist-container', footer: 'da-dist-footer',
    badge: 'da-dist-badge', banner: 'da-dist-banner',
  });
  claimBlock('da-paircost-container');
  renderPairCostKdeChart(stats, {
    container: 'da-paircost-container', footer: 'da-paircost-footer', badge: 'da-paircost-badge',
  });
  claimBlock('da-mc-container');
  renderMonteCarloChart(stats, typeof currentMcCycles !== 'undefined' ? currentMcCycles : 100, {
    container: 'da-mc-container', footer: 'da-mc-footer',
  });
  claimBlock('da-bell-container');
  renderProbabilityBellChart(stats, {
    container: 'da-bell-container', footer: 'da-bell-footer', badge: 'da-bell-badge',
  });
  claimBlock('da-markout-container');
  renderMarkoutChart(stats, {
    container: 'da-markout-container', footer: 'da-markout-footer',
  });
}

// Node-only: lets tests reach the registry and the renderer. Dead code in the page.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    STAT_REGISTRY, claimBlock, resetStatLedger, requiredDAObservations,
    renderDAHeadline, renderDataAnalysis,
    claimedBlocks: () => Array.from(_daClaimedBlocks || []),
  };
}
