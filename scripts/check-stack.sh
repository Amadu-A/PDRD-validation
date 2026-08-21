# scripts/check-stack.sh

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


echo "=== Docker ==="

docker version \
    --format 'Docker server: {{.Server.Version}}'

docker compose version


echo
echo "=== Shared network ==="

docker network inspect \
    "${SHARED_DOCKER_NETWORK}" \
    >/dev/null

echo "Network ${SHARED_DOCKER_NETWORK}: OK"


echo
echo "=== PDRD containers ==="

docker compose ps


echo
echo "=== PDRD services ==="

curl -fsS \
    -o /dev/null \
    -w 'Frontend HTTP %{http_code}\n' \
    "http://127.0.0.1:${FRONTEND_PORT:-8080}/"

curl -fsS \
    -o /dev/null \
    -w 'PDF service HTTP %{http_code}\n' \
    "http://127.0.0.1:${PDF_SERVICE_PORT:-8101}/health/live"

curl -fsS \
    "http://127.0.0.1:${QDRANT_HTTP_PORT:-6333}/readyz"

echo


echo
echo "=== Shared services ==="

curl -fsS \
    -o /dev/null \
    -w 'Shared n8n HTTP %{http_code}\n' \
    "${SHARED_N8N_URL}/healthz"

curl -fsS \
    "${KB_OLLAMA_URL}/api/tags" \
    >/dev/null

echo "Shared Ollama HTTP: OK"


echo
echo "STACK CHECK PASSED"