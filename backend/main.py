import json
import math
import os
import pickle
import time
import threading
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pyproj import Transformer
from scipy.spatial import KDTree

from algorithms.astar import astar_instrumented
from algorithms.bidirectional import bidirectional_dijkstra_instrumented
from algorithms.dijkstra import dijkstra_instrumented
from algorithms.highway import (
    hh_query_instrumented,
    load_hh,
)
from create_graph_cache import (
    build_graph as create_graph_build_graph,
    build_highway_cache as create_graph_build_highway_cache,
    ensure_binary_caches as create_graph_ensure_binary_caches,
)

BASE_DIR = os.path.dirname(__file__)
GRAPH_PATH = os.path.join(BASE_DIR, "export", "graph.json")
HH_CACHE_PATH = os.path.join(BASE_DIR, "export", "highway_cache.json")
GRAPH_CACHE_PATH = os.path.join(BASE_DIR, "export", "graph_cache.pkl")
HH_CACHE_BIN_PATH = os.path.join(BASE_DIR, "export", "highway_cache.pkl")
TRACE_DIJKSTRA_STEPS = os.environ.get("TRACE_DIJKSTRA_STEPS", "0") == "1"
TRACE_BIDIRECTIONAL_STEPS = os.environ.get("TRACE_BIDIRECTIONAL_STEPS", "0") == "1"
TRACE_ASTAR_STEPS = os.environ.get("TRACE_ASTAR_STEPS", "0") == "1"
TRACE_HIGHWAY_STEPS = os.environ.get("TRACE_HIGHWAY_STEPS", "0") == "1"

_TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
_TO_WGS84 = Transformer.from_crs("EPSG:32644", "EPSG:4326", always_xy=True)


class GraphState:
    def __init__(self) -> None:
        self.graph_loaded = False
        self.graph_error: str | None = None
        self.startup_stage: str | None = None

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


def _load_pickle(path: str) -> Any:
    with open(path, "rb") as file:
        return pickle.load(file)


def _save_pickle(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as file:
        pickle.dump(payload, file, protocol=pickle.HIGHEST_PROTOCOL)


def _is_fresh(cache_path: str, source_path: str) -> bool:
    if not os.path.exists(cache_path):
        return False
    if not os.path.exists(source_path):
        return True
    return os.path.getmtime(cache_path) >= os.path.getmtime(source_path)


def _graph_source_path() -> str:
    return os.environ.get("GEOJSON_FILE", os.path.join(BASE_DIR, "data", "lka_roads.geojson"))


def _startup_set_stage(stage: str | None) -> None:
    state.startup_stage = stage


def _load_graph_cache() -> None:
    cached = _load_pickle(GRAPH_CACHE_PATH)
    state.nodes_utm = cached["nodes_utm"]
    state.nodes_wgs84 = cached["nodes_wgs84"]
    state.graph = cached["graph"]
    state.rev_graph = cached["rev_graph"]
    state.kdtree_ids = cached["kdtree_ids"]
    state.bbox_min_lon = cached["bbox_min_lon"]
    state.bbox_max_lon = cached["bbox_max_lon"]
    state.bbox_min_lat = cached["bbox_min_lat"]
    state.bbox_max_lat = cached["bbox_max_lat"]

    # Always rebuild KDTree from coordinates instead of trusting the pickled
    # object — scipy's KDTree can deserialise incorrectly across versions and
    # rebuilding is fast (microseconds vs. unpickling overhead).
    kdtree = cached.get("kdtree")
    if kdtree is not None:
        try:
            # Quick sanity-check: query a point; raises if the object is corrupt.
            kdtree.query([0.0, 0.0])
            state.kdtree = kdtree
        except Exception:
            kdtree = None

    if kdtree is None:
        pts = [state.nodes_utm[nid] for nid in state.kdtree_ids]
        state.kdtree = KDTree(pts)

    state.graph_loaded = True


def _load_highway_cache() -> None:
    hw_graph, rev_hw_graph, radii = load_hh(HH_CACHE_BIN_PATH)
    state.hw_graph = hw_graph
    state.rev_hw_graph = rev_hw_graph
    state.neighbourhood_r = radii
    state.highway_ready = True


def _build_graph_and_highway_background(source_geojson: str, epsg: int) -> None:
    try:
        _startup_set_stage("building graph")
        create_graph_build_graph(
            geojson_path=source_geojson,
            output_path=GRAPH_PATH,
            epsg=epsg,
            tolerance=1.0,
            show_progress=False,
            stage_callback=_startup_set_stage,
        )
        _load_graph_cache()
        _load_highway_cache()
        _startup_set_stage("ok")
        state.graph_error = None
    except Exception as exc:
        state.graph_error = f"Failed to build graph cache: {exc}"
        state.graph_loaded = False
        state.highway_ready = False
        _startup_set_stage(None)


def _ensure_binary_caches_background(epsg: int) -> None:
    try:
        create_graph_ensure_binary_caches(GRAPH_PATH, epsg=epsg, stage_callback=_startup_set_stage)
        _load_graph_cache()
        _load_highway_cache()
        _startup_set_stage("ok")
        state.graph_error = None
    except Exception as exc:
        state.graph_error = f"Failed to build binary caches: {exc}"
        state.graph_loaded = False
        state.highway_ready = False
        _startup_set_stage(None)


def _build_highway_background() -> None:
    try:
        _startup_set_stage("building highway cache")
        create_graph_build_highway_cache(state.graph, HH_CACHE_BIN_PATH)
        _load_highway_cache()
        _startup_set_stage("ok")
        state.graph_error = None
    except Exception as exc:
        state.graph_error = f"Failed to build highway cache: {exc}"
        state.highway_ready = False
        _startup_set_stage(None)


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
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "source": {"type": "Point", "coordinates": [79.84, 6.93]},
                    "destination": {"type": "Point", "coordinates": [79.88, 6.96]},
                    "algorithm": "dijkstra",
                }
            ]
        }
    )

    source: GeoJSONPoint
    destination: GeoJSONPoint
    algorithm: str = Field(..., description="Selected routing algorithm")
    steps_enabled: bool = Field(
        False,
        description=(
            "When true, the response includes search_steps and node_coords for "
            "visualising the algorithm's exploration. Adds latency — leave false "
            "for production routing."
        ),
    )


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
    started_at = time.perf_counter()
    print(f"[startup] Loading graph cache from {GRAPH_CACHE_PATH}...")
    state.reset()

    try:
        if _is_fresh(GRAPH_CACHE_PATH, _graph_source_path()):
            _load_graph_cache()
        elif os.path.exists(GRAPH_PATH):
            state.startup_stage = "building graph"
            return
        else:
            state.startup_stage = "building graph"
            return

        state.graph_loaded = True
        state.graph_error = None

        elapsed_s = time.perf_counter() - started_at
        print(
            f"[graph] Loaded {len(state.nodes_utm):,} nodes and "
            f"{sum(len(v) for v in state.graph.values()):,} directed edges "
            f"in {elapsed_s:.1f}s."
        )

    except Exception as exc:
        state.reset()
        state.graph_error = f"Failed to load graph cache: {exc}"
        print(f"[graph] {state.graph_error}")


