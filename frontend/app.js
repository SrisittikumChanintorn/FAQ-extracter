/**
 * app.js — FAQ Mining System
 * Handles: upload, pipeline run/poll, page routing,
 *          FAQ library/filter, smart search, clusters,
 *          analytics, data management (select/delete).
 */

const API = 'http://localhost:8000';

// ── State ─────────────────────────────────────────────────────────────────────
let state = {
  faqs:       [],
  clusters:   [],
  analytics:  null,
  apiReady:   false,
  searchTab:  'faqs',
  selectedFile: null,
  uploadedPath: null,
  pipelinePollTimer: null,
  dmSelected: new Set(),   // indices selected in Data Management
  selectAll:  false,
};

const STAGE_LABELS = [
  'Load Data',
  'Clean Text',
  'Filter Questions',
  'Generate AI Embeddings',
  'Remove Duplicates',
  'Group Similar Questions',
  'Quality Check Groups',
  'Build FAQ Answers (1/3)',
  'Build FAQ Answers (2/3)',
  'Build FAQ Answers (3/3)',
  'Build Search Index',
  'Save Results',
  'Generate Report',
];

const PAGE_META = {
  upload:   { title:'Upload Data',             sub:'Accepted formats: Excel, CSV, JSON' },
  pipeline: { title:'Process & Analyze',        sub:'Run AI extraction pipeline to generate FAQs' },
  faqs:     { title:'FAQ Library',              sub:'Browse and explore your extracted FAQ library' },
  search:   { title:'Smart Search',             sub:'Find answers using natural language — AI matches by meaning' },
  clusters: { title:'Topic Groups',             sub:'Questions grouped by subject — each group becomes one FAQ' },
  analytics:{ title:'Reports & Charts',         sub:'Insights from the extraction process' },
  manage:   { title:'Data Management',          sub:'Select and edit individual FAQs, or edit by Topic Groups' },
  viz:      { title:'3D Cluster Visualization', sub:'FAQ embeddings projected into 3D semantic space via PCA' },
  manual:   { title:'User Manual',              sub:'Documentation and system guide' },
  terms:    { title:'Terms of Service',         sub:'System usage terms and conditions' },
  privacy:  { title:'Privacy Policy',           sub:'Data processing and security policy' },
};

let dmViewMode = 'items';
let dmSelectedGroups = new Set();

// ── Utilities ─────────────────────────────────────────────────────────────────
function showLoading(txt='Loading…') {
  document.getElementById('loadingOverlay').classList.add('visible');
  document.getElementById('loadingText').textContent = txt;
}
function hideLoading() { document.getElementById('loadingOverlay').classList.remove('visible'); }

function showToast(msg, type='info') {
  const c = document.getElementById('toastContainer');
  const t = document.createElement('div');
  t.className = `toast ${type}`; t.textContent = msg;
  c.appendChild(t); setTimeout(()=>t.remove(), 3400);
}

async function apiFetch(path, opts={}) {
  const res = await fetch(`${API}${path}`, opts);
  if (!res.ok) { const txt = await res.text(); throw new Error(`${res.status}: ${txt}`); }
  return res.json();
}

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Page Navigation ────────────────────────────────────────────────────────────
function switchPage(id, el) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  document.getElementById(`page-${id}`).classList.add('active');
  el.classList.add('active');
  const m = PAGE_META[id];
  document.getElementById('pageTitle').textContent    = m.title;
  document.getElementById('pageSubtitle').textContent = m.sub;
  if (id==='manage') renderDMTable();
  if (id==='viz')    initVisualization();
}

// ── Health Check ──────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const d = await apiFetch('/health');
    document.getElementById('apiDot').className     = 'dot online';
    document.getElementById('apiStatus').textContent = `Online · ${d.faq_count} FAQs`;
    state.apiReady = true;
  } catch {
    document.getElementById('apiDot').className     = 'dot';
    document.getElementById('apiStatus').textContent = 'API Offline';
    state.apiReady = false;
  }
}

// ── Load All Data ─────────────────────────────────────────────────────────────
async function loadAll() {
  await checkHealth();
  if (!state.apiReady) {
    showToast('API is offline. Start the server first.', 'error');
    return;
  }
  showLoading('Fetching data…');
  try {
    const [faqData, clusterData, analyticsData] = await Promise.all([
      apiFetch('/faqs?limit=1000'),
      apiFetch('/clusters'),
      apiFetch('/analytics'),
    ]);
    state.faqs      = faqData.faqs      || [];
    state.clusters  = clusterData.clusters || [];
    state.analytics = analyticsData;
    updateStats();
    renderFAQs();
    renderClusters();
    renderAnalytics();
    populateClusterFilter();
    populateHintChips();
    renderDMTable();
    showToast(`Loaded ${state.faqs.length} FAQs from ${state.clusters.length} topic groups`, 'success');
  } catch(err) {
    showToast(`Failed to load data: ${err.message}`, 'error');
  } finally { hideLoading(); }
}

// ── Stats ─────────────────────────────────────────────────────────────────────
function updateStats() {
  document.getElementById('stat-faqs').textContent     = (state.faqs.length).toLocaleString();
  document.getElementById('stat-clusters').textContent = (state.clusters.length).toLocaleString();
  document.getElementById('badge-faqs').textContent    = state.faqs.length;
  document.getElementById('badge-clusters').textContent= state.clusters.length;
  if (state.analytics?.summary) {
    const s = state.analytics.summary;
    document.getElementById('stat-conversations').textContent =
      s.total_conversations?.toLocaleString() ?? '—';
    document.getElementById('stat-noise').textContent =
      s.noise_ratio_percent != null ? `${s.noise_ratio_percent}%` : '—';
  }
}

