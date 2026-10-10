/* Neon Tokyo — Dashboard App
 * Binds the existing spread-hunter API to the Neon Tokyo layout.
 * Vanilla JS, no framework. Mirrors the data contract of the original app.js.
 */
'use strict';

const POLL_INTERVAL_MS = 5000;
const MAX_FAILURES = 3;

let pollTimer = null;
let lastKpi = null;
let lastState = null;
let lastStatus = null;
let lastMarkets = null;
let consecutiveFailures = 0;
let currentTab = 'markets';
let currentRange = '1d';
let searchFilter = '';
let pinned = false;

// ── Utilities ──

function fmt$(v, decimals) {
  if (decimals === undefined) decimals = 2;
  if (v == null || isNaN(v)) return '$' + (0).toFixed(decimals);
  return '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtAge(seconds) {
  if (seconds == null || isNaN(seconds)) return '--';
  seconds = Math.floor(seconds);
  if (seconds < 60) return seconds + 's';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h + 'h ' + m + 'm';
}

function fmtDuration(seconds) {
  if (!seconds || isNaN(seconds)) return '--';
  seconds = Math.floor(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return h + 'h ' + m + 'm';
  return m + 'm';
}

function fmtTime(ts) {
  if (!ts) return '--';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function fmtDate(ts) {
  if (!ts) return '--';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' +
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function escHtml(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setBar(id, pct) {
  const el = document.getElementById(id);
  if (el) el.style.width = Math.max(0, Math.min(100, pct)) + '%';
}

// ── API ──

async function apiGet(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(path + ' -> ' + resp.status);
  return resp.json();
}

async function apiPost(path) {
  const resp = await fetch(path, { method: 'POST' });
  if (!resp.ok) throw new Error(path + ' -> ' + resp.status);
  return resp.json().catch(function () { return {}; });
}

// ── Live-state vocabulary ──

function stateKey(running, ageSec, thresholds) {
  const t = thresholds || { degraded: 15, down: 60 };
  if (running === false || running === null || running === undefined) return 'stopped';
  if (ageSec == null) return 'unknown';
  if (ageSec > t.down) return 'down';
  if (ageSec > t.degraded) return 'degraded';
  return 'running';
}

function stateDotColor(state) {
  switch (state) {
    case 'running': return '#10b981';
    case 'degraded': return '#f59e0b';
    case 'down': return '#f43f5e';
    default: return '#64748b';
  }
}

// ── Renderers ──

function renderHeader(status) {
  if (!status) return;
  if (status.wallet) setText('wallet-addr', status.wallet);
  setText('run-id', status.run_id || status.db_label || '--');
  if (status.run_duration_sec) setText('run-duration', fmtDuration(status.run_duration_sec));
}

function renderHealth(status) {
  const trigger = document.getElementById('health-trigger');
  const label = document.getElementById('health-label');
  const rows = document.getElementById('health-rows');
  if (!trigger || !label || !rows) return;

  const services = (status && status.services) || {};
  const keys = Object.keys(services);
  let worst = 'running';
  let activeCount = 0;

  keys.forEach(function (k) {
    const svc = services[k];
    const st = stateKey(svc.running, svc.age_sec, svc.thresholds);
    if (st === 'down') worst = 'down';
    else if (st === 'degraded' && worst !== 'down') worst = 'degraded';
    if (svc.running) activeCount++;
  });

  trigger.className = 'nt-health-trigger' + (worst === 'down' ? ' down' : worst === 'degraded' ? ' warn' : '');

  if (keys.length === 0) {
    label.textContent = 'No services';
  } else {
    const suffix = ' (' + activeCount + '/' + keys.length + (worst === 'running' ? ' Active)' : ')');
    label.textContent = worst === 'running' ? 'All Systems Operational' + suffix
      : worst === 'degraded' ? 'Degraded' + suffix
      : 'System Down' + suffix;
  }

  rows.innerHTML = keys.map(function (k) {
    const svc = services[k];
    const st = stateKey(svc.running, svc.age_sec, svc.thresholds);
    const color = stateDotColor(st);
    const age = svc.age_sec;
    const ageText = (st === 'running' || st === 'degraded' || st === 'down') && age != null
      ? ' (' + fmtAge(age) + ')' : '';
    return '<div class="nt-popover-row">' +
      '<span class="nt-popover-label"><span class="nt-popover-dot" style="background:' + color + '"></span>' + escHtml(k.toUpperCase()) + '</span>' +
      '<span class="nt-popover-state" style="color:' + color + '">' + st.toUpperCase() + ageText + '</span>' +
      '</div>';
  }).join('');
}

function renderAccount(kpi) {
  if (!kpi || !kpi.portfolio) return;
  const p = kpi.portfolio;

  const equity = p.equity_usd != null ? p.equity_usd : (p.total_value_usd || 0);
  const cash = p.cash_usd != null ? p.cash_usd : (p.available_usdc || 0);
  const held = p.held_usd != null ? p.held_usd : (p.positions_value_usd || 0);
  const bankroll = p.starting_bankroll != null ? p.starting_bankroll : (p.bankroll || 0);
  const pnl = p.realized_pnl_usd != null ? p.realized_pnl_usd : (p.pnl_usd || 0);

  setText('total-equity', fmt$(equity));
  setText('cash-available', fmt$(cash));
  setText('held-positions', fmt$(held));
  setText('bankroll', fmt$(bankroll));

  const cashPct = equity > 0 ? (cash / equity * 100) : 100;
  const heldPct = equity > 0 ? (held / equity * 100) : 0;
  setText('cash-pct', cashPct.toFixed(1) + '% Liquid');
  setText('held-pct', heldPct.toFixed(1) + '% of Equity');
  setBar('cash-bar', cashPct);
  setBar('held-bar', heldPct);
  setText('exposure-label', held > 0 ? 'Exposed' : 'Zero Exposure');

  const badge = document.getElementById('pnl-badge');
  const pnlText = document.getElementById('pnl-text');
  if (badge && pnlText) {
    badge.style.display = 'inline-flex';
    const isPos = pnl >= 0;
    badge.style.background = isPos ? 'rgba(16,185,129,0.1)' : 'rgba(244,63,94,0.1)';
    badge.style.border = '1px solid ' + (isPos ? 'rgba(16,185,129,0.35)' : 'rgba(244,63,94,0.35)');
    badge.style.color = isPos ? 'var(--nt-green)' : 'var(--nt-rose)';
    const pct = bankroll > 0 ? (pnl / bankroll * 100) : 0;
    pnlText.textContent = (isPos ? '+' : '') + fmt$(pnl) + ' (' + (isPos ? '+' : '') + pct.toFixed(2) + '%) All-Time';
  }

  const runBadge = document.getElementById('run-pnl-badge');
  if (runBadge) {
    const runId = kpi.run_id || (lastStatus && lastStatus.run_id) || '--';
    const isPos = pnl >= 0;
    runBadge.textContent = 'RUN #' + runId + ' • ' + (isPos ? '+' : '') + fmt$(pnl) + ' net';
    runBadge.style.background = isPos ? 'rgba(16,185,129,0.1)' : 'rgba(244,63,94,0.1)';
    runBadge.style.border = '1px solid ' + (isPos ? 'rgba(16,185,129,0.3)' : 'rgba(244,63,94,0.3)');
    runBadge.style.color = isPos ? 'var(--nt-green)' : 'var(--nt-rose)';
  }
}

function renderTelemetry(kpi) {
  if (!kpi) return;
  const ta = kpi.trade_analytics || {};
  const rp = kpi.run_profitability || {};

  const wins = ta.wins != null ? ta.wins : (rp.wins || 0);
  const losses = ta.losses != null ? ta.losses : (rp.losses || 0);
  const total = wins + losses;
  const winRate = total > 0 ? (wins / total * 100) : 0;

  setText('win-rate', total > 0 ? winRate.toFixed(1) + '%' : '--');
  setBar('win-rate-bar', winRate);
  setText('win-loss-count', wins + ' Wins / ' + losses + ' Losses');
  setText('win-rate-label', winRate >= 60 ? 'High Edge' : winRate >= 50 ? 'Positive Edge' : 'Needs Work');

  const pf = ta.profit_factor != null ? ta.profit_factor : rp.profit_factor;
  setText('profit-factor', pf != null ? pf.toFixed(2) + 'x' : '--');
  setText('sharpe', ta.sharpe != null ? ta.sharpe.toFixed(2) : '--');
  setText('sr-ratio', ta.sharpe_ratio != null ? ta.sharpe_ratio.toFixed(2) : '--');

  const realized = ta.realized_pnl_usd != null ? ta.realized_pnl_usd
    : (rp.realized_pnl_usd != null ? rp.realized_pnl_usd : (kpi.portfolio && kpi.portfolio.realized_pnl_usd) || 0);
  setText('realized-spread', (realized >= 0 ? '+' : '') + fmt$(realized));
  const avg = ta.avg_spread_per_close != null ? ta.avg_spread_per_close : ta.avg_realized_spread;
  setText('avg-spread', avg != null ? 'Avg ' + fmt$(avg) + ' / close' : '--');

  const funnel = kpi.funnel || {};
  const quoted = funnel.graduated != null ? funnel.graduated : (ta.quoted_markets || 0);
  setText('quoted-markets', quoted + ' Markets');
}

function renderEquityChart(kpi) {
  const series = kpi && kpi.equity_series;
  if (!series || series.length < 2) return;

  const now = Date.now() / 1000;
  const ranges = { '1d': 86400, '1w': 604800, '1m': 2592000, 'all': Infinity };
  const cutoff = now - (ranges[currentRange] || Infinity);
  const pts = series.filter(function (p) { return p.ts >= cutoff; });
  if (pts.length < 2) return;

  const W = 700, H = 200, PAD_B = 20;
  const vals = pts.map(function (p) { return p.v; });
  const minV = Math.min.apply(null, vals.concat([0]));
  const maxV = Math.max.apply(null, vals);
  const range = (maxV - minV) || 1;
  const ts0 = pts[0].ts, ts1 = pts[pts.length - 1].ts;
  const tRange = (ts1 - ts0) || 1;

  function x(t) { return ((t - ts0) / tRange) * W; }
  function y(v) { return H - PAD_B - ((v - minV) / range) * (H - PAD_B - 10); }

  const grid = document.getElementById('chart-grid');
  if (grid) {
    let gridHtml = '';
    [0.25, 0.5, 0.75].forEach(function (f) {
      const gy = 10 + f * (H - PAD_B - 10);
      gridHtml += '<line stroke="#1a2538" stroke-dasharray="3 3" stroke-width="1" x1="0" x2="' + W + '" y1="' + gy + '" y2="' + gy + '"/>';
    });
    grid.innerHTML = gridHtml;
  }

  let lineD = '';
  pts.forEach(function (p, i) {
    const px = x(p.ts).toFixed(1), py = y(p.v).toFixed(1);
    lineD += (i === 0 ? 'M' : 'L') + ' ' + px + ',' + py + ' ';
  });
  const lastX = x(pts[pts.length - 1].ts).toFixed(1);
  const firstX = x(pts[0].ts).toFixed(1);
  const areaD = lineD + 'L' + lastX + ',' + H + ' L' + firstX + ',' + H + ' Z';

  const lineEl = document.getElementById('chart-line');
  const areaEl = document.getElementById('chart-area');
  if (lineEl) lineEl.setAttribute('d', lineD);
  if (areaEl) areaEl.setAttribute('d', areaD);

  const lastPt = pts[pts.length - 1];
  const dotO = document.getElementById('chart-dot-outer');
  const dotI = document.getElementById('chart-dot-inner');
  if (dotO && dotI) {
    const dx = x(lastPt.ts), dy = y(lastPt.v);
    dotO.setAttribute('cx', dx); dotO.setAttribute('cy', dy);
    dotI.setAttribute('cx', dx); dotI.setAttribute('cy', dy);
    dotO.style.display = ''; dotI.style.display = '';
  }

  const xAxis = document.getElementById('chart-x-axis');
  if (xAxis) {
    const count = Math.min(4, pts.length);
    const labels = [];
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(i * (pts.length - 1) / (count - 1 || 1));
      labels.push('<span>' + fmtDate(pts[idx].ts) + '</span>');
    }
    xAxis.innerHTML = labels.join('');
  }
}

function renderTable(state, markets) {
  const tbody = document.getElementById('table-body');
  const headRow = document.getElementById('table-head-row');
  if (!tbody) return;

  let rows = [];
  let headers = [];

  if (currentTab === 'markets') {
    headers = ['Age / Stamp', 'Market', 'Category', 'UP Quote', 'DOWN Quote', 'Pair Cost', 'Edge', 'Volume', 'Status'];
    const list = extractMarketList(markets);
    rows = list.map(function (m) {
      const up = num(m.up_price != null ? m.up_price : m.up);
      const down = num(m.down_price != null ? m.down_price : m.down);
      const pairCost = up + down;
      const edge = (1 - pairCost) * 100;
      return {
        age: m.age_sec != null ? m.age_sec : m.first_seen_sec,
        stamp: m.ts || m.timestamp,
        name: m.title || m.question || m.market || '--',
        sub: m.subtitle || m.classification || '',
        category: m.category || m.sport || '--',
        up: up, down: down, pairCost: pairCost, edge: edge,
        volume: num(m.volume_24h != null ? m.volume_24h : m.volume),
        status: m.status || m.state || 'active',
      };
    });
  } else if (currentTab === 'orders') {
    headers = ['Age / Stamp', 'Market', 'Side', 'Price', 'Size', 'Filled', 'Status', '', ''];
    const orders = (state && state.orders) || [];
    rows = orders.map(function (o) {
      return {
        age: o.age_sec, stamp: o.ts,
        name: o.market_title || o.title || '--',
        sub: o.side || '',
        category: o.token_side || '',
        up: num(o.price), down: num(o.size), pairCost: num(o.filled),
        edge: o.status || 'resting', volume: 0,
        status: o.status || 'resting',
      };
    });
  } else if (currentTab === 'positions') {
    headers = ['Opened', 'Market', 'State', 'Size', 'Avg Price', 'Value', 'P&L', '', ''];
    const pairs = (state && state.pairs) || [];
    rows = pairs.map(function (p) {
      return {
        age: null, stamp: p.opened_ts || p.ts,
        name: p.market_title || p.title || '--',
        sub: p.pair_state || '',
        category: p.market_class || '',
        up: num(p.size_shares), down: num(p.avg_price), pairCost: num(p.value_usd),
        edge: num(p.pnl_usd), volume: 0,
        status: p.hedge || 'partial',
      };
    });
  } else if (currentTab === 'closed') {
    headers = ['Closed', 'Market', 'Reason', 'P&L', 'Duration', 'Fills', '', '', ''];
    const closed = (kpi_closed());
    rows = closed.map(function (c) {
      return {
        age: null, stamp: c.closed_ts || c.ts,
        name: c.title || c.market || '--',
        sub: c.close_reason || '',
        category: c.category || '',
        up: num(c.pnl_usd), down: num(c.duration_sec), pairCost: num(c.fills),
        edge: num(c.realized_usd), volume: 0,
        status: c.outcome || 'closed',
      };
    });
  }

  if (searchFilter) {
    const q = searchFilter.toLowerCase();
    rows = rows.filter(function (r) {
      return (r.name || '').toLowerCase().indexOf(q) !== -1 || (r.category || '').toLowerCase().indexOf(q) !== -1;
    });
  }

  if (headRow) {
    headRow.innerHTML = headers.map(function (h) { return '<th>' + h + '</th>'; }).join('');
  }

  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="' + headers.length + '"><div class="nt-empty"><div class="nt-empty-title">No data</div><div class="nt-empty-msg">Nothing to show right now.</div></div></td></tr>';
  } else {
    tbody.innerHTML = rows.slice(0, 50).map(function (r) {
      const isQuoting = r.status === 'quoting' || r.status === 'active' || r.status === 'running';
      const statusClass = isQuoting ? 'quoting' : 'resting';
      const statusLabel = String(r.status || '--').toUpperCase();
      const ageText = r.age != null ? fmtAge(r.age) : (r.stamp ? fmtTime(r.stamp) : '--');
      const stampText = r.stamp ? fmtDate(r.stamp) : '';
      let edgeHtml;
      if (currentTab === 'markets') {
        edgeHtml = '<span class="' + (r.edge >= 0 ? 'nt-edge' : 'nt-edge negative') + '">' + (r.edge >= 0 ? '+' : '') + r.edge.toFixed(1) + '&cent;</span>';
      } else if (typeof r.edge === 'number') {
        edgeHtml = '<span class="' + (r.edge >= 0 ? 'nt-edge' : 'nt-edge negative') + '">' + (r.edge >= 0 ? '+' : '') + fmt$(r.edge) + '</span>';
      } else {
        edgeHtml = escHtml(r.edge);
      }
      return '<tr>' +
        '<td class="nt-tabular" style="color:var(--nt-text-muted);font-size:11px">' + ageText + (stampText ? '<div style="font-size:10px;color:var(--nt-text-faint)">' + stampText + '</div>' : '') + '</td>' +
        '<td><div class="nt-market-name">' + escHtml(r.name) + '</div>' + (r.sub ? '<div class="nt-market-sub">' + escHtml(r.sub) + '</div>' : '') + '</td>' +
        '<td><span class="nt-category-tag">' + escHtml(r.category) + '</span></td>' +
        '<td class="nt-price-up">' + fmt$(r.up) + '</td>' +
        '<td class="nt-price-down">' + fmt$(r.down) + '</td>' +
        '<td class="nt-tabular" style="color:var(--nt-text-secondary)">' + fmt$(r.pairCost) + '</td>' +
        '<td>' + edgeHtml + '</td>' +
        '<td class="nt-tabular" style="color:var(--nt-text-muted)">' + (r.volume > 0 ? fmt$(r.volume, 0) : '--') + '</td>' +
        '<td style="text-align:right"><span class="nt-status-pill ' + statusClass + '"><span class="nt-status-dot"></span>' + escHtml(statusLabel) + '</span></td>' +
        '</tr>';
    }).join('');
  }

  setText('table-summary', 'Showing ' + rows.length + ' entries');
  const quoting = rows.filter(function (r) { return r.status === 'quoting' || r.status === 'active' || r.status === 'running'; }).length;
  setText('quoting-count', quoting);
  setText('resting-count', rows.length - quoting);

  setText('count-markets', extractMarketList(markets).length);
  setText('count-orders', ((state && state.orders) || []).length);
  setText('count-positions', ((state && state.pairs) || []).length);
  setText('count-closed', kpi_closed().length);

  const totalVol = extractMarketList(markets).reduce(function (s, m) {
    return s + num(m.volume_24h != null ? m.volume_24h : m.volume);
  }, 0);
  setText('total-volume', totalVol > 0 ? fmt$(totalVol, 0) + ' USDC' : '--');
}

function num(v) { const n = Number(v); return isNaN(n) ? 0 : n; }

function extractMarketList(markets) {
  if (!markets) return [];
  if (Array.isArray(markets)) return markets;
  if (Array.isArray(markets.markets)) return markets.markets;
  if (Array.isArray(markets.active)) return markets.active;
  if (Array.isArray(markets.data)) return markets.data;
  return [];
}

function kpi_closed() {
  if (!lastKpi) return [];
  const c = lastKpi.closed_markets || lastKpi.resolved_markets;
  return Array.isArray(c) ? c : [];
}

// ── Poll loop ──

async function poll() {
  try {
    const results = await Promise.all([
      apiGet('/api/kpi').catch(function () { return null; }),
      apiGet('/api/state').catch(function () { return null; }),
      apiGet('/api/system/status').catch(function () { return null; }),
    ]);
    lastKpi = results[0];
    lastState = results[1];
    lastStatus = results[2];

    let markets = null;
    try { markets = await apiGet('/api/active-markets'); } catch (_) { /* optional */ }
    lastMarkets = markets;

    consecutiveFailures = 0;
    const banner = document.getElementById('stale-banner');
    if (banner) banner.classList.remove('visible');
    setText('feed-status', 'Live');
    const dot = document.getElementById('feed-dot');
    if (dot) dot.style.background = 'var(--nt-green)';

    if (lastStatus) { renderHeader(lastStatus); renderHealth(lastStatus); }
    if (lastKpi) { renderAccount(lastKpi); renderTelemetry(lastKpi); renderEquityChart(lastKpi); }
    renderTable(lastState, lastMarkets);
  } catch (err) {
    consecutiveFailures++;
    if (consecutiveFailures >= MAX_FAILURES) {
      const banner = document.getElementById('stale-banner');
      if (banner) banner.classList.add('visible');
      setText('feed-status', 'Stale');
      const dot = document.getElementById('feed-dot');
      if (dot) dot.style.background = 'var(--nt-amber)';
    }
  }
}

// ── Events ──

function initEvents() {
  const sidebar = document.getElementById('sidebar');
  const pinBtn = document.getElementById('pin-toggle');
  const pinBadge = document.getElementById('pin-badge');

  pinned = localStorage.getItem('nt-sidebar-pinned') === '1';
  if (sidebar) sidebar.classList.toggle('collapsed', !pinned);
  if (pinBadge) pinBadge.textContent = pinned ? 'PINNED' : 'AUTO';

  if (pinBtn) {
    pinBtn.addEventListener('click', function () {
      pinned = !pinned;
      localStorage.setItem('nt-sidebar-pinned', pinned ? '1' : '0');
      if (sidebar) sidebar.classList.toggle('collapsed', !pinned);
      if (pinBadge) pinBadge.textContent = pinned ? 'PINNED' : 'AUTO';
    });
  }

  if (sidebar) {
    sidebar.addEventListener('mouseenter', function () { sidebar.classList.remove('collapsed'); });
    sidebar.addEventListener('mouseleave', function () { if (!pinned) sidebar.classList.add('collapsed'); });
  }

  document.querySelectorAll('.nt-nav-item').forEach(function (item) {
    item.addEventListener('click', function (e) {
      e.preventDefault();
      document.querySelectorAll('.nt-nav-item').forEach(function (i) { i.classList.remove('active'); });
      item.classList.add('active');
      localStorage.setItem('nt-page', item.dataset.page);
    });
  });

  const savedPage = localStorage.getItem('nt-page');
  if (savedPage) {
    const item = document.querySelector('.nt-nav-item[data-page="' + savedPage + '"]');
    if (item) {
      document.querySelectorAll('.nt-nav-item').forEach(function (i) { i.classList.remove('active'); });
      item.classList.add('active');
    }
  }

  document.querySelectorAll('#range-selector .nt-segment-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('#range-selector .nt-segment-btn').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      currentRange = btn.dataset.range;
      if (lastKpi) renderEquityChart(lastKpi);
    });
  });

  document.querySelectorAll('#table-tabs .nt-segment-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('#table-tabs .nt-segment-btn').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      currentTab = btn.dataset.tab;
      renderTable(lastState, lastMarkets);
    });
  });

  const searchInput = document.getElementById('search-input');
  if (searchInput) {
    searchInput.addEventListener('input', function (e) {
      searchFilter = e.target.value.trim();
      renderTable(lastState, lastMarkets);
    });
  }

  const syncBtn = document.getElementById('btn-sync');
  if (syncBtn) {
    syncBtn.addEventListener('click', async function () {
      const orig = syncBtn.innerHTML;
      syncBtn.innerHTML = '<svg class="nt-spinning" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" stroke-linecap="round" stroke-linejoin="round"/></svg> SYNCING...';
      try { await apiPost('/api/system/sync'); } catch (_) { /* surfaced by poll */ }
      setTimeout(function () { syncBtn.innerHTML = orig; poll(); }, 1200);
    });
  }

  const stopBtn = document.getElementById('btn-stop');
  if (stopBtn) {
    stopBtn.addEventListener('click', async function () {
      if (!confirm('Stop the running stack? This halts all quoting.')) return;
      try { await apiPost('/api/system/stop'); } catch (_) { /* surfaced by poll */ }
      poll();
    });
  }

  const resetBtn = document.getElementById('btn-reset');
  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      if (!confirm('Reset dashboard UI preferences (sidebar pin, active page)?')) return;
      localStorage.removeItem('nt-sidebar-pinned');
      localStorage.removeItem('nt-page');
      location.reload();
    });
  }
}

// ── Boot ──

document.addEventListener('DOMContentLoaded', function () {
  initEvents();
  poll();
  pollTimer = setInterval(poll, POLL_INTERVAL_MS);
});
