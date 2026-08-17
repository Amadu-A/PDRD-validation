# scripts/check-stage2.sh

#!/usr/bin/env bash

set -euo pipefail

echo "=== CONTAINERS ==="
docker compose ps

echo
echo "=== PDF SERVICE LIVE ==="
curl -fsS \
  http://localhost:${PDF_SERVICE_PORT:-8101}/health/live
echo

echo
echo "=== PDF SERVICE READY ==="
curl -fsS \
  http://localhost:${PDF_SERVICE_PORT:-8101}/health/ready
echo

echo
echo "=== OLLAMA MODELS ==="
curl -fsS \
  http://localhost:${OLLAMA_PORT:-11434}/api/tags
echo

echo
echo "=== QDRANT ==="
curl -fsS \
  http://localhost:${QDRANT_HTTP_PORT:-6333}/readyz
echo

echo
echo "=== FRONTEND ==="
curl -fsS \
  -o /dev/null \
  -w 'HTTP %{http_code}\n' \
  http://localhost:${FRONTEND_PORT:-8080}/

echo
echo "=== N8N ==="
curl -fsS \
  -o /dev/null \
  -w 'HTTP %{http_code}\n' \
  http://localhost:${N8N_PORT:-5678}/

echo
echo "Stage 2 infrastructure check: OK"