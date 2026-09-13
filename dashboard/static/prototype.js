/* Sidebar-pages layout (#95 frame, #140 content move).
 *
 * #120 shipped the frame empty. This fills it with the dashboard's real
 * panels — by moving the live nodes, not by copying their markup. `app.js`
 * finds everything it renders into by id, and ids survive a change of parent,
 * so there is exactly one copy of every panel and no second render path to
 * keep in sync with `index.html`.
 *
 * This is the dashboard now. `/` serves it. `/prototype` stays in
 * `LAYOUT_PATHS` so the path the layout was reviewed through still lands on
 * the layout instead of on a tab row nobody uses any more.
 *
 * Wrapped in an IIFE: this file is loaded into the same global scope as
 * `app.js`, and two classic scripts sharing a top-level `const` name is a
 * load-time error that would take the whole page down.
 */
(function () {
'use strict';

const PAGES =['home', 'data-markets', 'strategy', 'trades', 'reports'];
const STORAGE_KEY = 'sh-proto-page';
/* The pinned rail is a preference, so it persists -- under its own key. One
 * shared slot would let pinning the rail forget the chosen page. */
const PIN_KEY = 'sh-proto-rail-pinned';
const PIN_ON = '1';
const DEFAULT_PAGE = 'home';
const LAYOUT_PATHS = ['/', '/prototype'];
const EXPLAINER_SRC = '/static/strategy_explainer.html';

/* Which live panel lands on which page.
 *
 * `icon` is SVG path data drawn at 16x16: a font glyph renders differently on
 * every machine, and an icon-only rail has nothing else to identify a page by.
 *
 * Every selector is an id `app.js` already renders into; a selector that no
 * longer matches is skipped rather than throwing, so a renamed panel costs one
 * empty slot instead of the whole layout. */
const PAGE_LAYOUT = [
  {
    page: 'home',
    label: 'Dashboard',
    icon: 'M2.4 2.4h4.4v4.4H2.4z M9.2 2.4h4.4v4.4H9.2z M2.4 9.2h4.4v4.4H2.4z M9.2 9.2h4.4v4.4H9.2z',
    note: 'Where the account stands right now.',
    // The bankroll strip is part of the portfolio card and travels with it.
    selectors: ['#live-ops-master-card', '#broker-portfolio-overview',
                '#orders-trades-card'],
  },
  {
    page: 'data-markets',
    label: 'Data & Markets',
    icon: 'M8 2.2c2.9 0 5 .8 5 1.8s-2.1 1.8-5 1.8-5-.8-5-1.8 2.1-1.8 5-1.8z M3 4v8c0 1 2.1 1.8 5 1.8s5-.8 5-1.8V4 M3 8c0 1 2.1 1.8 5 1.8s5-.8 5-1.8',
    note: 'What the Market Filter saw on its last scan.',
    selectors: ['#screener-header', '#kanban-carousel-container', '#market-inspection-card'],
  },
  {
    page: 'strategy',
    label: 'Strategy',
    icon: 'M8 1.6v12.8 M1.6 8h12.8 M8 4.6a3.4 3.4 0 1 0 0 6.8 3.4 3.4 0 0 0 0-6.8z',
    note: 'How the strategy is configured and what it has to clear.',
    selectors: ['#params-panel', '#analytics-gates'],
  },
  {
    page: 'trades',
    label: 'Trades & Positions',
    icon: 'M2.6 5.6h10.8 M10.4 2.6l3 3-3 3 M13.4 10.4H2.6 M5.6 7.4l-3 3 3 3',
    note: 'The services running the loop, and what they are doing.',
    selectors: ['#services-deck', '#event-ticker-card'],
  },
  {
    page: 'reports',
    label: 'Reports & Analytics',
    icon: 'M2.4 13.6h11.2 M4.6 11.4V7 M8 11.4V3.4 M11.4 11.4V8.6',
    note: 'The evidence behind the numbers on the Dashboard.',
    selectors: ['.stats-subnav-container', '#analytics-surface'],
  },
];

function pageId(page) {
  return 'page-' + page;
}

// A stored value is data, not a command: anything that is not one of the five
// known pages falls back to Home rather than hiding every panel.
function normalizePage(page) {
  return PAGES.includes(page) ? page : DEFAULT_PAGE;
}

function readStoredPage() {
  try {
    return normalizePage(window.localStorage.getItem(STORAGE_KEY));
  } catch (e) {
    return DEFAULT_PAGE;
  }
}

function storePage(page) {
  try {
    window.localStorage.setItem(STORAGE_KEY, page);
  } catch (e) {
    // A browser with storage disabled still navigates; it just forgets.
  }
}

// Stored state is data, not a command: anything but the on marker reads as
// unpinned, so a stale or hand-edited value collapses the rail instead of
// wedging it open.
function readStoredPin() {
  try {
    return window.localStorage.getItem(PIN_KEY) === PIN_ON;
  } catch (e) {
    return false;
  }
}

function storePin(pinned) {
  try {
    window.localStorage.setItem(PIN_KEY, pinned ? PIN_ON : '0');
  } catch (e) {
    // A browser with storage disabled still pins; it just forgets.
  }
}

/* Pinning is the desktop rail's own state. It deliberately does not touch
 * `proto-nav-open`: below 900px the rail is a drawer over the page, and a pin
 * that opened the drawer would cover the dashboard on a phone. */
function setRailPinned(pinned, doc) {
  const scope = doc || document;
  const body = scope.body;
  if (body && body.classList) {
    body.classList.toggle('proto-rail-pinned', !!pinned);
  }
  const pin = scope.getElementById('proto-rail-pin');
  if (pin) {
    pin.setAttribute('aria-pressed', pinned ? 'true' : 'false');
    pin.setAttribute('title', pinned ? 'Unpin sidebar' : 'Pin sidebar');
  }
  return !!pinned;
}

function showPage(page, doc) {
  const scope = doc || document;
  const target = normalizePage(page);

  for (const candidate of PAGES) {
    const section = scope.getElementById(pageId(candidate));
    if (section) section.hidden = candidate !== target;
  }

  const buttons = scope.querySelectorAll('.proto-nav-btn');
  buttons.forEach(button => {
    const isCurrent = button.dataset.page === target;
    if (isCurrent) {
      button.setAttribute('aria-current', 'page');
    } else {
      button.removeAttribute('aria-current');
    }
  });

  storePage(target);
  // The kanban measures its own scroll width, which reads as zero while the
  // page holding it is hidden. Re-measure once it is on screen.
  if (target === 'data-markets' && typeof updateKanbanNavButtons === 'function') {
    setTimeout(updateKanbanNavButtons, 60);
  }
  return target;
}

function setNavOpen(open, doc) {
  const scope = doc || document;
  const toggle = scope.getElementById('proto-nav-toggle');
  const body = scope.body;
  if (body && body.classList) {
    body.classList.toggle('proto-nav-open', !!open);
  }
  if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function initPrototype(doc) {
  const scope = doc || document;
  showPage(readStoredPage(), scope);

  let pinned = readStoredPin();
  setRailPinned(pinned, scope);
  const pin = scope.getElementById('proto-rail-pin');
  if (pin) {
    pin.addEventListener('click', () => {
      pinned = !pinned;
      setRailPinned(pinned, scope);
      storePin(pinned);
    });
  }

  scope.querySelectorAll('.proto-nav-btn').forEach(button => {
    button.addEventListener('click', () => {
      showPage(button.dataset.page, scope);
      // On a narrow window the rail is a drawer over the page, so a chosen
      // page has to close it or the operator cannot see what they picked.
      setNavOpen(false, scope);
    });
  });

  const toggle = scope.getElementById('proto-nav-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') !== 'true';
      setNavOpen(open, scope);
    });
  }
}

