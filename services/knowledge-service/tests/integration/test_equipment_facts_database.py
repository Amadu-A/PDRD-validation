# services/knowledge-service/tests/integration/test_equipment_facts_database.py

"""Настоящий PostgreSQL: визуальные EQ Facts, cache hit и immutable identity."""

import os
from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.equipment_fact_index import EquipmentFactIndex
from pdrd_knowledge_service.core.settings import Settings
from pdrd_knowledge_service.infrastructure.database.engine import build_async_engine
from pdrd_knowledge_service.infrastructure.database.equipment_facts import (
    PostgresEquipmentFactIndex,
)
from sqlalchemy import text

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_DATABASE_TESTS") != "1",
        reason="Нужен настоящий PostgreSQL с миграциями Knowledge.",
    ),
]


async def test_visual_eq_cache_round_trip_and_snapshot_identity() -> None:
    """Сохранённый скан повторно читается без извлечения, другой SHA запрещён."""
    settings = Settings(_env_file=None)
    if (
        settings.database.host != "knowledge-test-postgres"
        or settings.database.name != "pdrd_knowledge_test"
    ):
        pytest.skip(
            "EQ Facts DB тест запускается через ops/compose.knowledge-test.yaml."
        )
    engine = build_async_engine(settings.database)
    index = EquipmentFactIndex(PostgresEquipmentFactIndex(engine))
    source_id = "EQ-" + uuid4().hex
    raw = (
        {
            "page": 2,
            "property_name": "Output voltage",
            "value_raw": "21...29",
            "unit_raw": "V",
            "current_type": "DC",
            "snippet": "DRC-100B Output voltage 21...29 V DC",
        },
    )
    args = {
        "source_id": source_id,
        "sha256": "a" * 64,
        "manufacturer": "MEAN WELL",
        "model": "DRC-100B",
        "variant": "",
        "pages": [{"page": 2, "text": ""}],
        "vision_facts": raw,
    }
    try:
        first = await index.extract_or_get(**args)
        second = await index.extract_or_get(**args)
        assert first["cache_hit"] is False and second["cache_hit"] is True
        assert first["status"] == second["status"] == "needs_review"
        assert first["facts"] == second["facts"]
        assert first["facts"][0]["document_source_id"] == source_id
        assert first["facts"][0]["page"] == 2 and first["facts"][0]["confidence"] == 0.6
        with pytest.raises(ValueError):
            await index.extract_or_get(**{**args, "sha256": "b" * 64})
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM knowledge.equipment_fact_sets WHERE document_source_id = :source_id"
                ),
                {"source_id": source_id},
            )
        await engine.dispose()


async def test_empty_text_attempt_does_not_poison_postgresql_cache():
    """Пустой текстовый проход не блокирует последующие доказательства того же snapshot."""
    settings = Settings(_env_file=None)
    if (
        settings.database.host != "knowledge-test-postgres"
        or settings.database.name != "pdrd_knowledge_test"
    ):
        pytest.skip("Нужна изолированная Knowledge PostgreSQL.")
    engine = build_async_engine(settings.database)
    cache = PostgresEquipmentFactIndex(engine)
    index = EquipmentFactIndex(cache)
    source_id = "EQ-" + uuid4().hex
    args = {
        "source_id": source_id,
        "sha256": "a" * 64,
        "manufacturer": "ACME",
        "model": "PS-24",
        "variant": "",
        "pages": [],
    }
    try:
        empty = await index.extract_or_get(**args)
        assert not empty["facts"] and await cache.get(source_id) is None
        raw = (
            {
                "page": 1,
                "property_name": "Output voltage",
                "value_raw": "20...30",
                "unit_raw": "V",
                "snippet": "PS-24 Output voltage 20...30 V",
            },
        )
        first = await index.extract_or_get(**args, vision_facts=raw)
        warm = await index.extract_or_get(**args)
        assert first["facts"] == warm["facts"] and warm["cache_hit"]
        assert warm["document_sha256"] == "a" * 64
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM knowledge.equipment_fact_sets WHERE document_source_id = :source_id"
                ),
                {"source_id": source_id},
            )
        await engine.dispose()