// ── FAQ Library ───────────────────────────────────────────────────────────────
function renderFAQs(faqs=state.faqs) {
  const grid = document.getElementById('faqGrid');
  document.getElementById('faqCount').textContent = `Showing ${faqs.length} items`;
  if (!faqs.length) {
    grid.innerHTML = `<div class="empty-state"><div class="empty-icon">📭</div>
      <p>No FAQs yet</p><small>Upload a file, then click "Start Analysis"</small></div>`;
    return;
  }
  grid.innerHTML = faqs.map((faq,i)=>`
    <div class="faq-card" id="faq-${i}" onclick="toggleFAQ(${i})">
      <div class="faq-expand-icon">▶</div>
      <div class="faq-card-header">
        <div class="faq-rank">#${i+1}</div>
        <div style="flex:1;min-width:0;padding-right:26px">
          <div class="faq-question">${escHtml(faq.faq_question)}</div>
          <div class="faq-badges">
            <span class="badge badge-cluster">🔗 Group ${faq.cluster_id}</span>
            <span class="badge badge-support">🗣️ ${faq.support_count} mentions</span>
            ${faq.similarity_score!=null
              ?`<span class="badge badge-score">⚡ ${(faq.similarity_score*100).toFixed(1)}%</span>`:''}
          </div>
        </div>
      </div>
      <div class="faq-answer">${escHtml(faq.faq_answer)}</div>
    </div>`).join('');
}

function toggleFAQ(i) { document.getElementById(`faq-${i}`).classList.toggle('expanded'); }

function populateClusterFilter() {
  const sel = document.getElementById('clusterFilter');
  const ids = [...new Set(state.faqs.map(f=>f.cluster_id))].sort((a,b)=>a-b);
  sel.innerHTML = `<option value="">📌 All Topic Groups</option>` +
    ids.map(id=>`<option value="${id}">Group ${id}</option>`).join('');
}

function filterFAQs() {
  const val = document.getElementById('clusterFilter').value;
  const filtered = val==='' ? state.faqs : state.faqs.filter(f=>String(f.cluster_id)===val);
  renderFAQs(filtered);
}

// ── Topic Groups ──────────────────────────────────────────────────────────────
function renderClusters(clusters=state.clusters) {
  const grid = document.getElementById('clusterGrid');
  if (!clusters.length) {
    grid.innerHTML = `<div class="empty-state"><div class="empty-icon">🔗</div><p>No topic groups yet</p></div>`;
    return;
  }
  const maxSup = Math.max(...clusters.map(c=>c.support_count||c.size||0),1);
  grid.innerHTML = clusters.map(c=>{
    const pct = Math.round((c.support_count||c.size||0)/maxSup*100);
    return `<div class="cluster-card">
      <div class="cluster-id-pill">◆ Group ${c.cluster_id}</div>
      <div class="cluster-question">${escHtml(c.faq_question||'—')}</div>
      <div class="cluster-bar-bg"><div class="cluster-bar-fill" style="width:${pct}%"></div></div>
      <div class="cluster-stats">
        <span>${c.size??'—'} questions</span>
        <span>${c.support_count??'—'} mentions</span>
      </div>
    </div>`;
  }).join('');
}

// ── Reports & Charts ──────────────────────────────────────────────────────────
function renderAnalytics() {
  const grid = document.getElementById('analyticsGrid');
  if (!state.analytics) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1"><div class="empty-icon">📊</div>
      <p>No analytics yet — run analysis first</p></div>`;
    return;
  }
  const s        = state.analytics.summary;
  const topics   = (state.analytics.top_faq_topics||[]).slice(0,8);
  const noiseQs  = state.analytics.unanswered_noise_questions||[];
  const maxSup   = Math.max(...topics.map(t=>t.support_count),1);

  const summaryHtml = `<div class="analytics-card"><h3>Pipeline Summary</h3>
    <div class="summary-grid">${[
      ['Total Conversations',   (s.total_conversations||0).toLocaleString()],
      ['Valid Questions',       (s.total_valid_questions||0).toLocaleString()],
      ['After Dedup',           (s.total_after_deduplication||0).toLocaleString()],
      ['Duplicates Removed',    (s.total_duplicates_removed||0).toLocaleString()],
      ['FAQs Generated',        (s.total_faqs_generated||0).toLocaleString()],
      ['Avg Group Size',        s.average_cluster_size??'—'],
      ['Clustered Questions',   (s.total_clustered_questions||0).toLocaleString()],
      ['Unclustered (Noise)',   (s.total_noise_questions||0).toLocaleString()],
    ].map(([l,v])=>`<div class="summary-item"><span class="summary-lbl">${l}</span><span class="summary-val">${v}</span></div>`).join('')}
    </div></div>`;

  const topicsHtml = `<div class="analytics-card"><h3>Top FAQ Topics</h3>
    <div class="top-topics-list">${topics.map((t,i)=>`
      <div class="topic-row">
        <span class="topic-num">${i+1}</span>
        <div class="topic-info">
          <div class="topic-q" title="${escHtml(t.faq_question)}">${escHtml(t.faq_question)}</div>
          <div class="topic-bar-bg"><div class="topic-bar-fill" style="width:${Math.round(t.support_count/maxSup*100)}%"></div></div>
        </div>
        <span class="topic-count">${t.support_count}</span>
      </div>`).join('')}
    </div></div>`;

  const cl=s.total_clustered_questions||0, ns=s.total_noise_questions||0, dp=s.total_duplicates_removed||0;
  const tot=cl+ns+dp||1;
  const distHtml = `<div class="analytics-card"><h3>Question Distribution</h3>
    <div class="pie-container">${buildDonut([
      {label:'Grouped into FAQs', value:cl, color:'#6366f1'},
      {label:'Unclustered',       value:ns, color:'#ef4444'},
      {label:'Duplicates Removed',value:dp, color:'#f59e0b'},
    ],tot)}</div></div>`;

  const noiseHtml = `<div class="analytics-card"><h3>Unclustered Questions (Sample)</h3>
    ${noiseQs.length
      ?`<div class="noise-list">${noiseQs.map(q=>`<div class="noise-item">${escHtml(q)}</div>`).join('')}</div>`
      :`<div class="empty-state" style="padding:24px 0"><p>No unclustered questions — great grouping! 🎉</p></div>`}
    </div>`;

  grid.innerHTML = summaryHtml + topicsHtml + distHtml + noiseHtml;
}

function buildDonut(segs, tot) {
  const sz=220, r=85, cx=110, cy=110, circ=2*Math.PI*r;
  let off=0;
  const paths=segs.map((seg, i)=>{
    const dash=circ*(seg.value/tot), gap=circ-dash;
    const pct=Math.round((seg.value/tot)*100);
    const p=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${seg.color}"
      stroke-width="30" stroke-dasharray="${dash} ${gap}" stroke-dashoffset="${-off}"
      transform="rotate(-90 ${cx} ${cy})"
      onmouseover="showChartTooltip(event, '${seg.label}', ${seg.value}, ${pct}, '${seg.color}')"
      onmousemove="moveChartTooltip(event)"
      onmouseout="hideChartTooltip()"
      style="transition: stroke-width 0.2s; cursor:pointer;"
      onmouseenter="this.setAttribute('stroke-width', '36')"
      onmouseleave="this.setAttribute('stroke-width', '30')">
      </circle>`;
    off+=dash; return p;
  });
  const legend=segs.map(s=>`<div class="pie-legend-item">
    <div class="pie-dot" style="background:${s.color}"></div>
    <span>${s.label}: <strong>${s.value}</strong></span>
  </div>`).join('');
  return `<svg width="${sz}" height="${sz}" viewBox="0 0 ${sz} ${sz}" style="overflow:visible;">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(255,255,255,.05)" stroke-width="30"/>
    ${paths.join('')}
  </svg><div class="pie-legend" style="margin-left:32px;">${legend}</div>`;
}

