"""
Bidirectional Dijkstra
======================
Runs two simultaneous Dijkstra searches:
  - Forward  search from source  (following edge direction)
  - Backward search from target  (following edges in reverse)

The searches alternate, always expanding the side with the
smallest tentative distance at the top of its priority queue.
The algorithm stops when a node is *settled* by both searches.
The shortest path is then:
    min { dist_fwd[v] + dist_bwd[v]  for all v visited by both }

Reference: Bast (2012) – Efficient Route Planning
"""

import json
import math
from heapq import heappush, heappop
from pyproj import Transformer

# ── coordinate transformer (WGS84 → UTM Zone 44N) ─────────────────────────
_transformer = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)


# ── graph / node I/O ────────────────────────────────────────────────────────

def load_graph(graph_file: str):
    """Load adjacency dict and node coordinates from JSON."""
    print("Loading graph...")
    with open(graph_file) as f:
        data = json.load(f)

    graph = {
        int(float(k)): {int(float(nk)): nv for nk, nv in v.items()}
        for k, v in data["graph"].items()
    }
    nodes = {
        int(float(r["node_id"])): (r["x"], r["y"])
        for r in data["nodes"]
    }
    print(f"  Loaded {len(graph)} nodes, "
          f"{sum(len(v) for v in graph.values())} directed edges")
    return graph, nodes


def build_reverse_graph(graph: dict) -> dict:
    """Return a graph where every edge (u→v, w) becomes (v→u, w)."""
    rev = {}
    for u, neighbours in graph.items():
        for v, w in neighbours.items():
            rev.setdefault(v, {})[u] = w
    return rev


def latlon_to_xy(lat: float, lon: float):
    x, y = _transformer.transform(lon, lat)
    return x, y


def find_nearest_node(x: float, y: float, nodes: dict):
    best_id, best_dist = None, float("inf")
    for nid, (nx, ny) in nodes.items():
        d = math.hypot(nx - x, ny - y)
        if d < best_dist:
            best_dist, best_id = d, nid
    return best_id, best_dist


def snap_to_node(latlon, nodes, label="Point"):
    x, y = latlon_to_xy(*latlon)
    nid, dist = find_nearest_node(x, y, nodes)
    print(f"  {label:12} {latlon}  →  node {nid}  ({dist:.1f} m away)")
    return nid


# ── core algorithm ───────────────────────────────────────────────────────────

def bidirectional_dijkstra(graph: dict, rev_graph: dict,
                           source: int, target: int):
    """
    Bidirectional Dijkstra.

    Returns
    -------
    path : list[int] | None
    total_distance : float
    """
    if source == target:
        return [source], 0.0

    # tentative distances
    dist_fwd = {source: 0.0}
    dist_bwd = {target: 0.0}

    # predecessor maps for path reconstruction
    prev_fwd = {source: None}
    prev_bwd = {target: None}

    # settled sets
    settled_fwd: set = set()
    settled_bwd: set = set()

    # priority queues  (dist, node)
    pq_fwd = [(0.0, source)]
    pq_bwd = [(0.0, target)]

    best = float("inf")   # best known meeting-point cost
    meeting_node = None

    def _relax(pq, dist, prev, graph_dir, settled_other, dist_other):
        nonlocal best, meeting_node

        if not pq:
            return
        d, u = heappop(pq)
        if d > dist.get(u, float("inf")):
            return          # stale entry
        settled = set()     # local – we add u below
        settled.add(u)

        for v, w in graph_dir.get(u, {}).items():
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heappush(pq, (nd, v))

            # check meeting point
            if v in dist_other:
                candidate = nd + dist_other[v]
                if candidate < best:
                    best = candidate
                    meeting_node = v

        # also check u itself as meeting point
        if u in dist_other:
            candidate = d + dist_other[u]
            if candidate < best:
                best = candidate
                meeting_node = u

    # alternate between forward and backward expansions
    while pq_fwd or pq_bwd:
        # choose the side with the smaller top-of-queue distance
        top_fwd = pq_fwd[0][0] if pq_fwd else float("inf")
        top_bwd = pq_bwd[0][0] if pq_bwd else float("inf")

        # stopping condition: both tops exceed the best found cost
        if top_fwd + top_bwd >= best:
            break

        if top_fwd <= top_bwd:
            _relax(pq_fwd, dist_fwd, prev_fwd, graph,     settled_bwd, dist_bwd)
        else:
            _relax(pq_bwd, dist_bwd, prev_bwd, rev_graph, settled_fwd, dist_fwd)

    if meeting_node is None:
        return None, float("inf")

    # ── reconstruct path ───────────────────────────────────────────────────
    # forward half: source → meeting_node
    path_fwd = []
    node = meeting_node
    while node is not None:
        path_fwd.append(node)
        node = prev_fwd.get(node)
    path_fwd.reverse()

    # backward half: meeting_node → target
    path_bwd = []
    node = prev_bwd.get(meeting_node)
    while node is not None:
        path_bwd.append(node)
        node = prev_bwd.get(node)

    return path_fwd + path_bwd, best


# ── public entry-point ───────────────────────────────────────────────────────

def run_bidirectional_dijkstra(graph_file: str,
                               source_latlon: tuple,
                               destination_latlon: tuple):
    """Full pipeline: load → snap → search → report."""
    graph, nodes = load_graph(graph_file)
    rev_graph    = build_reverse_graph(graph)

    print("\nSnapping coordinates to nearest nodes...")
    src  = snap_to_node(source_latlon,      nodes, "Source")
    tgt  = snap_to_node(destination_latlon, nodes, "Destination")

    print("\nRunning Bidirectional Dijkstra...")
    path, dist = bidirectional_dijkstra(graph, rev_graph, src, tgt)

    if path is None:
        print("❌  No path found.")
    else:
        print(f"\n✅  Shortest path found!")
        print(f"   Total distance : {dist / 1000:.2f} km  ({dist:.0f} m)")
        print(f"   Nodes in path  : {len(path)}")
        print(f"   Node sequence  : {path}")

    return path, dist


# ── standalone test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, sys
    GRAPH_FILE        = os.path.join("export", "cmb_roads_graph.json")
    SOURCE_LATLON      = (6.9271, 79.8612)
    DESTINATION_LATLON = (6.8935, 79.8553)
    run_bidirectional_dijkstra(GRAPH_FILE, SOURCE_LATLON, DESTINATION_LATLON)
