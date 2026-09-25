# services/experience-service/src/pdrd_experience_service/application/ports/confirmed_areas.py

"""Порт получения подтверждённых инженером областей из доверенного backend."""

from typing import Protocol
from uuid import UUID

from pdrd_experience_service.domain.experience_selection import (
    ConfirmedFindingArea,
)


class ConfirmedAreasReader(Protocol):
    """Возвращает только явные подтверждения области, не raw VLM bbox."""

    async def load_confirmed(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ConfirmedFindingArea, ...]:
        """Читает серверные подтверждения для текущего completed job."""
        ...
