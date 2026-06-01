"""
Highway Hierarchies query.

The query uses three phases:
1. exact local search around the source,
2. exact local search around the target on the reverse graph,
3. shortest-path search on the reduced highway graph that skips local nodes.

If the hierarchy cannot connect the two sides, it falls back to bidirectional
Dijkstra so the API still returns an optimal shortest path for nonnegative
weights.
"""

import math
import os
import pickle
import time
from heapq import heappop, heappush

from algorithms.bidirectional import bidirectional_dijkstra_instrumented

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


def _run_limited_dijkstra(graph, source, max_distance, direction, started_at):
    dist = {source: 0.0}
    prev = {source: None}
    pq = [(0.0, source)]
    settled: set[int] = set()
    steps = []
    nodes_expanded = 0

    while pq:
        current_dist, u = heappop(pq)
        if u in settled or current_dist > dist.get(u, math.inf):
            continue
        if current_dist > max_distance:
            break

        settled.add(u)
        nodes_expanded += 1
        steps.append({"type": "node", "id": u, "direction": direction, "timestamp_ms": round((time.perf_counter() - started_at) * 1000, 3)})

        for v, w in graph.get(u, {}).items():
            if v in settled:
                continue
            tentative = current_dist + w
            if tentative < dist.get(v, math.inf):
                dist[v] = tentative
                prev[v] = u
                heappush(pq, (tentative, v))
                steps.append({"type": "edge", "from": u, "to": v, "direction": direction, "timestamp_ms": round((time.perf_counter() - started_at) * 1000, 3)})

    return dist, prev, steps, nodes_expanded, settled


def _run_multisource_dijkstra(graph, seeds, direction, started_at):
    dist = dict(seeds)
    prev = {node: None for node in seeds}
    pq = [(d, node) for node, d in seeds.items()]
    settled: set[int] = set()
    steps = []
    nodes_expanded = 0

    while pq:
        current_dist, u = heappop(pq)
        if u in settled or current_dist > dist.get(u, math.inf):
            continue

        settled.add(u)
        nodes_expanded += 1
        steps.append({"type": "node", "id": u, "direction": direction, "timestamp_ms": round((time.perf_counter() - started_at) * 1000, 3)})

        for v, w in graph.get(u, {}).items():
            if v in settled:
                continue
            tentative = current_dist + w
            if tentative < dist.get(v, math.inf):
                dist[v] = tentative
                prev[v] = u
                heappush(pq, (tentative, v))
                steps.append({"type": "edge", "from": u, "to": v, "direction": direction, "timestamp_ms": round((time.perf_counter() - started_at) * 1000, 3)})

    return dist, prev, steps, nodes_expanded


def _boundary_seeds(local_dist, highway_graph):
    seeds = {}
    for node, distance in local_dist.items():
        if highway_graph.get(node):
            seeds[node] = distance
    return seeds


def _reconstruct_path(prev, node):
    path = []
    while node is not None:
        path.append(node)
        node = prev.get(node)
    path.reverse()
    return path


def _combine_path(source_prev, highway_prev, target_prev, meeting_node):
    left = _reconstruct_path(highway_prev, meeting_node)
    if not left:
        return None

    source_anchor = left[0]
    source_path = _reconstruct_path(source_prev, source_anchor)
    if not source_path:
        return None

    right = []
    target_anchor = highway_prev.get(meeting_node)
    # The caller passes a backward predecessor map into target_prev, so the
    # meeting node can be extended by following predecessor links toward target.
    node = target_prev.get(meeting_node)
    while node is not None:
        right.append(node)
        node = target_prev.get(node)

    path = source_path[:-1] + left
    if right:
        path.extend(right)
    return path


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
    with open(path, "wb") as f:
        pickle.dump({
            "highway_graph": hw_graph,
            "rev_highway_graph": rev_hw_graph,
            "neighbourhood_r": neighbourhood_r,
            "neighbourhood_size": NEIGHBOURHOOD_SIZE,
        }, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_hh(path):
    try:
        with open(path, "rb") as f:
            cached = pickle.load(f)
    except Exception:
        import json

        with open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)

    if cached["highway_graph"] and isinstance(next(iter(cached["highway_graph"].keys())), str):
        hw = {int(k): {int(nk): nv for nk, nv in nbs.items()} for k, nbs in cached["highway_graph"].items()}
        rev_hw = {int(k): {int(nk): nv for nk, nv in nbs.items()} for k, nbs in cached["rev_highway_graph"].items()}
        radii = {int(k): v for k, v in cached["neighbourhood_r"].items()}
        return hw, rev_hw, radii

    return cached["highway_graph"], cached["rev_highway_graph"], cached["neighbourhood_r"]


