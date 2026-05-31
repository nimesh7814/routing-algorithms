# Pathfinder

Interactive shortest-path explorer with animated search frontiers.
Built with FastAPI (Python) + MapLibre GL (frontend) in a single Docker container.

## Algorithms

| Algorithm | Description |
|---|---|
| Dijkstra | Uniform-cost search, explores all directions |
| Bidirectional Dijkstra | Two simultaneous frontiers meeting in the middle |
| A* (Euclidean) | Heuristic-guided search, fewer nodes explored |
| Highway Hierarchies | Restricts search to "important" edges for long routes |

## Quick Start

### 1. Place your GeoJSON road file

```
pathfinder/
└── data/
    └── roads.geojson     ← your road network here
```

The file should contain `LineString` or `MultiLineString` features.
Any GeoJSON exported from OpenStreetMap (e.g. via Overpass or osmnx) works.

### 2. Set your EPSG projection

Edit `docker-compose.yml` and set the correct UTM zone for your region:

```yaml
environment:
  - EPSG=32644      # UTM Zone 44N — Sri Lanka / South Asia
  # - EPSG=32632    # UTM Zone 32N — Central Europe
  # - EPSG=32618    # UTM Zone 18N — Eastern USA
  # - EPSG=32754    # UTM Zone 54S — Eastern Australia
```

Find your UTM zone: https://spatialreference.org/

### 3. Build and run

```bash
docker compose up --build
```

Open http://localhost:8000

On first run the graph is built from your GeoJSON and cached to `export/`.
Subsequent starts load directly from the cache (fast).
The Highway Hierarchy index (`*_hh.json`) is also built and cached on first use.

### 4. Use the app

- **Click the map** to set source (A) and destination (B) pins, or
- **Type coordinates** (WGS84 lat/lon) directly into the fields
- Select an algorithm and click **Find Path**
- Watch the search frontier animate in real time
- The shortest path appears in green when animation completes

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GRAPH_FILE` | `/app/export/graph.json` | Path to cached graph JSON |
| `GEOJSON_FILE` | `/app/data/roads.geojson` | Input road network GeoJSON |
| `EPSG` | `32644` | Projected CRS EPSG code for your region |

## Rebuild the graph

Delete `export/graph.json` (and `export/*_hh.json` to rebuild Highway Hierarchy) then restart:

```bash
rm export/graph.json export/*_hh.json
docker compose up
```

## API Endpoints

```
GET  /api/info          — graph metadata (nodes, edges, HH status)
POST /api/route         — run a routing query
```

### POST /api/route

```json
{
  "source_lat": 6.9271,
  "source_lon": 79.8612,
  "dest_lat": 6.8935,
  "dest_lon": 79.8553,
  "algorithm": "astar"   // dijkstra | bidirectional | astar | highway
}
```

### Response (abbreviated)

```json
{
  "success": true,
  "distance_m": 5432.1,
  "distance_km": 5.432,
  "nodes_expanded": 1203,
  "time_ms": 42.3,
  "path_coords": [[lat, lon], ...],
  "steps": [{"type": "node", "id": 123, "direction": "fwd"}, ...],
  "node_coords": {"123": [lat, lon], ...}
}
```

## Project Structure

```
pathfinder/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── app/
│   ├── main.py              # FastAPI app + static mount
│   ├── graph_store.py       # Singleton: load/build graph on startup
│   ├── models.py            # Pydantic schemas
│   └── algorithms/
│       ├── dijkstra.py      # Instrumented — records exploration steps
│       ├── bidirectional.py
│       ├── astar.py
│       └── highway.py
├── frontend/
│   ├── index.html
│   ├── app.js               # MapLibre GL + animation engine
│   └── style.css
└── data/                    # Mount your GeoJSON here
```
