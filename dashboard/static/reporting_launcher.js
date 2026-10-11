/**
 * Reporting & Tournament Service Launcher (Issue #485)
 *
 * Provides one-click status polling, auto-start, and navigation
 * for Tournament Hub (port 8798) and Tournament Cockpit (port 8800).
 */
(function () {
  'use strict';

  const POLL_INTERVAL_MS = 10000;
  const START_POLL_INTERVAL_MS = 1000;
  const MAX_START_POLL_ATTEMPTS = 30;

  const stateCache = {};
  const startingServices = new Set();

  function getToken() {
    return typeof CONTROL_TOKEN !== 'undefined' ? CONTROL_TOKEN : '';
  }

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
      if (!startingServices.has(name)) {
        pollStatus(name);
      }
    });
  }

  function startAndNavigate(name, targetUrl) {
    if (startingServices.has(name)) return;
    startingServices.add(name);
    updateDot(name, { name, state: 'starting', data_available: false });

    // Pre-open a blank tab during the direct user gesture to avoid popup blockers
    let tab = null;
    try {
      tab = window.open('about:blank', '_blank');
      if (tab) tab.opener = null;
    } catch (e) {
      tab = null;
    }

    const headers = {
      'Content-Type': 'application/json',
      'X-Control-Token': getToken(),
    };

    fetch(`/api/services/${name}/start`, { method: 'POST', headers })
      .then(res => {
        if (res.status === 409) {
          return res.json().then(data => {
            if (tab && !tab.closed) tab.close();
            updateDot(name, { name, state: 'conflict', detail: data.message });
            alert(data.message || `Port conflict: another program is running on ${name} port.`);
            throw new Error('Conflict');
          });
        }
        if (!res.ok) {
          if (tab && !tab.closed) tab.close();
          return res.json().catch(() => ({})).then(data => {
            const errDetail = data.detail || data.error || `HTTP ${res.status}`;
            updateDot(name, { name, state: 'failed', detail: errDetail });
            throw new Error(`Start request failed with HTTP ${res.status}: ${errDetail}`);
          });
        }
        return res.json();
      })
      .then(startData => {
        const destUrl = startData.url || targetUrl;
        let attempts = 0;

        function check() {
          attempts += 1;
          pollStatus(name).then(status => {
            if (status.state === 'running') {
              startingServices.delete(name);
              if (tab && !tab.closed) {
                tab.location.href = destUrl;
              } else {
                window.open(destUrl, '_blank');
              }
            } else if (attempts < MAX_START_POLL_ATTEMPTS) {
              setTimeout(check, START_POLL_INTERVAL_MS);
            } else {
              startingServices.delete(name);
              if (tab && !tab.closed) tab.close();
              updateDot(name, { name, state: 'failed', detail: 'Timed out waiting for service to start' });
            }
          });
        }

        setTimeout(check, 500);
      })
      .catch(err => {
        startingServices.delete(name);
        if (err.message !== 'Conflict') {
          if (tab && !tab.closed) tab.close();
          updateDot(name, { name, state: 'failed' });
        }
      });
  }

  function handleLauncherClick(e) {
    const launcher = e.target.closest('[data-launcher]');
    if (!launcher) return;

    const name = launcher.getAttribute('data-launcher');
    if (!name) return;

    const currentState = stateCache[name] ? stateCache[name].state : null;
    const targetUrl = launcher.href;

    // If confirmed running, standard navigation proceeds
    if (currentState === 'running') {
      return;
    }

    // If stopped, failed, or unknown: trigger auto-start and navigation
    e.preventDefault();
    startAndNavigate(name, targetUrl);
  }

  function init() {
    document.addEventListener('click', handleLauncherClick);
    pollAll();
    setInterval(pollAll, POLL_INTERVAL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
