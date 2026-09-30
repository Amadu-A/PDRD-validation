#!/usr/bin/env bash
# scripts/check-stack.sh
#
# Проверка runtime-состояния PDRD, включая уже запущенные optional services.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_DIR}"

if [[ -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source ".env"
    set +a
fi

# shellcheck source=scripts/lib/stack-profiles.sh
source "${REPO_DIR}/scripts/lib/stack-profiles.sh"

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
    local status

    if status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${url}" 2>/dev/null)" \
        && [[ "${status}" == 2?? ]]; then
        ok "${name}"
    else
        bad "${name}: HTTP ${status:-000}"
    fi
}

check_service_state() {
    local service="$1"
    local profile="${2:-}"
    local container_id
    local state
    local -a compose_args=(compose)

    if [[ -n "${profile}" ]]; then
        compose_args+=(--profile "${profile}")
    fi

    container_id="$(
        docker "${compose_args[@]}" ps \
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
    local profile="${2:-}"
    local container_id
    local state
    local exit_code
    local -a compose_args=(compose)

    if [[ -n "${profile}" ]]; then
        compose_args+=(--profile "${profile}")
    fi

    container_id="$(
        docker "${compose_args[@]}" ps \
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

check_migrations_current() {
    local service="$1"
    local profile="$2"
    local name="$3"

    # Одноразовый migrator может удаляться через `run --rm`. Проверяем схему БД,
    # а не существование его контейнера после успешного развёртывания.
    if docker compose --profile "${profile}" exec \
        -T \
        "${service}" \
        python -m alembic -c alembic.ini current --check-heads \
        >/dev/null 2>&1; then

        ok "${name}: migrations current"
    else
        bad "${name}: migrations not at head"
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
echo "=== Optional profiles ==="

review_active=0
identity_active=0
experience_index_active=0

if optional_profile_active "review" \
    "experience-migrate" "experience-service" "review-frontend"; then
    review_active=1
    ok "review: selected or containers present"
else
    echo "[SKIP] review: profile not selected and no containers"
fi

if optional_profile_active "identity" "user-migrate" "user-service"; then
    identity_active=1
    ok "identity: selected or containers present"
else
    echo "[SKIP] identity: profile not selected and no containers"
fi

if optional_profile_active "experience-index" "experience-indexer"; then
    experience_index_active=1
    ok "experience-index: selected or containers present"
else
    echo "[SKIP] experience-index: profile not selected and no containers"
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

if (( review_active )); then
    check_service_state "experience-service" "review"
    check_migrations_current "experience-service" "review" "Experience Service"
    check_service_state "review-frontend" "review"
fi

if (( identity_active )); then
    check_service_state "user-service" "identity"
    check_migrations_current "user-service" "identity" "User Service"
fi

if (( experience_index_active )); then
    check_service_state "experience-indexer" "experience-index"
fi

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

if (( review_active )); then
    if docker compose --profile review exec \
        -T \
        experience-service \
        python3 \
        -c 'import json, urllib.request; payload=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=5)); assert payload.get("status") == "ready"' \
        >/dev/null 2>&1; then

        ok "Experience Service ready (internal)"
    else
        bad "Experience Service ready (internal)"
    fi

    check_http \
        "Review frontend HTTP" \
        "http://127.0.0.1:${REVIEW_FRONTEND_PORT:-8081}/"

    check_http \
        "Review frontend -> API Gateway proxy" \
        "http://127.0.0.1:${REVIEW_FRONTEND_PORT:-8081}/api/v1/review/config"
fi

if (( identity_active )); then
    if docker compose --profile identity exec \
        -T \
        user-service \
        python3 \
        -c 'import json, urllib.request; payload=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=5)); assert payload.get("status") == "ready"' \
        >/dev/null 2>&1; then

        ok "User Service ready (internal)"
    else
        bad "User Service ready (internal)"
    fi
fi

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

if docker compose exec \
    -T \
    knowledge-service \
    python3 \
    -c 'import urllib.request; urllib.request.urlopen("http://shared-embedding:8000/health", timeout=10)' \
    >/dev/null 2>&1; then

    ok "Knowledge Service -> shared-embedding health"
else
    bad "Knowledge Service -> shared-embedding health"
fi

if docker compose exec \
    -T \
    knowledge-service \
    python3 \
    -c 'import json, urllib.request; payload=json.load(urllib.request.urlopen("http://shared-embedding:8000/v1/models", timeout=10)); assert any(isinstance(item, dict) and item.get("id") == "shared-embedding" for item in payload.get("data", []))' \
    >/dev/null 2>&1; then

    ok "Knowledge Service -> shared-embedding model alias"
else
    bad "Knowledge Service -> shared-embedding model alias"
fi

echo

if [[ "${fail}" -eq 0 ]]; then
    echo "STACK CHECK PASSED"
else
    echo "STACK CHECK FAILED"
fi

exit "${fail}"
