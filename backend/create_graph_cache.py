"""
create_graph_cache.py
═════════════════════
Build the routing graph JSON plus the binary graph and highway caches.

This script is the cache-aware variant used by the FastAPI startup flow.
It writes:
- export/graph.json
- export/graph_cache.pkl
- export/highway_cache.pkl

If graph.json already exists, it can regenerate just the binary caches.
"""

import argparse
import json
import logging
import os
import pickle
import sys
import time
from collections import defaultdict
from math import hypot
from typing import Any, Callable

from pyproj import Transformer
from scipy.spatial import KDTree

from algorithms.highway import build_highway_hierarchy, save_hh

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BANNER = """
╔══════════════════════════════════════╗
║       Pathfinder — Graph Builder     ║
╚══════════════════════════════════════╝"""

StageCallback = Callable[[str | None], None]


def _iter_line_segments(geojson: dict):
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties") or {}
        geometry_type = geometry.get("type")

        if geometry_type == "LineString":
            yield geometry.get("coordinates", []), properties
        elif geometry_type == "MultiLineString":
            for line in geometry.get("coordinates", []):
                yield line, properties


def _count_line_segments(geojson: dict) -> int:
    count = 0
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        geometry_type = geometry.get("type")
        if geometry_type == "LineString":
            count += 1
        elif geometry_type == "MultiLineString":
            count += len(geometry.get("coordinates", []))
    return count


def _is_oneway(properties: dict) -> bool:
    value = properties.get("oneway")
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"yes", "true", "1", "forward"}


def _render_progress(current: int, total: int) -> None:
    if total <= 0:
        return

    width = 30
    ratio = current / total
    filled = int(width * ratio)
    bar = "=" * filled + "-" * (width - filled)
    sys.stdout.write(f"\r[{bar}] {current}/{total} ({ratio * 100:5.1f}%)")
    sys.stdout.flush()


def _graph_cache_path(output_path: str) -> str:
    return os.path.join(os.path.dirname(output_path) or ".", "graph_cache.pkl")


def _highway_cache_path(output_path: str) -> str:
    return os.path.join(os.path.dirname(output_path) or ".", "highway_cache.pkl")


def _write_pickle(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as file:
        pickle.dump(payload, file, protocol=pickle.HIGHEST_PROTOCOL)


def _load_pickle(path: str) -> Any:
    with open(path, "rb") as file:
        return pickle.load(file)


def _build_rev_graph(graph: dict[int, dict[int, float]]) -> dict[int, dict[int, float]]:
    rev: dict[int, dict[int, float]] = {}
    for u, nbrs in graph.items():
        for v, w in nbrs.items():
            rev.setdefault(v, {})[u] = w
    return rev


def _load_graph_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _graph_from_json(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[int, dict[int, float]]]:
    nodes = data.get("nodes", [])
    graph = {
        int(node_id): {int(neighbour): float(weight) for neighbour, weight in neighbours.items()}
        for node_id, neighbours in data.get("graph", {}).items()
    }
    return nodes, graph


def _write_graph_cache_from_json(graph_json_path: str, cache_path: str, epsg: int) -> dict[str, Any]:
    data = _load_graph_json(graph_json_path)
    nodes, graph = _graph_from_json(data)

    to_wgs84 = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)

    nodes_utm: dict[int, tuple[float, float]] = {}
    nodes_wgs84: dict[int, tuple[float, float]] = {}
    kdtree_points: list[tuple[float, float]] = []
    kdtree_ids: list[int] = []

    for rec in nodes:
        node_id = int(rec["node_id"])
        x = float(rec["x"])
        y = float(rec["y"])
        nodes_utm[node_id] = (x, y)
        nodes_wgs84[node_id] = to_wgs84.transform(x, y)
        kdtree_points.append((x, y))
        kdtree_ids.append(node_id)

    lons = [coord[0] for coord in nodes_wgs84.values()]
    lats = [coord[1] for coord in nodes_wgs84.values()]

    payload = {
        "nodes_utm": nodes_utm,
        "nodes_wgs84": nodes_wgs84,
        "graph": graph,
        "rev_graph": _build_rev_graph(graph),
        "kdtree": KDTree(kdtree_points) if kdtree_points else None,
        "kdtree_ids": kdtree_ids,
        "bbox_min_lon": min(lons) if lons else 0.0,
        "bbox_max_lon": max(lons) if lons else 0.0,
        "bbox_min_lat": min(lats) if lats else 0.0,
        "bbox_max_lat": max(lats) if lats else 0.0,
    }
    _write_pickle(cache_path, payload)
    return payload


def build_highway_cache(graph: dict[int, dict[int, float]], output_path: str) -> tuple[dict[int, dict[int, float]], dict[int, dict[int, float]], dict[int, float]]:
    hw_graph, rev_hw_graph, radii = build_highway_hierarchy(graph)
    save_hh(output_path, hw_graph, rev_hw_graph, radii)
    return hw_graph, rev_hw_graph, radii


