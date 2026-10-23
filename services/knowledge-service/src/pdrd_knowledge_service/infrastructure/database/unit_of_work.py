# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/unit_of_work.py

"""SQLAlchemy Unit of Work нормативного каталога."""

import logging
from types import TracebackType

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from pdrd_knowledge_service.application.ports.persistence import (
    CatalogConnectionLostError,
)
from pdrd_knowledge_service.infrastructure.database.normative_requirements import (
    SqlAlchemyNormativeRequirementRepository,
)
from pdrd_knowledge_service.infrastructure.database.outbox_repository import (
    SqlAlchemyNormativeOutboxRepository,
)
from pdrd_knowledge_service.infrastructure.database.repositories import (
    SqlAlchemyNormativeCategoryRepository,
    SqlAlchemyNormativeDocumentRepository,
    SqlAlchemyNormativeSectionRepository,
)

logger = logging.getLogger(__name__)


def catalog_connection_lost(error: BaseException | None) -> bool:
    """Не принимает ошибки SQL/данных и произвольный TimeoutError за потерю соединения."""
    if not isinstance(error, DBAPIError):
        return False
    state = getattr(error.orig, "sqlstate", None)
    return error.connection_invalidated or (
        isinstance(state, str)
        and (state.startswith("08") or state in {"57P01", "57P02", "57P03"})
    )


class SqlAlchemyNormativeCatalogUnitOfWork:
    """Объединяет repositories одной PostgreSQL transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Создаёт независимую session для Unit of Work."""
        self._session = session_factory()
        self.requirements = SqlAlchemyNormativeRequirementRepository(self._session)

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
        cleanup_error = None
        try:
            if self._session.in_transaction():
                await self._session.rollback()
        except BaseException as error:
            cleanup_error = error
        finally:
            try:
                await self._session.close()
            except BaseException as error:
                cleanup_error = cleanup_error or error
        failure = exc_value if exc_value is not None else cleanup_error
        if catalog_connection_lost(failure):
            raise CatalogConnectionLostError(
                "Соединение PostgreSQL потеряно; результат commit требует сверки."
            ) from failure
        if cleanup_error is not None:
            if exc_value is None:
                raise cleanup_error
            # Исходная программная ошибка/отмена важнее вторичного сбоя cleanup.
            logger.warning(
                "catalog_cleanup_failed error_type=%s", type(cleanup_error).__name__
            )

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
