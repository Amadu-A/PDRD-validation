# services/auth-service/src/pdrd_auth_service/infrastructure/database/local_credentials.py

"""Короткие транзакции только в собственной таблице auth.local_credentials."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pdrd_auth_service.domain.local_credential import LocalCredential
from pdrd_auth_service.infrastructure.database.models import LocalCredentialModel


class SqlAlchemyLocalCredentialStore:
    """Уникальный логин и условная привязка защищают от дублей и смены профиля."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Получает фабрику соединений только Auth Service."""
        self._session_factory = session_factory

    async def get_by_username(self, username: str) -> LocalCredential | None:
        """Возвращает локальные учётные данные по нормализованному ключу."""
        async with self._session_factory() as database:
            row = await database.scalar(
                select(LocalCredentialModel).where(
                    LocalCredentialModel.username == username
                )
            )
            return (
                LocalCredential(
                    row.subject,
                    row.username,
                    row.password_hash,
                    row.created_at,
                    row.user_id,
                )
                if row
                else None
            )

    async def create(self, credential: LocalCredential) -> bool:
        """Резервирует логин до вызова другого сервиса с устойчивым subject."""
        async with self._session_factory() as database, database.begin():
            result = await database.execute(
                insert(LocalCredentialModel)
                .values(
                    subject=credential.subject,
                    username=credential.username,
                    password_hash=credential.password_hash,
                    created_at=credential.created_at,
                    user_id=None,
                )
                .on_conflict_do_nothing(index_elements=["username"])
            )
            return result.rowcount == 1

    async def attach_user(self, subject: UUID, user_id: UUID) -> None:
        """Разрешает только первую привязку либо повтор с тем же UUID."""
        async with self._session_factory() as database, database.begin():
            result = await database.execute(
                update(LocalCredentialModel)
                .where(
                    LocalCredentialModel.subject == subject,
                    LocalCredentialModel.user_id.is_(None)
                    | (LocalCredentialModel.user_id == user_id),
                )
                .values(user_id=user_id)
            )
            if result.rowcount != 1:
                raise ValueError("Связанный профиль локального аккаунта изменился")
