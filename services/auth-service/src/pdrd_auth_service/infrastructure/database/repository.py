# services/auth-service/src/pdrd_auth_service/infrastructure/database/repository.py

"""Атомарное хранение, продление и отзыв серверных сессий PostgreSQL."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pdrd_auth_service.domain.session import AuthSession
from pdrd_auth_service.infrastructure.database.models import SessionModel


def _to_domain(row: SessionModel) -> AuthSession:
    """Возвращает только метаданные и хеш без исходного токена."""
    return AuthSession(
        session_id=row.session_id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        authorization_version=row.authorization_version,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        idle_expires_at=row.idle_expires_at,
        absolute_expires_at=row.absolute_expires_at,
        revoked_at=row.revoked_at,
    )


class SqlAlchemySessionStore:
    """Выполняет условные SQL UPDATE, чтобы отзыв побеждал позднее продление."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Получает собственную фабрику соединений auth-service."""
        self._session_factory = session_factory

    async def create(self, session: AuthSession) -> None:
        """Фиксирует хеш токена и сроки в отдельной транзакции."""
        async with self._session_factory() as database, database.begin():
            database.add(
                SessionModel(
                    session_id=session.session_id,
                    user_id=session.user_id,
                    token_hash=session.token_hash,
                    authorization_version=session.authorization_version,
                    created_at=session.created_at,
                    last_seen_at=session.last_seen_at,
                    idle_expires_at=session.idle_expires_at,
                    absolute_expires_at=session.absolute_expires_at,
                    revoked_at=session.revoked_at,
                )
            )

    async def find_by_token_hash(self, token_hash: str) -> AuthSession | None:
        """Сравнивает только SHA-256 с уникальным индексом."""
        async with self._session_factory() as database:
            row = await database.scalar(
                select(SessionModel).where(SessionModel.token_hash == token_hash)
            )
            return _to_domain(row) if row is not None else None

    async def renew_if_active(
        self, session: AuthSession, now: datetime, idle_timeout_seconds: int
    ) -> AuthSession | None:
        """Продлевает только неотозванную запись, активную на момент UPDATE."""
        if idle_timeout_seconds < 1:
            raise ValueError("Недопустимый срок простоя")
        async with self._session_factory() as database, database.begin():
            result = await database.scalars(
                update(SessionModel)
                .where(
                    SessionModel.session_id == session.session_id,
                    SessionModel.token_hash == session.token_hash,
                    SessionModel.authorization_version == session.authorization_version,
                    SessionModel.revoked_at.is_(None),
                    SessionModel.idle_expires_at > now,
                    SessionModel.absolute_expires_at > now,
                )
                .values(
                    last_seen_at=func.greatest(SessionModel.last_seen_at, now),
                    idle_expires_at=func.least(
                        SessionModel.absolute_expires_at,
                        func.greatest(
                            SessionModel.idle_expires_at,
                            now + timedelta(seconds=idle_timeout_seconds),
                        ),
                    ),
                )
                .returning(SessionModel)
            )
            row = result.one_or_none()
            return _to_domain(row) if row is not None else None

    async def revoke_token_hash(self, token_hash: str, now: datetime) -> None:
        """Завершает найденную сессию без ошибок при повторном logout."""
        async with self._session_factory() as database, database.begin():
            await database.execute(
                update(SessionModel)
                .where(
                    SessionModel.token_hash == token_hash,
                    SessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )

    async def revoke_user(self, user_id: UUID, now: datetime) -> int:
        """Отзывает только ещё действующие сессии одного пользователя."""
        async with self._session_factory() as database, database.begin():
            result = await database.execute(
                update(SessionModel)
                .where(
                    SessionModel.user_id == user_id,
                    SessionModel.revoked_at.is_(None),
                    SessionModel.idle_expires_at > now,
                    SessionModel.absolute_expires_at > now,
                )
                .values(revoked_at=now)
            )
            return result.rowcount

    async def revoke_owned(
        self, user_id: UUID, session_id: UUID, now: datetime
    ) -> bool:
        """Обновляет запись только при совпадении владельца и UUID сессии."""
        async with self._session_factory() as database, database.begin():
            result = await database.execute(
                update(SessionModel)
                .where(
                    SessionModel.user_id == user_id,
                    SessionModel.session_id == session_id,
                    SessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            return result.rowcount == 1

    async def list_user(self, user_id: UUID, now: datetime) -> tuple[AuthSession, ...]:
        """Показывает лишь действующие сессии владельца без токенов."""
        async with self._session_factory() as database:
            rows = await database.scalars(
                select(SessionModel)
                .where(
                    SessionModel.user_id == user_id,
                    SessionModel.revoked_at.is_(None),
                    SessionModel.idle_expires_at > now,
                    SessionModel.absolute_expires_at > now,
                )
                .order_by(SessionModel.created_at.desc())
            )
            return tuple(_to_domain(row) for row in rows)
