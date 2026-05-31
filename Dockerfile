FROM python:3.11-slim

# System deps for geopandas / pyproj
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

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY app/ ./app/
COPY frontend/ ./frontend/

# Create dirs for mounted data
RUN mkdir -p /app/data /app/export

# Environment defaults (override via docker-compose or -e flags)
ENV GRAPH_FILE=/app/export/graph.json
ENV GEOJSON_FILE=/app/data/roads.geojson
ENV EPSG=32644

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
