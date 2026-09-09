# services/api-gateway/src/pdrd_api_gateway/application/ports/technical_assignment_content.py

"""Application port чтения browser-viewable ТЗ."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class TechnicalAssignmentContentError(
    RuntimeError,
):
    """Knowledge Service не смог вернуть ТЗ."""


class TechnicalAssignmentContentNotFoundError(
    TechnicalAssignmentContentError,
):
    """ТЗ не найдено."""


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentContent:
    """Бинарный PDF или PDF-preview."""

    content: bytes

    mime_type: str


class TechnicalAssignmentContentReader(
    Protocol,
):
    """Контракт public T citation proxy."""

    async def get_content(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignmentContent:
        """Возвращает browser-viewable содержимое."""
        ...
