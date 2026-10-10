(() => {
  'use strict';

  const VIEWS = ['Overview','Outcomes','Attention','Flow','Success','Codebase','Atlas','System','Settings'];
  const THEMES = [['cyberpunk','Cyberpunk'],['gpuflo','GPUFlo'],['district','District'],['factory','Factory'],['rocm','ROCm'],['porcelain','Porcelain'],['sandstone','Sandstone'],['slate','Slate'],['forest','Forest']];
  const app = document.querySelector('#app');
  const overlay = document.querySelector('#overlay');
  const live = document.querySelector('#live-status');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const narrow = matchMedia('(max-width: 920px)');
  const preferences = WorkspaceSettings.preferences;
  const state = {
    data: {observed_at:null,repositories:[],collection:{}}, loading:true, error:'', request:null,
    ops:new Map(), inspections:new Map(),
    chats:new Map(), chatMeta:new Map(), chatOpen:new URLSearchParams(location.search).get('chat')==='1', chatCase:null, palette:false, paletteQuery:'',
    pollTimer:null, visitBaseline:new Map()
  };
  try { state.themePreference = localStorage.getItem('factory-theme'); } catch {}
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
  const array = value => Array.isArray(value) ? value : [];
  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const positive = value => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : null;
  const date = value => { const parsed = new Date(value || ''); return Number.isFinite(parsed.valueOf()) ? parsed.toLocaleString() : String(value || 'unknown'); };
  const safeUrl = value => { try { const url = new URL(String(value)); return ['http:','https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } };
  const normalize = value => String(value || '').trim().toLowerCase().replace(/[\s_]+/g,'-');
  const slugify = value => normalize(value);
  const tone = value => {
    const normalized = normalize(value);
    if (['complete','ready','fresh','merged','applied','attested','owner-attested','available'].includes(normalized)) return 'good';
    if (['partial','building','refreshing','held','waiting','escalated','needs-info','stale','interrupted'].includes(normalized)) return 'warn';
    if (['failed','error','unavailable','invalid'].includes(normalized)) return 'bad';
    return 'unknown';
  };
  const route = () => {
    const params = new URLSearchParams(location.search);
    const requested = slugify(params.get('view'));
    return {view: VIEWS.find(view => slugify(view) === requested) || 'Overview', repository: params.get('repository') || '', caseNumber: positive(params.get('case'))};
  };
  function setRoute(changes, replace = false) {
    const params = new URLSearchParams(location.search);
    for (const [key,value] of Object.entries(changes)) value == null || value === '' ? params.delete(key) : params.set(key,String(value));
    history[replace ? 'replaceState' : 'pushState']({},'',`${location.pathname}${params.size ? `?${params}` : ''}`);
    render();
    syncDrawer();
  }
  const repositories = () => array(state.data.repositories);
  const currentRepo = () => repositories().find(repo => repo.slug === route().repository) || repositories()[0] || null;
  const observation = repo => object(repo?.observation);
  const roadmap = repo => object(repo?.roadmap);
  const runtime = repo => object(repo?.runtime);
  const flow = repo => object(repo?.flow);
  const cases = repo => array(observation(repo).cases);
  const plans = repo => array(object(roadmap(repo).investigation).plans).length ? array(object(roadmap(repo).investigation).plans) : array(roadmap(repo).plans);
  const coverage = value => object(value?.coverage);
  const repoObservedAt = repo => repo?.observed_at || observation(repo).observed_at || null;
  const isTerminal = item => ['closed','merged','done'].includes(normalize(item?.stage)) || normalize(item?.state) === 'closed' || Boolean(item?.pr?.merged_at);
  const hasCases = repo => Array.isArray(repo?.observation?.cases);
  const hasPlans = repo => Array.isArray(repo?.roadmap?.investigation?.plans) || Array.isArray(repo?.roadmap?.plans);
  const signal = (label,value=label) => `<span class="signal" data-tone="${tone(value)}">${esc(label)}</span>`;
  function announce(message) { live.textContent = message; live.classList.remove('sr-only'); setTimeout(() => live.classList.add('sr-only'),3500); }

  function selectedTheme(repo) {
    const chosen=state.themePreference;
    if(THEMES.some(([value])=>value===chosen))return chosen;
    return repo?.repository_theme?'repository':'factory';
  }
  function applyTheme(repo) {
    const theme=selectedTheme(repo),key=`${repo?.slug}:${theme}`;
    if(theme==='repository')delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme=theme;
    if(state.themeSelection===key)return;
    state.themeSelection=key;
    document.querySelector('#repository-theme')?.remove();
    if(theme!=='repository')return;
    WorkspaceAPI.text(`/api/theme?${new URLSearchParams({repository:repo.slug})}`).then(css=>{
      if(state.themeSelection!==key)return;
      const style=document.createElement('style');style.id='repository-theme';style.textContent=css;document.head.append(style);
    }).catch(error=>{if(state.themeSelection===key)announce(`Repository theme unavailable: ${error.message||error}`);});
  }
  function themePicker() {
    const repo=currentRepo(),theme=selectedTheme(repo),themes=repo?.repository_theme?[['repository','Repository'],...THEMES]:THEMES;
    return `<label class="theme-picker"><span>Theme</span><select data-theme>${themes.map(([value,label]) => `<option value="${value}"${value === theme ? ' selected' : ''}>${label}</option>`).join('')}</select></label>`;
  }
  function repoPicker(repo) {
    return `<label class="repo-picker"><span class="sr-only">Repository</span><select data-repository>${repositories().map(row => `<option value="${esc(row.slug)}"${row.slug === repo?.slug ? ' selected' : ''}>${esc(row.slug || 'unregistered')}</option>`).join('')}</select></label>`;
  }
  function nav(view) {
    return `<nav class="workspace-nav" aria-label="Workspace views">${VIEWS.map(item => `<button type="button" data-view="${slugify(item)}"${item === view ? ' aria-current="page"' : ''}>${item}</button>`).join('')}</nav>`;
  }
  function collectionMarkup(repo) {
    const collection = object(repo?.collection);
    const status = collection.refreshing ? 'refreshing' : collection.error || state.error ? 'error' : repoObservedAt(repo) ? 'ready' : 'building';
    return `<div class="collection"><span class="signal" data-status="${status}" data-tone="${tone(status)}">${esc(status)}</span><span>${repoObservedAt(repo) ? `latest source ${esc(date(repoObservedAt(repo)))}` : 'no observation yet'}</span><button class="btn btn-small" type="button" data-refresh ${collection.refreshing || state.request ? 'disabled' : ''}>Refresh selected</button></div>`;
  }

  function evidenceNotices(repo) {
    if (!repo) return '';
    const errors = [...array(repo.errors), repo.collection?.error, state.error && {message:state.error}].filter(Boolean);
    const unique = [...new Map(errors.map(error => [JSON.stringify(error),error])).values()];
    const sources = [['Cases',repo.observation],['Runtime',repo.runtime],['Outcomes',repo.roadmap]];
    return `<details class="coverage-notice"><summary>${unique.length ? `Evidence incomplete · ${esc(unique[0].message || unique[0])}` : 'Evidence freshness and source coverage'}</summary><dl class="kv">${sources.map(([label,value])=>`<dt>${label}</dt><dd>${value ? `${esc(date(value.observed_at || value.generated_at))} · ${esc(coverage(value).status || 'bounded')}` : 'unavailable'}</dd>`).join('')}</dl>${unique.length ? `<ul>${unique.map(error=>`<li>${esc(error.operation || 'collection')} · ${esc(error.code || 'unavailable')}: ${esc(error.message || error)}</li>`).join('')}</ul>` : ''}<p>Retained timestamps belong to each source. A newer runtime read does not make older issue or outcome evidence fresh.</p></details>`;
  }

  function journeyMarkup() {
    const journeys = [['Return after absence','overview'],['Investigate delay','flow'],['Resolve attention','attention'],['Handle abnormality','system'],['Verify outcomes','success']];
    return `<nav class="journeys" aria-label="Operator journeys">${journeys.map(([label,view],index)=>`<button type="button" data-view="${view}"><span>0${index+1}</span><strong>${label}</strong></button>`).join('')}</nav>`;
  }

  function baselineIdentity(repo) {
    const rows = cases(repo).map(item => [String(item.number),normalize(item.stage),String(item.updated_at || '')]).sort((a,b) => a[0].localeCompare(b[0]));
    return {repository:repo.slug,observed_at:repoObservedAt(repo),cases:rows};
  }
  function loadVisitBaseline(repo) {
    if (state.visitBaseline.has(repo.slug)) return state.visitBaseline.get(repo.slug);
    let value = null;
    try { value = JSON.parse(localStorage.getItem(`factory-workspace-baseline:${repo.slug}`) || 'null'); } catch {}
    state.visitBaseline.set(repo.slug,value);
    return value;
  }
  function saveBaselines() {
    for (const repo of repositories()) {
      try { localStorage.setItem(`factory-workspace-baseline:${repo.slug}`,JSON.stringify(baselineIdentity(repo))); } catch {}
    }
  }
  function deltaMarkup(repo) {
    if (!hasCases(repo)) return '<div class="notice">Current case observation is unavailable. No change count is inferred from missing evidence.</div>';
    const before = loadVisitBaseline(repo);
    const after = baselineIdentity(repo);
    if (!before?.observed_at) return `<div class="notice"><strong>No prior browser baseline.</strong> Current bounded observation ${esc(after.observed_at ? date(after.observed_at) : 'is unavailable')}. Changes since an earlier visit cannot be reconstructed.</div>`;
    if (before.repository !== repo.slug) return `<div class="notice"><strong>Baseline scope mismatch.</strong> No comparison is made across repositories.</div>`;
    const oldRows = new Map(array(before.cases).map(row => [String(row[0]),row]));
    const nextRows = new Map(after.cases.map(row => [String(row[0]),row]));
    let added=0,changed=0,absent=0;
    for (const [key,row] of nextRows) !oldRows.has(key) ? added++ : JSON.stringify(oldRows.get(key)) !== JSON.stringify(row) && changed++;
    for (const key of oldRows.keys()) if (!nextRows.has(key)) absent++;
    return `<section class="return-summary"><div><span>Baseline</span><strong>${esc(date(before.observed_at))}</strong></div><div><span>Current</span><strong>${esc(after.observed_at ? date(after.observed_at) : 'unavailable')}</strong></div><div><span>Newly observed</span><strong>${added}</strong></div><div><span>Changed</span><strong>${changed}</strong></div><div><span>No longer in bounded set</span><strong>${absent}</strong></div><p>This compares two browser-retained bounded identities only. “No longer present” is not closed, deleted, or lifetime history.</p></section>`;
  }

  function sourceAge(item) {
    const days=Math.floor((Date.now()-Date.parse(item?.updated_at||''))/86400000);
    return !isTerminal(item)&&Number.isFinite(days)&&days>=preferences.attentionDays?days:null;
  }
  function ageMarker(item) {
    const days=sourceAge(item);
    return days===null?'':`<span class="lingering">Last observed source update · ${days}d ago</span>`;
  }
  function caseButton(item) {
    const wait = array(flow(currentRepo()).waits?.current).find(row => Number(row.number) === Number(item.number));
    return `<button class="case-row" type="button" data-case="${esc(item.number)}"${sourceAge(item)!==null?' data-lingering':''}><span><b>#${esc(item.number)}</b><strong>${esc(item.title || 'Untitled case')}</strong></span><small>${signal(item.stage || 'unknown',item.stage)} ${wait ? `recorded wait · ${esc(wait.reason || 'reason unavailable')}` : 'wait not observed'} · updated ${esc(date(item.updated_at))}</small>${ageMarker(item)}</button>`;
  }
  function overview(repo) {
    const open = cases(repo).filter(item => !isTerminal(item));
    const active = array(flow(repo).cases).filter(item => item.active_execution);
    const waits = array(flow(repo).waits?.current);
    const delivered = plans(repo).filter(plan => object(plan.delivery).status === 'owner_attested');
    return `<header class="workspace-head"><div><p class="eyebrow">Return after absence</p><h1>What changed, what is live, what is still unknown.</h1><p>Current observations, browser-bounded deltas, live work, waits, and historical milestones remain separate.</p></div>${signal(coverage(observation(repo)).status || 'unavailable')}</header>${deltaMarkup(repo)}<section class="metric-grid"><article><span>Selected nonterminal</span><strong>${hasCases(repo) ? `${open.length} observed` : 'unknown'}</strong><small>bounded case set</small></article><article><span>Active executions</span><strong>${flow(repo).schema_version ? `${active.length} observed` : 'unknown'}</strong><small>recorded runtime evidence</small></article><article><span>Current waits</span><strong>${flow(repo).schema_version ? `${waits.length} observed` : 'unknown'}</strong><small>paired wait starts</small></article><article><span>Owner-accepted</span><strong>${hasPlans(repo) ? `${delivered.length} observed` : 'unknown'}</strong><small>current canonical attestations</small></article></section><section class="section-block"><header><h2>Current work</h2><button class="btn btn-small" data-view="flow" type="button">Investigate flow</button></header><div class="case-list">${open.slice(0,12).map(caseButton).join('') || '<p class="empty">No nonterminal cases appear in this bounded observation. This is not a repository-wide zero.</p>'}</div></section>${success(repo,true)}`;
  }

  function attestedCell(kind, delivery) {
    const value = object(object(delivery.attributed)[kind]);
    const evidence = object(value.evidence);
    const url = safeUrl(evidence.evidence_url || evidence.url);
    return `<article class="delivery-cell" data-tone="${tone(value.status)}"><span>${esc(kind)}</span><strong>${esc(value.status || 'unknown')}</strong><p>${esc(value.reason || evidence.summary || 'No current owner attestation.')}</p>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Evidence ↗</a>` : ''}</article>`;
  }
  function planCard(plan) {
    const delivery = object(plan.delivery);
    const children = array(plan.children);
    const title = plan.title || `Initiative #${plan.number}`;
    return `<article class="outcome-card"><header><span class="badge">#${esc(plan.number)}</span>${signal(delivery.status || plan.status || 'unknown')}</header><h2>${safeUrl(plan.url) ? `<a href="${esc(safeUrl(plan.url))}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>` : esc(title)}</h2><p>${esc(object(plan.sections).Outcome || 'Declared outcome unavailable.')}</p><div class="outcome-facts"><span>owner ${esc(delivery.owner || plan.owner || 'unknown')}</span><span>revision <code>${esc(String(delivery.canonical_revision || 'unknown').slice(0,12))}</code></span><span>${children.filter(item => normalize(item.state) === 'closed').length} linked closed</span></div><div class="delivery-grid">${['released','installed','healthy','accepted'].map(kind => attestedCell(kind,delivery)).join('')}</div><p class="boundary">${esc(delivery.detail || 'Merge and closure do not establish release, installation, health, or accepted usefulness.')}</p><div class="cluster"><button class="btn" type="button" data-direct-outcome="${esc(plan.number)}" data-title="${esc(title)}">Comment direction</button><button class="btn btn-accent" type="button" data-attest-outcome="${esc(plan.number)}" data-title="${esc(title)}">Record owner attestation</button></div>${coverage(delivery).status ? `<small>comment coverage: ${esc(coverage(delivery).status)} · ${esc(coverage(delivery).reason || `${coverage(delivery).comments ?? 0} comments observed`)}</small>` : ''}</article>`;
  }
  function outcomes(repo) {
    const rows = plans(repo);
    return `<header class="workspace-head"><div><p class="eyebrow">Outcome control</p><h1>Delivery evidence keeps its attribution.</h1><p>Implementation, merge, owner attestations, and verified deployment or health are separate states. Attestation kinds never imply one another.</p></div>${signal(coverage(roadmap(repo)).status || 'unavailable')}</header><div class="outcome-grid">${rows.map(planCard).join('') || '<p class="empty">No canonical initiatives were returned by the bounded roadmap read.</p>'}</div>`;
  }

  function opsRecord(repo) { return state.ops.get(repo.slug) || {status:'idle',data:null,error:''}; }
  function isSyncIssue(ticket,snapshot) {
    const blocker = object(object(snapshot?.upstream).blocker);
    return Number(blocker.number) === Number(ticket.number) || array(ticket.labels).some(label => /upstream[-_ ]sync|sync[-_ ]issue/i.test(String(label)));
  }
  function attentionKind(ticket,snapshot) {
    if (isTerminal(ticket)) return ticket.worktree ? 'stale' : '';
    if (isSyncIssue(ticket,snapshot)) return 'sync';
    const stage = normalize(ticket.stage);
    if (stage === 'escalated') return 'escalated';
    if (stage === 'needs-info') return 'needs-info';
    if (stage === 'triage') return array(ticket.events).some(event => /^Triage proposal: wontfix/.test(event.body || '')) ? 'wontfix' : 'triage';
    if (stage === 'queued' && array(ticket.assignees).length && !ticket.lock_held) return 'stuck';
    if (stage === 'pr-open' && ticket.pr) return 'pr';
    return '';
  }
  function actionOptions(ticket,kind,snapshot) {
    if (!ticket || ticket.lock_held || kind === 'sync') return [];
    const issue = extra => ({op:'issue',number:ticket.number,...extra});
    const pr = extra => ({op:'pr',number:ticket.pr.number,...extra});
    const approved = snapshot?.config?.approved_label || 'factory-approved';
    const result=[];
    const add=(title,effect,next,requests,danger=false)=>result.push({title,effect,next,requests,danger});
    if (['escalated'].includes(kind)) {
      add('Re-queue agent','Add ready-for-agent, remove ready-for-human, and remove only the authenticated actor assignment. Other assignees remain.','Dispatcher may claim once no assignees remain.',[issue({add:['ready-for-agent'],remove:['ready-for-human'],unassign:true})]);
      add('I will take it','Assign the authenticated actor; keep labels and other assignees.','Authenticated maintainer.',[issue({assign:true})]);
      if (ticket.pr && !ticket.pr.approved) add('Re-approve PR for merge',`Add ${approved} to PR #${ticket.pr.number}; remove ready-for-human from issue. This does not rerun gate or review.`,'Merge stage only after CI and current-main checks.',[pr({add:[approved]}),issue({remove:['ready-for-human']})]);
      add('Close · not planned','Close the issue as not planned. Associated PR and local work remain.','Maintainer owns remaining PR/worktree.',[issue({close:'not planned'})],true);
    } else if (kind === 'needs-info') {
      add('Answer → re-triage','Replace needs-info with needs-triage after recording guidance.','Configured triage service on a later pass.',[issue({add:['needs-triage'],remove:['needs-info']})]);
      add('Answer → queue agent','Replace needs-info with ready-for-agent.','Dispatcher when claimable.',[issue({add:['ready-for-agent'],remove:['needs-info']})]);
      add('Answer → human','Replace needs-info with ready-for-human; assign nobody.','Human maintainer.',[issue({add:['ready-for-human'],remove:['needs-info']})]);
      add('Close · not planned','Close the issue as not planned. An associated PR, approval, and local work remain unchanged.','No issue worker; a maintainer owns any remaining PR or worktree.',[issue({close:'not planned'})],true);
    } else if (kind === 'wontfix') {
      add('Accept · close wontfix','Add wontfix, remove needs-triage, close not planned.','No issue worker.',[issue({add:['wontfix'],remove:['needs-triage'],close:'not planned'})],true);
      add('Reject → queue agent','Replace needs-triage with ready-for-agent.','Dispatcher when claimable.',[issue({add:['ready-for-agent'],remove:['needs-triage']})]);
      add('Reject → human','Replace needs-triage with ready-for-human.','Human maintainer.',[issue({add:['ready-for-human'],remove:['needs-triage']})]);
    } else if (kind === 'triage') {
      add('Run LLM triage','Invoke existing triage for this issue after recording the decision.','Configured triage service.',[{op:'triage',number:ticket.number}]);
      add('Queue agent','Replace needs-triage with ready-for-agent.','Dispatcher when claimable.',[issue({add:['ready-for-agent'],remove:['needs-triage']})]);
      add('Needs human','Replace needs-triage with ready-for-human.','Human maintainer.',[issue({add:['ready-for-human'],remove:['needs-triage']})]);
      add('Needs info','Replace needs-triage with needs-info.','Issue reporter.',[issue({add:['needs-info'],remove:['needs-triage']})]);
      add('Close · not planned','Close the issue as not planned. An associated PR, approval, and local work remain unchanged.','No issue worker; a maintainer owns any remaining PR or worktree.',[issue({close:'not planned'})],true);
    } else if (kind === 'stuck') {
      add('Release my claim','Remove only the authenticated actor assignment.','Dispatcher after no assignees remain.',[issue({unassign:true})]);
      add('Send to human','Replace ready-for-agent with ready-for-human; preserve assignees.','Current assignee.',[issue({add:['ready-for-human'],remove:['ready-for-agent']})]);
    } else if (kind === 'pr') {
      ticket.pr.approved ? add('Hold merge',`Remove ${approved} from PR #${ticket.pr.number}.`,'Maintainer must re-approve before merge.',[pr({remove:[approved]})]) : add('Approve for merge',`Add ${approved}. This does not run gate/review or bypass CI.`,'Merge stage after all existing gates.',[pr({add:[approved]})]);
      add('Send back to agent','Add ready-for-agent and remove only the authenticated actor issue assignment; existing PR approval remains.','Dispatcher when claimable.',[issue({add:['ready-for-agent'],unassign:true})]);
    } else if (kind === 'stale') add('Remove worktree + branch',`Delete ${ticket.worktree.path} and the issue branch. GitHub remains unchanged.`,'No worker. Remote history and retained artifacts remain.',[{op:'cleanup',number:ticket.number}],true);
    return result;
  }
  function attention(repo) {
    const ops = opsRecord(repo);
    const snapshot = ops.data;
    const questions = array(object(roadmap(repo).investigation).attention).length ? array(object(roadmap(repo).investigation).attention) : array(roadmap(repo).attention);
    let opsMarkup = '';
    if ((ops.status === 'loading'&&!snapshot) || snapshot?.status === 'building') opsMarkup = '<p class="empty">Full Ops snapshot is building. Action choices remain unavailable until retained state is ready.</p>';
    else if (ops.error&&!snapshot) opsMarkup = `<p class="error">${esc(ops.error)}</p>`;
    else if (snapshot) {
      const tickets = array(snapshot.tickets).map(ticket => ({ticket,kind:attentionKind(ticket,snapshot)})).filter(row => row.kind);
      opsMarkup = tickets.map(({ticket,kind}) => {
        const options = actionOptions(ticket,kind,snapshot);
        const sync = kind === 'sync';
        return `<article class="attention-card"${sourceAge(ticket)!==null?' data-lingering':''}><header><span class="badge">${esc(kind)}</span><strong>#${esc(ticket.number)} · ${esc(ticket.title)}</strong></header>${ageMarker(ticket)}<p>${sync ? 'Upstream synchronization requires maintainer resolution, validation, explicit push, and blocker closure. It cannot enter ordinary agent requeue paths.' : ticket.lock_held ? 'A live pipeline holds this case; mutation is blocked until it releases.' : 'Inspect evidence, ask FM, or prepare exactly one supported action.'}</p><div class="cluster"><button class="btn btn-small" type="button" data-case="${esc(ticket.number)}">Inspect</button><button class="btn btn-small" type="button" data-chat-case="${esc(ticket.number)}">Ask FM</button></div>${options.length ? `<div class="action-menu"><label>Supported action <select data-action-choice="${esc(ticket.number)}" data-action-options="${esc(JSON.stringify(options))}"><option value="">Choose…</option>${options.map((option,index) => `<option value="${index}">${esc(option.title)}</option>`).join('')}</select></label><button class="btn btn-accent" type="button" data-prepare-action="${esc(ticket.number)}">Review action</button></div>` : ''}</article>`;
      }).join('') || '<p class="empty">No actionable case appears in the bounded Ops snapshot.</p>';
      if(ops.error)opsMarkup=`<p class="error">Refresh failed; retained Ops evidence is shown. ${esc(ops.error)}</p>${opsMarkup}`;
    }
    return `<header class="workspace-head"><div><p class="eyebrow">Resolve attention</p><h1>Inspect, advise, preview, confirm, observe.</h1><p>Routing is not execution. A receipt is not resulting target state. Sync blockers remain maintainer operations, never ordinary requeue.</p></div></header><section class="attention-grid">${questions.map(item => `<article class="attention-card"><header><span class="badge">${esc(item.kind || 'question')}</span><strong>${esc(item.question || 'Question unavailable')}</strong></header><p>${esc(item.readiness_reason || 'Readiness unknown.')}</p>${safeUrl(item.url) ? `<a href="${esc(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">Canonical source ↗</a>` : ''}${positive(item.ticket) ? `<div class="cluster"><button class="btn" data-case="${item.ticket}">Inspect #${item.ticket}</button><button class="btn" data-chat-case="${item.ticket}">Ask FM</button></div>` : ''}</article>`).join('')}${opsMarkup}</section><section class="section-block"><header><h2>Retained action receipts</h2><p>Proposal identities retained by this browser; receipt authority is server-side.</p></header><div data-decision-history></div></section>`;
  }

  function countLabel(value) { const item=object(value); return item.total == null ? `${item.observed ?? 0} observed · total unknown` : `${item.observed ?? 0} / ${item.total}`; }
  function flowView(repo) {
    const value=flow(repo), rows=array(value.cases).filter(row => !isTerminal(row));
    if (!value.schema_version) return `<header class="workspace-head"><div><p class="eyebrow">Live flow</p><h1>Work, waits, and uncertainty.</h1></div></header><p class="empty">Flow projection is unavailable. No WIP total is inferred from selected ticket stages.</p>`;
    const currentWaits=array(value.waits?.current);
    const stages=[['triage','Triage'],['queued','Ready'],['in-flight','Running'],['pr-open','Review / merge']];
    const sourceByNumber=new Map(cases(repo).map(item=>[Number(item.number),item]));
    const card=row=>`<button class="flow-card" type="button" data-case="${row.number}" data-flow-case="${esc(`${repo.slug}:${row.number}`)}" data-stage="${esc(row.stage||'unknown')}"${sourceAge(sourceByNumber.get(row.number))!==null?' data-lingering':''}><span class="flow-card-id">#${row.number}${row.active_execution?'<span class="execution-cue"><i aria-hidden="true"></i>Observed active</span>':''}</span><b>${esc(row.title||'Case title unavailable')}</b><small>${esc(row.stage||'stage unknown')} · attempts ${esc(array(row.attempts?.observed).join(', ')||'not observed')} · ${row.known_started_wip?'known start':'start unknown'}${row.current_wait_ids?.length?' · recorded wait':''}</small>${ageMarker(sourceByNumber.get(row.number))}</button>`;
    const lanes=rows.filter(row=>!stages.some(([stage])=>stage===normalize(row.stage)));
    return `<header class="workspace-head"><div><p class="eyebrow">Bounded pure projection</p><h1>Known WIP, observed waits, partial coverage.</h1><p>Closed and merged cases never appear here. Missing admission history is not converted into an exact WIP total.</p></div>${signal(coverage(value).status || 'unavailable')}</header>
      <section class="metric-grid"><article><span>Selected nonterminal</span><strong>${esc(countLabel(value.counts?.selected_nonterminal_cases))}</strong></article><article><span>Known started WIP</span><strong>${esc(countLabel(value.counts?.known_started_wip))}</strong></article><article><span>Active execution cases</span><strong>${esc(countLabel(value.counts?.active_execution_cases))}</strong></article><article><span>Current waits</span><strong>${currentWaits.length} observed</strong></article></section>
      <section class="section-block"><header><div><h2>Live pipeline</h2><p>Current stage observations, not a replay of execution history. Counts are bounded; no WIP limit is inferred.</p></div></header><div class="flow-board">${stages.map(([stage,label])=>{const members=rows.filter(row=>normalize(row.stage)===stage);return `<section><h3>${label}<span>${members.length} observed</span></h3><div class="flow-stage-cases" data-flow-scroll="${stage}">${members.map(card).join('')||'<p class="empty">None in this selection</p>'}</div></section>`;}).join('')}</div></section>
      ${lanes.length?`<section class="section-block"><header><div><h2>Information, human holds, and unplaced work</h2><p>These are not downstream completion stages. Unknown placement stays explicit.</p></div></header><div class="flow-holds">${lanes.map(card).join('')}</div></section>`:''}
      <section class="split"><article class="section-block"><h2>Recorded waits</h2>${currentWaits.map(wait=>{const number=positive(wait.number),tag=number?'button':'div';return `<${tag} class="case-row"${number?` data-case="${number}" type="button"`:''}><span><b>${number?`#${number}`:'System wait'}</b><strong>${esc(wait.reason || 'reason unavailable')}</strong></span><small>${esc(wait.mode || 'mode unknown')} · resource ${esc(object(wait.resource).id || 'unknown')} · observed ${esc(wait.observed_for_seconds ?? 'unknown')}s</small></${tag}>`;}).join('')||'<p class="empty">No current paired waits in the retained runtime window.</p>'}</article><article class="section-block"><h2>Constraint candidates</h2>${array(value.constraint_candidates).map(candidate=>`<div class="constraint"><strong>${esc(candidate.reason || object(candidate.resource).id || 'candidate')}</strong><p>${candidate.observed_waits} observed waits across ${array(candidate.case_numbers).length} cases · ${candidate.current_waits} current · ${candidate.completed_waits} completed.</p><small>Resource: ${esc(object(candidate.resource).id || 'unknown')}. Candidate only; frequency is not proof of a bottleneck.</small></div>`).join('')||'<p class="empty">No repeated constraint candidate in this bounded projection.</p>'}</article></section>
      <details class="coverage-notice"><summary>Flow coverage and denominator</summary><pre>${esc(JSON.stringify({time:value.time,coverage:value.coverage,denominator:value.denominator},null,2))}</pre></details>`;
  }

  function success(repo,compact=false) {
    const merged=cases(repo).filter(item=>normalize(item.stage)==='merged'||item.pr?.merged_at);
    const deliveries=plans(repo).map(plan=>({plan,delivery:object(plan.delivery)}));
    const accepted=deliveries.filter(row=>row.delivery.status==='owner_attested');
    const verifiedDeployment=deliveries.filter(row=>object(object(row.delivery.verified).deployment).status==='verified');
    const body=`<div class="success-path"><article data-tone="${merged.length?'good':'unknown'}"><span>1</span><div><small>Engineering merge</small><strong>${hasCases(repo)?`${merged.length} observed`:'unknown'}</strong><p>Bounded merge-stage or PR timestamp evidence.</p></div></article><article data-tone="${accepted.length?'good':'unknown'}"><span>2</span><div><small>Attributed accepted usefulness</small><strong>${accepted.length||'unknown'}</strong><p>Current owner attestation bound to canonical revision.</p></div></article><article data-tone="${verifiedDeployment.length?'good':'unknown'}"><span>3</span><div><small>Verified deployment / health</small><strong>${verifiedDeployment.length||'unknown'}</strong><p>Owner comments do not substitute for machine verification.</p></div></article></div>`;
    if(compact)return `<section class="section-block"><header><div><h2>Success means verified value</h2><p>Merge, attributed acceptance, and verified delivery remain different states.</p></div><button class="btn btn-small" type="button" data-view="success">Open Success</button></header>${body}</section>`;
    return `<header class="workspace-head"><div><p class="eyebrow">Success evidence</p><h1>Merge is not delivery.</h1><p>Engineering milestones, owner-attributed acceptance, and independently verified deployment or health never collapse into one score.</p></div></header>${body}<section class="split"><article class="section-block"><h2>Observed merge milestones</h2>${merged.slice(0,12).map(caseButton).join('')||'<p class="empty">No merge evidence in this bounded case set; not a repository-wide zero.</p>'}</article><article class="section-block"><h2>Current owner acceptance</h2>${accepted.map(({plan,delivery})=>`<article class="source-card"><strong>#${plan.number} · ${esc(plan.title)}</strong><p>${esc(delivery.detail)}</p>${attestedCell('accepted',delivery)}</article>`).join('')||'<p class="empty">No current complete-coverage owner accepted attestation. Accepted usefulness remains unknown.</p>'}</article></section>`;
  }

  function system(repo) {
    const ops=opsRecord(repo),snapshot=ops.data,collection=object(repo.collection);
    if((ops.status==='loading'&&!snapshot)||snapshot?.status==='building')return `<header class="workspace-head"><div><p class="eyebrow">System</p><h1>Ops snapshot is building.</h1><p>The cold-start response is loading evidence, not empty or healthy state.</p></div></header><pre class="raw">${esc(JSON.stringify(snapshot?.collection||collection,null,2))}</pre>`;
    if(ops.error&&!snapshot)return `<header class="workspace-head"><div><h1>System evidence unavailable.</h1></div></header><p class="error">${esc(ops.error)}</p>`;
    if(!snapshot)return '<p class="empty">Full Ops snapshot has not been requested.</p>';
    const dispatcher=object(snapshot.dispatcher),config=object(snapshot.config),metrics=object(snapshot.metrics),audit=object(snapshot.coverage||snapshot.audit_coverage),spend=object(snapshot.spend);
    const money=value=>typeof value==='number'&&Number.isFinite(value)?`$${value.toFixed(2)}`:'unknown';
    const spent=array(snapshot.tickets).filter(ticket=>ticket.spend?.rounds||ticket.spend?.seconds||ticket.spend?.cost!=null);
    return `<header class="workspace-head"><div><p class="eyebrow">Ops diagnostics</p><h1>Runtime, guards, bounds, and source coverage.</h1><p>Inactive services are not automatically unhealthy. Bounded audit history is not lifetime throughput.</p></div>${signal(ops.error?'refresh failed':['loading','building'].includes(ops.status)?'refreshing':array(snapshot.errors).length?'errors observed':'snapshot ready',ops.error||array(snapshot.errors).length?'error':['loading','building'].includes(ops.status)?'refreshing':'ready')}</header>${ops.error?`<p class="error">Retaining the previous snapshot. Refresh failed: ${esc(ops.error)}</p>`:''}
      <div class="system-grid"><section><h2>Repository and machinery</h2><dl class="kv"><dt>Repository</dt><dd>${esc(snapshot.repo||repo.slug)}</dd><dt>Generated</dt><dd>${esc(date(snapshot.generated_at))}</dd><dt>Main branch</dt><dd>${esc(config.main||'unknown')}</dd><dt>Max active</dt><dd>${esc(config.max_active??'unknown')}</dd><dt>Attempt limit</dt><dd>${esc(config.max_attempts??'unknown')}</dd><dt>Review rounds</dt><dd>${esc(config.review_rounds??'unknown')}</dd><dt>Gate checks</dt><dd>${esc(array(config.gate_checks).join(', ')||'unknown')}</dd><dt>Host GPU lock</dt><dd>${snapshot.gpu_lock_held===true?'held':snapshot.gpu_lock_held===false?'not held at observation':'unknown'}</dd></dl></section>
      <section><h2>Dispatcher</h2><dl class="kv"><dt>Service active</dt><dd>${esc(dispatcher.service_active??'unknown')}</dd><dt>Timer active</dt><dd>${esc(dispatcher.timer?.active??'unknown')}</dd><dt>Next pass</dt><dd>${esc(date(dispatcher.timer?.next))}</dd><dt>Recent runs</dt><dd>${array(dispatcher.runs).length}</dd></dl><p>Inactivity alone does not establish failure or authorize restart.</p><pre class="raw">${esc(JSON.stringify({capacity:dispatcher.capacity,schedule:dispatcher.schedule,errors:dispatcher.errors},null,2))}</pre></section>
      <section><h2>Retained spend</h2><dl class="kv"><dt>Reported cost</dt><dd>${money(spend.cost)} USD</dd><dt>Recorded work</dt><dd>${typeof spend.seconds==='number'?`${(spend.seconds/3600).toFixed(1)} hours`:'unknown'}</dd><dt>Cases with spend</dt><dd>${esc(spend.tickets??'unknown')}</dd></dl><p>Bounded retained records, not lifetime spend. Missing cost remains unknown.</p>${spent.map(ticket=>`<button class="case-row" type="button" data-case="${ticket.number}"><span><b>#${ticket.number}</b><strong>${money(ticket.spend.cost)} · ${ticket.spend.rounds} rounds</strong></span><small>${esc(ticket.title)}</small></button>`).join('')}</section>
      <section><h2>Worker outcomes</h2><div class="ops-table-wrap"><table class="ops-table"><thead><tr><th>Worker</th><th>First-gate pass</th><th>Attempts</th><th>Reported cost</th></tr></thead><tbody>${array(snapshot.workers).map(worker=>`<tr><th>${esc(worker.worker)}</th><td>${typeof worker.first_pass==='number'?`${(worker.first_pass*100).toFixed(1)}%`:'unknown'}</td><td>${esc(worker.attempts)}</td><td>${money(worker.cost)}</td></tr>`).join('')}</tbody></table></div><p>Claim-attributed observations in the retained audit window. A worker score is not system throughput or accepted value.</p></section>
      <section class="wide"><h2>Recent dispatcher journal</h2><div class="run-list">${array(dispatcher.runs).slice(-20).reverse().map(run=>`<details><summary>${esc(run.result||'unknown')} · ${esc(date(run.started))}</summary><pre>${esc(array(run.lines).join('\n')||'No bounded lines returned.')}</pre></details>`).join('')||'<p class="empty">No retained runs in the bounded snapshot.</p>'}</div></section>
      <section><h2>Bounded metrics</h2><pre class="raw">${esc(JSON.stringify(metrics,null,2))}</pre><p>Counts apply only to the returned cohort and coverage.</p></section><section><h2>Audit coverage</h2><pre class="raw">${esc(JSON.stringify(audit,null,2))}</pre></section>
      <section class="wide"><h2>Upstream synchronization</h2><pre class="raw">${esc(JSON.stringify(snapshot.upstream||{status:'not configured or unavailable'},null,2))}</pre><p>Resolve conflicts on the host, validate, explicitly push, then close the blocker. Sync issues are never requeued as ordinary agent work.</p></section>
      <section><h2>External PR review queue</h2>${array(snapshot.review_queue).map(pr=>`<article class="source-card"><strong>PR #${esc(pr.number)} · ${esc(pr.title)}</strong><p>${esc(pr.state)} · CI ${esc(pr.ci_state||'unknown')} · head <code>${esc(String(pr.head||'').slice(0,12))}</code></p>${safeUrl(pr.url)?`<a href="${esc(safeUrl(pr.url))}" target="_blank" rel="noopener noreferrer">Open PR ↗</a>`:''}</article>`).join('')||'<p class="empty">No opted-in PRs in this bounded snapshot.</p>'}</section>
      <section><h2>Structured errors</h2>${array(snapshot.errors).map(error=>`<p class="error">${esc(typeof error==='string'?error:JSON.stringify(error))}</p>`).join('')||'<p class="empty">No structured snapshot errors returned.</p>'}</section></div>`;
  }

  function content(view,repo) {
    if(!repo)return '<p class="empty">No registered repository row was returned.</p>';
    if(view==='Overview')return overview(repo);
    if(view==='Outcomes')return outcomes(repo);
    if(view==='Attention')return attention(repo);
    if(view==='Flow')return flowView(repo);
    if(view==='Success')return success(repo);
    if(view==='System')return system(repo);
    if(view==='Settings')return WorkspaceSettings.markup(repo.slug);
    if(['Codebase','Atlas'].includes(view))return `<header class="workspace-head"><div><p class="eyebrow">Native tool</p><h1>${view}</h1><p>${view==='Codebase'?'Commit-pinned cached structure, comparison, symbols, and relationships.':'Factory reference diagrams and printed, commit-pinned source evidence.'}</p></div></header><div data-native-tool="${view.toLowerCase()}"></div>`;
    return overview(repo);
  }

  function render() {
    const current=route(),repo=currentRepo();
    if(repo&&['Attention','System'].includes(current.view))ensureOps(repo);
    const viewKey=`${repo?.slug}:${current.view}`;
    const moving=current.view==='Flow'&&state.viewKey===viewKey&&preferences.motion&&!reducedMotion.matches&&document.visibilityState==='visible';
    const positions=new Map(moving?[...app.querySelectorAll('[data-flow-case]')].map(node=>[node.dataset.flowCase,node.dataset.stage]):[]);
    const scrolls=new Map([...app.querySelectorAll('[data-flow-scroll]')].map(node=>[node.dataset.flowScroll,node.scrollTop]));
    const choices=new Map([...app.querySelectorAll('[data-action-choice]')].filter(node=>node.value!=='').map(node=>[node.dataset.actionChoice,JSON.stringify(JSON.parse(node.dataset.actionOptions)[Number(node.value)])]));
    applyTheme(repo);
    const focused=document.activeElement;
    const focusAttribute=app.contains(focused)?['data-action-choice','data-case','data-view','data-repository','data-theme','data-chat','data-refresh','data-palette','data-prepare-action'].find(name=>focused.hasAttribute(name)):null;
    const focusSelector=focusAttribute?`[${focusAttribute}="${CSS.escape(focused.getAttribute(focusAttribute))}"]`:null;
    const previousSettings=app.querySelector('.workspace-settings');
    const keepSettings=Boolean(repo&&previousSettings&&current.view==='Settings'&&previousSettings.querySelector('[data-real-settings]')?.dataset.repository===repo.slug);
    if(keepSettings)previousSettings.remove();
    app.setAttribute('aria-busy', String(state.loading));
    document.title=`${current.view} · Factory Workspace`;
    const previousTool=app.querySelector('[data-native-tool]');
    const keep=previousTool&&previousTool.dataset.nativeTool===current.view.toLowerCase()&&previousTool.dataset.repository===repo?.slug;
    if(keep)previousTool.remove(); else WorkspaceTools.dispose();
    app.innerHTML=`<div class="workspace-shell"><aside class="rail"><div><strong class="wordmark">Factory</strong><span>Outcome workspace</span></div>${nav(current.view)}<div class="rail-tools"><button class="btn shortcut" type="button" data-palette>Jump <kbd>${navigator.platform.includes('Mac')?'⌘':'Ctrl'} K</kbd></button>${themePicker()}<button class="btn" type="button" data-chat aria-expanded="${state.chatOpen}">FM dock</button></div><footer>${repo?`<strong>${esc(repo.slug)}</strong><small>${esc(repo.root||'registered root withheld')}</small>`:'No repository'}${signal(coverage(observation(repo)).status||'unobserved')}</footer></aside><div class="workspace-main"><header class="toolbar">${repoPicker(repo)}${collectionMarkup(repo)}</header><main id="main" tabindex="-1">${state.loading&&!repo?'<p class="empty">Loading registered repositories…</p>':state.error&&!repo?`<p class="error">${esc(state.error)}</p>`:content(current.view,repo)}</main></div></div>`;
    if(current.view==='Overview')app.querySelector('#main .workspace-head')?.insertAdjacentHTML('afterend',journeyMarkup());
    app.querySelector('#main')?.insertAdjacentHTML('beforeend',evidenceNotices(repo));
    for(const node of app.querySelectorAll('[data-action-choice]')){const chosen=choices.get(node.dataset.actionChoice);if(chosen){const index=JSON.parse(node.dataset.actionOptions).findIndex(option=>JSON.stringify(option)===chosen);if(index>=0)node.value=String(index);}}
    for(const node of app.querySelectorAll('[data-flow-scroll]'))node.scrollTop=scrolls.get(node.dataset.flowScroll)||0;
    for(const node of app.querySelectorAll('[data-flow-case]')){const before=positions.get(node.dataset.flowCase);if(before&&before!==node.dataset.stage)node._factoryMotion=Motion.animate(node,{x:[8,0],opacity:[.55,1]},{duration:.3,ease:[.16,1,.3,1]});}
    const host=app.querySelector('[data-native-tool]');
    if(host){host.dataset.repository=repo.slug;if(keep)host.replaceWith(previousTool);else WorkspaceTools.mount(host,host.dataset.nativeTool,repo.slug);}
    if(current.view==='Settings'){if(keepSettings)app.querySelector('.workspace-settings').replaceWith(previousSettings);else WorkspaceSettings.mount(app,repo?.slug);}
    if(current.view==='Attention'&&repo)WorkspaceDecisions.mountHistory(app.querySelector('[data-decision-history]'),repo.slug);
    applyPreferences();
    renderChat(false);
    if(document.activeElement===document.body)(focused?.isConnected?focused:focusSelector?app.querySelector(focusSelector):null)?.focus({preventScroll:true});
    if(state.viewKey!==viewKey&&preferences.motion&&!reducedMotion.matches&&document.visibilityState==='visible'){const main=app.querySelector('#main');main._factoryMotion=Motion.animate(main,{opacity:[.72,1]},{duration:.16,ease:[.16,1,.3,1]});}
    state.viewKey=viewKey;
    if(repo&&current.caseNumber&&!overlay.querySelector('.case-drawer'))syncDrawer();
  }

  async function fetchData(refresh=false) {
    if(state.request)return state.request;
    state.loading=!repositories().length; state.error='';
    const selected=route().repository||currentRepo()?.slug;
    const query=new URLSearchParams();
    if(selected)query.set('repository',selected);
    if(refresh)query.set('refresh','1');
    state.request=(async()=>{try{
      if(refresh)await WorkspaceAPI.unlock();
      const value=await WorkspaceAPI.json(`/api/data${query.size?`?${query}`:''}`);
      if(!Array.isArray(value?.repositories))throw new Error('Workspace response did not include registry rows.');
      state.data=value;
      const current=route();
      if(!value.repositories.some(row=>row.slug===current.repository)&&value.repositories[0]?.slug)setRoute({repository:value.repositories[0].slug,case:null},true);
    }catch(error){state.error=error.message||String(error);announce(`Workspace evidence unavailable: ${state.error}`);}finally{state.loading=false;state.request=null;render();}})();
    return state.request;
  }

  async function ensureOps(repo,force=false) {
    if(!repo)return;
    const record=opsRecord(repo);
    if(record.status==='loading'||(!force&&Date.now()<record.nextRequestAt))return;
    state.ops.set(repo.slug,{status:'loading',data:record.data,error:''});
    try{
      const query=new URLSearchParams({repository:repo.slug});if(force)query.set('fresh','1');
      const value=await WorkspaceAPI.json(`/api/snapshot?${query}`);
      state.ops.set(repo.slug,{status:value?.status==='building'?'building':'ready',data:value?.status==='building'?(record.data||value):value,error:'',nextRequestAt:Date.now()+preferences.pollSeconds*1000});
    }catch(error){state.ops.set(repo.slug,{status:'error',data:record.data,error:error.message||String(error),nextRequestAt:Date.now()+preferences.pollSeconds*1000});}render();
  }

  async function inspection(repo,number) {
    const key=`${repo.slug}:${number}`,cached=state.inspections.get(key);
    if(cached&&Date.now()<cached.expires)return cached.promise;
    const record={promise:WorkspaceAPI.json(`/api/inspect?${new URLSearchParams({repository:repo.slug,number:String(number)})}`),expires:Infinity};
    state.inspections.set(key,record);
    try{const value=await record.promise;record.expires=Date.now()+preferences.pollSeconds*1000;return value;}
    catch(error){state.inspections.delete(key);throw error;}
  }
  function drawerMarkup(repo,number,inspect) {
    const inspected=positive(inspect.case?.number)===number;
    const summary=(inspected?inspect.case:null)||cases(repo).find(item=>Number(item.number)===number)||{number,title:'Case evidence'};
    const ops=opsRecord(repo).data;
    const ticket=array(ops?.tickets).find(item=>Number(item.number)===number);
    const sources=array(inspect.sources);
    const observedAt=inspected?inspect.observed_at:observation(repo).observed_at;
    return `<div class="drawer-scrim" data-close-drawer></div><aside class="case-drawer" role="dialog" aria-modal="${narrow.matches}" aria-labelledby="drawer-title"><header><button class="icon-btn" type="button" data-close-drawer aria-label="Close inspection">×</button><div><p class="eyebrow">Case inspection · #${number}</p><strong>${esc(repo.slug)}</strong></div>${signal(coverage(inspect).status||summary.stage||'bounded')}</header><div class="drawer-body">
      <section><h1 id="drawer-title">${esc(summary.title||'Purpose unavailable')}</h1><div class="cluster">${safeUrl(summary.url)?`<a href="${esc(safeUrl(summary.url))}" target="_blank" rel="noopener noreferrer">Canonical issue ↗</a>`:''}<button class="btn" data-chat-case="${number}" type="button">Ask FM</button></div></section>
      <section><h2>Observed case state</h2><p class="notice">${inspected?'Case inspection':'Retained case summary'} · observed ${esc(date(observedAt))}. Source update time is separate from collection time.</p><dl class="kv"><dt>Canonical state</dt><dd>${esc(summary.state||'unknown')}</dd><dt>Derived stage</dt><dd>${esc(summary.stage||'unknown')}</dd><dt>Source updated</dt><dd>${esc(date(summary.updated_at))}</dd><dt>Labels</dt><dd>${esc(Array.isArray(summary.labels)?summary.labels.join(', ')||'none observed':'unknown')}</dd><dt>Assignments</dt><dd>${esc(Array.isArray(summary.assignees)?summary.assignees.join(', ')||'unassigned':'unknown')}</dd><dt>PR</dt><dd>${esc(summary.pr?.number||'none observed')}</dd></dl>${array(inspect.errors).length?`<p class="notice">Inspection gaps: ${array(inspect.errors).map(error=>esc(`${error.source||'source'} / ${error.scope||'scope'}: ${error.code||'unavailable'}`)).join('; ')}</p>`:''}</section>
      ${ticket?`<section><h2>Retained Ops diagnostics</h2><p class="notice">Snapshot generated ${esc(date(ops.generated_at))} · case lock observed ${esc(ticket.lock_held??'unknown')}. This is separate from the case inspection above.</p><details open><summary>Timeline · ${array(ticket.events).length} events</summary><pre>${esc(JSON.stringify(ticket.events,null,2))}</pre></details><details><summary>Attempts · ${array(ticket.attempts).length}</summary><pre>${esc(JSON.stringify(ticket.attempts,null,2))}</pre></details><details><summary>Gate and review</summary><pre>${esc(JSON.stringify({gate:ticket.gate,review:ticket.review,feedback:ticket.pr?.feedback},null,2))}</pre></details><details><summary>Branch, prompt, issue</summary><pre>${esc(JSON.stringify({worktree:ticket.worktree,prompt:ticket.prompt,issue:{labels:ticket.labels,body:ticket.body},pr:ticket.pr},null,2))}</pre></details></section>`:'<section><p class="notice">Full Ops ticket evidence is not in the retained snapshot yet.</p></section>'}
      <section><h2>Bounded cited evidence</h2>${sources.map(source=>`<details class="source-card"><summary>${esc(source.label||source.id||'source')}${source.truncated?' · truncated':''}</summary>${source.path?`<p class="mono">${esc(source.path)}</p>`:''}<pre>${esc(source.text||'No source text returned.')}</pre>${safeUrl(source.url)?`<a href="${esc(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">Canonical source ↗</a>`:''}</details>`).join('')||'<p class="empty">No cited source was returned. Absence remains unknown.</p>'}</section></div></aside>`;
  }
  function animatePanel(panel,visible) {
    if(!panel||(visible&&panel.dataset.entered&&!panel.dataset.closing)||(!visible&&panel.dataset.closing))return;
    panel._factoryMotion?.stop();
    if(visible)delete panel.dataset.closing;else panel.dataset.closing='true';
    if(!preferences.motion||reducedMotion.matches||document.visibilityState!=='visible'){
      if(visible){panel.style.transform='none';panel.style.opacity='1';panel.dataset.entered='true';}else panel.remove();
      return;
    }
    const entering=visible&&!panel.dataset.entered,width=panel.getBoundingClientRect().width;
    panel.dataset.entered='true';
    const animation=Motion.animate(panel,{x:entering?[width,0]:visible?0:width,opacity:entering?[0,1]:visible?1:0},{duration:visible?.24:.16,ease:[.16,1,.3,1]});
    panel._factoryMotion=animation;
    if(!visible)animation.then(()=>{if(panel._factoryMotion===animation)panel.remove();});
  }
  function syncModalState() {
    app.inert=state.palette||Boolean(currentRepo()&&narrow.matches&&(route().caseNumber||state.chatOpen));
    overlay.inert=state.palette;
    const dock=document.querySelector('#fm-dock');
    if(dock){dock.hidden=Boolean(route().caseNumber);dock.inert=state.palette;dock.setAttribute('role',narrow.matches?'dialog':'complementary');dock.setAttribute('aria-modal',String(narrow.matches));}
  }
  async function syncDrawer() {
    const repo=currentRepo(),number=route().caseNumber;
    syncModalState();
    let panel=overlay.querySelector('.case-drawer');
    if(!repo||!number){animatePanel(panel,false);overlay.querySelector('.drawer-scrim')?.remove();return;}
    const scope=`${repo.slug}:${number}`;
    if(panel?.dataset.scope===scope&&!panel.dataset.closing){panel.setAttribute('aria-modal',String(narrow.matches));return;}
    if(!panel){overlay.innerHTML='<div class="drawer-scrim" data-close-drawer></div><aside class="case-drawer" role="dialog"></aside>';panel=overlay.querySelector('.case-drawer');}
    if(!overlay.querySelector('.drawer-scrim'))panel.insertAdjacentHTML('beforebegin','<div class="drawer-scrim" data-close-drawer></div>');
    panel.dataset.scope=scope;
    panel.setAttribute('aria-modal',String(narrow.matches));
    panel.removeAttribute('aria-labelledby');
    panel.setAttribute('aria-label',`Case inspection ${number}`);
    panel.innerHTML='<div class="tool-state"><h2>Loading case evidence</h2><button class="btn" data-close-drawer>Close inspection</button></div>';
    animatePanel(panel,true);
    if(narrow.matches)panel.querySelector('button')?.focus({preventScroll:true});
    ensureOps(repo);
    const stillSelected=()=>currentRepo()?.slug===repo.slug&&route().caseNumber===number&&panel.dataset.scope===scope&&!panel.dataset.closing;
    try{
      const value=await inspection(repo,number);if(!stillSelected())return;
      const template=document.createElement('template');template.innerHTML=drawerMarkup(repo,number,value);
      panel.replaceChildren(...template.content.querySelector('.case-drawer').childNodes);
      panel.setAttribute('aria-labelledby','drawer-title');
      if(narrow.matches)panel.querySelector('[data-close-drawer]')?.focus({preventScroll:true});
    }catch(error){
      if(!stillSelected())return;
      panel.innerHTML=`<div class="tool-state"><h2>Inspection unavailable</h2><p>${esc(error.message||error)}</p><button class="btn" data-close-drawer>Close inspection</button></div>`;
      panel.querySelector('[data-close-drawer]')?.focus({preventScroll:true});
    }
  }

  function chatSession(repo,number) {const key=`${repo}:${number||'repository'}`;if(!state.chats.has(key))state.chats.set(key,{messages:[],draft:'',pending:false,error:''});return state.chats.get(key);}
  async function ensureChatMeta(repo){if(state.chatMeta.has(repo))return;state.chatMeta.set(repo,{loading:true});try{state.chatMeta.set(repo,await WorkspaceAPI.json(`/api/chat/meta?${new URLSearchParams({repository:repo})}`));}catch(error){state.chatMeta.set(repo,{error:error.message||String(error)});}renderChat();}
  function renderChat(refresh=true){
    let dock=document.querySelector('#fm-dock');
    if(!state.chatOpen){animatePanel(dock,false);syncModalState();return;}
    const repo=currentRepo();if(!repo)return;
    const scope=`${repo.slug}:${state.chatCase||'repository'}`;
    const focusQuestion=dock?.dataset.scope!==scope||dock?.contains(document.activeElement);
    syncModalState();
    if(dock?.dataset.closing)animatePanel(dock,true);
    if(dock?.dataset.scope===scope&&!refresh)return;
    const session=chatSession(repo.slug,state.chatCase),meta=state.chatMeta.get(repo.slug);
    const question=dock?.querySelector('#fm-question');
    const selection=dock?.dataset.scope===scope&&document.activeElement===question?[question.selectionStart,question.selectionEnd]:null;
    const scroll=dock?.dataset.scope===scope?dock.querySelector('.chat-log')?.scrollTop:null;
    if(!dock){dock=document.createElement('aside');dock.id='fm-dock';dock.className='fm-dock';document.body.append(dock);}
    dock.setAttribute('aria-label','Factory Manager chat');
    syncModalState();
    animatePanel(dock,true);
    dock.innerHTML=`<header><div><strong>Factory Manager</strong><span>${esc(repo.slug)} · ${state.chatCase?`#${state.chatCase}`:'repository'}</span></div><button class="icon-btn" data-close-chat aria-label="Close FM dock">×</button></header><div class="fm-boundary">Read-only advice. FM has no action tool and no terminal or Pi console capability.</div><label>Scoped context<select data-chat-context><option value="">Repository</option>${cases(repo).map(item=>`<option value="${item.number}"${Number(item.number)===state.chatCase?' selected':''}>#${item.number} · ${esc(item.title)}</option>`).join('')}</select></label><div class="chat-meta">${esc(meta?.provider?`${meta.provider} · ${meta.model} · ${meta.transport}`:meta?.error||'Loading provider disclosure…')}</div><div class="chat-log" role="log" aria-live="polite">${session.messages.map(message=>`<article data-role="${message.role}" data-status="${message.status||'complete'}"><strong>${message.role==='user'?'You':'FM'}</strong><p>${esc(message.text)}</p>${array(message.sources).length?`<footer>${array(message.sources).map(source=>safeUrl(source.url)?`<a href="${esc(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">${esc(source.label||source.id||'source')} ↗</a>`:`<span>${esc(source.label||source.id||source)}</span>`).join('')}</footer>`:''}</article>`).join('')||'<p class="empty">Ask about the selected bounded context. Conversation persists while you navigate.</p>'}</div><form data-chat-form><label for="fm-question">Question</label><textarea id="fm-question" maxlength="4000" rows="4" ${session.pending?'disabled':''}>${esc(session.draft)}</textarea>${session.error?`<p class="error">${esc(session.error)}</p>`:''}<button class="btn btn-accent" type="submit" ${session.pending?'disabled':''}>${session.pending?'Waiting for FM…':'Ask FM'}</button></form>`;
    dock.dataset.scope=scope;
    const log=dock.querySelector('.chat-log');log.scrollTop=scroll??log.scrollHeight;
    if(focusQuestion){const input=dock.querySelector('#fm-question');(session.pending?dock.querySelector('[data-close-chat]'):input).focus({preventScroll:true});if(selection&&!session.pending)input.setSelectionRange(...selection);}
    ensureChatMeta(repo.slug);
  }
  async function submitChat(){
    const repo=currentRepo(),number=state.chatCase;if(!repo)return;
    const session=chatSession(repo.slug,number),question=session.draft.trim();if(!question||session.pending)return;
    const pending={role:'assistant',text:'Reading bounded evidence…',status:'pending',sources:[]};
    session.messages.push({role:'user',text:question},pending);session.draft='';session.pending=true;session.error='';renderChat();
    try{
      const payload={repository:repo.slug,question};
      if(number){await inspection(repo,number);payload.number=number;}
      const value=await WorkspaceAPI.json('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      pending.text=value.answer;pending.sources=array(value.sources);pending.status='complete';
    }catch(error){pending.text=error.message||String(error);pending.status='error';session.error=pending.text;}
    finally{session.pending=false;if(currentRepo()?.slug===repo.slug&&state.chatCase===number){renderChat();const log=document.querySelector('.chat-log');log?.scrollTo(0,log.scrollHeight);}}
  }

  function applyPreferences(){
    document.body.dataset.density=preferences.density;
    document.body.dataset.motion=preferences.motion&&!reducedMotion.matches?'on':'off';
    document.body.dataset.hidden=String(document.visibilityState!=='visible');
    document.documentElement.style.setProperty('--inspector-width',`${preferences.inspectorWidth}px`);
    if(document.body.dataset.motion==='off')for(const node of document.querySelectorAll('.case-drawer,.fm-dock,[data-flow-case],#main'))if(node._factoryMotion){node._factoryMotion.stop();if(node.dataset.closing)node.remove();else{node.style.transform='none';node.style.opacity='1';}}
    if(state.pollSeconds===preferences.pollSeconds&&state.pollTimer)return;
    clearInterval(state.pollTimer);state.pollSeconds=preferences.pollSeconds;
    state.pollTimer=setInterval(()=>{if(document.visibilityState==='visible')fetchData(false);},preferences.pollSeconds*1000);
  }
  function closeChat(){state.chatOpen=false;render();(app.querySelector(`[data-chat-case="${state.chatCase}"]`)||app.querySelector('[data-chat]'))?.focus({preventScroll:true});}
  function closeDrawer(){const number=route().caseNumber;setRoute({case:null});const target=narrow.matches&&state.chatOpen?document.querySelector('#fm-question'):app.querySelector(`[data-case="${number}"]`)||app.querySelector('#main');target?.focus({preventScroll:true});}
  function closePalette(){state.palette=false;renderPalette();app.querySelector('[data-palette]')?.focus({preventScroll:true});}
  function openAction(button){const repo=currentRepo(),number=positive(button.dataset.prepareAction),card=button.closest('.attention-card'),select=card?.querySelector('[data-action-choice]');if(!repo||!number||select?.value==='')return;let options=[];try{options=JSON.parse(select.dataset.actionOptions);}catch{return;}const option=options[Number(select.value)];if(!option)return;WorkspaceDecisions.open({repository:repo.slug,number,title:option.title,effect:option.effect,next:option.next,requests:option.requests,mode:'action'});}
  function openPalette(){state.palette=true;state.paletteQuery='';renderPalette();}
  function renderPalette(){
    let root=document.querySelector('#palette');syncModalState();
    if(!state.palette){root?.remove();return;}
    if(!root){root=document.createElement('div');root.id='palette';document.body.append(root);}
    const input=root.querySelector('input'),selection=document.activeElement===input?[input.selectionStart,input.selectionEnd]:null;
    const query=state.paletteQuery.toLowerCase(),commands=[...VIEWS.map(view=>({view,label:view})),...cases(currentRepo()).map(item=>({case:item.number,label:`#${item.number} · ${item.title}`}))].filter(item=>item.label.toLowerCase().includes(query));
    root.innerHTML=`<div class="palette-scrim" data-close-palette><section class="palette" role="dialog" aria-modal="true" aria-label="Workspace commands"><input type="search" data-palette-search value="${esc(state.paletteQuery)}" placeholder="Navigate or inspect a case…" aria-label="Workspace command search"><div>${commands.map(item=>`<button type="button" ${item.view?`data-view="${slugify(item.view)}"`:`data-case="${item.case}"`}>${esc(item.label)}</button>`).join('')||'<p>No matches</p>'}</div></section></div>`;
    root.querySelector('input').focus();if(selection)root.querySelector('input').setSelectionRange(...selection);
  }

  document.addEventListener('click',event=>{
    const view=event.target.closest('[data-view]');if(view){state.palette=false;renderPalette();setRoute({view:view.dataset.view,case:null});requestAnimationFrame(()=>document.querySelector('#main')?.focus({preventScroll:true}));return;}
    const caseTarget=event.target.closest('[data-case]');if(caseTarget){state.palette=false;renderPalette();setRoute({case:caseTarget.dataset.case});return;}
    if(event.target.closest('[data-refresh]')){fetchData(true);if(['Attention','System'].includes(route().view))ensureOps(currentRepo(),true);return;}
    if(event.target.closest('[data-chat]')){if(state.chatOpen)closeChat();else{state.chatOpen=true;render();}return;}
    const chatCase=event.target.closest('[data-chat-case]');if(chatCase){state.chatOpen=true;state.chatCase=positive(chatCase.dataset.chatCase);setRoute({case:null});renderChat();return;}
    if(event.target.closest('[data-close-chat]')){closeChat();return;}
    if(event.target.closest('[data-close-drawer]')){closeDrawer();return;}
    if(event.target.closest('[data-palette]')){openPalette();return;}
    if(event.target.matches('[data-close-palette]')){closePalette();return;}
    const prepare=event.target.closest('[data-prepare-action]');if(prepare){openAction(prepare);return;}
    const direct=event.target.closest('[data-direct-outcome]');if(direct)WorkspaceDecisions.open({repository:currentRepo().slug,number:positive(direct.dataset.directOutcome),title:`Direction · ${direct.dataset.title}`,mode:'comment'});
    const attest=event.target.closest('[data-attest-outcome]');if(attest)WorkspaceDecisions.open({repository:currentRepo().slug,number:positive(attest.dataset.attestOutcome),title:`Owner attestation · ${attest.dataset.title}`,mode:'outcome'});
  });
  document.addEventListener('change',event=>{
    if(event.target.matches('[data-repository]')){state.chatCase=null;setRoute({repository:event.target.value,case:null});fetchData(false);return;}
    if(event.target.matches('[data-theme]')){state.themePreference=event.target.value;try{localStorage.setItem('factory-theme',event.target.value);}catch{}applyTheme(currentRepo());return;}
    if(event.target.matches('[data-chat-context]')){state.chatCase=positive(event.target.value);renderChat();}
  });
  document.addEventListener('input',event=>{if(event.target.matches('#fm-question'))chatSession(currentRepo().slug,state.chatCase).draft=event.target.value;if(event.target.matches('[data-palette-search]')){state.paletteQuery=event.target.value;renderPalette();}});
  document.addEventListener('submit',event=>{if(event.target.matches('[data-chat-form]')){event.preventDefault();submitChat();}});
  document.addEventListener('keydown',event=>{
    if(event.isComposing||document.querySelector('dialog[open]'))return;
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();state.palette?closePalette():openPalette();return;}
    if(event.key==='Escape'){if(state.palette)closePalette();else if(route().caseNumber)closeDrawer();else if(state.chatOpen)closeChat();}
    const modal=state.palette?'#palette':narrow.matches&&route().caseNumber?'#overlay':narrow.matches&&state.chatOpen?'#fm-dock':null;
    if(event.key==='Tab'&&modal){const controls=[...document.querySelectorAll(`${modal} button:not([disabled]),${modal} a[href],${modal} summary,${modal} input:not([disabled]),${modal} select,${modal} textarea:not([disabled])`)];const first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
  });
  window.addEventListener('popstate',()=>{render();syncDrawer();});
  window.addEventListener('pagehide',saveBaselines);
  document.addEventListener('visibilitychange',()=>{document.body.dataset.hidden=String(document.visibilityState!=='visible');if(document.visibilityState==='visible')fetchData(false);});
  document.addEventListener('workspace-preferences-change',()=>{applyPreferences();announce('Workspace preference saved in this browser.');});
  document.addEventListener('workspace-receipts-change',()=>{if(route().view==='Attention')WorkspaceDecisions.mountHistory(app.querySelector('[data-decision-history]'),currentRepo().slug);});
  document.addEventListener('workspace-settings-saved',event=>{state.chatMeta.delete(event.detail.repository);if(currentRepo()?.slug===event.detail.repository&&state.chatOpen)renderChat();});
  reducedMotion.addEventListener('change',applyPreferences);
  narrow.addEventListener('change',syncDrawer);

  applyPreferences();
  render();
  fetchData(false);
})();
