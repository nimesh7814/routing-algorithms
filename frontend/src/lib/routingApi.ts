import type { Algorithm } from "./pathfinding";

export type LatLng = { lat: number; lng: number };

export type BBoxResponse = {
  status: string;
  bbox_wgs84?: {
    min_lon: number;
    min_lat: number;
    max_lon: number;
    max_lat: number;
  };
};

export async function fetchBBox(baseUrl: string, signal?: AbortSignal): Promise<BBoxResponse> {
  const url = baseUrl.replace(/\/$/, "") + "/bbox";
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`bbox ${res.status}`);
  return (await res.json()) as BBoxResponse;
}

export type GeoStep = {
  type: string;
  // Common variants we tolerate from the backend:
  node?: number;
  from?: number;
  to?: number;
  u?: number;
  v?: number;
  cell?: number;
  // pass through anything else
  [k: string]: unknown;
};

export type RouteResponse = {
  status: string;
  algorithm: string;
  source_node_id: number;
  destination_node_id: number;
  source_snap_distance_m: number;
  destination_snap_distance_m: number;
  total_distance_m: number;
  time_ms: number;
  total_nodes: number;
  nodes_expanded: number;
  edges_explored: number;
  search_steps: GeoStep[];
  path_nodes: number[];
  // node_coords: { [node_id]: [lng, lat] }  (we tolerate either order)
  node_coords: Record<string, [number, number]>;
  geojson: GeoJSON.Feature | GeoJSON.FeatureCollection;
};

export type RouteRequest = {
  source: LatLng;
  destination: LatLng;
  algorithm: Algorithm;
  stepsEnabled?: boolean;
};

export async function fetchRoute(
  baseUrl: string,
  req: RouteRequest,
  signal?: AbortSignal,
): Promise<RouteResponse> {
  const url = baseUrl.replace(/\/$/, "") + "/route";
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      source: {
        type: "Point",
        coordinates: [req.source.lng, req.source.lat],
      },
      destination: {
        type: "Point",
        coordinates: [req.destination.lng, req.destination.lat],
      },
      algorithm: req.algorithm,
      steps_enabled: req.stepsEnabled ?? true,
    }),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail =
          typeof body.detail === "string"
            ? body.detail
            : body.detail.message ?? JSON.stringify(body.detail);
      }
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as RouteResponse;
}

// Backend returns [lng, lat] (GeoJSON convention). Some setups return
// [lat, lng]; we auto-detect by checking which coordinate is in the lat range.
export function normalizeCoord(c: [number, number]): LatLng {
  const [a, b] = c;
  // If the first value looks like a longitude (|a| > 90) → [lng, lat]
  if (Math.abs(a) > 90) return { lat: b, lng: a };
  // If the second value looks like a longitude (|b| > 90) → [lat, lng]
  if (Math.abs(b) > 90) return { lat: a, lng: b };
  // Ambiguous (both in [-90, 90]) — trust GeoJSON convention [lng, lat]
  return { lat: b, lng: a };
}

export function extractPathCoordsFromGeoJSON(
  gj: RouteResponse["geojson"],
): LatLng[] {
  const features =
    gj.type === "FeatureCollection"
      ? gj.features
      : gj.type === "Feature"
      ? [gj]
      : [];
  for (const f of features) {
    const g = f.geometry;
    if (!g) continue;
    if (g.type === "LineString") {
      return (g.coordinates as [number, number][]).map(normalizeCoord);
    }
    if (g.type === "MultiLineString") {
      return (g.coordinates as [number, number][][])
        .flat()
        .map(normalizeCoord);
    }
  }
  return [];
}

// Build an ordered list of LatLng to animate from search_steps. We accept
// several common shapes by extracting any referenced node id and looking it
// up in node_coords.
export function extractAnimationCoords(
  steps: GeoStep[],
  nodeCoords: RouteResponse["node_coords"],
): LatLng[] {
  const out: LatLng[] = [];
  for (const s of steps) {
    const candidates = [s.node, s.to, s.v, s.cell, s.from, s.u];
    for (const id of candidates) {
      if (id == null) continue;
      const c = nodeCoords[String(id)];
      if (c) {
        out.push(normalizeCoord(c));
        break;
      }
    }
  }
  return out;
}

// Extract directed edges (from→to) for animating exploration along the
// road network. Falls back to empty if step shape lacks a pair.
export function extractAnimationEdges(
  steps: GeoStep[],
  nodeCoords: RouteResponse["node_coords"],
): [LatLng, LatLng][] {
  const out: [LatLng, LatLng][] = [];
  for (const s of steps) {
    const a = (s.from ?? s.u) as number | undefined;
    const b = (s.to ?? s.v) as number | undefined;
    if (a == null || b == null) continue;
    const ca = nodeCoords[String(a)];
    const cb = nodeCoords[String(b)];
    if (ca && cb) out.push([normalizeCoord(ca), normalizeCoord(cb)]);
  }
  return out;
}