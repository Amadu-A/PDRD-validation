# services/api-gateway/src/pdrd_api_gateway/application/ports/equipment_updates.py

"""Порты версий EQ и сопоставления без сетевых зависимостей application."""

from typing import Protocol
from uuid import UUID


class EquipmentJobReader(Protocol):
    """Читает состояние или неизменяемую завершённую редакцию."""

    async def state(self, document_id: UUID) -> dict:
        """Возвращает текущую проверку."""

    async def version(self, document_id: UUID, revision: int) -> dict:
        """Возвращает сохранённую редакцию."""


class EquipmentComparison(Protocol):
    """Сопоставляет серверные проектные данные с сохранёнными фактами."""

    async def compare(self, pages: list[dict], search: dict) -> dict:
        """Возвращает отдельное предложение обновления отчёта."""