// Interactive Chart Tooltip Logic
let chartTooltipEl = null;
function getChartTooltip() {
  if (!chartTooltipEl) {
    chartTooltipEl = document.createElement('div');
    chartTooltipEl.style.cssText = 'position:fixed;pointer-events:none;background:rgba(11,17,32,.94);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:8px 12px;opacity:0;transition:opacity 0.15s;z-index:9999;box-shadow:0 8px 24px rgba(0,0,0,0.6);backdrop-filter:blur(8px);font-size:12px;color:#fff;display:flex;flex-direction:column;gap:4px;';
    document.body.appendChild(chartTooltipEl);
  }
  return chartTooltipEl;
}
function showChartTooltip(e, label, val, pct, color) {
  const tt = getChartTooltip();
  tt.innerHTML = `<div style="display:flex;align-items:center;gap:6px;"><div style="width:8px;height:8px;border-radius:2px;background:${color}"></div><span style="font-weight:600;color:var(--text-secondary)">${label}</span></div><div style="font-size:14px;font-weight:700;">${val} items <span style="color:var(--text-muted);font-weight:500;font-size:11px;">(${pct}%)</span></div>`;
  tt.style.opacity = '1';
  moveChartTooltip(e);
}
function moveChartTooltip(e) {
  if(!chartTooltipEl) return;
  // Offset slightly from cursor
  chartTooltipEl.style.left = (e.clientX + 15) + 'px';
  chartTooltipEl.style.top = (e.clientY + 15) + 'px';
}
function hideChartTooltip() {
  if(chartTooltipEl) chartTooltipEl.style.opacity = '0';
}

// ── Data Management ────────────────────────────────────────────────────────────
function setDMViewMode(mode) {
  dmViewMode = mode;
  document.getElementById('tab-dm-items').classList.toggle('active', mode==='items');
  document.getElementById('tab-dm-groups').classList.toggle('active', mode==='groups');
  renderDMTable();
}

function renderDMTable() {
  const body  = document.getElementById('dmBody');
  const empty = document.getElementById('dmEmpty');
  const label = document.getElementById('dmCountLabel');
  const thead = document.querySelector('#dmTable thead');
  
  state.dmSelected.clear();
  dmSelectedGroups.clear();
  document.getElementById('cbAll').checked = false;

  if (dmViewMode === 'items') {
    thead.innerHTML = `<tr>
      <th style="width:40px"><input type="checkbox" class="dm-cb" id="cbAll" onchange="onCbAllChange(this)"></th>
      <th>No.</th>
      <th>Question</th>
      <th>Answer (Preview)</th>
      <th>Topic Group</th>
      <th>Mentions</th>
    </tr>`;

    if (!state.faqs.length) {
      body.innerHTML = '';
      empty.style.display = 'block';
      label.textContent = '';
      populateRelabelDropdown();
      updateDeleteBtn();
      return;
    }
    empty.style.display = 'none';
    label.textContent = `${state.faqs.length} FAQs total`;

    body.innerHTML = state.faqs.map((faq,i)=>`
      <tr id="dm-row-${i}" onclick="toggleDMRow(event,${i})">
        <td><input type="checkbox" class="dm-cb" id="dm-cb-${i}" onchange="onRowCbChange(${i})" onclick="event.stopPropagation()"></td>
        <td style="color:var(--text-muted);font-size:12px;">${i+1}</td>
        <td><div class="dm-question" title="${escHtml(faq.faq_question)}">${escHtml(faq.faq_question)}</div></td>
        <td><div class="dm-answer" title="${escHtml(faq.faq_answer)}">${escHtml(faq.faq_answer)}</div></td>
        <td><span class="badge badge-cluster" title="Topic Group ID">Group ${faq.cluster_id}</span></td>
        <td><span class="badge badge-support" style="cursor:help;" title="${faq.support_count} related queries were combined to form this FAQ (after duplicates removed).">${faq.support_count}</span></td>
      </tr>`).join('');
  } else {
    thead.innerHTML = `<tr>
      <th style="width:40px"><input type="checkbox" class="dm-cb" id="cbAll" onchange="onCbAllChange(this)"></th>
      <th>Group ID</th>
      <th>Representative Question</th>
      <th>No. FAQs</th>
      <th>Mentions</th>
    </tr>`;

    if (!state.clusters.length) {
      body.innerHTML = '';
      empty.style.display = 'block';
      label.textContent = '';
      populateRelabelDropdown();
      updateDeleteBtn();
      return;
    }
    empty.style.display = 'none';
    label.textContent = `${state.clusters.length} Topic Groups total`;

    body.innerHTML = state.clusters.map((c, i)=>{
      const gId = c.cluster_id;
      return `
      <tr id="dm-row-g${gId}" onclick="toggleDMRowGroup(event,${gId})">
        <td><input type="checkbox" class="dm-cb" id="dm-cb-g${gId}" onchange="onRowCbGroupChange(${gId})" onclick="event.stopPropagation()"></td>
        <td><span class="badge badge-cluster" style="font-size:12px;padding:4px 10px;">ID ${gId}</span></td>
        <td><div class="dm-question" title="${escHtml(c.faq_question||'')}">${escHtml(c.faq_question||'—')}</div></td>
        <td style="font-weight:600;">${c.size||'—'}</td>
        <td><span class="badge badge-support">${c.support_count||0}</span></td>
      </tr>`;
    }).join('');
  }
}

