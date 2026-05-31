"""
main.py — FastAPI app with routing endpoints + static frontend serving.
"""

import os
import time
import logging
import math
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.models import RouteRequest, RouteResponse, GraphInfoResponse
import app.graph_store as gs

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    gs.init()
    yield


app = FastAPI(title="Pathfinder API", version="1.0.0", lifespan=lifespan)


# ── helpers ───────────────────────────────────────────────────────────────────

def _collect_node_ids(steps: list, path: list) -> set:
    ids = set(path)
    for s in steps:
        if s["type"] == "node":
            ids.add(s["id"])
        else:
            ids.add(s["from"])
            ids.add(s["to"])
    return ids


def _node_coords_map(node_ids: set) -> dict:
    """Return { str(nid): [lat, lon] } for every id in node_ids."""
    result = {}
    for nid in node_ids:
        if nid in gs.nodes_latlon:
            lat, lon = gs.nodes_latlon[nid]
            result[str(nid)] = [lat, lon]
    return result


def _path_coords(path: list) -> list:
    return [list(gs.nodes_latlon[n]) for n in path if n in gs.nodes_latlon]


def _reconstruct_bwd(meeting_node, prev_fwd, prev_bwd):
    fwd = []
    node = meeting_node
    while node is not None:
        fwd.append(node)
        node = prev_fwd.get(node)
    fwd.reverse()

    bwd = []
    node = prev_bwd.get(meeting_node)
    while node is not None:
        bwd.append(node)
        node = prev_bwd.get(node)

    return fwd + bwd


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/api/info", response_model=GraphInfoResponse)
def graph_info():
    n_edges = sum(len(v) for v in gs.graph.values())
    return GraphInfoResponse(
        total_nodes=len(gs.graph),
        total_edges=n_edges,
        graph_file=os.environ.get("GRAPH_FILE", "/app/export/graph.json"),
        hh_loaded=gs.hh_loaded,
    )


@app.post("/api/route", response_model=RouteResponse)
def route(req: RouteRequest):
    if not gs.graph:
        raise HTTPException(503, "Graph not loaded yet")

    src = gs.snap(req.source_lat, req.source_lon)
    tgt = gs.snap(req.dest_lat, req.dest_lon)

    total_nodes = len(gs.graph)
    total_edges = sum(len(v) for v in gs.graph.values())

    t0 = time.perf_counter()
    path = None
    dist = math.inf
    steps: list[Any] = []
    nodes_expanded = 0

    try:
        if req.algorithm == "dijkstra":
            from app.algorithms.dijkstra import dijkstra_instrumented
            path, dist, steps, nodes_expanded = dijkstra_instrumented(
                gs.graph, gs.nodes, src, tgt
            )

        elif req.algorithm == "bidirectional":
            from app.algorithms.bidirectional import bidirectional_dijkstra_instrumented
            path, dist, steps, nodes_expanded = bidirectional_dijkstra_instrumented(
                gs.graph, gs.rev_graph, src, tgt
            )

        elif req.algorithm == "astar":
            from app.algorithms.astar import astar_instrumented
            path, dist, steps, nodes_expanded = astar_instrumented(
                gs.graph, gs.nodes, src, tgt
            )

        elif req.algorithm == "highway":
            gs.ensure_hh()
            from app.algorithms.highway import hh_query_instrumented
            dist, meeting_node, prev_fwd, prev_bwd, steps, nodes_expanded = \
                hh_query_instrumented(
                    gs.graph, gs.hh_highway_graph,
                    gs.rev_graph, gs.hh_rev_highway_graph,
                    gs.hh_neighbourhood_r, src, tgt
                )
            if meeting_node is not None:
                path = _reconstruct_bwd(meeting_node, prev_fwd, prev_bwd)

    except Exception as e:
        logger.exception("Algorithm error")
        raise HTTPException(500, str(e))

    elapsed_ms = (time.perf_counter() - t0) * 1000

    if path is None or dist == math.inf:
        return RouteResponse(
            success=False,
            algorithm=req.algorithm,
            distance_m=0, distance_km=0,
            nodes_in_path=0, nodes_expanded=nodes_expanded,
            total_nodes=total_nodes, total_edges=total_edges,
            time_ms=elapsed_ms,
            path_coords=[], steps=[], node_coords={},
            source_node=src, dest_node=tgt,
            error="No path found between the two points."
        )

    # Collect all referenced node IDs for coordinate lookup
    referenced_ids = _collect_node_ids(steps, path)
    node_coords = _node_coords_map(referenced_ids)

    return RouteResponse(
        success=True,
        algorithm=req.algorithm,
        distance_m=round(dist, 1),
        distance_km=round(dist / 1000, 3),
        nodes_in_path=len(path),
        nodes_expanded=nodes_expanded,
        total_nodes=total_nodes,
        total_edges=total_edges,
        time_ms=round(elapsed_ms, 2),
        path_coords=_path_coords(path),
        steps=steps,
        node_coords=node_coords,
        source_node=src,
        dest_node=tgt,
    )


# ── static frontend ───────────────────────────────────────────────────────────
FRONTEND_DIR = "/app/frontend"

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
