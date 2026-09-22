#!/usr/bin/env bash
# scripts/migrate-embedding-indexes.sh
#
# Controlled blue/green cutover embedding indexes на shared-embedding.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_DIR}"

PDRD_STARTUP_TIMEOUT_SECONDS="${PDRD_STARTUP_TIMEOUT_SECONDS:-360}"
PDRD_STARTUP_POLL_SECONDS="${PDRD_STARTUP_POLL_SECONDS:-5}"

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

if [[ ! -f ".env" ]]; then
    die "Файл ${REPO_DIR}/.env не найден."
fi

set -a
# shellcheck disable=SC1091
source ".env"
set +a

QDRANT_URL="http://127.0.0.1:${QDRANT_HTTP_PORT:-6333}"

timestamp="$(date +%Y%m%d-%H%M%S)"

alias_snapshot="/tmp/pdrd-embedding-aliases-before-${timestamp}.json"

migration_log="/tmp/pdrd-embedding-migration-${timestamp}.log"

print_aliases() {
    curl -fsS \
        "${QDRANT_URL}/aliases" \
        | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
aliases = payload.get("result", {}).get("aliases", [])

for item in sorted(
    aliases,
    key=lambda value: value.get("alias_name", ""),
):
    print(
        f"{item.get("alias_name")} -> "
        f"{item.get("collection_name")}"
    )
'
}

print_collections() {
    curl -fsS \
        "${QDRANT_URL}/collections" \
        | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
collections = payload.get("result", {}).get("collections", [])

for item in sorted(
    collections,
    key=lambda value: value.get("name", ""),
):
    print(item.get("name"))
'
}

echo "=== Compose validation ==="

docker compose config --quiet

echo "Compose config: OK"

echo
echo "=== PostgreSQL / Qdrant ==="

docker compose up \
    -d \
    --remove-orphans \
    postgres \
    qdrant

echo
echo "=== Shared embedding preflight ==="

docker compose run \
    --rm \
    --no-deps \
    knowledge-service \
    python \
    - <<'PY'
import asyncio
import time

from pdrd_knowledge_service.core.settings import get_settings
from pdrd_knowledge_service.infrastructure.embedding.text_http import (
    HttpTextEmbeddingProvider,
)


async def main() -> None:
    settings = get_settings()

    expected_model = "shared-embedding"
    expected_schema = 2
    expected_base_url = "http://shared-embedding:8000/v1"

    if settings.embedding_model != expected_model:
        raise SystemExit(
            "PDRD_EMBEDDING_MODEL должен быть "
            f"{expected_model!r}, получено "
            f"{settings.embedding_model!r}."
        )

    if settings.embedding_schema_version != expected_schema:
        raise SystemExit(
            "PDRD_EMBEDDING_SCHEMA_VERSION должен быть "
            f"{expected_schema}, получено "
            f"{settings.embedding_schema_version}."
        )

    if settings.embedding_dimension != 4096:
        raise SystemExit(
            "PDRD_EMBEDDING_DIMENSION должен быть 4096."
        )

    if settings.embedding.base_url != expected_base_url:
        raise SystemExit(
            "Text embedding base_url должен быть "
            f"{expected_base_url!r}, получено "
            f"{settings.embedding.base_url!r}."
        )

    if settings.multimodal_embedding.base_url != expected_base_url:
        raise SystemExit(
            "Multimodal embedding base_url должен быть "
            f"{expected_base_url!r}, получено "
            f"{settings.multimodal_embedding.base_url!r}."
        )

    provider = HttpTextEmbeddingProvider(
        base_url=settings.embedding.base_url,
        request_timeout_seconds=(
            settings.embedding.request_timeout_seconds
        ),
        connect_timeout_seconds=(
            settings.embedding.connect_timeout_seconds
        ),
        health_timeout_seconds=(
            settings.embedding.health_timeout_seconds
        ),
    )

    if not await provider.is_ready():
        raise SystemExit(
            "shared-embedding readiness failed."
        )

    batch_size = settings.indexing.embed_batch_size

    texts = tuple(
        f"PDRD shared embedding migration preflight {index}"
        for index in range(batch_size)
    )

    started_at = time.perf_counter()

    vectors = await provider.embed(
        texts,
        instruction=None,
    )

    elapsed = time.perf_counter() - started_at

    if len(vectors) != batch_size:
        raise SystemExit(
            "shared-embedding вернул неправильное "
            "количество vectors."
        )

    if any(
        len(vector) != settings.embedding_dimension
        for vector in vectors
    ):
        raise SystemExit(
            "shared-embedding вернул неправильную dimension."
        )

    print(
        "embedding_identity",
        f"model={settings.embedding_model}",
        f"dimension={settings.embedding_dimension}",
        f"schema={settings.embedding_schema_version}",
        f"fingerprint={settings.embedding_identity.fingerprint}",
    )

    print(
        "embedding_preflight",
        f"batch={batch_size}",
        f"seconds={elapsed:.3f}",
        f"items_per_second={batch_size / elapsed:.2f}",
    )


asyncio.run(main())
PY

echo
echo "Shared embedding preflight: OK"

echo
echo "=== Current aliases ==="

curl -fsS \
    "${QDRANT_URL}/aliases" \
    > "${alias_snapshot}"

print_aliases

echo
echo "Alias rollback snapshot: ${alias_snapshot}"

echo
echo "=== Current physical collections ==="

print_collections

echo
echo "=== Stop vector-space consumers/writers ==="

docker compose stop \
    api-gateway \
    api-gateway-outbox \
    api-gateway-worker \
    knowledge-service \
    knowledge-service-outbox \
    knowledge-indexer \
    technical-assignment-indexer \
    analysis-service

echo
echo "Application vector-space consumers stopped."

migration_failed() {
    echo
    echo "EMBEDDING MIGRATION FAILED."
    echo "Application services intentionally remain stopped."
    echo "Existing aliases were not supposed to move before a full rebuild."
    echo "Rollback alias snapshot: ${alias_snapshot}"
    echo "Migration log: ${migration_log}"
}

trap migration_failed ERR

echo
echo "=== Build schema-v2 physical indexes ==="

docker compose run \
    --rm \
    --no-deps \
    knowledge-embedding-migrator \
    2>&1 \
    | tee "${migration_log}"

trap - ERR

echo
echo "=== Aliases after migration ==="

print_aliases

echo
echo "=== Physical collections after migration ==="

print_collections

echo
echo "=== Start PDRD with schema-v2 aliases ==="

docker compose up -d --remove-orphans

echo
echo "=== Frontend proxy refresh ==="

docker compose up \
    -d \
    --no-deps \
    --force-recreate \
    frontend

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
            knowledge-embedding-migrator \
            knowledge-indexer \
            technical-assignment-indexer \
            analysis-service \
            frontend \
            || true

        die "PDRD stack не стал ready после embedding cutover."
    fi

    sleep "${PDRD_STARTUP_POLL_SECONDS}"
done

echo
echo "EMBEDDING CUTOVER PASSED"
echo "Rollback alias snapshot: ${alias_snapshot}"
echo "Migration log: ${migration_log}"