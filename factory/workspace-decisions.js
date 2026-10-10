(() => {
  'use strict';

  const STORAGE_KEY = 'factory-workspace-receipts';
  const MAX_RECEIPTS = 40;
  let dialog;
  let current;
  let returnFocus;

  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const array = value => Array.isArray(value) ? value : [];
  const text = value => value == null ? '' : String(value);
  const node = (tag, attrs = {}, ...children) => {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'class') element.className = value;
      else if (key === 'text') element.textContent = value;
      else if (key.startsWith('on') && typeof value === 'function') element.addEventListener(key.slice(2), value);
      else if (value !== false && value != null) element.setAttribute(key, value === true ? '' : String(value));
    }
    element.append(...children.flat().filter(Boolean));
    return element;
  };

  function stored() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      return Array.isArray(value) ? value.filter(item => item && typeof item.repository === 'string' && typeof item.id === 'string').slice(0, MAX_RECEIPTS) : [];
    } catch { return []; }
  }

  function retain(repository, id) {
    const values = stored().filter(item => !(item.repository === repository && item.id === id));
    values.unshift({repository, id, at: new Date().toISOString()});
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(values.slice(0, MAX_RECEIPTS))); } catch {}
    document.dispatchEvent(new CustomEvent('workspace-receipts-change', {detail: {repository}}));
  }

  function ensureDialog() {
    if (dialog) return;
    dialog = node('dialog', {class: 'decision-dialog', 'aria-labelledby': 'decision-title'});
    document.body.append(dialog);
    dialog.addEventListener('cancel', event => { if (current?.busy) event.preventDefault(); });
    dialog.addEventListener('close', () => {
      if (returnFocus?.isConnected) returnFocus.focus({preventScroll: true});
      returnFocus = null;
    });
  }

  function notice(message, tone = '') {
    return node('p', {class: `decision-notice${tone ? ` is-${tone}` : ''}`, role: tone === 'error' ? 'alert' : 'status', text: message});
  }

  function close() { if (!current?.busy && dialog?.open) dialog.close(); }

  function frame(body) {
    const closeButton = node('button', {class: 'icon-btn', type: 'button', 'aria-label': 'Close decision', onclick: close, disabled: current.busy}, '×');
    dialog.replaceChildren(node('div', {class: 'decision-panel'},
      node('header', {class: 'decision-head'}, node('div', {}, node('p', {class: 'eyebrow', text: 'Human decision'}), node('h2', {id: 'decision-title', text: current.title}), node('p', {class: 'muted', text: current.repository})), closeButton),
      node('div', {class: 'decision-body'}, body)));
  }

  function commentCompose(outcome) {
    const form = node('form', {class: 'decision-form'});
    const fields = outcome ? [
      ['kind', 'Attestation kind', 'select'],
      ['source_revision', 'Source revision', 'text'],
      ['evidence_url', 'Evidence URL', 'url'],
      ['summary', 'Evidence summary', 'textarea']
    ] : [['comment', 'Exact issue comment', 'textarea']];
    for (const [name, label, type] of fields) {
      const id = `decision-${name}`;
      const control = type === 'select'
        ? node('select', {id, name, required: true}, ...['released', 'installed', 'healthy', 'accepted'].map(value => node('option', {value, text: value})))
        : type === 'textarea'
          ? node('textarea', {id, name, rows: outcome ? 5 : 9, maxlength: outcome ? 4000 : 12000, required: true})
          : node('input', {id, name, type, maxlength: type === 'url' ? 2000 : 200, required: true, autocomplete: 'off'});
      control.value = current.values[name] || '';
      control.addEventListener('input', () => { current.values[name] = control.value; current.error = ''; });
      form.append(node('label', {for: id, text: label}), control);
    }
    form.append(node('p', {class: 'decision-boundary', text: outcome
      ? 'This records attributed owner evidence bound by the server to the initiative and canonical revision. It does not release, install, deploy, or measure health.'
      : 'This publishes one durable issue comment. It does not change routing, approval, merge, deployment, or host state.'}));
    if (current.error) form.append(notice(current.error, 'error'));
    form.append(node('footer', {class: 'decision-actions'}, node('button', {class: 'btn', type: 'button', disabled: current.busy, onclick: close}, 'Cancel'), node('button', {class: 'btn btn-accent', type: 'submit', disabled: current.busy}, current.busy ? 'Preparing…' : 'Prepare exact preview')));
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      current.requests = outcome ? [{op: 'outcome', number: current.number, kind: current.values.kind, source_revision: current.values.source_revision.trim(), evidence_url: current.values.evidence_url.trim(), summary: current.values.summary.trim()}] : [{op: 'issue', number: current.number, comment: current.values.comment.trim()}];
      prepare();
    });
    return form;
  }

  function prepared() {
    const value = object(current.preview);
    const targets = value.targets ?? [];
    const requests = array(value.requests);
    const effects = value.effects ?? [];
    return node('div', {class: 'decision-preview'},
      node('dl', {class: 'decision-kv'},
        node('dt', {text: 'Authenticated actor'}), node('dd', {text: value.actor ? `@${value.actor}` : 'server resolved'}),
        node('dt', {text: 'Proposal'}), node('dd', {class: 'mono', text: value.proposal_id || 'missing'}),
        node('dt', {text: 'Expires'}), node('dd', {text: value.expires_at || 'not reported'})),
      node('h3', {text: 'Exact targets'}), node('pre', {class: 'exact-preview', text: JSON.stringify(targets, null, 2)}),
      node('h3', {text: 'Exact stored requests'}), node('pre', {class: 'exact-preview', text: JSON.stringify(requests, null, 2)}),
      node('h3', {text: 'Expected effects'}), node('pre', {class: 'exact-preview', text: JSON.stringify(effects, null, 2)}),
      notice('Nothing has been applied. Confirmation is bound to this server-stored proposal and its exact targets.'),
      current.error ? notice(current.error, 'error') : null,
      node('footer', {class: 'decision-actions'},
        node('button', {class: 'btn', type: 'button', disabled: current.busy, onclick: () => { current.preview = null; current.error = ''; render(); }}, 'Cancel preview'),
        node('button', {class: 'btn btn-accent', type: 'button', disabled: current.busy, onclick: apply}, current.busy ? 'Applying…' : 'Confirm exact proposal')));
  }

  function receiptView() {
    const value = object(current.receipt);
    const status = value.status || 'unknown';
    return node('div', {class: 'decision-receipt'},
      notice(`Receipt ${status}. Execution and resulting target state are shown separately.`, status === 'success' ? 'success' : status === 'failure' ? 'error' : 'warning'),
      current.observation ? node('section', {}, node('h3', {text: 'Observed resulting state'}), node('pre', {class: 'exact-preview', text: JSON.stringify(current.observation, null, 2)})) : null,
      node('h3', {text: 'Durable action receipt'}),
      node('pre', {class: 'exact-preview', text: JSON.stringify(value, null, 2)}),
      current.error ? notice(current.error, 'error') : null,
      node('footer', {class: 'decision-actions'},
        node('button', {class: 'btn', type: 'button', onclick: close}, 'Close receipt'),
        node('button', {class: 'btn btn-accent', type: 'button', disabled: current.busy, onclick: observe}, current.busy ? 'Checking…' : 'Check resulting state')));
  }

  function render() {
    ensureDialog();
    frame(current.receipt ? receiptView() : current.preview ? prepared() : current.mode === 'action' ? actionCompose() : commentCompose(current.mode === 'outcome'));
    requestAnimationFrame(() => dialog.querySelector('textarea, input, select, .btn-accent')?.focus({preventScroll: true}));
  }

  function actionCompose() {
    const rationale = node('textarea', {id: 'decision-rationale', rows: 5, maxlength: 12000, required: true});
    rationale.value = current.values.rationale || '';
    rationale.addEventListener('input', () => { current.values.rationale = rationale.value; });
    const form = node('form', {class: 'decision-form'},
      node('p', {class: 'decision-boundary', text: current.effect}),
      node('label', {for: 'decision-rationale', text: 'Decision rationale / guidance'}), rationale,
      node('h3', {text: 'Requested operations'}), node('pre', {class: 'exact-preview', text: JSON.stringify(current.baseRequests, null, 2)}),
      current.error ? notice(current.error, 'error') : null,
      node('footer', {class: 'decision-actions'}, node('button', {class: 'btn', type: 'button', disabled: current.busy, onclick: close}, 'Cancel'), node('button', {class: 'btn btn-accent', type: 'submit', disabled: current.busy}, current.busy ? 'Preparing…' : 'Prepare exact preview')));
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const comment = `Factory human decision: ${current.title}\n\nRationale: ${rationale.value.trim()}\n\nNext: ${current.next || 'Inspect the resulting target state.'}`;
      current.requests = current.baseRequests.map(request => ({...request}));
      if (current.requests[0]?.op === 'issue') current.requests[0].comment = comment;
      else current.requests.unshift({op: 'issue', number: current.number, comment});
      for (const request of current.requests) if (request.op === 'pr') request.comment = comment;
      prepare();
    });
    return form;
  }

  async function prepare() {
    if (!current || current.busy) return;
    const snapshot = current;
    snapshot.busy = true;
    snapshot.error = '';
    render();
    try {
      const value = await WorkspaceAPI.json('/api/decisions/prepare', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({repository: snapshot.repository, requests: snapshot.requests})});
      if (!value?.proposal_id || !value?.confirmation || !Array.isArray(value.requests)) throw new Error('The server returned an incomplete bound proposal.');
      snapshot.preview = value;
    } catch (error) { snapshot.error = error.message || String(error); }
    finally { snapshot.busy = false; if (current === snapshot) render(); }
  }

  async function apply() {
    const snapshot = current;
    if (!snapshot.preview || snapshot.busy) return;
    snapshot.busy = true;
    snapshot.error = '';
    render();
    try {
      const value = await WorkspaceAPI.json('/api/decisions/apply', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({repository: snapshot.repository, proposal_id: snapshot.preview.proposal_id, confirmation: snapshot.preview.confirmation})});
      snapshot.receipt = value;
      retain(snapshot.repository, snapshot.preview.proposal_id);
    } catch (error) {
      snapshot.error = `${error.message || error}. The effect may have happened; the proposal will not be applied again from this dialog.`;
      retain(snapshot.repository, snapshot.preview.proposal_id);
      try {
        snapshot.receipt = await getReceipt(snapshot.repository, snapshot.preview.proposal_id);
      } catch {
        snapshot.receipt = {status: 'uncertain', proposal_id: snapshot.preview.proposal_id, message: 'Apply outcome is unconfirmed. Retrieve this durable receipt or inspect resulting state before any new action.'};
      }
    } finally { snapshot.busy = false; if (current === snapshot) render(); }
  }

  async function getReceipt(repository, id, observeState = false) {
    const query = new URLSearchParams({repository, id});
    if (observeState) query.set('observe', '1');
    return WorkspaceAPI.json(`/api/decisions/receipt?${query}`);
  }

  async function observe() {
    const snapshot = current;
    if (!snapshot?.preview?.proposal_id || snapshot.busy) return;
    snapshot.busy = true;
    snapshot.error = '';
    render();
    try {
      const value = await getReceipt(snapshot.repository, snapshot.preview.proposal_id, true);
      if (!Array.isArray(value?.observation?.targets)) throw new Error('The server returned no resulting-state observation.');
      const {observation, ...receipt} = value;
      snapshot.receipt = receipt;
      snapshot.observation = observation;
    }
    catch (error) { snapshot.error = error.message || String(error); }
    finally { snapshot.busy = false; if (current === snapshot) render(); }
  }

  function open(options) {
    if (!options?.repository || !Number.isSafeInteger(Number(options.number)) || Number(options.number) <= 0) throw new TypeError('Decision scope requires a repository and positive issue number.');
    ensureDialog();
    returnFocus = document.activeElement;
    current = {
      repository: options.repository,
      number: Number(options.number),
      title: text(options.title) || `Decision for #${options.number}`,
      mode: options.mode || 'action',
      effect: text(options.effect),
      next: text(options.next),
      baseRequests: array(options.requests).map(request => ({...request})),
      requests: [], values: {}, preview: null, receipt: null, observation: null, error: '', busy: false
    };
    render();
    if (!dialog.open) dialog.showModal();
  }

  async function mountHistory(host, repository) {
    if (!(host instanceof Element)) return;
    const entries = stored().filter(item => item.repository === repository);
    host.replaceChildren(node('p', {class: 'muted', text: entries.length ? 'Loading retained server receipts…' : 'No retained proposal ids on this browser.'}));
    if (!entries.length) return;
    const list = node('div', {class: 'receipt-list'});
    const values = await Promise.all(entries.slice(0, 12).map(async entry => {
      try { return {entry, value: await getReceipt(repository, entry.id)}; }
      catch (error) { return {entry, error: error.message || String(error)}; }
    }));
    for (const row of values) {
      const card = node('article', {class: 'receipt-card'}, node('strong', {class: 'mono', text: row.entry.id}), node('span', {text: row.error || row.value?.status || row.value?.state || 'receipt available'}));
      if (!row.error) card.append(node('button', {class: 'btn btn-small', type: 'button', onclick: () => {
        returnFocus = document.activeElement;
        current = {repository, title: 'Durable action receipt', busy: false, error: '', preview: {proposal_id: row.entry.id}, receipt: row.value, observation: null};
        ensureDialog(); render(); dialog.showModal();
      }}, 'Open receipt'));
      list.append(card);
    }
    host.replaceChildren(list);
  }

  window.WorkspaceDecisions = Object.freeze({open, mountHistory});
})();
