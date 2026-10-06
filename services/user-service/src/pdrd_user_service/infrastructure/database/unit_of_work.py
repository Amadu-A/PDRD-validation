# services/user-service/src/pdrd_user_service/infrastructure/database/unit_of_work.py

"""Транзакционная единица работы с профилями и полномочиями."""

from types import TracebackType

from pdrd_user_service.infrastructure.database.repository import (
    SqlAlchemyUserRepository,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SqlAlchemyUnitOfWork:
    """Выделяет одну сессию на команду и требует явного commit."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Создаёт сессию и репозиторий в единой транзакционной области."""
        self._session = session_factory()
        self.users = SqlAlchemyUserRepository(self._session)

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        """Возвращает единицу работы без неявной фиксации."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Откатывает незавершённые изменения и закрывает сессию."""
        if self._session.in_transaction():
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        """Фиксирует успешную команду."""
        await self._session.commit()

    async def rollback(self) -> None:
        """Явно отменяет команду при обработке ошибки внутри контекста."""
        await self._session.rollback()
