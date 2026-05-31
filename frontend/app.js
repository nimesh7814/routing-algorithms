/* ═══════════════════════════════════════════════════════════════
   Pathfinder — app.js
   Polls /api/status while graph builds, then unlocks the UI.
   ═══════════════════════════════════════════════════════════════ */
'use strict';

const API_BASE = window.BACKEND_API_URL || '';

// ── Algorithm metadata ────────────────────────────────────────────────────────
const ALGO_META = {
  dijkstra:      { label: 'Dijkstra',              desc: 'Explores all directions uniformly by expanding the closest unsettled node. Optimal but explores the most nodes.' },
  bidirectional: { label: 'Bidirectional Dijkstra', desc: 'Two simultaneous searches — forward from source (blue) and backward from target (orange) — meeting in the middle.' },
  astar:         { label: 'A* (Euclidean)',          desc: 'Guides the search toward the target using straight-line distance as a heuristic. Explores fewer nodes than Dijkstra.' },
  highway:       { label: 'Highway Hierarchies',     desc: 'Restricts the search to "important" highway edges once outside the local neighbourhood. Fastest for long routes.' },
};

// ── State ─────────────────────────────────────────────────────────────────────
let map, selectedAlgo = 'dijkstra';
let pins = { src: null, dst: null };
let animFrame = null, clickState = 0;
const NODES_PER_FRAME = 20;
let graphReady = false;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const overlay          = document.getElementById('loading-overlay');
const loadingBar       = document.getElementById('loading-bar-fill');
const loadingPct       = document.getElementById('loading-pct');
const loadingStatusTxt = document.getElementById('loading-status-text');
const loadingSub       = document.getElementById('loading-sub');
const mapBanner        = document.getElementById('map-status-banner');
const mapBannerTxt     = document.getElementById('map-status-text');
const mapBannerPct     = document.getElementById('map-status-pct');
const runBtn           = document.getElementById('run-btn');
const runLabel         = document.getElementById('run-label');
const runSpinner       = document.getElementById('run-spinner');
const statsSection     = document.getElementById('stats-section');
const progressWrap     = document.getElementById('progress-wrap');
const progressFill     = document.getElementById('progress-bar-fill');
const progressLbl      = document.getElementById('progress-label');
const algoDesc         = document.getElementById('algo-desc');
const graphMeta        = document.getElementById('graph-meta');
const toast            = document.getElementById('toast');

// ── Graph status polling ──────────────────────────────────────────────────────
let _pollTimer = null;
let _retryCount = 0;
const MAX_RETRIES = 120; // 10 min at 5s intervals

async function pollStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const s = await res.json();
    _retryCount = 0;
    onStatusUpdate(s);
  } catch (err) {
    _retryCount++;
    if (_retryCount < MAX_RETRIES) {
      setOverlayText(`Waiting for backend… (${err.message})`, 0);
    } else {
      setOverlayText('Cannot reach backend. Check Docker is running.', 0);
      loadingSub.textContent = `${API_BASE}/api/status — connection refused`;
    }
    _pollTimer = setTimeout(pollStatus, 5000);
  }
}

function onStatusUpdate(s) {
  const pct = Math.max(0, Math.min(100, s.progress));

  if (s.status === 'ready') {
    // ── Graph is ready — dismiss overlay, show map ────────────────────────
    graphReady = true;

    // Animate bar to 100% then fade overlay
    setOverlayText('Ready! Loading map…', 100);
    setTimeout(() => {
      overlay.classList.add('fade-out');
      setTimeout(() => overlay.style.display = 'none', 600);
    }, 500);

    mapBanner.classList.add('hidden');
    updateRunBtn();

    const edges = s.total_edges.toLocaleString();
    const nodes = s.total_nodes.toLocaleString();
    graphMeta.innerHTML = `${nodes} nodes &nbsp;·&nbsp; ${edges} edges`;

    if (_pollTimer) clearTimeout(_pollTimer);
    return;
  }

  if (s.status === 'error') {
    setOverlayText(`Error: ${s.message}`, 0);
    loadingSub.textContent = 'Check backend logs for details.';
    if (_pollTimer) clearTimeout(_pollTimer);
    return;
  }

  // ── Still building/loading ────────────────────────────────────────────────
  setOverlayText(s.message, pct);

  // Also update the in-map banner (visible after overlay dismissed, e.g. reload)
  mapBanner.classList.remove('hidden');
  mapBannerTxt.textContent = s.message;
  mapBannerPct.textContent = `${Math.round(pct)}%`;

  const isFirstBuild = s.status === 'building';
  loadingSub.style.display = isFirstBuild ? '' : 'none';

  _pollTimer = setTimeout(pollStatus, 2000);
}

function setOverlayText(msg, pct) {
  loadingStatusTxt.textContent = msg;
  loadingBar.style.width = pct + '%';
  loadingPct.textContent = Math.round(pct) + '%';
}

