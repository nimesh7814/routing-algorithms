/* ═══════════════════════════════════════════════════════════════
   Pathfinder — frontend app.js
   MapLibre GL + animated search frontier + shortest path overlay
   ═══════════════════════════════════════════════════════════════ */

'use strict';

// ── Algorithm metadata ────────────────────────────────────────────────────────
const ALGO_META = {
  dijkstra: {
    label: 'Dijkstra',
    desc: 'Explores all directions uniformly by expanding the closest unsettled node. Optimal but explores the most nodes.',
  },
  bidirectional: {
    label: 'Bidirectional Dijkstra',
    desc: 'Two simultaneous searches — forward from source (blue) and backward from target (orange) — meeting in the middle.',
  },
  astar: {
    label: 'A* (Euclidean)',
    desc: 'Guides the search toward the target using straight-line distance as a heuristic. Explores fewer nodes than Dijkstra.',
  },
  highway: {
    label: 'Highway Hierarchies',
    desc: 'Restricts the search to "important" highway edges once outside the local neighbourhood. Fastest for long routes.',
  },
};

// ── State ─────────────────────────────────────────────────────────────────────
let map;
let selectedAlgo = 'dijkstra';
let clickMode = null; // 'src' | 'dst' | null
let pins = { src: null, dst: null };
let animFrame = null;
const NODES_PER_FRAME = 20;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const runBtn       = document.getElementById('run-btn');
const runLabel     = document.getElementById('run-label');
const runSpinner   = document.getElementById('run-spinner');
const statsSection = document.getElementById('stats-section');
const progressWrap = document.getElementById('progress-wrap');
const progressFill = document.getElementById('progress-bar-fill');
const progressLbl  = document.getElementById('progress-label');
const algoDesc     = document.getElementById('algo-desc');
const graphMeta    = document.getElementById('graph-meta');
const toast        = document.getElementById('toast');

// ── Init map ──────────────────────────────────────────────────────────────────
function initMap() {
  map = new maplibregl.Map({
    container: 'map',
    style: {
      version: 8,
      sources: {
        osm: {
          type: 'raster',
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
          attribution: '© OpenStreetMap contributors',
          maxzoom: 19,
        },
      },
      layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
    },
    center: [79.86, 6.91],
    zoom: 13,
  });

  map.addControl(new maplibregl.NavigationControl(), 'bottom-right');

  map.on('load', () => {
    addMapSources();
    fetchGraphInfo();
  });

  map.on('click', onMapClick);
  map.getCanvas().style.cursor = 'crosshair';
}

