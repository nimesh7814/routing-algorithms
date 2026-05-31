import json
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from tqdm import tqdm
import os


def get_or_create_node(point, nodes, tolerance=1.0):
    for nid, coords in nodes.items():
        if abs(coords[0] - point.x) < tolerance and abs(coords[1] - point.y) < tolerance:
            return nid
    new_id = len(nodes)
    nodes[new_id] = (point.x, point.y)
    return new_id


def create_graph(input_file, output_dir, epsg=32644, tolerance=1.0):
    os.makedirs(output_dir, exist_ok=True)

    # Derive output filename from input filename
    input_name = os.path.splitext(os.path.basename(input_file))[0]
    output_file = os.path.join(output_dir, f"{input_name}_graph.json")

    # Load file
    print("Loading file...")
    gdf = gpd.read_file(input_file)
    print(f"  {len(gdf)} road segments found")

    # Reproject to meters
    print("Reprojecting to meters...")
    gdf = gdf.to_crs(epsg=epsg)

    # Extract nodes & edges
    nodes = {}
    edges = []

    print("Extracting nodes & edges...")
    for _, row in tqdm(gdf.iterrows(), total=len(gdf), desc="Processing roads", unit="seg"):
        geom = row.geometry
        if geom is None:
            continue
        lines = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
        for line in lines:
            start    = Point(line.coords[0])
            end      = Point(line.coords[-1])
            distance = line.length
            start_id = get_or_create_node(start, nodes, tolerance=tolerance)
            end_id   = get_or_create_node(end,   nodes, tolerance=tolerance)
            edges.append((start_id, end_id, round(distance, 2)))

    # Build DataFrames
    print("Building DataFrames...")
    nodes_df = pd.DataFrame(
        [(nid, x, y) for nid, (x, y) in nodes.items()],
        columns=["node_id", "x", "y"]
    )
    edges_df = pd.DataFrame(edges, columns=["from_node", "to_node", "distance_m"])

    # Build adjacency dict
    print("Building adjacency dict...")
    graph_dict = {}
    for _, row in tqdm(edges_df.iterrows(), total=len(edges_df), desc="Building graph", unit="edge"):
        u, v, d = row["from_node"], row["to_node"], row["distance_m"]
        graph_dict.setdefault(u, {})[v] = d
        graph_dict.setdefault(v, {})[u] = d

    # Save to JSON
    print(f"Saving graph to {output_file}...")
    with open(output_file, "w") as f:
        json.dump({
            "graph": {str(k): v for k, v in graph_dict.items()},
            "nodes": nodes_df.to_dict(orient="records")
        }, f)

    print(f"\n Graph created with {len(nodes_df)} nodes, {len(edges_df)} edges.")
    print(f"💾 Saved → {output_file}")

    return output_file