/* ── Layout mount ───────────────────────────────────────────────────────── */

const SVG_NS = 'http://www.w3.org/2000/svg';

/* Drawn, not written: `createElementNS` is the only way an `<svg>` built in JS
 * ends up in the SVG namespace, and an HTML-namespace `<svg>` renders as
 * nothing at all. */
function buildIcon(doc, pathData) {
  const svg = doc.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'proto-nav-icon');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const path = doc.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', pathData);
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'currentColor');
  path.setAttribute('stroke-width', '1.3');
  path.setAttribute('stroke-linecap', 'round');
  path.setAttribute('stroke-linejoin', 'round');
  svg.appendChild(path);
  return svg;
}

/* The label is clipped when the rail is collapsed, never removed: an
 * icon-only rail that drops its labels leaves a screen reader with seven
 * unnamed controls. */
function buildNavLabel(doc, text) {
  const label = doc.createElement('span');
  label.className = 'proto-nav-label';
  label.textContent = text;
  return label;
}

/* Where the operator goes next. Everything here opens in a new tab, so
 * leaving the rail never takes the running dashboard down with it — including
 * the research pages: they read stores outside data/orders.db and keep their
 * own documents (Hebrew RTL, their own poll loops, no control token), so they
 * are adopted by navigation rather than restyled into the shell. */
