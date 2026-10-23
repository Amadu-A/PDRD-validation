# services/analysis-service/src/pdrd_analysis_service/application/ports/normative_protocol.py

"""Долговечная публикация полного протокола через существующее хранилище Gateway."""

from collections.abc import Awaitable, Callable
from typing import Protocol


class NormativeProtocolUnavailable(RuntimeError):
    """Сохранение не подтверждено; стадия не может вернуть успешный результат."""


class NormativeProtocolWriter(Protocol):
    """Публикует архив атомарно и возвращает только проверенную неизменяемую ссылку."""

    async def save(
        self, *, archive: dict, check_cancellation: Callable[[], Awaitable[None]]
    ) -> dict:
        """Не возвращает ссылку до подтверждения сохранения всех частей."""
        ...
