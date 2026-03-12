/**
 * viz.js — 3D FAQ Cluster Visualization
 * Requires Three.js r128 + OrbitControls (loaded before this file via CDN).
 */

const VIZ_PALETTE = [
  '#6366f1','#06b6d4','#10b981','#f59e0b','#a855f7',
  '#ec4899','#84cc16','#f97316','#38bdf8','#c084fc',
  '#34d399','#fb923c','#22d3ee','#e879f9','#4ade80',
  '#60a5fa','#facc15','#2dd4bf','#f472b6','#a78bfa',
];

// ── Shared viz state ──────────────────────────────────────────────────────────
const V = {
  scene: null, camera: null, renderer: null, controls: null,
  pointsMesh: null,
  data: [],           // flat array of {x,y,z,cluster_id,faq_question,...}
  clustersMap: {},    // { "0": [pt,...], "1": [...] }
  selectedGroup: null,
  raycaster: new THREE.Raycaster(),
  mouse: new THREE.Vector2(),
  hoveredIdx: -1,
  rafId: null,
  canvasEl: null,
};
V.raycaster.params.Points = { threshold: 0.3 };

// ── Helpers ───────────────────────────────────────────────────────────────────
function vizColor(clusterId) {
  return new THREE.Color(VIZ_PALETTE[Math.abs(Number(clusterId)) % VIZ_PALETTE.length]);
}

function hexToStr(clusterId) {
  return VIZ_PALETTE[Math.abs(Number(clusterId)) % VIZ_PALETTE.length];
}

// ── Entry point (called from app.js switchPage) ───────────────────────────────
async function initVisualization() {
  const page = document.getElementById('page-viz');
  if (!page || !page.classList.contains('active')) return;

  const wrapEl   = document.getElementById('vizCanvas');
  const spinEl   = document.getElementById('vizSpinner');
  const emptyEl  = document.getElementById('vizEmpty');

  spinEl.style.display  = 'flex';
  emptyEl.style.display = 'none';
  cleanupViz();  // Tear down previous instance

  // Fetch 3D projection from backend
  let raw;
  try {
    raw = await apiFetch('/visualization-data');
  } catch(e) {
    spinEl.style.display = 'none';
    emptyEl.style.display = 'block';
    emptyEl.textContent = `Cannot load visualization data: ${e.message}`;
    return;
  }

  V.data = raw.points || [];
  if (V.data.length < 2) {
    spinEl.style.display = 'none';
    emptyEl.style.display = 'block';
    emptyEl.textContent = 'Not enough FAQ data to visualize. Run analysis first.';
    return;
  }

  // Build cluster map
  V.clustersMap = {};
  V.data.forEach(pt => {
    const k = String(pt.cluster_id);
    if (!V.clustersMap[k]) V.clustersMap[k] = [];
    V.clustersMap[k].push(pt);
  });

  spinEl.style.display = 'none';
  setupVizScene(wrapEl);
  buildPointCloud();
  buildVizGroupPanel();
  renderVizFAQDetail(null);
  startVizLoop();
}

// ── Scene setup ───────────────────────────────────────────────────────────────
function setupVizScene(container) {
  const w = container.clientWidth  || 600;
  const h = container.clientHeight || 500;

  // Scene
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#070c18');
  scene.fog = new THREE.FogExp2('#070c18', 0.028);
  V.scene = scene;

  // Camera
  const camera = new THREE.PerspectiveCamera(55, w / h, 0.01, 500);
  camera.position.set(5, 4, 7);
  V.camera = camera;

  // Renderer — insert canvas BEFORE other overlay elements so tooltip stays intact
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(w, h);
  // Remove any old Three.js canvas only (don't wipe tooltip/spinner/hints)
  container.querySelectorAll('canvas').forEach(c => c.remove());
  container.insertBefore(renderer.domElement, container.firstChild);
  renderer.domElement.style.position = 'absolute';
  renderer.domElement.style.inset = '0';
  V.renderer = renderer;
  V.canvasEl = renderer.domElement;

  // OrbitControls
  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping  = true;
  controls.dampingFactor  = 0.07;
  controls.rotateSpeed    = 0.6;
  controls.zoomSpeed      = 0.9;
  controls.minDistance    = 1;
  controls.maxDistance    = 50;
  V.controls = controls;

  // Grid plane
  const gridMat = new THREE.LineBasicMaterial({ color: 0x111827, transparent: true, opacity: 0.6 });
  const gVerts  = [];
  for (let i = -6; i <= 6; i++) {
    gVerts.push(-6,-3,i, 6,-3,i);
    gVerts.push(i,-3,-6, i,-3,6);
  }
  const gridGeo = new THREE.BufferGeometry();
  gridGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(gVerts), 3));
  scene.add(new THREE.LineSegments(gridGeo, gridMat));

  // Subtle ambient light sphere at origin
  const origin = new THREE.Mesh(
    new THREE.SphereGeometry(0.05),
    new THREE.MeshBasicMaterial({ color: 0x4a5568 })
  );
  scene.add(origin);

  // Listeners
  renderer.domElement.addEventListener('mousemove', onVizMouseMove);
  window.addEventListener('resize', onVizResize);
}

