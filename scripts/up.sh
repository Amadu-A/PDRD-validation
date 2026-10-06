#!/usr/bin/env bash
# scripts/up.sh
#
# Идемпотентный запуск полного PDRD stack.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_DIR}"

SHARED_STARTUP_TIMEOUT_SECONDS="${SHARED_STARTUP_TIMEOUT_SECONDS:-240}"
PDRD_STARTUP_POLL_SECONDS="${PDRD_STARTUP_POLL_SECONDS:-5}"
AUTH_SERVICE_AD_CA_HOST_PATH="${REPO_DIR}/ops/certificates/ad-ca.pem"

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

if [[ ! -f ".env.example" ]]; then
    die "Файл ${REPO_DIR}/.env.example не найден."
fi

if [[ ! -f ".env" ]]; then
    die "Файл ${REPO_DIR}/.env не найден."
fi

set -a
# shellcheck disable=SC1091
source ".env.example"
# shellcheck disable=SC1091
source ".env"
set +a

# shellcheck source=scripts/lib/shared-infrastructure.sh
source "${REPO_DIR}/scripts/lib/shared-infrastructure.sh"

profile_enabled() {
    local profile="$1"
    [[ ",${COMPOSE_PROFILES:-}," == *,"${profile}",* ]]
}

if profile_enabled "experience-index"; then
    profile_enabled "review" \
        || die "COMPOSE_PROFILES=experience-index требует также review."
    PDRD_STARTUP_TIMEOUT_SECONDS="${PDRD_STARTUP_TIMEOUT_SECONDS:-1200}"
else
    PDRD_STARTUP_TIMEOUT_SECONDS="${PDRD_STARTUP_TIMEOUT_SECONDS:-360}"
fi

