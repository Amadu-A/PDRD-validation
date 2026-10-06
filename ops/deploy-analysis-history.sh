# ops/deploy-analysis-history.sh
# Проверяет код и изолированные БД, обновляет четыре настройки и разворачивает сервисы.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

cleanup() {
    local result=$?
    trap - EXIT
    for suite in user auth gateway experience; do
        docker compose -f "ops/compose.${suite}-test.yaml" down --remove-orphans || true
    done
    exit "$result"
}
trap cleanup EXIT

# Любой отказ тестов останавливает скрипт до изменения рабочего .env и контейнеров.
docker compose --profile test run --rm --no-deps --build quality-tests sh -ec '
    python -m ruff check .
    python -m ruff format --check .
    python -m pytest -q --import-mode=importlib
    node --test frontend/tests/*.test.js
'
for suite in user auth gateway experience; do
    docker compose -f "ops/compose.${suite}-test.yaml" up --build \
        --abort-on-container-exit --exit-code-from "${suite}-test-runner"
done

# Резервная копия секретного .env остаётся в игнорируемом каталоге с правами 0600.
python3 - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import os

path = Path(".env")
if not path.is_file():
    raise SystemExit("Рабочий .env не найден. Развёртывание остановлено.")
source = path.read_text(encoding="utf-8")
values = {
    "AUTH_SERVICE_SESSIONS__IDLE_TIMEOUT_SECONDS": "86400",
    "AUTH_SERVICE_SESSIONS__ABSOLUTE_TIMEOUT_SECONDS": "2592000",
    "DOCUMENT_SERVICE_PDF__MAX_ANALYSIS_PAGES": "200",
    "ANALYSIS_SERVICE_PIPELINE__MAX_STAGE_PAGES": "200",
}
lines = []
seen = set()
for line in source.splitlines():
    key = line.partition("=")[0].strip()
    if key in values:
        if key not in seen:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
    else:
        lines.append(line)
for key in values.keys() - seen:
    lines.append(f"{key}={values[key]}")
updated = "\n".join(lines) + "\n"
if updated != source:
    backups = Path(".codex-test-temp/deployment-backups")
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backups, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    descriptor = os.open(backups / f"env-analysis-history-{stamp}.bak",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as backup:
        backup.write(source)
    with path.open("w", encoding="utf-8", newline="\n") as target:
        target.write(updated)
print("Настройки обновлены: простой 24 часа, срок 30 дней, PDF до 200 страниц.")
PY

# Обновляются также User/Admin/Experience из предыдущего исправления,
# которое не дошло до рабочего развёртывания из-за отказа теста миграции.
compose=(docker compose --profile identity --profile auth --profile review --profile experience-index)
services=(user-service auth-service admin-service experience-service api-gateway
    api-gateway-worker api-gateway-outbox document-service analysis-service frontend)
# Ранее выключенный индексатор автоматически не включается.
if [[ -n "$("${compose[@]}" ps -q --status running experience-indexer)" ]]; then
    services+=(experience-indexer)
fi
"${compose[@]}" build "${services[@]}" user-migrate auth-migrate experience-migrate
for migration in user-migrate auth-migrate experience-migrate; do
    "${compose[@]}" run --rm --no-deps "$migration"
done
# Индекс добавляется новой миграцией; существующие задания и владельцы сохраняются.
"${compose[@]}" run --rm --no-deps api-gateway python -m alembic -c alembic.ini upgrade head
if ! "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 180 "${services[@]}"; then
    "${compose[@]}" ps "${services[@]}"
    "${compose[@]}" logs --tail=100 "${services[@]}"
    exit 1
fi
"${compose[@]}" ps "${services[@]}"
"${compose[@]}" logs --tail=60 user-service auth-service experience-service api-gateway api-gateway-worker document-service analysis-service
