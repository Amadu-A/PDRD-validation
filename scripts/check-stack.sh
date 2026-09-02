# scripts/check-stack.sh

#!/usr/bin/env bash

set -euo pipefail


if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi


echo "=== Docker ==="

docker version \
    --format 'Docker server: {{.Server.Version}}'

docker compose version


echo
echo "=== Shared network ==="

docker network inspect \
    "${SHARED_DOCKER_NETWORK:-ai-shared}" \
    >/dev/null

echo "Shared network: OK"


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
    "http://127.0.0.1:${API_GATEWAY_HOST_PORT:-8200}/health/ready"

echo

curl -fsS \
    "http://127.0.0.1:${DOCUMENT_SERVICE_HOST_PORT:-8301}/health/ready"

echo

curl -fsS \
    "http://127.0.0.1:${KNOWLEDGE_SERVICE_HOST_PORT:-8401}/health/ready"

echo

curl -fsS \
    "http://127.0.0.1:${ANALYSIS_SERVICE_HOST_PORT:-8501}/health/ready"

echo

curl -fsS \
    "http://127.0.0.1:${QDRANT_HTTP_PORT:-6333}/readyz"

echo


echo
echo "=== API Gateway -> shared n8n ==="

docker compose exec -T api-gateway \
    python - <<'PY'
import urllib.request

with urllib.request.urlopen(
    "http://n8n:5678/healthz",
    timeout=10,
) as response:
    print(
        f"Shared n8n HTTP {response.status}",
    )
PY


echo
echo "STACK CHECK PASSED"