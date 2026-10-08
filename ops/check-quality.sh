# ops/check-quality.sh
# Общая проверка и шесть изолированных интеграционных наборов Linux.
# Запуск: bash ops/check-quality.sh. После успеха: bash scripts/up.sh.

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repository_root}"

active_suite=""
current_stage="Общие проверки"
stage_number=1

compose_suite() {
    local suite="$1"
    shift
    docker compose -p "pdrd-${suite}-test" \
        -f "${repository_root}/ops/compose.${suite}-test.yaml" "$@"
}

finish() {
    local result=$?
    local cleanup_result=0
    trap - EXIT
    if [[ -n "${active_suite}" ]]; then
        compose_suite "${active_suite}" down --remove-orphans || cleanup_result=$?
        if (( result == 0 && cleanup_result != 0 )); then
            result=${cleanup_result}
        fi
    fi
    if (( result != 0 )); then
        printf '\nОШИБКА: этап «%s», код %s. Исправьте ошибку и повторите проверку.\n' \
            "${current_stage}" "${result}" >&2
    fi
    exit "${result}"
}

trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf '\n[%s/7] %s\n' "${stage_number}" "${current_stage}"
git diff --check
docker compose --profile test run --rm --no-deps --build quality-tests

for suite in gateway knowledge experience user auth equipment; do
    stage_number=$((stage_number + 1))
    current_stage="Интеграционные тесты ${suite}"
    active_suite="${suite}"
    printf '\n[%s/7] %s\n' "${stage_number}" "${current_stage}"
    compose_suite "${suite}" up --build --abort-on-container-exit \
        --exit-code-from "${suite}-test-runner"
    compose_suite "${suite}" down --remove-orphans
    active_suite=""
done

printf '\nВсе проверки прошли. Следующий шаг: bash scripts/up.sh\n'
