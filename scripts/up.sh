#!/usr/bin/env bash
# scripts/up.sh
#
# Идемпотентный запуск полного PDRD stack:
# - проверяет project secrets;
# - поднимает shared infrastructure;
# - восстанавливает PDRD namespace в shared RabbitMQ;
# - собирает project images;
# - применяет database migrations;
# - поднимает project containers;
# - дожидается готовности всего stack.

set -euo pipefail


REPO_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.." \
        && pwd -P
)"

cd "${REPO_DIR}"


SHARED_STARTUP_TIMEOUT_SECONDS="${SHARED_STARTUP_TIMEOUT_SECONDS:-240}"
PDRD_STARTUP_TIMEOUT_SECONDS="${PDRD_STARTUP_TIMEOUT_SECONDS:-360}"
PDRD_STARTUP_POLL_SECONDS="${PDRD_STARTUP_POLL_SECONDS:-5}"

CHECK_OUTPUT=""


cleanup() {
    if [[ -n "${CHECK_OUTPUT}" && -f "${CHECK_OUTPUT}" ]]; then
        rm -f "${CHECK_OUTPUT}"
    fi
}


trap cleanup EXIT


step() {
    printf '\n=== %s ===\n' "$1"
}


die() {
    printf '\nERROR: %s\n' "$1" >&2
    exit 1
}


require_command() {
    local command_name="$1"

    if ! command -v "${command_name}" >/dev/null 2>&1; then
        die "Не найдена обязательная команда: ${command_name}"
    fi
}


validate_secret() {
    local variable_name="$1"
    local value="${!variable_name:-}"

    if [[ -z "${value}" ]]; then
        die "${variable_name} должен быть задан в .env."
    fi

    case "${value}" in
        change-me | CHANGE_ME*)
            die "${variable_name} всё ещё содержит placeholder."
            ;;
    esac
}


load_project_environment() {
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
}


resolve_shared_infrastructure() {
    local candidate
    local candidates=()

    if [[ -n "${SHARED_INFRA_DIR:-}" ]]; then
        candidates+=(
            "${SHARED_INFRA_DIR}"
        )
    fi

    candidates+=(
        "${REPO_DIR}/../shared-infrastructure"
        "${REPO_DIR}/../shared_infrasktructure"
    )

    for candidate in "${candidates[@]}"; do
        if (
            [[ -d "${candidate}" ]]
            && [[ -f "${candidate}/compose.yaml" ]]
            && [[ -f "${candidate}/scripts/bootstrap.sh" ]]
            && [[ -f "${candidate}/scripts/check.sh" ]]
        ); then
            SHARED_INFRA_DIR="$(
                cd "${candidate}" \
                    && pwd -P
            )"

            export SHARED_INFRA_DIR

            return
        fi
    done

    die "Не найден shared-infrastructure. Задайте SHARED_INFRA_DIR или разместите repository рядом с PDRD-validation."
}


shared_rabbitmqctl() {
    (
        cd "${SHARED_INFRA_DIR}"

        docker compose exec \
            -T \
            rabbitmq \
            rabbitmqctl \
            "$@"
    )
}


ensure_shared_stack() {
    step "Shared infrastructure"

    (
        cd "${SHARED_INFRA_DIR}"

        bash scripts/bootstrap.sh

        docker compose up \
            -d \
            --wait \
            --wait-timeout "${SHARED_STARTUP_TIMEOUT_SECONDS}"

        bash scripts/check.sh
    )
}