// ── Point cloud ───────────────────────────────────────────────────────────────
function buildPointCloud() {
  const n = V.data.length;
  const pos = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);

  // Normalize coordinates to [-3, 3] range
  const xs = V.data.map(p => p.x), ys = V.data.map(p => p.y), zs = V.data.map(p => p.z);
  const [minX, maxX] = [Math.min(...xs), Math.max(...xs)];
  const [minY, maxY] = [Math.min(...ys), Math.max(...ys)];
  const [minZ, maxZ] = [Math.min(...zs), Math.max(...zs)];
  const rX = (maxX - minX) || 1, rY = (maxY - minY) || 1, rZ = (maxZ - minZ) || 1;
  const SPREAD = 7;

  V.data.forEach((pt, i) => {
    const sx = ((pt.x - minX) / rX - 0.5) * SPREAD;
    const sy = ((pt.y - minY) / rY - 0.5) * SPREAD;
    const sz = ((pt.z - minZ) / rZ - 0.5) * SPREAD;
    pos[i*3] = sx; pos[i*3+1] = sy; pos[i*3+2] = sz;
    pt._sx = sx; pt._sy = sy; pt._sz = sz;   // store for later

    const c = vizColor(pt.cluster_id);
    col[i*3] = c.r; col[i*3+1] = c.g; col[i*3+2] = c.b;
  });

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('color',    new THREE.BufferAttribute(col, 3));
  geo.computeBoundingSphere();

  const mat = new THREE.PointsMaterial({
    size: 0.45,
    vertexColors: true,
    sizeAttenuation: true,
    transparent: true,
    opacity: 0.95,
  });

  if (V.pointsMesh) { V.scene.remove(V.pointsMesh); V.pointsMesh.geometry.dispose(); }
  V.pointsMesh = new THREE.Points(geo, mat);
  V.scene.add(V.pointsMesh);
}

// ── Refresh colors after selection change ─────────────────────────────────────
function refreshVizColors() {
  if (!V.pointsMesh) return;
  const colAttr = V.pointsMesh.geometry.attributes.color;
  const sel     = V.selectedGroup;

  V.data.forEach((pt, i) => {
    let c;
    if (sel === null) {
      c = vizColor(pt.cluster_id);
    } else if (String(pt.cluster_id) === sel) {
      c = new THREE.Color('#ef4444');  // selected = red
    } else {
      c = new THREE.Color('#1e293b');  // unselected = near-background dark
    }
    colAttr.setXYZ(i, c.r, c.g, c.b);
  });
  colAttr.needsUpdate = true;
}

// ── Right panel — group list ──────────────────────────────────────────────────
function buildVizGroupPanel() {
  const listEl = document.getElementById('vizGroupList');
  const ids    = Object.keys(V.clustersMap).sort((a,b) => Number(a)-Number(b));

  listEl.innerHTML = ids.map(id => {
    const color = hexToStr(id);
    const count = V.clustersMap[id].length;
    return `
      <div class="viz-gi" id="vgi-${id}" data-gid="${id}" onclick="selectVizGroup('${id}')">
        <div class="viz-gdot" style="background:${color}; box-shadow:0 0 6px ${color}66"></div>
        <div class="viz-gtext">
          <div class="viz-gname">Group ${id}</div>
          <div class="viz-gcount">${count} FAQ(s)</div>
        </div>
      </div>`;
  }).join('');
}

window.selectVizGroup = function(id) {
  V.selectedGroup = (V.selectedGroup === id) ? null : id;
  document.querySelectorAll('.viz-gi').forEach(el => {
    el.classList.toggle('active', el.dataset.gid === V.selectedGroup);
  });
  refreshVizColors();
  renderVizFAQDetail(V.selectedGroup);
};

