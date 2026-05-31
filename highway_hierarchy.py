"""
Highway Hierarchies (HH)
=========================
A locality-based speed-up technique for shortest-path queries.

CORE IDEA
---------
Real road networks have strong *locality*: most of any long route is spent
on high-class roads (motorways, trunk roads).  Highway Hierarchies exploit
this by defining a *highway network* H ⊆ G of edges that are "important"
globally, and pruning the Dijkstra search: once the search has left the local
neighbourhood of source or target, it is restricted to H.

DEFINITIONS
-----------
neighbourhood_radius(v) = the distance at which Dijkstra from v first reaches
    a node whose removal from the local search would NOT change any shortest
    path from v — computed via a local Dijkstra that bypasses v when it leaves
    the local area.

highway_edge(u→v) = an edge that appears on a shortest path from some node
    outside u's neighbourhood to some node outside v's neighbourhood.
    In practice we approximate: (u→v) is a highway edge iff
    it is used in a settled-node sequence that passes beyond both
    endpoints' neighbourhoods.

ALGORITHM (simplified, after Sanders & Schultes 2005)
------------------------------------------------------
PRE-PROCESSING  (create_hh.py)
  1. For each node v, compute neighbourhood_radius r(v) using a local
     Dijkstra that stops as soon as the search can no longer affect
     shortest paths outside the local area.
  2. Mark highway edges: edge (u→v, w) is a highway edge iff there exist
     s, t such that (u→v) lies on a shortest s-t path and
     dist(s, u) > r(s)  and  dist(v, t) > r(t).
     Approximation: sample many pairs and mark used edges.
  3. Build H = subgraph of highway edges.

QUERY
-----
Bidirectional Dijkstra with two phases per direction:
  Phase 1 (local):   search on G  within the neighbourhood of the endpoint.
  Phase 2 (highway): once outside the neighbourhood, restrict to H.

The searches meet on H, giving the optimal path.

NOTE ON THIS IMPLEMENTATION
----------------------------
This is a *readable, correct* implementation for learning purposes.
The neighbourhood radii are estimated by a simplified heuristic
(k-nearest settled nodes rather than the full Sanders-Schultes criterion),
and highway edges are identified by running local Dijkstras from every node.
For very large graphs (> 500 k nodes) a production implementation would
use parallel processing and more sophisticated neighbourhood estimation.

Reference: Sanders & Schultes (2005) – Highway Hierarchies Hasten Exact
           Shortest Path Queries; Bast (2012) slides 26-40.
"""

import json
import math
import os
import sys
import time
from heapq import heappush, heappop, heapify
from pyproj import Transformer

_transformer = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)

# ── tuneable parameters ───────────────────────────────────────────────────────
# Number of settled nodes that define the local neighbourhood boundary.
# Larger → fewer highway edges (tighter hierarchy), faster queries, but
# pre-processing is slower and may miss some highway edges.
NEIGHBOURHOOD_SIZE = 40   # nodes settled before neighbourhood ends


# ═══════════════════════════════════════════════════════════════════════════════
# I/O helpers
# ═══════════════════════════════════════════════════════════════════════════════

def load_graph(graph_file: str):
    print(f"Loading graph from {graph_file} ...")
    with open(graph_file) as f:
        data = json.load(f)
    graph = {
        int(float(k)): {int(float(nk)): float(nv) for nk, nv in v.items()}
        for k, v in data["graph"].items()
    }
    nodes = {
        int(float(r["node_id"])): (r["x"], r["y"])
        for r in data["nodes"]
    }
    n_edges = sum(len(v) for v in graph.values())
    print(f"  {len(graph):,} nodes,  {n_edges:,} directed edges")
    return graph, nodes


def latlon_to_xy(lat: float, lon: float) -> tuple[float, float]:
    return _transformer.transform(lon, lat)


def find_nearest_node(x: float, y: float, nodes: dict) -> tuple[int, float]:
    best_id, best_dist = None, math.inf
    for nid, (nx, ny) in nodes.items():
        d = math.hypot(nx - x, ny - y)
        if d < best_dist:
            best_dist, best_id = d, nid
    return best_id, best_dist


def snap_to_node(latlon: tuple, nodes: dict, label: str = "Point") -> int:
    x, y = latlon_to_xy(*latlon)
    nid, dist = find_nearest_node(x, y, nodes)
    print(f"  {label:12} {latlon}  →  node {nid}  ({dist:.1f} m away)")
    return nid


# ═══════════════════════════════════════════════════════════════════════════════
# Pre-processing: neighbourhood radii + highway edge identification
# ═══════════════════════════════════════════════════════════════════════════════