ensure_rabbitmq_namespace() {
    local user="${PDRD_RABBITMQ_USER}"
    local password="${PDRD_RABBITMQ_PASSWORD}"
    local vhost="${PDRD_RABBITMQ_VHOST}"

    step "PDRD RabbitMQ namespace"

    if shared_rabbitmqctl list_vhosts \
        | awk 'NF > 0 && $1 != "Listing" {print $1}' \
        | grep -Fqx "${vhost}"
    then
        printf 'RabbitMQ vhost already exists: %s\n' "${vhost}"
    else
        printf 'Creating RabbitMQ vhost: %s\n' "${vhost}"

        shared_rabbitmqctl \
            add_vhost \
            "${vhost}"
    fi

    if shared_rabbitmqctl list_users \
        | awk 'NF > 0 && $1 != "Listing" {print $1}' \
        | grep -Fqx "${user}"
    then
        printf 'RabbitMQ user already exists: %s\n' "${user}"

        if shared_rabbitmqctl \
            authenticate_user \
            "${user}" \
            "${password}" \
            >/dev/null 2>&1
        then
            printf 'RabbitMQ password is already valid.\n'
        else
            printf 'Updating RabbitMQ password for %s.\n' "${user}"

            shared_rabbitmqctl \
                change_password \
                "${user}" \
                "${password}"
        fi
    else
        printf 'Creating RabbitMQ user: %s\n' "${user}"

        shared_rabbitmqctl \
            add_user \
            "${user}" \
            "${password}"
    fi

    shared_rabbitmqctl \
        set_permissions \
        -p "${vhost}" \
        "${user}" \
        '.*' \
        '.*' \
        '.*'

    shared_rabbitmqctl \
        authenticate_user \
        "${user}" \
        "${password}" \
        >/dev/null

    printf 'RabbitMQ namespace is ready: user=%s vhost=%s\n' \
        "${user}" \
        "${vhost}"
}


wait_for_postgres() {
    local deadline

    deadline=$((SECONDS + 120))

    until docker compose exec \
        -T \
        postgres \
        pg_isready \
        -U "${PDRD_POSTGRES_USER:-pdrd}" \
        -d "${PDRD_POSTGRES_DB:-pdrd}" \
        >/dev/null 2>&1
    do
        if (( SECONDS >= deadline )); then
            docker compose ps postgres || true

            die "PostgreSQL не стал ready за 120 секунд."
        fi

        sleep 2
    done

    printf 'PostgreSQL: ready\n'
}


run_database_migrations() {
    step "Database migrations"

    printf 'API Gateway migrations...\n'

    docker compose run \
        --rm \
        --no-deps \
        api-gateway \
        alembic upgrade head

    printf 'Knowledge Service migrations...\n'

    docker compose run \
        --rm \
        --no-deps \
        knowledge-service \
        alembic upgrade head

    printf 'Database migrations: OK\n'
}


wait_for_project_stack() {
    local deadline

    CHECK_OUTPUT="$(
        mktemp
    )"

    deadline=$((SECONDS + PDRD_STARTUP_TIMEOUT_SECONDS))

    while true; do
        if bash scripts/check-stack.sh \
            >"${CHECK_OUTPUT}" \
            2>&1
        then
            cat "${CHECK_OUTPUT}"

            return
        fi

        if (( SECONDS >= deadline )); then
            printf '\nПоследняя ошибка stack check:\n' >&2

            cat "${CHECK_OUTPUT}" >&2

            printf '\nContainer state:\n' >&2

            docker compose ps >&2 || true

            printf '\nRelevant logs:\n' >&2

            docker compose logs \
                --tail=80 \
                api-gateway \
                api-gateway-outbox \
                api-gateway-worker \
                knowledge-service \
                knowledge-service-outbox \
                knowledge-indexer \
                technical-assignment-indexer \
                knowledge-embedding-migrator \
                analysis-service \
                multimodal-embedding-service \
                >&2 \
                || true

            die "PDRD stack не стал ready за ${PDRD_STARTUP_TIMEOUT_SECONDS} секунд."
        fi

        sleep "${PDRD_STARTUP_POLL_SECONDS}"
    done
}


main() {
    step "Prerequisites"

    require_command "docker"
    require_command "curl"

    docker version \
        --format 'Docker server: {{.Server.Version}}'

    docker compose version

    load_project_environment
    resolve_shared_infrastructure

    printf 'PDRD repository: %s\n' "${REPO_DIR}"
    printf 'Shared infrastructure: %s\n' "${SHARED_INFRA_DIR}"

    ensure_shared_stack
    ensure_rabbitmq_namespace

    step "PDRD compose validation"

    docker compose config \
        --quiet

    step "PDRD image build"

    docker compose build

    step "Project infrastructure"

    docker compose up \
        -d \
        postgres \
        qdrant

    wait_for_postgres
    run_database_migrations

    step "PDRD application stack"

    docker compose up \
        -d

    step "PDRD readiness"

    wait_for_project_stack

    printf '\nPDRD STACK IS READY\n'
}


main "$@"