// ── Map sources & layers ──────────────────────────────────────────────────────
function addMapSources() {
  // Explored edges — forward
  map.addSource('explored-fwd', { type: 'geojson', data: emptyFC() });
  map.addLayer({
    id: 'explored-fwd-lines',
    type: 'line',
    source: 'explored-fwd',
    paint: {
      'line-color': '#0071e3',
      'line-width': 1.2,
      'line-opacity': 0.5,
    },
  });

  // Explored edges — backward
  map.addSource('explored-bwd', { type: 'geojson', data: emptyFC() });
  map.addLayer({
    id: 'explored-bwd-lines',
    type: 'line',
    source: 'explored-bwd',
    paint: {
      'line-color': '#ff6b35',
      'line-width': 1.2,
      'line-opacity': 0.5,
    },
  });

  // Explored nodes — forward
  map.addSource('explored-nodes-fwd', { type: 'geojson', data: emptyFC() });
  map.addLayer({
    id: 'explored-nodes-fwd-circles',
    type: 'circle',
    source: 'explored-nodes-fwd',
    paint: {
      'circle-radius': 2.5,
      'circle-color': '#0071e3',
      'circle-opacity': 0.55,
    },
  });

  // Explored nodes — backward
  map.addSource('explored-nodes-bwd', { type: 'geojson', data: emptyFC() });
  map.addLayer({
    id: 'explored-nodes-bwd-circles',
    type: 'circle',
    source: 'explored-nodes-bwd',
    paint: {
      'circle-radius': 2.5,
      'circle-color': '#ff6b35',
      'circle-opacity': 0.55,
    },
  });

  // Shortest path
  map.addSource('path', { type: 'geojson', data: emptyFC() });
  map.addLayer({
    id: 'path-shadow',
    type: 'line',
    source: 'path',
    paint: {
      'line-color': '#000000',
      'line-width': 6,
      'line-opacity': 0.12,
      'line-blur': 3,
    },
  });
  map.addLayer({
    id: 'path-line',
    type: 'line',
    source: 'path',
    paint: {
      'line-color': '#34c759',
      'line-width': 4,
      'line-opacity': 1,
      'line-cap': 'round',
      'line-join': 'round',
    },
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function emptyFC() {
  return { type: 'FeatureCollection', features: [] };
}

function nodeFeature(lat, lon) {
  return { type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] } };
}

function edgeFeature(fromLatLon, toLatLon) {
  return {
    type: 'Feature',
    geometry: {
      type: 'LineString',
      coordinates: [
        [fromLatLon[1], fromLatLon[0]],
        [toLatLon[1], toLatLon[0]],
      ],
    },
  };
}

function clearLayers() {
  ['explored-fwd', 'explored-bwd', 'explored-nodes-fwd', 'explored-nodes-bwd', 'path']
    .forEach(id => map.getSource(id)?.setData(emptyFC()));
}

function setSource(id, features) {
  map.getSource(id)?.setData({ type: 'FeatureCollection', features });
}

// ── Map click — pin placement ─────────────────────────────────────────────────
let clickState = 0; // 0=waiting src, 1=waiting dst, 2=both set

function onMapClick(e) {
  const { lat, lng } = e.lngLat;

  if (clickState === 0 || clickState === 2) {
    // set source
    setCoord('src', lat, lng);
    clickState = 1;
    showToast('Source set — click map to set destination');
  } else {
    // set destination
    setCoord('dst', lat, lng);
    clickState = 2;
    showToast('Destination set');
  }
}

function setCoord(which, lat, lon) {
  document.getElementById(`${which}-lat`).value = lat.toFixed(6);
  document.getElementById(`${which}-lon`).value = lon.toFixed(6);

  if (pins[which]) pins[which].remove();
  const el = document.createElement('div');
  el.className = 'map-pin';
  el.style.cssText = `
    width:22px;height:22px;border-radius:50%;
    background:${which === 'src' ? '#0071e3' : '#ff6b35'};
    border:3px solid white;
    box-shadow:0 2px 8px rgba(0,0,0,0.25);
    display:flex;align-items:center;justify-content:center;
    font-size:9px;font-weight:700;color:white;font-family:Inter,sans-serif;
    cursor:pointer;
  `;
  el.textContent = which === 'src' ? 'A' : 'B';
  pins[which] = new maplibregl.Marker({ element: el, anchor: 'center' })
    .setLngLat([lon, lat])
    .addTo(map);

  updateRunBtn();
}

// ── Input change listeners ────────────────────────────────────────────────────
['src-lat', 'src-lon', 'dst-lat', 'dst-lon'].forEach(id => {
  document.getElementById(id).addEventListener('input', () => {
    updateRunBtn();
    const lat = parseFloat(document.getElementById(`${id.slice(0,3)}-lat`).value);
    const lon = parseFloat(document.getElementById(`${id.slice(0,3)}-lon`).value);
    const which = id.startsWith('src') ? 'src' : 'dst';
    if (!isNaN(lat) && !isNaN(lon)) {
      if (pins[which]) pins[which].remove();
      const el = document.createElement('div');
      el.style.cssText = `width:22px;height:22px;border-radius:50%;background:${which === 'src' ? '#0071e3' : '#ff6b35'};border:3px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.25);display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;color:white;font-family:Inter,sans-serif;`;
      el.textContent = which === 'src' ? 'A' : 'B';
      pins[which] = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([lon, lat]).addTo(map);
    }
  });
});

function updateRunBtn() {
  const hasAll = ['src-lat', 'src-lon', 'dst-lat', 'dst-lon']
    .every(id => document.getElementById(id).value.trim() !== '');
  runBtn.disabled = !hasAll;
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

// ── Run button ────────────────────────────────────────────────────────────────
runBtn.addEventListener('click', runRoute);

async function runRoute() {
  const srcLat = parseFloat(document.getElementById('src-lat').value);
  const srcLon = parseFloat(document.getElementById('src-lon').value);
  const dstLat = parseFloat(document.getElementById('dst-lat').value);
  const dstLon = parseFloat(document.getElementById('dst-lon').value);

  if ([srcLat, srcLon, dstLat, dstLon].some(isNaN)) {
    showToast('Please set both source and destination.');
    return;
  }

  // UI: loading state
  setLoading(true);
  clearLayers();
  statsSection.style.display = 'none';
  if (animFrame) cancelAnimationFrame(animFrame);

  try {
    const res = await fetch('/api/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_lat: srcLat, source_lon: srcLon,
        dest_lat: dstLat, dest_lon: dstLon,
        algorithm: selectedAlgo,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Server error');
    }

    const data = await res.json();

    if (!data.success) {
      showToast(data.error || 'No path found.');
      setLoading(false);
      return;
    }

    // Show stats immediately
    showStats(data);
    setLoading(false);

    // Animate exploration, then draw path
    animateSteps(data.steps, data.node_coords, data.path_coords, data);

  } catch (err) {
    showToast('Error: ' + err.message);
    setLoading(false);
  }
}

// ── Animation engine ──────────────────────────────────────────────────────────
function animateSteps(steps, nodeCoords, pathCoords, data) {
  const fwdEdges = [];
  const bwdEdges = [];
  const fwdNodes = [];
  const bwdNodes = [];

  let cursor = 0;
  const total = steps.length;

  progressWrap.classList.add('visible');
  progressFill.style.width = '0%';

  function frame() {
    const end = Math.min(cursor + NODES_PER_FRAME, total);

    for (let i = cursor; i < end; i++) {
      const s = steps[i];
      const dir = s.direction || 'fwd';

      if (s.type === 'node') {
        const coords = nodeCoords[String(s.id)];
        if (coords) {
          const feat = nodeFeature(coords[0], coords[1]);
          dir === 'fwd' ? fwdNodes.push(feat) : bwdNodes.push(feat);
        }
      } else if (s.type === 'edge') {
        const fc = nodeCoords[String(s.from)];
        const tc = nodeCoords[String(s.to)];
        if (fc && tc) {
          const feat = edgeFeature(fc, tc);
          dir === 'fwd' ? fwdEdges.push(feat) : bwdEdges.push(feat);
        }
      }
    }

    cursor = end;

    setSource('explored-fwd', fwdEdges);
    setSource('explored-bwd', bwdEdges);
    setSource('explored-nodes-fwd', fwdNodes);
    setSource('explored-nodes-bwd', bwdNodes);

    const pct = Math.round((cursor / total) * 100);
    progressFill.style.width = pct + '%';
    progressLbl.textContent = `Animating… ${pct}%`;

    if (cursor < total) {
      animFrame = requestAnimationFrame(frame);
    } else {
      // Animation done — draw path
      progressLbl.textContent = 'Path found ✓';
      drawPath(pathCoords);
    }
  }

  animFrame = requestAnimationFrame(frame);
}

function drawPath(pathCoords) {
  if (!pathCoords || pathCoords.length < 2) return;
  const coords = pathCoords.map(([lat, lon]) => [lon, lat]);
  setSource('path', [{
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: coords },
  }]);

  // Fit map to path
  const lons = coords.map(c => c[0]);
  const lats = coords.map(c => c[1]);
  map.fitBounds(
    [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
    { padding: { top: 60, bottom: 60, left: 60, right: 60 }, duration: 800 }
  );
}

// ── Stats display ─────────────────────────────────────────────────────────────
function showStats(data) {
  statsSection.style.display = 'block';

  document.getElementById('stat-dist').textContent =
    data.distance_km >= 1 ? `${data.distance_km.toFixed(2)} km` : `${data.distance_m.toFixed(0)} m`;
  document.getElementById('stat-time').textContent =
    data.time_ms >= 1000 ? `${(data.time_ms/1000).toFixed(2)}s` : `${data.time_ms.toFixed(1)}ms`;
  document.getElementById('stat-expanded').textContent =
    data.nodes_expanded.toLocaleString();
  document.getElementById('stat-path-nodes').textContent =
    data.nodes_in_path.toLocaleString();
  document.getElementById('stat-pct').textContent =
    data.total_nodes ? `${((data.nodes_expanded / data.total_nodes) * 100).toFixed(1)}%` : '—';
  document.getElementById('stat-algo').textContent =
    ALGO_META[data.algorithm]?.label || data.algorithm;
}

// ── Fetch graph info on load ──────────────────────────────────────────────────
async function fetchGraphInfo() {
  try {
    const res = await fetch('/api/info');
    const info = await res.json();
    graphMeta.innerHTML =
      `${info.total_nodes.toLocaleString()} nodes &nbsp;·&nbsp; ${info.total_edges.toLocaleString()} edges`;
  } catch (_) {}
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function setLoading(on) {
  runBtn.disabled = on;
  runLabel.style.display = on ? 'none' : 'inline';
  runSpinner.style.display = on ? 'inline' : 'none';
}

let toastTimer;
function showToast(msg, duration = 3000) {
  toast.textContent = msg;
  toast.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add('hidden'), duration);
}

// ── Boot ──────────────────────────────────────────────────────────────────────
initMap();
