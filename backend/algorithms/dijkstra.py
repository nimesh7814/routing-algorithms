import time
from heapq import heapify, heappop, heappush


def dijkstra_instrumented(
    graph: dict,
    _nodes_coords: dict,
    source: int,
    target: int,
    started_at: float | None = None,
    trace_steps: bool = False,  # disabled by default — enable only when caller needs animation steps
):
    if started_at is None:
        started_at = time.perf_counter()

    def timestamp_ms() -> float:
        return (time.perf_counter() - started_at) * 1000

    inf = float("inf")
    distances = {source: 0.0}
    predecessors = {source: None}
    pq = [(0, source)]
    heapify(pq)
    visited = set()
    steps = [] if trace_steps else None
    nodes_expanded = 0
    graph_get = graph.get
    push = heappush
    pop = heappop

    def record_step(step: dict) -> None:
        if steps is not None:
            steps.append(step)

    while pq:
        current_dist, u = pop(pq)
        if u in visited:
            continue
        visited.add(u)
        nodes_expanded += 1

        record_step({"type": "node", "id": u, "direction": "fwd", "timestamp_ms": round(timestamp_ms(), 3)})

        if u == target:
            break

        current_neighbours = graph_get(u, {})
        for v, w in current_neighbours.items():
            if v in visited:
                continue
            tentative = current_dist + w
            if tentative < distances.get(v, inf):
                distances[v] = tentative
                predecessors[v] = u
                push(pq, (tentative, v))
                record_step({"type": "edge", "from": u, "to": v, "direction": "fwd", "timestamp_ms": round(timestamp_ms(), 3)})

    if distances.get(target, inf) == inf:
        return None, inf, steps or [], nodes_expanded

    path, node = [], target
    while node is not None:
        path.append(node)
        node = predecessors.get(node)
    path.reverse()

    return path, distances[target], steps or [], nodes_expanded
