(() => {
  'use strict';
  function mount(host, repository) {
    host.classList.add('codebase-page');
    host.innerHTML = `
    <div class="cb-meta" id="history-meta" hidden>
      <span id="health" class="cb-health">Loading</span>
      <span>repository <strong id="meta-repo">—</strong></span>
      <span>ref <strong id="meta-ref">—</strong></span>
      <span>history <strong id="meta-count">—</strong></span>
      <span>generated <strong id="meta-freshness">—</strong></span>
      <span>extractor <strong id="meta-extractor">—</strong></span>
    </div>
    <div id="notice" class="cb-notice" aria-live="polite"></div>

<div id="codebase-app">

  <section id="load-state" class="cb-state" role="status" aria-live="polite">
    <h2 id="load-title">Building the codebase map</h2>
    <p id="load-detail">Waiting for commit-pinned structural history from the local Factory monitor.</p>
    <button id="retry" type="button" hidden>Try again</button>
  </section>

  <section id="codebase-main" class="cb-shell" tabindex="-1" hidden>
    <section class="cb-timeline cb-surface" aria-labelledby="timeline-heading">
      <div class="cb-timeline-top">
        <div class="cb-stepper" aria-label="Revision controls">
          <button id="previous" type="button" aria-label="Previous, older revision">Previous</button>
          <button id="next" type="button" aria-label="Next, newer revision">Next</button>
          <button id="latest" class="cb-latest" type="button">Latest</button>
        </div>
        <div class="cb-range-wrap">
          <label for="commit-range"><span id="timeline-heading">Revision</span><span id="range-position">—</span></label>
          <input id="commit-range" type="range" min="0" max="0" value="0" step="1" aria-describedby="commit-subject">
        </div>
        <div class="cb-commit">
          <div class="cb-commit-line"><time id="commit-date">—</time><a id="commit-sha" target="_blank" rel="noopener noreferrer">—</a></div>
          <p id="commit-subject" class="cb-subject">No revision selected</p>
        </div>
      </div>
      <div class="cb-compare">
        <label class="cb-field" for="baseline">
          <span class="cb-label">Compare with baseline</span>
          <select id="baseline"><option value="">None — organization only</option></select>
        </label>
        <p id="compare-summary" class="cb-compare-summary">Choose a baseline to see file and relationship changes.</p>
      </div>
      <details id="warnings" class="cb-warnings" hidden>
        <summary id="warnings-summary">Extraction coverage</summary>
        <ul id="warning-list" class="cb-warning-list"></ul>
      </details>
    </section>

    <div class="cb-workspace">
      <section class="cb-surface" aria-labelledby="map-heading">
        <header class="cb-panel-head">
          <h2 id="map-heading">Repository organization</h2>
          <p id="map-summary" class="cb-panel-note">Stable file slots across the loaded timeline</p>
        </header>
        <div class="cb-map-key" aria-label="Comparison legend">
          <span class="cb-key"><i class="cb-swatch added" aria-hidden="true"></i>Added</span>
          <span class="cb-key"><i class="cb-swatch changed" aria-hidden="true"></i>Changed blob</span>
          <span class="cb-key"><i class="cb-swatch moved" aria-hidden="true"></i>Moved</span>
          <span class="cb-key"><i class="cb-swatch deleted" aria-hidden="true"></i>Deleted</span>
          <span class="cb-key"><i class="cb-swatch coverage" aria-hidden="true"></i>Coverage changed</span>
        </div>
        <div id="map" class="cb-map" aria-label="Folders and files"></div>
      </section>

      <aside class="cb-surface cb-inspector" aria-labelledby="inspector-heading">
        <header class="cb-panel-head"><h2 id="inspector-heading">Inspector</h2></header>
        <div id="inspector" class="cb-inspector-body"><p class="cb-copy">Select a folder or file to inspect its structure and relationships.</p></div>
      </aside>
    </div>
  </section>
</div>

`;
    let disposed = false;
  const byId = id => host.querySelector('#' + CSS.escape(id));
  const dom = {
    main: byId('codebase-main'), loadState: byId('load-state'), loadTitle: byId('load-title'),
    loadDetail: byId('load-detail'), retry: byId('retry'), meta: byId('history-meta'),
    health: byId('health'), repo: byId('meta-repo'), ref: byId('meta-ref'), count: byId('meta-count'),
    freshness: byId('meta-freshness'), extractor: byId('meta-extractor'), notice: byId('notice'),
    previous: byId('previous'), next: byId('next'), latest: byId('latest'), range: byId('commit-range'),
    rangePosition: byId('range-position'), commitDate: byId('commit-date'), commitSha: byId('commit-sha'),
    commitSubject: byId('commit-subject'), baseline: byId('baseline'), compareSummary: byId('compare-summary'),
    warnings: byId('warnings'), warningsSummary: byId('warnings-summary'), warningList: byId('warning-list'),
    map: byId('map'), mapSummary: byId('map-summary'), inspector: byId('inspector')
  };

  let history = null;
  let snapshots = [];
  let slots = [];
  let maxLines = 1;
  let currentSha = '';
  let baselineSha = '';
  let selection = null;
  let followsLatest = true;
  let envelopeStatus = 'building';
  let envelopeError = '';
  let inFlight = false;
  let version = '';
  let pendingBaselineSync = false;

  const make = (tag, className, content) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = String(content);
    return node;
  };
  const string = value => value == null ? '' : String(value);
  const shortSha = sha => string(sha).slice(0, 7) || 'unknown';
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const formatNumber = value => new Intl.NumberFormat().format(number(value));
  const dateValue = value => {
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? null : date;
  };
  const formatDate = (value, options = { dateStyle: 'medium', timeStyle: 'short' }) => {
    const date = dateValue(value);
    return date ? new Intl.DateTimeFormat(undefined, options).format(date) : string(value) || 'Unknown date';
  };
  const relativeDate = value => {
    const date = dateValue(value);
    if (!date) return string(value) || 'unknown';
    const seconds = Math.round((date.valueOf() - Date.now()) / 1000);
    const units = [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60]];
    const [unit, size] = units.find(([, span]) => Math.abs(seconds) >= span) || ['second', 1];
    return new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(Math.round(seconds / size), unit);
  };
  const fileMap = snapshot => new Map((snapshot && Array.isArray(snapshot.files) ? snapshot.files : []).map(file => [string(file.id), file]));
  const snapshotMap = () => new Map(snapshots.map(snapshot => [string(snapshot.sha), snapshot]));
  const selectedSnapshot = () => snapshotMap().get(currentSha) || null;
  const baselineSnapshot = () => baselineSha ? snapshotMap().get(baselineSha) || null : null;

  function safeGithubUrl(kind, sha, path, line) {
    const repo = string(history && history.repo);
    const parts = repo.split('/');
    if (parts.length !== 2 || !parts.every(part => /^[A-Za-z0-9_.-]+$/.test(part) && part !== '.' && part !== '..') || !/^[0-9a-f]{7,64}$/i.test(string(sha))) return '';
    const root = 'https://github.com/' + parts.map(encodeURIComponent).join('/');
    if (kind === 'commit') return root + '/commit/' + encodeURIComponent(string(sha));
    const pathParts = string(path).split('/');
    if (!pathParts.length || pathParts.some(part => !part || part === '.' || part === '..')) return '';
    const suffix = number(line) > 0 ? '#L' + Math.floor(number(line)) : '';
    return root + '/blob/' + encodeURIComponent(string(sha)) + '/' + pathParts.map(encodeURIComponent).join('/') + suffix;
  }

  function sourceLink(label, sha, path, line) {
    const url = safeGithubUrl('blob', sha, path, line);
    const node = make(url ? 'a' : 'span', url ? '' : 'cb-muted', label);
    if (url) {
      node.href = url;
      node.target = '_blank';
      node.rel = 'noopener noreferrer';
    }
    return node;
  }

  function showState(kind, title, detail) {
    dom.main.hidden = true;
    dom.loadState.hidden = false;
    dom.loadState.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    dom.loadState.className = 'cb-state ' + kind;
    dom.loadTitle.textContent = title;
    dom.loadDetail.textContent = detail;
    dom.retry.hidden = kind !== 'error';
  }

  function mergeSlots(incoming) {
    const ordered = (Array.isArray(incoming) ? incoming : [])
      .filter(slot => slot && slot.id != null)
      .slice()
      .sort((a, b) => number(a.order) - number(b.order));
    const seen = new Set(slots.map(slot => string(slot.id)));
    const merged = slots.slice();
    for (const slot of ordered) {
      const id = string(slot.id);
      if (!seen.has(id)) {
        merged.push({ id, group: string(slot.group) || '.', order: number(slot.order) });
        seen.add(id);
      }
    }
    return merged;
  }

  function mergePinnedSnapshots(incoming, oldSnapshots) {
    const merged = (Array.isArray(incoming) ? incoming : []).filter(snapshot => snapshot && snapshot.sha);
    const seen = new Set(merged.map(snapshot => string(snapshot.sha)));
    const wanted = new Set([currentSha, baselineSha].filter(Boolean));
    const pinned = oldSnapshots.filter(snapshot => wanted.has(string(snapshot.sha)) && !seen.has(string(snapshot.sha)));
    return [...pinned, ...merged];
  }

  function applyHistory(data, status, error) {
    if (!data || data.repo !== repository || !Array.isArray(data.snapshots) || !Array.isArray(data.slots)) throw new Error('The codebase API returned an invalid or differently scoped history payload.');
    const nextVersion = [string(data.generated_at), string(data.tip), data.snapshots.length].join('|');
    envelopeStatus = status;
    envelopeError = string(error);
    if (history && version === nextVersion) {
      history = data;
      renderHeader();
      return;
    }

    const oldSnapshots = snapshots.slice();
    const oldLatest = oldSnapshots.at(-1);
    const wasLatest = followsLatest || !currentSha || (oldLatest && currentSha === string(oldLatest.sha));
    history = data;
    version = nextVersion;
    slots = mergeSlots(data.slots);
    snapshots = mergePinnedSnapshots(data.snapshots, oldSnapshots);
    maxLines = snapshots.reduce((maximum, snapshot) => snapshot.files.reduce((value, file) => Math.max(value, number(file.lines)), maximum), 1);

    if (!snapshots.length) {
      renderHeader();
      showState('empty', 'No history is available', 'The observed ref has no extracted commits. Check the ref and extraction warnings, then retry.');
      return;
    }

    if (wasLatest) currentSha = string(snapshots.at(-1).sha);
    else if (!snapshotMap().has(currentSha)) currentSha = string(snapshots[0].sha);
    if (baselineSha && !snapshotMap().has(baselineSha)) baselineSha = '';
    followsLatest = wasLatest;
    if (!selection) {
      const currentFiles = fileMap(selectedSnapshot());
      const first = slots.find(slot => currentFiles.has(string(slot.id)));
      selection = first ? { type: 'group', id: string(first.group) || '.' } : null;
    }

    syncBaselineOptions();
    dom.loadState.hidden = true;
    dom.main.hidden = false;
    dom.meta.hidden = false;
    renderAll();
  }

  function renderHeader() {
    if (!history) return;
    dom.meta.hidden = false;
    dom.main.setAttribute('aria-busy', envelopeStatus === 'building' ? 'true' : 'false');
    dom.health.className = 'cb-health ' + envelopeStatus;
    dom.health.textContent = envelopeStatus === 'ready' ? 'Ready' : envelopeStatus === 'building' ? 'Updating' : 'Update error';
    dom.repo.textContent = string(history.repo) || 'local repository';
    dom.ref.textContent = string(history.ref) || 'unknown';
    const serverCount = Array.isArray(history.snapshots) ? history.snapshots.length : 0;
    const retained = Math.max(0, snapshots.length - serverCount);
    dom.count.textContent = formatNumber(serverCount) + (history.truncated ? ' commits · bounded' : ' commits') + (retained ? ' · ' + retained + ' retained selection' : '');
    dom.freshness.textContent = formatDate(history.generated_at) + ' · ' + relativeDate(history.generated_at);
    dom.extractor.textContent = string(history.extractor) || 'unknown';
    dom.notice.className = 'cb-notice ' + envelopeStatus;
    if (envelopeStatus === 'error') dom.notice.textContent = 'Update failed; showing the last completed history. ' + (envelopeError || 'The local monitor reported an error.');
    else if (envelopeStatus === 'building') dom.notice.textContent = 'History is updating in the background. The last completed map remains available.';
    else dom.notice.textContent = '';
  }

  function syncBaselineOptions() {
    if (document.activeElement === dom.baseline) {
      pendingBaselineSync = true;
      return;
    }
    pendingBaselineSync = false;
    const none = make('option', '', 'None — organization only');
    none.value = '';
    const options = [none];
    for (const snapshot of snapshots) {
      const option = make('option', '', formatDate(snapshot.date, { dateStyle: 'medium' }) + ' · ' + shortSha(snapshot.sha) + ' · ' + string(snapshot.subject));
      option.value = string(snapshot.sha);
      options.push(option);
    }
    dom.baseline.replaceChildren(...options);
    dom.baseline.value = baselineSha;
  }

  const unmapped = (snapshot, id) => snapshot && (snapshot.unmapped || []).includes(id);

  function fileStatuses(current, baseline) {
    if (!baselineSha) return [];
    if (current && !baseline) return [unmapped(baselineSnapshot(), current.id) ? 'coverage' : 'added'];
    if (!current && baseline) return [unmapped(selectedSnapshot(), baseline.id) ? 'coverage' : 'deleted'];
    if (!current || !baseline) return [];
    const statuses = [];
    if (string(current.path) !== string(baseline.path)) statuses.push('moved');
    if (string(current.blob) !== string(baseline.blob)) statuses.push('changed');
    return statuses;
  }

  function comparisonCounts() {
    const current = fileMap(selectedSnapshot());
    const baseline = fileMap(baselineSnapshot());
    const counts = { added: 0, changed: 0, moved: 0, deleted: 0, coverage: 0 };
    for (const slot of slots) for (const status of fileStatuses(current.get(string(slot.id)), baseline.get(string(slot.id)))) counts[status]++;
    return counts;
  }

  function renderTimeline() {
    const snapshot = selectedSnapshot();
    const index = snapshots.findIndex(item => string(item.sha) === currentSha);
    if (!snapshot || index < 0) return;
    dom.range.min = '0';
    dom.range.max = String(Math.max(0, snapshots.length - 1));
    dom.range.value = String(index);
    dom.range.disabled = snapshots.length < 2;
    dom.range.setAttribute('aria-valuetext', formatDate(snapshot.date) + ', ' + string(snapshot.subject) + ', ' + shortSha(snapshot.sha));
    dom.rangePosition.textContent = formatNumber(index + 1) + ' of ' + formatNumber(snapshots.length);
    dom.previous.disabled = index === 0;
    dom.next.disabled = index === snapshots.length - 1;
    dom.latest.disabled = index === snapshots.length - 1;
    dom.commitDate.textContent = formatDate(snapshot.date);
    dom.commitDate.dateTime = string(snapshot.date);
    dom.commitSubject.textContent = string(snapshot.subject) || '(No commit subject)';
    dom.commitSha.textContent = shortSha(snapshot.sha);
    const commitUrl = safeGithubUrl('commit', snapshot.sha);
    if (commitUrl) {
      dom.commitSha.href = commitUrl;
      dom.commitSha.removeAttribute('aria-disabled');
    } else {
      dom.commitSha.removeAttribute('href');
      dom.commitSha.setAttribute('aria-disabled', 'true');
    }
    dom.baseline.value = baselineSha;

    if (!baselineSha) {
      dom.compareSummary.textContent = 'Choose a baseline to compare file blobs, paths, and resolved relationships.';
    } else {
      const baseline = baselineSnapshot();
      const counts = comparisonCounts();
      dom.compareSummary.replaceChildren(
        document.createTextNode('From '),
        make('strong', '', shortSha(baseline && baseline.sha)),
        document.createTextNode(': ' + counts.added + ' added · ' + counts.changed + ' changed · ' + counts.moved + ' moved · ' + counts.deleted + ' deleted' + (counts.coverage ? ' · ' + counts.coverage + ' coverage changes' : ''))
      );
    }
  }

  function renderWarnings() {
    const warnings = [];
    if (history.truncated) warnings.push('Earlier commits are omitted by the configured history limit.');
    const serverShas = new Set((history.snapshots || []).map(snapshot => string(snapshot.sha)));
    if (snapshots.some(snapshot => !serverShas.has(string(snapshot.sha)))) warnings.push('A previously selected revision is retained locally while the bounded history advances.');
    for (const snapshot of snapshots) {
      for (const warning of Array.isArray(snapshot.warnings) ? snapshot.warnings : []) warnings.push(shortSha(snapshot.sha) + ': ' + string(warning));
    }
    dom.warnings.hidden = warnings.length === 0;
    dom.warningsSummary.textContent = warnings.length ? 'Extraction coverage · ' + warnings.length + ' warning' + (warnings.length === 1 ? '' : 's') : 'Extraction coverage';
    dom.warningList.replaceChildren(...warnings.map(warning => make('li', '', warning)));
  }

  function splitPath(path) {
    const value = string(path);
    const slash = value.lastIndexOf('/');
    return slash < 0 ? { name: value || '(unnamed)', directory: '.' } : { name: value.slice(slash + 1) || '(unnamed)', directory: value.slice(0, slash) || '.' };
  }

  function renderMap() {
    const focusKey = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.focusKey : '';
    const current = fileMap(selectedSnapshot());
    const baseline = fileMap(baselineSnapshot());
    const groups = new Map();
    for (const slot of slots) {
      const group = string(slot.group) || '.';
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(slot);
    }
    const currentFiles = [...current.values()];
    const totalLines = currentFiles.reduce((sum, file) => sum + number(file.lines), 0);
    dom.mapSummary.textContent = formatNumber(currentFiles.length) + ' files · ' + formatNumber(groups.size) + ' stable folders · ' + formatNumber(totalLines) + ' measured lines';

    const groupNodes = [];
    for (const [group, groupSlots] of groups) {
      const section = make('section', 'cb-group' + (selection && selection.type === 'group' && selection.id === group ? ' selected' : ''));
      section.setAttribute('aria-label', 'Folder ' + group);
      const header = make('div', 'cb-group-head');
      const groupButton = make('button', '', group);
      groupButton.type = 'button';
      groupButton.setAttribute('aria-pressed', selection && selection.type === 'group' && selection.id === group ? 'true' : 'false');
      groupButton.dataset.focusKey = 'group:' + group;
      groupButton.title = 'Inspect folder ' + group;
      groupButton.addEventListener('click', () => {
        selection = { type: 'group', id: group };
        renderMap();
        renderInspector();
        if (window.innerWidth <= 1040) dom.inspector.scrollIntoView({block: 'start', behavior: 'instant'});
      });
      const liveFiles = groupSlots.map(slot => current.get(string(slot.id))).filter(Boolean);
      const currentDirs = [...new Set(liveFiles.map(file => splitPath(file.path).directory))];
      if (currentDirs.length === 1) groupButton.textContent = currentDirs[0] === '.' ? '(root)' : currentDirs[0];
      groupButton.title = 'Stable area anchored at ' + group + '; current folders: ' + (currentDirs.join(', ') || 'none at this revision');
      const tally = make('span', 'cb-group-tally', liveFiles.length + ' files · ' + formatNumber(liveFiles.reduce((sum, file) => sum + number(file.lines), 0)) + ' lines');
      header.append(groupButton, tally);
      section.append(header);
      const grid = make('div', 'cb-file-grid');

      for (const slot of groupSlots) {
        const id = string(slot.id);
        const currentFile = current.get(id);
        const baselineFile = baseline.get(id);
        const statuses = fileStatuses(currentFile, baselineFile);
        const shown = currentFile || (baselineSha && baselineFile);
        const holder = make('div', 'cb-file-slot' + (shown ? '' : ' vacant'));
        const file = currentFile || baselineFile;
        const button = make('button', 'cb-file-card' + statuses.map(status => ' ' + status).join('') + (selection && selection.type === 'file' && selection.id === id ? ' selected' : ''));
        button.type = 'button';
        button.setAttribute('aria-pressed', selection && selection.type === 'file' && selection.id === id ? 'true' : 'false');
        button.dataset.focusKey = 'file:' + id;
        if (!shown) {
          button.disabled = true;
          button.tabIndex = -1;
          button.setAttribute('aria-hidden', 'true');
        } else {
          const path = splitPath(file.path);
          button.title = string(file.path);
          button.setAttribute('aria-label', string(file.path) + ', ' + (statuses.length ? statuses.join(' and ') : 'present') + ', ' + number(file.lines) + ' lines');
          button.append(make('span', 'cb-file-name', path.name), make('span', 'cb-file-dir', path.directory));
          const foot = make('span', 'cb-file-foot');
          const meter = make('span', 'cb-file-size');
          meter.setAttribute('aria-hidden', 'true');
          const fill = make('span');
          fill.style.width = (100 * number(file.lines) / maxLines) + '%';
          meter.append(fill);
          foot.append(meter);
          const stats = make('span', 'cb-file-stats');
          stats.append(make('span', '', formatNumber(file.lines) + ' lines'), make('span', '', formatNumber(Array.isArray(file.symbols) ? file.symbols.length : 0) + ' callables'));
          foot.append(stats);
          if (statuses.length) {
            const badges = make('span', 'cb-badges');
            for (const status of statuses) badges.append(make('span', 'cb-badge ' + status, status === 'changed' ? 'changed blob' : status));
            foot.append(badges);
          }
          button.append(foot);
          button.addEventListener('click', () => {
            selection = { type: 'file', id };
            renderMap();
            renderInspector();
            if (window.innerWidth <= 1040) dom.inspector.scrollIntoView({block: 'start', behavior: 'instant'});
          });
        }
        holder.append(button);
        grid.append(holder);
      }
      section.append(grid);
      groupNodes.push(section);
    }
    if (!currentFiles.length && !baselineSha) groupNodes.unshift(make('p', 'cb-empty-inline', 'No supported files are present at this revision. Extraction coverage above may explain why.'));
    dom.map.replaceChildren(...groupNodes);
    if (focusKey) {
      const target = [...dom.map.querySelectorAll('[data-focus-key]')].find(node => node.dataset.focusKey === focusKey);
      if (target) target.focus({ preventScroll: true });
    }
  }

  function appendKv(list, label, value) {
    list.append(make('dt', '', label), make('dd', '', value));
  }

  function nearestFile(id) {
    const currentIndex = snapshots.findIndex(snapshot => string(snapshot.sha) === currentSha);
    for (let distance = 0; distance < snapshots.length; distance++) {
      for (const index of [currentIndex - distance, currentIndex + distance]) {
        if (index < 0 || index >= snapshots.length) continue;
        const file = fileMap(snapshots[index]).get(id);
        if (file) return { file, snapshot: snapshots[index] };
      }
    }
    return null;
  }

  function groupedEdges(snapshot, selected) {
    const groups = new Map();
    if (!snapshot) return groups;
    const stableGroups = new Map(slots.map(slot => [string(slot.id), string(slot.group) || '.']));
    for (const edge of Array.isArray(snapshot.edges) ? snapshot.edges : []) {
      const source = string(edge.source), target = string(edge.target);
      const matches = selected.type === 'file'
        ? source === selected.id || target === selected.id
        : stableGroups.get(source) === selected.id || stableGroups.get(target) === selected.id;
      if (!matches) continue;
      const key = source + '\u001f' + target + '\u001f' + string(edge.relation);
      if (!groups.has(key)) groups.set(key, { source, target, relation: string(edge.relation) || 'relationship', evidence: [] });
      groups.get(key).evidence.push(edge);
    }
    return groups;
  }
  function renderNeighborhood(container, selected, records) {
    if (selected.type !== 'file' || !records.length) return;
    const files = new Map([...fileMap(baselineSnapshot()), ...fileMap(selectedSnapshot())]);
    const neighbors = [...new Set(records.flatMap(record => [record.source, record.target]))]
      .filter(id => id !== selected.id);
    const visible = neighbors.slice(0, 4);
    const height = Math.max(64, visible.length * 64);
    const figure = make('figure', 'cb-neighborhood');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 300 ${height}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-labelledby', 'neighbors-title neighbors-desc');
    const draw = (tag, attrs, text, parent = svg) => {
      const node = document.createElementNS(svg.namespaceURI, tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
      if (text !== undefined) node.textContent = text;
      parent.append(node);
      return node;
    };
    draw('title', {id: 'neighbors-title'}, 'Selected file and its neighbors');
    draw('desc', {id: 'neighbors-desc'}, 'Arrows show resolved relationship directions. Full names, confidence and source citations follow below.');
    const marker = draw('marker', {id: 'neighbor-arrow', viewBox: '0 0 8 8', refX: '7', refY: '4', markerWidth: '6', markerHeight: '6', orient: 'auto-start-reverse'}, undefined, draw('defs', {}));
    draw('polygon', {points: '0,0 8,4 0,8', fill: 'var(--cb-accent)'}, undefined, marker);
    visible.forEach((id, index) => {
      const fromY = height / 2 - 20 + (40 * (index + 1) / (visible.length + 1));
      const toY = index * 64 + 32;
      const bend = 132 + index * 8;
      const outgoing = records.some(record => record.source === selected.id && record.target === id);
      const incoming = records.some(record => record.target === selected.id && record.source === id);
      const sign = Math.sign(toY - fromY);
      const attrs = {d: sign
        ? `M120 ${fromY} H${bend - 6} Q${bend} ${fromY} ${bend} ${fromY + sign * 6} V${toY - sign * 6} Q${bend} ${toY} ${bend + 6} ${toY} H180`
        : `M120 ${fromY} H180`};
      if (records.filter(record => record.source === id || record.target === id).every(record => record.removed)) {
        attrs['stroke-dasharray'] = '4 3';
      }
      if (outgoing) attrs['marker-end'] = 'url(#neighbor-arrow)';
      if (incoming) attrs['marker-start'] = 'url(#neighbor-arrow)';
      draw('path', attrs);
    });
    const node = (id, x, y, focused) => {
      const group = draw('g', {class: focused ? 'cb-focus' : ''});
      const path = files.get(id)?.path || id;
      draw('title', {}, path, group);
      draw('rect', {x, y, width: '120', height: '40', rx: '4'}, undefined, group);
      const label = splitPath(path).name;
      draw('text', {x: x + 8, y: y + 24}, label.length > 15 ? label.slice(0, 13) + '…' : label, group);
    };
    node(selected.id, 0, height / 2 - 20, true);
    visible.forEach((id, index) => node(id, 180, index * 64 + 12, false));
    figure.append(svg, make('figcaption', '', 'Resolved neighborhood' + (neighbors.length > visible.length ? ` · ${neighbors.length - visible.length} more neighbors in the evidence below` : '') + '. Dashed: only in baseline. Full evidence below.'));
    container.append(figure);
  }


  function renderRelations(container, selected, showInferred) {
    const currentSnapshot = selectedSnapshot();
    const baseSnapshot = baselineSnapshot();
    const currentGroups = groupedEdges(currentSnapshot, selected);
    const baseGroups = groupedEdges(baseSnapshot, selected);
    const keys = new Set([...currentGroups.keys(), ...baseGroups.keys()]);
    const currentFiles = fileMap(currentSnapshot);
    const baseFiles = fileMap(baseSnapshot);
    const rows = [];
    const diagramRecords = [];
    let inferredHidden = 0;

    for (const key of keys) {
      const current = currentGroups.get(key);
      const baseline = baseGroups.get(key);
      const record = current || baseline;
      const evidences = record.evidence;
      const confidences = [...new Set(evidences.map(edge => string(edge.confidence).toUpperCase() || 'UNKNOWN'))].sort();
      const inferred = confidences.every(confidence => confidence === 'INFERRED');
      if (inferred && !showInferred) {
        inferredHidden++;
        continue;
      }
      diagramRecords.push({...record, removed: !current});
      const status = baselineSha ? (current && !baseline ? 'added' : !current && baseline ? 'removed' : '') : '';
      const snapshot = current ? currentSnapshot : baseSnapshot;
      const files = current ? currentFiles : baseFiles;
      const source = files.get(record.source);
      const target = files.get(record.target);
      const row = make('li', 'cb-relation' + (status ? ' ' + status : ''));
      row.append(make('div', 'cb-relation-title', (source ? string(source.path) : record.source) + ' → ' + (target ? string(target.path) : record.target)));
      const meta = make('div', 'cb-relation-meta');
      meta.append(make('span', '', record.relation), make('span', '', confidences.join(' / ') + ' confidence'));
      if (status) meta.append(make('span', 'cb-badge ' + status, status === 'added' ? 'newly resolved' : 'no longer resolved'));
      row.append(meta);
      const evidence = make('div', 'cb-evidence');
      for (const item of evidences) {
        const line = Math.max(0, Math.floor(number(item.line)));
        evidence.append(sourceLink(string(item.path) + (line ? ':' + line : ''), snapshot && snapshot.sha, item.path, line));
      }
      row.append(evidence);
      rows.push(row);
    }

    rows.sort((left, right) => left.textContent.localeCompare(right.textContent));
    const controls = make('div', 'cb-edge-controls');
    const label = make('label');
    const checkbox = make('input');
    checkbox.type = 'checkbox';
    checkbox.id = 'show-inferred';
    checkbox.checked = showInferred;
    checkbox.addEventListener('change', renderInspector);
    label.append(checkbox, document.createTextNode('Show inferred relationships'));
    controls.append(label);
    renderNeighborhood(container, selected, diagramRecords);
    if (inferredHidden) controls.append(make('span', '', inferredHidden + ' inferred hidden'));
    container.append(controls);
    if (rows.length) {
      const list = make('ul', 'cb-relations');
      list.append(...rows);
      container.append(list);
    } else {
      container.append(make('p', 'cb-empty-inline', inferredHidden ? 'Only inferred relationships are available. Enable them above to inspect the evidence.' : 'No resolved relationships touch this selection at the chosen revision.'));
    }
  }

  function renderInspector() {
    const inferredInput = byId('show-inferred');
    const showInferred = inferredInput ? inferredInput.checked : false;
    const restoreInferredFocus = document.activeElement === inferredInput;
    const body = make('div');
    if (!selection) {
      body.append(make('p', 'cb-copy', 'Select a folder or file to inspect its structure and relationships.'));
      dom.inspector.replaceChildren(...body.childNodes);
      return;
    }
    const back = make('button', 'cb-return', 'Back to map');
    back.type = 'button';
    back.addEventListener('click', () => {
      const key = selection.type + ':' + selection.id;
      const target = [...dom.map.querySelectorAll('[data-focus-key]')].find(node => node.dataset.focusKey === key && !node.disabled);
      (target || dom.map).scrollIntoView({block: 'center', behavior: 'instant'});
      if (target) target.focus({preventScroll: true});
    });
    body.append(back);

    const currentSnapshot = selectedSnapshot();
    const baseSnapshot = baselineSnapshot();
    const currentFiles = fileMap(currentSnapshot);
    const baseFiles = fileMap(baseSnapshot);

    if (selection.type === 'group') {
      const stableGroups = new Map(slots.map(slot => [string(slot.id), string(slot.group) || '.']));
      const files = [...currentFiles.entries()].filter(([id]) => stableGroups.get(id) === selection.id).map(([, file]) => file);
      body.append(make('h3', 'cb-inspector-title', selection.id), make('p', 'cb-inspector-kicker', 'Stable folder area at ' + shortSha(currentSnapshot && currentSnapshot.sha)));
      const kv = make('dl', 'cb-kv');
      appendKv(kv, 'Files', formatNumber(files.length));
      appendKv(kv, 'Measured LOC', formatNumber(files.reduce((sum, file) => sum + number(file.lines), 0)));
      appendKv(kv, 'Callables', formatNumber(files.reduce((sum, file) => sum + (Array.isArray(file.symbols) ? file.symbols.length : 0), 0)));
      if (baselineSha) {
        const changed = slots.filter(slot => (string(slot.group) || '.') === selection.id && fileStatuses(currentFiles.get(string(slot.id)), baseFiles.get(string(slot.id))).length).length;
        appendKv(kv, 'Files changed', formatNumber(changed));
      }
      body.append(kv, make('h4', 'cb-section-title', 'Relationships touching this area'));
      dom.inspector.replaceChildren(...body.childNodes);
      renderRelations(dom.inspector, selection, showInferred);
      if (restoreInferredFocus) byId('show-inferred').focus({ preventScroll: true });
      return;
    }

    const id = selection.id;
    const currentFile = currentFiles.get(id);
    const baselineFile = baseFiles.get(id);
    const nearest = nearestFile(id);
    const absence = unmapped(currentSnapshot, id) ? 'outside extraction coverage at selected revision' : 'not present at selected revision';
    const display = currentFile
      ? { file: currentFile, snapshot: currentSnapshot, context: 'Selected revision' }
      : baselineFile
        ? { file: baselineFile, snapshot: baseSnapshot, context: 'Baseline revision · ' + absence }
        : nearest
          ? { ...nearest, context: 'Nearest available revision · ' + absence }
          : null;

    if (!display) {
      body.append(make('h3', 'cb-inspector-title', id), make('p', 'cb-copy', 'This stable file identity has no available file record in the loaded history.'));
      dom.inspector.replaceChildren(...body.childNodes);
      return;
    }

    const file = display.file;
    const statuses = fileStatuses(currentFile, baselineFile);
    body.append(make('h3', 'cb-inspector-title', string(file.path)), make('p', 'cb-inspector-kicker', display.context));
    if (statuses.length) {
      const badges = make('div', 'cb-badges');
      for (const status of statuses) badges.append(make('span', 'cb-badge ' + status, status === 'changed' ? 'changed blob' : status));
      body.append(badges);
    }
    const kv = make('dl', 'cb-kv');
    appendKv(kv, 'Stable ID', id);
    appendKv(kv, 'Folder area', string(file.group) || '.');
    appendKv(kv, 'Measured LOC', formatNumber(file.lines));
    appendKv(kv, 'Blob', shortSha(file.blob));
    body.append(kv);
    const source = make('p', 'cb-copy');
    source.append(sourceLink('Open source at ' + shortSha(display.snapshot && display.snapshot.sha), display.snapshot && display.snapshot.sha, file.path));
    body.append(source, make('h4', 'cb-section-title', 'Callable symbols'));
    const symbols = (Array.isArray(file.symbols) ? file.symbols : []).slice().sort((a, b) => number(a.line) - number(b.line));
    if (symbols.length) {
      const list = make('ul', 'cb-symbols');
      for (const symbol of symbols) {
        const line = Math.max(0, Math.floor(number(symbol.line)));
        const item = make('li');
        item.append(sourceLink(string(symbol.name) || '(unnamed symbol)', display.snapshot && display.snapshot.sha, file.path, line));
        item.append(make('div', 'cb-symbol-line', line ? 'line ' + line : 'line unavailable'));
        list.append(item);
      }
      body.append(list);
    } else {
      body.append(make('p', 'cb-empty-inline', 'No callable symbols were extracted for this file.'));
    }
    body.append(make('h4', 'cb-section-title', 'Relationships touching this file'));
    dom.inspector.replaceChildren(...body.childNodes);
    renderRelations(dom.inspector, selection, showInferred);
    if (restoreInferredFocus) byId('show-inferred').focus({ preventScroll: true });
  }

  function renderAll() {
    renderHeader();
    renderTimeline();
    renderWarnings();
    renderMap();
    renderInspector();
  }

  function selectIndex(index) {
    const bounded = Math.max(0, Math.min(snapshots.length - 1, index));
    const snapshot = snapshots[bounded];
    if (!snapshot) return;
    currentSha = string(snapshot.sha);
    followsLatest = bounded === snapshots.length - 1;
    renderTimeline();
    renderMap();
    renderInspector();
  }

  async function poll() {
    if (inFlight || disposed) return;
    inFlight = true;
    dom.retry.disabled = true;
    try {
      const envelope = await WorkspaceAPI.json('/api/codebase?' + new URLSearchParams({repository}));
      if (disposed) return;
      if (!envelope || !['building', 'ready', 'error'].includes(envelope.status)) throw new Error('The codebase API returned an invalid status envelope.');
      if (envelope.data) applyHistory(envelope.data, envelope.status, envelope.error);
      else if (envelope.status === 'building') {
        envelopeStatus = 'building';
        showState('loading', 'Building the codebase map', 'Waiting for commit-pinned structural history from the local Factory monitor.');
      } else if (history) {
        envelopeStatus = 'error';
        envelopeError = string(envelope.error);
        renderHeader();
      } else {
        envelopeStatus = 'error';
        showState('error', 'Codebase history is unavailable', string(envelope.error) || 'The local history monitor did not return a completed dataset.');
      }
    } catch (error) {
      envelopeStatus = 'error';
      envelopeError = error instanceof Error ? error.message : String(error);
      if (history) renderHeader();
      else showState('error', 'Could not load codebase history', envelopeError + ' The page will keep retrying automatically.');
    } finally {
      inFlight = false;
      dom.retry.disabled = false;
    }
  }

  dom.previous.addEventListener('click', () => selectIndex(number(dom.range.value) - 1));
  dom.next.addEventListener('click', () => selectIndex(number(dom.range.value) + 1));
  dom.latest.addEventListener('click', () => selectIndex(snapshots.length - 1));
  dom.range.addEventListener('input', () => selectIndex(number(dom.range.value)));
  dom.baseline.addEventListener('change', () => {
    baselineSha = dom.baseline.value;
    renderTimeline();
    renderMap();
    renderInspector();
  });
  dom.baseline.addEventListener('blur', () => { if (pendingBaselineSync) syncBaselineOptions(); });
  dom.retry.addEventListener('click', poll);
  const observer = new ResizeObserver(([entry]) => dom.main.style.setProperty('--timeline-height', entry.target.getBoundingClientRect().height + 'px'));
  observer.observe(host.querySelector('.cb-timeline'));
  const timer = window.setInterval(() => {
    if (document.hidden) return;
    if (history) renderHeader();
    poll();
  }, 30000);
  poll();
  return {dispose() { disposed = true; window.clearInterval(timer); observer.disconnect(); }};

  }
  window.WorkspaceCodebase = Object.freeze({mount});
})();
