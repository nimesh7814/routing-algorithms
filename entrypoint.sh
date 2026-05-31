#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# entrypoint.sh  —  Pathfinder container init
#
# 1. If GRAPH_FILE does not exist, resolve the input (GeoJSON or Shapefile),
#    convert .shp → .geojson if necessary, then build the graph.
# 2. Start the FastAPI server.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GRAPH_FILE="${GRAPH_FILE:-/app/export/graph.json}"
GEOJSON_FILE="${GEOJSON_FILE:-/app/data/roads.geojson}"
SHP_FILE="${SHP_FILE:-}"          # optional: explicit shapefile path
EPSG="${EPSG:-32644}"
DATA_DIR="/app/data"
EXPORT_DIR="/app/export"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║       Pathfinder — Container Init    ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Ensure export dir exists (volume may be empty on first run) ───────────────
mkdir -p "${EXPORT_DIR}"

# ── Resolve input file ────────────────────────────────────────────────────────
resolve_input() {
    # Priority:
    #   1. Explicit SHP_FILE env var
    #   2. Explicit GEOJSON_FILE env var (if it exists on disk)
    #   3. Auto-detect: first .geojson found in /app/data
    #   4. Auto-detect: first .shp found in /app/data  (convert to geojson)

    if [[ -n "${SHP_FILE}" && -f "${SHP_FILE}" ]]; then
        echo "${SHP_FILE}"
        return
    fi

    if [[ -f "${GEOJSON_FILE}" ]]; then
        echo "${GEOJSON_FILE}"
        return
    fi

    # Auto-detect GeoJSON
    local found_geojson
    found_geojson=$(find "${DATA_DIR}" -maxdepth 2 \( -iname "*.geojson" -o -iname "*.json" \) | head -1 || true)
    if [[ -n "${found_geojson}" ]]; then
        echo "${found_geojson}"
        return
    fi

    # Auto-detect Shapefile
    local found_shp
    found_shp=$(find "${DATA_DIR}" -maxdepth 2 -iname "*.shp" | head -1 || true)
    if [[ -n "${found_shp}" ]]; then
        echo "${found_shp}"
        return
    fi

    echo ""  # nothing found
}

# ── Convert shapefile → GeoJSON ───────────────────────────────────────────────
convert_shp_to_geojson() {
    local shp_path="$1"
    local out_path="${EXPORT_DIR}/roads_converted.geojson"

    echo "[init] Shapefile detected: ${shp_path}"
    echo "[init] Converting to GeoJSON → ${out_path}"

    ogr2ogr \
        -f GeoJSON \
        -t_srs EPSG:4326 \
        "${out_path}" \
        "${shp_path}"

    echo "[init] Conversion complete."
    echo "${out_path}"
}

# ── Main init logic ───────────────────────────────────────────────────────────
if [[ -f "${GRAPH_FILE}" ]]; then
    echo "[init] Graph already exists at ${GRAPH_FILE} — skipping build."
else
    echo "[init] No graph found at ${GRAPH_FILE} — building now..."

    INPUT_FILE=$(resolve_input)

    if [[ -z "${INPUT_FILE}" ]]; then
        echo ""
        echo "ERROR: No input file found."
        echo "  Mount a .geojson or .shp road-network file into /app/data/"
        echo "  or set GEOJSON_FILE / SHP_FILE environment variables."
        echo ""
        exit 1
    fi

    echo "[init] Input file : ${INPUT_FILE}"

    # Convert shapefile if needed
    case "${INPUT_FILE,,}" in
        *.shp)
            INPUT_FILE=$(convert_shp_to_geojson "${INPUT_FILE}")
            ;;
    esac

    echo "[init] EPSG       : ${EPSG}"
    echo "[init] Output     : ${GRAPH_FILE}"
    echo ""

    python /app/create_graph.py \
        --input  "${INPUT_FILE}" \
        --output "${GRAPH_FILE}" \
        --epsg   "${EPSG}"

    echo ""
    echo "[init] Graph build complete."
fi

# ── Start the server ──────────────────────────────────────────────────────────
echo "[init] Starting Pathfinder server on :8000 ..."
echo ""
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1
