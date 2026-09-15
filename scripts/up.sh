#!/usr/bin/env bash
# scripts/up.sh
#
# Идемпотентный запуск полного PDRD stack.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_DIR}"

SHARED_STARTUP_TIMEOUT_SECONDS="${SHARED_STARTUP_TIMEOUT_SECONDS:-240}"
PDRD_STARTUP_TIMEOUT_SECONDS="${PDRD_STARTUP_TIMEOUT_SECONDS:-360}"
PDRD_STARTUP_POLL_SECONDS="${PDRD_STARTUP_POLL_SECONDS:-5}"

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

validate_secret() {
    local variable_name="$1"
    local value="${!variable_name:-}"

    if [[ -z "${value}" ]]; then
        die "${variable_name} должен быть задан в .env."
    fi

    case "${value}" in
        change-me|CHANGE_ME*)
            die "${variable_name} содержит placeholder."
            ;;
    esac
}

if [[ ! -f ".env" ]]; then
    die "Файл ${REPO_DIR}/.env не найден."
fi

set -a
# shellcheck disable=SC1091
source ".env"
set +a

validate_secret "PDRD_POSTGRES_PASSWORD"
validate_secret "PDRD_RABBITMQ_PASSWORD"

PDRD_RABBITMQ_USER="${PDRD_RABBITMQ_USER:-pdrd_validation}"
PDRD_RABBITMQ_VHOST="${PDRD_RABBITMQ_VHOST:-pdrd-validation}"

export PDRD_RABBITMQ_USER
export PDRD_RABBITMQ_VHOST

SHARED_INFRA_DIR="${SHARED_INFRA_DIR:-}"

candidates=()

if [[ -n "${SHARED_INFRA_DIR}" ]]; then
    candidates+=("${SHARED_INFRA_DIR}")
fi

candidates+=(
    "/projects/shared-infrastructure"
    "/projects/shared_infrasktructure"
    "${HOME}/projects/shared-infrastructure"
    "${HOME}/projects/shared_infrasktructure"
    "${REPO_DIR}/../shared-infrastructure"
    "${REPO_DIR}/../shared_infrasktructure"
)

SHARED_INFRA_DIR=""

for candidate in "${candidates[@]}"; do
    if [[ -d "${candidate}" \
        && -f "${candidate}/compose.yaml" \
        && -f "${candidate}/scripts/bootstrap.sh" \
        && -f "${candidate}/scripts/check.sh" ]]; then
        SHARED_INFRA_DIR="$(cd "${candidate}" && pwd -P)"
        break
    fi
done

if [[ -z "${SHARED_INFRA_DIR}" ]]; then
    die "Не найден shared-infrastructure."
fi

echo "PDRD repository: ${REPO_DIR}"
echo "Shared infrastructure: ${SHARED_INFRA_DIR}"

echo
echo "=== Shared infrastructure ==="

(
    cd "${SHARED_INFRA_DIR}"

    bash scripts/bootstrap.sh

    docker compose up \
        -d \
        --wait \
        --wait-timeout "${SHARED_STARTUP_TIMEOUT_SECONDS}"

    bash scripts/check.sh
)

rabbitmqctl_shared() {
    (
        cd "${SHARED_INFRA_DIR}"

        docker compose exec \
            -T \
            rabbitmq \
            rabbitmqctl \
            "$@"
    )
}

echo
echo "=== PDRD RabbitMQ namespace ==="

if rabbitmqctl_shared list_vhosts \
    | awk 'NF > 0 && $1 != "Listing" && $1 != "name" {print $1}' \
    | grep -Fqx "${PDRD_RABBITMQ_VHOST}"; then
    echo "RabbitMQ vhost already exists: ${PDRD_RABBITMQ_VHOST}"
else
    rabbitmqctl_shared \
        add_vhost \
        "${PDRD_RABBITMQ_VHOST}"
fi

if rabbitmqctl_shared list_users \
    | awk 'NF > 0 && $1 != "Listing" && $1 != "user" {print $1}' \
    | grep -Fqx "${PDRD_RABBITMQ_USER}"; then

    echo "RabbitMQ user already exists: ${PDRD_RABBITMQ_USER}"

    if rabbitmqctl_shared \
        authenticate_user \
        "${PDRD_RABBITMQ_USER}" \
        "${PDRD_RABBITMQ_PASSWORD}" \
        >/dev/null 2>&1; then

        echo "RabbitMQ password is valid."
    else
        echo "Updating RabbitMQ password."

        rabbitmqctl_shared \
            change_password \
            "${PDRD_RABBITMQ_USER}" \
            "${PDRD_RABBITMQ_PASSWORD}"
    fi
else
    rabbitmqctl_shared \
        add_user \
        "${PDRD_RABBITMQ_USER}" \
        "${PDRD_RABBITMQ_PASSWORD}"
fi

rabbitmqctl_shared \
    set_permissions \
    -p "${PDRD_RABBITMQ_VHOST}" \
    "${PDRD_RABBITMQ_USER}" \
    '.*' \
    '.*' \
    '.*'

rabbitmqctl_shared \
    authenticate_user \
    "${PDRD_RABBITMQ_USER}" \
    "${PDRD_RABBITMQ_PASSWORD}" \
    >/dev/null

echo "RabbitMQ namespace: OK"

echo
echo "=== Compose validation ==="

docker compose config --quiet

echo
echo "=== Build ==="

docker compose build

echo
echo "=== PostgreSQL / Qdrant ==="

docker compose up \
    -d \
    postgres \
    qdrant

deadline=$((SECONDS + 120))

until docker compose exec \
    -T \
    postgres \
    pg_isready \
    -U "${PDRD_POSTGRES_USER:-pdrd}" \
    -d "${PDRD_POSTGRES_DB:-pdrd}" \
    >/dev/null 2>&1; do

    if (( SECONDS >= deadline )); then
        docker compose ps postgres
        die "PostgreSQL не стал ready."
    fi

    sleep 2
done

echo "PostgreSQL: ready"

echo
echo "=== Database migrations ==="

docker compose run \
    --rm \
    --no-deps \
    api-gateway \
    alembic upgrade head

docker compose run \
    --rm \
    --no-deps \
    knowledge-service \
    alembic upgrade head

echo
echo "=== Application stack ==="

docker compose up -d

echo
echo "=== Stack readiness ==="

deadline=$((SECONDS + PDRD_STARTUP_TIMEOUT_SECONDS))

while true; do
    if bash scripts/check-stack.sh; then
        break
    fi

    if (( SECONDS >= deadline )); then
        echo
        docker compose ps

        echo
        docker compose logs \
            --tail=80 \
            api-gateway \
            api-gateway-worker \
            knowledge-service \
            knowledge-indexer \
            technical-assignment-indexer \
            analysis-service \
            multimodal-embedding-service \
            || true

        die "PDRD stack не стал ready."
    fi

    sleep "${PDRD_STARTUP_POLL_SECONDS}"
done

echo
echo "PDRD STACK IS READY"