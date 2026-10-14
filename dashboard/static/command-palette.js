/* Command palette (#477) — the keyboard entry point to the dashboard.
 *
 * Cmd/Ctrl+K (or `/` outside a text field) opens a modal dialog over the
 * dashboard: a filterable list of entries with arrow-key selection. The shell
 * owns the keyboard while open — one capture-phase window listener stops every
 * background handler (reset Escape, kanban arrows) from reacting — traps focus
 * on the search input, and restores focus to the opener on close.
 *
 * Markup and styles reuse the reset-modal pattern (#reset-modal, `.modal-*`);
 * this file adds the behavior only. Browser mount is guarded the same way as
 * prototype.js; the offline harness (tests/js/command_palette_harness.cjs)
 * drives the exported functions instead.
 */
(function () {
  'use strict';

  var doc = null;
  var overlay = null;
  var search = null;
  var list = null;
  var opener = null;
  var entries = [];
  var visible = [];
  var highlight = -1;
  var rowId = 0;
  var initialized = false;

  /* ── Small DOM helpers ── */

  function editableTarget(target) {
    if (!target) return false;
    var tag = String(target.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
    return target.isContentEditable === true;
  }

  function resetModalShown() {
    var rm = doc.getElementById('reset-modal');
    return !!(rm && rm.classList && rm.classList.contains('show'));
  }

  function canFocus(el) {
    return !!el && (el.isConnected === undefined || el.isConnected) && !el.hidden;
  }

  function pickFocus(fallback) {
    if (canFocus(opener)) return opener;
    return canFocus(fallback) ? fallback : null;
  }

  /* ── Entry building ── */

  function inertEntry(label) {
    return { id: 'inert:' + label, label: label, group: null, inert: true, disabled: true, run: function () {} };
  }

  function buildEntries() {
    var out = [];
    // Pages come from the DOM, never a hardcoded list: the rail owns which
    // pages exist (six today). External rail links carry .proto-nav-btn
    // without data-page — the [data-page] selector keeps them out.
    var navs = doc.querySelectorAll('.proto-nav-btn[data-page]') || [];
    Array.prototype.forEach.call(navs, function (btn) {
      var page = btn.dataset && btn.dataset.page;
      if (!page) return;
      out.push({
        id: 'page:' + page,
        label: String(btn.textContent || '').trim() || page,
        group: 'Pages',
        run: function () {
          btn.click(); // the real sidebar click: persistence, aria-current, drawer
          closePalette({ focusEl: pickFocus(btn) });
        },
      });
    });
    var sync = doc.getElementById('btn-sync');
    if (sync) {
      out.push({
        id: 'action:sync',
        label: 'SYNC VENUE',
        group: 'Actions',
        disabled: !!sync.disabled,
        run: function () {
          sync.click();
          closePalette({ focusEl: pickFocus(sync) });
        },
      });
    }
    var reset = doc.getElementById('btn-reset');
    if (reset) {
      out.push({
        id: 'action:reset',
        label: 'RESET',
        sub: 'Opens the typed confirmation.',
        group: 'Actions',
        run: function () {
          closePalette({ restoreFocus: false }); // the modal takes focus itself
          reset.click();
        },
      });
    }
    return out;
  }

  /* ── Entry filtering ── */

  function filterEntries(list, query) {
    var q = String(query || '').trim().toLowerCase();
    if (!q) return list.slice();
    return list.filter(function (e) {
      return String(e.label || '').toLowerCase().indexOf(q) !== -1;
    });
  }

  /* ── List rendering ── */

  function clearList() {
    if (!list) return;
    if (typeof list.removeChild === 'function' && list.firstChild) {
      while (list.firstChild) list.removeChild(list.firstChild);
    } else if (list.children) {
      list.children.length = 0;
    }
  }

  function heading(text) {
    var h = doc.createElement('div');
    h.className = 'palette-group';
    h.setAttribute('role', 'presentation');
    h.textContent = text;
    return h;
  }

  function makeRow(entry) {
    var row = doc.createElement('div');
    row.className = 'palette-row';
    row.id = 'palette-opt-' + rowId;
    rowId += 1;
    row.setAttribute('role', 'option');
    row.dataset.label = entry.label;
    if (entry.sub) row.dataset.sub = entry.sub;
    if (entry.disabled || entry.inert) row.setAttribute('aria-disabled', 'true');
    var label = doc.createElement('div');
    label.textContent = entry.label;
    row.appendChild(label);
    if (entry.sub) {
      var sub = doc.createElement('div');
      sub.className = 'palette-sub';
      sub.textContent = entry.sub;
      row.appendChild(sub);
    }
    row.addEventListener('click', function () { activate(entry); });
    return row;
  }

  function renderRows() {
    clearList();
    rowId = 0;
    var group = null;
    visible.forEach(function (entry) {
      if (entry.group !== group) {
        group = entry.group;
        if (group) list.appendChild(heading(group));
      }
      list.appendChild(makeRow(entry));
    });
    highlight = -1;
    for (var i = 0; i < visible.length; i++) {
      if (selectableAt(i)) { setHighlight(i); break; }
    }
  }

  function selectableAt(i) {
    var e = visible[i];
    return !!e && !e.disabled && !e.inert;
  }

  function setHighlight(i) {
    if (i < 0 || i >= visible.length) return;
    if (highlight >= 0 && visible[highlight]) {
      var prev = list.children.filter(function (c) {
        return c.attrs && c.attrs.role === 'option';
      })[highlight];
      if (prev) prev.setAttribute('aria-selected', 'false');
    }
    highlight = i;
    var row = list.children.filter(function (c) {
      return c.attrs && c.attrs.role === 'option';
    })[i];
    if (row) {
      row.setAttribute('aria-selected', 'true');
      if (typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' });
    }
    var selectable = visible.some(function (e) { return !e.disabled && !e.inert; });
    search.setAttribute('aria-expanded', selectable ? 'true' : 'false');
    search.setAttribute('aria-activedescendant', selectable && row ? row.id : '');
  }

  function refreshList() {
    visible = filterEntries(entries, search ? search.value : '');
    if (visible.length === 0) visible = [inertEntry('No matches.')];
    renderRows();
  }

  function moveHighlight(delta) {
    var i = highlight;
    for (var step = 0; step < visible.length; step++) {
      i += delta;
      if (i < 0 || i >= visible.length) return; // stop at the first and last rows
      if (selectableAt(i)) { setHighlight(i); return; }
    }
  }

  function activate(entry) {
    if (!entry || entry.disabled || entry.inert) return;
    entry.run();
  }

  function activateHighlighted() {
    activate(visible[highlight]);
  }

  /* ── Open / close ── */

  function openPalette() {
    if (!overlay || !search || !list) return;
    if (!overlay.hidden) {
      if (typeof search.select === 'function') search.select();
      return;
    }
    opener = doc.activeElement || null;
    overlay.hidden = false;
    overlay.classList.add('show');
    search.value = '';
    entries = buildEntries();
    refreshList();
    if (typeof search.focus === 'function') search.focus();
  }

  function closePalette(opts) {
    opts = opts || {};
    if (!overlay) return;
    overlay.hidden = true;
    overlay.classList.remove('show');
    clearList();
    visible = [];
    highlight = -1;
    if (search) {
      search.removeAttribute('aria-activedescendant');
      search.setAttribute('aria-expanded', 'false');
    }
    var target = null;
    if (opts.focusEl && canFocus(opts.focusEl)) target = opts.focusEl;
    else if (opts.restoreFocus !== false) target = pickFocus(null);
    if (target && typeof target.focus === 'function') target.focus();
    else if (doc.body && typeof doc.body.focus === 'function') doc.body.focus();
  }

  function isPaletteOpen() {
    return !!overlay && !overlay.hidden;
  }

  /* ── Keyboard ── */

  function onKeydown(e) {
    if (!overlay) return;
    var key = String(e.key || '');
    var mod = e.metaKey || e.ctrlKey;
    if (isPaletteOpen()) {
      // The palette owns the keyboard while open: no background handler may
      // see a key, so every key stops propagation here.
      e.stopPropagation();
      switch (key) {
        case 'ArrowDown': e.preventDefault(); moveHighlight(1); break;
        case 'ArrowUp': e.preventDefault(); moveHighlight(-1); break;
        case 'Enter': e.preventDefault(); activateHighlighted(); break;
        case 'Escape': e.preventDefault(); closePalette(); break;
        case 'Tab': e.preventDefault(); break; // the input is the only tabbable control
        default:
          if (mod && key.toLowerCase() === 'k' && typeof search.select === 'function') {
            search.select();
          }
      }
      return;
    }
    if (resetModalShown()) return;
    if (mod && key.toLowerCase() === 'k') {
      e.preventDefault();
      e.stopPropagation();
      openPalette();
      return;
    }
    if (key === '/' && !mod && !e.shiftKey && !e.altKey
        && !editableTarget(e.target) && !e.defaultPrevented) {
      e.preventDefault();
      e.stopPropagation();
      openPalette();
    }
  }

  function onFocusIn(e) {
    if (!isPaletteOpen() || !search) return;
    var target = e && e.target;
    if (target && overlay.contains && !overlay.contains(target)
        && typeof search.focus === 'function') {
      search.focus();
    }
  }

  function onOverlayClick(e) {
    if (e && e.target === overlay) closePalette();
  }

  /* ── Init ── */

  function initPalette(d) {
    if (initialized) return;
    doc = d;
    overlay = doc.getElementById('command-palette');
    if (!overlay) return; // no markup → inert, never an error
    search = doc.getElementById('palette-search');
    list = doc.getElementById('palette-list');
    if (!search || !list) return;
    initialized = true;
    overlay.addEventListener('click', onOverlayClick);
    doc.addEventListener('focusin', onFocusIn);
    search.addEventListener('input', refreshList);
    window.addEventListener('keydown', onKeydown, true);
  }

  if (typeof module === 'undefined' || !module.exports) {
    initPalette(document);
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      initPalette: initPalette,
      openPalette: openPalette,
      closePalette: closePalette,
      isPaletteOpen: isPaletteOpen,
      buildEntries: buildEntries,
      filterEntries: filterEntries,
      moveHighlight: moveHighlight,
      activateHighlighted: activateHighlighted,
    };
  }
})();