def _build_rev_graph(graph):
    rev = {}
    for u, nbrs in graph.items():
        for v, w in nbrs.items():
            rev.setdefault(v, {})[u] = w
    return rev


def hh_query_instrumented(graph, highway_graph, rev_graph, rev_highway_graph,
                           neighbourhood_r, source, target, started_at: float | None = None):
    """Return a shortest path using highway nodes where possible."""
    if source == target:
        return [source], 0.0, [], 0

    if started_at is None:
        started_at = time.perf_counter()

    r_src = neighbourhood_r.get(source, 0.0)
    r_tgt = neighbourhood_r.get(target, 0.0)

    source_local_dist, source_local_prev, source_steps, source_expanded, source_settled = _run_limited_dijkstra(
        graph, source, r_src, "fwd", started_at
    )
    target_local_dist, target_local_prev, target_steps, target_expanded, target_settled = _run_limited_dijkstra(
        rev_graph, target, r_tgt, "bwd", started_at
    )

    steps = source_steps + target_steps
    nodes_expanded = source_expanded + target_expanded

    overlap = set(source_local_dist).intersection(target_local_dist)
    if overlap:
        meeting_node = min(overlap, key=lambda node: source_local_dist[node] + target_local_dist[node])
        path_to_meeting = _reconstruct_path(source_local_prev, meeting_node)
        path_from_meeting = []
        node = target_local_prev.get(meeting_node)
        while node is not None:
            path_from_meeting.append(node)
            node = target_local_prev.get(node)
        return (
            path_to_meeting + path_from_meeting,
            source_local_dist[meeting_node] + target_local_dist[meeting_node],
            steps,
            nodes_expanded,
        )

    highway_seeds = _boundary_seeds(source_local_dist, highway_graph)
    highway_target_seeds = _boundary_seeds(target_local_dist, rev_highway_graph)

    if not highway_seeds:
        highway_seeds = {source: 0.0}
    if not highway_target_seeds:
        highway_target_seeds = {target: 0.0}

    if not highway_graph or not rev_highway_graph:
        return bidirectional_dijkstra_instrumented(graph, rev_graph, source, target)

    fwd_dist, fwd_prev, fwd_steps, fwd_expanded = _run_multisource_dijkstra(
        highway_graph, highway_seeds, "fwd", started_at
    )
    bwd_dist, bwd_prev, bwd_steps, bwd_expanded = _run_multisource_dijkstra(
        rev_highway_graph, highway_target_seeds, "bwd", started_at
    )

    steps.extend(fwd_steps)
    steps.extend(bwd_steps)
    nodes_expanded += fwd_expanded + bwd_expanded

    meeting_candidates = set(fwd_dist).intersection(bwd_dist)
    if meeting_candidates:
        meeting_node = min(meeting_candidates, key=lambda node: fwd_dist[node] + bwd_dist[node])

        source_anchor = meeting_node
        while fwd_prev.get(source_anchor) is not None:
            source_anchor = fwd_prev[source_anchor]

        target_anchor = meeting_node
        while bwd_prev.get(target_anchor) is not None:
            target_anchor = bwd_prev[target_anchor]

        source_path = _reconstruct_path(source_local_prev, source_anchor)
        if not source_path:
            return bidirectional_dijkstra_instrumented(graph, rev_graph, source, target)

        highway_path = _reconstruct_path(fwd_prev, meeting_node)
        if highway_path:
            highway_path = highway_path[1:]

        target_path = []
        node = bwd_prev.get(meeting_node)
        while node is not None:
            target_path.append(node)
            node = bwd_prev.get(node)

        path = source_path[:-1] + highway_path + target_path
        total_distance = fwd_dist[meeting_node] + bwd_dist[meeting_node]
        return path, total_distance, steps, nodes_expanded

    return bidirectional_dijkstra_instrumented(graph, rev_graph, source, target)
