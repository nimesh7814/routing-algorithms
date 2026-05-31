"""
main.py — FastAPI routing service
==================================

Endpoints kept in this file:

GET /
  Returns {"status": "ok"} when export/graph.json is available and loaded.
  Returns {"status": "no_graph"} when export/graph.json is not available or cannot be loaded.

GET /summary
  Returns a JSON summary of the loaded graph.

POST /route
  Accepts WGS84 GeoJSON Point objects for source and destination.
  Snaps both points to the nearest graph nodes internally.
  Runs one selected algorithm from the available algorithm list.
  Returns the route as WGS84 GeoJSON.

Expected route request:
  {
    "source": {
      "type": "Point",
      "coordinates": [80.6350, 7.2906]
    },
    "destination": {
      "type": "Point",
      "coordinates": [80.6420, 7.2950]
    },
    "algorithm": "dijkstra"
  }

The source and destination coordinates must be WGS84 EPSG:4326 in
GeoJSON order: [longitude, latitude].
"""

import json
import math
import os
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from pyproj import Transformer
from scipy.spatial import KDTree

from algorithms.astar import astar_instrumented
from algorithms.bidirectional import bidirectional_dijkstra_instrumented, build_reverse_graph
from algorithms.dijkstra import dijkstra_instrumented
from algorithms.highway import (
    build_highway_hierarchy,
    hh_query_instrumented,
    load_hh,
    save_hh,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(__file__)
GRAPH_PATH = os.path.join(BASE_DIR, "export", "graph.json")
HH_CACHE_PATH = os.path.join(BASE_DIR, "export", "highway_cache.json")

_TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
_TO_WGS84 = Transformer.from_crs("EPSG:32644", "EPSG:4326", always_xy=True)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
class GraphState:
    def __init__(self) -> None:
        self.graph_loaded = False
        self.graph_error: str | None = None

        self.nodes_utm: dict[int, tuple[float, float]] = {}
        self.nodes_wgs84: dict[int, tuple[float, float]] = {}
        self.graph: dict[int, dict[int, float]] = {}
        self.rev_graph: dict[int, dict[int, float]] = {}

        self.hw_graph: dict[int, dict[int, float]] = {}
        self.rev_hw_graph: dict[int, dict[int, float]] = {}
        self.neighbourhood_r: dict[int, float] = {}
        self.highway_ready = False

        self.kdtree: KDTree | None = None
        self.kdtree_ids: list[int] = []

        self.bbox_min_lon = 0.0
        self.bbox_max_lon = 0.0
        self.bbox_min_lat = 0.0
        self.bbox_max_lat = 0.0

    def reset(self) -> None:
        self.__init__()


state = GraphState()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class AlgorithmEnum(str, Enum):
    dijkstra = "dijkstra"
    astar = "astar"
    bidirectional = "bidirectional"
    highway = "highway"


class GeoJSONPoint(BaseModel):
    type: str = Field(..., description="Must be Point")
    coordinates: list[float] = Field(
        ...,
        description="WGS84 coordinates in GeoJSON order: [longitude, latitude]",
        min_length=2,
        max_length=2,
    )

    @field_validator("type")
    @classmethod
    def validate_point_type(cls, value: str) -> str:
        if value != "Point":
            raise ValueError("Geometry type must be 'Point'.")
        return value

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(cls, value: list[float]) -> list[float]:
        lon, lat = value
        if not -180 <= lon <= 180:
            raise ValueError("Longitude must be between -180 and 180.")
        if not -90 <= lat <= 90:
            raise ValueError("Latitude must be between -90 and 90.")
        return value

    @property
    def lon(self) -> float:
        return float(self.coordinates[0])

    @property
    def lat(self) -> float:
        return float(self.coordinates[1])


class RouteRequest(BaseModel):
    source: GeoJSONPoint
    destination: GeoJSONPoint
    algorithm: str = Field(..., description="Selected routing algorithm")


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------
def available_algorithms() -> list[str]:
    algorithms = [
        AlgorithmEnum.dijkstra.value,
        AlgorithmEnum.astar.value,
        AlgorithmEnum.bidirectional.value,
    ]
    if state.highway_ready:
        algorithms.append(AlgorithmEnum.highway.value)
    return algorithms


def _load_graph() -> None:
    state.reset()

    if not os.path.exists(GRAPH_PATH):
        state.graph_error = f"graph.json not found at {GRAPH_PATH}"
        return

    try:
        with open(GRAPH_PATH, "r", encoding="utf-8") as file:
            raw = json.load(file)

        raw_nodes = raw["nodes"]
        xs: list[float] = []
        ys: list[float] = []
        ids: list[int] = []

        for rec in raw_nodes:
            node_id = int(rec["node_id"])
            x = float(rec["x"])
            y = float(rec["y"])

            state.nodes_utm[node_id] = (x, y)
            lon, lat = _TO_WGS84.transform(x, y)
            state.nodes_wgs84[node_id] = (lon, lat)

            xs.append(x)
            ys.append(y)
            ids.append(node_id)

        state.kdtree = KDTree(np.column_stack([xs, ys]))
        state.kdtree_ids = ids

        all_lons = [coord[0] for coord in state.nodes_wgs84.values()]
        all_lats = [coord[1] for coord in state.nodes_wgs84.values()]
        state.bbox_min_lon = min(all_lons)
        state.bbox_max_lon = max(all_lons)
        state.bbox_min_lat = min(all_lats)
        state.bbox_max_lat = max(all_lats)

        raw_graph = raw["graph"]
        for u_str, neighbours in raw_graph.items():
            u = int(u_str)
            state.graph[u] = {int(v_str): float(weight) for v_str, weight in neighbours.items()}

        state.rev_graph = build_reverse_graph(state.graph)
        state.graph_loaded = True
        state.graph_error = None

        print(
            f"[graph] Loaded {len(state.nodes_utm):,} nodes and "
            f"{sum(len(v) for v in state.graph.values()):,} directed edges."
        )

    except Exception as exc:
        state.reset()
        state.graph_error = f"Failed to load graph.json: {exc}"
        print(f"[graph] {state.graph_error}")


def _build_or_load_highway() -> None:
    if not state.graph_loaded:
        return

    if os.path.exists(HH_CACHE_PATH):
        try:
            hw_graph, rev_hw_graph, radii = load_hh(HH_CACHE_PATH)
            state.hw_graph = hw_graph
            state.rev_hw_graph = rev_hw_graph
            state.neighbourhood_r = radii
            state.highway_ready = True
            print("[highway] Loaded cached highway hierarchy.")
            return
        except Exception as exc:
            print(f"[highway] Cache load failed: {exc}. Rebuilding.")

    try:
        hw_graph, rev_hw_graph, radii = build_highway_hierarchy(state.graph)
        state.hw_graph = hw_graph
        state.rev_hw_graph = rev_hw_graph
        state.neighbourhood_r = radii
        state.highway_ready = True
        save_hh(HH_CACHE_PATH, hw_graph, rev_hw_graph, radii)
        print("[highway] Highway hierarchy built and cached.")
    except Exception as exc:
        state.highway_ready = False
        print(f"[highway] Failed to prepare highway hierarchy: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_graph()
    _build_or_load_highway()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Routing API",
    description="Routing API with only /, /summary, and /route endpoints.",
    version="3.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Coordinate and GeoJSON helpers
# ---------------------------------------------------------------------------
def wgs84_to_utm(lon: float, lat: float) -> tuple[float, float]:
    return _TO_UTM.transform(lon, lat)


def nearest_node(point: GeoJSONPoint, label: str) -> tuple[int, float]:
    if not state.graph_loaded or state.kdtree is None:
        raise HTTPException(status_code=503, detail="Graph is not available.")

    lon = point.lon
    lat = point.lat

    if not (
        state.bbox_min_lon <= lon <= state.bbox_max_lon
        and state.bbox_min_lat <= lat <= state.bbox_max_lat
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"{label} point is outside the graph extent.",
                "input": {"lon": lon, "lat": lat},
                "graph_bbox_wgs84": {
                    "min_lon": round(state.bbox_min_lon, 7),
                    "min_lat": round(state.bbox_min_lat, 7),
                    "max_lon": round(state.bbox_max_lon, 7),
                    "max_lat": round(state.bbox_max_lat, 7),
                },
            },
        )

    x, y = wgs84_to_utm(lon, lat)
    distance, index = state.kdtree.query([x, y])
    return state.kdtree_ids[index], float(distance)


def _node_to_wgs84_coord(node_id: int) -> list[float]:
    lon, lat = state.nodes_wgs84[node_id]
    return [lon, lat]


def _build_route_geojson(path: list[int]) -> dict[str, Any]:
    route_coords = [_node_to_wgs84_coord(node_id) for node_id in path if node_id in state.nodes_wgs84]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": route_coords,
                },
                "properties": {
                    "step_type": "route",
                    "node_count": len(path),
                },
            }
        ],
    }


