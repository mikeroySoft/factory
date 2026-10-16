(() => {
  'use strict';

  let active = null;
  const cache = new Map();
  const text = value => value == null ? '' : String(value);
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const esc = value => text(value).replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
  const array = value => Array.isArray(value) ? value : [];

  async function history(repository, force = false) {
    const record = cache.get(repository) || {value: null, promise: null};
    cache.set(repository, record);
    if (record.value && !force) return record.value;
    if (record.promise) return record.promise;
    record.promise = WorkspaceAPI.json(`/api/codebase?${new URLSearchParams({repository})}`).then(value => {
      if (!value || !['building','ready','error'].includes(value.status)) throw new Error('The codebase cache returned an invalid status envelope.');
      if (value.data && value.data.repo !== repository) throw new Error('The codebase cache belongs to a different repository.');
      record.value = value;
      return value;
    }).finally(() => { record.promise = null; });
    return record.promise;
  }

  function safeRepo(value) {
    const parts = text(value).split('/');
    return parts.length === 2 && parts.every(part => /^[A-Za-z0-9_.-]+$/.test(part) && !['.','..'].includes(part)) ? parts : null;
  }
  function sourceUrl(repo, sha, path, line = 0) {
    const parts = safeRepo(repo);
    const pathParts = text(path).split('/');
    if (!parts || !/^[0-9a-f]{7,64}$/i.test(text(sha)) || pathParts.some(part => !part || ['.','..'].includes(part))) return '';
    return `https://github.com/${parts.map(encodeURIComponent).join('/')}/blob/${encodeURIComponent(sha)}/${pathParts.map(encodeURIComponent).join('/')}${number(line) > 0 ? `#L${Math.floor(number(line))}` : ''}`;
  }

  function mountCodebase(host, repository) {
    active = WorkspaceCodebase.mount(host, repository);
    return active;
  }

  const NODES = [
    ['issues','GitHub issues','External source',60,60], ['triage','Triage','Deterministic preflight',410,60], ['dispatch','Dispatch','Bounded controller',760,60],
    ['worktree','Worktree','Isolated local state',760,230], ['worker','Worker','Label-routed command',410,230], ['gate','Gate','Independent checks',60,230],
    ['review','Review','SHA-bound verdict',60,400], ['pr','Pull request','CI-gated merge',410,400], ['outcome','Outcome evidence','Owner attribution',760,400]
  ];
  const EDGES = [
    ['M300 103H410',355,85,'needs-triage'], ['M650 103H760',705,85,'ready-for-agent'],
    ['M880 146V230',915,193,'claim'], ['M760 273H650',705,255,'prompt'],
    ['M410 273H300',355,255,'commits'], ['M300 304H410',355,334,'bounded retry',true],
    ['M180 316V400',210,364,'pass'], ['M260 400V365H480V316',370,352,'revise',true],
    ['M300 443H410',355,425,'approve'], ['M650 443H760',705,425,'milestone only']
  ];
  const BOUNDARIES = {
    issues:['Canonical issue and PR state remains on the provider. Browser rows are bounded observations, not an exhaustive repository census.',['factory/evidence.py','factory/plan.py']],
    triage:['Deterministic preflight precedes model recommendations. Routing a case does not execute it or establish delivery.',['factory/triage.py']],
    dispatch:['Sync, serialized landing, review intake, and the configured manager pass precede new claims. Concurrency, attempts, wall-clock budget, and review rounds remain configured; this workspace adds no global WIP cap.',['factory/dispatch.py','factory/config.py']],
    worktree:['Per-case worktrees retain prompts, handoffs, and gate reports. Case locks survive worktree cleanup; the merge lock serializes landing. Separately confirmed cleanup cannot race a live claim.',['factory/dispatch.py','factory/decisions.py']],
    worker:['Runs the configured, label-routed command under the admitted case contract. FM chat is not a worker terminal and cannot run this command.',['factory/dispatch.py','factory/config.py']],
    gate:['Fixed conflict-marker and leak scans surround configured checks, which run in declaration order with timeouts. Exclusive checks use the host lock. Retained reports, not worker claims, supply passing evidence.',['factory/gate.py']],
    review:['Review evidence binds to the exact head. Failed gates, exhausted reviews, and budget overruns escalate; an old verdict is not authority over new commits.',['factory/dispatch.py']],
    pr:['Approved, green, current-head PRs without a human veto land one per pass. The manager can re-escalate red, stale, or late-feedback PRs. Merge is not release, installation, health, or accepted usefulness.',['factory/dispatch.py','factory/manage.py']],
    outcome:['Owner attestations bind to the current canonical initiative revision and retain their author and evidence URL. Edited, stale, or incomplete evidence stays unknown; comments are not machine verification.',['factory/outcomes.py','factory/roadmap.py']]
  };
  const SUPPORT = [
    ['operator','Human control and passive observation','The workspace combines bounded GitHub, local runtime, systemd, gate, and review evidence. Prepare records an exact proposal without mutation; apply rechecks actor, target, claims, and head. Durable receipts survive restart; partial failures stop later operations. Re-observe the target rather than interpreting acknowledgment as success. FM remains citation-gated and read-only.',['factory/workspace.py','factory/decisions.py','factory/dashboard.py','factory/briefing.py']],
    ['codebase','Codebase history','Graphify extracts commit-pinned structure from local Git objects without switching revisions. Stable file identities survive renames; snapshots publish atomically. The workspace reads published caches and exposes missing coverage. Reading a view does not request extraction or acquire the pipeline journal lock.',['factory/codebase.py','factory/workspace.py']],
    ['manager','Manager and learning loops','The configured manager assesses opt-in viability, handles untouched escalations, waits on CI, and delivers current-head feedback. Plan, evidence, and chat expose bounded decision context. Learn distils runtime outcomes into repository lessons for later workers.',['factory/manage.py','factory/chat.py','factory/evidence.py','factory/learn.py']],
    ['handoff','Routed human handoffs','The final manager pass publishes only after automatic recovery is terminal, disabled, exhausted, or unavailable. One durable request identity per escalation generation survives uncertain publication and restart. Validated owners receive the handoff; human replies and edits stop conflicting recovery but do not authorize retry, approval, or merge.',['factory/handoff.py','factory/manage.py']]
  ];
  const make = (tag, className, content) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = text(content);
    return node;
  };
  const lineNumber = value => Math.max(0, Math.floor(number(value)));
  const kindName = value => {
    switch (text(value)) {
      case 'action': return 'Action';
      case 'state': return 'State';
      case 'out': return 'Out';
      case 'view': return 'View';
      default: return 'Step';
    }
  };
  const kindClass = value => ['action','state','out','view'].includes(text(value)) ? text(value) : 'step';

  function safeHttpUrl(value, line = 0) {
    if (!value) return '';
    try {
      const url = new URL(text(value), window.location.href);
      if (!['http:', 'https:'].includes(url.protocol)) return '';
      if (lineNumber(line) && !url.hash) url.hash = `L${lineNumber(line)}`;
      return url.href;
    } catch {
      return '';
    }
  }

  function normalizeTicketFlow(value) {
    if (!value || !Array.isArray(value.nodes) || !Array.isArray(value.edges) || !value.source) {
      throw new Error('The ticket-flow API returned an invalid payload.');
    }
    const ids = new Set();
    const nodes = value.nodes.map((node, index) => {
      const id = text(node?.id);
      if (!id || ids.has(id)) throw new Error(`The ticket-flow API returned an invalid step at position ${index + 1}.`);
      ids.add(id);
      return {id, label:text(node.label) || id, kind:text(node.kind), group:text(node.group), line:lineNumber(node.line)};
    });
    const edges = value.edges.map((edge, index) => {
      const from = text(edge?.from), to = text(edge?.to);
      if (!ids.has(from) || !ids.has(to)) throw new Error(`The ticket-flow API returned a transition with an unknown step at position ${index + 1}.`);
      return {
        from,
        to,
        label:text(edge.label),
        gap:Boolean(edge.gap),
        gapDetail:typeof edge.gap === 'string' && edge.gap !== 'true' ? edge.gap : '',
        line:lineNumber(edge.line),
        citations:array(edge.citations).map(citation => ({
          symbol:text(citation?.symbol),
          path:text(citation?.path),
          line:lineNumber(citation?.line),
          url:text(citation?.url)
        }))
      };
    });
    return {
      nodes,
      edges,
      source:{path:text(value.source.path) || 'docs/ticket-flow.md', revision:text(value.source.revision), url:text(value.source.url)}
    };
  }

  function mountTicketFlow(section, controller) {
    const state = section.querySelector('[data-ticket-state]');
    const shell = section.querySelector('[data-ticket-shell]');
    const search = section.querySelector('[data-ticket-search]');
    const select = section.querySelector('[data-ticket-select]');
    const results = section.querySelector('[data-ticket-results]');
    const summary = section.querySelector('[data-ticket-summary]');
    const stage = section.querySelector('[data-ticket-stage]');
    const ledger = section.querySelector('[data-ticket-ledger]');
    const reset = section.querySelector('[data-ticket-reset]');
    let flow = null;
    let nodesById = new Map();
    let selected = '';
    let requestVersion = 0;
    let requestController = null;
    const sourceEvidence = (line, label) => {
      const url = safeHttpUrl(flow.source.url, line);
      const node = make(url ? 'a' : 'span', url ? '' : 'ticket-flow-unverified', label);
      if (url) {
        node.href = url;
        node.target = '_blank';
        node.rel = 'noopener noreferrer';
      } else {
        node.append(document.createTextNode(' · link unavailable'));
      }
      return node;
    };
    const citationEvidence = citation => {
      const label = [citation.symbol, citation.path && `${citation.path}${citation.line ? `:${citation.line}` : ''}`].filter(Boolean).join(' · ') || 'Code citation';
      const url = safeHttpUrl(citation.url);
      const node = make(url ? 'a' : 'span', url ? '' : 'ticket-flow-unverified', label);
      if (url) {
        node.href = url;
        node.target = '_blank';
        node.rel = 'noopener noreferrer';
      } else {
        node.append(document.createTextNode(' · link unavailable'));
      }
      return node;
    };
    const stepButton = (node, className = 'ticket-flow-node') => {
      const button = make('button', className);
      button.type = 'button';
      button.dataset.ticketNode = node.id;
      button.dataset.kind = kindClass(node.kind);
      button.setAttribute('aria-label', `Open ${node.label}, ${kindName(node.kind)}`);
      button.append(make('strong', '', node.label), make('span', '', `${kindName(node.kind)} · ${node.line ? `document line ${node.line}` : 'document line unavailable'}`));
      return button;
    };
    const focusHeading = () => {
      if (controller.disposed) return;
      const heading = stage.querySelector('[data-ticket-focus-heading]');
      if (heading) heading.focus({preventScroll:true});
    };

    function renderList() {
      const query = search.value.trim().toLocaleLowerCase();
      const matching = flow.nodes.filter(node => `${node.label} ${node.id} ${node.group} ${node.kind}`.toLocaleLowerCase().includes(query));
      const options = matching.map(node => {
        const option = make('option', '', `${node.label} — ${kindName(node.kind)}${node.group ? ` · ${node.group}` : ''}`);
        option.value = node.id;
        return option;
      });
      if (!options.length) {
        const option = make('option', '', 'No steps match this search');
        option.disabled = true;
        options.push(option);
      }
      select.replaceChildren(...options);
      if (matching.some(node => node.id === selected)) select.value = selected;
      else select.selectedIndex = -1;
      results.textContent = `${matching.length} of ${flow.nodes.length} canonical steps`;
      reset.disabled = !selected && !search.value;
    }

    function renderOverview() {
      const groups = new Map();
      for (const node of flow.nodes) {
        const group = node.group || 'Cross-view and external';
        if (!groups.has(group)) groups.set(group, []);
        groups.get(group).push(node);
      }
      const gaps = flow.edges.filter(edge => edge.gap).length;
      const wrapper = make('div', 'ticket-flow-overview');
      const heading = make('h3', '', 'Canonical ticket-flow overview');
      heading.id = 'ticket-flow-stage-heading';
      wrapper.append(heading, make('p', 'ticket-flow-intro', `All ${flow.nodes.length} steps are shown below in canonical order. The ${flow.edges.length} transitions${gaps ? `, including ${gaps} documented gaps,` : ''} appear without omission in each step’s focused view and in the printed transition index.`));
      const map = make('div', 'ticket-flow-map');
      map.setAttribute('role', 'group');
      map.setAttribute('aria-label', 'All canonical ticket-flow steps grouped by view');
      for (const [group, nodes] of groups) {
        const groupSection = make('section', 'ticket-flow-group');
        groupSection.append(make('h4', '', group));
        const list = make('div', 'ticket-flow-group-steps');
        list.append(...nodes.map(node => stepButton(node)));
        groupSection.append(list);
        map.append(groupSection);
      }
      wrapper.append(map);
      stage.replaceChildren(wrapper);
      stage.setAttribute('aria-labelledby', heading.id);
      summary.textContent = 'Overview. Select any step in the map or searchable list to inspect every incoming, outgoing, and self transition.';
    }

    function transitionCard(edge, direction) {
      const nodes = nodesById;
      const neighborId = direction === 'incoming' ? edge.from : edge.to;
      const neighbor = nodes.get(neighborId);
      const card = make('article', `ticket-flow-transition${edge.gap ? ' is-gap' : ''}`);
      const header = make('header');
      header.append(make('span', 'ticket-flow-direction', direction === 'incoming' ? 'Incoming' : direction === 'outgoing' ? 'Outgoing' : 'Self transition'));
      if (edge.gap) header.append(make('span', 'ticket-flow-gap', 'Documented gap'));
      card.append(header);
      const route = make('p', 'ticket-flow-route');
      route.append(make('span', '', `${nodes.get(edge.from)?.label || edge.from} → ${nodes.get(edge.to)?.label || edge.to}`));
      card.append(route, make('p', 'ticket-flow-condition', edge.label || 'No transition condition is documented.'));
      if (edge.gapDetail) card.append(make('p', 'ticket-flow-gap-detail', edge.gapDetail));
      if (neighbor && direction !== 'self') {
        const neighborButton = stepButton(neighbor, 'ticket-flow-neighbor');
        neighborButton.querySelector('span').textContent = direction === 'incoming' ? 'Open source step' : 'Open destination step';
        card.append(neighborButton);
      }
      const evidence = make('ul', 'ticket-flow-citations');
      const canonical = make('li');
      canonical.append(sourceEvidence(edge.line, `${flow.source.path}${edge.line ? `:${edge.line}` : ''} · canonical transition`));
      evidence.append(canonical);
      if (edge.citations.length) {
        for (const citation of edge.citations) {
          const item = make('li');
          item.append(citationEvidence(citation));
          evidence.append(item);
        }
      } else {
        evidence.append(make('li', 'ticket-flow-unverified', 'No verified code citation was returned for this transition.'));
      }
      card.append(evidence);
      return card;
    }

    function transitionLane(title, edges, direction) {
      const lane = make('section', `ticket-flow-lane ${direction}`);
      lane.append(make('h4', '', `${title} · ${edges.length}`));
      if (edges.length) lane.append(...edges.map(edge => transitionCard(edge, direction)));
      else lane.append(make('p', 'empty', `No ${direction} transitions are documented for this step.`));
      return lane;
    }

    function renderFocusDiagram(node, incoming, outgoing, loops) {
      const collect = (edges, key) => {
        const grouped = new Map();
        for (const edge of edges) {
          const id = edge[key];
          if (!grouped.has(id)) grouped.set(id, {node:nodesById.get(id), count:0});
          grouped.get(id).count++;
        }
        return [...grouped.values()].filter(item => item.node);
      };
      const sources = collect(incoming, 'from');
      const destinations = collect(outgoing, 'to');
      const rowCount = Math.max(sources.length, destinations.length, 1);
      const width = 1000;
      const height = Math.max(360, rowCount * 88 + 180);
      const nodeWidth = 240;
      const nodeHeight = 60;
      const selectedWidth = 280;
      const selectedHeight = Math.max(84, (rowCount - 1) * 88 + 84);
      const leftX = 24;
      const selectedX = (width - selectedWidth) / 2;
      const rightX = width - nodeWidth - 24;
      const selectedY = (height - selectedHeight) / 2;
      const laneY = (index, count) => height / 2 + (index - (count - 1) / 2) * 88;
      const attachY = (index, count) => laneY(index, count);
      const ns = 'http://www.w3.org/2000/svg';
      const figure = make('figure', 'ticket-flow-focus-diagram');
      const svg = document.createElementNS(ns, 'svg');
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.setAttribute('role', 'group');
      svg.setAttribute('aria-labelledby', 'ticket-flow-diagram-title ticket-flow-diagram-desc');
      const draw = (tag, attrs = {}, content, parent = svg) => {
        const item = document.createElementNS(ns, tag);
        for (const [name, value] of Object.entries(attrs)) item.setAttribute(name, value);
        if (content !== undefined) item.textContent = content;
        parent.append(item);
        return item;
      };
      draw('title', {id:'ticket-flow-diagram-title'}, `${node.label} ticket-flow neighborhood`);
      draw('desc', {id:'ticket-flow-diagram-desc'}, `Directional diagram with ${sources.length} incoming neighbor steps, ${destinations.length} outgoing neighbor steps, and ${loops.length} self transitions. Select a neighbor to focus it.`);
      const defs = draw('defs');
      const marker = draw('marker', {id:'ticket-flow-focus-arrow',viewBox:'0 0 8 8',refX:'7',refY:'4',markerWidth:'7',markerHeight:'7',orient:'auto'}, undefined, defs);
      draw('path', {d:'M0 0L8 4L0 8Z'}, undefined, marker);
      const connectors = draw('g', {class:'ticket-flow-svg-connectors','aria-hidden':'true'});
      const elbow = (sx, sy, tx, ty, bend) => {
        if (Math.abs(sy - ty) < 1) return `M${sx} ${sy}H${tx}`;
        const direction = ty > sy ? 1 : -1;
        const radius = Math.min(8, Math.abs(ty - sy) / 2);
        return `M${sx} ${sy}H${bend - radius}Q${bend} ${sy} ${bend} ${sy + direction * radius}V${ty - direction * radius}Q${bend} ${ty} ${bend + radius} ${ty}H${tx}`;
      };
      sources.forEach((item, index) => {
        const y = laneY(index, sources.length);
        draw('path', {d:elbow(leftX + nodeWidth, y, selectedX, attachY(index, sources.length), 340),'marker-end':'url(#ticket-flow-focus-arrow)'}, undefined, connectors);
      });
      destinations.forEach((item, index) => {
        const y = laneY(index, destinations.length);
        draw('path', {d:elbow(selectedX + selectedWidth, attachY(index, destinations.length), rightX, y, 660),'marker-end':'url(#ticket-flow-focus-arrow)'}, undefined, connectors);
      });
      if (loops.length) {
        const startX = selectedX + selectedWidth * .68;
        const endX = selectedX + selectedWidth * .32;
        const top = selectedY - 52;
        draw('path', {d:`M${startX} ${selectedY}V${top + 8}Q${startX} ${top} ${startX - 8} ${top}H${endX + 8}Q${endX} ${top} ${endX} ${top + 8}V${selectedY}`,'marker-end':'url(#ticket-flow-focus-arrow)'}, undefined, connectors);
        draw('text', {x:String((startX + endX) / 2),y:String(top - 10),class:'ticket-flow-svg-loop'}, `${loops.length} self transition${loops.length === 1 ? '' : 's'}`);
      }
      const shortLabel = value => {
        const characters = Array.from(value);
        return characters.length > 30 ? `${characters.slice(0, 28).join('')}…` : value;
      };
      const drawNode = (item, x, y, heightValue, meta, interactive, focused = false) => {
        const attrs = {
          class:`ticket-flow-svg-node kind-${kindClass(item.kind)}${focused ? ' is-selected' : ''}`,
          'aria-label':`${item.label}, ${kindName(item.kind)}${interactive ? ', select to focus' : ', selected step'}`
        };
        if (interactive) {
          attrs.href = '#';
          attrs.tabindex = '0';
          attrs['data-ticket-node'] = item.id;
        } else {
          attrs.role = 'img';
        }
        const group = draw(interactive ? 'a' : 'g', attrs);
        draw('title', {}, item.label, group);
        draw('rect', {x:String(x),y:String(y - heightValue / 2),width:String(interactive ? nodeWidth : selectedWidth),height:String(heightValue),rx:item.kind === 'state' ? String(heightValue / 2) : '6'}, undefined, group);
        draw('text', {x:String(x + (interactive ? nodeWidth : selectedWidth) / 2),y:String(y - 3),class:'ticket-flow-svg-label'}, shortLabel(item.label), group);
        draw('text', {x:String(x + (interactive ? nodeWidth : selectedWidth) / 2),y:String(y + 18),class:'ticket-flow-svg-meta'}, meta, group);
      };
      sources.forEach((item, index) => drawNode(item.node, leftX, laneY(index, sources.length), nodeHeight, `${item.count} incoming transition${item.count === 1 ? '' : 's'}`, true));
      destinations.forEach((item, index) => drawNode(item.node, rightX, laneY(index, destinations.length), nodeHeight, `${item.count} outgoing transition${item.count === 1 ? '' : 's'}`, true));
      drawNode(node, selectedX, height / 2, selectedHeight, `${kindName(node.kind)} · selected`, false, true);
      figure.append(svg, make('figcaption', '', `Connected neighborhood for ${node.label}. Parallel transitions share one connector and retain every condition and citation below.`));
      return figure;
    }

    function renderFocus() {
      const node = nodesById.get(selected);
      if (!node) {
        selected = '';
        renderList();
        renderOverview();
        return;
      }
      const incoming = flow.edges.filter(edge => edge.to === node.id && edge.from !== node.id);
      const outgoing = flow.edges.filter(edge => edge.from === node.id && edge.to !== node.id);
      const loops = flow.edges.filter(edge => edge.from === node.id && edge.to === node.id);
      const wrapper = make('div', 'ticket-flow-focus');
      const head = make('header', 'ticket-flow-focus-head');
      const back = make('button', 'btn', 'Back to overview');
      back.type = 'button';
      back.dataset.ticketBack = '';
      const titleWrap = make('div');
      const heading = make('h3', '', node.label);
      heading.id = 'ticket-flow-focus-heading';
      heading.tabIndex = -1;
      heading.dataset.ticketFocusHeading = '';
      const meta = make('p', '', `${kindName(node.kind)}${node.group ? ` · ${node.group}` : ''}`);
      titleWrap.append(heading, meta);
      head.append(back, titleWrap);
      const focusCard = make('article', 'ticket-flow-selected');
      focusCard.dataset.kind = kindClass(node.kind);
      focusCard.append(make('p', 'eyebrow', 'Selected canonical step'), make('h4', '', node.label));
      const evidence = make('p', 'ticket-flow-selected-evidence');
      evidence.append(sourceEvidence(node.line, `${flow.source.path}${node.line ? `:${node.line}` : ''} · canonical step`));
      focusCard.append(evidence);
      const grid = make('div', 'ticket-flow-focus-grid');
      grid.append(transitionLane('Incoming transitions', incoming, 'incoming'), transitionLane('Outgoing transitions', outgoing, 'outgoing'));
      wrapper.append(head, renderFocusDiagram(node, incoming, outgoing, loops), focusCard, grid);
      if (loops.length) wrapper.append(transitionLane('Self transitions', loops, 'self'));
      stage.replaceChildren(wrapper);
      stage.setAttribute('aria-labelledby', 'ticket-flow-focus-heading');
      summary.textContent = `${node.label}: ${incoming.length} incoming, ${outgoing.length} outgoing, and ${loops.length} self transitions.`;
      requestAnimationFrame(focusHeading);
    }

    function renderLedger() {
      const nodes = nodesById;
      const sourceHead = make('header', 'ticket-flow-ledger-head');
      const titleWrap = make('div');
      const heading = make('h3', '', 'Canonical document evidence');
      heading.id = 'ticket-flow-evidence-heading';
      const source = make('p', '');
      source.append(sourceEvidence(0, flow.source.path));
      if (flow.source.revision) source.append(document.createTextNode(` · revision ${flow.source.revision}`));
      titleWrap.append(heading, source);
      sourceHead.append(titleWrap, make('span', 'badge', `${flow.nodes.length} steps · ${flow.edges.length} transitions`));

      const stepSection = make('section', 'ticket-flow-step-index');
      stepSection.append(make('h4', '', 'Step index'));
      const stepList = make('ul');
      for (const node of flow.nodes) {
        const item = make('li');
        item.append(make('strong', '', node.label), document.createTextNode(` · ${kindName(node.kind)}${node.group ? ` · ${node.group}` : ''} · `), sourceEvidence(node.line, node.line ? `line ${node.line}` : 'line unavailable'));
        stepList.append(item);
      }
      stepSection.append(stepList);

      const edgeSection = make('section', 'ticket-flow-edge-index');
      edgeSection.append(make('h4', '', 'Transition index'));
      const edgeList = make('ol');
      for (const edge of flow.edges) {
        const item = make('li', edge.gap ? 'is-gap' : '');
        const route = make('p', 'ticket-flow-index-route');
        route.append(make('strong', '', `${nodes.get(edge.from)?.label || edge.from} → ${nodes.get(edge.to)?.label || edge.to}`));
        if (edge.gap) route.append(make('span', 'ticket-flow-gap', 'Documented gap'));
        item.append(route, make('p', 'ticket-flow-condition', edge.label || 'No transition condition is documented.'));
        const citations = make('div', 'ticket-flow-index-citations');
        citations.append(sourceEvidence(edge.line, `${flow.source.path}${edge.line ? `:${edge.line}` : ''}`));
        for (const citation of edge.citations) citations.append(citationEvidence(citation));
        if (!edge.citations.length) citations.append(make('span', 'ticket-flow-unverified', 'No verified code citation returned'));
        item.append(citations);
        edgeList.append(item);
      }
      edgeSection.append(edgeList);
      ledger.replaceChildren(sourceHead, stepSection, edgeSection);
      ledger.setAttribute('aria-labelledby', heading.id);
    }

    function choose(id) {
      if (!flow || !nodesById.has(id)) return;
      selected = id;
      renderList();
      renderFocus();
    }

    function showLoadState(kind, message) {
      shell.hidden = true;
      state.hidden = false;
      state.setAttribute('role', kind === 'error' ? 'alert' : 'status');
      state.className = `ticket-flow-state ${kind}`;
      const heading = make('h3', '', kind === 'error' ? 'Ticket flow unavailable' : 'Loading canonical ticket flow');
      const detail = make('p', '', message);
      const children = [heading, detail];
      if (kind === 'error') {
        const retry = make('button', 'btn', 'Try again');
        retry.type = 'button';
        retry.dataset.ticketRetry = '';
        children.push(retry);
      }
      state.replaceChildren(...children);
      section.setAttribute('aria-busy', kind === 'loading' ? 'true' : 'false');
    }

    async function load() {
      const version = ++requestVersion;
      requestController?.abort();
      requestController = new AbortController();
      showLoadState('loading', 'Reading the fixed Factory reference. This does not inspect the selected target repository or run the pipeline.');
      try {
        const payload = await WorkspaceAPI.json('/api/ticket-flow', {signal:requestController.signal});
        if (controller.disposed || version !== requestVersion) return;
        flow = normalizeTicketFlow(payload);
        nodesById = new Map(flow.nodes.map(node => [node.id, node]));
        selected = nodesById.has('RFA') ? 'RFA' : flow.edges[0]?.from || flow.nodes[0]?.id || '';
        search.value = '';
        state.hidden = true;
        shell.hidden = false;
        section.setAttribute('aria-busy', 'false');
        renderList();
        if (selected) renderFocus();
        else renderOverview();
        renderLedger();
      } catch (error) {
        if (controller.disposed || version !== requestVersion || error?.name === 'AbortError') return;
        showLoadState('error', `${error?.message || error} Retry the read-only reference request; no pipeline activity was started.`);
      }
    }

    search.addEventListener('input', renderList);
    select.addEventListener('change', () => choose(select.value));
    reset.addEventListener('click', () => {
      const prior = selected;
      selected = '';
      search.value = '';
      renderList();
      renderOverview();
      search.focus({preventScroll:true});
      summary.textContent = prior ? 'Explorer reset to the full canonical overview.' : 'Search cleared; the full canonical overview remains visible.';
    });
    section.addEventListener('click', event => {
      const node = event.target.closest('[data-ticket-node]');
      if (node && section.contains(node)) {
        event.preventDefault();
        choose(node.dataset.ticketNode);
        return;
      }
      const back = event.target.closest('[data-ticket-back]');
      const overview = event.target.closest('[data-ticket-overview]');
      if (back || overview) {
        const prior = selected;
        selected = '';
        renderList();
        renderOverview();
        if (back) {
          const target = [...stage.querySelectorAll('[data-ticket-node]')].find(button => button.dataset.ticketNode === prior);
          target?.focus({preventScroll:true});
        }
        return;
      }
      if (event.target.closest('[data-ticket-retry]')) load();
    });
    load();
    return {dispose() { requestVersion++; requestController?.abort(); }};
  }

  function mountAtlas(host, repository) {
    const controller={host,repository,disposed:false};
    active=controller;
    const boundaries=[...NODES.map(([id,label])=>[id,label,...BOUNDARIES[id]]),...SUPPORT];
    host.innerHTML=`<div class="workspace-tool atlas-tool"><header class="atlas-head"><div><h2>Factory reference atlas</h2><p>A reference model of the Factory—not extracted architecture of <strong>${esc(repository)}</strong>, not live activity, and not proof of the installed version. Source availability is checked against the selected repository's published cache.</p></div><button class="btn" type="button" data-atlas-print>Print Atlas</button></header>
      <section class="section-block ticket-flow" data-ticket-flow aria-labelledby="ticket-flow-heading" aria-busy="true"><header><div><p class="eyebrow">Fixed Factory reference · not live flow</p><h2 id="ticket-flow-heading">Every path a ticket can take</h2><p>This canonical Factory lifecycle is independent of the selected target repository <strong>${esc(repository)}</strong>. It reads documentation and provenance only; opening this explorer never runs, queues, or refreshes pipeline work.</p></div><span class="badge">Reference</span></header>
        <div class="ticket-flow-state loading" data-ticket-state role="status"><h3>Loading canonical ticket flow</h3><p>Reading the fixed Factory reference.</p></div>
        <div data-ticket-shell hidden>
          <div class="ticket-flow-explorer">
            <aside class="ticket-flow-controls" aria-label="Ticket-flow step finder">
              <label for="ticket-flow-search">Search canonical steps</label>
              <input id="ticket-flow-search" type="search" autocomplete="off" spellcheck="false" placeholder="Label, ID, view, or kind" data-ticket-search>
              <label for="ticket-flow-select">Select a step</label>
              <select id="ticket-flow-select" size="9" data-ticket-select aria-describedby="ticket-flow-results"></select>
              <p id="ticket-flow-results" data-ticket-results role="status"></p>
              <div class="ticket-flow-actions"><button class="btn" type="button" data-ticket-overview>Overview</button><button class="btn" type="button" data-ticket-reset disabled>Reset explorer</button></div>
            </aside>
            <div class="ticket-flow-workspace">
              <p class="ticket-flow-summary" data-ticket-summary role="status" aria-live="polite"></p>
              <div class="ticket-flow-stage" data-ticket-stage role="region"></div>
            </div>
          </div>
          <section class="ticket-flow-ledger" data-ticket-ledger></section>
        </div>
      </section>
      <section class="atlas-diagram" tabindex="0" role="region" aria-label="Scrollable Factory pipeline reference"><svg viewBox="0 0 1080 530" role="img" aria-labelledby="atlas-title atlas-desc"><title id="atlas-title">Factory pipeline reference</title><desc id="atlas-desc">Issues pass through triage, dispatch, worktree, worker, gate, review and pull request. Gate retries and review revisions return to the worker. Outcome evidence is separate from merge. Linked boundaries and source citations are printed below.</desc><defs><marker id="atlas-arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="var(--muted)"/></marker></defs>
      ${EDGES.map(([path,x,y,label,back])=>`<path class="atlas-edge${back?' is-return':''}" d="${path}" marker-end="url(#atlas-arrow)"/><text class="atlas-edge-label" x="${x}" y="${y}">${esc(label)}</text>`).join('')}
      ${NODES.map(([id,label,sub,x,y])=>`<a class="atlas-node" href="#atlas-boundary-${id}" aria-label="${esc(`${label}: read boundary and source evidence`)}"><rect x="${x}" y="${y}" width="240" height="86" rx="6"/><text x="${x+120}" y="${y+36}" text-anchor="middle">${esc(label)}</text><text class="atlas-node-sub" x="${x+120}" y="${y+59}" text-anchor="middle">${esc(sub)}</text></a>`).join('')}</svg></section>
      <section class="section-block"><header><div><h2>Human decision → observed result</h2><p>Canceling a proposal has no target effect. Drift or a live claim can refuse application; a partial receipt is not success.</p></div></header><div class="atlas-diagram" tabindex="0" role="region" aria-label="Scrollable human control reference"><svg viewBox="0 0 1080 190" role="img" aria-labelledby="atlas-control-title atlas-control-desc"><title id="atlas-control-title">Guarded human control cycle</title><desc id="atlas-control-desc">Inspect evidence, prepare an exact proposal, confirm it, apply through guarded operations, then observe resulting provider state. Fresh observations inform the next decision. FM may advise but cannot execute.</desc><path class="atlas-edge is-return" d="M938 66V28H138V66" marker-end="url(#atlas-arrow)"/><text class="atlas-edge-label" x="538" y="17">fresh observation—not assumed success</text>
      ${[['Inspect','Bounded evidence'],['Prepare','Exact proposal'],['Confirm','Explicit authority'],['Apply','Durable receipt'],['Observe','Resulting state']].map(([label,sub],index)=>{const x=48+index*200;return `${index<4?`<path class="atlas-edge" d="M${x+180} 105H${x+200}" marker-end="url(#atlas-arrow)"/>`:''}<a class="atlas-node" href="#atlas-boundary-operator" aria-label="${label}: read human control boundary"><rect x="${x}" y="66" width="180" height="78" rx="6"/><text x="${x+90}" y="98" text-anchor="middle">${label}</text><text class="atlas-node-sub" x="${x+90}" y="120" text-anchor="middle">${sub}</text></a>`;}).join('')}</svg></div></section>
      <section class="atlas-sources"><h3>Source evidence for ${esc(repository)}</h3><p data-atlas-cache role="status">Resolving the published codebase cache…</p><button class="btn btn-small" type="button" data-tool-retry>Refresh cached evidence</button><p>Only paths present in that snapshot receive commit-pinned links. All boundaries and evidence appear below without expansion.</p></section>
      <section class="atlas-boundaries">${boundaries.map(([id,label,detail])=>`<article class="atlas-boundary" id="atlas-boundary-${id}"><h3>${esc(label)}</h3><p>${esc(detail)}</p><div class="atlas-links" data-atlas-sources="${id}"></div></article>`).join('')}</section></div>`;
    const ticketFlow=mountTicketFlow(host.querySelector('[data-ticket-flow]'),controller);
    async function loadEvidence() {
      const button=host.querySelector('[data-tool-retry]'),notice=host.querySelector('[data-atlas-cache]');
      if(button.disabled)return;
      button.disabled=true;notice.textContent='Resolving the published codebase cache…';
      try {
        const envelope=await history(repository,true);
        if(controller.disposed)return;
        const data=envelope.data||{},latest=array(data.snapshots).at(-1),sha=latest?.sha||data.tip||'';
        const files=new Set(array(latest?.files).map(file=>text(file.path)));
        notice.textContent=latest?`Published snapshot ${text(sha).slice(0,12)} · generated ${data.generated_at||envelope.generated_at||'time unavailable'} · monitor ${envelope.provenance?.monitor_status||'not asserted here'}. ${envelope.error?.message||''}`:`Reference diagrams remain available; source evidence is ${envelope.status==='building'?'building':'unavailable'}. ${text(envelope.error?.message||envelope.error||'No retained snapshot was returned.')}`;
        for(const [id,, ,paths] of boundaries)host.querySelector(`[data-atlas-sources="${id}"]`).innerHTML=paths.map(path=>{const url=files.has(path)?sourceUrl(repository,sha,path):'';return url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(path)} ↗</a>`:`<span>${esc(path)} · not verified in this cache</span>`;}).join('');
      } catch(error) {
        if(!controller.disposed)notice.textContent=`Source refresh unavailable: ${error.message||error}. Reference diagrams and any previously displayed commit-pinned evidence are retained.`;
      } finally {if(!controller.disposed)button.disabled=false;}
    }
    const handleClick=event=>{if(event.target.closest('[data-tool-retry]'))loadEvidence();if(event.target.closest('[data-atlas-print]'))window.print();};
    host.addEventListener('click',handleClick);
    controller.dispose=()=>{ticketFlow.dispose();host.removeEventListener('click',handleClick);};
    loadEvidence();
    return controller;
  }

  function dispose() { if (!active) return; active.disposed = true; active.dispose?.(); active = null; }
  function mount(host, kind, repository) {
    if (!(host instanceof Element) || !['codebase','atlas'].includes(kind)) throw new TypeError('WorkspaceTools.mount requires a host and codebase or atlas kind.');
    dispose();
    return kind === 'codebase' ? mountCodebase(host, repository) : mountAtlas(host, repository);
  }
  window.WorkspaceTools = Object.freeze({mount, dispose});
})();
