"""
graph_store.py — singleton that holds the loaded graph in memory.
Built once on startup from env-configured paths.
"""

import json
import math
import os
import time
import logging

logger = logging.getLogger(__name__)

# ── populated by init() ───────────────────────────────────────────────────────
graph: dict = {}
nodes: dict = {}          # { node_id: (x_m, y_m) }
nodes_latlon: dict = {}   # { node_id: (lat, lon) }  — for frontend
rev_graph: dict = {}

# HH extras (populated lazily)
hh_highway_graph: dict = {}
hh_rev_highway_graph: dict = {}
hh_neighbourhood_r: dict = {}
hh_loaded: bool = False

_transformer_fwd = None   # projected → latlon  (set in init)


def _build_rev(g: dict) -> dict:
    rev = {}
    for u, nbrs in g.items():
        for v, w in nbrs.items():
            rev.setdefault(v, {})[u] = w
    return rev


def _xy_to_latlon(x: float, y: float, transformer) -> tuple:
    lon, lat = transformer.transform(x, y)
    return lat, lon


def init():
    """Called once at FastAPI startup."""
    global graph, nodes, nodes_latlon, rev_graph, _transformer_fwd

    epsg      = int(os.environ.get("EPSG", "32644"))
    graph_file = os.environ.get("GRAPH_FILE", "/app/export/graph.json")
    geojson    = os.environ.get("GEOJSON_FILE", "/app/data/roads.geojson")
    export_dir = os.path.dirname(graph_file)

    # Build inverse transformer: projected CRS → WGS84
    from pyproj import Transformer as T
    _transformer_fwd = T.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)

    # ── Build graph if missing ──────────────────────────────────────────────
    if not os.path.exists(graph_file):
        logger.info("Graph file not found — building from GeoJSON...")
        from app.core.builder import build_graph
        build_graph(geojson, graph_file, epsg=epsg, show_progress=False)

    # ── Load graph ──────────────────────────────────────────────────────────
    logger.info(f"Loading graph from {graph_file}...")
    t0 = time.perf_counter()
    with open(graph_file) as f:
        data = json.load(f)

    graph = {
        int(float(k)): {int(float(nk)): nv for nk, nv in v.items()}
        for k, v in data["graph"].items()
    }
    nodes = {
        int(float(r["node_id"])): (r["x"], r["y"])
        for r in data["nodes"]
    }

    # pre-compute lat/lon for every node (used by frontend)
    nodes_latlon = {}
    for nid, (x, y) in nodes.items():
        lat, lon = _xy_to_latlon(x, y, _transformer_fwd)
        nodes_latlon[nid] = (lat, lon)

    rev_graph = _build_rev(graph)
    elapsed = time.perf_counter() - t0
    n_edges = sum(len(v) for v in graph.values())
    logger.info(f"Graph loaded: {len(graph)} nodes, {n_edges} edges in {elapsed:.2f}s")


def ensure_hh():
    """Load (or build) the Highway Hierarchy index."""
    global hh_highway_graph, hh_rev_highway_graph, hh_neighbourhood_r, hh_loaded

    if hh_loaded:
        return

    from app.algorithms.highway import (
        build_highway_hierarchy, save_hh, load_hh
    )

    graph_file = os.environ.get("GRAPH_FILE", "/app/export/graph.json")
    hh_file = os.path.splitext(graph_file)[0] + "_hh.json"

    if os.path.exists(hh_file):
        logger.info(f"Loading HH cache from {hh_file}...")
        hh_highway_graph, hh_rev_highway_graph, hh_neighbourhood_r = load_hh(hh_file)
    else:
        logger.info("Building Highway Hierarchy (this may take a while)...")
        t0 = time.perf_counter()
        hh_highway_graph, hh_rev_highway_graph, hh_neighbourhood_r = build_highway_hierarchy(graph)
        logger.info(f"HH built in {time.perf_counter() - t0:.1f}s — saving to {hh_file}")
        save_hh(hh_file, hh_highway_graph, hh_rev_highway_graph, hh_neighbourhood_r)

    hh_loaded = True


def latlon_to_xy(lat: float, lon: float) -> tuple:
    epsg = int(os.environ.get("EPSG", "32644"))
    from pyproj import Transformer as T
    tr = T.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x, y = tr.transform(lon, lat)
    return x, y


def find_nearest_node(x: float, y: float) -> int:
    best_id, best_dist = None, float("inf")
    for nid, (nx, ny) in nodes.items():
        d = math.hypot(nx - x, ny - y)
        if d < best_dist:
            best_dist, best_id = d, nid
    return best_id


def snap(lat: float, lon: float) -> int:
    x, y = latlon_to_xy(lat, lon)
    return find_nearest_node(x, y)



