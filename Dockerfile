FROM python:3.11-slim

# ── System deps ───────────────────────────────────────────────────────────────
# gdal-bin provides ogr2ogr (shapefile → GeoJSON conversion)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    gdal-bin \
    libproj-dev \
    proj-data \
    proj-bin \
    libgeos-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps ───────────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── App source ────────────────────────────────────────────────────────────────
COPY app/         ./app/
COPY frontend/    ./frontend/
COPY create_graph.py .

# ── Entrypoint script ─────────────────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Persistent dirs (overridden by docker-compose volumes) ────────────────────
RUN mkdir -p /app/data /app/export

# ── Environment defaults (override via docker-compose or -e flags) ────────────
ENV GRAPH_FILE=/app/export/graph.json
ENV GEOJSON_FILE=/app/data/roads.geojson
ENV EPSG=32644

EXPOSE 8000

# entrypoint: build graph if missing, then start server
ENTRYPOINT ["/entrypoint.sh"]
