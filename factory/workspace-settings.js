(() => {
  'use strict';

  const PREF_KEY = 'factory-workspace-preferences';
  const defaults = {motion: true, density: 'compact', pollSeconds: 30, attentionDays: 7, inspectorWidth: 440};
  const allowed = {
    density: new Set(['compact', 'comfortable']), pollSeconds: new Set([10, 30, 60]),
    attentionDays: new Set([1, 3, 7, 14, 30]), inspectorWidth: new Set([360, 440, 560])
  };
  const fields = [
    ['dispatch.max_active', 'Maximum active tickets', 'How many tickets a new dispatcher pass may run at once.', 1],
    ['dispatch.budget_min', 'Time limit per ticket (minutes)', 'Wall-clock limit captured by newly admitted work.', 1],
    ['dispatch.max_attempts', 'Worker + gate attempts', 'Attempts before escalation; reviewer revision rounds are separate.', 1],
    ['dispatch.review_rounds', 'Reviewer revision rounds', 'Allowed revisions after gate success. Zero disables reviewer bounce.', 0],
    ['manager.model', 'Factory Manager model override', 'Optional repository override for new FM requests.', null]
  ];
  const sessions = new Map();

  function readPreferences() {
    let stored = {};
    try { stored = JSON.parse(localStorage.getItem(PREF_KEY) || '{}'); } catch {}
    const value = {...defaults};
    if (typeof stored.motion === 'boolean') value.motion = stored.motion;
    for (const key of Object.keys(allowed)) {
      const candidate = key === 'density' ? stored[key] : Number(stored[key]);
      if (allowed[key].has(candidate)) value[key] = candidate;
    }
    return value;
  }

  const preferences = readPreferences();
  function persistPreferences() {
    try { localStorage.setItem(PREF_KEY, JSON.stringify(preferences)); } catch {}
  }
  function setPreference(key, candidate) {
    const value = key === 'motion' ? Boolean(candidate) : key === 'density' ? candidate : Number(candidate);
    if (key === 'motion' || allowed[key]?.has(value)) {
      preferences[key] = value;
      persistPreferences();
      document.dispatchEvent(new CustomEvent('workspace-preferences-change', {detail: {key, value}}));
    }
  }

  const esc = value => String(value ?? '').replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
  const fieldId = key => `real-setting-${key.replaceAll('.', '-')}`;

  function preferenceMarkup() {
    const option = (value, label, selected) => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(label)}</option>`;
    return `<section class="settings-section" aria-labelledby="presentation-title"><header><div><h2 id="presentation-title">Presentation and observation</h2><p>Browser-local preferences. They never change repository policy or collection authority.</p></div><span class="badge">browser local</span></header><div class="settings-rows">
      <label class="setting-row"><span><strong>Purposeful motion</strong><small>Brief transitions only. The system reduced-motion preference takes precedence.</small></span><input type="checkbox" data-preference="motion"${preferences.motion ? ' checked' : ''}></label>
      <label class="setting-row"><span><strong>Information density</strong><small>Changes spacing, never evidence.</small></span><select data-preference="density">${option('compact','Compact',preferences.density)}${option('comfortable','Comfortable',preferences.density)}</select></label>
      <label class="setting-row"><span><strong>Visible-page polling</strong><small>Reads cached observations for only the selected repository.</small></span><select data-preference="pollSeconds">${option(10,'10 seconds',preferences.pollSeconds)}${option(30,'30 seconds',preferences.pollSeconds)}${option(60,'60 seconds',preferences.pollSeconds)}</select></label>
      <label class="setting-row"><span><strong>Lingering update threshold</strong><small>Last-update age is not time blocked or time in stage.</small></span><select data-preference="attentionDays">${[1,3,7,14,30].map(value => option(value,`${value} day${value === 1 ? '' : 's'}`,preferences.attentionDays)).join('')}</select></label>
      <label class="setting-row"><span><strong>Inspector width</strong><small>Desktop preference; narrow screens use the viewport.</small></span><select data-preference="inspectorWidth">${[360,440,560].map(value => option(value,`${value} px`,preferences.inspectorWidth)).join('')}</select></label>
    </div></section>`;
  }

  function markup(repository) {
    return `<div class="workspace-settings"><header class="workspace-head"><div><h1>Settings</h1><p>Browser preferences and the selected repository's real <code>.factory.toml</code> controls remain separate.</p></div></header>${preferenceMarkup()}<section class="settings-section" aria-labelledby="repository-settings-title" data-real-settings data-repository="${esc(repository || '')}"><header><div><h2 id="repository-settings-title">Repository settings</h2><p>Scoped to <strong>${esc(repository || 'unavailable')}</strong>. Nothing is saved automatically.</p></div><span class="badge">local config</span></header><div class="settings-status" data-settings-status role="status" aria-live="polite">Loading settings…</div><form data-settings-form><div class="settings-rows" data-settings-fields></div><section class="settings-agents" data-settings-agents></section><section class="settings-review" data-settings-review hidden></section><footer class="settings-actions"><button class="btn btn-accent" type="submit" data-settings-save disabled>Save reviewed changes</button><button class="btn" type="button" data-settings-revert disabled>Revert edits</button><button class="btn" type="button" data-settings-reload>Reload current settings</button></footer></form></section></div>`;
  }

  const normalize = (key, raw) => {
    if (key === 'manager.model') return String(raw ?? '').trim() || null;
    if (raw === '' || raw == null) return NaN;
    const value = Number(raw);
    return Number.isSafeInteger(value) ? value : NaN;
  };
  const equal = (key, left, right) => key === 'manager.model' ? (String(left ?? '').trim() || null) === (right || null) : Number(left) === Number(right);
  const errorFor = (key, raw) => {
    const value = normalize(key, raw);
    if (key === 'manager.model') return value && value.length > 200 ? 'Use 200 characters or fewer.' : '';
    const minimum = key === 'dispatch.review_rounds' ? 0 : 1;
    return !Number.isSafeInteger(value) || value < minimum ? `Enter a whole number of ${minimum} or more.` : '';
  };

  function safeProgram(value) {
    const base = String(value || '').split(/[\\/]/).at(-1) || '';
    return /^[A-Za-z0-9._+-]+$/.test(base) ? base : 'program omitted';
  }
  function safeModel(value) { return value && String(value).length <= 200 ? String(value) : 'none / inherited'; }
  function safeOrigin(value) {
    try { const url = new URL(String(value), location.origin); return ['http:','https:'].includes(url.protocol) ? url.origin : 'endpoint omitted'; }
    catch { return 'endpoint omitted'; }
  }

  function sessionFor(root, repository) {
    if (!sessions.has(repository)) sessions.set(repository, {root, repository, snapshot: null, draft: {}, busy: false, error: '', errors: {}, conflict: false, review: false});
    const state = sessions.get(repository);
    state.root = root;
    return state;
  }

  function changes(state) {
    if (!state.snapshot) return [];
    return fields.map(([key, label]) => {
      const raw = state.draft[key] ?? '';
      const value = normalize(key, raw);
      const before = state.snapshot.fields?.[key]?.value;
      return {key, label, raw, value, before, error: errorFor(key, raw)};
    }).filter(row => row.error || !equal(row.key, row.value, row.before));
  }

  function render(state) {
    if (!state.root.isConnected) return;
    const fieldHost = state.root.querySelector('[data-settings-fields]');
    const active = document.activeElement;
    const selection = active?.matches?.('[data-real-setting]') ? [active.dataset.realSetting, active.selectionStart, active.selectionEnd] : null;
    fieldHost.innerHTML = fields.map(([key, label, help, minimum]) => {
      const definition = state.snapshot?.fields?.[key];
      const type = minimum == null ? 'text' : 'number';
      return `<label class="setting-row setting-input" for="${fieldId(key)}"><span><strong>${esc(label)}</strong><small>${esc(help)}</small><small class="setting-effective">Effective: ${esc(definition?.value == null ? 'inherited / none' : definition.value)} · ${esc(definition?.applies || 'next new request')}</small><small class="setting-source">Source: ${esc(definition?.source || 'unavailable')}</small><small class="setting-error">${esc(state.errors[key] || errorFor(key, state.draft[key] ?? ''))}</small></span><input id="${fieldId(key)}" data-real-setting="${esc(key)}" name="${esc(key)}" type="${type}"${minimum == null ? ' maxlength="200" autocomplete="off"' : ` min="${minimum}" step="1" inputmode="numeric"`} value="${esc(state.draft[key] ?? '')}" ${state.snapshot && !state.busy ? '' : 'disabled'}></label>`;
    }).join('');
    const agents = state.snapshot?.agents || {};
    const workers = Array.isArray(agents.workers) ? agents.workers : [];
    state.root.querySelector('[data-settings-agents]').innerHTML = state.snapshot ? `<h3>Effective agents · read only</h3>${workers.map(worker => `<p><strong>${esc(worker.label || 'worker')}</strong> · ${esc(safeProgram(worker.program))} · model ${esc(safeModel(worker.model))}<small>${esc(worker.source || 'source not reported')} · arguments and environment omitted</small></p>`).join('')}<p><strong>Reviewer</strong> · ${esc(safeProgram(agents.reviewer?.program))} · model ${esc(safeModel(agents.reviewer?.model))}<small>${esc(agents.reviewer?.source || 'source not reported')}</small></p><p><strong>Triage</strong> · ${esc(safeOrigin(agents.triage?.endpoint))} · model ${esc(safeModel(agents.triage?.model))}<small>Only endpoint origin is shown; paths, queries and credentials are omitted.</small></p>` : '';
    const rows = changes(state);
    const review = state.root.querySelector('[data-settings-review]');
    review.hidden = !rows.length;
    review.innerHTML = rows.length ? `<h3>Pending changes</h3><ul>${rows.map(row => `<li><strong>${esc(row.label)}</strong><span>before: ${esc(row.before == null ? 'inherited / none' : row.before)}</span><span>after: ${esc(row.error ? `${row.raw} · invalid` : row.value == null ? 'remove override' : row.value)}</span></li>`).join('')}</ul>` : '';
    const valid = rows.length && !rows.some(row => row.error);
    state.root.querySelector('[data-settings-save]').disabled = !state.snapshot || !valid || state.busy || state.conflict || state.review;
    state.root.querySelector('[data-settings-revert]').disabled = !rows.length || state.busy;
    const status = state.root.querySelector('[data-settings-status]');
    status.className = `settings-status${state.error ? ' is-error' : state.conflict || state.review ? ' is-warning' : ''}`;
    status.textContent = state.busy ? 'Working…' : state.conflict ? `${state.error || 'Settings changed on disk.'} Reload and review before saving; your draft is retained.` : state.review ? 'Latest settings loaded. Review current values against your retained draft, then acknowledge by reloading once more.' : state.error || (state.snapshot ? rows.length ? `${rows.length} unsaved change${rows.length === 1 ? '' : 's'}.` : 'Effective values loaded. Nothing is saved automatically.' : 'Settings are unavailable independently of other workspace evidence.');
    if (selection) {
      const input = state.root.querySelector(`[data-real-setting="${CSS.escape(selection[0])}"]`);
      input?.focus({preventScroll: true});
      if (input?.type === 'text') input.setSelectionRange(selection[1], selection[2]);
    }
  }

  async function load(state, force = false) {
    if (state.busy) return;
    const old = state.snapshot;
    const retained = changes(state).reduce((result, row) => ({...result, [row.key]: row.raw}), {});
    state.busy = true; state.error = ''; render(state);
    try {
      const query = new URLSearchParams({repository: state.repository});
      if (force) query.set('fresh', '1');
      const value = await WorkspaceAPI.json(`/api/settings?${query}`);
      if (!value?.ok || !value.fields) throw new Error(value?.error || 'Settings response is incomplete.');
      state.snapshot = value;
      state.draft = Object.fromEntries(fields.map(([key]) => [key, key === 'manager.model' ? value.fields[key]?.value || '' : String(value.fields[key]?.value ?? '')]));
      Object.assign(state.draft, retained);
      state.review = Boolean(old && old.revision !== value.revision && Object.keys(retained).length);
      state.conflict = false; state.errors = {};
    } catch (error) { state.error = error.message || String(error); }
    finally { state.busy = false; render(state); }
  }

  async function save(state) {
    const rows = changes(state);
    if (!state.snapshot || state.busy || !rows.length || rows.some(row => row.error) || state.conflict || state.review) return;
    state.busy = true; state.error = ''; render(state);
    try {
      const value = await WorkspaceAPI.json(`/api/settings?${new URLSearchParams({repository: state.repository})}`, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({repository: state.repository, revision: state.snapshot.revision, changes: Object.fromEntries(rows.map(row => [row.key, row.value]))})});
      if (!value?.ok || !value.fields) throw Object.assign(new Error(typeof value?.error === 'string' ? value.error : 'Settings save response is incomplete.'), {body: value});
      state.snapshot = value;
      state.draft = Object.fromEntries(fields.map(([key]) => [key, key === 'manager.model' ? value.fields[key]?.value || '' : String(value.fields[key]?.value ?? '')]));
      state.conflict = false; state.review = false;
      document.dispatchEvent(new CustomEvent('workspace-settings-saved', {detail: {repository: state.repository}}));
    } catch (error) {
      state.error = error.message || String(error);
      state.errors = error.body?.errors && typeof error.body.errors === 'object' ? error.body.errors : {};
      state.conflict = error.status === 409 || /stale|revision|changed/i.test(state.error);
    } finally { state.busy = false; render(state); }
  }

  function mount(root, repository) {
    const host = root?.querySelector?.('[data-real-settings]');
    if (!host || !repository) return;
    const state = sessionFor(host, repository);
    host.addEventListener('input', event => {
      const input = event.target.closest('[data-real-setting]');
      if (!input) return;
      state.draft[input.dataset.realSetting] = input.value;
      state.errors[input.dataset.realSetting] = '';
      render(state);
    });
    host.addEventListener('submit', event => { event.preventDefault(); save(state); });
    host.addEventListener('click', event => {
      if (event.target.closest('[data-settings-revert]')) {
        state.draft = Object.fromEntries(fields.map(([key]) => [key, key === 'manager.model' ? state.snapshot?.fields?.[key]?.value || '' : String(state.snapshot?.fields?.[key]?.value ?? '')]));
        state.error = ''; state.errors = {}; state.conflict = false; state.review = false; render(state);
      } else if (event.target.closest('[data-settings-reload]')) {
        if (state.review) state.review = false;
        load(state, true);
      }
    });
    root.querySelectorAll('[data-preference]').forEach(control => control.addEventListener('change', () => setPreference(control.dataset.preference, control.type === 'checkbox' ? control.checked : control.value)));
    load(state);
  }

  persistPreferences();
  window.WorkspaceSettings = Object.freeze({preferences, markup, mount, setPreference});
})();
