# services/auth-service/src/pdrd_auth_service/application/ports/rate_limits.py

"""Порт общего ограничения частоты публичных действий с учётной записью."""

from datetime import datetime, timedelta
from typing import Protocol


class AttemptLimiterPort(Protocol):
    """Считает попытки атомарно между процессами без хранения открытого ключа."""

    async def allow(
        self,
        *,
        namespace: str,
        subject: str,
        limit: int,
        window: timedelta,
        now: datetime,
    ) -> bool:
        """Возвращает False при превышении предела в данном окне."""
        ...