def _build_or_load_highway() -> None:
    if not state.graph_loaded:
        return

    started_at = time.perf_counter()
    print("[startup] Preparing highway hierarchy cache...")

    graph_source_path = GRAPH_CACHE_PATH if os.path.exists(GRAPH_CACHE_PATH) else _graph_source_path()

    if _is_fresh(HH_CACHE_BIN_PATH, graph_source_path):
        try:
            _load_highway_cache()
            state.highway_ready = True
            elapsed_s = time.perf_counter() - started_at
            print(f"[highway] Loaded cached highway hierarchy from binary cache in {elapsed_s:.1f}s.")
            return
        except Exception as exc:
            print(f"[highway] Cache load failed: {exc}. Rebuilding.")

    if _is_fresh(HH_CACHE_PATH, graph_source_path):
        try:
            hw_graph, rev_hw_graph, radii = load_hh(HH_CACHE_PATH)
            state.hw_graph = hw_graph
            state.rev_hw_graph = rev_hw_graph
            state.neighbourhood_r = radii
            state.highway_ready = True
            create_graph_build_highway_cache(state.graph, HH_CACHE_BIN_PATH)
            elapsed_s = time.perf_counter() - started_at
            print(f"[highway] Loaded cached highway hierarchy from legacy JSON in {elapsed_s:.1f}s.")
            return
        except Exception as exc:
            print(f"[highway] Legacy cache load failed: {exc}. Rebuilding.")

    try:
        _build_highway_background()
        elapsed_s = time.perf_counter() - started_at
        print(f"[highway] Highway hierarchy built and cached in {elapsed_s:.1f}s.")
    except Exception as exc:
        state.highway_ready = False
        print(f"[highway] Failed to prepare highway hierarchy: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_started_at = time.perf_counter()
    print("[startup] Starting routing service...")
    _load_graph()
    if state.graph_loaded:
        if _is_fresh(HH_CACHE_BIN_PATH, GRAPH_CACHE_PATH):
            _build_or_load_highway()
            _startup_set_stage("ok")
        else:
            _startup_set_stage("building highway cache")
            thread = threading.Thread(target=_build_highway_background, daemon=True)
            thread.start()
    else:
        if state.startup_stage == "building graph":
            epsg = int(os.environ.get("EPSG", "32644"))
            if os.path.exists(GRAPH_PATH):
                thread = threading.Thread(target=_ensure_binary_caches_background, args=(epsg,), daemon=True)
            else:
                thread = threading.Thread(
                    target=_build_graph_and_highway_background,
                    args=(_graph_source_path(), epsg),
                    daemon=True,
                )
            thread.start()
    print(f"[startup] Routing service ready in {time.perf_counter() - startup_started_at:.1f}s.")
    yield


app = FastAPI(
    title="Routing API",
    description="Routing API with only /, /summary, and /route endpoints.",
    version="3.0.0",
    lifespan=lifespan,
)


from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def _build_trace_node_coords(steps: list[dict], path: list[int]) -> dict[str, list[float]]:
    node_ids: set[int] = set(path)
    for step in steps:
        if step.get("type") == "node":
            node_ids.add(int(step["id"]))
        elif step.get("type") == "edge":
            node_ids.add(int(step["from"]))
            node_ids.add(int(step["to"]))

    coords: dict[str, list[float]] = {}
    for node_id in node_ids:
        if node_id in state.nodes_wgs84:
            lon, lat = state.nodes_wgs84[node_id]
            coords[str(node_id)] = [lon, lat]
    return coords


def _run_algorithm(src: int, tgt: int, algorithm: str, started_at: float, steps_enabled: bool = False):
    # Per-request tracing flag: the env-var TRACE_* flags set a server-wide
    # default; the caller can override by passing steps_enabled=True.
    trace = steps_enabled

    if algorithm == AlgorithmEnum.dijkstra.value:
        return dijkstra_instrumented(
            state.graph,
            state.nodes_utm,
            src,
            tgt,
            started_at=started_at,
            trace_steps=trace or TRACE_DIJKSTRA_STEPS,
        )

    if algorithm == AlgorithmEnum.astar.value:
        return astar_instrumented(
            state.graph,
            state.nodes_utm,
            src,
            tgt,
            started_at=started_at,
            trace_steps=trace or TRACE_ASTAR_STEPS,
        )

    if algorithm == AlgorithmEnum.bidirectional.value:
        return bidirectional_dijkstra_instrumented(
            state.graph,
            state.rev_graph,
            src,
            tgt,
            started_at=started_at,
            trace_steps=trace or TRACE_BIDIRECTIONAL_STEPS,
        )

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
            trace_steps=trace or TRACE_HIGHWAY_STEPS,
        )

        return path, best_dist, steps, nodes_expanded

    raise HTTPException(status_code=400, detail="Unknown routing algorithm.")


