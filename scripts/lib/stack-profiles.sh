#!/usr/bin/env bash
# scripts/lib/stack-profiles.sh
# Определяет необязательные профили по конфигурации и фактическим контейнерам.

# Профиль явно выбран в закрытом .env или окружении запуска.
profile_enabled() {
    local profile="$1"
    [[ ",${COMPOSE_PROFILES:-}," == *,"${profile}",* ]]
}

# Compose ищет уже созданный контейнер даже при невыбранном профиле.
service_container_exists() {
    local profile="$1"
    local service="$2"
    local container_id

    container_id="$(
        docker compose --profile "${profile}" ps \
            --all --quiet "${service}" 2>/dev/null
    )" || return 1

    [[ -n "${container_id}" ]]
}

# Проверять профиль нужно также после запуска контейнеров отдельной командой.
optional_profile_active() {
    local profile="$1"
    local service
    shift

    if profile_enabled "${profile}"; then
        return 0
    fi

    for service in "$@"; do
        if service_container_exists "${profile}" "${service}"; then
            return 0
        fi
    done

    return 1
}
