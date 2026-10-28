/**
 * Reporting & Tournament service status dots (Issue #485).
 *
 * Tournament Hub and Tournament Cockpit are folded into this dashboard: the
 * side-rail links navigate there in place and the pages render in-process,
 * so there is nothing to start. This script only polls each tool's status
 * endpoint so every link carries a live running/stopped dot; it never opens
 * popups and never starts anything itself.
 */
(function () {
  'use strict';

  const POLL_INTERVAL_MS = 10000;

  const stateCache = {};

  function getLauncherElements() {
    return document.querySelectorAll('[data-launcher]');
  }

  function updateDot(name, status) {
    stateCache[name] = status;
    const links = document.querySelectorAll(`[data-launcher="${name}"]`);
    links.forEach(link => {
      const dot = link.querySelector('.proto-service-dot');
      if (dot) {
        dot.className = `proto-service-dot dot-${status.state || 'stopped'}`;
        let title = `${link.title || name}: ${status.state || 'stopped'}`;
        if (status.state === 'running' && status.data_available === false) {
          title += ' (no tournament data yet)';
        } else if (status.state === 'conflict') {
          title += ' (port in use by another program)';
        } else if (status.state === 'failed' && status.detail) {
          title += ` (${status.detail})`;
        }
        dot.setAttribute('title', title);
      }
    });
  }

  function pollStatus(name) {
    return fetch(`/api/services/${name}/status`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(status => {
        updateDot(name, status);
        return status;
      })
      .catch(() => {
        const fallback = { name, state: 'stopped', data_available: false };
        updateDot(name, fallback);
        return fallback;
      });
  }

  function pollAll() {
    const names = new Set();
    getLauncherElements().forEach(el => {
      const name = el.getAttribute('data-launcher');
      if (name) names.add(name);
    });
    names.forEach(name => {
      pollStatus(name);
    });
  }

  function init() {
    pollAll();
    setInterval(pollAll, POLL_INTERVAL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
