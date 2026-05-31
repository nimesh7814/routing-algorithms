"""
Bidirectional Dijkstra — instrumented for animation.
Forward steps tagged "fwd", backward steps tagged "bwd".
Each step includes an elapsed-millisecond timestamp.
"""

import math
import time
from heapq import heappush, heappop


def build_reverse_graph(graph: dict) -> dict:
    rev = {}
    for u, neighbours in graph.items():
        for v, w in neighbours.items():
            rev.setdefault(v, {})[u] = w
    return rev


def bidirectional_dijkstra_instrumented(graph: dict, rev_graph: dict,
                                         source: int, target: int,
                                         started_at: float | None = None):
    if source == target:
        return [source], 0.0, [], 0

    if started_at is None:
        started_at = time.perf_counter()

    def timestamp_ms() -> float:
        return (time.perf_counter() - started_at) * 1000

    INF = float("inf")
    dist_fwd = {source: 0.0}
    dist_bwd = {target: 0.0}
    prev_fwd = {source: None}
    prev_bwd = {target: None}
    settled_fwd: set = set()
    settled_bwd: set = set()
    pq_fwd = [(0.0, source)]
    pq_bwd = [(0.0, target)]
    best = INF
    meeting_node = None
    steps = []
    nodes_expanded = 0

    def _relax_fwd():
        nonlocal best, meeting_node, nodes_expanded
        if not pq_fwd:
            return
        d, u = heappop(pq_fwd)
        if u in settled_fwd or d > dist_fwd.get(u, INF):
            return
        settled_fwd.add(u)
        nodes_expanded += 1
        steps.append({"type": "node", "id": u, "direction": "fwd", "timestamp_ms": round(timestamp_ms(), 3)})

        for v, w in graph.get(u, {}).items():
            nd = d + w
            if nd < dist_fwd.get(v, INF):
                dist_fwd[v] = nd
                prev_fwd[v] = u
                heappush(pq_fwd, (nd, v))
                steps.append({"type": "edge", "from": u, "to": v, "direction": "fwd", "timestamp_ms": round(timestamp_ms(), 3)})
            if v in dist_bwd:
                c = nd + dist_bwd[v]
                if c < best:
                    best, meeting_node = c, v
        if u in dist_bwd:
            c = d + dist_bwd[u]
            if c < best:
                best, meeting_node = c, u

    def _relax_bwd():
        nonlocal best, meeting_node, nodes_expanded
        if not pq_bwd:
            return
        d, u = heappop(pq_bwd)
        if u in settled_bwd or d > dist_bwd.get(u, INF):
            return
        settled_bwd.add(u)
        nodes_expanded += 1
        steps.append({"type": "node", "id": u, "direction": "bwd", "timestamp_ms": round(timestamp_ms(), 3)})

        for v, w in rev_graph.get(u, {}).items():
            nd = d + w
            if nd < dist_bwd.get(v, INF):
                dist_bwd[v] = nd
                prev_bwd[v] = u
                heappush(pq_bwd, (nd, v))
                steps.append({"type": "edge", "from": u, "to": v, "direction": "bwd", "timestamp_ms": round(timestamp_ms(), 3)})
            if v in dist_fwd:
                c = nd + dist_fwd[v]
                if c < best:
                    best, meeting_node = c, v
        if u in dist_fwd:
            c = d + dist_fwd[u]
            if c < best:
                best, meeting_node = c, u

    while pq_fwd or pq_bwd:
        top_fwd = pq_fwd[0][0] if pq_fwd else INF
        top_bwd = pq_bwd[0][0] if pq_bwd else INF
        if top_fwd + top_bwd >= best:
            break
        if top_fwd <= top_bwd:
            _relax_fwd()
        else:
            _relax_bwd()

    if meeting_node is None:
        return None, INF, steps, nodes_expanded

    path_fwd = []
    node = meeting_node
    while node is not None:
        path_fwd.append(node)
        node = prev_fwd.get(node)
    path_fwd.reverse()

    path_bwd = []
    node = prev_bwd.get(meeting_node)
    while node is not None:
        path_bwd.append(node)
        node = prev_bwd.get(node)

    return path_fwd + path_bwd, best, steps, nodes_expanded
