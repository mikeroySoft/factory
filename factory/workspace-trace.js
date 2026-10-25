(() => {
  'use strict';

  const STAGES = ['ticket','triage','binding','admission','worker_select','claim','plan','attempt','gate','review','approved','manager','merge','merged','wontfix','escalate','learn','curate'];
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
  const array = value => Array.isArray(value) ? value : [];
  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const positive = value => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : null;
  const node = (tag, className = '', text = '') => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== '') element.textContent = String(text);
    return element;
  };
  const safeUrl = value => {
    try {
      const url = new URL(String(value));
      return ['http:','https:'].includes(url.protocol) ? url.href : '';
    } catch {
      return '';
    }
  };
  const stageLabel = value => String(value || 'unknown').replaceAll('_', ' ');
  const ticketFrom = value => {
    const match = /^ticket:([1-9][0-9]*)$/.exec(String(value || ''));
    return match ? positive(match[1]) : null;
  };
  const tokens = value => ['prompt_tokens','completion_tokens','cached_tokens']
    .reduce((total, key) => total + (Number.isFinite(Number(value?.[key])) ? Number(value[key]) : 0), 0);
  const costLabel = value => {
    const cost = object(value);
    const seconds = Number.isFinite(Number(cost.seconds)) ? Number(cost.seconds) : 0;
    return `${tokens(cost).toLocaleString('en-US')} tokens · ${seconds.toLocaleString('en-US')} seconds`;
  };
  const terminal = graph => {
    const flow = array(graph?.nodes).filter(item => !['ticket','learn','curate'].includes(item?.stage));
    const last = flow.at(-1);
    if (!last) return null;
    if (last.event === 'merged' || (last.stage === 'merge' && last.chosen === 'merge now')) {
      return {kind:'merged', label:'Merged', detail:last.pr ? `PR #${last.pr}` : 'Code landed'};
    }
    if (last.stage === 'wontfix') return {kind:'wontfix', label:"Won't-fix", detail:last.chosen};
    if (last.stage === 'escalate' || last.event === 'escalate') {
      const decision = flow.filter(item => item.stage === 'escalate' && item.synthesized !== true).at(-1);
      return {kind:'escalated', label:'Escalated', detail:decision?.chosen || last.chosen || last.rationale || 'human decision required'};
    }
    return null;
  };
  const orderedStages = nodes => [...new Set(nodes.map(item => item.stage || 'unknown'))].sort((left, right) => {
    const a = STAGES.indexOf(left), b = STAGES.indexOf(right);
    return (a < 0 ? STAGES.length : a) - (b < 0 ? STAGES.length : b) || left.localeCompare(right);
  });
  const recentNumbers = value => [...new Set(array(value).map(item => positive(object(item).number ?? item)).filter(Boolean))].slice(0, 12);

  function optionMarkup(item, chosen) {
    const value = object(item);
    const selected = value.option === chosen;
    const reason = value.reason || (selected ? 'selected' : 'not selected');
    const classes = `trace-option ${selected ? 'is-chosen' : 'is-unchosen'}${value.eligible === false ? ' is-ineligible' : ''}`;
    return `<span class="${classes}"${selected ? '' : ' tabindex="0"'} title="${esc(reason)}" aria-label="${esc(`${value.option || 'unknown'}: ${reason}`)}">${esc(value.option || 'unknown')}</span>`;
  }

  function traceNodeMarkup(item, loopByNode, frontier) {
    const value = object(item);
    const id = String(value.decision_id || 'unknown');
    const options = array(value.options);
    const ticket = value.stage === 'ticket' ? positive(value.ticket) : ticketFrom(value.chosen);
    const actor = [value.actor?.kind, value.actor?.name].filter(Boolean).join(' · ');
    const classes = `trace-node${value.synthesized === true ? ' is-synthesized' : ''}${id === frontier ? ' is-frontier' : ''}`;
    const loop = loopByNode.get(id);
    return `<article class="${classes}" data-trace-node="${esc(id)}" data-at="${esc(value.at || '')}"${loop == null ? '' : ` data-loop="${loop}"`}>
      <button class="trace-node-select" type="button" data-trace-select="${esc(id)}" aria-pressed="false">
        <span class="trace-node-meta"><b>${esc(stageLabel(value.stage))}</b>${value.stage === 'plan' && actor ? `<i>${esc(actor)}</i>` : ''}${value.synthesized === true ? '<i>synthesized</i>' : ''}${id === frontier ? '<i>frontier</i>' : ''}</span>
        <strong>${esc(value.chosen || value.event || 'unknown')}</strong>
        <time>${esc(value.at || 'time not recorded')}</time>
      </button>
      ${options.length ? `<div class="trace-options" aria-label="Decision options">${options.map(option => optionMarkup(option, value.chosen)).join('')}</div>` : ''}
      ${ticket ? `<button class="trace-ticket-link" type="button" data-trace-ticket="${ticket}">Open ticket #${ticket}</button>` : ''}
    </article>`;
  }

  function markup(graph, options = {}) {
    const value = object(graph);
    const nodes = array(value.nodes);
    const edges = array(value.edges);
    const loops = array(value.loops);
    const ticket = positive(options.ticket ?? value.ticket);
    const recent = recentNumbers(options.recentTickets);
    const loopByNode = new Map();
    loops.forEach((loop, index) => array(loop?.nodes).forEach(id => loopByNode.set(String(id), index)));
    const stages = orderedStages(nodes);
    const dated = nodes.filter(item => Number.isFinite(new Date(item?.at || '').valueOf()));
    const end = terminal(value);
    const recentMarkup = recent.map(number => `<button type="button" data-trace-ticket="${number}"${number === ticket ? ' aria-current="true"' : ''}>#${number}</button>`).join('');
    const loopMarkup = loops.map((loop, index) => {
      const ids = array(loop?.nodes).map(String);
      return `<details class="trace-loop" data-trace-loop="${index}" data-loop-members="${esc(ids.join(','))}"${index === 0 ? ' open' : ''}><summary>Retry loop ${index + 1}<span>${esc(costLabel(loop?.cost))}</span></summary><p>${ids.length} decision${ids.length === 1 ? '' : 's'} in this iteration.</p></details>`;
    }).join('');
    const groups = stages.map(stage => {
      const members = nodes.filter(item => (item.stage || 'unknown') === stage);
      return `<section class="trace-stage" data-stage="${esc(stage)}"><h2>${esc(stageLabel(stage))}<span>${members.length}</span></h2><div class="trace-stage-nodes">${members.map(item => traceNodeMarkup(item, loopByNode, value.frontier)).join('')}</div></section>`;
    }).join('');
    const relations = edges.map(edge => `<li>${esc(edge.relation || 'follows')}: ${esc(edge.from)} → ${esc(edge.to)}</li>`).join('');
    const coverage = array(value.coverage);
    return `<section class="trace-tool" data-ticket="${ticket || ''}">
      <form class="trace-picker" data-trace-picker>
        <label for="trace-ticket">Ticket</label>
        <input id="trace-ticket" name="ticket" type="number" min="1" step="1" required value="${ticket || ''}" list="trace-recent-tickets">
        <button class="btn btn-accent" type="submit">Open trace</button>
        <datalist id="trace-recent-tickets">${recent.map(number => `<option value="${number}"></option>`).join('')}</datalist>
        <div class="trace-recent" aria-label="Recent tickets">${recentMarkup}</div>
      </form>
      ${ticket ? '' : '<p class="empty">Choose a ticket to inspect its decision trace.</p>'}
      ${ticket && !nodes.length ? '<p class="empty">No decision records were found for this ticket.</p>' : ''}
      ${nodes.length ? `<div class="trace-status"><p>${value.frontier ? `<strong>Frontier:</strong> ${esc(value.frontier_stage || 'unknown')}` : '<strong>Terminal or no active frontier.</strong>'}</p><p><strong>Total:</strong> ${esc(costLabel(value.cost))}</p></div>
      <label class="trace-scrubber"><span>Replay through <output data-trace-time>${esc(dated.at(-1)?.at || 'time not recorded')}</output></span><input type="range" min="0" max="${Math.max(0, dated.length - 1)}" value="${Math.max(0, dated.length - 1)}" step="1" data-trace-range ${dated.length < 2 ? 'disabled' : ''}></label>
      ${loopMarkup ? `<div class="trace-loops" aria-label="Retry loops">${loopMarkup}</div>` : ''}
      <div class="trace-layout"><div class="trace-board" data-trace-board><svg class="trace-lines" data-trace-lines aria-hidden="true"></svg><div class="trace-stages" role="list" aria-label="Decision nodes in pipeline order">${groups}</div><ol class="sr-only" aria-label="Recorded relationships">${relations}</ol></div><aside class="trace-detail" data-trace-detail aria-live="polite"><p class="muted">Select a decision node for its evidence.</p></aside></div>
      ${coverage.length ? `<section class="trace-coverage"><h2>Not recorded</h2><ul>${coverage.map(stage => `<li>${esc(stageLabel(stage))}: not recorded</li>`).join('')}</ul></section>` : ''}
      ${end ? `<section class="trace-terminal" data-terminal="${end.kind}"><span>Terminal</span><strong>${esc(end.label)}</strong><p>${esc(end.detail || '')}</p></section>` : ''}` : ''}
    </section>`;
  }

  function renderDetail(panel, value, options) {
    const item = object(value);
    panel.replaceChildren();
    const header = node('header');
    header.append(node('p', 'eyebrow', stageLabel(item.stage)), node('h2', '', item.chosen || item.event || 'Unknown decision'));
    panel.append(header);
    const rationale = node('section');
    rationale.append(node('h3', '', 'Rationale'), node('p', '', item.rationale || 'Not recorded'));
    panel.append(rationale);
    const facts = node('dl', 'trace-facts');
    const addFact = (label, text) => { facts.append(node('dt', '', label), node('dd', '', text)); };
    addFact('Actor', [item.actor?.kind, item.actor?.name, item.actor?.model, item.actor?.version].filter(Boolean).join(' · ') || 'not recorded');
    addFact('Cost', item.cost ? costLabel(item.cost) : 'not recorded');
    addFact('At', item.at || 'not recorded');
    panel.append(facts);
    const evidence = node('section');
    evidence.append(node('h3', '', 'Inputs and evidence'));
    const list = node('ul', 'trace-evidence');
    if (positive(item.pr) && options.repository) {
      const link = node('a', '', `PR #${item.pr}`);
      link.href = safeUrl(`https://github.com/${options.repository}/pull/${item.pr}`);
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const row = node('li'); row.append(link); list.append(row);
    }
    for (const input of array(item.inputs)) {
      const entry = object(input), row = node('li');
      row.append(node('strong', '', entry.kind || 'evidence'), node('span', '', entry.summary || ''));
      const url = safeUrl(entry.ref);
      if (url) {
        const link = node('a', '', entry.ref);
        link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; row.append(link);
      } else if (entry.ref) {
        const reference = node('code', '', entry.ref);
        const copy = node('button', 'trace-copy', 'Copy');
        copy.type = 'button'; copy.dataset.copy = entry.ref; copy.setAttribute('aria-label', `Copy ${entry.ref}`);
        row.append(reference, copy);
      }
      list.append(row);
    }
    if (!list.children.length) list.append(node('li', 'muted', 'No inputs recorded.'));
    evidence.append(list); panel.append(evidence);
  }

  function bind(host, graph, options) {
    const values = new Map(array(graph?.nodes).map(item => [String(item.decision_id), item]));
    const buttons = [...host.querySelectorAll('[data-trace-select]')];
    const panel = host.querySelector('[data-trace-detail]');
    const board = host.querySelector('[data-trace-board]');
    const svg = host.querySelector('[data-trace-lines]');
    const dated = [...host.querySelectorAll('[data-trace-node]')]
      .filter(item => Number.isFinite(new Date(item.dataset.at || '').valueOf()))
      .sort((left, right) => new Date(left.dataset.at) - new Date(right.dataset.at));
    let cutoff = dated.length ? new Date(dated.at(-1).dataset.at).valueOf() : Infinity;

    const drawEdges = () => {
      if (!board || !svg || !host.isConnected) return;
      svg.replaceChildren();
      const width = board.scrollWidth, height = board.scrollHeight;
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.setAttribute('width', width); svg.setAttribute('height', height);
      const rect = board.getBoundingClientRect();
      const byId = new Map([...host.querySelectorAll('[data-trace-node]')].map(item => [item.dataset.traceNode, item]));
      for (const edge of array(graph?.edges)) {
        const source = byId.get(String(edge.from)), target = byId.get(String(edge.to));
        if (!source || !target || source.hidden || target.hidden) continue;
        const a = source.getBoundingClientRect(), b = target.getBoundingClientRect();
        const x1 = a.right - rect.left + board.scrollLeft, y1 = a.top + a.height / 2 - rect.top + board.scrollTop;
        const x2 = b.left - rect.left + board.scrollLeft, y2 = b.top + b.height / 2 - rect.top + board.scrollTop;
        const bend = Math.max(24, Math.abs(x2 - x1) / 2);
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M${x1} ${y1} C${x1 + bend} ${y1},${x2 - bend} ${y2},${x2} ${y2}`);
        path.setAttribute('class', `trace-edge${target.classList.contains('is-synthesized') ? ' is-synthesized' : ''}`);
        svg.append(path);
        const label = document.createElementNS(svg.namespaceURI, 'text');
        label.setAttribute('x', String((x1 + x2) / 2)); label.setAttribute('y', String((y1 + y2) / 2 - 6));
        label.setAttribute('class', 'trace-edge-label'); label.textContent = edge.relation || 'follows'; svg.append(label);
      }
    };
    const scheduleEdges = () => typeof requestAnimationFrame === 'function' ? requestAnimationFrame(drawEdges) : drawEdges();
    const applyVisibility = () => {
      const closed = [...host.querySelectorAll('[data-trace-loop]:not([open])')].map(item => new Set((item.dataset.loopMembers || '').split(',')));
      for (const item of host.querySelectorAll('[data-trace-node]')) {
        const after = Number.isFinite(new Date(item.dataset.at || '').valueOf()) && new Date(item.dataset.at).valueOf() > cutoff;
        item.hidden = after || closed.some(group => group.has(item.dataset.traceNode));
      }
      scheduleEdges();
    };
    const select = id => {
      const value = values.get(String(id));
      if (!value || !panel) return;
      buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.traceSelect === String(id))));
      renderDetail(panel, value, options);
    };
    const navigate = ticket => {
      if (ticket && typeof options.onTicket === 'function') options.onTicket(ticket);
    };

    host.addEventListener('click', event => {
      const selected = event.target.closest('[data-trace-select]');
      if (selected) { select(selected.dataset.traceSelect); return; }
      const ticket = event.target.closest('[data-trace-ticket]');
      if (ticket) { navigate(positive(ticket.dataset.traceTicket)); return; }
      const copy = event.target.closest('[data-copy]');
      if (copy && navigator.clipboard) navigator.clipboard.writeText(copy.dataset.copy).catch(() => {});
    });
    host.querySelector('[data-trace-picker]')?.addEventListener('submit', event => {
      event.preventDefault(); navigate(positive(new FormData(event.currentTarget).get('ticket')));
    });
    host.querySelector('[data-trace-range]')?.addEventListener('input', event => {
      const selected = dated[Number(event.target.value)];
      cutoff = selected ? new Date(selected.dataset.at).valueOf() : Infinity;
      const output = host.querySelector('[data-trace-time]');
      if (output) output.textContent = selected?.dataset.at || 'time not recorded';
      applyVisibility();
    });
    host.querySelectorAll('[data-trace-loop]').forEach(details => details.addEventListener('toggle', applyVisibility));
    buttons.forEach((button, index) => button.addEventListener('keydown', event => {
      const keys = {ArrowRight:1, ArrowDown:1, ArrowLeft:-1, ArrowUp:-1};
      let target = keys[event.key] == null ? null : index + keys[event.key];
      if (event.key === 'Home') target = 0;
      if (event.key === 'End') target = buttons.length - 1;
      if (target == null) return;
      event.preventDefault();
      const visible = buttons.filter(item => !item.closest('[data-trace-node]').hidden);
      const current = visible.indexOf(button);
      const next = event.key === 'Home' ? visible[0] : event.key === 'End' ? visible.at(-1) : visible[current + keys[event.key]];
      next?.focus();
    }));
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(scheduleEdges) : null;
    if (observer && board) observer.observe(board);
    host._traceCleanup = () => observer?.disconnect();
    if (buttons.length) select(buttons[0].dataset.traceSelect);
    applyVisibility();
  }

  function render(host, graph, options = {}) {
    host._traceCleanup?.();
    host.innerHTML = markup(graph, options);
    bind(host, graph, options);
  }

  async function mount(host, options = {}) {
    const ticket = positive(options.ticket);
    if (!ticket) {
      render(host, {ticket:null,nodes:[],edges:[],loops:[],coverage:[]}, options);
      return;
    }
    const request = Symbol('trace');
    host._traceRequest = request;
    host.innerHTML = `<p class="empty">Loading decision trace for ticket #${ticket}…</p>`;
    try {
      const query = new URLSearchParams({ticket:String(ticket)});
      if (options.repository) query.set('repository', options.repository);
      const graph = await WorkspaceAPI.json(`/api/trace?${query}`);
      if (host._traceRequest === request && host.isConnected) render(host, graph, options);
    } catch (error) {
      if (host._traceRequest === request && host.isConnected) {
        host.innerHTML = `<p class="error">${esc(error.message || error)}</p>`;
      }
    }
  }

  window.WorkspaceTrace = Object.freeze({markup, mount, render, safeUrl});
})();
