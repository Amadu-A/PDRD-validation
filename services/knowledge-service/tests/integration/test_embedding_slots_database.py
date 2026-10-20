# services/knowledge-service/tests/integration/test_embedding_slots_database.py

"""Межпроцессный контракт PostgreSQL admission в изолированном существующем DB-runner."""

import os
from uuid import uuid4

import pytest
from pdrd_knowledge_service.core.settings import Settings
from pdrd_knowledge_service.infrastructure.database.embedding_slots import (
    PostgresEmbeddingSlots,
)
from pdrd_knowledge_service.infrastructure.database.engine import build_async_engine

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_DATABASE_TESTS", "0") != "1",
        reason="Нужен изолированный PostgreSQL runner.",
    ),
]


async def test_api_and_indexer_share_slots_and_release_after_failure():
    """Два независимых пула одной модели конкурируют за общий слот, ошибка освобождает его."""
    settings = Settings(_env_file=None)
    first_engine, second_engine = (
        build_async_engine(settings.database),
        build_async_engine(settings.database),
    )
    identity = str(uuid4())
    first = PostgresEmbeddingSlots(first_engine, identity=identity, capacity=1)
    second = PostgresEmbeddingSlots(second_engine, identity=identity, capacity=1)
    try:
        with pytest.raises(RuntimeError, match="temporary"):
            async with first.try_slot() as acquired:
                assert acquired
                async with second.try_slot() as other:
                    assert not other
                raise RuntimeError("temporary")
        async with second.try_slot() as acquired:
            assert acquired
    finally:
        await first_engine.dispose()
        await second_engine.dispose()