def _local_dijkstra_neighbourhood(graph: dict, source: int,
                                  neighbourhood_size: int
                                  ) -> tuple[float, set[tuple[int, int]]]:
    """
    Run Dijkstra from `source`, stopping after `neighbourhood_size` nodes
    are settled.  Returns:
      radius     – distance to the last settled node  (neighbourhood boundary)
      used_edges – set of (u, v) edges used inside the neighbourhood
    """
    dist      = {source: 0.0}
    pq        = [(0.0, source, -1)]    # (dist, node, parent)
    settled   = 0
    radius    = 0.0
    used      = set()

    while pq and settled < neighbourhood_size:
        d, u, parent = heappop(pq)
        if d > dist.get(u, math.inf):
            continue
        settled += 1
        radius   = d
        if parent >= 0:
            used.add((parent, u))
        for v, w in graph.get(u, {}).items():
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                heappush(pq, (nd, v, u))

    return radius, used


def build_highway_hierarchy(graph: dict,
                            neighbourhood_size: int = NEIGHBOURHOOD_SIZE
                            ) -> tuple[dict, dict, dict]:
    """
    Identify highway edges and build the highway subgraph.

    Algorithm
    ---------
    For each node v:
      1. Run a local Dijkstra limited to `neighbourhood_size` settled nodes.
      2. Record the neighbourhood radius r(v) and the edges used.
    An edge (u→v) is a *highway edge* if it is used by nodes whose
    neighbourhood does NOT contain both endpoints — i.e., it is needed
    for paths that leave the local area of at least one endpoint.

    Simplified criterion used here (efficient for moderate graphs):
      edge (u→v) is a highway edge if it was NOT used in the local
      Dijkstra of u  (i.e., it only appears on paths that leave u's
      neighbourhood from the start).

    Returns
    -------
    highway_graph     : dict  –  subgraph of highway edges
    rev_highway_graph : dict  –  reverse of highway_graph (for bwd search)
    neighbourhood_r   : dict  –  { node_id: radius }
    """
    n = len(graph)
    print(f"  Computing neighbourhood radii for {n:,} nodes ...")

    neighbourhood_r: dict[int, float]             = {}
    local_edges:     dict[int, set[tuple[int,int]]] = {}

    for i, v in enumerate(graph):
        r, used = _local_dijkstra_neighbourhood(graph, v, neighbourhood_size)
        neighbourhood_r[v] = r
        local_edges[v]     = used
        if (i + 1) % max(1, n // 10) == 0:
            pct = 100 * (i + 1) / n
            print(f"    {i+1:,}/{n:,}  ({pct:.0f} %)")

    # ── mark highway edges ─────────────────────────────────────────────────
    # An edge (u→v) is a highway edge iff it does NOT appear in the local
    # Dijkstra of u  AND  does NOT appear in the local Dijkstra of v.
    # Such an edge is only needed for long-distance routes.
    print("  Identifying highway edges ...")
    highway_graph:     dict[int, dict[int, float]] = {}
    rev_highway_graph: dict[int, dict[int, float]] = {}
    highway_count = 0

    for u, nbrs in graph.items():
        for v, w in nbrs.items():
            local_u = (u, v) in local_edges.get(u, set())
            local_v = (u, v) in local_edges.get(v, set())
            if not local_u or not local_v:
                # edge is needed for paths leaving at least one endpoint's
                # neighbourhood → highway edge
                highway_graph.setdefault(u, {})[v] = w
                rev_highway_graph.setdefault(v, {})[u] = w
                highway_count += 1

    n_all = sum(len(nb) for nb in graph.values())
    pct_hw = 100 * highway_count / n_all if n_all else 0
    print(f"  Highway edges: {highway_count:,} / {n_all:,}  ({pct_hw:.1f} %)")

    return highway_graph, rev_highway_graph, neighbourhood_r


# ═══════════════════════════════════════════════════════════════════════════════
# Save / Load
# ═══════════════════════════════════════════════════════════════════════════════

def save_hh(path: str,
            hw_graph: dict, rev_hw_graph: dict,
            neighbourhood_r: dict) -> None:
    print(f"Saving HH to {path} ...")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "highway_graph": {
                str(k): {str(nk): nv for nk, nv in nbs.items()}
                for k, nbs in hw_graph.items()
            },
            "rev_highway_graph": {
                str(k): {str(nk): nv for nk, nv in nbs.items()}
                for k, nbs in rev_hw_graph.items()
            },
            "neighbourhood_r": {str(k): v for k, v in neighbourhood_r.items()},
            "neighbourhood_size": NEIGHBOURHOOD_SIZE,
        }, f)
    size_mb = os.path.getsize(path) / 1_048_576
    print(f"  Saved  ({size_mb:.1f} MB)")


