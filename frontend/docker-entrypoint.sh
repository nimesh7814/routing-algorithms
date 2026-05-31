#!/bin/sh
# Inject the backend URL into index.html at container start time.
# The placeholder NGINX_BACKEND_URL_PLACEHOLDER is replaced with the real value.

set -e

BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"

echo "[frontend-init] BACKEND_URL = ${BACKEND_URL}"

# Replace placeholder in the HTML file
sed -i "s|NGINX_BACKEND_URL_PLACEHOLDER|${BACKEND_URL}|g" /usr/share/nginx/html/index.html

echo "[frontend-init] Starting nginx..."
exec nginx -g 'daemon off;'
