# ops/deploy-experience-index.sh
# Этап 8: quality/SQL gate, read-only канал, индексатор и проверочный поиск E.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

bash ops/deploy-review-services.sh "Review перед подключением индекса E" </dev/null
python3 ops/prepare-experience-index.py </dev/null
docker compose --profile review --profile experience-index config --quiet </dev/null
docker compose --profile review --profile experience-index build \
    knowledge-service experience-indexer analysis-service </dev/null
docker compose --profile review up -d --no-deps --force-recreate --wait experience-service </dev/null
docker compose up -d --no-deps --force-recreate --wait knowledge-service analysis-service </dev/null
docker compose --profile review --profile experience-index run --rm --no-deps -T experience-indexer \
    python -m pdrd_knowledge_service.experience_runtime sync </dev/null
docker compose --profile review --profile experience-index up -d --no-deps --force-recreate \
    --wait --wait-timeout 1200 experience-indexer </dev/null
docker compose exec -T knowledge-service python -c \
    'from pdrd_knowledge_service.core.settings import Settings; assert Settings().search.experience_enabled is False; print("Рабочий E выключен, индексатор подключён")' </dev/null
docker compose --profile review --profile experience-index ps experience-indexer knowledge-service experience-service analysis-service </dev/null
echo "Этап 8: E индексируется автоматически. Для рабочего поиска требуется отложенная оценка качества."
