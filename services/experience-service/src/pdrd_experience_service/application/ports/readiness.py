# services/experience-service/src/pdrd_experience_service/application/ports/readiness.py

"""Контракт проверки готовности инфраструктуры Experience Service.

Application layer знает только о возможности проверить
готовность хранилища. Он не импортирует SQLAlchemy,
не содержит SQL-запросов и не управляет подключениями.
"""

from typing import Protocol


class DatabaseReadiness(Protocol):
    """Контракт минимальной проверки доступности хранилища."""

    async def is_ready(self) -> bool:
        """Возвращает True только при готовности базы к работе."""
        ...