// ── Right panel — FAQ detail ──────────────────────────────────────────────────
function renderVizFAQDetail(groupId) {
  const panel = document.getElementById('vizFaqPanel');
  if (!groupId) {
    panel.innerHTML = `<div class="viz-hint">← Click a group to see its FAQs here</div>`;
    return;
  }
  const pts   = V.clustersMap[groupId] || [];
  const color = hexToStr(groupId);
  panel.innerHTML =
    `<div class="viz-panel-label" style="color:${color}">
       Group ${groupId} &nbsp;·&nbsp; ${pts.length} FAQ(s)
     </div>` +
    pts.map(pt => `
      <div class="viz-faq-card">
        <div class="viz-faq-q">${escHtml(pt.faq_question)}</div>
        <div class="viz-faq-a">${escHtml((pt.faq_answer||'').substring(0,160))}${(pt.faq_answer||'').length>160?'…':''}</div>
        <div class="viz-faq-meta">🗣️ ${pt.support_count} mentions</div>
      </div>`).join('');
}

// ── Hover tooltip ─────────────────────────────────────────────────────────────
function onVizMouseMove(e) {
  if (!V.renderer || !V.pointsMesh) return;
  const rect = V.canvasEl.getBoundingClientRect();
  V.mouse.x  =  ((e.clientX - rect.left) / rect.width)  * 2 - 1;
  V.mouse.y  = -((e.clientY - rect.top)  / rect.height) * 2 + 1;

  V.raycaster.setFromCamera(V.mouse, V.camera);
  const hits = V.raycaster.intersectObject(V.pointsMesh);
  const tip  = document.getElementById('vizTooltip');

  if (hits.length > 0) {
    const idx = hits[0].index;
    const pt  = V.data[idx];
    if (pt) {
      V.hoveredIdx = idx;
      const color = hexToStr(pt.cluster_id);
      tip.innerHTML = `
        <div style="font-size:10px;font-weight:700;color:${color};text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px">
          Group ${pt.cluster_id}
        </div>
        <div style="font-size:12px;font-weight:600;line-height:1.4;color:#eef2ff;margin-bottom:5px">
          ${escHtml(pt.faq_question)}
        </div>
        <div style="font-size:11px;color:#8892a4;line-height:1.45">
          ${escHtml((pt.faq_answer||'').substring(0,110))}${(pt.faq_answer||'').length>110?'…':''}
        </div>
        <div style="font-size:10px;color:#06b6d4;margin-top:6px">🗣️ ${pt.support_count} mentions</div>`;
      tip.style.opacity = '1';
      tip.style.left = (e.clientX - rect.left + 16) + 'px';
      tip.style.top  = Math.max(0, e.clientY - rect.top - 14) + 'px';
      V.canvasEl.style.cursor = 'crosshair';
    }
  } else {
    V.hoveredIdx = -1;
    tip.style.opacity = '0';
    V.canvasEl.style.cursor = 'grab';
  }
}

// ── Resize ────────────────────────────────────────────────────────────────────
function onVizResize() {
  const container = document.getElementById('vizCanvas');
  if (!container || !V.renderer) return;
  const w = container.clientWidth;
  const h = container.clientHeight;
  V.camera.aspect = w / h;
  V.camera.updateProjectionMatrix();
  V.renderer.setSize(w, h);
}

// ── Animation loop ────────────────────────────────────────────────────────────
function startVizLoop() {
  function loop() {
    V.rafId = requestAnimationFrame(loop);
    V.controls?.update();
    V.renderer?.render(V.scene, V.camera);
  }
  loop();
}

// ── Cleanup ───────────────────────────────────────────────────────────────────
function cleanupViz() {
  if (V.rafId) { cancelAnimationFrame(V.rafId); V.rafId = null; }
  if (V.canvasEl) V.canvasEl.removeEventListener('mousemove', onVizMouseMove);
  window.removeEventListener('resize', onVizResize);
  if (V.pointsMesh) { V.pointsMesh.geometry.dispose(); V.pointsMesh = null; }
  if (V.renderer)   { V.renderer.dispose(); V.renderer = null; }
  V.scene = null; V.camera = null; V.controls = null;
  V.canvasEl = null; V.selectedGroup = null;
  V.data = []; V.clustersMap = {};
}
