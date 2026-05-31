# Pathfinder

Interactive shortest-path explorer with animated search frontiers. Drop in a road network file, run `docker compose up`, and watch Dijkstra, A\*, Bidirectional Dijkstra, and Highway Hierarchies race each other across your map.

Built with **FastAPI** + **MapLibre GL** in a single Docker container. The routing graph is built automatically on first start and cached so subsequent restarts are instant.

---

## Quick start

### 1. Get your road network file

You need a file containing road geometries — either a **GeoJSON** or an **ESRI Shapefile**. The features must be `LineString` or `MultiLineString` type.

**Option A — Download from OpenStreetMap via Overpass Turbo**

Go to [overpass-turbo.eu](https://overpass-turbo.eu), run a query like the one below, then export as GeoJSON:

```
[out:json][timeout:60];
(
  way["highway"]({{bbox}});
);
out body geom;
```

**Option B — Export with osmnx (Python)**

```python
import osmnx as ox
G = ox.graph_from_place("Colombo, Sri Lanka", network_type="drive")
ox.save_graph_geojson(G, filepath="data/roads.geojson")
```

**Option C — Use an existing Shapefile**

Any road network `.shp` file works. Place it in `data/` alongside its sidecar files (`.dbf`, `.shx`, `.prj`). The container converts it to GeoJSON automatically on first run.

---

### 2. Place the file in `data/`

```
pathfinder/
└── data/
    └── roads.geojson     ← GeoJSON (or roads.shp + sidecar files)
```

The container mounts `./data` as read-only. It will **auto-detect** any `.geojson` or `.shp` file it finds there, so no configuration is required for a single file.

---

### 3. Set your EPSG projection

Pathfinder works in a projected CRS (metres) for accurate distance calculations. Open `docker-compose.yml` and set the UTM zone for your region:

```yaml
environment:
  - EPSG=32644   # ← change this
```

| EPSG  | Zone       | Covers                        |
|-------|------------|-------------------------------|
| 32644 | UTM 44N    | Sri Lanka, South Asia (default) |
| 32643 | UTM 43N    | Pakistan, NW India            |
| 32632 | UTM 32N    | Central Europe (Germany, Italy) |
| 32631 | UTM 31N    | France, Spain, W. Europe      |
| 32630 | UTM 30N    | UK, Ireland                   |
| 32618 | UTM 18N    | Eastern USA                   |
| 32614 | UTM 14N    | Central USA                   |
| 32754 | UTM 54S    | Eastern Australia, Japan      |

Find any zone at [spatialreference.org](https://spatialreference.org/).

---

### 4. Build and run

```bash
docker compose up --build
```

Open **http://localhost:8000**

**What happens on first run:**

1. The container checks whether `export/graph.json` exists.
2. If not, it builds the graph from your input file (this can take 30 seconds to a few minutes depending on network size).
3. The graph is saved to `./export/graph.json` on your host machine.
4. The FastAPI server starts and the app is ready.

On every subsequent `docker compose up` the graph is loaded directly from the cache — startup takes a second or two.

---

## Configuration reference

All settings are environment variables. Set them in `docker-compose.yml` under `environment:`.

### Input file

| Variable      | Default                    | Description |
|---------------|----------------------------|-------------|
| `GEOJSON_FILE` | `/app/data/roads.geojson` | Path to GeoJSON input file inside the container |
| `SHP_FILE`    | *(unset)*                  | Path to Shapefile input. Set this to use a `.shp` instead of GeoJSON. The container converts it automatically. |

The entrypoint resolves the input in this priority order:

1. `SHP_FILE` (if set and file exists)
2. `GEOJSON_FILE` (if file exists at that path)
3. Auto-detect: first `.geojson` or `.json` found in `/app/data`
4. Auto-detect: first `.shp` found in `/app/data`

If no file is found the container exits with a clear error message.

### Graph / projection

| Variable     | Default                      | Description |
|--------------|------------------------------|-------------|
| `EPSG`       | `32644`                      | EPSG code for the projected CRS used during graph construction. Must match the geographic region of your input data. |
| `GRAPH_FILE` | `/app/export/graph.json`     | Path inside the container where the built graph is stored and loaded from. Maps to `./export/graph.json` on the host via the volume mount. |

---

## Using a Shapefile

Add the `.shp` and all its sidecar files (`.dbf`, `.shx`, `.prj`, optionally `.cpg`) to `data/`:

```
data/
├── roads.shp
├── roads.dbf
├── roads.shx
└── roads.prj
```

Then in `docker-compose.yml`, uncomment and set `SHP_FILE`:

```yaml
environment:
  - SHP_FILE=/app/data/roads.shp
  - EPSG=32632   # set your region
```

The container calls `ogr2ogr` internally to reproject and convert the shapefile to GeoJSON before building the graph. The converted file is saved to `export/roads_converted.geojson` for reference.

---

## Rebuilding the graph

Delete `graph.json` from the `export/` folder on your host and restart:

```bash
rm export/graph.json
docker compose up
```

To also force a rebuild of the Highway Hierarchy index:

```bash
rm export/graph.json export/*_hh.json
docker compose up
```

You can also rebuild outside the container using the standalone CLI tool:

```bash
# Minimal
python create_graph.py

# Explicit paths and projection
python create_graph.py --input data/roads.geojson --output export/graph.json --epsg 32632

# Force rebuild even if output already exists
python create_graph.py --force

# Adjust node-snapping tolerance (default 1.0 m; increase if your network has gaps)
python create_graph.py --tol 2.5
```

---

## Using the app

1. **Set source and destination** — click two points on the map, or type WGS84 coordinates (lat, lon) into the input fields.
2. **Select an algorithm** from the panel on the left.
3. Click **Find Path**.
4. Watch the search frontier animate across the map. The shortest path is drawn in green when complete.
5. Stats (distance, nodes expanded, time) are shown below the controls.

### Algorithms

| Algorithm | Description | Best for |
|---|---|---|
| **Dijkstra** | Uniform-cost, explores all directions | Baseline; always finds the optimal path |
| **Bidirectional Dijkstra** | Two frontiers (blue forward, orange backward) meeting in the middle | Faster than Dijkstra on most queries |
| **A\* (Euclidean)** | Heuristic-guided toward the target | Fewer nodes explored; same result as Dijkstra |
| **Highway Hierarchies** | Restricts search to important edges outside the local neighbourhood | Fastest for long routes; HH index built and cached on first use |

---

## API reference

The server exposes two endpoints.

### `GET /api/info`

Returns metadata about the loaded graph.

```json
{
  "nodes": 18432,
  "edges": 41205,
  "hh_ready": false
}
```

### `POST /api/route`

Run a routing query.

**Request body:**

```json
{
  "source_lat": 6.9271,
  "source_lon": 79.8612,
  "dest_lat":   6.8935,
  "dest_lon":   79.8553,
  "algorithm":  "astar"
}
```

`algorithm` must be one of: `dijkstra`, `bidirectional`, `astar`, `highway`.

**Response (abbreviated):**

```json
{
  "success":        true,
  "distance_m":     5432.1,
  "distance_km":    5.432,
  "nodes_expanded": 1203,
  "time_ms":        42.3,
  "path_coords":    [[6.927, 79.861], ["..."]],
  "steps":          [{"type": "node", "id": 123, "direction": "fwd"}, "..."],
  "node_coords":    {"123": [6.927, 79.861], "...": "..."}
}
```

---

## Project structure

```
pathfinder/
├── Dockerfile               # Container image definition
├── docker-compose.yml       # Service, volumes, and environment config
├── entrypoint.sh            # Init script: builds graph if missing, then starts server
├── create_graph.py          # Standalone CLI graph builder (also called by entrypoint)
├── requirements.txt
├── app/
│   ├── main.py              # FastAPI app, static file mount, route handlers
│   ├── graph_store.py       # Singleton: loads graph.json into memory on startup
│   ├── models.py            # Pydantic request / response schemas
│   └── algorithms/
│       ├── dijkstra.py
│       ├── bidirectional.py
│       ├── astar.py
│       └── highway.py       # Highway Hierarchies; builds *_hh.json index on first use
├── frontend/
│   ├── index.html
│   ├── app.js               # MapLibre GL map + animated frontier rendering
│   └── style.css
├── data/                    # Mount your input file(s) here (read-only inside container)
└── export/                  # graph.json and *_hh.json are written and cached here
```

---

## Troubleshooting

**Container exits immediately with "No input file found"**
No road network file was found in `./data`. Check that the file is there and that the `data` volume is mounted correctly in `docker-compose.yml`.

**"EPSG" projection errors during graph build**
Your input data may be in a different geographic region than the configured EPSG zone. Check [spatialreference.org](https://spatialreference.org/) for the correct code and update `docker-compose.yml`.

**Highway Hierarchies returns no path or errors**
The HH index is built lazily on the first HH query. This can take a minute for large networks. The index is cached to `export/*_hh.json` — delete that file and restart to rebuild it.

**Gaps in the road network / disconnected graph**
Increase the node-snapping tolerance when running `create_graph.py`:
```bash
python create_graph.py --tol 5.0 --force
```
Or set `--tol` higher if your source data has coordinate imprecision.