function toggleDMRow(event, i) {
  const cb = document.getElementById(`dm-cb-${i}`);
  cb.checked = !cb.checked;
  onRowCbChange(i);
}

function onRowCbChange(i) {
  const cb = document.getElementById(`dm-cb-${i}`);
  const row= document.getElementById(`dm-row-${i}`);
  if (cb.checked) { state.dmSelected.add(i); row.classList.add('selected'); }
  else            { state.dmSelected.delete(i); row.classList.remove('selected'); }
  updateDeleteBtn();
  document.getElementById('cbAll').checked = (state.dmSelected.size === state.faqs.length);
}

function toggleDMRowGroup(event, gId) {
  const cb = document.getElementById(`dm-cb-g${gId}`);
  cb.checked = !cb.checked;
  onRowCbGroupChange(gId);
}

function onRowCbGroupChange(gId) {
  const cb = document.getElementById(`dm-cb-g${gId}`);
  const row= document.getElementById(`dm-row-g${gId}`);
  if (cb.checked) { dmSelectedGroups.add(gId); row.classList.add('selected'); }
  else            { dmSelectedGroups.delete(gId); row.classList.remove('selected'); }
  updateDeleteBtn();
  document.getElementById('cbAll').checked = (dmSelectedGroups.size === state.clusters.length);
}

function onCbAllChange(cbAll) {
  if (dmViewMode === 'items') {
    state.faqs.forEach((_,i)=>{
      const cb  = document.getElementById(`dm-cb-${i}`);
      const row = document.getElementById(`dm-row-${i}`);
      if (!cb || !row) return;
      cb.checked = cbAll.checked;
      if (cbAll.checked) { state.dmSelected.add(i); row.classList.add('selected'); }
      else               { state.dmSelected.delete(i); row.classList.remove('selected'); }
    });
  } else {
    state.clusters.forEach(c => {
      const gId = c.cluster_id;
      const cb  = document.getElementById(`dm-cb-g${gId}`);
      const row = document.getElementById(`dm-row-g${gId}`);
      if (!cb || !row) return;
      cb.checked = cbAll.checked;
      if (cbAll.checked) { dmSelectedGroups.add(gId); row.classList.add('selected'); }
      else               { dmSelectedGroups.delete(gId); row.classList.remove('selected'); }
    });
  }
  updateDeleteBtn();
}

function toggleSelectAll() {
  const cbAll = document.getElementById('cbAll');
  cbAll.checked = !cbAll.checked;
  onCbAllChange(cbAll);
}

function updateDeleteBtn() {
  const btn = document.getElementById('deleteSelectedBtn');
  const relabelBtn = document.getElementById('editGroupBtn');
  const n = dmViewMode === 'items' ? state.dmSelected.size : dmSelectedGroups.size;
  const isAll = dmViewMode === 'items' 
    ? (state.dmSelected.size === state.faqs.length && state.faqs.length > 0)
    : (dmSelectedGroups.size === state.clusters.length && state.clusters.length > 0);
  
  document.getElementById('deleteSelectedCount').textContent = n;
  
  const relabelCount = document.getElementById('relabelSelectedCount');
  if(relabelCount) relabelCount.textContent = n;

  btn.disabled = (n === 0);
  if(relabelBtn) relabelBtn.disabled = (n === 0);

  document.getElementById('selectAllBtn').textContent = isAll ? '☐ Deselect All' : '☑ Select All';
}

async function deleteSelected() {
  let indices = [];
  if (dmViewMode === 'items') {
    if (state.dmSelected.size === 0) return;
    indices = [...state.dmSelected].sort((a,b)=>b-a);
  } else {
    if (dmSelectedGroups.size === 0) return;
    state.faqs.forEach((f, i) => {
      if (dmSelectedGroups.has(f.cluster_id)) indices.push(i);
    });
    indices.sort((a,b)=>b-a);
  }

  if (indices.length === 0) return;

  const msg = dmViewMode === 'items' 
    ? `Delete ${indices.length} FAQ(s)?`
    : `Delete ${dmSelectedGroups.size} Topic Group(s)? This will delete ${indices.length} FAQs across these groups.`;
    
  if (!confirm(`${msg}\n\nThis cannot be undone.`)) return;

  const deleteBtn = document.getElementById('deleteSelectedBtn');
  deleteBtn.disabled = true;
  deleteBtn.innerHTML = `<div class="mini-spinner" style="border-color:rgba(239,68,68,.2);border-top-color:#ef4444"></div> Deleting…`;

  try {
    const res = await apiFetch('/faqs/delete', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ indices }),
    });

    // ── 1. Update the single source of truth ──────────────────────────
    const deletedSet   = new Set(indices);
    const remainingFAQs = state.faqs.filter((_,i) => !deletedSet.has(i));
    state.faqs         = remainingFAQs;
    state.dmSelected.clear();
    dmSelectedGroups.clear();

    // ── 2. Derive clusters from surviving FAQs ────────────────────────
    const survivingClusterIds = new Set(remainingFAQs.map(f => f.cluster_id));
    state.clusters = state.clusters.filter(c => survivingClusterIds.has(c.cluster_id));

    // ── 3. Patch analytics summary counts to match reality ────────────
    if (state.analytics?.summary) {
      state.analytics.summary.total_faqs_generated = remainingFAQs.length;
      if (state.analytics.top_faq_topics) {
        const survivingQuestions = new Set(remainingFAQs.map(f => f.faq_question));
        state.analytics.top_faq_topics = state.analytics.top_faq_topics
          .filter(t => survivingQuestions.has(t.faq_question));
      }
    }

    // ── 4. Re-render EVERY page that displays FAQ data ────────────────
    updateStats();               
    renderFAQs();                
    populateClusterFilter();     
    renderClusters();            
    renderAnalytics();           
    renderDMTable();             
    resetSearchUI();             
    populateHintChips();         

    showToast(
      remainingFAQs.length === 0
        ? `All FAQs deleted. Upload a file and run analysis to start fresh.`
        : `✅ Deleted ${res.deleted} FAQ(s). ${res.remaining} remaining.`,
      remainingFAQs.length === 0 ? 'info' : 'success'
    );
  } catch(err) {
    showToast(`Delete failed: ${err.message}`, 'error');
  } finally {
    deleteBtn.disabled = false;
    deleteBtn.innerHTML = `🗑 Delete Selected (<span id="deleteSelectedCount">0</span>)`;
    updateDeleteBtn();
  }
}