// ── Init map (starts immediately so tiles load in background) ─────────────────
function initMap() {
  map = new maplibregl.Map({
    container: 'map',
    style: {
      version: 8,
      sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors', maxzoom: 19 } },
      layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
    },
    center: [79.86, 6.91],
    zoom: 13,
  });

  map.addControl(new maplibregl.NavigationControl(), 'bottom-right');
  map.on('load', addMapSources);
  map.on('click', onMapClick);
  map.getCanvas().style.cursor = 'crosshair';
}

function addMapSources() {
  const addLine = (id, color) => {
    map.addSource(id, { type: 'geojson', data: emptyFC() });
    map.addLayer({ id: id + '-lines', type: 'line', source: id, paint: { 'line-color': color, 'line-width': 1.2, 'line-opacity': 0.5 } });
  };
  const addCircle = (id, color) => {
    map.addSource(id, { type: 'geojson', data: emptyFC() });
    map.addLayer({ id: id + '-circles', type: 'circle', source: id, paint: { 'circle-radius': 2.5, 'circle-color': color, 'circle-opacity': 0.55 } });
  };
  addLine('explored-fwd', '#0071e3');
  addLine('explored-bwd', '#ff6b35');
  addCircle('explored-nodes-fwd', '#0071e3');
  addCircle('explored-nodes-bwd', '#ff6b35');
  map.addSource('path', { type: 'geojson', data: emptyFC() });
  map.addLayer({ id: 'path-shadow', type: 'line', source: 'path', paint: { 'line-color': '#000', 'line-width': 6, 'line-opacity': 0.12, 'line-blur': 3 } });
  map.addLayer({ id: 'path-line', type: 'line', source: 'path', paint: { 'line-color': '#34c759', 'line-width': 4, 'line-cap': 'round', 'line-join': 'round' } });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const emptyFC = () => ({ type: 'FeatureCollection', features: [] });
const nodeFeature = (lat, lon) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] } });
const edgeFeature = (a, b) => ({ type: 'Feature', geometry: { type: 'LineString', coordinates: [[a[1], a[0]], [b[1], b[0]]] } });

function clearLayers() {
  ['explored-fwd', 'explored-bwd', 'explored-nodes-fwd', 'explored-nodes-bwd', 'path']
    .forEach(id => map.getSource(id)?.setData(emptyFC()));
}
function setSource(id, feats) {
  map.getSource(id)?.setData({ type: 'FeatureCollection', features: feats });
}

// ── Map click ─────────────────────────────────────────────────────────────────
function onMapClick(e) {
  if (!graphReady) return;
  const { lat, lng } = e.lngLat;
  if (clickState === 0 || clickState === 2) { setCoord('src', lat, lng); clickState = 1; showToast('Source set — click map to set destination'); }
  else { setCoord('dst', lat, lng); clickState = 2; showToast('Destination set'); }
}

function setCoord(which, lat, lon) {
  document.getElementById(`${which}-lat`).value = lat.toFixed(6);
  document.getElementById(`${which}-lon`).value = lon.toFixed(6);
  if (pins[which]) pins[which].remove();
  const el = document.createElement('div');
  el.style.cssText = `width:22px;height:22px;border-radius:50%;background:${which==='src'?'#0071e3':'#ff6b35'};border:3px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.25);display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;color:white;font-family:Inter,sans-serif;cursor:pointer;`;
  el.textContent = which === 'src' ? 'A' : 'B';
  pins[which] = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat([lon, lat]).addTo(map);
  updateRunBtn();
}

['src-lat','src-lon','dst-lat','dst-lon'].forEach(id => {
  document.getElementById(id).addEventListener('input', () => {
    updateRunBtn();
    const p = id.startsWith('src') ? 'src' : 'dst';
    const lat = parseFloat(document.getElementById(`${p}-lat`).value);
    const lon = parseFloat(document.getElementById(`${p}-lon`).value);
    if (!isNaN(lat) && !isNaN(lon)) {
      if (pins[p]) pins[p].remove();
      const el = document.createElement('div');
      el.style.cssText = `width:22px;height:22px;border-radius:50%;background:${p==='src'?'#0071e3':'#ff6b35'};border:3px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.25);display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;color:white;font-family:Inter,sans-serif;`;
      el.textContent = p === 'src' ? 'A' : 'B';
      pins[p] = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat([lon, lat]).addTo(map);
    }
  });
});

function updateRunBtn() {
  const hasAll = ['src-lat','src-lon','dst-lat','dst-lon'].every(id => document.getElementById(id).value.trim() !== '');
  runBtn.disabled = !hasAll || !graphReady;
}

// ── Algorithm selector ────────────────────────────────────────────────────────
document.querySelectorAll('.pill[data-algo]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.pill[data-algo]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    selectedAlgo = btn.dataset.algo;
    algoDesc.textContent = ALGO_META[selectedAlgo].desc;
  });
});
algoDesc.textContent = ALGO_META[selectedAlgo].desc;

