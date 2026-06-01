import { useEffect, useMemo, useRef, useState } from "react";
import type { Algorithm } from "@/lib/pathfinding";
import {
  fetchRoute,
  fetchBBox,
  extractPathCoordsFromGeoJSON,
  extractAnimationEdges,
  type RouteResponse,
} from "@/lib/routingApi";
import type * as LeafletNS from "leaflet";
type LeafletModule = typeof import("leaflet");

type Coord = { lat: number; lng: number };

const DEFAULT_START: Coord = { lat: 37.7749, lng: -122.4194 };
const DEFAULT_GOAL: Coord = { lat: 37.8044, lng: -122.2712 };
const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const API_BASE_KEY = "pathfinder.apiBase";

type Theme = "light" | "dark";

const TILE_URLS: Record<Theme, string> = {
  light: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
  dark: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
};
const TILE_ATTRIB =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

function getCssVar(name: string): string | null {
  if (typeof window === "undefined") return null;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || null;
}

function computeBBox(a: Coord, b: Coord) {
  const padLat = Math.max(Math.abs(a.lat - b.lat) * 0.35, 0.01);
  const padLng = Math.max(Math.abs(a.lng - b.lng) * 0.35, 0.01);
  return {
    minLat: Math.min(a.lat, b.lat) - padLat,
    maxLat: Math.max(a.lat, b.lat) + padLat,
    minLng: Math.min(a.lng, b.lng) - padLng,
    maxLng: Math.max(a.lng, b.lng) + padLng,
  };
}