def _run_algorithm(src: int, tgt: int, algorithm: str, started_at: float):
    if algorithm == AlgorithmEnum.dijkstra.value:
        return dijkstra_instrumented(state.graph, state.nodes_utm, src, tgt, started_at=started_at)

    if algorithm == AlgorithmEnum.astar.value:
        return astar_instrumented(state.graph, state.nodes_utm, src, tgt, started_at=started_at)

    if algorithm == AlgorithmEnum.bidirectional.value:
        return bidirectional_dijkstra_instrumented(state.graph, state.rev_graph, src, tgt, started_at=started_at)

    if algorithm == AlgorithmEnum.highway.value:
        path, best_dist, steps, nodes_expanded = hh_query_instrumented(
            graph=state.graph,
            highway_graph=state.hw_graph,
            rev_graph=state.rev_graph,
            rev_highway_graph=state.rev_hw_graph,
            neighbourhood_r=state.neighbourhood_r,
            source=src,
            target=tgt,
            started_at=started_at,
        )

        return path, best_dist, steps, nodes_expanded

    raise HTTPException(status_code=400, detail="Unknown routing algorithm.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/", summary="Check whether graph.json is available")
def root():
    if state.graph_loaded:
        return {"status": "ok"}

    return {
        "status": "no_graph",
        "graph_path": GRAPH_PATH,
        "detail": state.graph_error or "graph.json is not available.",
    }


