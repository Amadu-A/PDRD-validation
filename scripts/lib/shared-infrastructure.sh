#!/usr/bin/env bash
# scripts/lib/shared-infrastructure.sh
# Отделяет Compose общей инфраструктуры от namespace и профилей проекта PDRD.

# Вызывается только в subshell: PDRD сохраняет собственные COMPOSE_* после возврата.
isolate_shared_compose_environment() {
    local variable
    for variable in "${!COMPOSE_@}"; do
        unset "${variable}"
    done
}

# Только читает Docker labels. Останавливает запуск до удаления чужих orphan-контейнеров.
require_separate_shared_namespace() {
    local project_name="$1"
    local service
    local container_ids

    for service in rabbitmq n8n-db n8n; do
        if ! container_ids="$(
            docker ps --all --quiet \
                --filter "label=com.docker.compose.project=${project_name}" \
                --filter "label=com.docker.compose.service=${service}"
        )"; then
            printf 'ERROR: Не удалось проверить Compose namespace %s.\n' "${project_name}" >&2
            return 1
        fi
        if [[ -n "${container_ids}" ]]; then
            printf 'ERROR: В проекте %s обнаружен shared-сервис %s. Сначала проверьте labels и mounts и вручную удалите подтверждённый дубликат; автоматическая очистка запрещена.\n' \
                "${project_name}" "${service}" >&2
            return 1
        fi
    done
}