export function PathfindingVisualizer() {
  const [algorithm, setAlgorithm] = useState<Algorithm>("astar");
  const [startLat, setStartLat] = useState(DEFAULT_START.lat.toString());
  const [startLng, setStartLng] = useState(DEFAULT_START.lng.toString());
  const [goalLat, setGoalLat] = useState(DEFAULT_GOAL.lat.toString());
  const [goalLng, setGoalLng] = useState(DEFAULT_GOAL.lng.toString());
  const [speed, setSpeed] = useState(8); // ms per step
  const apiBase = DEFAULT_API_BASE;
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === "undefined") return "light";
    return document.documentElement.classList.contains("dark") ? "dark" : "light";
  });

  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<{
    nodesExpanded: number;
    edgesExplored: number;
    distanceM: number;
    timeMs: number;
    pathLen: number;
  } | null>(null);
  const [routeGeoJSON, setRouteGeoJSON] = useState<GeoJSON.Feature | null>(null);
  const [routeUpdating, setRouteUpdating] = useState(false);

  const animRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mapElRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletNS.Map | null>(null);
  const tileRef = useRef<LeafletNS.TileLayer | null>(null);
  const startMarkerRef = useRef<LeafletNS.Marker | null>(null);
  const goalMarkerRef = useRef<LeafletNS.Marker | null>(null);
  const exploredLayerRef = useRef<LeafletNS.LayerGroup | null>(null);
  const pathLineRef = useRef<LeafletNS.Polyline | null>(null);
  const LRef = useRef<LeafletModule | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [serviceBBox, setServiceBBox] = useState<{
    minLat: number; maxLat: number; minLng: number; maxLng: number;
  } | null>(null);
  const didInitialFitRef = useRef(false);
  const didFitBothRef = useRef(false);
  const draggingMarkerRef = useRef(false);

  const isValidNum = (s: string) => s.trim() !== "" && Number.isFinite(parseFloat(s));
  const hasStart = isValidNum(startLat) && isValidNum(startLng);
  const hasGoal = isValidNum(goalLat) && isValidNum(goalLng);
  const bothSet = hasStart && hasGoal;

  const startCoord: Coord = { lat: parseFloat(startLat) || 0, lng: parseFloat(startLng) || 0 };
  const goalCoord: Coord = { lat: parseFloat(goalLat) || 0, lng: parseFloat(goalLng) || 0 };
  const bbox = useMemo(
    () =>
      bothSet
        ? computeBBox(startCoord, goalCoord)
        : computeBBox(DEFAULT_START, DEFAULT_GOAL),
    [startLat, startLng, goalLat, goalLng, bothSet],
  );

  // Clear any previous render when inputs change
  useEffect(() => {
    if (draggingMarkerRef.current) return;
    stopAnim();
    clearMapLayers();
    setStats(null);
    setRouteGeoJSON(null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startLat, startLng, goalLat, goalLng, algorithm]);

  function stopAnim() {
    if (animRef.current) {
      window.clearTimeout(animRef.current);
      animRef.current = null;
    }
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setRunning(false);
  }

  function clearMapLayers() {
    if (exploredLayerRef.current) {
      exploredLayerRef.current.clearLayers();
    }
    if (pathLineRef.current && mapRef.current) {
      mapRef.current.removeLayer(pathLineRef.current);
      pathLineRef.current = null;
    }
  }

  async function visualize() {
    stopAnim();
    clearMapLayers();
    setStats(null);
    setRouteGeoJSON(null);
    setError(null);
    setRunning(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let resp: RouteResponse;
    try {
      resp = await fetchRoute(
        apiBase,
        {
          source: startCoord,
          destination: goalCoord,
          algorithm,
          stepsEnabled: true,
        },
        ctrl.signal,
      );
    } catch (e) {
      if ((e as Error)?.name === "AbortError") return;
      setRunning(false);
      setRouteUpdating(false);
      setError((e as Error)?.message ?? "Request failed");
      return;
    }

    const L = LRef.current;
    const map = mapRef.current;
    if (!L || !map) {
      setRunning(false);
      return;
    }
    if (!exploredLayerRef.current) {
      exploredLayerRef.current = L.layerGroup().addTo(map);
    }
    const layer = exploredLayerRef.current;

    // Fit map to the route extent first
    const pathCoords = extractPathCoordsFromGeoJSON(resp.geojson);
    if (!pathCoords.length) {
      setRunning(false);
      setRouteUpdating(false);
      setError("Backend returned no route geometry.");
      return;
    }
    if (pathCoords.length >= 2) {
      const bounds = L.latLngBounds(
        pathCoords.map((p) => [p.lat, p.lng] as [number, number]),
      );
      map.fitBounds(bounds.pad(0.2), { animate: true });
    }

    const pathColor = getCssVar("--path") ?? "#10b981";
    const visitedColor = getCssVar("--visited") ?? "#60a5fa";
    const frontierColor = getCssVar("--frontier") ?? "#f59e0b";

    // Build the per-edge exploration animation along the road network.
    const exploredEdges = extractAnimationEdges(resp.search_steps, resp.node_coords);

    const setFinalStats = () => {
      setStats({
        nodesExpanded: resp.nodes_expanded,
        edgesExplored: resp.edges_explored,
        distanceM: resp.total_distance_m,
        timeMs: resp.time_ms,
        pathLen: resp.path_nodes.length,
      });
      setRouteGeoJSON(
        resp.geojson.type === "Feature"
          ? resp.geojson
          : resp.geojson.type === "FeatureCollection" && resp.geojson.features.length > 0
          ? (resp.geojson.features[0] as GeoJSON.Feature)
          : null,
      );
      setRunning(false);
      setRouteUpdating(false);
    };

    // Build the growing polyline along the road network, then animate it
    // by extending one road segment (edge) per tick.
    const latlngs: [number, number][] = [
      [pathCoords[0].lat, pathCoords[0].lng],
    ];
    // Draw a darker casing under the path for extra contrast on light tiles.
    L.polyline(latlngs as unknown as [number, number][], {
      color: "#0b3b1f",
      weight: 10,
      opacity: 0.55,
      lineCap: "round",
      lineJoin: "round",
    }).addTo(layer);
    pathLineRef.current = L.polyline(latlngs, {
      color: pathColor,
      weight: 7,
      opacity: 1,
      lineCap: "round",
      lineJoin: "round",
    }).addTo(map);
    // Drop a small node marker at the starting graph node.
    L.circleMarker([pathCoords[0].lat, pathCoords[0].lng], {
      radius: 3,
      color: pathColor,
      weight: 0,
      fillOpacity: 0.9,
    }).addTo(layer);

    const stepDelay = Math.max(8, speed * 4);

    const animatePath = () => {
      let idx = 1;
      const tickPath = () => {
        if (idx >= pathCoords.length) {
          setFinalStats();
          return;
        }
        const p = pathCoords[idx];
        latlngs.push([p.lat, p.lng]);
        pathLineRef.current?.setLatLngs(latlngs);
        L.circleMarker([p.lat, p.lng], {
          radius: 2.5,
          color: pathColor,
          weight: 0,
          fillOpacity: 0.85,
        }).addTo(layer);
        idx++;
        animRef.current = window.setTimeout(tickPath, stepDelay) as unknown as number;
      };
      tickPath();
    };

    if (!exploredEdges.length) {
      animatePath();
      return;
    }

    // Animate exploration: draw one road edge per tick along the frontier,
    // then animate the final shortest path on top.
    const exploreDelay = Math.max(1, speed);
    const exploreBatch = Math.max(1, Math.floor(60 / exploreDelay));
    let ei = 0;
    const tickExplore = () => {
      for (let b = 0; b < exploreBatch && ei < exploredEdges.length; b++, ei++) {
        const [a, c] = exploredEdges[ei];
        const isFrontier = ei >= exploredEdges.length - exploreBatch;
        L.polyline(
          [
            [a.lat, a.lng],
            [c.lat, c.lng],
          ],
          {
            color: isFrontier ? frontierColor : visitedColor,
            weight: isFrontier ? 2.5 : 1.8,
            opacity: isFrontier ? 0.95 : 0.55,
            lineCap: "round",
          },
        ).addTo(layer);
      }
      if (ei >= exploredEdges.length) {
        animatePath();
        return;
      }
      animRef.current = window.setTimeout(tickExplore, exploreDelay) as unknown as number;
    };
    tickExplore();
  }

  function clearPoints() {
    stopAnim();
    clearMapLayers();
    setStartLat("");
    setStartLng("");
    setGoalLat("");
    setGoalLng("");
    setStats(null);
    setRouteGeoJSON(null);
    setError(null);
    setRouteUpdating(false);
    didFitBothRef.current = false;
  }

  function clearStart() {
    stopAnim();
    clearMapLayers();
    setStartLat("");
    setStartLng("");
    setStats(null);
    setRouteGeoJSON(null);
    setRouteUpdating(false);
    didFitBothRef.current = false;
  }

  function clearGoal() {
    stopAnim();
    clearMapLayers();
    setGoalLat("");
    setGoalLng("");
    setStats(null);
    setRouteGeoJSON(null);
    setRouteUpdating(false);
    didFitBothRef.current = false;
  }

  function downloadGeoJSON() {
    if (!routeGeoJSON) return;
    const feature: GeoJSON.Feature = {
      ...routeGeoJSON,
      properties: {
        ...routeGeoJSON.properties,
        name: "Shortest path",
        algorithm,
        source: { lat: startCoord.lat, lng: startCoord.lng },
        destination: { lat: goalCoord.lat, lng: goalCoord.lng },
        generatedAt: new Date().toISOString(),
      },
    };
    const blob = new Blob([JSON.stringify(feature, null, 2)], { type: "application/geo+json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `route-${algorithm}-${Date.now()}.geojson`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function setPointFromLatLng(lat: number, lng: number) {
    const latStr = lat.toFixed(6);
    const lngStr = lng.toFixed(6);
    if (!hasStart) {
      setStartLat(latStr);
      setStartLng(lngStr);
    } else if (!hasGoal) {
      setGoalLat(latStr);
      setGoalLng(lngStr);
    } else {
      // both already set — replace start so next click sets goal
      setStartLat(latStr);
      setStartLng(lngStr);
      setGoalLat("");
      setGoalLng("");
    }
  }

  useEffect(() => () => stopAnim(), []);

  // Fetch the backend's available data boundary so the map can frame it
  // on first load and via the "Zoom to area" button.
  useEffect(() => {
    const ctrl = new AbortController();
    fetchBBox(apiBase, ctrl.signal)
      .then((r) => {
        if (r.status === "ok" && r.bbox_wgs84) {
          const b = r.bbox_wgs84;
          setServiceBBox({
            minLat: b.min_lat,
            maxLat: b.max_lat,
            minLng: b.min_lon,
            maxLng: b.max_lon,
          });
        }
      })
      .catch(() => { /* backend offline — silently ignore */ });
    return () => ctrl.abort();
  }, [apiBase]);

  function zoomToServiceArea() {
    const map = mapRef.current;
    const L = LRef.current;
    if (!map || !L || !serviceBBox) return;
    const bounds = L.latLngBounds(
      [serviceBBox.minLat, serviceBBox.minLng],
      [serviceBBox.maxLat, serviceBBox.maxLng],
    );
    map.flyToBounds(bounds.pad(0.6), { duration: 0.7 });
  }

  // On first map load + bbox available, frame the service area.
  useEffect(() => {
    if (!mapReady || !serviceBBox || didInitialFitRef.current) return;
    if (hasStart || hasGoal) {
      didInitialFitRef.current = true;
      return;
    }
    didInitialFitRef.current = true;
    zoomToServiceArea();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, serviceBBox]);

  // Theme: sync with <html class="dark"> and update tile layer
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  // Initialize Leaflet map once (client only)
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!mapElRef.current || mapRef.current) return;
    let cancelled = false;
    import("leaflet").then((mod) => {
      if (cancelled || !mapElRef.current) return;
      const L = (mod.default ?? mod) as LeafletModule;
      LRef.current = L;
      const map = L.map(mapElRef.current, {
        zoomControl: true,
        attributionControl: true,
        dragging: true,
        scrollWheelZoom: true,
        doubleClickZoom: true,
        boxZoom: true,
        keyboard: true,
        touchZoom: true,
        zoomSnap: 0.01,
      });
      tileRef.current = L.tileLayer(TILE_URLS[theme], { attribution: TILE_ATTRIB }).addTo(map);
      exploredLayerRef.current = L.layerGroup().addTo(map);
      mapRef.current = map;
      setMapReady(true);
    });
    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
      tileRef.current = null;
      exploredLayerRef.current = null;
      pathLineRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Swap tile layer when theme changes
  useEffect(() => {
    const map = mapRef.current;
    const L = LRef.current;
    if (!map || !L) return;
    if (tileRef.current) map.removeLayer(tileRef.current);
    tileRef.current = L.tileLayer(TILE_URLS[theme], { attribution: TILE_ATTRIB }).addTo(map);
  }, [theme, mapReady]);

  // Map click → set next missing point
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const handler = (e: LeafletNS.LeafletMouseEvent) => {
      setPointFromLatLng(e.latlng.lat, e.latlng.lng);
    };
    map.on("click", handler);
    return () => {
      map.off("click", handler);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, hasStart, hasGoal]);

  // Fit map to bbox whenever coordinates change (only when no route drawn yet)
  useEffect(() => {
    const map = mapRef.current;
    const L = LRef.current;
    if (!map || !L) return;
    if (pathLineRef.current) return;
    if (!bothSet) return;
    if (draggingMarkerRef.current) return;
    if (didFitBothRef.current) return;
    didFitBothRef.current = true;
    const bounds = L.latLngBounds(
      [bbox.minLat, bbox.minLng],
      [bbox.maxLat, bbox.maxLng],
    );
    map.fitBounds(bounds, { animate: false, padding: [0, 0] });
  }, [bbox.minLat, bbox.maxLat, bbox.minLng, bbox.maxLng, mapReady]);

  // Create / update draggable markers for start & goal
  useEffect(() => {
    const map = mapRef.current;
    const L = LRef.current;
    if (!map || !L) return;

    const makeIcon = (color: string, label: string) =>
      L.divIcon({
        className: "",
        iconSize: [26, 34],
        iconAnchor: [13, 32],
        html: `<div style="position:relative;width:26px;height:34px;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.35));">
          <svg viewBox="0 0 26 34" width="26" height="34" xmlns="http://www.w3.org/2000/svg">
            <path d="M13 0 C5.8 0 0 5.6 0 12.6 C0 22 13 34 13 34 C13 34 26 22 26 12.6 C26 5.6 20.2 0 13 0 Z"
              fill="${color}" stroke="white" stroke-width="2"/>
            <circle cx="13" cy="12.5" r="4.5" fill="white"/>
          </svg>
          <span style="position:absolute;top:6px;left:0;right:0;text-align:center;font-size:9px;font-weight:700;color:${color};">${label}</span>
        </div>`,
      });

    if (hasStart && !startMarkerRef.current) {
      const m = L.marker([startCoord.lat, startCoord.lng], {
        draggable: true,
        icon: makeIcon("#34c759", "A"),
        autoPan: true,
      }).addTo(map);
      m.on("dragstart", () => { draggingMarkerRef.current = true; });
      m.on("dragend", () => {
        const { lat, lng } = m.getLatLng();
        setStartLat(lat.toFixed(6));
        setStartLng(lng.toFixed(6));
        setRouteUpdating(true);
        setTimeout(() => {
          draggingMarkerRef.current = false;
          visualize();
        }, 0);
      });
      startMarkerRef.current = m;
    } else if (!hasStart && startMarkerRef.current) {
      map.removeLayer(startMarkerRef.current);
      startMarkerRef.current = null;
    }
    if (hasGoal && !goalMarkerRef.current) {
      const m = L.marker([goalCoord.lat, goalCoord.lng], {
        draggable: true,
        icon: makeIcon("#ff3b30", "B"),
        autoPan: true,
      }).addTo(map);
      m.on("dragstart", () => { draggingMarkerRef.current = true; });
      m.on("dragend", () => {
        const { lat, lng } = m.getLatLng();
        setGoalLat(lat.toFixed(6));
        setGoalLng(lng.toFixed(6));
        setRouteUpdating(true);
        setTimeout(() => {
          draggingMarkerRef.current = false;
          visualize();
        }, 0);
      });
      goalMarkerRef.current = m;
    } else if (!hasGoal && goalMarkerRef.current) {
      map.removeLayer(goalMarkerRef.current);
      goalMarkerRef.current = null;
    }
  }, [mapReady, hasStart, hasGoal]);

  // Keep marker positions in sync when inputs change externally
  useEffect(() => {
    const L = LRef.current;
    if (!L) return;
    const sm = startMarkerRef.current;
    if (sm) {
      const cur = sm.getLatLng();
      if (cur.lat !== startCoord.lat || cur.lng !== startCoord.lng) {
        sm.setLatLng([startCoord.lat, startCoord.lng]);
      }
    }
    const gm = goalMarkerRef.current;
    if (gm) {
      const cur = gm.getLatLng();
      if (cur.lat !== goalCoord.lat || cur.lng !== goalCoord.lng) {
        gm.setLatLng([goalCoord.lat, goalCoord.lng]);
      }
    }
  }, [startCoord.lat, startCoord.lng, goalCoord.lat, goalCoord.lng, mapReady]);

  return (
    <div className="flex h-screen w-full flex-col bg-background">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border/70 bg-[var(--glass)] px-6 backdrop-blur-2xl">
        <div className="flex items-center gap-3">
          <div className="flex size-7 items-center justify-center rounded-[9px] bg-primary text-primary-foreground shadow-sm">
            <svg viewBox="0 0 24 24" fill="none" className="size-4" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2 4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/></svg>
          </div>
          <h1 className="text-[15px] font-semibold tracking-tight text-foreground">Navigation Algorithms</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={zoomToServiceArea}
            disabled={!serviceBBox}
            className="inline-flex items-center gap-1.5 rounded-full border border-border/80 bg-card px-3 py-1.5 text-[12px] font-medium text-foreground/80 transition hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Zoom to service area"
            title="Zoom to available data area"
          >
            <svg viewBox="0 0 24 24" fill="none" className="size-3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/><path d="M11 8v6M8 11h6"/></svg>
            Zoom to area
          </button>
          <button
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            className="rounded-full border border-border/80 bg-card px-3 py-1.5 text-[12px] font-medium text-foreground/80 transition hover:bg-accent hover:text-foreground"
            aria-label="Toggle theme"
          >
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[340px_1fr]">
        {/* Control panel */}
        <aside className="flex h-full min-h-0 flex-col overflow-y-auto border-b border-border/70 bg-card lg:border-b-0 lg:border-r">
          <div className="flex flex-col gap-6 p-6">
            <section className="flex flex-col gap-3">
              <label className="text-[12px] font-medium text-muted-foreground">
                Algorithm
              </label>
              <div className="grid grid-cols-2 gap-1 rounded-2xl bg-secondary p-1">
                {(["dijkstra", "bidirectional", "astar", "highway"] as Algorithm[]).map((a) => (
                  <button
                    key={a}
                    onClick={() => setAlgorithm(a)}
                    disabled={running}
                    className={`rounded-xl px-3 py-2 text-[13px] font-medium transition ${
                      algorithm === a
                        ? "bg-card text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground/80"
                    }`}
                  >
                    {a === "astar"
                      ? "A*"
                      : a === "dijkstra"
                      ? "Dijkstra"
                      : a === "bidirectional"
                      ? "Bidirectional"
                      : "Highway"}
                  </button>
                ))}
              </div>
            </section>

            <section className="flex flex-col gap-2.5">
              <div className="flex items-center justify-between">
                <label className="text-[12px] font-medium text-muted-foreground">
                  Points
                </label>
                <span className="text-[11px] text-muted-foreground/80">
                  Tap the map or edit values
                </span>
              </div>
              <div className="overflow-hidden rounded-2xl border border-border bg-secondary/40">
                <CoordRow
                  color="var(--start)"
                  label="A"
                  lat={startLat}
                  lng={startLng}
                  onLat={setStartLat}
                  onLng={setStartLng}
                  onClear={clearStart}
                  disabled={running}
                  hasValue={hasStart}
                />
                <div className="h-px bg-border" />
                <CoordRow
                  color="var(--goal)"
                  label="B"
                  lat={goalLat}
                  lng={goalLng}
                  onLat={setGoalLat}
                  onLng={setGoalLng}
                  onClear={clearGoal}
                  disabled={running}
                  hasValue={hasGoal}
                />
              </div>
            </section>

            <section className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <label className="text-[12px] font-medium text-muted-foreground">
                  Animation speed
                </label>
              </div>
              <input
                type="range"
                min={1}
                max={40}
                value={41 - speed}
                onChange={(e) => setSpeed(41 - Number(e.target.value))}
                className="w-full accent-[var(--primary)]"
                disabled={running}
              />
              <div className="flex justify-between text-[11px] text-muted-foreground">
                <span>Slow</span><span>Fast</span>
              </div>
            </section>

            <section className="flex flex-col gap-2">
              {bothSet ? (
                <div className="flex gap-2">
                  <button
                    onClick={visualize}
                    disabled={running}
                    className="flex-1 rounded-full bg-primary px-4 py-2.5 text-[14px] font-semibold text-primary-foreground shadow-sm transition hover:brightness-110 active:scale-[0.98] disabled:opacity-50"
                  >
                    {running ? "Calculating…" : "Calculate"}
                  </button>
                  <button
                    onClick={() => { stopAnim(); clearMapLayers(); setStats(null); setError(null); setRouteUpdating(false); }}
                    className="rounded-full border border-border bg-card px-4 py-2.5 text-[14px] font-medium text-foreground transition hover:bg-accent"
                  >
                    Reset
                  </button>
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-border bg-secondary/60 px-4 py-3 text-center text-[12px] text-muted-foreground">
                  Set Start and Destination to visualize
                </div>
              )}
              <button
                onClick={clearPoints}
                disabled={running || (!hasStart && !hasGoal)}
                title="Empty both coordinate fields, remove the A and B map pins, and clear the current search"
                className="w-full rounded-full border border-border bg-card px-3 py-2 text-[12px] font-medium text-muted-foreground transition hover:bg-accent disabled:opacity-50"
              >
                Clear all points
              </button>
              {error && (
                <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
                  {error}
                </div>
              )}
            </section>

            {stats && (
              <div className="grid grid-cols-2 gap-px overflow-hidden rounded-2xl bg-border text-center">
                <Stat label="Nodes expanded" value={stats.nodesExpanded.toLocaleString()} />
                <Stat label="Edges explored" value={stats.edgesExplored.toLocaleString()} />
                <Stat label="Distance" value={`${(stats.distanceM / 1000).toFixed(2)} km`} />
                <Stat label="Time" value={`${stats.timeMs.toFixed(1)} ms`} />
              </div>
            )}

            {routeGeoJSON && (
              <button
                onClick={downloadGeoJSON}
                className="flex items-center justify-center gap-2 rounded-full border border-border bg-card px-4 py-2.5 text-[13px] font-medium text-foreground transition hover:bg-accent"
              >
                <svg viewBox="0 0 24 24" fill="none" className="size-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="7 10 12 15 17 10"/>
                  <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                Export GeoJSON
              </button>
            )}

            {stats && <Legend />}
          </div>
        </aside>

        {/* Map */}
        <main className="relative min-h-0 min-w-0">
          <div className="absolute inset-0">
            <div ref={mapElRef} className="absolute inset-0 h-full w-full" />
          </div>
          {(!hasStart || !hasGoal) && (
            <div className="pointer-events-none absolute left-1/2 top-4 z-[400] -translate-x-1/2 rounded-full border border-border/60 bg-[var(--glass)] px-4 py-1.5 text-[12px] font-medium text-foreground shadow-[0_4px_20px_-8px_rgba(0,0,0,0.18)] backdrop-blur-2xl">
              {!hasStart
                ? "Click the map to set Start (A)"
                : "Click the map to set Destination (B)"}
            </div>
          )}
          {routeUpdating && (
            <div className="pointer-events-none absolute bottom-6 left-1/2 z-[400] -translate-x-1/2 rounded-full border border-border/60 bg-[var(--glass)] px-4 py-1.5 text-[12px] font-medium text-foreground shadow-[0_4px_20px_-8px_rgba(0,0,0,0.18)] backdrop-blur-2xl">
              <span className="mr-1.5 inline-block size-1.5 animate-pulse rounded-full bg-primary" />
              Updating route…
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function CoordInput({
  value, onChange, disabled, placeholder,
}: { value: string; onChange: (v: string) => void; disabled?: boolean; placeholder?: string }) {
  return (
    <input
      type="text"
      inputMode="decimal"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      placeholder={placeholder}
      className="w-full rounded-xl border border-input bg-card px-3 py-2 text-[13px] tabular-nums text-foreground placeholder:text-muted-foreground transition focus:border-ring focus:outline-none focus:ring-4 focus:ring-ring/15 disabled:opacity-60"
    />
  );
}

function CoordRow({
  color, label, lat, lng, onLat, onLng, onClear, disabled, hasValue,
}: {
  color: string;
  label: string;
  lat: string;
  lng: string;
  onLat: (v: string) => void;
  onLng: (v: string) => void;
  onClear: () => void;
  disabled?: boolean;
  hasValue: boolean;
}) {
  const inputCls =
    "min-w-0 flex-1 bg-transparent px-2 py-2 text-[13px] tabular-nums text-foreground placeholder:text-muted-foreground/70 focus:outline-none disabled:opacity-60";
  return (
    <div className="flex items-center gap-2 bg-card px-3 py-1.5">
      <span
        className="size-2.5 shrink-0 rounded-full ring-2 ring-card"
        style={{ background: color }}
        aria-hidden
      />
      <span className="w-3 shrink-0 text-[11px] font-semibold tracking-wide text-muted-foreground">
        {label}
      </span>
      <input
        type="text"
        inputMode="decimal"
        value={lat}
        onChange={(e) => onLat(e.target.value)}
        disabled={disabled}
        placeholder="lat"
        className={inputCls}
      />
      <span className="text-muted-foreground/50">·</span>
      <input
        type="text"
        inputMode="decimal"
        value={lng}
        onChange={(e) => onLng(e.target.value)}
        disabled={disabled}
        placeholder="lng"
        className={inputCls}
      />
      <button
        onClick={onClear}
        disabled={disabled || !hasValue}
        aria-label={`Clear ${label}`}
        className="shrink-0 rounded-full p-1.5 text-muted-foreground transition hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-30"
      >
        <svg viewBox="0 0 24 24" fill="none" className="size-3.5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-card px-3 py-3">
      <div className="text-[17px] font-semibold tabular-nums tracking-tight text-foreground">{value}</div>
      <div className="mt-0.5 text-[11px] font-medium text-muted-foreground">{label}</div>
    </div>
  );
}

function Legend() {
  const items: { color: string; label: string }[] = [
    { color: "var(--start)", label: "Start" },
    { color: "var(--goal)", label: "Destination" },
    { color: "var(--frontier)", label: "Frontier" },
    { color: "var(--visited)", label: "Visited" },
    { color: "var(--path)", label: "Final route" },
    { color: "var(--wall)", label: "Obstacle" },
  ];
  return (
    <div className="mt-2 grid grid-cols-2 gap-y-2 gap-x-3 border-t border-border/70 pt-5">
      {items.map((i) => (
        <div key={i.label} className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <span className="size-2.5 rounded-full" style={{ background: i.color }} />
          {i.label}
        </div>
      ))}
    </div>
  );
}

