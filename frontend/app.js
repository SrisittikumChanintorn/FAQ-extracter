/**
 * app.js — FAQ Mining System
 * Handles: upload, pipeline run/poll, page routing,
 *          FAQ library/filter, smart search, clusters,
 *          analytics, data management (select/delete).
 */

const API = typeof window !== 'undefined' && window.location && window.location.origin ? '' : 'http://localhost:8000';

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
  'LLM Extract FAQs + Groups',
  'Merge Batches (dedupe)',
  'Build Search Index',
  'Save & Report',
];

const PAGE_META = {
  upload:    { title: 'Upload Data',              sub: 'Accepted formats: Excel, CSV, JSON' },
  manipulate:{ title: 'Data Manipulation',        sub: 'Edit uploaded data before running analysis' },
  pipeline:  { title: 'Process & Analyze',       sub: 'Run AI extraction pipeline to generate FAQs' },
  faqs:      { title: 'FAQ Library',             sub: 'Browse and explore your extracted FAQ library' },
  search:    { title: 'Smart Search',            sub: 'Find answers using natural language — AI matches by meaning' },
  clusters:  { title: 'Topic Groups',            sub: 'Questions grouped by subject' },
  analytics: { title: 'Reports & Charts',        sub: 'Insights from the extraction process' },
  manage:    { title: 'Data Management',         sub: 'Select and edit groups, merge or delete' },
  viz:       { title: '3D Cluster Visualization', sub: 'FAQ groups in 3D semantic space' },
  manual:    { title: 'User Manual',             sub: 'Documentation and system guide' },
  terms:     { title: 'Terms of Service',       sub: 'System usage terms and conditions' },
  privacy:   { title: 'Privacy Policy',         sub: 'Data processing and security policy' },
};


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

async function apiFetch(path, opts = {}) {
  const base = API || '';
  const url = base ? `${base}${path}` : path;
  const res = await fetch(url, opts);
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
  if (id === 'manage') renderDMTable();
  if (id === 'manipulate') loadRawData();
  if (id === 'viz') initVisualization();
}

// ── Health Check ──────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const base = API || '';
    const res = await fetch(base ? `${base}/health` : '/health');
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || res.status);
    document.getElementById('apiDot').className = 'dot online';
    const n = d.faq_count != null ? d.faq_count : 0;
    document.getElementById('apiStatus').textContent = n ? `Online · ${n} groups` : 'Online · Ready';
    state.apiReady = true;
  } catch {
    document.getElementById('apiDot').className = 'dot';
    document.getElementById('apiStatus').textContent = 'Offline';
    state.apiReady = false;
  }
}

// ── Load All Data ─────────────────────────────────────────────────────────────
async function loadAll() {
  await checkHealth();
  if (!state.apiReady) {
    showToast('API offline. Start the server first.', 'error');
    return;
  }
  showLoading('Loading…');
  try {
    const [faqData, clusterData, analyticsData] = await Promise.all([
      apiFetch('/faqs?limit=1000'),
      apiFetch('/clusters'),
      apiFetch('/analytics'),
    ]);
    state.faqs = faqData.faqs || [];
    state.clusters = clusterData.clusters || [];
    state.analytics = analyticsData || null;
    updateStats();
    renderFAQs();
    renderClusters();
    renderAnalytics();
    populateClusterFilter();
    populateHintChips();
    renderDMTable();
    const totalItems = state.faqs.reduce((a, g) => a + (g.total_faqs || (g.faqs || []).length), 0);
    showToast(totalItems ? `Loaded ${totalItems} FAQs in ${state.faqs.length} groups` : 'Ready. Upload a file and run analysis.', 'success');
  } catch (err) {
    showToast('Failed to load data.', 'error');
  } finally { hideLoading(); }
}