def load_hh(path: str) -> tuple[dict, dict, dict]:
    print(f"Loading HH from {path} ...")
    with open(path) as f:
        cached = json.load(f)
    hw      = {int(k): {int(nk): nv for nk, nv in nbs.items()}
               for k, nbs in cached["highway_graph"].items()}
    rev_hw  = {int(k): {int(nk): nv for nk, nv in nbs.items()}
               for k, nbs in cached["rev_highway_graph"].items()}
    radii   = {int(k): v for k, v in cached["neighbourhood_r"].items()}
    n_hw    = sum(len(nb) for nb in hw.values())
    print(f"  {len(radii):,} nodes,  {n_hw:,} highway edges  "
          f"(neighbourhood_size={cached.get('neighbourhood_size', '?')})")
    return hw, rev_hw, radii


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone precomputation entry-point  (mirrors create_ch.py)
# ═══════════════════════════════════════════════════════════════════════════════

def create_hh(graph_file: str) -> str:
    """
    Build (if needed) and save the Highway Hierarchy for `graph_file`.
    Returns the path to the HH cache file.
    """
    stem    = os.path.splitext(graph_file)[0]
    hh_file = f"{stem}_hh.json"

    if os.path.exists(hh_file):
        print(f"HH cache already exists at {hh_file} — skipping build.")
        print("  Delete that file to force a rebuild.")
        return hh_file

    graph, _ = load_graph(graph_file)

    print("\nBuilding Highway Hierarchy...")
    t0 = time.perf_counter()
    hw_graph, rev_hw_graph, neighbourhood_r = build_highway_hierarchy(graph)
    elapsed = time.perf_counter() - t0
    print(f"  Pre-processing time: {elapsed:.1f} s")

    save_hh(hh_file, hw_graph, rev_hw_graph, neighbourhood_r)

    print(f"\n✅  HH saved → {hh_file}")
    return hh_file


# ═══════════════════════════════════════════════════════════════════════════════
# Query: two-phase bidirectional Dijkstra
# ═══════════════════════════════════════════════════════════════════════════════

def hh_query(graph: dict, highway_graph: dict,
             rev_graph: dict, rev_highway_graph: dict,
             neighbourhood_r: dict,
             source: int, target: int):
    """
    Highway Hierarchy bidirectional query.

    Each direction has two phases:
      Phase 1 (local):   use full graph G within the neighbourhood radius.
      Phase 2 (highway): once distance > neighbourhood_r[source/target],
                         restrict to highway_graph / rev_highway_graph.

    Returns
    -------
    shortest_dist  : float
    meeting_node   : int | None
    dist_fwd       : dict
    dist_bwd       : dict
    prev_fwd       : dict
    prev_bwd       : dict
    nodes_expanded : int
    """
    if source == target:
        return (0.0, source,
                {source: 0.0}, {target: 0.0},
                {source: None}, {target: None}, 0)

    INF = math.inf
    r_src = neighbourhood_r.get(source, 0.0)
    r_tgt = neighbourhood_r.get(target, 0.0)

    dist_fwd: dict[int, float]      = {source: 0.0}
    dist_bwd: dict[int, float]      = {target: 0.0}
    prev_fwd: dict[int, int | None] = {source: None}
    prev_bwd: dict[int, int | None] = {target: None}

    pq_fwd = [(0.0, source)]
    pq_bwd = [(0.0, target)]

    settled_fwd: set[int] = set()
    settled_bwd: set[int] = set()

    best          = INF
    meeting_node  = None
    nodes_expanded = 0

    def _check_meeting(v, d_fwd_v, d_bwd_v):
        nonlocal best, meeting_node
        c = d_fwd_v + d_bwd_v
        if c < best:
            best, meeting_node = c, v

    while pq_fwd or pq_bwd:
        top_f = pq_fwd[0][0] if pq_fwd else INF
        top_b = pq_bwd[0][0] if pq_bwd else INF

        if top_f + top_b >= best:
            break

        if pq_fwd and top_f <= top_b:
            # ── forward step ───────────────────────────────────────────────
            d, u = heappop(pq_fwd)
            if u in settled_fwd:
                continue
            settled_fwd.add(u)
            nodes_expanded += 1

            if u in dist_bwd:
                _check_meeting(u, d, dist_bwd[u])

            # choose graph: local or highway
            g_use = graph if d <= r_src else highway_graph

            for v, w in g_use.get(u, {}).items():
                nd = d + w
                if nd < dist_fwd.get(v, INF):
                    dist_fwd[v] = nd
                    prev_fwd[v] = u
                    heappush(pq_fwd, (nd, v))
                    if v in dist_bwd:
                        _check_meeting(v, nd, dist_bwd[v])

        else:
            # ── backward step ──────────────────────────────────────────────
            d, u = heappop(pq_bwd)
            if u in settled_bwd:
                continue
            settled_bwd.add(u)
            nodes_expanded += 1

            if u in dist_fwd:
                _check_meeting(u, dist_fwd[u], d)

            # choose graph: local or highway (reversed)
            g_use = rev_graph if d <= r_tgt else rev_highway_graph

            for v, w in g_use.get(u, {}).items():
                nd = d + w
                if nd < dist_bwd.get(v, INF):
                    dist_bwd[v] = nd
                    prev_bwd[v] = u
                    heappush(pq_bwd, (nd, v))
                    if v in dist_fwd:
                        _check_meeting(v, dist_fwd[v], nd)

    return best, meeting_node, dist_fwd, dist_bwd, prev_fwd, prev_bwd, nodes_expanded


