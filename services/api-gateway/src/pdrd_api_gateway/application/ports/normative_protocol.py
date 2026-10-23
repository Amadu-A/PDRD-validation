# services/api-gateway/src/pdrd_api_gateway/application/ports/normative_protocol.py

"""Порт неизменяемого протокола в существующем хранилище артефактов анализа."""

from typing import Protocol
from uuid import UUID


class NormativeProtocolError(RuntimeError):
    """Протокол не сохранён либо не прошёл проверку целостности."""


class NormativeProtocolNotFound(LookupError):
    """Протокол ещё не опубликован или принадлежит другому документу."""


class NormativeProtocolStore(Protocol):
    """Части доступны только после атомарной публикации проверенного манифеста."""

    async def put_part(
        self, document_id: UUID, protocol_id: str, index: int, content: bytes
    ) -> None:
        """Идемпотентно сохраняет ограниченную неизменяемую часть."""
        ...

    async def publish(
        self, document_id: UUID, protocol_id: str, manifest: dict
    ) -> dict:
        """Проверяет все части и digest перед публикацией."""
        ...

    async def read(
        self,
        document_id: UUID,
        protocol_id: str,
        collection: str,
        offset: int,
        limit: int,
    ) -> dict:
        """Возвращает ограниченную страницу опубликованного протокола."""
        ...

    async def read_part(self, document_id: UUID, protocol_id: str, index: int) -> bytes:
        """Возвращает одну ограниченную часть опубликованного архива."""
        ...

    async def stream(self, document_id: UUID, protocol_id: str, offset: int) -> dict:
        """Передаёт результаты стадии ограниченным потоком JSONL."""
        ...

    async def verify(self, document_id: UUID, protocol_id: str) -> None:
        """Повторно сверяет фактические bytes перед завершением analysis job."""
        ...

    async def diagnostic_plan(self, document_id: UUID, protocol_id: str) -> dict:
        """Восстанавливает достоверные поля счётчиков без всех нормативных цитат."""
        ...
