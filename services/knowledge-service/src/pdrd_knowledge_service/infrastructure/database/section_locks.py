# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/section_locks.py

"""Сеансовые рекомендательные блокировки PostgreSQL без открытой бизнес-транзакции."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from hashlib import sha256
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresCatalogSectionLocks:
    """Блокирует один раздел, сохраняя параллелизм независимых разделов."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Сохраняет пул соединений приложения."""
        self._engine = engine

    def shared(self, section_id: UUID) -> AbstractAsyncContextManager[None]:
        """Разрешает несколько индексаторов до запроса удаления."""
        return self._lock(section_id, shared=True)

    def exclusive(self, section_id: UUID) -> AbstractAsyncContextManager[None]:
        """Ожидает завершения индексаторов и загрузок раздела."""
        return self._lock(section_id, shared=False)

    @asynccontextmanager
    async def _lock(self, section_id: UUID, *, shared: bool) -> AsyncIterator[None]:
        """Держит session lock и гарантирует освобождение соединения при ошибке."""
        key = int.from_bytes(
            sha256(b"pdrd-catalog-section:" + section_id.bytes).digest()[:8],
            "big",
            signed=True,
        )
        suffix = "_shared" if shared else ""
        async with self._engine.connect() as connection:
            try:
                await connection.execute(
                    text(f"SELECT pg_advisory_lock{suffix}(:key)"), {"key": key}
                )
                await connection.commit()
                try:
                    yield
                finally:
                    await connection.execute(
                        text(f"SELECT pg_advisory_unlock{suffix}(:key)"), {"key": key}
                    )
                    await connection.commit()
            except BaseException:
                # Физическое закрытие гарантирует снятие session lock даже при cancel.
                await connection.invalidate()
                raise