def _build_rev_graph(graph: dict) -> dict:
    """Reverse every edge for the backward search."""
    rev: dict[int, dict[int, float]] = {}
    for u, nbrs in graph.items():
        for v, w in nbrs.items():
            rev.setdefault(v, {})[u] = w
    return rev


def _reconstruct_path(meeting_node: int,
                      prev_fwd: dict, prev_bwd: dict) -> list[int]:
    fwd: list[int] = []
    node = meeting_node
    while node is not None:
        fwd.append(node)
        node = prev_fwd.get(node)
    fwd.reverse()

    bwd: list[int] = []
    node = prev_bwd.get(meeting_node)
    while node is not None:
        bwd.append(node)
        node = prev_bwd.get(node)

    return fwd + bwd


# ═══════════════════════════════════════════════════════════════════════════════
# Public entry-point
# ═══════════════════════════════════════════════════════════════════════════════

def run_highway_hierarchy(graph_file: str,
                          source_latlon: tuple,
                          destination_latlon: tuple,
                          hh_cache_file: str | None = None):
    """
    Full Highway Hierarchy query pipeline.

    Parameters
    ----------
    graph_file        : path to the graph JSON (from create_graph.py)
    source_latlon     : (lat, lon) of origin
    destination_latlon: (lat, lon) of destination
    hh_cache_file     : path to the HH cache.
                        If None, derived as <graph_stem>_hh.json.
                        If not found, pre-processing runs automatically.
    """
    # ── derive HH cache path ───────────────────────────────────────────────
    if hh_cache_file is None:
        hh_cache_file = os.path.splitext(graph_file)[0] + "_hh.json"

    # ── load graph ─────────────────────────────────────────────────────────
    graph, nodes = load_graph(graph_file)

    # ── load or build HH ───────────────────────────────────────────────────
    if os.path.exists(hh_cache_file):
        hw_graph, rev_hw_graph, neighbourhood_r = load_hh(hh_cache_file)
    else:
        print(f"\nHH cache not found. Building Highway Hierarchy ...")
        t0 = time.perf_counter()
        hw_graph, rev_hw_graph, neighbourhood_r = build_highway_hierarchy(graph)
        elapsed = time.perf_counter() - t0
        print(f"  Pre-processing time: {elapsed:.1f} s")
        save_hh(hh_cache_file, hw_graph, rev_hw_graph, neighbourhood_r)

    rev_graph = _build_rev_graph(graph)

    # ── snap coordinates ───────────────────────────────────────────────────
    print("\nSnapping coordinates to nearest nodes...")
    src = snap_to_node(source_latlon,      nodes, "Source")
    tgt = snap_to_node(destination_latlon, nodes, "Destination")

    # ── query ──────────────────────────────────────────────────────────────
    print("\nRunning Highway Hierarchy query...")
    dist, meeting_node, dist_fwd, dist_bwd, prev_fwd, prev_bwd, expanded = \
        hh_query(graph, hw_graph, rev_graph, rev_hw_graph,
                 neighbourhood_r, src, tgt)

    if dist == math.inf or meeting_node is None:
        print("❌  No path found.")
        return None, math.inf

    path = _reconstruct_path(meeting_node, prev_fwd, prev_bwd)

    total = len(graph)
    pct   = 100.0 * expanded / total if total else 0
    print(f"\n✅  Shortest path found!")
    print(f"   Total distance  : {dist / 1000:.2f} km  ({dist:.0f} m)")
    print(f"   Nodes in path   : {len(path)}")
    print(f"   Nodes expanded  : {expanded} / {total}  ({pct:.1f} %)")
    print(f"   Node sequence   : {path}")

    return path, dist


# ── standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    GRAPH_FILE         = os.path.join("export", "cmb_roads_graph.json")
    HH_CACHE           = os.path.join("export", "cmb_roads_graph_hh.json")
    SOURCE_LATLON      = (6.9271, 79.8612)
    DESTINATION_LATLON = (6.8935, 79.8553)
    run_highway_hierarchy(GRAPH_FILE, SOURCE_LATLON, DESTINATION_LATLON, HH_CACHE)
