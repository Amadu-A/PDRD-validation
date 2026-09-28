# ops/deploy-reviewed-pdf.sh
# Проверяет этап 6 и обновляет проектные сервисы из уже синхронизированной ветки.
# Общий quality gate и отдельный PostgreSQL должны пройти до изменения runtime.
# Shared-сервисы, рабочие volumes, .env и Git-история этим скриптом не меняются.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Отдельные команды сохраняют set -e: ошибка Git внутри условия не игнорируется.
current_branch="$(git branch --show-current)"
working_changes="$(git status --porcelain)"
if [[ "$current_branch" != "feature/experience-base" ]]; then
    echo "Нужна ветка feature/experience-base." >&2
    exit 1
fi
if [[ -n "$working_changes" ]]; then
    echo "Рабочая копия содержит изменения. Сначала завершите синхронизацию." >&2
    exit 1
fi

docker compose --profile review --profile test config --quiet </dev/null
docker compose --profile test build quality-tests </dev/null
docker compose --profile test run --rm --no-deps -T quality-tests </dev/null

# Собственное имя исключает пересечение с другим параллельным тестовым прогоном.
test_project="pdrd-experience-isolated-${BASHPID}"
test_compose="ops/compose.experience-test.yaml"

cleanup_test_stack() {
    # Удаляет только контейнеры и сеть временного теста; рабочий стек не затрагивается.
    docker compose -p "$test_project" -f "$test_compose" down </dev/null
}
trap cleanup_test_stack EXIT
docker compose -p "$test_project" -f "$test_compose" up \
    --build --force-recreate --abort-on-container-exit \
    --exit-code-from experience-test-runner --attach experience-test-runner </dev/null
cleanup_test_stack
trap - EXIT

docker compose --profile review build \
    api-gateway experience-service experience-migrate document-service frontend review-frontend </dev/null
# Этап 6 не добавляет DDL: действующая вершина миграций — 20260928_0002.
docker compose --profile review run --rm --no-deps -T experience-migrate </dev/null
docker compose --profile review run --rm --no-deps -T experience-migrate \
    python -m alembic -c alembic.ini current --check-heads --verbose </dev/null
docker compose --profile review up -d --no-deps --force-recreate --wait \
    experience-service document-service </dev/null
docker compose --profile review up -d --no-deps --force-recreate --wait \
    api-gateway frontend review-frontend </dev/null

curl -fsS --retry 10 --retry-delay 1 --retry-connrefused --max-time 5 \
    http://127.0.0.1:8080/api/v1/review/config |
    python3 -c 'import json, sys; data = json.load(sys.stdin); assert data == {"enabled": False}, data; print("Frontend 8080: локальный Review")'
curl -fsS --retry 10 --retry-delay 1 --retry-connrefused --max-time 5 \
    http://127.0.0.1:8081/api/v1/review/config |
    python3 -c 'import json, sys; data = json.load(sys.stdin); assert data == {"enabled": True}, data; print("Frontend 8081: серверный Review")'
docker compose --profile review ps experience-service document-service api-gateway frontend review-frontend </dev/null
echo "Этап 6 развёрнут. Проверьте итоговый PDF через закрытый frontend 8081."
