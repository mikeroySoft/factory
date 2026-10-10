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
  function mountAtlas(host, repository) {
    const controller={host,repository,disposed:false};
    active=controller;
    const boundaries=[...NODES.map(([id,label])=>[id,label,...BOUNDARIES[id]]),...SUPPORT];
    host.innerHTML=`<div class="workspace-tool atlas-tool"><header class="atlas-head"><div><h2>Factory reference atlas</h2><p>A reference model of the Factory—not extracted architecture of <strong>${esc(repository)}</strong>, not live activity, and not proof of the installed version. Source availability is checked against the selected repository's published cache.</p></div><button class="btn" type="button" data-atlas-print>Print Atlas</button></header>
      <section class="atlas-diagram" tabindex="0" role="region" aria-label="Scrollable Factory pipeline reference"><svg viewBox="0 0 1080 530" role="img" aria-labelledby="atlas-title atlas-desc"><title id="atlas-title">Factory pipeline reference</title><desc id="atlas-desc">Issues pass through triage, dispatch, worktree, worker, gate, review and pull request. Gate retries and review revisions return to the worker. Outcome evidence is separate from merge. Linked boundaries and source citations are printed below.</desc><defs><marker id="atlas-arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="var(--muted)"/></marker></defs>
      ${EDGES.map(([path,x,y,label,back])=>`<path class="atlas-edge${back?' is-return':''}" d="${path}" marker-end="url(#atlas-arrow)"/><text class="atlas-edge-label" x="${x}" y="${y}">${esc(label)}</text>`).join('')}
      ${NODES.map(([id,label,sub,x,y])=>`<a class="atlas-node" href="#atlas-boundary-${id}" aria-label="${esc(`${label}: read boundary and source evidence`)}"><rect x="${x}" y="${y}" width="240" height="86" rx="6"/><text x="${x+120}" y="${y+36}" text-anchor="middle">${esc(label)}</text><text class="atlas-node-sub" x="${x+120}" y="${y+59}" text-anchor="middle">${esc(sub)}</text></a>`).join('')}</svg></section>
      <section class="section-block"><header><div><h2>Human decision → observed result</h2><p>Canceling a proposal has no target effect. Drift or a live claim can refuse application; a partial receipt is not success.</p></div></header><div class="atlas-diagram" tabindex="0" role="region" aria-label="Scrollable human control reference"><svg viewBox="0 0 1080 190" role="img" aria-labelledby="atlas-control-title atlas-control-desc"><title id="atlas-control-title">Guarded human control cycle</title><desc id="atlas-control-desc">Inspect evidence, prepare an exact proposal, confirm it, apply through guarded operations, then observe resulting provider state. Fresh observations inform the next decision. FM may advise but cannot execute.</desc><path class="atlas-edge is-return" d="M938 66V28H138V66" marker-end="url(#atlas-arrow)"/><text class="atlas-edge-label" x="538" y="17">fresh observation—not assumed success</text>
      ${[['Inspect','Bounded evidence'],['Prepare','Exact proposal'],['Confirm','Explicit authority'],['Apply','Durable receipt'],['Observe','Resulting state']].map(([label,sub],index)=>{const x=48+index*200;return `${index<4?`<path class="atlas-edge" d="M${x+180} 105H${x+200}" marker-end="url(#atlas-arrow)"/>`:''}<a class="atlas-node" href="#atlas-boundary-operator" aria-label="${label}: read human control boundary"><rect x="${x}" y="66" width="180" height="78" rx="6"/><text x="${x+90}" y="98" text-anchor="middle">${label}</text><text class="atlas-node-sub" x="${x+90}" y="120" text-anchor="middle">${sub}</text></a>`;}).join('')}</svg></div></section>
      <section class="atlas-sources"><h3>Source evidence for ${esc(repository)}</h3><p data-atlas-cache role="status">Resolving the published codebase cache…</p><button class="btn btn-small" type="button" data-tool-retry>Refresh cached evidence</button><p>Only paths present in that snapshot receive commit-pinned links. All boundaries and evidence appear below without expansion.</p></section>
      <section class="atlas-boundaries">${boundaries.map(([id,label,detail])=>`<article class="atlas-boundary" id="atlas-boundary-${id}"><h3>${esc(label)}</h3><p>${esc(detail)}</p><div class="atlas-links" data-atlas-sources="${id}"></div></article>`).join('')}</section></div>`;
    async function load() {
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
    host.addEventListener('click',event=>{if(event.target.closest('[data-tool-retry]'))load();if(event.target.closest('[data-atlas-print]'))window.print();});
    load();
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
