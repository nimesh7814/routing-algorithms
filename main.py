"""
main.py – run all four routing algorithms and compare them.

Algorithms
----------
1. Dijkstra                    (dijkstra.py)
2. Bidirectional Dijkstra      (bidirectional_dijkstra.py)
3. A* with Euclidean heuristic (astar.py)
4. Highway Hierarchies         (highway_hierarchy.py)

Pre-processing (run once, cached to disk)
-----------------------------------------
  python create_graph.py            – build road-network graph
  python highway_hierarchy.py       – HH index is built on first run automatically
"""

import os
import time

# ── configuration ─────────────────────────────────────────────────────────────
INPUT_FILE         = os.path.join("data", "cmb_roads.geojson")
OUTPUT_DIR         = "export"
EPSG               = 32644
TOLERANCE          = 1.0
SOURCE_LATLON      = (6.9271, 79.8612)
DESTINATION_LATLON = (6.8935, 79.8553)

# derived paths
input_name  = os.path.splitext(os.path.basename(INPUT_FILE))[0]
GRAPH_FILE  = os.path.join(OUTPUT_DIR, f"{input_name}_graph.json")
HH_CACHE    = os.path.join(OUTPUT_DIR, f"{input_name}_graph_hh.json")

# ── 1. build / reuse graph ────────────────────────────────────────────────────
if not os.path.exists(GRAPH_FILE):
    print("Building graph from road network...")
    from create_graph import create_graph
    create_graph(INPUT_FILE, OUTPUT_DIR, epsg=EPSG, tolerance=TOLERANCE)
else:
    print(f"Graph already exists at {GRAPH_FILE} — skipping build.\n")

# ── helper ────────────────────────────────────────────────────────────────────
def _run(label, fn, *args, **kwargs):
    print(f"  {label}")
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - t0
    print(f"\n  ⏱  Wall-clock time: {elapsed:.3f} s")
    return result


# ── 3. Dijkstra ───────────────────────────────────────────────────────────────
from dijkstra import run_dijkstra
_run("Dijkstra", run_dijkstra,
     GRAPH_FILE, SOURCE_LATLON, DESTINATION_LATLON)

# ── 4. Bidirectional Dijkstra ─────────────────────────────────────────────────
from bidirectional_dijkstra import run_bidirectional_dijkstra
_run("Bidirectional Dijkstra", run_bidirectional_dijkstra,
     GRAPH_FILE, SOURCE_LATLON, DESTINATION_LATLON)

# ── 5. A* (Euclidean heuristic) ───────────────────────────────────────────────
from astar import run_astar
_run("A* (Euclidean heuristic)", run_astar,
     GRAPH_FILE, SOURCE_LATLON, DESTINATION_LATLON)

# ── 6. Highway Hierarchies ────────────────────────────────────────────────────
from highway_hierarchy import run_highway_hierarchy
_run("Highway Hierarchies", run_highway_hierarchy,
     GRAPH_FILE, SOURCE_LATLON, DESTINATION_LATLON, HH_CACHE)