@app.get("/summary", summary="Return graph summary as JSON")
def summary():
    if not state.graph_loaded:
        return {
            "status": "no_graph",
            "graph_path": GRAPH_PATH,
            "detail": state.graph_error or "graph.json is not available.",
        }

    edge_count = sum(len(neighbours) for neighbours in state.graph.values())
    return {
        "status": "ok",
        "graph_path": GRAPH_PATH,
        "node_count": len(state.nodes_utm),
        "edge_count": edge_count,
        "crs": {
            "input": "EPSG:4326 WGS84",
            "internal": "EPSG:32644 UTM Zone 44N",
            "output": "EPSG:4326 WGS84",
        },
        "bbox_wgs84": {
            "type": "Polygon",
            "coordinates": [
                [
                    [round(state.bbox_min_lon, 7), round(state.bbox_min_lat, 7)],
                    [round(state.bbox_max_lon, 7), round(state.bbox_min_lat, 7)],
                    [round(state.bbox_max_lon, 7), round(state.bbox_max_lat, 7)],
                    [round(state.bbox_min_lon, 7), round(state.bbox_max_lat, 7)],
                    [round(state.bbox_min_lon, 7), round(state.bbox_min_lat, 7)],
                ]
            ],
        },
        "available_algorithms": available_algorithms(),
    }


@app.post("/route", summary="Compute route from source Point to destination Point")
def route(req: RouteRequest):
    if not state.graph_loaded:
        raise HTTPException(status_code=503, detail="Graph is not available.")

    if req.algorithm not in available_algorithms():
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Algorithm '{req.algorithm}' is not available.",
                "available_algorithms": available_algorithms(),
            },
        )

    src_id, src_snap = nearest_node(req.source, label="source")
    tgt_id, tgt_snap = nearest_node(req.destination, label="destination")

    if src_id == tgt_id:
        raise HTTPException(
            status_code=400,
            detail="Source and destination snap to the same graph node.",
        )

    started_at = time.perf_counter()
    path, total_distance, steps, nodes_expanded = _run_algorithm(src_id, tgt_id, req.algorithm, started_at)
    elapsed_ms = (time.perf_counter() - started_at) * 1000

    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"No path found between node {src_id} and node {tgt_id}.",
        )

    edges_explored = sum(1 for step in steps if step.get("type") == "edge")

    return {
        "status": "ok",
        "algorithm": req.algorithm,
        "available_algorithms": available_algorithms(),
        "source": req.source.model_dump(),
        "destination": req.destination.model_dump(),
        "source_node_id": src_id,
        "destination_node_id": tgt_id,
        "source_snap_distance_m": round(src_snap, 2),
        "destination_snap_distance_m": round(tgt_snap, 2),
        "total_distance_m": round(total_distance, 4),
        "time_ms": round(elapsed_ms, 3),
        "nodes_expanded": nodes_expanded,
        "edges_explored": edges_explored,
        "search_steps": steps,
        "path_nodes": path,
        "geojson": _build_route_geojson(path),
    }
