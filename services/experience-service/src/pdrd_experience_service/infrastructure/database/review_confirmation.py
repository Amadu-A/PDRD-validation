# services/experience-service/src/pdrd_experience_service/infrastructure/database/review_confirmation.py

"""PostgreSQL-транзакция совместного принятия Review и текущих областей.

Блокировка Review общая с правкой координат и отзывом. Снимок, событие решения
и все события областей фиксируются вместе; сбой любой вставки откатывает всё.
Не содержит сетевых вызовов, PDF-рендеринга или выбора обучающих примеров.
"""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_experience_service.domain.area_confirmation import (
    AreaConfirmation,
    build_confirmation,
)
from pdrd_experience_service.domain.review import Action, ReviewError, ReviewSession
from pdrd_experience_service.infrastructure.database.codec import snapshot_to_json
from pdrd_experience_service.infrastructure.database.confirmed_areas import (
    SqlAlchemyConfirmedAreasRepository,
)
from pdrd_experience_service.infrastructure.database.models import ReviewSessionModel
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)


class SqlAlchemyReviewConfirmationCommitter:
    """Реализует атомарный application port на той же фабрике сессий Experience."""

    def __init__(
        self,
        sessions: Callable[[], AsyncSession],
        areas: SqlAlchemyConfirmedAreasRepository,
    ) -> None:
        """Внедряет общую фабрику и адаптер записи аудита координат."""
        self._sessions, self._areas = sessions, areas

    async def save(
        self,
        *,
        review: ReviewSession,
        expected_revision: int,
        confirmations: tuple[AreaConfirmation, ...],
    ) -> None:
        """Сохраняет новую либо идемпотентную редакцию и области под одним CAS."""
        async with self._sessions() as database, database.begin():
            current = await self._areas._locked_review(
                database, review.job_id, expected_revision
            )
            if review != current:
                if (
                    review.revision != expected_revision + 1
                    or review.history[:-1] != current.history
                    or review.history[-1].action
                    not in (Action.DECIDED, Action.APPROVED)
                    or review.history[-1].session_revision != review.revision
                    or review.document_id != current.document_id
                    or review.source_sha256 != current.source_sha256
                ):
                    raise ReviewError(
                        "Некорректная редакция совместного решения Review."
                    )
                row = await database.get(ReviewSessionModel, review.job_id)
                row.revision = review.revision
                row.approved_revision = review.approved_revision
                row.snapshot = snapshot_to_json(review)
                row.updated_at = review.history[-1].occurred_at
                database.add(
                    SqlAlchemyReviewRepository._event_row(
                        review.job_id, review.history[-1]
                    )
                )
            for confirmation in confirmations:
                checked = build_confirmation(
                    session=review,
                    finding_id=confirmation.finding_id,
                    regions=confirmation.regions,
                    mode=confirmation.mode,
                    note=confirmation.note,
                    actor=confirmation.confirmed_by,
                    at=confirmation.confirmed_at,
                    expected_review_revision=review.revision,
                )
                if checked != confirmation:
                    raise ReviewError("Область не соответствует сохраняемому решению.")
                await self._areas.write_confirmation(
                    database, confirmation=checked, keep_current=True
                )
