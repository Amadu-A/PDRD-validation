# services/experience-service/src/pdrd_experience_service/application/ports/review_confirmation.py

"""Атомарная запись решения Review и проверенных этим действием областей.

Порт объединяет снимок, журнал решения и аудит координат одной транзакцией.
Простая запись браузерной геометрии этот порт не вызывает.
"""

from typing import Protocol

from pdrd_experience_service.domain.area_confirmation import AreaConfirmation
from pdrd_experience_service.domain.review import ReviewSession


class ReviewConfirmationCommitter(Protocol):
    """Фиксирует решение и области вместе с CAS; сбой откатывает обе части."""

    async def save(
        self,
        *,
        review: ReviewSession,
        expected_revision: int,
        confirmations: tuple[AreaConfirmation, ...],
    ) -> None:
        """Сохраняет редакцию и аудит областей, не дублируя актуальное подтверждение."""
        ...
