(() => {
  'use strict';

  const STAGES = ['ticket','triage','binding','admission','worker_select','claim','plan','attempt','gate','review','approved','manager','merge','merged','wontfix','escalate','learn','curate'];
  const LIVE_LANES = ['triage','worker','gate','reviewer','manager','watch','learn'];
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
  const EDGE_TYPES = Object.freeze({
    follows:{className:'relation-follows',color:'var(--info)',dash:'none'},
    retries:{className:'relation-retries',color:'var(--warn)',dash:'8 5'},
    blocked_by:{className:'relation-blocked-by',color:'var(--bad)',dash:'3 4'},
    split_from:{className:'relation-split-from',color:'var(--accent)',dash:'10 4'},
    unblocked_by:{className:'relation-unblocked-by',color:'var(--good)',dash:'10 3 2 3'},
    overrides:{className:'relation-overrides',color:'var(--muted)',dash:'2 3'},
  });
  const edgeType = relation => ({...(EDGE_TYPES[String(relation || '')] || EDGE_TYPES.follows)});
  const nodeId = value => String(value?.decision_id || '');
  const stageColumns = nodes => new Map(orderedStages(nodes).map((stage, index) => [stage, index]));
  function bundleEdges(nodes, edges) {
    const columns = stageColumns(nodes);
    const stages = new Map(nodes.map(item => [nodeId(item), columns.get(item.stage || 'unknown')]));
    return edges.flatMap((edge, index) => Math.abs((stages.get(String(edge.to)) ?? 0) - (stages.get(String(edge.from)) ?? 0)) > 1 ? [index] : []);
  }
  function bundleSpacing(groups) {
    const largest = groups.reduce((maximum, members) => Math.max(maximum, members.length), 1);
    const total = groups.reduce((sum, members) => sum + members.length, 0);
    return {
      stripe:Math.min(2.4, 28 / Math.max(1, largest - 1)),
      track:Math.min(2.4, 44 / Math.max(1, total)),
    };
  }
  function crossingCount(nodes, edges) {
    const columns = stageColumns(nodes);
    const order = new Map(), stage = new Map(), counts = new Map();
    for (const item of nodes) {
      const name = item.stage || 'unknown', index = counts.get(name) || 0;
      stage.set(nodeId(item), name);
      order.set(nodeId(item), index);
      counts.set(name, index + 1);
    }
    let total = 0;
    for (let left = 0; left < edges.length; left += 1) {
      const a = edges[left], aFrom = stage.get(String(a.from)), aTo = stage.get(String(a.to));
      if (aFrom == null || aTo == null || columns.get(aFrom) === columns.get(aTo)) continue;
      for (let right = left + 1; right < edges.length; right += 1) {
        const b = edges[right];
        if (stage.get(String(b.from)) !== aFrom || stage.get(String(b.to)) !== aTo) continue;
        const sourceDelta = order.get(String(a.from)) - order.get(String(b.from));
        const targetDelta = order.get(String(a.to)) - order.get(String(b.to));
        if (sourceDelta * targetDelta < 0) total += 1;
      }
    }
    return total;
  }
  function orderNodes(nodes, edges) {
    const stages = orderedStages(nodes);
    const stageIndexes = new Map(stages.map((stage, index) => [stage, index]));
    const groups = new Map(stages.map(stage => [stage, nodes.filter(item => (item.stage || 'unknown') === stage)]));
    const neighbors = new Map(nodes.map(item => [nodeId(item), []]));
    for (const edge of edges) {
      neighbors.get(String(edge.from))?.push(String(edge.to));
      neighbors.get(String(edge.to))?.push(String(edge.from));
    }
    const sweep = (indexes, direction) => {
      for (const stageIndex of indexes) {
        const members = groups.get(stages[stageIndex]);
        const previous = new Map(members.map((item, index) => [nodeId(item), index]));
        const positions = new Map();
        for (const [name, rows] of groups) rows.forEach((item, index) => positions.set(nodeId(item), {stage:stageIndexes.get(name),index}));
        members.sort((left, right) => {
          const score = item => {
            const linked = neighbors.get(nodeId(item)).map(id => positions.get(id))
              .filter(value => value && (value.stage - stageIndex) * direction < 0);
            return linked.length ? linked.reduce((sum, value) => sum + value.index, 0) / linked.length : previous.get(nodeId(item));
          };
          return score(left) - score(right) || previous.get(nodeId(left)) - previous.get(nodeId(right)) || nodeId(left).localeCompare(nodeId(right));
        });
      }
    };
    for (let pass = 0; pass < 3; pass += 1) {
      sweep(stages.map((_, index) => index).slice(1), 1);
      sweep(stages.map((_, index) => index).slice(0, -1).reverse(), -1);
    }
    return stages.flatMap(stage => groups.get(stage));
  }
  const relationLabel = relation => ({
    follows:'follows',retries:'retry',blocked_by:'blocked by',split_from:'split from',
    unblocked_by:'unblocked by',overrides:'override / rejected',
  }[relation] || stageLabel(relation || 'follows'));

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
      <button class="trace-node-drag" type="button" data-trace-drag="${esc(id)}" aria-label="${esc(`Drag ${value.chosen || value.event || 'decision'}`)}">Drag</button>
      <button class="trace-node-select" type="button" data-trace-select="${esc(id)}" aria-pressed="false">
        <span class="trace-node-meta"><b>${esc(stageLabel(value.stage))}</b>${value.stage === 'plan' && actor ? `<i>${esc(actor)}</i>` : ''}${value.synthesized === true ? '<i>synthesized</i>' : ''}${id === frontier ? '<i>frontier</i>' : ''}</span>
        <strong>${esc(value.chosen || value.event || 'unknown')}</strong>
        <time>${esc(value.at || 'time not recorded')}</time>
      </button>
      ${options.length ? `<div class="trace-options" aria-label="Decision options">${options.map(option => optionMarkup(option, value.chosen)).join('')}</div>` : ''}
      ${ticket ? `<button class="trace-ticket-link" type="button" data-trace-ticket="${ticket}">Open ticket #${ticket}</button>` : ''}
    </article>`;
  }

  const modeMarkup = mode => `<div class="trace-modes" role="tablist" aria-label="Breadboard mode">
    <button type="button" role="tab" data-trace-mode="history" aria-selected="${mode === 'history'}">Historical</button>
    <button type="button" role="tab" data-trace-mode="live" aria-selected="${mode === 'live'}">Live</button>
  </div>`;
  const elapsedLabel = seconds => {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    if (value < 60) return `${value}s`;
    if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s`;
    return `${Math.floor(value / 3600)}h ${Math.floor(value % 3600 / 60)}m`;
  };
  function liveRows(snapshot, now = Date.now()) {
    const value = object(snapshot);
    const threshold = Number.isFinite(Number(value.stale_after_seconds)) && Number(value.stale_after_seconds) > 0
      ? Number(value.stale_after_seconds) : 60;
    return array(value.executions).filter(item => {
      const row = object(item);
      return LIVE_LANES.includes(row.lane) && positive(row.ticket) && !row.ended_at
        && ['active','stale'].includes(String(row.state || 'active'));
    }).map(item => {
      const row = object(item);
      const observed = new Date(row.observed_at || '').valueOf();
      const stale = row.stale === true || row.state === 'stale'
        || !Number.isFinite(observed) || (now - observed) / 1000 > threshold;
      return {...row, stale, status:stale ? 'stale' : 'running'};
    });
  }
  function liveGraph(rows) {
    const nodes = rows.map(row => ({...row, decision_id:String(row.execution_id), stage:row.lane}));
    const edges = [];
    const tickets = new Map();
    for (const row of rows) {
      const ticket = positive(row.ticket);
      if (!tickets.has(ticket)) tickets.set(ticket, []);
      tickets.get(ticket).push(row);
    }
    for (const members of tickets.values()) {
      members.sort((left, right) => LIVE_LANES.indexOf(left.lane) - LIVE_LANES.indexOf(right.lane)
        || new Date(left.entered_at || 0) - new Date(right.entered_at || 0)
        || String(left.execution_id).localeCompare(String(right.execution_id)));
      for (let index = 1; index < members.length; index += 1) {
        if (members[index - 1].lane !== members[index].lane) {
          edges.push({from:String(members[index - 1].execution_id),to:String(members[index].execution_id),relation:'follows'});
        }
      }
    }
    return {nodes,edges};
  }
  function liveMarkup(snapshot, options = {}) {
    const value = object(snapshot);
    const now = Number.isFinite(Number(options.now)) ? Number(options.now) : Date.now();
    const rows = liveRows(value, now);
    const graph = liveGraph(rows);
    const groups = LIVE_LANES.map(lane => {
      const members = rows.filter(row => row.lane === lane);
      const cards = members.map(row => {
        const entered = new Date(row.entered_at || '').valueOf();
        const elapsed = elapsedLabel(Number.isFinite(entered) ? (now - entered) / 1000 : 0);
        return `<article class="trace-node live-execution${row.stale ? ' is-stale' : ''}" data-trace-node="${esc(row.execution_id)}" data-lane="${esc(lane)}" data-at="${esc(row.entered_at || '')}" data-status="${row.status}">
          <button class="trace-node-drag" type="button" data-trace-drag="${esc(row.execution_id)}" aria-label="${esc(`Drag ticket #${row.ticket}`)}">Drag</button>
          <button class="live-execution-open" type="button" data-trace-ticket="${row.ticket}">
            <span class="trace-node-meta"><b>${esc(row.agent || 'agent unavailable')}</b><i>${esc(row.harness || 'harness unavailable')}</i></span>
            <strong>#${row.ticket} · ${esc(row.title || 'Title unavailable')}</strong>
            <time>${esc(elapsed)} elapsed · ${row.stale ? 'Stale' : 'Running'}</time>
          </button>
        </article>`;
      }).join('');
      return `<section class="trace-stage" data-stage="${lane}"><h2>${esc(stageLabel(lane))}<span>${members.length}</span></h2><div class="trace-stage-nodes">${cards || '<p class="empty">No active executions</p>'}</div></section>`;
    }).join('');
    const relations = graph.edges.map(edge => `<li>follows: ${esc(edge.from)} → ${esc(edge.to)}</li>`).join('');
    return `<section class="trace-tool live-breadboard">
      ${modeMarkup('live')}
      <div class="trace-status"><p><strong>Live fleet:</strong> ${rows.length} active execution${rows.length === 1 ? '' : 's'}</p><p>Updated ${esc(value.generated_at || 'time unavailable')}</p></div>
      <div class="trace-board" data-trace-board><svg class="trace-lines" data-trace-lines aria-hidden="true"></svg><div class="trace-stages" role="list" aria-label="Live executions by pipeline loop">${groups}</div><ol class="sr-only" aria-label="Live ticket movement">${relations}</ol></div>
    </section>`;
  }

  function markup(graph, options = {}) {
    const value = object(graph);
    const edges = array(value.edges);
    const nodes = orderNodes(array(value.nodes), edges);
    const loops = array(value.loops);
    const ticket = positive(options.ticket ?? value.ticket);
    const recent = recentNumbers(options.recentTickets);
    const loopByNode = new Map();
    loops.forEach((loop, index) => array(loop?.nodes).forEach(id => loopByNode.set(String(id), index)));
    const stages = orderedStages(nodes);
    const dated = array(value.nodes).filter(item => Number.isFinite(new Date(item?.at || '').valueOf()));
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
    const legendTypes = [...new Set(edges.map(edge => edge.relation || 'follows'))];
    const legend = legendTypes.map(relation => {
      const type = edgeType(relation);
      return `<li><span class="trace-legend-line ${type.className}" style="--trace-color:${type.color};--trace-dash:${type.dash}"></span>${esc(relationLabel(relation))}</li>`;
    }).join('');
    const relations = edges.map(edge => `<li>${esc(edge.relation || 'follows')}: ${esc(edge.from)} → ${esc(edge.to)}</li>`).join('');
    const coverage = array(value.coverage);
    return `<section class="trace-tool" data-ticket="${ticket || ''}">
      ${modeMarkup('history')}
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
      <ul class="trace-legend" aria-label="Relationship legend">${legend}</ul>
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
    const listeners = new AbortController();
    const listenerOptions = {signal:listeners.signal};
    const values = new Map(array(graph?.nodes).map(item => [String(item.decision_id), item]));
    const edges = array(graph?.edges);
    const bundled = new Set(bundleEdges(array(graph?.nodes), edges));
    const buttons = [...host.querySelectorAll('[data-trace-select]')];
    const panel = host.querySelector('[data-trace-detail]');
    const board = host.querySelector('[data-trace-board]');
    const svg = host.querySelector('[data-trace-lines]');
    const dated = [...host.querySelectorAll('[data-trace-node]')]
      .filter(item => Number.isFinite(new Date(item.dataset.at || '').valueOf()))
      .sort((left, right) => new Date(left.dataset.at) - new Date(right.dataset.at));
    const reduceMotion = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    const wires = new Map();
    let cutoff = dated.length ? new Date(dated.at(-1).dataset.at).valueOf() : Infinity;
    let pinned = null, hovered = null, scheduleFrame = null, motionFrame = null, ribbonLayer = null, edgeLayer = null;

    const svgNode = (tag, attributes = {}) => {
      const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
      return element;
    };
    if (svg) {
      const marker = svgNode('marker', {id:'trace-arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,markerUnits:'userSpaceOnUse',orient:'auto-start-reverse'});
      marker.append(svgNode('path', {d:'M 0 0 L 10 5 L 0 10 z',class:'trace-arrow'}));
      const defs = svgNode('defs'); defs.append(marker);
      ribbonLayer = svgNode('g', {class:'trace-ribbon-layer'});
      edgeLayer = svgNode('g', {class:'trace-wire-layer'});
      svg.append(defs, ribbonLayer, edgeLayer);
    }
    const roundedPath = points => {
      const radius = 8;
      let path = `M${points[0].x} ${points[0].y}`;
      for (let index = 1; index < points.length - 1; index += 1) {
        const previous = points[index - 1], current = points[index], next = points[index + 1];
        const beforeDistance = Math.hypot(current.x - previous.x, current.y - previous.y);
        const afterDistance = Math.hypot(next.x - current.x, next.y - current.y);
        const beforeRatio = Math.min(radius, beforeDistance / 2) / (beforeDistance || 1);
        const afterRatio = Math.min(radius, afterDistance / 2) / (afterDistance || 1);
        const before = {x:current.x + (previous.x - current.x) * beforeRatio,y:current.y + (previous.y - current.y) * beforeRatio};
        const after = {x:current.x + (next.x - current.x) * afterRatio,y:current.y + (next.y - current.y) * afterRatio};
        path += ` L${before.x} ${before.y} Q${current.x} ${current.y} ${after.x} ${after.y}`;
      }
      return `${path} L${points.at(-1).x} ${points.at(-1).y}`;
    };
    const updateWire = (wire, points) => {
      const path = roundedPath(points);
      wire.paths.forEach(item => item.setAttribute('d', path));
      wire.label.setAttribute('x', String((points[2].x + points[3].x) / 2));
      wire.label.setAttribute('y', String((points[2].y + points[3].y) / 2 - 7));
    };
    const createWire = route => {
      const type = edgeType(route.edge.relation);
      const group = svgNode('g', {
        class:`trace-edge-group ${type.className}${route.bundled ? ' is-bundled' : ''}${route.synthesized ? ' is-synthesized' : ''}`,
        'data-trace-edge':route.index,
      });
      group.style.setProperty('--trace-color', type.color);
      group.style.setProperty('--trace-dash', type.dash);
      const paths = [];
      let ribbon = null;
      if (route.bundled) {
        ribbon = svgNode('path', {class:'trace-bundle-ribbon'});
        ribbonLayer.append(ribbon); paths.push(ribbon);
      }
      const edge = svgNode('path', {class:'trace-edge','marker-end':'url(#trace-arrow)'});
      const hit = svgNode('path', {class:'trace-edge-hit'});
      const label = svgNode('text', {class:'trace-edge-label'});
      label.textContent = relationLabel(route.edge.relation);
      group.append(edge, hit, label);
      paths.push(edge, hit);
      edgeLayer.append(group);
      return {group,ribbon,paths,label,current:null};
    };
    const routes = () => {
      if (!board || !svg || !host.isConnected) return [];
      const width = board.scrollWidth, height = board.scrollHeight;
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.setAttribute('width', width); svg.setAttribute('height', height);
      svg.style.width = `${width}px`; svg.style.height = `${height}px`;
      const boardRect = board.getBoundingClientRect();
      const byId = new Map([...host.querySelectorAll('[data-trace-node]')].map(item => [item.dataset.traceNode, item]));
      const stageIndex = new Map([...host.querySelectorAll('[data-stage]')].map((item, index) => [item.dataset.stage, index]));
      const columns = new Map([...byId].map(([id, item]) => [id, stageIndex.get(item.closest('[data-stage]').dataset.stage)]));
      const groups = new Map();
      for (const index of bundled) {
        const edge = edges[index], from = columns.get(String(edge.from)), to = columns.get(String(edge.to));
        if (from == null || to == null) continue;
        const key = `${from}:${to > from ? 'forward' : 'backward'}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(index);
      }
      const bundleInfo = new Map();
      const bundleGroups = [...groups.values()];
      const spacing = bundleSpacing(bundleGroups);
      let track = 8 + spacing.track / 2;
      bundleGroups.forEach(members => members.forEach((index, stripe) => {
        bundleInfo.set(index, {spacing:spacing.stripe,stripe:(stripe - (members.length - 1) / 2) * spacing.stripe,track});
        track += spacing.track;
      }));
      return edges.flatMap((edge, index) => {
        const source = byId.get(String(edge.from)), target = byId.get(String(edge.to));
        if (!source || !target || source.hidden || target.hidden) return [];
        const a = source.getBoundingClientRect(), b = target.getBoundingClientRect();
        const from = columns.get(String(edge.from)), to = columns.get(String(edge.to));
        const x = value => value - boardRect.left + board.scrollLeft;
        const y = value => value - boardRect.top + board.scrollTop;
        const y1 = y(a.top + a.height / 2), y2 = y(b.top + b.height / 2);
        const info = bundleInfo.get(index);
        let points;
        if (from === to) {
          const gutter = x(Math.max(a.right, b.right)) + 18;
          const track = 12 + index % 4 * 9;
          points = [{x:x(a.right),y:y1},{x:gutter,y:y1},{x:gutter,y:track},{x:gutter + 16,y:track},{x:gutter + 16,y:y2},{x:x(b.right),y:y2}];
        } else if (to < from) {
          const stripe = info?.stripe || 0;
          const track = info?.track ?? 12 + index % 4 * 9;
          points = [{x:x(a.left),y:y1},{x:x(a.left) - 16 + stripe,y:y1},{x:x(a.left) - 16 + stripe,y:track},{x:x(b.right) + 16 + stripe,y:track},{x:x(b.right) + 16 + stripe,y:y2},{x:x(b.right),y:y2}];
        } else if (info) {
          const track = info.track;
          points = [{x:x(a.right),y:y1},{x:x(a.right) + 16 + info.stripe,y:y1},{x:x(a.right) + 16 + info.stripe,y:track},{x:x(b.left) - 16 + info.stripe,y:track},{x:x(b.left) - 16 + info.stripe,y:y2},{x:x(b.left),y:y2}];
        } else {
          const gutter = (x(a.right) + x(b.left)) / 2;
          points = [{x:x(a.right),y:y1},{x:gutter,y:y1},{x:gutter,y:y1},{x:gutter,y:y2},{x:gutter,y:y2},{x:x(b.left),y:y2}];
        }
        return [{index,edge,points,bundled:bundled.has(index),spacing:info?.spacing,synthesized:target.classList.contains('is-synthesized')}];
      });
    };
    const applyHighlight = () => {
      const trace = hovered || pinned;
      const activeNodes = new Set(), activeEdges = new Set();
      if (trace?.kind === 'edge') {
        const edge = edges[trace.id];
        if (edge) {
          activeEdges.add(trace.id);
          activeNodes.add(String(edge.from)); activeNodes.add(String(edge.to));
        }
      } else if (trace?.kind === 'node') {
        const id = String(trace.id);
        activeNodes.add(id);
        const walk = (start, reverse) => {
          const queue = [start], seen = new Set(queue);
          while (queue.length) {
            const current = queue.shift();
            edges.forEach((edge, index) => {
              if (String(edge[reverse ? 'to' : 'from']) !== current) return;
              const next = String(edge[reverse ? 'from' : 'to']);
              activeEdges.add(index); activeNodes.add(next);
              if (!seen.has(next)) { seen.add(next); queue.push(next); }
            });
          }
        };
        walk(id, true); walk(id, false);
      }
      board?.classList.toggle('is-tracing', Boolean(trace));
      host.querySelectorAll('[data-trace-node]').forEach(item => item.classList.toggle('is-trace-active', activeNodes.has(item.dataset.traceNode)));
      wires.forEach((wire, index) => wire.group.classList.toggle('is-trace-active', activeEdges.has(index)));
    };
    const drawEdges = () => {
      const next = routes(), visible = new Set(next.map(route => route.index));
      wires.forEach((wire, index) => {
        if (!visible.has(index)) { wire.group.remove(); wire.ribbon?.remove(); wires.delete(index); }
      });
      const moving = [];
      for (const route of next) {
        let wire = wires.get(route.index);
        if (!wire) { wire = createWire(route); wires.set(route.index, wire); }
        wire.group.setAttribute('class', `trace-edge-group ${edgeType(route.edge.relation).className}${route.bundled ? ' is-bundled' : ''}${route.synthesized ? ' is-synthesized' : ''}`);
        if (route.spacing) wire.group.style.setProperty('--trace-stripe-spacing', route.spacing);
        if (!wire.current || reduceMotion) {
          wire.current = route.points.map(point => ({...point}));
          updateWire(wire, wire.current);
        } else {
          moving.push({wire,start:wire.current.map(point => ({...point})),target:route.points});
        }
      }
      applyHighlight();
      if (!moving.length) return;
      if (motionFrame != null) cancelAnimationFrame(motionFrame);
      const started = performance.now();
      const tick = now => {
        const elapsed = Math.min(1, (now - started) / 500);
        const spring = elapsed === 1 ? 1 : 1 - Math.exp(-7 * elapsed) * Math.cos(12 * elapsed);
        for (const move of moving) {
          move.wire.current = move.target.map((point, index) => ({
            x:move.start[index].x + (point.x - move.start[index].x) * spring,
            y:move.start[index].y + (point.y - move.start[index].y) * spring,
          }));
          updateWire(move.wire, move.wire.current);
        }
        motionFrame = elapsed < 1 ? requestAnimationFrame(tick) : null;
      };
      motionFrame = requestAnimationFrame(tick);
    };
    const scheduleEdges = () => {
      if (scheduleFrame != null) cancelAnimationFrame(scheduleFrame);
      if (typeof requestAnimationFrame === 'function') scheduleFrame = requestAnimationFrame(() => { scheduleFrame = null; drawEdges(); });
      else drawEdges();
    };
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
    const clearHighlight = () => { pinned = null; hovered = null; applyHighlight(); };

    host.addEventListener('click', event => {
      const mode = event.target.closest('[data-trace-mode]');
      if (mode && typeof options.onMode === 'function') {
        options.onMode(mode.dataset.traceMode);
        return;
      }
      const selected = event.target.closest('[data-trace-select]');
      if (selected) {
        select(selected.dataset.traceSelect);
        pinned = {kind:'node',id:selected.dataset.traceSelect}; applyHighlight();
        return;
      }
      const edge = event.target.closest('[data-trace-edge]');
      if (edge) { pinned = {kind:'edge',id:Number(edge.dataset.traceEdge)}; applyHighlight(); return; }
      const ticket = event.target.closest('[data-trace-ticket]');
      if (ticket) { navigate(positive(ticket.dataset.traceTicket)); return; }
      const copy = event.target.closest('[data-copy]');
      if (copy && navigator.clipboard) { navigator.clipboard.writeText(copy.dataset.copy).catch(() => {}); return; }
      if (event.target.closest('[data-trace-board]') && !event.target.closest('[data-trace-node]')) clearHighlight();
    }, listenerOptions);
    host.addEventListener('pointerover', event => {
      const target = event.target.closest?.('[data-trace-edge],[data-trace-node]');
      if (!target || target.contains(event.relatedTarget)) return;
      hovered = target.matches('[data-trace-edge]')
        ? {kind:'edge',id:Number(target.dataset.traceEdge)}
        : {kind:'node',id:target.dataset.traceNode};
      applyHighlight();
    }, listenerOptions);
    host.addEventListener('pointerout', event => {
      const target = event.target.closest?.('[data-trace-edge],[data-trace-node]');
      if (!target || target.contains(event.relatedTarget)) return;
      hovered = null; applyHighlight();
    }, listenerOptions);
    const onEscape = event => {
      if (event.key === 'Escape' && (pinned || hovered)) { clearHighlight(); event.preventDefault(); }
    };
    document.addEventListener('keydown', onEscape, listenerOptions);
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
      const keys = {ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1};
      if (keys[event.key] == null && !['Home','End'].includes(event.key)) return;
      event.preventDefault();
      const visible = buttons.filter(item => !item.closest('[data-trace-node]').hidden);
      const current = visible.indexOf(button);
      const next = event.key === 'Home' ? visible[0] : event.key === 'End' ? visible.at(-1) : visible[current + keys[event.key]];
      next?.focus();
    }));
    host.querySelectorAll('[data-trace-drag]').forEach(handle => {
      const item = handle.closest('[data-trace-node]');
      const move = (x, y) => {
        item.dataset.dragX = String(x); item.dataset.dragY = String(y);
        item.style.transform = `translate(${x}px,${y}px)`;
        scheduleEdges();
      };
      handle.addEventListener('pointerdown', event => {
        const startX = event.clientX, startY = event.clientY;
        const originX = Number(item.dataset.dragX || 0), originY = Number(item.dataset.dragY || 0);
        item.classList.add('is-dragging');
        handle.setPointerCapture(event.pointerId);
        const drag = value => move(originX + value.clientX - startX, originY + value.clientY - startY);
        const stop = value => {
          handle.releasePointerCapture(value.pointerId);
          item.classList.remove('is-dragging');
          handle.removeEventListener('pointermove', drag); handle.removeEventListener('pointerup', stop); handle.removeEventListener('pointercancel', stop);
        };
        handle.addEventListener('pointermove', drag); handle.addEventListener('pointerup', stop); handle.addEventListener('pointercancel', stop);
        event.preventDefault();
      });
      handle.addEventListener('keydown', event => {
        const delta = {ArrowLeft:[-10,0],ArrowRight:[10,0],ArrowUp:[0,-10],ArrowDown:[0,10]}[event.key];
        if (!delta) return;
        move(Number(item.dataset.dragX || 0) + delta[0], Number(item.dataset.dragY || 0) + delta[1]);
        event.preventDefault();
      });
    });
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(scheduleEdges) : null;
    if (observer && board) observer.observe(board);
    host._traceCleanup = () => {
      observer?.disconnect();
      listeners.abort();
      if (scheduleFrame != null) cancelAnimationFrame(scheduleFrame);
      if (motionFrame != null) cancelAnimationFrame(motionFrame);
    };
    if (buttons.length) select(buttons[0].dataset.traceSelect);
    applyVisibility();
  }

  function render(host, graph, options = {}) {
    host._traceCleanup?.();
    host.innerHTML = markup(graph, options);
    bind(host, graph, options);
  }

  function stopLive(host) {
    clearTimeout(host._traceLiveTimer);
    host._traceLiveTimer = null;
  }

  function dispose(host) {
    if (!host) return;
    stopLive(host);
    host._traceRequest = Symbol('disposed');
    host._traceCleanup?.();
  }

  async function loadHistory(host, options) {
    stopLive(host);
    host._traceMode = 'history';
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
        render(host, {ticket,nodes:[],edges:[],loops:[],coverage:[]}, options);
        host.querySelector('.trace-modes')?.insertAdjacentHTML('afterend', `<p class="error">${esc(error.message || error)}</p>`);
      }
    }
  }

  function renderLive(host, snapshot, options) {
    const now = Date.now();
    const rows = liveRows(snapshot, now);
    const graph = liveGraph(rows);
    host._traceCleanup?.();
    host.innerHTML = liveMarkup(snapshot, {...options,now});
    bind(host, graph, options);
  }

  function scheduleLive(host, snapshot, options) {
    stopLive(host);
    const configured = Number(options.refreshSeconds);
    const supplied = Number(snapshot?.refresh_seconds);
    const seconds = configured > 0 ? configured : supplied > 0 ? supplied : 10;
    host._traceLiveTimer = setTimeout(() => {
      if (host.isConnected && host._traceMode === 'live') {
        if (document.visibilityState === 'visible') loadLive(host, options);
        else scheduleLive(host, snapshot, options);
      }
    }, seconds * 1000);
  }

  async function loadLive(host, options) {
    stopLive(host);
    host._traceMode = 'live';
    const request = Symbol('live');
    host._traceRequest = request;
    const query = new URLSearchParams();
    if (options.repository) query.set('repository', options.repository);
    host.innerHTML = `${modeMarkup('live')}<p class="empty">Loading live executions…</p>`;
    try {
      const snapshot = await WorkspaceAPI.json(`/api/live-executions${query.size ? `?${query}` : ''}`);
      if (snapshot?.schema_version !== 1 || !Array.isArray(snapshot.executions)) {
        throw new Error('The live execution response is incomplete.');
      }
      if (host._traceRequest === request && host.isConnected) {
        renderLive(host, snapshot, options);
        scheduleLive(host, snapshot, options);
      }
    } catch (error) {
      if (host._traceRequest === request && host.isConnected) {
        const snapshot = {schema_version:1,generated_at:'unavailable',executions:[]};
        renderLive(host, snapshot, options);
        host.querySelector('.trace-modes')?.insertAdjacentHTML('afterend', `<p class="error">${esc(error.message || error)}</p>`);
        scheduleLive(host, snapshot, options);
      }
    }
  }

  function mount(host, options = {}) {
    const configured = {...options};
    configured.onMode = mode => mode === 'live'
      ? loadLive(host, configured)
      : loadHistory(host, configured);
    loadHistory(host, configured);
  }

  window.WorkspaceTrace = Object.freeze({
    bundleEdges, bundleSpacing, crossingCount, dispose, edgeType, liveGraph, liveMarkup,
    markup, mount, orderNodes, render, safeUrl,
  });
})();
