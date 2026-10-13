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

    // Pre-open a tab during the direct user gesture to avoid popup blockers
    let tab = null;
    const titleName = name === 'hub' ? 'Tournament Hub' : 'Tournament Cockpit';
    const portNum = name === 'hub' ? '8798' : '8800';
    try {
      tab = window.open('', '_blank');
      if (tab && !tab.closed) {
        try {
          tab.document.title = `Starting ${titleName}...`;
          tab.document.body.innerHTML = `
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0f19; color: #f3f4f6; height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; margin: 0;">
              <div style="width: 28px; height: 28px; border: 3px solid rgba(88, 166, 255, 0.2); border-top-color: #58a6ff; border-radius: 50%; animation: spin 0.8s linear infinite; margin-bottom: 16px;"></div>
              <style>@keyframes spin { to { transform: rotate(360deg); } }</style>
              <div style="font-size: 18px; font-weight: 600; margin-bottom: 6px;">Opening ${titleName}...</div>
              <div style="font-size: 13px; color: #9ca3af;">Initializing service on port ${portNum}. Redirecting automatically...</div>
            </div>
          `;
        } catch (e) {
          // Fallback if document writing fails
        }
      }
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
            if (tab && !tab.closed) {
              try {
                tab.document.body.innerHTML = `
                  <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0f19; color: #f3f4f6; height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; margin: 0;">
                    <div style="font-size: 18px; font-weight: 600; color: #fbbf24; margin-bottom: 6px;">Port Conflict (${portNum})</div>
                    <div style="font-size: 13px; color: #9ca3af; margin-bottom: 16px;">Port ${portNum} is in use by another program.</div>
                    <a href="${destUrl || targetUrl}" style="background: #2563eb; color: #fff; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-size: 13px;">Open Port ${portNum} Directly</a>
                  </div>
                `;
              } catch (err) {
                tab.close();
              }
            }
            updateDot(name, { name, state: 'conflict', detail: data.message });
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
                try {
                  tab.location.href = destUrl;
                } catch (err) {
                  window.open(destUrl, '_blank');
                }
              } else {
                window.open(destUrl, '_blank');
              }
            } else if (attempts < MAX_START_POLL_ATTEMPTS) {
              setTimeout(check, START_POLL_INTERVAL_MS);
            } else {
              startingServices.delete(name);
              if (tab && !tab.closed) {
                try {
                  tab.document.body.innerHTML = `
                    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0f19; color: #f3f4f6; height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; margin: 0;">
                      <div style="font-size: 18px; font-weight: 600; color: #f87171; margin-bottom: 6px;">Timed Out Starting ${titleName}</div>
                      <div style="font-size: 13px; color: #9ca3af; margin-bottom: 16px;">Service did not respond in time on port ${portNum}.</div>
                      <a href="${destUrl}" style="background: #2563eb; color: #fff; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-size: 13px;">Try Opening Anyway</a>
                    </div>
                  `;
                } catch (err) {
                  tab.close();
                }
              }
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
