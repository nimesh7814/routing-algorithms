import math
import time
from heapq import heappush, heappop


def euclidean_heuristic(node_id: int, target_id: int, nodes: dict) -> float:
    x1, y1 = nodes[node_id]
    x2, y2 = nodes[target_id]
    return math.hypot(x2 - x1, y2 - y1)


def astar_instrumented(
    graph: dict,
    nodes_coords: dict,
    source: int,
    target: int,
    started_at: float | None = None,
    trace_steps: bool = False,  # disabled by default — enable only when caller needs animation steps
):
    if started_at is None:
        started_at = time.perf_counter()

    def timestamp_ms() -> float:
        return (time.perf_counter() - started_at) * 1000

    g = {source: 0.0}
    h_src = euclidean_heuristic(source, target, nodes_coords)
    pq = [(h_src, source)]
    prev = {source: None}
    settled: set = set()
    steps = [] if trace_steps else None
    nodes_expanded = 0

    def record_step(step: dict) -> None:
        if steps is not None:
            steps.append(step)

    while pq:
        f_u, u = heappop(pq)
        if u in settled:
            continue
        settled.add(u)
        nodes_expanded += 1
        record_step({"type": "node", "id": u, "direction": "fwd", "timestamp_ms": round(timestamp_ms(), 3)})

        if u == target:
            break

        g_u = g[u]
        for v, w in graph.get(u, {}).items():
            if v in settled:
                continue
            tentative_g = g_u + w
            if tentative_g < g.get(v, float("inf")):
                g[v] = tentative_g
                prev[v] = u
                h_v = euclidean_heuristic(v, target, nodes_coords)
                heappush(pq, (tentative_g + h_v, v))
                record_step({"type": "edge", "from": u, "to": v, "direction": "fwd", "timestamp_ms": round(timestamp_ms(), 3)})

    if target not in g:
        return None, float("inf"), steps or [], nodes_expanded

    path, node = [], target
    while node is not None:
        path.append(node)
        node = prev.get(node)
    path.reverse()

    return path, g[target], steps or [], nodes_expanded