// ── Relabel (Edit Group) ──────────────────────────────────────────────────────
async function relabelSelected(newClusterId) {
  toggleEditDropdown(); 
  
  let indices = [];
  if (dmViewMode === 'items') {
    if (state.dmSelected.size === 0) return;
    indices = [...state.dmSelected];
  } else {
    if (dmSelectedGroups.size === 0) return;
    state.faqs.forEach((f, i) => {
      if (dmSelectedGroups.has(f.cluster_id)) indices.push(i);
    });
  }

  if (isNaN(newClusterId) || indices.length === 0) return;

  const countStr = dmViewMode === 'items' ? `${indices.length} FAQ(s)` : `all FAQs in ${dmSelectedGroups.size} selected Group(s) (${indices.length} total)`;
  if (!confirm(`Move ${countStr} to Group ${newClusterId}?`)) return;

  const relabelBtn = document.getElementById('editGroupBtn');
  relabelBtn.disabled = true;
  relabelBtn.innerHTML = `<div class="mini-spinner"></div>`;

  try {
    const res = await apiFetch('/faqs/relabel', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ indices, new_cluster_id: newClusterId }),
    });

    // 1. Update single source of truth locally
    indices.forEach(i => {
      state.faqs[i].cluster_id = newClusterId;
    });
    
    // Clear selection so the UI resets cleanly
    state.dmSelected.clear();
    dmSelectedGroups.clear();

    // 2. Refresh analytics
    try {
      state.analytics = await apiFetch('/analytics');
      state.clusters  = state.analytics.cluster_sizes || [];
    } catch(e) { console.warn("Could not refresh analytics after relabel", e); }

    // 3. Re-render EVERY page that displays FAQ data
    updateStats();
    renderFAQs();
    populateClusterFilter();
    renderClusters();
    renderAnalytics();
    renderDMTable();
    resetSearchUI();
    populateHintChips();

    showToast(`✅ Successfully moved ${res.relabeled} FAQ(s) to Group ${res.new_cluster_id}.`, 'success');
  } catch(err) {
    showToast(`Relabel failed: ${err.message}`, 'error');
  } finally {
    relabelBtn.disabled = false;
    relabelBtn.innerHTML = `✏️ Edit Group (<span id="relabelSelectedCount">0</span>) <span style="font-size:10px;margin-left:4px;color:rgba(255,255,255,0.6)">▼</span>`;
    updateDeleteBtn();
  }
}

function toggleEditDropdown() {
  const menu = document.getElementById('editGroupMenu');
  if (menu.style.display === 'block') {
    menu.style.display = 'none';
    hideRelabelPreview();
    return;
  }
  
  // Populate menu with current clusters
  let html = '';
  if (state.clusters.length === 0) {
    html = `<div style="padding:10px 14px;font-size:12px;color:var(--text-muted);text-align:center;">No groups available</div>`;
  } else {
    // Also add option to create "New Group" at the top
    const nextId = Math.max(0, ...state.clusters.map(c=>c.cluster_id)) + 1;
    html += `<div style="padding:8px 14px;font-size:12px;font-weight:600;color:var(--text-primary);cursor:pointer;border-bottom:1px solid var(--border);" 
               onmouseenter="showRelabelPreviewForGroup(${nextId})" 
               onmouseleave="hideRelabelPreview()"
               onclick="relabelSelected(${nextId})">
               ✨ Move to New Group (${nextId})
             </div>`;
             
    state.clusters.forEach(c => {
      html += `<div style="padding:8px 14px;font-size:12px;color:var(--text-secondary);cursor:pointer;transition:background 0.2s;" 
                 onmouseenter="this.style.background='var(--bg-glass-hover)'; showRelabelPreviewForGroup(${c.cluster_id})" 
                 onmouseleave="this.style.background='transparent'; hideRelabelPreview()"
                 onclick="relabelSelected(${c.cluster_id})">
                 🔗 Group ${c.cluster_id} <span style="float:right;color:var(--text-muted);font-size:10px;">${c.size||c.support_count||0} faqs</span>
               </div>`;
    });
  }
  menu.innerHTML = html;
  menu.style.display = 'block';
}

// Relabel Hover Tooltip Logic
function hideRelabelPreview() {
  const preview = document.getElementById('relabelPreview');
  if(preview) preview.style.display = 'none';
}

