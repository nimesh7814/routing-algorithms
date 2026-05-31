"""
create_graph.py
═══════════════
Standalone CLI script to build a road-network graph JSON from a GeoJSON file.

Run this once before starting the Docker container, or whenever you have a new
road network file. The output is cached in export/ and loaded directly by the
FastAPI server on startup — skipping the build step entirely.

Usage
─────
  # Minimal (uses .env / defaults)
  python create_graph.py

  # Explicit paths
  python create_graph.py --input data/cmb_roads.geojson --output export/cmb_roads_graph.json

  # Different projection (e.g. Central Europe)
  python create_graph.py --input data/berlin.geojson --epsg 32633

  # Force rebuild even if output already exists
  python create_graph.py --force

Options
───────
  --input   PATH    Input GeoJSON file          [default: data/roads.geojson]
  --output  PATH    Output graph JSON file       [default: export/graph.json]
  --epsg    INT     Projected CRS EPSG code      [default: 32644]
  --tol     FLOAT   Node-snapping tolerance (m)  [default: 1.0]
  --force           Overwrite existing output

EPSG quick-reference
────────────────────
  32644  UTM Zone 44N  — Sri Lanka / South Asia      (default)
  32643  UTM Zone 43N  — Pakistan / NW India
  32632  UTM Zone 32N  — Central Europe (Germany, Italy…)
  32630  UTM Zone 30N  — UK / Ireland
  32618  UTM Zone 18N  — Eastern USA
  32754  UTM Zone 54S  — Eastern Australia / Japan

Find your zone: https://spatialreference.org/
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from math import hypot

from pyproj import Transformer

# ── Allow running from project root without installing the package ─────────────
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


def build_graph(geojson_path: str, output_path: str, epsg: int = 32644, tolerance: float = 1.0, show_progress: bool = True):
    started_at = time.perf_counter()
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)

    with open(geojson_path, "r", encoding="utf-8") as file:
        geojson = json.load(file)

    total_segments = _count_line_segments(geojson)

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

    nodes = [
        {"node_id": node_id, "x": coords[0], "y": coords[1]}
        for node_id, coords in sorted(node_coords.items())
    ]
    graph_payload = {
        "nodes": nodes,
        "graph": {str(node_id): {str(neighbour): weight for neighbour, weight in neighbours.items()} for node_id, neighbours in graph.items()},
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(graph_payload, file)

    elapsed_s = time.perf_counter() - started_at
    if show_progress:
        logger.info("Built %s nodes and %s directed edges.", len(nodes), edge_count)

    return {
        "n_nodes": len(nodes),
        "n_edges": edge_count,
        "elapsed_s": elapsed_s,
        "output_path": output_path,
    }


def parse_args():
    p = argparse.ArgumentParser(
        description="Build a routing graph JSON from a GeoJSON road network.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input",  default=None, help="Input GeoJSON path")
    p.add_argument("--output", default=None, help="Output graph JSON path")
    p.add_argument("--epsg",   type=int, default=None, help="Projected CRS EPSG code")
    p.add_argument("--tol",    type=float, default=1.0, help="Node-snapping tolerance in metres")
    p.add_argument("--force",  action="store_true", help="Rebuild even if output exists")
    return p.parse_args()


def resolve_paths(args):
    """
    Priority order for each setting:
      1. CLI argument
      2. Environment variable (same as docker-compose uses)
      3. Sensible default
    """
    input_file  = args.input  or os.environ.get("GEOJSON_FILE", "data/roads.geojson")
    output_file = args.output or os.environ.get("GRAPH_FILE",   "export/graph.json")
    epsg        = args.epsg   or int(os.environ.get("EPSG", "32644"))
    return input_file, output_file, epsg


def main():
    print(BANNER)
    args = parse_args()
    input_file, output_file, epsg = resolve_paths(args)

    logger.info(f"Input    : {input_file}")
    logger.info(f"Output   : {output_file}")
    logger.info(f"EPSG     : {epsg}")
    logger.info(f"Tolerance: {args.tol} m")

    # ── Validate input ────────────────────────────────────────────────────────
    if not os.path.exists(input_file):
        logger.error(f"Input file not found: {input_file}")
        logger.error("Place your GeoJSON in the data/ folder, or pass --input <path>")
        sys.exit(1)

    # ── Skip if output already exists (unless --force) ────────────────────────
    if os.path.exists(output_file) and not args.force:
        size_mb = os.path.getsize(output_file) / 1_048_576
        logger.info(
            f"Output already exists ({size_mb:.1f} MB) — skipping build.\n"
            f"  Use --force to rebuild."
        )
        _print_summary(output_file)
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
    print()
    print("Next steps:")
    print("  1. Run  docker compose up  (the server will load this file directly)")
    print("  2. Or pre-build the Highway Hierarchy index:")
    print(f"       python create_graph.py --input {input_file} --output {output_file}")
    print(f"     Then start the container — HH builds automatically on first HH query.")


def _print_summary(output_file):
    """Quick peek at an existing graph file."""
    import json
    try:
        with open(output_file) as f:
            data = json.load(f)
        n_nodes = len(data.get("graph", {}))
        n_edges = sum(len(v) for v in data.get("graph", {}).values())
        print()
        print(f"   Existing graph: {n_nodes:,} nodes, {n_edges:,} edges")
        print(f"   File          : {output_file}")
        print()
    except Exception:
        pass


if __name__ == "__main__":
    main()
