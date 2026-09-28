# services/experience-service/src/pdrd_experience_service/application/ports/confirmed_areas.py

"""Порты чтения и изменения явно подтверждённых инженером областей.

Предложения VLM не удовлетворяют этим контрактам: источник данных
обязан сохранять действие инженера и проверять версии в транзакции.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pdrd_experience_service.domain.area_confirmation import (
    AreaConfirmation,
    AreaConfirmationReceipt,
)
from pdrd_experience_service.domain.experience_selection import (
    ConfirmedFindingArea,
)


class ConfirmedAreasReader(Protocol):
    """Возвращает только актуальные подтверждения для отбора Experience."""

    async def load_confirmed(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ConfirmedFindingArea, ...]:
        """Не возвращает отозванные либо устаревшие после правок области."""
        ...


class ConfirmedAreasWriter(Protocol):
    """Сохраняет подтверждения с отдельной версией и аудитом."""

    async def save(
        self,
        *,
        confirmation: AreaConfirmation,
        expected_confirmation_revision: int,
    ) -> AreaConfirmationReceipt:
        """Записывает одну версию подтверждения и её событие атомарно."""
        ...

    async def revoke(
        self,
        *,
        job_id: UUID,
        finding_id: str,
        actor: str,
        reason: str,
        at: datetime,
        expected_review_revision: int,
        expected_confirmation_revision: int,
    ) -> AreaConfirmationReceipt:
        """Отзывает подтверждение, сохраняя прежнюю геометрию в истории."""
        ...