@app.get("/", summary="Check whether the graph cache is available")
def root():
    if state.graph_loaded and state.highway_ready:
        return {"status": "ok"}

    if state.startup_stage in {"building graph", "building highway cache"}:
        return {"status": state.startup_stage}

    if state.graph_loaded:
        return {"status": "building highway cache"}

    return {
        "status": "no_graph",
        "graph_path": GRAPH_PATH,
        "detail": state.graph_error or "graph cache is not available.",
    }


@app.get("/summary", summary="Return graph summary as JSON")
def summary():
    if not state.graph_loaded:
        return {
            "status": "no_graph",
            "graph_path": GRAPH_PATH,
            "detail": state.graph_error or "graph cache is not available.",
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


@app.get("/bbox", summary="Return available data boundary as WGS84")
def bbox():
    if not state.graph_loaded:
        return {
            "status": "no_graph",
            "graph_path": GRAPH_PATH,
            "detail": state.graph_error or "graph cache is not available.",
        }

    return {
        "status": "ok",
        "crs": "EPSG:4326 WGS84",
        "bbox_wgs84": {
            "min_lon": round(state.bbox_min_lon, 7),
            "min_lat": round(state.bbox_min_lat, 7),
            "max_lon": round(state.bbox_max_lon, 7),
            "max_lat": round(state.bbox_max_lat, 7),
        },
        "geojson": {
            "type": "Feature",
            "geometry": {
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
            "properties": {
                "crs": "EPSG:4326 WGS84",
            },
        },
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
    path, total_distance, steps, nodes_expanded = _run_algorithm(
        src_id, tgt_id, req.algorithm, started_at, steps_enabled=req.steps_enabled
    )
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
        "total_nodes": len(state.nodes_utm),
        "nodes_expanded": nodes_expanded,
        "edges_explored": edges_explored,
        # steps and node_coords are only populated when steps_enabled=true
        "search_steps": steps if req.steps_enabled else [],
        "path_nodes": path,
        "node_coords": _build_trace_node_coords(steps, path) if req.steps_enabled else {},
        "geojson": _build_route_geojson(path),
    }
