# services/api-gateway/src/pdrd_api_gateway/infrastructure/database/unit_of_work.py

"""SQLAlchemy Unit of Work API Gateway."""

from types import TracebackType

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from pdrd_api_gateway.infrastructure.database.repositories import (
    SqlAlchemyAnalysisJobRepository,
)


class SqlAlchemyUnitOfWork:
    """Объединяет repositories одной database transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Создаёт независимую session для одного Unit of Work."""
        self._session = session_factory()

        self.analysis_jobs = SqlAlchemyAnalysisJobRepository(
            self._session,
        )

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
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

    async def commit(self) -> None:
        """Фиксирует transaction."""
        await self._session.commit()

    async def rollback(self) -> None:
        """Откатывает transaction."""
        await self._session.rollback()