// ── Stats ─────────────────────────────────────────────────────────────────────
function updateStats() {
  const totalFaqItems = (state.faqs || []).reduce((acc, g) => acc + (g.total_faqs || (g.faqs || []).length), 0);
  const numGroups = (state.faqs || []).length;
  const el = (id) => document.getElementById(id);
  if (el('stat-faqs')) el('stat-faqs').textContent = totalFaqItems.toLocaleString();
  if (el('stat-clusters')) el('stat-clusters').textContent = numGroups.toLocaleString();
  if (el('badge-faqs')) el('badge-faqs').textContent = totalFaqItems;
  if (el('badge-clusters')) el('badge-clusters').textContent = numGroups;
  if (state.analytics?.summary) {
    const s = state.analytics.summary;
    if (el('stat-conversations')) el('stat-conversations').textContent = s.total_conversations?.toLocaleString() ?? '—';
    if (el('stat-noise')) el('stat-noise').textContent = s.noise_ratio_percent != null ? `${s.noise_ratio_percent}%` : '—';
  } else {
    if (el('stat-conversations')) el('stat-conversations').textContent = '—';
    if (el('stat-noise')) el('stat-noise').textContent = '—';
  }
}

// ── FAQ Library ───────────────────────────────────────────────────────────────
function renderFAQs(faqs=state.faqs) {
  const grid = document.getElementById('faqGrid');
  document.getElementById('faqCount').textContent = `Show ${faqs.length} groups`;
  if (!faqs.length) {
    grid.innerHTML = `<div class="empty-state"><div class="empty-icon">📭</div>
      <p>No FAQs yet</p><small>Upload a file, then click Start Analysis</small></div>`;
    return;
  }
  
  grid.innerHTML = faqs.map((g, i) => {
    const groupName = g.group_name || g.faq_question || 'Unnamed Category';
    const innerFaqs = g.faqs || [];
    const totalCount = g.total_faqs || innerFaqs.length || 0;
    
    const faqsHtml = innerFaqs.map((faq, j) => {
      const mention = faq.mention_count != null ? faq.mention_count : 1;
      return `
      <div class="inner-faq-card" onclick="this.classList.toggle('expanded')">
        <div class="inner-faq-header">
          <div class="inner-faq-q-icon">Q:</div>
          <div class="inner-faq-question">${escHtml(faq.question)}</div>
          ${mention > 1 ? `<span class="badge badge-support" style="font-size:10px;flex-shrink:0;">${mention} mentions</span>` : ''}
          <div class="inner-faq-expand-icon">▶</div>
        </div>
        <div class="inner-faq-answer">
          <div style="display:flex;align-items:flex-start;gap:8px;">
            <div style="color:var(--success);font-weight:700;font-size:12px;margin-top:1px;">A:</div>
            <div style="flex:1;">${escHtml(faq.answer)}</div>
          </div>
        </div>
      </div>
    `}).join('');

    return `
      <div class="faq-group-card">
        <div class="faq-group-header">
          <div class="faq-group-rank">#${i+1}</div>
          <div class="faq-group-info">
            <div class="faq-group-title">${escHtml(groupName)}</div>
            <div class="faq-badges">
              <span class="badge badge-cluster">📁 Category</span>
              <span class="badge badge-support">🗣️ ${g.support_count || totalCount} mentions</span>
              <span class="badge badge-count">📝 ${totalCount} FAQs</span>
              ${g.confidence_score != null ? `<span class="badge badge-score" title="Confidence Score">⚡ ${Math.round(g.confidence_score * 100)}%</span>` : ''}
              <button class="btn btn-ghost" style="padding:4px 10px;font-size:11px;border-radius:20px;" onclick="event.stopPropagation(); window.openEditModal(${i})">✏️ Edit Group</button>
            </div>
          </div>
        </div>
        <div class="faq-group-body">
          ${faqsHtml}
        </div>
      </div>
    `;
  }).join('');
}

function toggleFAQ(i) { document.getElementById(`faq-${i}`).classList.toggle('expanded'); }

function populateClusterFilter() {
  const sel = document.getElementById('clusterFilter');
  if (!sel) return;
  sel.innerHTML = `<option value="">All groups</option>` +
    state.faqs.map(g => `<option value="${g.cluster_id ?? g.group_id ?? ''}">${escHtml(g.group_name || 'Other')}</option>`).join('');
}

function filterFAQs() {
  const val = document.getElementById('clusterFilter').value;
  const filtered = val === '' ? state.faqs : state.faqs.filter(f => String(f.cluster_id ?? f.group_id ?? '') === val);
  renderFAQs(filtered);
}

// ── Topic Groups ──────────────────────────────────────────────────────────────
function renderClusters() {}

