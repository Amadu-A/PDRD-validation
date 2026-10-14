# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/equipment_facts.py

"""PostgreSQL кэш EQ Facts по snapshot, схеме и версии извлекателя."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from pdrd_knowledge_service.application.equipment_facts import (
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
)


@dataclass(slots=True)
class PostgresEquipmentFactIndex:
    """Хранит версионированные характеристики неизменённого документа."""

    engine: AsyncEngine

    async def save(self, result: dict[str, Any]) -> None:
        """Сохраняет неизменяемый набор фактов без бизнес-оркестрации."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO knowledge.equipment_fact_sets
                    (document_source_id, document_sha256, manufacturer, model,
                     variant, schema_version, extractor_version, status,
                     facts, created_at)
                    VALUES (:document_source_id, :document_sha256, :manufacturer,
                            :model, :variant, :schema_version,
                            :extractor_version, :status, CAST(:facts AS JSONB),
                            :created_at)
                    ON CONFLICT (document_source_id, schema_version, extractor_version)
                    DO NOTHING
                """),
                {
                    **{key: value for key, value in result.items() if key != "facts"},
                    "facts": json.dumps(result["facts"], ensure_ascii=False),
                    "created_at": datetime.now(UTC),
                },
            )

    async def get(self, source_id: str) -> dict[str, Any] | None:
        """Читает текущую версию фактов и provenance документа."""
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                        SELECT document_source_id, document_sha256,
                               manufacturer, model, variant, schema_version,
                               extractor_version, status, facts
                        FROM knowledge.equipment_fact_sets
                        WHERE document_source_id = :source_id
                          AND schema_version = :schema_version
                          AND extractor_version = :extractor_version
                    """),
                    {
                        "source_id": source_id,
                        "schema_version": SCHEMA_VERSION,
                        "extractor_version": EXTRACTOR_VERSION,
                    },
                )
            ).first()
        return dict(row._mapping) if row else None
