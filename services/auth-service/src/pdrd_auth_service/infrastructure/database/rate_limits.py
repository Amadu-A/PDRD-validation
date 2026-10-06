# services/auth-service/src/pdrd_auth_service/infrastructure/database/rate_limits.py

"""Общее PostgreSQL-ограничение частоты попыток входа и регистрации."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy import case, delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pdrd_auth_service.infrastructure.database.models import RateLimitModel


class SqlAlchemyAttemptLimiter:
    """Атомарно считает попытки во всех процессах Auth Service."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], secret: str
    ) -> None:
        """Скрывает адрес и IP в БД за HMAC отдельного namespace."""
        self._session_factory = session_factory
        self._secret = secret.encode("utf-8")

    async def allow(
        self,
        *,
        namespace: str,
        subject: str,
        limit: int,
        window: timedelta,
        now: datetime,
    ) -> bool:
        """Возвращает False после лимита, не сохраняя открытый идентификатор."""
        if limit < 1 or window <= timedelta(0):
            raise ValueError("Некорректный предел частоты")
        key_hash = hmac.new(
            self._secret,
            f"{namespace}\0{subject}".encode(),
            hashlib.sha256,
        ).hexdigest()
        expired = RateLimitModel.window_started_at <= now - window
        statement = (
            insert(RateLimitModel)
            .values(key_hash=key_hash, window_started_at=now, attempts=1)
            .on_conflict_do_update(
                index_elements=["key_hash"],
                set_={
                    "window_started_at": case(
                        (expired, now), else_=RateLimitModel.window_started_at
                    ),
                    "attempts": case(
                        (expired, 1),
                        else_=func.least(RateLimitModel.attempts + 1, limit + 1),
                    ),
                },
            )
            .returning(RateLimitModel.attempts)
        )
        async with self._session_factory() as database, database.begin():
            count = await database.scalar(statement)
            if secrets.randbelow(100) == 0:
                await database.execute(
                    delete(RateLimitModel).where(
                        RateLimitModel.window_started_at < now - timedelta(days=2)
                    )
                )
        return count is not None and count <= limit