function showRelabelPreviewForGroup(clusterId) {
  const preview = document.getElementById('relabelPreview');
  const list = document.getElementById('relabelPreviewList');
  const title = document.getElementById('relabelPreviewTitle');
  
  if(!preview || !list || !title) return;
  
  const groupFaqs = state.faqs.filter(f => f.cluster_id === clusterId);
  if(groupFaqs.length === 0) {
    title.textContent = `New Group ${clusterId}`;
    list.innerHTML = `<li>Empty group. FAQs moved here will form a new topic.</li>`;
  } else {
    title.textContent = `Group ${clusterId} Preview`;
    // Take top 3 examples
    const samples = groupFaqs.slice(0,3);
    list.innerHTML = samples.map(f => `<li>${escHtml(f.faq_question.substring(0, 60))}...</li>`).join('');
    if(groupFaqs.length > 3) {
      list.innerHTML += `<li style="list-style:none;color:var(--text-muted);font-style:italic;margin-top:4px;">+ ${groupFaqs.length - 3} more</li>`;
    }
  }
  preview.style.display = 'block';
}

// ── Upload ────────────────────────────────────────────────────────────────────
function initUpload() {
  const drop = document.getElementById('dropZone');
  const inp  = document.getElementById('fileInput');
  inp.addEventListener('change', ()=>{ if(inp.files[0]) selectFile(inp.files[0]); });
  drop.addEventListener('dragover', e=>{e.preventDefault();drop.classList.add('dragging');});
  drop.addEventListener('dragleave', ()=>drop.classList.remove('dragging'));
  drop.addEventListener('drop', e=>{
    e.preventDefault(); drop.classList.remove('dragging');
    if(e.dataTransfer.files[0]) selectFile(e.dataTransfer.files[0]);
  });
}

function selectFile(f) {
  const ext = f.name.split('.').pop().toLowerCase();
  if(!['xlsx','xls','csv','json'].includes(ext)) {
    showToast(`File type ".${ext}" is not supported (use Excel, CSV, or JSON)`, 'error'); return;
  }
  state.selectedFile = f;
  document.getElementById('filePreview').classList.add('visible');
  document.getElementById('fileName').textContent = f.name;
  document.getElementById('fileSize').textContent = (f.size/1024).toFixed(1)+' KB';
  document.getElementById('fileIconBox').textContent = {xlsx:'📊',xls:'📊',csv:'📄',json:'🗂️'}[ext]||'📄';
  document.getElementById('uploadActions').style.display = 'flex';
  document.getElementById('uploadSuccess').style.display = 'none';
}

function clearFile() {
  state.selectedFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('filePreview').classList.remove('visible');
  document.getElementById('uploadActions').style.display = 'none';
  document.getElementById('uploadSuccess').style.display = 'none';
  document.getElementById('dataMappingSection').style.display = 'none';
}

async function doUpload() {
  if (!state.selectedFile) return;
  const btn = document.getElementById('uploadBtn');
  btn.disabled = true; btn.textContent = '⬆️ Uploading…';
  try {
    const fd = new FormData();
    fd.append('file', state.selectedFile);
    const res  = await fetch(`${API}/upload`, {method:'POST', body:fd});
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail||res.statusText);
    
    state.uploadedPath = body.saved_path;
    document.getElementById('uploadActions').style.display  = 'none';
    showToast(`✅ "${body.filename}" uploaded successfully. Fetching preview...`, 'success');
    
    // Fetch preview for data mapping
    const prevRes = await apiFetch('/preview-data', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ file_path: body.saved_path })
    });
    
    renderMappingUI(prevRes.columns, prevRes.rows, body.filename);

  } catch(err) {
    showToast(`Upload failed: ${err.message}`, 'error');
  } finally {

    btn.disabled = false; btn.textContent = '⬆️ Upload File';
  }
}

function renderMappingUI(cols, rows, filename) {
  document.getElementById('dataMappingSection').style.display = 'block';
  
  const selQ = document.getElementById('mapQuestion');
  const selA = document.getElementById('mapAnswer');
  
  const opts = cols.map(c => `<option value="${c}">${c}</option>`).join('');
  selQ.innerHTML = opts;
  selA.innerHTML = opts;

  // Auto-matching logic
  const lowerCols = cols.map(c => c.toLowerCase());
  
  // Try to find question column
  const qMatch = lowerCols.findIndex(c => c.includes('question') || c.includes('query') || c.includes('customer') || c.includes('msg'));
  if(qMatch !== -1) selQ.selectedIndex = qMatch;
  else if(cols.length > 0) selQ.selectedIndex = 0;

  // Try to find answer column
  const aMatch = lowerCols.findIndex(c => c.includes('answer') || c.includes('reply') || c.includes('admin') || c.includes('response'));
  if(aMatch !== -1) selA.selectedIndex = aMatch;
  else if(cols.length > 1) selA.selectedIndex = 1;

  // Render preview table
  const thead = document.getElementById('previewHead');
  const tbody = document.getElementById('previewBody');
  
  thead.innerHTML = `<tr>${cols.map(c => `<th>${escHtml(c)}</th>`).join('')}</tr>`;
  tbody.innerHTML = rows.map(r => 
    `<tr>${cols.map(c => `<td><div style="max-width:200px;overflow:hidden;text-overflow:ellipsis;" title="${escHtml(String(r[c]||''))}">${escHtml(String(r[c]||''))}</div></td>`).join('')}</tr>`
  ).join('');
  
  // Store filename for later
  state.uploadedFilename = filename;
}

