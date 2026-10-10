(() => {
  'use strict';

  const STORAGE_KEY = 'factory-workspace-access';
  let access = '';
  let prompting = null;
  let dismissed = false;

  try { access = sessionStorage.getItem(STORAGE_KEY) || ''; } catch {}

  function seedFromFragment() {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const value = fragment.get('access');
    if (!value) return;
    access = value;
    try { sessionStorage.setItem(STORAGE_KEY, value); } catch {}
    fragment.delete('access');
    history.replaceState(history.state, '', `${location.pathname}${location.search}${fragment.size ? `#${fragment}` : ''}`);
  }

  async function ask() {
    if (dismissed) throw new Error('Workspace locked. Choose Refresh selected to enter the access key.');
    if (prompting) return prompting;
    prompting = new Promise((resolve, reject) => {
      const dialog = document.createElement('dialog');
      dialog.className = 'decision-dialog';
      dialog.setAttribute('aria-labelledby', 'workspace-unlock-title');
      dialog.innerHTML = '<div class="decision-panel"><header class="decision-head"><div><p class="eyebrow">Network control credential</p><h2 id="workspace-unlock-title">Unlock Factory</h2></div></header><div class="decision-body"><form method="dialog" class="decision-form"><p>This key grants operator access to this workspace. Use a trusted network or protected HTTPS connection.</p><label for="workspace-access-key">Workspace access key</label><input id="workspace-access-key" type="password" autocomplete="off" required autofocus><p class="muted">Read the owner-only .factory/workspace-access.key file in the server startup repository. The key stays in this browser tab and is never sent to FM.</p><footer class="decision-actions"><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn btn-accent" value="unlock">Unlock workspace</button></footer></form></div></div>';
      dialog.addEventListener('close', () => {
        const input = dialog.querySelector('input'), value = input.value.trim();
        input.value = '';
        dialog.remove();
        if (dialog.returnValue !== 'unlock' || !value) {
          dismissed = true;
          reject(new Error('Workspace locked. Choose Refresh selected to enter the access key.'));
          return;
        }
        access = value;
        try { sessionStorage.setItem(STORAGE_KEY, access); } catch {}
        resolve();
      }, {once: true});
      document.body.append(dialog);
      dialog.showModal();
    }).finally(() => { prompting = null; });
    return prompting;
  }

  async function request(input, options = {}, retried = false) {
    if (!access) await ask();
    const headers = new Headers(options.headers || {});
    headers.set('X-Factory-Access', access);
    const method = String(options.method || 'GET').toUpperCase();
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-Factory-Act', '1');
    const response = await fetch(input, {...options, headers, credentials: 'same-origin'});
    if (response.status === 401) {
      access = '';
      try { sessionStorage.removeItem(STORAGE_KEY); } catch {}
      if (!retried) { await ask(); return request(input, options, true); }
      dismissed = true;
    }
    return response;
  }

  async function json(input, options = {}) {
    const response = await request(input, {cache: 'no-store', ...options, headers: {Accept: 'application/json', ...(options.headers || {})}});
    let body = null;
    try { body = await response.json(); } catch {}
    if (!response.ok) {
      const detail = body?.error;
      const error = new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (HTTP ${response.status}).`);
      error.status = response.status;
      error.code = detail?.code || body?.code || 'request_failed';
      error.body = body;
      throw error;
    }
    return body;
  }

  seedFromFragment();
  window.WorkspaceAPI = Object.freeze({request, json, unlock: async () => { dismissed = false; if (!access) await ask(); }, text: async (input, options) => {
    const response = await request(input, options);
    if (!response.ok) throw new Error(`Request failed (HTTP ${response.status}).`);
    return response.text();
  }});
})();
