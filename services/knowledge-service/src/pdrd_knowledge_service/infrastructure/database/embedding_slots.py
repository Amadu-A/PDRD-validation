# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/embedding_slots.py

"""Общие для API и индексаторов слоты физических embedding-запросов без новой таблицы."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from pdrd_knowledge_service.application.ports.embedding import EmbeddingTemporaryError


class PostgresEmbeddingSlots:
    """Ограничивает запросы PDRD к общей модели, не управляя очередью самого vLLM."""

    def __init__(self, engine: AsyncEngine, *, identity: str, capacity: int) -> None:
        """Сохраняет пул и стабильные ключи session advisory locks для одной модели."""
        self._engine = engine
        self._keys = tuple(
            int.from_bytes(
                sha256(f"pdrd-embedding:{identity}:{slot}".encode()).digest()[:8],
                "big",
                signed=True,
            )
            for slot in range(capacity)
        )

    @asynccontextmanager
    async def try_slot(self) -> AsyncIterator[bool]:
        """Сразу возвращает занятность; соединение не удерживается во время backoff."""
        try:
            async with self._engine.connect() as connection:
                acquired = None
                try:
                    for key in self._keys:
                        result = await connection.execute(
                            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                        )
                        if result.scalar_one():
                            acquired = key
                            break
                    await connection.commit()
                    try:
                        yield acquired is not None
                    finally:
                        if acquired is not None:
                            await connection.execute(
                                text("SELECT pg_advisory_unlock(:key)"),
                                {"key": acquired},
                            )
                            await connection.commit()
                except BaseException:
                    await connection.invalidate()
                    raise
        except SQLAlchemyError as error:
            raise EmbeddingTemporaryError(
                "Недоступен механизм допуска embedding-запросов.",
                reason_code="embedding_admission_unavailable",
            ) from error
