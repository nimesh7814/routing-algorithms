"""
Highway Hierarchies query — instrumented for animation.
Forward steps tagged "fwd", backward steps tagged "bwd".
"""

import math
import json
import os
import time
from heapq import heappush, heappop

NEIGHBOURHOOD_SIZE = 40


def _local_dijkstra_neighbourhood(graph, source, neighbourhood_size):
    dist = {source: 0.0}
    pq = [(0.0, source, -1)]
    settled = 0
    radius = 0.0
    used = set()
    while pq and settled < neighbourhood_size:
        d, u, parent = heappop(pq)
        if d > dist.get(u, math.inf):
            continue
        settled += 1
        radius = d
        if parent >= 0:
            used.add((parent, u))
        for v, w in graph.get(u, {}).items():
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                heappush(pq, (nd, v, u))
    return radius, used


def build_highway_hierarchy(graph):
    n = len(graph)
    neighbourhood_r = {}
    local_edges = {}
    for v in graph:
        r, used = _local_dijkstra_neighbourhood(graph, v, NEIGHBOURHOOD_SIZE)
        neighbourhood_r[v] = r
        local_edges[v] = used

    highway_graph = {}
    rev_highway_graph = {}
    for u, nbrs in graph.items():
        for v, w in nbrs.items():
            local_u = (u, v) in local_edges.get(u, set())
            local_v = (u, v) in local_edges.get(v, set())
            if not local_u or not local_v:
                highway_graph.setdefault(u, {})[v] = w
                rev_highway_graph.setdefault(v, {})[u] = w

    return highway_graph, rev_highway_graph, neighbourhood_r


def save_hh(path, hw_graph, rev_hw_graph, neighbourhood_r):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "highway_graph": {str(k): {str(nk): nv for nk, nv in nbs.items()} for k, nbs in hw_graph.items()},
            "rev_highway_graph": {str(k): {str(nk): nv for nk, nv in nbs.items()} for k, nbs in rev_hw_graph.items()},
            "neighbourhood_r": {str(k): v for k, v in neighbourhood_r.items()},
            "neighbourhood_size": NEIGHBOURHOOD_SIZE,
        }, f)


def load_hh(path):
    with open(path) as f:
        cached = json.load(f)
    hw = {int(k): {int(nk): nv for nk, nv in nbs.items()} for k, nbs in cached["highway_graph"].items()}
    rev_hw = {int(k): {int(nk): nv for nk, nv in nbs.items()} for k, nbs in cached["rev_highway_graph"].items()}
    radii = {int(k): v for k, v in cached["neighbourhood_r"].items()}
    return hw, rev_hw, radii


def _build_rev_graph(graph):
    rev = {}
    for u, nbrs in graph.items():
        for v, w in nbrs.items():
            rev.setdefault(v, {})[u] = w
    return rev


def hh_query_instrumented(graph, highway_graph, rev_graph, rev_highway_graph,
                           neighbourhood_r, source, target):
    if source == target:
        return 0.0, source, {source: None}, {target: None}, [], 0

    INF = math.inf
    r_src = neighbourhood_r.get(source, 0.0)
    r_tgt = neighbourhood_r.get(target, 0.0)

    dist_fwd = {source: 0.0}
    dist_bwd = {target: 0.0}
    prev_fwd = {source: None}
    prev_bwd = {target: None}
    pq_fwd = [(0.0, source)]
    pq_bwd = [(0.0, target)]
    settled_fwd: set = set()
    settled_bwd: set = set()
    best = INF
    meeting_node = None
    steps = []
    nodes_expanded = 0

    def check(v, df, db):
        nonlocal best, meeting_node
        c = df + db
        if c < best:
            best, meeting_node = c, v

    while pq_fwd or pq_bwd:
        top_f = pq_fwd[0][0] if pq_fwd else INF
        top_b = pq_bwd[0][0] if pq_bwd else INF
        if top_f + top_b >= best:
            break

        if pq_fwd and top_f <= top_b:
            d, u = heappop(pq_fwd)
            if u in settled_fwd or d > dist_fwd.get(u, INF):
                continue
            settled_fwd.add(u)
            nodes_expanded += 1
            steps.append({"type": "node", "id": u, "direction": "fwd"})
            if u in dist_bwd:
                check(u, d, dist_bwd[u])
            g_use = graph if d <= r_src else highway_graph
            for v, w in g_use.get(u, {}).items():
                nd = d + w
                if nd < dist_fwd.get(v, INF):
                    dist_fwd[v] = nd
                    prev_fwd[v] = u
                    heappush(pq_fwd, (nd, v))
                    steps.append({"type": "edge", "from": u, "to": v, "direction": "fwd"})
                if v in dist_bwd:
                    check(v, nd, dist_bwd[v])
        else:
            d, u = heappop(pq_bwd)
            if u in settled_bwd or d > dist_bwd.get(u, INF):
                continue
            settled_bwd.add(u)
            nodes_expanded += 1
            steps.append({"type": "node", "id": u, "direction": "bwd"})
            if u in dist_fwd:
                check(u, dist_fwd[u], d)
            g_use = rev_graph if d <= r_tgt else rev_highway_graph
            for v, w in g_use.get(u, {}).items():
                nd = d + w
                if nd < dist_bwd.get(v, INF):
                    dist_bwd[v] = nd
                    prev_bwd[v] = u
                    heappush(pq_bwd, (nd, v))
                    steps.append({"type": "edge", "from": u, "to": v, "direction": "bwd"})
                if v in dist_fwd:
                    check(v, dist_fwd[v], nd)

    if meeting_node is None:
        return INF, None, prev_fwd, prev_bwd, steps, nodes_expanded

    return best, meeting_node, prev_fwd, prev_bwd, steps, nodes_expanded
