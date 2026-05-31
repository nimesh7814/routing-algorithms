"""
Dijkstra — instrumented to record exploration steps for frontend animation.
Each step includes a timestamp in milliseconds relative to the start of the
search so the frontend can animate the exploration order.
"""

import math
import time
from heapq import heapify, heappop, heappush


def dijkstra_instrumented(graph: dict, nodes_coords: dict, source: int, target: int, started_at: float | None = None):
    """
    Returns:
        path           : list[int] | None
        total_distance : float
        steps          : list of dicts  (explored nodes/edges in order)
        nodes_expanded : int
    """
    if started_at is None:
        started_at = time.perf_counter()

    def timestamp_ms() -> float:
        return (time.perf_counter() - started_at) * 1000

    distances = {node: float("inf") for node in graph}
    distances[source] = 0
    pq = [(0, source)]
    heapify(pq)
    visited = set()
    predecessors = {node: None for node in graph}
    steps = []
    nodes_expanded = 0

    while pq:
        current_dist, u = heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        nodes_expanded += 1

        steps.append({"type": "node", "id": u, "direction": "fwd", "timestamp_ms": round(timestamp_ms(), 3)})

        if u == target:
            break

        for v, w in graph.get(u, {}).items():
            if v in visited:
                continue
            tentative = current_dist + w
            if tentative < distances.get(v, float("inf")):
                distances[v] = tentative
                predecessors[v] = u
                heappush(pq, (tentative, v))
                steps.append({"type": "edge", "from": u, "to": v, "direction": "fwd", "timestamp_ms": round(timestamp_ms(), 3)})

    if distances.get(target, float("inf")) == float("inf"):
        return None, float("inf"), steps, nodes_expanded

    path, node = [], target
    while node is not None:
        path.append(node)
        node = predecessors.get(node)
    path.reverse()

    return path, distances[target], steps, nodes_expanded
