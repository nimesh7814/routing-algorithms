import json
import math
from heapq import heapify, heappop, heappush
from pyproj import Transformer

# Reusable transformer: WGS84 (lat/lon) → UTM Zone 44N (EPSG:32644)
_transformer = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)


def load_graph(graph_file):
    """Load graph and nodes from a saved JSON file."""
    print("Loading graph...")
    with open(graph_file, "r") as f:
        data = json.load(f)

    graph_dict = {
        int(float(k)): {int(float(nk)): nv for nk, nv in v.items()}
        for k, v in data["graph"].items()
    }
    nodes = {int(float(row["node_id"])): (row["x"], row["y"]) for row in data["nodes"]}

    print(f" Loaded {len(graph_dict)} nodes, {sum(len(v) for v in graph_dict.values())} edges")
    return graph_dict, nodes


def latlon_to_xy(lat, lon):
    """Convert WGS84 (lat/lon) → UTM Zone 44N (EPSG:32644) using pyproj."""
    x, y = _transformer.transform(lon, lat)  # always_xy=True → (lon, lat) order
    return x, y


def find_nearest_node(x, y, nodes):
    """Find the closest graph node to a projected (x, y) coordinate."""
    nearest_id   = None
    nearest_dist = float("inf")
    for nid, (nx, ny) in nodes.items():
        dist = math.sqrt((nx - x) ** 2 + (ny - y) ** 2)
        if dist < nearest_dist:
            nearest_dist = dist
            nearest_id   = nid
    return nearest_id, nearest_dist


def snap_to_node(latlon, nodes, label="Point"):
    """Convert a (lat, lon) to the nearest graph node ID."""
    x, y = latlon_to_xy(*latlon)
    node_id, dist = find_nearest_node(x, y, nodes)
    print(f"  {label:12} {latlon}  →  node {node_id}  ({dist:.1f}m away)")
    return node_id


class Graph:
    def __init__(self, graph):
        self.graph = graph

    def shortest_distances(self, source):
        distances         = {node: float("inf") for node in self.graph}
        distances[source] = 0
        pq                = [(0, source)]
        heapify(pq)
        visited      = set()
        predecessors = {node: None for node in self.graph}

        while pq:
            current_distance, current_node = heappop(pq)
            if current_node in visited:
                continue
            visited.add(current_node)
            for neighbor, weight in self.graph[current_node].items():
                tentative = current_distance + weight
                if tentative < distances[neighbor]:
                    distances[neighbor]    = tentative
                    predecessors[neighbor] = current_node
                    heappush(pq, (tentative, neighbor))

        return distances, predecessors

    def shortest_path(self, source, target):
        distances, predecessors = self.shortest_distances(source)

        if distances[target] == float("inf"):
            return None, float("inf")

        path         = []
        current_node = target
        while current_node is not None:
            path.append(current_node)
            current_node = predecessors[current_node]
        path.reverse()

        return path, distances[target]


def run_dijkstra(graph_file, source_latlon, destination_latlon):
    """Full pipeline: load graph, snap coords, run Dijkstra, print results."""
    graph_dict, nodes = load_graph(graph_file)

    print("\nSnapping coordinates to nearest nodes...")
    source_node = snap_to_node(source_latlon,      nodes, label="Source")
    target_node = snap_to_node(destination_latlon, nodes, label="Destination")

    print("\nRunning Dijkstra...")
    G = Graph(graph=graph_dict)
    path, total_distance = G.shortest_path(source_node, target_node)

    if path is None:
        print("❌ No path found between the two points.")
    else:
        print(f"\n✅ Shortest path found!")
        print(f"   Total distance : {total_distance / 1000:.2f} km  ({total_distance:.0f} m)")
        print(f"   Nodes in path  : {len(path)}")
        print(f"   Node sequence  : {path}")

    return path, total_distance