validate_secret "PDRD_RETENTION_INTERNAL_KEY"
if (( ${#PDRD_RETENTION_INTERNAL_KEY} < 32 )); then
    die "PDRD_RETENTION_INTERNAL_KEY должен содержать не менее 32 символов."
fi

validate_secret "PDRD_POSTGRES_PASSWORD"
validate_secret "PDRD_RABBITMQ_PASSWORD"
if profile_enabled "identity"; then
    validate_secret "USER_SERVICE_INTERNAL_KEY"
    if (( ${#USER_SERVICE_INTERNAL_KEY} < 32 )); then
        die "USER_SERVICE_INTERNAL_KEY должен содержать не менее 32 символов."
    fi
fi

if profile_enabled "review"; then
    if [[ "${API_GATEWAY_REVIEW__ENABLED:-false}" != "true" || "${API_GATEWAY_REVIEW__CONTROLLED_ACCESS:-false}" != "true" ]]; then
        die "Профиль review требует включённый закрытый канал Gateway."
    fi
    validate_secret "API_GATEWAY_REVIEW__ACTOR"
    validate_secret "API_GATEWAY_REVIEW__UI_KEY"
    validate_secret "API_GATEWAY_REVIEW__INTERNAL_KEY"
    if [[ "${API_GATEWAY_REVIEW__UI_KEY}" == "${API_GATEWAY_REVIEW__INTERNAL_KEY}" ]]; then
        die "Служебные ключи Review должны различаться."
    fi
fi

if profile_enabled "auth"; then
    profile_enabled "identity" \
        || die "COMPOSE_PROFILES=auth требует также identity."

    validate_secret "AUTH_SERVICE_HTTP__PUBLIC_ORIGIN"
    validate_secret "AUTH_SERVICE_EMAIL__PUBLIC_BASE_URL"
    if [[ "${AUTH_SERVICE_HTTP__PUBLIC_ORIGIN}" != https://* ]]; then
        die "AUTH_SERVICE_HTTP__PUBLIC_ORIGIN должен начинаться с https://."
    fi
    if [[ "${AUTH_SERVICE_EMAIL__PUBLIC_BASE_URL%/}" != "${AUTH_SERVICE_HTTP__PUBLIC_ORIGIN%/}" ]]; then
        die "Адрес ссылки подтверждения должен совпадать с публичным origin."
    fi

    validate_secret "AUTH_SERVICE_HTTP__INTERNAL_KEY"
    validate_secret "AUTH_SERVICE_HTTP__CSRF_KEY"
    validate_secret "PDRD_FRONTEND_PROXY_KEY"
    validate_secret "PDRD_TECHNICAL_ASSIGNMENT_ACCESS_KEY"
    if (( ${#AUTH_SERVICE_HTTP__INTERNAL_KEY} < 32 )); then
        die "AUTH_SERVICE_HTTP__INTERNAL_KEY должен содержать не менее 32 символов."
    fi
    if (( ${#AUTH_SERVICE_HTTP__CSRF_KEY} < 32 )); then
        die "AUTH_SERVICE_HTTP__CSRF_KEY должен содержать не менее 32 символов."
    fi
    if (( ${#PDRD_FRONTEND_PROXY_KEY} < 32 )); then
        die "PDRD_FRONTEND_PROXY_KEY должен содержать не менее 32 символов."
    fi
    if (( ${#PDRD_TECHNICAL_ASSIGNMENT_ACCESS_KEY} < 32 )); then
        die "PDRD_TECHNICAL_ASSIGNMENT_ACCESS_KEY должен содержать не менее 32 символов."
    fi

    validate_secret "AUTH_SERVICE_EMAIL__SMTP_USER"
    validate_secret "AUTH_SERVICE_EMAIL__SMTP_PASSWORD"
    validate_secret "AUTH_SERVICE_EMAIL__FROM_EMAIL"
    if [[ "${API_GATEWAY_IDENTITY_PROXY__ENABLED:-false}" != "true" ]]; then
        die "Для профиля auth задайте API_GATEWAY_IDENTITY_PROXY__ENABLED=true."
    fi
    if [[ "${API_GATEWAY_IDENTITY_PROXY__AUTHORIZATION_ENABLED:-false}" != "true" ]]; then
        die "Для профиля auth задайте API_GATEWAY_IDENTITY_PROXY__AUTHORIZATION_ENABLED=true."
    fi

    if [[ "${AUTH_SERVICE_ENABLED:-false}" == "true" ]]; then
        if [[ ! -r "${AUTH_SERVICE_AD_CA_HOST_PATH}" || ! -s "${AUTH_SERVICE_AD_CA_HOST_PATH}" ]]; then
            die "Для AUTH_SERVICE_ENABLED=true нужен непустой читаемый ops/certificates/ad-ca.pem."
        fi
    fi
fi

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

require_separate_shared_namespace "${COMPOSE_PROJECT_NAME:-pdrd-validation-ai}"

echo
echo "=== Shared infrastructure ==="

(
    isolate_shared_compose_environment
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
        isolate_shared_compose_environment
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

# При выбранных профилях Compose сначала выполнит миграции через depends_on.
docker compose up -d --remove-orphans

echo
echo "=== Frontend proxy refresh ==="

docker compose up \
    -d \
    --no-deps \
    --force-recreate \
    frontend

# Nginx разрешает адрес Gateway при старте. После его пересоздания обновляем
# также отдельно поднятый Review frontend, даже если review не указан в .env.
review_frontend_container="$(
    docker compose --profile review ps --all --quiet review-frontend
)"
if profile_enabled "review" || [[ -n "${review_frontend_container}" ]]; then
    docker compose --profile review up \
        -d \
        --no-deps \
        --force-recreate \
        review-frontend
fi

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
        log_services=(
            api-gateway
            api-gateway-worker
            knowledge-service
            knowledge-embedding-migrator
            knowledge-indexer
            technical-assignment-indexer
            analysis-service
        )

        if profile_enabled "review"; then
            log_services+=(experience-migrate experience-service review-frontend)
        fi
        if profile_enabled "identity"; then
            log_services+=(user-migrate user-service)
        fi
        if profile_enabled "auth"; then
            log_services+=(auth-migrate auth-service admin-service)
        fi
        if profile_enabled "experience-index"; then
            log_services+=(experience-indexer)
        fi

        docker compose logs \
            --tail=80 \
            "${log_services[@]}" \
            || true

        die "PDRD stack не стал ready."
    fi

    sleep "${PDRD_STARTUP_POLL_SECONDS}"
done

echo
echo "PDRD STACK IS READY"