def ensure_binary_caches(graph_json_path: str, epsg: int = 32644, stage_callback: StageCallback | None = None) -> dict[str, Any]:
    if stage_callback is not None:
        stage_callback("building graph")

    graph_cache_path = _graph_cache_path(graph_json_path)
    highway_cache_path = _highway_cache_path(graph_json_path)

    if os.path.exists(graph_cache_path):
        cached = _load_pickle(graph_cache_path)
    else:
        cached = _write_graph_cache_from_json(graph_json_path, graph_cache_path, epsg)

    if stage_callback is not None:
        stage_callback("building highway cache")

    if not os.path.exists(highway_cache_path):
        build_highway_cache(cached["graph"], highway_cache_path)

    if stage_callback is not None:
        stage_callback("ok")

    return cached


def build_graph(geojson_path: str, output_path: str, epsg: int = 32644, tolerance: float = 1.0, show_progress: bool = True, stage_callback: StageCallback | None = None):
    started_at = time.perf_counter()
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)

    with open(geojson_path, "r", encoding="utf-8") as file:
        geojson = json.load(file)

    total_segments = _count_line_segments(geojson)
    if stage_callback is not None:
        stage_callback("building graph")

    node_lookup: dict[tuple[int, int], int] = {}
    node_coords: dict[int, tuple[float, float]] = {}
    graph: dict[int, dict[int, float]] = defaultdict(dict)
    next_node_id = 0
    edge_count = 0

    def get_node_id(lon: float, lat: float) -> int:
        nonlocal next_node_id
        x, y = transformer.transform(lon, lat)
        if tolerance > 0:
            key = (round(x / tolerance), round(y / tolerance))
        else:
            key = (round(x, 6), round(y, 6))

        node_id = node_lookup.get(key)
        if node_id is None:
            node_id = next_node_id
            next_node_id += 1
            node_lookup[key] = node_id
            node_coords[node_id] = (x, y)
        return node_id

    for index, (segment, properties) in enumerate(_iter_line_segments(geojson), start=1):
        if len(segment) < 2:
            if show_progress:
                _render_progress(index, total_segments)
            continue

        forward_only = _is_oneway(properties)
        reverse_only = str(properties.get("oneway", "")).strip() == "-1"

        segment_nodes = []
        for lon, lat in segment:
            segment_nodes.append(get_node_id(float(lon), float(lat)))

        for left_node, right_node in zip(segment_nodes, segment_nodes[1:]):
            if left_node == right_node:
                continue

            left_x, left_y = node_coords[left_node]
            right_x, right_y = node_coords[right_node]
            weight = hypot(right_x - left_x, right_y - left_y)

            if not reverse_only:
                current = graph[left_node].get(right_node)
                if current is None or weight < current:
                    graph[left_node][right_node] = weight
                    edge_count += 1

            if not forward_only:
                current = graph[right_node].get(left_node)
                if current is None or weight < current:
                    graph[right_node][left_node] = weight
                    edge_count += 1

        if show_progress:
            _render_progress(index, total_segments)

    if show_progress and total_segments > 0:
        sys.stdout.write("\n")
        sys.stdout.flush()

    node_coords_wgs84 = {
        node_id: Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True).transform(coords[0], coords[1])
        for node_id, coords in node_coords.items()
    }
    xs = [coords[0] for coords in node_coords.values()]
    ys = [coords[1] for coords in node_coords.values()]
    lons = [coord[0] for coord in node_coords_wgs84.values()]
    lats = [coord[1] for coord in node_coords_wgs84.values()]

    nodes = [
        {"node_id": node_id, "x": coords[0], "y": coords[1]}
        for node_id, coords in sorted(node_coords.items())
    ]
    graph_dict = {node_id: dict(neighbours) for node_id, neighbours in graph.items()}
    graph_payload = {
        "nodes": nodes,
        "graph": {str(node_id): {str(neighbour): weight for neighbour, weight in neighbours.items()} for node_id, neighbours in graph.items()},
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(graph_payload, file)

    graph_cache_payload = {
        "nodes_utm": node_coords,
        "nodes_wgs84": node_coords_wgs84,
        "graph": graph_dict,
        "rev_graph": _build_rev_graph(graph_dict),
        "kdtree": KDTree(list(zip(xs, ys))) if xs else None,
        "kdtree_ids": [node_id for node_id, _coords in sorted(node_coords.items())],
        "bbox_min_lon": min(lons) if lons else 0.0,
        "bbox_max_lon": max(lons) if lons else 0.0,
        "bbox_min_lat": min(lats) if lats else 0.0,
        "bbox_max_lat": max(lats) if lats else 0.0,
    }
    _write_pickle(_graph_cache_path(output_path), graph_cache_payload)

    if stage_callback is not None:
        stage_callback("building highway cache")
    build_highway_cache(graph_dict, _highway_cache_path(output_path))
    if stage_callback is not None:
        stage_callback("ok")

    elapsed_s = time.perf_counter() - started_at
    if show_progress:
        logger.info("Built %s nodes and %s directed edges.", len(nodes), edge_count)

    return {
        "n_nodes": len(nodes),
        "n_edges": edge_count,
        "elapsed_s": elapsed_s,
        "output_path": output_path,
        "graph_cache_path": _graph_cache_path(output_path),
        "highway_cache_path": _highway_cache_path(output_path),
    }


def parse_args():
    p = argparse.ArgumentParser(
        description="Build a routing graph cache from a GeoJSON road network.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", default=None, help="Input GeoJSON path")
    p.add_argument("--output", default=None, help="Output graph JSON path")
    p.add_argument("--epsg", type=int, default=None, help="Projected CRS EPSG code")
    p.add_argument("--tol", type=float, default=1.0, help="Node-snapping tolerance in metres")
    p.add_argument("--force", action="store_true", help="Rebuild even if output exists")
    return p.parse_args()


def resolve_paths(args):
    input_file = args.input or os.environ.get("GEOJSON_FILE", "data/lka_roads.geojson")
    output_file = args.output or os.environ.get("GRAPH_FILE", "export/graph.json")
    epsg = args.epsg or int(os.environ.get("EPSG", "32644"))
    return input_file, output_file, epsg


def _print_summary(output_file: str):
    try:
        if output_file.lower().endswith(".json"):
            with open(output_file, "r", encoding="utf-8") as file:
                data = json.load(file)
            n_nodes = len(data.get("graph", {}))
            n_edges = sum(len(v) for v in data.get("graph", {}).values())
        else:
            with open(output_file, "rb") as file:
                data = pickle.load(file)
            n_nodes = len(data.get("graph", {}))
            n_edges = sum(len(v) for v in data.get("graph", {}).values())
        print()
        print(f"   Existing graph: {n_nodes:,} nodes, {n_edges:,} edges")
        print(f"   File          : {output_file}")
        print()
    except Exception:
        pass


def _ensure_binary_caches(output_file: str, epsg: int) -> None:
    graph_cache_path = _graph_cache_path(output_file)
    highway_cache_path = _highway_cache_path(output_file)

    if not os.path.exists(output_file):
        return

    if not os.path.exists(graph_cache_path):
        cached = _write_graph_cache_from_json(output_file, graph_cache_path, epsg)
    else:
        cached = _load_pickle(graph_cache_path)

    if not os.path.exists(highway_cache_path):
        build_highway_cache(cached["graph"], highway_cache_path)


def main():
    print(BANNER)
    args = parse_args()
    input_file, output_file, epsg = resolve_paths(args)

    logger.info(f"Input    : {input_file}")
    logger.info(f"Output   : {output_file}")
    logger.info(f"EPSG     : {epsg}")
    logger.info(f"Tolerance: {args.tol} m")

    if not os.path.exists(input_file):
        logger.error(f"Input file not found: {input_file}")
        logger.error("Place your GeoJSON in the data/ folder, or pass --input <path>")
        sys.exit(1)

    if os.path.exists(output_file) and not args.force:
        size_mb = os.path.getsize(output_file) / 1_048_576
        logger.info(
            f"Output already exists ({size_mb:.1f} MB) — skipping graph rebuild.\n"
            f"  Ensuring binary caches are present. Use --force to rebuild the graph."
        )
        _ensure_binary_caches(output_file, epsg)
        _print_summary(output_file)
        print(f"   Graph cache   : {_graph_cache_path(output_file)}")
        print(f"   Highway cache : {_highway_cache_path(output_file)}")
        return

    logger.info("Building graph…")
    result = build_graph(
        geojson_path=input_file,
        output_path=output_file,
        epsg=epsg,
        tolerance=args.tol,
        show_progress=True,
    )

    print()
    print("✅  Graph built successfully!")
    print(f"   Nodes    : {result['n_nodes']:,}")
    print(f"   Edges    : {result['n_edges']:,}")
    print(f"   Time     : {result['elapsed_s']:.1f}s")
    print(f"   Saved to : {result['output_path']}")
    print(f"   Graph cache   : {result['graph_cache_path']}")
    print(f"   Highway cache : {result['highway_cache_path']}")
    print()
    print("Next steps:")
    print("  1. Run  docker compose up  (the server will load these files directly)")
    print("  2. Or pre-build the Highway Hierarchy index:")
    print(f"       python create_graph_cache.py --input {input_file} --output {output_file}")
    print("     Then start the container — the binary caches are also generated.")


if __name__ == "__main__":
    main()