// ── Reports & Charts ──────────────────────────────────────────────────────────
async function renderAnalytics() {
  const grid = document.getElementById('analyticsGrid');
  if (!state.analytics) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1"><div class="empty-icon">📊</div>
      <p>No analytics yet — run analysis first</p></div>`;
    return;
  }
  
  const s = state.analytics.summary;
  const pass = s.questions_passed_into_groups ?? s.total_clustered_questions ?? 0;
  const fail = s.questions_discarded ?? ((s.total_valid_questions ?? 0) - (s.total_clustered_questions ?? 0));
  const total = Math.max(pass + fail, 1);

  // 1. Top Row: Pie chart — Pass Filter vs Not Pass (filtered out / duplicate removed)
  const topRow = document.getElementById('analyticsTopRow');
  topRow.innerHTML = `
    <div class="analytics-card" style="height: 300px; display: flex; flex-direction: column;">
      <h3>Pass Filter vs Not Pass</h3>
      <div class="pie-container" style="flex:1; justify-content: center;">
        ${buildDonut([
          {label: 'Pass Filter (into FAQ)', value: pass, color: '#10b981'},
          {label: 'Not Pass (filtered out)', value: fail, color: '#ef4444'}
        ], total)}
      </div>
    </div>
    <div class="analytics-card" style="height: 300px;">
      <h3>Pipeline Efficiency</h3>
      <div class="summary-grid" style="margin-top: 10px;">
        ${[
          ['Total Input', (s.total_conversations || 0).toLocaleString()],
          ['Valid Questions', (s.total_valid_questions || 0).toLocaleString()],
          ['After Dedup', (s.total_after_deduplication || 0).toLocaleString()],
          ['Duplicates Removed', (s.total_duplicates_removed || 0).toLocaleString()],
          ['Groups Created', (s.total_faqs_generated || 0).toLocaleString()],
          ['Avg Group Size', s.average_cluster_size ?? '—'],
          ['Clustered Qs', (s.total_clustered_questions || 0).toLocaleString()],
          ['Noise (Unclustered)', (s.total_noise_questions || 0).toLocaleString()],
        ].map(([l, v]) => `<div class="summary-item"><span class="summary-lbl">${l}</span><span class="summary-val">${v}</span></div>`).join('')}
      </div>
    </div>
  `;

  // 2. Bottom Left: Raw Table
  renderAnalyticsRawTable();

  // 3. Bottom Right: FAQ - Mining (same card style as FAQ Library)
  const groupsList = document.getElementById('analyticsGroupsList');
  if (!state.faqs.length) {
    groupsList.innerHTML = '<div class="empty-state" style="padding:20px 0"><p>No groups available. Run analysis first.</p></div>';
  } else {
    groupsList.innerHTML = state.faqs.map((g, gi) => {
      const groupName = g.group_name || 'Other';
      const innerFaqs = g.faqs || [];
      const totalCount = g.total_faqs || innerFaqs.length || 0;

      const faqsHtml = innerFaqs.map((faq, j) => `
        <div class="inner-faq-card" onclick="event.stopPropagation(); this.classList.toggle('expanded')">
          <div class="inner-faq-header">
            <div class="inner-faq-q-icon">Q:</div>
            <div class="inner-faq-question">${escHtml(faq.question || '')}</div>
            <div class="inner-faq-expand-icon">▶</div>
          </div>
          <div class="inner-faq-answer">
            <div style="display:flex;align-items:flex-start;gap:8px;">
              <div style="color:var(--success);font-weight:700;font-size:12px;margin-top:1px;">A:</div>
              <div style="flex:1;">${escHtml(faq.answer || '')}</div>
            </div>
          </div>
        </div>
      `).join('');

      return `
      <div class="faq-group-card" style="margin-bottom:12px;padding:16px;">
        <div class="faq-group-header" style="margin-bottom:12px;">
          <div class="faq-group-rank" style="min-width:28px;height:28px;font-size:11px;">#${gi+1}</div>
          <div class="faq-group-info">
            <div class="faq-group-title" style="font-size:14px;">${escHtml(groupName)}</div>
            <div class="faq-badges">
              <span class="badge badge-cluster">📁 Category</span>
              <span class="badge badge-support">🗣️ ${g.support_count || totalCount} mentions</span>
              <span class="badge badge-count">📝 ${totalCount} FAQs</span>
            </div>
          </div>
        </div>
        <div class="faq-group-body" style="margin-left:6px;">
          ${faqsHtml || '<div style="font-size:12px;color:var(--text-muted);padding:8px;">No FAQs</div>'}
        </div>
      </div>`;
    }).join('');
  }
}

async function renderAnalyticsRawTable() {
  const thead = document.querySelector('#analyticsRawTable thead');
  const tbody = document.getElementById('analyticsRawBody');
  if (!tbody) return;
  if (thead) thead.innerHTML = `<tr><th style="width:45%">Question</th><th>Answer</th></tr>`;
  try {
    let displayData = [];
    if (!state.manipulateData) {
      try {
        const res = await apiFetch('/uploaded-data');
        state.manipulateData = res.data || [];
      } catch (_) {
        state.manipulateData = [];
      }
    }
    if (state.manipulateData && state.manipulateData.length) {
      displayData = state.manipulateData.map(row => ({
        question: row.customer_message || row.question || '',
        answer: row.admin_reply || row.answer || ''
      }));
    } else if (state.faqs && state.faqs.length) {
      state.faqs.forEach(g => {
        (g.faqs || []).forEach(f => {
          displayData.push({ question: f.question || '', answer: f.answer || '' });
        });
      });
    }
    if (!displayData.length) {
      tbody.innerHTML = '<tr><td colspan="2" style="text-align:center;color:var(--text-muted);padding:20px;">No data available. Upload a file or run analysis first.</td></tr>';
      return;
    }
    tbody.innerHTML = displayData.slice(0, 200).map(row => {
      const q = String(row.question).substring(0, 150);
      const a = String(row.answer).substring(0, 150);
      return `<tr><td><div style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(q)}">${escHtml(q)}</div></td><td><div style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(a)}">${escHtml(a)}</div></td></tr>`;
    }).join('');
  } catch (_) {
    tbody.innerHTML = '<tr><td colspan="2" style="text-align:center;color:var(--text-muted);padding:20px;">Error loading data.</td></tr>';
  }
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
function renderDMTable() {
  const body  = document.getElementById('dmBody');
  const empty = document.getElementById('dmEmpty');
  const label = document.getElementById('dmCountLabel');
  const thead = document.querySelector('#dmTable thead');
  if (!thead) return;

  state.dmSelected.clear();
  const cbAllEl = document.getElementById('cbAll');
  if (cbAllEl) cbAllEl.checked = false;

  thead.innerHTML = `<tr>
    <th style="width:40px"><input type="checkbox" class="dm-cb" id="cbAll" onchange="onCbAllChange(this)"></th>
    <th>No.</th>
    <th>Question</th>
    <th>Answer (Preview)</th>
    <th>Groups</th>
    <th>Mentions</th>
  </tr>`;

  if (!state.faqs.length) {
    body.innerHTML = '';
    empty.style.display = 'block';
    if (label) label.textContent = '';
    populateRelabelDropdown();
    updateDeleteBtn();
    return;
  }
  empty.style.display = 'none';
  if (label) label.textContent = `${state.faqs.length} groups`;

  body.innerHTML = state.faqs.map((faq,i)=>`
    <tr id="dm-row-${i}" draggable="true" ondragstart="window.onDragFAQ(event, ${i})" onclick="toggleDMRow(event,${i})" style="cursor: move;">
      <td><input type="checkbox" class="dm-cb" id="dm-cb-${i}" onchange="onRowCbChange(${i})" onclick="event.stopPropagation()"></td>
      <td style="color:var(--text-muted);font-size:12px;">${i+1}</td>
      <td><div class="dm-question" title="${escHtml(faq.canonical_question || faq.faq_question)}">${escHtml(faq.canonical_question || faq.faq_question)}</div></td>
      <td><div class="dm-answer" title="${escHtml(faq.canonical_answer || faq.faq_answer)}">${escHtml(faq.canonical_answer || faq.faq_answer)}</div></td>
      <td><span class="badge badge-cluster">${escHtml(faq.group_name || 'Other')}</span></td>
      <td>
        <span class="badge badge-support" style="cursor:help;" title="${faq.support_count} related queries">${faq.support_count}</span>
        <button class="btn btn-ghost" style="padding: 2px 8px; font-size: 10px; margin-left:6px;" onclick="event.stopPropagation(); window.openEditModal(${i})">✏️</button>
        <button class="btn btn-ghost" style="padding: 2px 8px; font-size: 10px;" onclick="event.stopPropagation(); window.openMergeModal(${i})">🔗 Merge</button>
      </td>
    </tr>`).join('');
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
  const cbAll = document.getElementById('cbAll');
  if (cbAll) cbAll.checked = (state.faqs.length > 0 && state.dmSelected.size === state.faqs.length);
}

function onCbAllChange(cbAll) {
  const checked = !!cbAll && cbAll.checked;
  state.faqs.forEach((_, i) => {
    const cb = document.getElementById(`dm-cb-${i}`);
    const row = document.getElementById(`dm-row-${i}`);
    if (!cb || !row) return;
    cb.checked = checked;
    if (checked) { state.dmSelected.add(i); row.classList.add('selected'); }
    else { state.dmSelected.delete(i); row.classList.remove('selected'); }
  });
  updateDeleteBtn();
}

function toggleSelectAll() {
  const cbAll = document.getElementById('cbAll');
  if (!cbAll) return;
  cbAll.checked = !cbAll.checked;
  onCbAllChange(cbAll);
}

function updateDeleteBtn() {
  const btn = document.getElementById('deleteSelectedBtn');
  const relabelBtn = document.getElementById('editGroupBtn');
  const n = state.dmSelected.size;
  const isAll = state.faqs.length > 0 && state.dmSelected.size === state.faqs.length;

  const relabelCount = document.getElementById('relabelSelectedCount');
  if (relabelCount) relabelCount.textContent = n;

  if (btn) btn.disabled = (n === 0);
  if (relabelBtn) relabelBtn.disabled = (n === 0);
  const selectAllBtn = document.getElementById('selectAllBtn');
  if (selectAllBtn) selectAllBtn.innerHTML = isAll ? '☐ Select All' : '☑ Select All';
}

async function deleteSelected() {
  if (state.dmSelected.size === 0) return;
  const indices = [...state.dmSelected].sort((a,b)=>b-a);
  if (!confirm(`Delete ${indices.length} group(s)? This cannot be undone.`)) return;

  const deleteBtn = document.getElementById('deleteSelectedBtn');
  deleteBtn.disabled = true;
  deleteBtn.innerHTML = `<div class="mini-spinner" style="border-color:rgba(239,68,68,.2);border-top-color:#ef4444"></div> Deleting…`;

  try {
    const res = await apiFetch('/faqs/delete', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ indices }),
    });

    const deletedSet   = new Set(indices);
    const remainingFAQs = state.faqs.filter((_,i) => !deletedSet.has(i));
    state.faqs         = remainingFAQs;
    state.dmSelected.clear();

    const survivingClusterIds = new Set(remainingFAQs.map(f => f.cluster_id));
    state.clusters = (state.analytics && state.analytics.cluster_sizes) ? state.analytics.cluster_sizes.filter(c => survivingClusterIds.has(c.cluster_id)) : [];

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
    deleteBtn.innerHTML = '🗑 Delete';
    updateDeleteBtn();
  }
}

// ── Relabel (Edit Group) ──────────────────────────────────────────────────────
async function relabelSelected(newClusterId) {
  toggleEditDropdown();
  if (state.dmSelected.size === 0) return;
  const indices = [...state.dmSelected];
  if (isNaN(newClusterId) || indices.length === 0) return;
  const targetName = (state.clusters.find(c => c.cluster_id === newClusterId) || {}).group_name || newClusterId;
  if (!confirm(`Move ${indices.length} group(s) to "${targetName}"?`)) return;

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
    state.dmSelected.clear();

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
  
    const clusters = state.clusters.length ? state.clusters : state.faqs.map((f, i) => ({ cluster_id: f.cluster_id ?? i, group_name: f.group_name, size: f.total_faqs, support_count: f.support_count }));
    let html = '';
    if (!clusters.length) {
      html = `<div style="padding:10px 14px;font-size:12px;color:var(--text-muted);text-align:center;">No other groups</div>`;
    } else {
      clusters.forEach(c => {
        const cid = c.cluster_id ?? c.group_id;
        const name = c.group_name || c.faq_question || 'Other';
        html += `<div style="padding:8px 14px;font-size:12px;color:var(--text-secondary);cursor:pointer;transition:background 0.2s;" 
                 onmouseenter="this.style.background='var(--bg-glass-hover)'; showRelabelPreviewForGroup(${cid})" 
                 onmouseleave="this.style.background='transparent'; hideRelabelPreview()"
                 onclick="relabelSelected(${cid})">
                 📁 ${escHtml(name)} <span style="float:right;color:var(--text-muted);font-size:10px;">${c.size||c.support_count||0}</span>
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
  if (!preview || !list || !title) return;
  const grp = state.faqs.find(f => (f.cluster_id ?? f.group_id) === clusterId);
  if (!grp) {
    title.textContent = 'Destination group';
    list.innerHTML = '<li>Selected group</li>';
  } else {
    title.textContent = grp.group_name || 'Other';
    const samples = (grp.faqs || []).slice(0, 3);
    list.innerHTML = samples.map(f => `<li>${escHtml((f.question || '').substring(0, 50))}…</li>`).join('') || '<li>—</li>';
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
  for(let i=1;i<=7;i++){
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
      body: JSON.stringify({
          input_file: state.uploadedPath || "",
      }),
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
      const total = d.total_stages || 7;
      document.getElementById('apiStatus').textContent = `Analyzing… Step ${d.stage}/${total}`;
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
  el.innerHTML = `<div class="search-cards-grid">${items.map((faq, i) => {
    const simPct = faq.similarity_score != null ? (faq.similarity_score * 100).toFixed(1) : null;
    return `
    <div class="search-result-card" onclick="this.classList.toggle('expanded')">
      <div class="src-header">
        <div class="src-rank">#${i+1}</div>
        <div class="src-content">
          <div class="src-question">${escHtml(faq.faq_question)}</div>
          <div class="src-meta">
            <span class="badge badge-cluster">📁 ${escHtml(faq.group_name || 'Unnamed')}</span>
            <span class="badge badge-support">🗣️ ${faq.support_count || 1} mentions</span>
            ${simPct != null ? `<span class="badge badge-score">⚡ ${simPct}%</span>` : ''}
          </div>
        </div>
        <div class="src-expand-icon">▶</div>
      </div>
      <div class="src-answer">
        <div style="display:flex;align-items:flex-start;gap:8px;">
          <div style="color:var(--success);font-weight:700;font-size:12px;margin-top:1px;">A:</div>
          <div style="flex:1;line-height:1.6;">${escHtml(faq.faq_answer || faq.suggested_admin_reply || '')}</div>
        </div>
      </div>
    </div>`;
  }).join('')}</div>`;
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
      empty.innerHTML = 'No uploaded data found.';
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
  } catch (err) {
    empty.innerHTML = 'No uploaded data found.';
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

// ── Export ──────────────────────────────────────────────────────────────────
window.exportData = function(format) {
  if (!state.faqs || state.faqs.length === 0) {
    showToast('No FAQs to export', 'warning');
    return;
  }
  window.open(`${API}/export?fmt=${format}`, '_blank');
};

// ── Edit FAQ ────────────────────────────────────────────────────────────────
let editingFaqIndex = -1;
window.openEditModal = function(index) {
  editingFaqIndex = index;
  const faq = state.faqs[index];
  document.getElementById('editQuestionInput').value = faq.canonical_question || faq.faq_question;
  document.getElementById('editAnswerInput').value = faq.canonical_answer || faq.faq_answer;
  document.getElementById('editModal').classList.add('visible');
};
window.closeEditModal = function() {
  document.getElementById('editModal').classList.remove('visible');
  editingFaqIndex = -1;
};
window.saveFAQEdit = async function() {
  if (editingFaqIndex < 0) return;
  const btn = document.getElementById('saveEditBtn');
  btn.disabled = true; btn.textContent = 'Saving...';
  
  const q = document.getElementById('editQuestionInput').value;
  const a = document.getElementById('editAnswerInput').value;
  
  try {
    await apiFetch('/faqs/edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: editingFaqIndex, question: q, answer: a })
    });
    
    // Update local state
    state.faqs[editingFaqIndex].canonical_question = q;
    state.faqs[editingFaqIndex].faq_question = q;
    state.faqs[editingFaqIndex].canonical_answer = a;
    state.faqs[editingFaqIndex].faq_answer = a;
    state.faqs[editingFaqIndex].suggested_admin_reply = a;
    
    // Refresh UI
    renderFAQs();
    if(document.getElementById('page-manage').classList.contains('active')) renderDMTable();
    
    showToast('FAQ updated successfully', 'success');
    closeEditModal();
  } catch(err) {
    showToast(`Failed to update FAQ: ${err.message}`, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Save Changes';
  }
};

// ── Merge Groups ────────────────────────────────────────────────────────────
let mergeSourceIndex = -1;
let mergeSourceId = -1;
window.openMergeModal = function(groupIndex) {
  mergeSourceIndex = groupIndex;
  const grp = state.faqs[groupIndex];
  if (!grp) return;
  mergeSourceId = grp.cluster_id ?? grp.group_id ?? groupIndex;
  const nameEl = document.getElementById('mergeSourceGroupName');
  if (nameEl) nameEl.textContent = grp.group_name || 'Other';
  const sel = document.getElementById('mergeTargetSelect');
  if (sel) {
    sel.innerHTML = state.faqs
      .filter((_, j) => j !== groupIndex)
      .map(g => `<option value="${g.cluster_id ?? g.group_id ?? 0}">${escHtml(g.group_name || 'Other')}</option>`)
      .join('') || '<option value="">No other groups</option>';
  }
  document.getElementById('mergeModal').classList.add('visible');
};
window.closeMergeModal = function() {
  document.getElementById('mergeModal').classList.remove('visible');
  mergeSourceIndex = -1;
  mergeSourceId = -1;
};
window.saveMergeGroups = async function() {
  const sel = document.getElementById('mergeTargetSelect');
  const targetId = sel ? parseInt(sel.value, 10) : NaN;
  if (isNaN(targetId) || targetId === mergeSourceId) {
    showToast('Please select a target group.', 'error');
    return;
  }
  const btn = document.getElementById('saveMergeBtn');
  btn.disabled = true; btn.textContent = 'Merging…';
  try {
    const res = await apiFetch('/faqs/merge-groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_group_id: mergeSourceId, target_group_id: targetId })
    });
    state.faqs.forEach(f => {
      if (f.cluster_id === mergeSourceId) f.cluster_id = targetId;
      if (f.group_id === mergeSourceId) f.group_id = targetId;
    });
    if (state.analytics && state.analytics.cluster_sizes) {
      const srcC = state.analytics.cluster_sizes.find(c => c.cluster_id === mergeSourceId);
      const tgtC = state.analytics.cluster_sizes.find(c => c.cluster_id === targetId);
      if (tgtC && srcC) {
        tgtC.size = (tgtC.size || 0) + (srcC.size || 0);
        state.analytics.cluster_sizes = state.analytics.cluster_sizes.filter(c => c.cluster_id !== mergeSourceId);
      }
    }
    updateStats();
    renderFAQs();
    populateClusterFilter();
    renderAnalytics();
    renderDMTable();
    showToast(`Merged ${res.merged_count} FAQs into the target group.`, 'success');
    closeMergeModal();
  } catch (err) {
    showToast('Merge failed.', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Merge Groups';
  }
};

// ── Drag & Drop Reassignment ────────────────────────────────────────────────
let draggedFaqIndex = -1;
window.onDragFAQ = function(e, index) {
  draggedFaqIndex = index;
  e.dataTransfer.effectAllowed = 'move';
};
window.onDropFAQ = async function(e, targetGroupId) {
  e.preventDefault();
  if (draggedFaqIndex < 0) return;
  const sourceIndex = draggedFaqIndex;
  draggedFaqIndex = -1;
  const faq = state.faqs[sourceIndex];
  if(faq.cluster_id === targetGroupId) return;
  
  try {
    await apiFetch('/faqs/relabel', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ indices: [sourceIndex], new_cluster_id: targetGroupId }),
    });
    
    faq.cluster_id = targetGroupId;
    renderDMTable();
    showToast(`Moved FAQ to Group ${targetGroupId}`, 'success');
  } catch(err) {
    showToast(`Failed to move: ${err.message}`, 'error');
  }
};
