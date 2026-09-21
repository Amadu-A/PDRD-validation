#!/usr/bin/env bash
# scripts/check-stack.sh
#
# Проверка runtime-состояния полного PDRD stack.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_DIR}"

if [[ -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source ".env"
    set +a
fi

fail=0

ok() {
    printf '[OK]   %s\n' "$1"
}

bad() {
    printf '[FAIL] %s\n' "$1"
    fail=1
}

check_http() {
    local name="$1"
    local url="$2"

    if curl -fsS "${url}" >/dev/null 2>&1; then
        ok "${name}"
    else
        bad "${name}"
    fi
}

check_service_state() {
    local service="$1"
    local container_id
    local state

    container_id="$(
        docker compose ps \
            --all \
            --quiet \
            "${service}" \
            2>/dev/null \
            || true
    )"

    if [[ -z "${container_id}" ]]; then
        bad "${service}: container missing"
        return
    fi

    state="$(
        docker inspect \
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
            "${container_id}" \
            2>/dev/null \
            || true
    )"

    case "${state}" in
        healthy|running)
            ok "${service}: ${state}"
            ;;
        *)
            bad "${service}: ${state:-unknown}"
            ;;
    esac
}

check_completed_service() {
    local service="$1"
    local container_id
    local state
    local exit_code

    container_id="$(
        docker compose ps \
            --all \
            --quiet \
            "${service}" \
            2>/dev/null \
            || true
    )"

    if [[ -z "${container_id}" ]]; then
        bad "${service}: container missing"
        return
    fi

    state="$(
        docker inspect \
            --format '{{.State.Status}}' \
            "${container_id}" \
            2>/dev/null \
            || true
    )"

    exit_code="$(
        docker inspect \
            --format '{{.State.ExitCode}}' \
            "${container_id}" \
            2>/dev/null \
            || true
    )"

    if [[ "${state}" == "exited" && "${exit_code}" == "0" ]]; then
        ok "${service}: completed"
    else
        bad "${service}: state=${state:-unknown} exit=${exit_code:-unknown}"
    fi
}

echo "=== Docker ==="

if docker info >/dev/null 2>&1; then
    ok "Docker daemon"
else
    bad "Docker daemon"
    echo "STACK CHECK FAILED"
    exit 1
fi

if docker compose config --quiet >/dev/null 2>&1; then
    ok "Compose config"
else
    bad "Compose config"
fi

echo
echo "=== Shared network ==="

if docker network inspect \
    "${SHARED_DOCKER_NETWORK:-ai-shared}" \
    >/dev/null 2>&1; then

    ok "Network ${SHARED_DOCKER_NETWORK:-ai-shared}"
else
    bad "Network ${SHARED_DOCKER_NETWORK:-ai-shared}"
fi

echo
echo "=== PDRD containers ==="

docker compose ps || fail=1

echo
echo "=== Container states ==="

check_service_state "postgres"
check_service_state "qdrant"
check_service_state "multimodal-embedding-service"

check_completed_service "knowledge-embedding-migrator"

check_service_state "api-gateway"
check_service_state "api-gateway-outbox"
check_service_state "api-gateway-worker"

check_service_state "document-service"

check_service_state "knowledge-service"
check_service_state "knowledge-service-outbox"
check_service_state "knowledge-indexer"
check_service_state "technical-assignment-indexer"

check_service_state "analysis-service"
check_service_state "frontend"

echo
echo "=== HTTP services ==="

check_http \
    "Frontend HTTP" \
    "http://127.0.0.1:${FRONTEND_PORT:-8080}/"

check_http \
    "Frontend -> API Gateway proxy" \
    "http://127.0.0.1:${FRONTEND_PORT:-8080}/api/v1/normative/sections"

check_http \
    "API Gateway ready" \
    "http://127.0.0.1:${API_GATEWAY_HOST_PORT:-8200}/health/ready"

check_http \
    "Document Service ready" \
    "http://127.0.0.1:${DOCUMENT_SERVICE_HOST_PORT:-8301}/health/ready"

check_http \
    "Knowledge Service ready" \
    "http://127.0.0.1:${KNOWLEDGE_SERVICE_HOST_PORT:-8401}/health/ready"

check_http \
    "Analysis Service ready" \
    "http://127.0.0.1:${ANALYSIS_SERVICE_HOST_PORT:-8501}/health/ready"

check_http \
    "Qdrant ready" \
    "http://127.0.0.1:${QDRANT_HTTP_PORT:-6333}/readyz"

echo
echo "=== Shared services ==="

if docker compose exec \
    -T \
    api-gateway \
    python3 \
    -c 'import urllib.request; urllib.request.urlopen("http://n8n:5678/healthz", timeout=10)' \
    >/dev/null 2>&1; then

    ok "API Gateway -> n8n"
else
    bad "API Gateway -> n8n"
fi

if docker compose exec \
    -T \
    analysis-service \
    python3 \
    -c 'import urllib.request; urllib.request.urlopen("http://shared-vlm:8000/health", timeout=10)' \
    >/dev/null 2>&1; then

    ok "Analysis Service -> shared-vlm health"
else
    bad "Analysis Service -> shared-vlm health"
fi

if docker compose exec \
    -T \
    analysis-service \
    python3 \
    -c 'import json, urllib.request; payload=json.load(urllib.request.urlopen("http://shared-vlm:8000/v1/models", timeout=10)); assert any(isinstance(item, dict) and item.get("id") == "shared-vlm" for item in payload.get("data", []))' \
    >/dev/null 2>&1; then

    ok "Analysis Service -> shared-vlm model alias"
else
    bad "Analysis Service -> shared-vlm model alias"
fi

echo

if [[ "${fail}" -eq 0 ]]; then
    echo "STACK CHECK PASSED"
else
    echo "STACK CHECK FAILED"
fi

exit "${fail}"