const RAIL_LINKS = [
  {
    href: '/tape',
    label: 'Price Tape',
    icon: 'M2.4 3.2h11.2 M2.4 8h11.2 M2.4 12.8h11.2 M4.4 3.2v9.6 M11.6 3.2v9.6',
  },
  {
    href: '/reversion',
    label: 'Reversion Test',
    icon: 'M2.6 12.6h10.8 M4.2 12.6V8.2 M13.4 3.4l-3 3 3 3 M13.4 6.4H6.2',
  },
  {
    href: 'https://polymarket.com/markets',
    label: 'Polymarket',
    icon: 'M8 1.6a6.4 6.4 0 1 0 0 12.8A6.4 6.4 0 0 0 8 1.6z M1.6 8h12.8 '
          + 'M8 1.6a9.6 9.6 0 0 1 0 12.8 M8 1.6a9.6 9.6 0 0 0 0 12.8',
  },
  {
    href: 'https://github.com/AI-Degen-69/spread-hunter-live',
    label: 'Repository',
    icon: 'M6.2 13.4c-2.6.8-2.6-1.3-3.6-1.6 M9.8 14.4v-2.3c0-.7.2-1 .5-1.2 '
          + '-2.3-.3-3.9-1.1-3.9-3.9 0-1 .3-1.7.8-2.3-.1-.3-.4-1.2.1-2.3 0 0 .7-.2 '
          + '2.3.9a7.6 7.6 0 0 1 4 0c1.6-1.1 2.3-.9 2.3-.9.5 1.1.2 2 .1 2.3.5.6.8 '
          + '1.3.8 2.3 0 2.8-1.6 3.6-3.9 3.9.3.3.5.8.5 1.5v2',
  },
];

function buildRailPin(doc) {
  const pin = doc.createElement('button');
  pin.type = 'button';
  pin.id = 'proto-rail-pin';
  pin.className = 'proto-rail-pin';
  pin.setAttribute('aria-pressed', 'false');
  pin.setAttribute('title', 'Pin sidebar');
  pin.appendChild(buildIcon(doc, 'M2.5 3.5h11 M2.5 8h6 M2.5 12.5h11'));
  pin.appendChild(buildNavLabel(doc, 'Pin sidebar'));
  return pin;
}

function buildRailDivider(doc) {
  const divider = doc.createElement('div');
  divider.className = 'proto-rail-divider';
  divider.setAttribute('role', 'presentation');
  return divider;
}

function buildRailLinks(doc) {
  const list = doc.createElement('ul');
  list.className = 'proto-nav proto-rail-links';
  list.setAttribute('role', 'list');

  for (const entry of RAIL_LINKS) {
    const item = doc.createElement('li');
    const link = doc.createElement('a');
    link.className = 'proto-nav-btn';
    link.href = entry.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.title = entry.label;
    link.appendChild(buildIcon(doc, entry.icon));
    link.appendChild(buildNavLabel(doc, entry.label));
    item.appendChild(link);
    list.appendChild(item);
  }
  return list;
}

function buildSidebar(doc) {
  const nav = doc.createElement('nav');
  nav.id = 'proto-sidebar';
  nav.className = 'proto-sidebar';
  nav.setAttribute('aria-label', 'Pages');

  const list = doc.createElement('ul');
  list.className = 'proto-nav';
  list.setAttribute('role', 'list');

  for (const entry of PAGE_LAYOUT) {
    const item = doc.createElement('li');
    const button = doc.createElement('button');
    button.type = 'button';
    button.className = 'proto-nav-btn';
    button.dataset.page = entry.page;
    // The collapsed rail shows icons only, so the name the pointer asks for
    // is the one the browser draws itself.
    button.title = entry.label;

    button.appendChild(buildIcon(doc, entry.icon));
    button.appendChild(buildNavLabel(doc, entry.label));
    item.appendChild(button);
    list.appendChild(item);
  }

  nav.appendChild(buildRailPin(doc));
  nav.appendChild(list);
  nav.appendChild(buildRailDivider(doc));
  nav.appendChild(buildRailLinks(doc));
  return nav;
}

/* Every selector is resolved before anything moves. Appending a panel detaches
 * it from the document, and `querySelector` does not see inside a detached
 * subtree -- so a later selector pointing at a node under an earlier one would
 * silently find nothing and its page would come up short. */
function resolvePanels(doc) {
  const found = {};
  for (const entry of PAGE_LAYOUT) {
    found[entry.page] = entry.selectors
      .map(selector => doc.querySelector(selector))
      .filter(Boolean);
  }
  return found;
}

