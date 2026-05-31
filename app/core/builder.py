"""
app/core/builder.py
────────────────────
Single source of truth for building a road-network graph from a GeoJSON file.

Used by:
  • create_graph.py          — standalone CLI script
  • app/graph_store.py       — FastAPI startup auto-build
"""

import json
import logging
import os
import time

from shapely.geometry import Point
from tqdm import tqdm

logger = logging.getLogger(__name__)


def build_graph(
    geojson_path: str,
    output_path: str,
    epsg: int = 32644,
    tolerance: float = 1.0,
    show_progress: bool = True,
) -> dict:
    """
    Parse a GeoJSON road network and write a graph JSON to *output_path*.

    Parameters
    ----------
    geojson_path  : path to input .geojson (LineString / MultiLineString)
    output_path   : where to write the resulting graph JSON
    epsg          : projected CRS for metric distance calculation (default 32644)
    tolerance     : node-snapping tolerance in metres (default 1.0 m)
    show_progress : show tqdm progress bars (False when called from API server)

    Returns
    -------
    dict with keys "n_nodes", "n_edges", "output_path", "elapsed_s"
    """
    import geopandas as gpd

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # ── Load & reproject ──────────────────────────────────────────────────────
    logger.info(f"Reading GeoJSON: {geojson_path}")
    gdf = gpd.read_file(geojson_path)
    logger.info(f"  {len(gdf)} road segments found — reprojecting to EPSG:{epsg}…")
    gdf = gdf.to_crs(epsg=epsg)

    # ── Extract nodes & edges ─────────────────────────────────────────────────
    _nodes: dict[int, tuple[float, float]] = {}
    edges: list[tuple[int, int, float]] = []

    def get_or_create(point: Point) -> int:
        for nid, (nx, ny) in _nodes.items():
            if abs(nx - point.x) < tolerance and abs(ny - point.y) < tolerance:
                return nid
        new_id = len(_nodes)
        _nodes[new_id] = (point.x, point.y)
        return new_id

    t0 = time.perf_counter()
    iterable = tqdm(gdf.iterrows(), total=len(gdf), desc="Processing roads", unit="seg") \
        if show_progress else gdf.iterrows()

    for _, row in iterable:
        geom = row.geometry
        if geom is None:
            continue
        lines = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
        for line in lines:
            s = Point(line.coords[0])
            e = Point(line.coords[-1])
            sid = get_or_create(s)
            eid = get_or_create(e)
            edges.append((sid, eid, round(line.length, 2)))

    # ── Build adjacency dict (undirected) ─────────────────────────────────────
    graph_dict: dict[int, dict[int, float]] = {}
    for u, v, d in edges:
        graph_dict.setdefault(u, {})[v] = d
        graph_dict.setdefault(v, {})[u] = d

    nodes_list = [
        {"node_id": nid, "x": x, "y": y}
        for nid, (x, y) in _nodes.items()
    ]

    # ── Save ──────────────────────────────────────────────────────────────────
    with open(output_path, "w") as f:
        json.dump(
            {"graph": {str(k): v for k, v in graph_dict.items()}, "nodes": nodes_list},
            f,
        )

    elapsed = time.perf_counter() - t0
    n_nodes = len(_nodes)
    n_edges = len(edges)
    size_mb = os.path.getsize(output_path) / 1_048_576

    logger.info(
        f"Graph saved → {output_path}  "
        f"({n_nodes:,} nodes, {n_edges:,} edges, {size_mb:.1f} MB, {elapsed:.1f}s)"
    )

    return {
        "n_nodes": n_nodes,
        "n_edges": n_edges,
        "output_path": output_path,
        "elapsed_s": round(elapsed, 2),
    }
