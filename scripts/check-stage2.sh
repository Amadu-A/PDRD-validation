# scripts/check-stage2.sh

#!/usr/bin/env bash

set -euo pipefail


if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi


SHARED_DOCKER_NETWORK="${SHARED_DOCKER_NETWORK:-ai-shared}"
SHARED_N8N_URL="${SHARED_N8N_URL:-http://127.0.0.1:5678}"
KB_OLLAMA_URL="${KB_OLLAMA_URL:-http://127.0.0.1:11434}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://ollama:11434}"


echo "=== PDRD CONTAINERS ==="

docker compose ps


echo
echo "=== SHARED NETWORK ==="

docker network inspect \
    "${SHARED_DOCKER_NETWORK}" \
    >/dev/null

echo "${SHARED_DOCKER_NETWORK}: OK"


echo
echo "=== PDF SERVICE LIVE ==="

curl -fsS \
    "http://127.0.0.1:${PDF_SERVICE_PORT:-8101}/health/live"

echo


echo
echo "=== PDF SERVICE READY ==="

curl -fsS \
    "http://127.0.0.1:${PDF_SERVICE_PORT:-8101}/health/ready"

echo


echo
echo "=== PDF SERVICE -> SHARED OLLAMA ==="

docker compose exec -T pdf-service \
    python -c \
    "import urllib.request; response = urllib.request.urlopen('${OLLAMA_BASE_URL}/api/tags', timeout=10); print('HTTP', response.status)"

echo


echo
echo "=== SHARED OLLAMA MODELS ==="

curl -fsS \
    "${KB_OLLAMA_URL}/api/tags"

echo


echo
echo "=== PROJECT QDRANT ==="

curl -fsS \
    "http://127.0.0.1:${QDRANT_HTTP_PORT:-6333}/readyz"

echo


echo
echo "=== FRONTEND ==="

curl -fsS \
    -o /dev/null \
    -w 'HTTP %{http_code}\n' \
    "http://127.0.0.1:${FRONTEND_PORT:-8080}/"


echo
echo "=== SHARED N8N ==="

curl -fsS \
    -o /dev/null \
    -w 'HTTP %{http_code}\n' \
    "${SHARED_N8N_URL}/healthz"


echo
echo "Stage 2 infrastructure check: OK"