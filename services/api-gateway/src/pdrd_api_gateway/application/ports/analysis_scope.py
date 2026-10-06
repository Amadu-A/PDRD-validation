# services/api-gateway/src/pdrd_api_gateway/application/ports/analysis_scope.py

"""Порт проверки доступа руководителя к заданию сотрудника."""

from typing import Protocol
from uuid import UUID


class AnalysisScopeUnavailable(RuntimeError):
    """Каталог пользователей не подтвердил область доступа к заданию."""


class AnalysisScopeChecker(Protocol):
    """Проверяет актуальные роли и подразделения на стороне user-service."""

    async def allows(self, *, actor_user_id: UUID, owner_user_id: UUID) -> bool:
        """Возвращает True только при полном совпадении разрешённой области."""
        ...
