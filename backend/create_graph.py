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
import logging
import os
import sys

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

    # ── Build ─────────────────────────────────────────────────────────────────
    from app.core.builder import build_graph

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