// ── Run ───────────────────────────────────────────────────────────────────────
runBtn.addEventListener('click', runRoute);

async function runRoute() {
  const srcLat = parseFloat(document.getElementById('src-lat').value);
  const srcLon = parseFloat(document.getElementById('src-lon').value);
  const dstLat = parseFloat(document.getElementById('dst-lat').value);
  const dstLon = parseFloat(document.getElementById('dst-lon').value);
  if ([srcLat,srcLon,dstLat,dstLon].some(isNaN)) { showToast('Please set both source and destination.'); return; }

  setLoading(true);
  clearLayers();
  statsSection.style.display = 'none';
  if (animFrame) cancelAnimationFrame(animFrame);

  try {
    const res = await fetch(`${API_BASE}/api/route`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_lat: srcLat, source_lon: srcLon, dest_lat: dstLat, dest_lon: dstLon, algorithm: selectedAlgo }),
    });
    if (!res.ok) { const err = await res.json(); throw new Error(err.detail || 'Server error'); }
    const data = await res.json();
    if (!data.success) { showToast(data.error || 'No path found.'); setLoading(false); return; }
    showStats(data);
    setLoading(false);
    animateSteps(data.steps, data.node_coords, data.path_coords);
  } catch (err) {
    showToast('Error: ' + err.message);
    setLoading(false);
  }
}

// ── Animation ─────────────────────────────────────────────────────────────────
function animateSteps(steps, nodeCoords, pathCoords) {
  const fE=[], bE=[], fN=[], bN=[];
  let cursor = 0, total = steps.length;
  progressWrap.classList.add('visible');
  progressFill.style.width = '0%';

  function frame() {
    const end = Math.min(cursor + NODES_PER_FRAME, total);
    for (let i = cursor; i < end; i++) {
      const s = steps[i], dir = s.direction || 'fwd';
      if (s.type === 'node') {
        const c = nodeCoords[String(s.id)];
        if (c) (dir==='fwd'?fN:bN).push(nodeFeature(c[0],c[1]));
      } else {
        const fc = nodeCoords[String(s.from)], tc = nodeCoords[String(s.to)];
        if (fc && tc) (dir==='fwd'?fE:bE).push(edgeFeature(fc,tc));
      }
    }
    cursor = end;
    setSource('explored-fwd', fE); setSource('explored-bwd', bE);
    setSource('explored-nodes-fwd', fN); setSource('explored-nodes-bwd', bN);
    const pct = Math.round((cursor/total)*100);
    progressFill.style.width = pct + '%';
    progressLbl.textContent = `Animating… ${pct}%`;
    if (cursor < total) animFrame = requestAnimationFrame(frame);
    else { progressLbl.textContent = 'Path found ✓'; drawPath(pathCoords); }
  }
  animFrame = requestAnimationFrame(frame);
}

function drawPath(pathCoords) {
  if (!pathCoords || pathCoords.length < 2) return;
  const coords = pathCoords.map(([lat,lon]) => [lon,lat]);
  setSource('path', [{ type:'Feature', geometry:{ type:'LineString', coordinates:coords } }]);
  const lons = coords.map(c=>c[0]), lats = coords.map(c=>c[1]);
  map.fitBounds([[Math.min(...lons),Math.min(...lats)],[Math.max(...lons),Math.max(...lats)]], { padding:60, duration:800 });
}

// ── Stats ─────────────────────────────────────────────────────────────────────
function showStats(data) {
  statsSection.style.display = 'block';
  document.getElementById('stat-dist').textContent = data.distance_km>=1 ? `${data.distance_km.toFixed(2)} km` : `${data.distance_m.toFixed(0)} m`;
  document.getElementById('stat-time').textContent = data.time_ms>=1000 ? `${(data.time_ms/1000).toFixed(2)}s` : `${data.time_ms.toFixed(1)}ms`;
  document.getElementById('stat-expanded').textContent = data.nodes_expanded.toLocaleString();
  document.getElementById('stat-path-nodes').textContent = data.nodes_in_path.toLocaleString();
  document.getElementById('stat-pct').textContent = data.total_nodes ? `${((data.nodes_expanded/data.total_nodes)*100).toFixed(1)}%` : '—';
  document.getElementById('stat-algo').textContent = ALGO_META[data.algorithm]?.label || data.algorithm;
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function setLoading(on) {
  runBtn.disabled = on;
  runLabel.style.display = on ? 'none' : 'inline';
  runSpinner.style.display = on ? 'inline' : 'none';
}

let toastTimer;
function showToast(msg, duration=3500) {
  toast.textContent = msg; toast.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add('hidden'), duration);
}

// ── Boot ──────────────────────────────────────────────────────────────────────
initMap();
pollStatus();
