# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/unit_of_work.py

"""SQLAlchemy Unit of Work нормативного каталога."""

from types import TracebackType

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from pdrd_knowledge_service.infrastructure.database.outbox_repository import (
    SqlAlchemyNormativeOutboxRepository,
)
from pdrd_knowledge_service.infrastructure.database.repositories import (
    SqlAlchemyNormativeCategoryRepository,
    SqlAlchemyNormativeDocumentRepository,
    SqlAlchemyNormativeSectionRepository,
)


class SqlAlchemyNormativeCatalogUnitOfWork:
    """Объединяет repositories одной PostgreSQL transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Создаёт независимую session для Unit of Work."""
        self._session = session_factory()

        self.sections = SqlAlchemyNormativeSectionRepository(
            self._session,
        )

        self.categories = SqlAlchemyNormativeCategoryRepository(
            self._session,
        )

        self.documents = SqlAlchemyNormativeDocumentRepository(
            self._session,
        )

        self.outbox = SqlAlchemyNormativeOutboxRepository(
            self._session,
        )

    async def __aenter__(
        self,
    ) -> "SqlAlchemyNormativeCatalogUnitOfWork":
        """Открывает Unit of Work."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Откатывает незавершённую transaction и закрывает session."""
        if self._session.in_transaction():
            await self._session.rollback()

        await self._session.close()

    async def commit(
        self,
    ) -> None:
        """Фиксирует transaction."""
        await self._session.commit()

    async def rollback(
        self,
    ) -> None:
        """Откатывает transaction."""
        await self._session.rollback()
