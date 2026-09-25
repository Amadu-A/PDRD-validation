# services/experience-service/src/pdrd_experience_service/application/use_cases/check_readiness.py

"""Сценарий проверки готовности Experience Service.

Назначение:
- отделить HTTP endpoint от проверки PostgreSQL;
- позволить тестировать приложение без настоящей базы;
- сохранить возможность замены infrastructure adapter.
"""

from dataclasses import dataclass

from pdrd_experience_service.application.ports.readiness import (
    DatabaseReadiness,
)


@dataclass(frozen=True, slots=True)
class CheckReadiness:
    """Проверяет готовность через переданный infrastructure port."""

    database: DatabaseReadiness

    async def execute(self) -> bool:
        """Возвращает результат проверки хранилища."""
        return await self.database.is_ready()