async function applyDataMapping() {
  const btn = document.getElementById('applyMappingBtn');
  const colQ = document.getElementById('mapQuestion').value;
  const colA = document.getElementById('mapAnswer').value;

  if (colQ === colA) {
    showToast("Question and Answer cannot use the same column.", "error");
    return;
  }

  btn.disabled = true;
  btn.innerHTML = `<div class="mini-spinner"></div> Applying mapping...`;

  try {
    const res = await apiFetch('/apply-mapping', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        file_path: state.uploadedPath,
        customer_col: colQ,
        admin_col: colA
      })
    });
    
    // Success: Hide mapping, show Success block, unlock Pipeline button.
    document.getElementById('dataMappingSection').style.display = 'none';
    document.getElementById('uploadSuccess').style.display = 'block';
    document.getElementById('uploadSuccessMsg').textContent = `Mapped ${res.row_count} rows successfully.`;
    document.getElementById('pipelineInputLabel').textContent = `${state.uploadedFilename} (Mapped)`;
    
    showToast(`✅ Data mapped successfully. Ready for processing!`, 'success');
  } catch(err) {
    showToast(`Mapping failed: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `Confirm Mapping & Continue →`;
  }
}

// ── Pipeline ──────────────────────────────────────────────────────────────────
function initStageList() {
  document.getElementById('stageList').innerHTML = STAGE_LABELS.map((s,i)=>`
    <div id="stage-row-${i+1}" style="display:flex;align-items:center;gap:9px;padding:5px 8px;border-radius:6px;transition:var(--transition);">
      <span id="stage-icon-${i+1}" style="font-size:13px;">⬜</span>
      <span style="font-size:12px;color:var(--text-muted);">Step ${i+1}: ${s}</span>
    </div>`).join('');
}

function updateStageUI(cur) {
  for(let i=1;i<=13;i++){
    const row=document.getElementById(`stage-row-${i}`);
    const icon=document.getElementById(`stage-icon-${i}`);
    if(!row||!icon) continue;
    if(i<cur){row.style.background='rgba(16,185,129,.06)';icon.textContent='✅';}
    else if(i===cur){row.style.background='rgba(99,102,241,.1)';icon.textContent='🔵';}
    else{row.style.background='transparent';icon.textContent='⬜';}
  }
}

async function runPipeline() {
  const btn = document.getElementById('runBtn');
  btn.disabled = true; btn.textContent = '⏳ Running…';
  document.getElementById('stopBtn').style.display = 'block';
  document.getElementById('logTerminal').innerHTML = '';
  try {
    const res = await apiFetch('/run-pipeline', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({input_file: ''}),
    });
    appendLog(`▶ ${res.message}`, 'log-stage');
    appendLog(`📄 Input: ${res.input_file}`, 'log-info');
    startPoll();
  } catch(err) {
    appendLog(`❌ Error: ${err.message}`, 'log-error');
    showToast(`Analysis failed: ${err.message}`, 'error');
    resetRunBtn();
  }
}

function appendLog(txt, cls='') {
  const el = document.getElementById('logTerminal');
  const d  = document.createElement('div');
  d.className = cls; d.textContent = txt;
  el.appendChild(d); el.scrollTop = el.scrollHeight;
}

function clearLog() { document.getElementById('logTerminal').innerHTML=''; }

let _lastLogLen = 0;
function startPoll() {
  _lastLogLen = 0;
  if(state.pipelinePollTimer) clearInterval(state.pipelinePollTimer);
  state.pipelinePollTimer = setInterval(pollStatus, 1200);
}

async function pollStatus() {
  try {
    const d = await apiFetch('/pipeline-status');
    const pct = d.total_stages ? Math.round((d.stage/d.total_stages)*100) : 0;
    document.getElementById('progressFill').style.width  = pct+'%';
    document.getElementById('progressPct').textContent   = pct+'%';
    document.getElementById('progressLabel').textContent = d.stage_name||d.status;
    document.getElementById('pipelineStatusBadge').textContent = `Status: ${d.status}`;
    const newLines = (d.logs||[]).slice(_lastLogLen);
    _lastLogLen = (d.logs||[]).length;
    newLines.forEach(l=>appendLog(l,
      l.startsWith('✅')?'log-success':l.startsWith('❌')?'log-error':
      l.startsWith('Stage')?'log-stage':l.startsWith('  →')?'log-info':''));
    updateStageUI(d.stage);
    if(d.status==='done'){
      stopPoll();
      document.getElementById('progressFill').style.width='100%';
      document.getElementById('progressPct').textContent='100%';
      document.getElementById('progressLabel').textContent=`✅ Complete (${d.elapsed_seconds}s) · ${d.faq_count} FAQs`;
      showToast(`✅ Analysis complete! ${d.faq_count} FAQs generated.`, 'success');
      resetRunBtn(); loadAll();
    } else if(d.status==='error'){
      stopPoll();
      appendLog(`❌ ${d.error||'Unknown error'}`, 'log-error');
      showToast(`Analysis failed: ${d.error}`, 'error');
      resetRunBtn();
    }
    if(d.status==='running'){
      document.getElementById('apiDot').className = 'dot running';
      document.getElementById('apiStatus').textContent = `Analyzing… Step ${d.stage}/13`;
    }
  } catch{}
}

function stopPoll() {
  if(state.pipelinePollTimer){clearInterval(state.pipelinePollTimer);state.pipelinePollTimer=null;}
}

function resetRunBtn() {
  const btn = document.getElementById('runBtn');
  btn.disabled=false; btn.textContent='▶ Start Analysis';
  document.getElementById('stopBtn').style.display='none';
  checkHealth();
}

// ── Smart Search ──────────────────────────────────────────────────────────────
let _debounceTimer;
function initSearch() {
  const inp = document.getElementById('searchInput');
  inp.addEventListener('input', ()=>{
    clearTimeout(_debounceTimer);
    const q = inp.value.trim();
    if(!q){resetSearchUI();return;}
    _debounceTimer = setTimeout(()=>doSearch(q), 350);
  });
}



window.fillSearch = function(txt) {
  const el = document.getElementById('searchInput');
  el.value = txt; el.dispatchEvent(new Event('input')); el.focus();
};

function populateHintChips() {
  const el = document.getElementById('hintChips');
  if(!el) return;
  el.innerHTML = state.faqs.slice(0,6)
    .map(f=>`<span class="hint-chip" onclick="fillSearch('${escHtml(f.faq_question.substring(0,50)).replace(/'/g,"&#39;")}')">${escHtml(f.faq_question.substring(0,50))}</span>`)
    .join('');
}

async function doSearch(q) {
  const topK = parseInt(document.getElementById('topKSelect').value);
  const hdr  = document.getElementById('searchResultsHeader');
  const res  = document.getElementById('searchResults');
  res.innerHTML=`<div style="display:flex;align-items:center;gap:10px;padding:14px;color:var(--text-secondary);font-size:13px;"><div class="mini-spinner"></div> Searching…</div>`;
  hdr.style.display='none';
  try {
    const data = await apiFetch('/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q,top_k:topK})});
    renderSearchFAQs(data.results||[],q);
    hdr.style.display='flex';
    document.getElementById('searchQueryDisplay').textContent=`"${q}"`;
    document.getElementById('resultCount').textContent=`${(data.results||[]).length} result(s)`;
  } catch(err){
    res.innerHTML=`<div class="empty-state"><div class="empty-icon">⚠️</div><p>Search failed: ${escHtml(err.message)}</p></div>`;
  }
}

function renderSearchFAQs(items, q) {
  const el = document.getElementById('searchResults');
  if(!items.length){el.innerHTML=`<div class="empty-state"><div class="empty-icon">🔍</div><p>No FAQs found for "${escHtml(q)}"</p></div>`;return;}
  el.innerHTML=`<div class="faq-grid">${items.map((faq,i)=>`
    <div class="faq-card" id="sfaq-${i}" onclick="document.getElementById('sfaq-${i}').classList.toggle('expanded')">
      <div class="faq-expand-icon">▶</div>
      <div class="faq-card-header">
        <div class="faq-rank">#${i+1}</div>
        <div style="flex:1;min-width:0;padding-right:26px">
          <div class="faq-question">${escHtml(faq.faq_question)}</div>
          <div class="faq-badges">
            <span class="badge badge-cluster">🔗 Group ${faq.cluster_id}</span>
            <span class="badge badge-support">🗣️ ${faq.support_count} mentions</span>
            ${faq.similarity_score!=null?`<span class="badge badge-score">⚡ ${(faq.similarity_score*100).toFixed(1)}%</span>`:''}
          </div>
        </div>
      </div>
      <div class="faq-answer">${escHtml(faq.faq_answer)}</div>
    </div>`).join('')}</div>`;
}



function resetSearchUI() {
  document.getElementById('searchResultsHeader').style.display='none';
  document.getElementById('searchResults').innerHTML=`
    <div class="empty-state">
      <div class="empty-icon">✨</div>
      <p><strong>AI-Powered Search</strong></p>
      <small>Type any question and AI will find the most relevant answer</small>
      <div class="hint-chips" id="hintChips" style="justify-content:center;margin-top:16px;"></div>
    </div>`;
  populateHintChips();
}

// ── Data Manipulation ─────────────────────────────────────────────────────────

async function loadRawData() {
  const container = document.getElementById('manipulateContainer');
  const empty = document.getElementById('manipulateEmpty');
  const tableWrap = document.getElementById('manipulateTableWrapper');
  const thead = document.getElementById('manipulateHead');
  const tbody = document.getElementById('manipulateBody');

  try {
    empty.style.display = 'block';
    empty.innerHTML = '<div class="mini-spinner"></div> Loading data...';
    tableWrap.style.display = 'none';

    const res = await apiFetch('/uploaded-data');
    if (!res.data || res.data.length === 0) {
      empty.innerHTML = 'File is empty or no valid data found.';
      return;
    }

    state.manipulateData = res.data; // Store locally
    
    // Extract headers from first object
    const headers = Object.keys(res.data[0]);
    if (headers.length === 0) {
      empty.innerHTML = 'Data has no columns.';
      return;
    }

    // Render Headers
    thead.innerHTML = `<tr>${headers.map(h => `<th>${escHtml(h)}</th>`).join('')}</tr>`;

    // Render Body (contenteditable)
    let html = '';
    // Limit to 500 rows to prevent extreme browser lag on huge datasets
    const maxRows = Math.min(res.data.length, 500); 
    for(let i=0; i<maxRows; i++) {
        const rowObj = res.data[i];
        html += `<tr data-idx="${i}">`;
        headers.forEach(h => {
             // Null/undefined safety
             const val = rowObj[h] !== null && rowObj[h] !== undefined ? rowObj[h] : ""; 
             html += `<td contenteditable="true" data-col="${escHtml(h)}" onblur="updateManipulateCell(this)" style="outline:none;background:rgba(255,255,255,0.01)">${escHtml(String(val))}</td>`;
        });
        html += `</tr>`;
    }
    
    tbody.innerHTML = html;
    empty.style.display = 'none';
    tableWrap.style.display = 'block';
    
    if(res.data.length > 500) {
        showToast('Viewing top 500 rows for performance. Full file will be saved.', 'info');
    }
  } catch(err) {
    empty.innerHTML = `No uploaded data found or error: ${err.message}`;
    tableWrap.style.display = 'none';
  }
}

function updateManipulateCell(td) {
  const tr = td.parentElement;
  const idx = parseInt(tr.getAttribute('data-idx'), 10);
  const col = td.getAttribute('data-col');
  if(!isNaN(idx) && state.manipulateData && state.manipulateData[idx]) {
      // Decode entities since innerText or innerHTML will have escaped values
      const val = td.innerText; 
      state.manipulateData[idx][col] = val;
  }
}

async function saveRawData() {
  if (!state.manipulateData) {
      showToast('No data to save.', 'warning');
      return;
  }
  const btn = document.getElementById('saveManipulateBtn');
  btn.disabled = true;
  btn.innerHTML = '<div class="mini-spinner"></div> Saving...';
  
  try {
      await apiFetch('/save-uploaded-data', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data: state.manipulateData })
      });
      showToast('Data saved successfully. You can now run the analysis.', 'success');
  } catch(err) {
      showToast(`Error saving data: ${err.message}`, 'error');
  } finally {
      btn.disabled = false;
      btn.innerHTML = '💾 Save Changes';
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
(async function init() {
  initUpload();
  initStageList();
  initSearch();
  await checkHealth();
  if(state.apiReady) {
    try { await loadAll(); } catch {}
  }
})();