function buildPage(doc, entry, panels) {
  const section = doc.createElement('section');
  section.id = pageId(entry.page);
  section.className = 'proto-page';
  section.hidden = true;
  section.setAttribute('aria-labelledby', pageId(entry.page) + '-title');

  const title = doc.createElement('h1');
  title.id = pageId(entry.page) + '-title';
  title.className = 'font-display proto-page-title';
  title.textContent = entry.label;

  const note = doc.createElement('p');
  note.className = 'proto-page-note';
  note.textContent = entry.note;

  section.appendChild(title);
  section.appendChild(note);

  for (const panel of panels || []) {
    section.appendChild(panel);
  }

  if (entry.page === 'strategy') section.appendChild(buildExplainer(doc));
  return section;
}

/* The explainer is a whole page of its own. Framing it keeps one copy of that
 * document instead of forking its markup into the dashboard. */
function buildExplainer(doc) {
  const card = doc.createElement('div');
  card.className = 'card proto-explainer-card';
  card.id = 'proto-strategy-explainer';

  const heading = doc.createElement('div');
  heading.className = 'font-display proto-explainer-title';
  heading.textContent = 'HOW THE SPREAD HUNTER STRATEGY WORKS';

  const frame = doc.createElement('iframe');
  frame.className = 'proto-explainer-frame';
  frame.setAttribute('title', 'Strategy explainer');
  frame.setAttribute('loading', 'lazy');
  frame.src = EXPLAINER_SRC;

  card.appendChild(heading);
  card.appendChild(frame);
  return card;
}

function mountSidebarLayout(doc) {
  const scope = doc || document;
  const container = scope.querySelector('.container');
  if (!container || scope.getElementById('proto-sidebar')) return null;

  const shell = scope.createElement('div');
  shell.className = 'proto-shell';

  const pages = scope.createElement('main');
  pages.id = 'proto-page-body';
  pages.className = 'proto-pages';

  const panels = resolvePanels(scope);
  shell.appendChild(buildSidebar(scope));
  for (const entry of PAGE_LAYOUT) {
    pages.appendChild(buildPage(scope, entry, panels[entry.page]));
  }
  shell.appendChild(pages);
  container.appendChild(shell);

  // The tab shells stay in the document, emptied. `app.js` toggles them by id
  // and reads `#tab-3.hidden` before it will scroll the kanban, so removing
  // them would break the keyboard navigation the kanban still needs. Unhiding
  // them costs nothing: they have no children left.
  for (const tab of ['tab-1', 'tab-2', 'tab-3']) {
    const section = scope.getElementById(tab);
    if (section) section.hidden = false;
  }

  const tabs = scope.querySelector('.tab-switcher');
  if (tabs) tabs.hidden = true;

  // Inside `.brand`, not as a third child of the header: the header is a
  // space-between flex, and a third child pushed the wordmark into the middle
  // of the bar at the widths where the toggle actually appears.
  const brand = scope.querySelector('header .brand');
  if (brand) brand.insertBefore(buildNavToggle(scope), brand.firstChild);

  if (scope.body && scope.body.classList) {
    scope.body.classList.add('proto-body');
  }

  // The analytics sub-nav filters the page it sits on, and the panels moved a
  // moment ago. Re-check which of its views still have something to show.
  if (typeof pruneStatsSubnav === 'function') pruneStatsSubnav();
  return shell;
}

function buildNavToggle(doc) {
  const toggle = doc.createElement('button');
  toggle.type = 'button';
  toggle.id = 'proto-nav-toggle';
  toggle.className = 'proto-hamburger';
  toggle.setAttribute('aria-label', 'Toggle navigation');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'proto-sidebar');
  for (let i = 0; i < 3; i += 1) toggle.appendChild(doc.createElement('span'));
  return toggle;
}

function shouldMount(loc) {
  if (!loc) return false;
  const path = String(loc.pathname || '').replace(/\/+$/, '') || '/';
  return LAYOUT_PATHS.includes(path);
}

if (typeof module === 'undefined' || !module.exports) {
  if (shouldMount(window.location)) {
    mountSidebarLayout(document);
    initPrototype(document);
  }
}

// Node-only: lets tests drive the navigation and read the layout map without a
// browser.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    PAGES, DEFAULT_PAGE, PAGE_LAYOUT, LAYOUT_PATHS,
    normalizePage, showPage, setNavOpen, setRailPinned, initPrototype,
    mountSidebarLayout, shouldMount, resolvePanels,
  };
}
})();
