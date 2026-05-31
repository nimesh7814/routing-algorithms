"""
A* with Euclidean-Distance Heuristic
=====================================
A* is a Dijkstra variant that uses a heuristic h(u) to guide
the search toward the target, reducing the number of nodes expanded.

Priority-queue key:  f(u) = g(u) + h(u)
  g(u) = known shortest distance from source to u
  h(u) = straight-line (Euclidean) distance from u to target  ← admissible

Admissibility:  h(u) ≤ dist(u, target)  — Euclidean distance never
overestimates real road distance, so the heuristic is admissible and
A* is guaranteed to find the optimal path.

Reference: Bast (2012) – Efficient Route Planning, slides 13-15
"""

import json
import math
from heapq import heappush, heappop
from pyproj import Transformer

# ── coordinate transformer ────────────────────────────────────────────────
_transformer = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)


# ── I/O helpers ──────────────────────────────────────────────────────────────

def load_graph(graph_file: str):
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


# ── heuristic ────────────────────────────────────────────────────────────────

def euclidean_heuristic(node_id: int, target_id: int, nodes: dict) -> float:
    """
    Straight-line (Euclidean) distance between two graph nodes.
    Coordinates are in metres (projected CRS), so the result is in metres.
    This is admissible because road distance ≥ straight-line distance.
    """
    x1, y1 = nodes[node_id]
    x2, y2 = nodes[target_id]
    return math.hypot(x2 - x1, y2 - y1)


# ── A* core ──────────────────────────────────────────────────────────────────

def astar(graph: dict, nodes: dict, source: int, target: int):
    """
    A* shortest-path search with Euclidean heuristic.

    Parameters
    ----------
    graph   : adjacency dict  { node_id: { neighbour_id: distance_m } }
    nodes   : coordinate dict { node_id: (x_m, y_m) }
    source  : start node id
    target  : goal  node id

    Returns
    -------
    path           : list[int] | None
    total_distance : float  (metres)
    nodes_expanded : int    (diagnostic – how many nodes were settled)
    """
    # g[u] = best-known cost from source to u
    g = {source: 0.0}

    # f[u] = g[u] + h(u)  – used as priority
    h_src = euclidean_heuristic(source, target, nodes)
    f_src = h_src  # g=0 at source

    # priority queue entries: (f_value, node_id)
    pq = [(f_src, source)]

    prev = {source: None}
    settled: set = set()
    nodes_expanded = 0

    while pq:
        f_u, u = heappop(pq)

        if u in settled:
            continue          # stale entry
        settled.add(u)
        nodes_expanded += 1

        # ── stop as soon as target is settled ──────────────────────────────
        if u == target:
            break

        g_u = g[u]

        for v, w in graph.get(u, {}).items():
            if v in settled:
                continue
            tentative_g = g_u + w
            if tentative_g < g.get(v, float("inf")):
                g[v]   = tentative_g
                prev[v] = u
                h_v    = euclidean_heuristic(v, target, nodes)
                heappush(pq, (tentative_g + h_v, v))

    if target not in g:
        return None, float("inf"), nodes_expanded

    # ── reconstruct path ───────────────────────────────────────────────────
    path, node = [], target
    while node is not None:
        path.append(node)
        node = prev.get(node)
    path.reverse()

    return path, g[target], nodes_expanded


# ── public entry-point ───────────────────────────────────────────────────────

def run_astar(graph_file: str,
              source_latlon: tuple,
              destination_latlon: tuple):
    """Full pipeline: load → snap → A* → report."""
    graph, nodes = load_graph(graph_file)

    print("\nSnapping coordinates to nearest nodes...")
    src = snap_to_node(source_latlon,      nodes, "Source")
    tgt = snap_to_node(destination_latlon, nodes, "Destination")

    print("\nRunning A* (Euclidean heuristic)...")
    path, dist, expanded = astar(graph, nodes, src, tgt)

    if path is None:
        print("❌  No path found.")
    else:
        total_nodes = len(graph)
        pct = 100.0 * expanded / total_nodes if total_nodes else 0
        print(f"\n✅  Shortest path found!")
        print(f"   Total distance : {dist / 1000:.2f} km  ({dist:.0f} m)")
        print(f"   Nodes in path  : {len(path)}")
        print(f"   Nodes expanded : {expanded} / {total_nodes}  ({pct:.1f} %)")
        print(f"   Node sequence  : {path}")

    return path, dist


# ── standalone test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    GRAPH_FILE         = os.path.join("export", "cmb_roads_graph.json")
    SOURCE_LATLON      = (6.9271, 79.8612)
    DESTINATION_LATLON = (6.8935, 79.8553)
    run_astar(GRAPH_FILE, SOURCE_LATLON, DESTINATION_LATLON)
