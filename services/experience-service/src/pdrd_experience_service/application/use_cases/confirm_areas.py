# services/experience-service/src/pdrd_experience_service/application/use_cases/confirm_areas.py

"""Сценарии подтверждения, исправления и отзыва областей VLM.

Используются только доверенным серверным транспортом: actor поступает
из проверенной серверной идентичности, а не из JSON браузера.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pdrd_experience_service.application.ports.confirmed_areas import (
    ConfirmedAreasWriter,
)
from pdrd_experience_service.application.ports.review import ReviewRepository
from pdrd_experience_service.domain.area_confirmation import (
    AreaConfirmationReceipt,
    ConfirmationMode,
    build_confirmation,
    find_vlm_finding,
)
from pdrd_experience_service.domain.review import (
    Rectangle,
    ReviewConflictError,
    ReviewError,
)


@dataclass(frozen=True, slots=True)
class ConfirmArea:
    """Подтверждает предложенную или заново нарисованную инженером область."""

    reviews: ReviewRepository
    areas: ConfirmedAreasWriter

    async def execute(
        self,
        *,
        job_id: UUID,
        finding_id: str,
        regions: tuple[Rectangle, ...],
        mode: ConfirmationMode,
        note: str,
        actor: str,
        expected_review_revision: int,
        expected_confirmation_revision: int,
    ) -> AreaConfirmationReceipt:
        """Проверяет актуальность Review до атомарной записи в PostgreSQL."""
        session = await self.reviews.load(job_id)
        if session is None:
            raise LookupError("Human Review ещё не открыт.")

        confirmation = build_confirmation(
            session=session,
            finding_id=finding_id,
            regions=regions,
            mode=mode,
            note=note,
            actor=actor,
            at=datetime.now(UTC),
            expected_review_revision=expected_review_revision,
        )

        return await self.areas.save(
            confirmation=confirmation,
            expected_confirmation_revision=expected_confirmation_revision,
        )


@dataclass(frozen=True, slots=True)
class RevokeArea:
    """Отзывает ошибочную локализацию: замечание снова становится текстовым."""

    reviews: ReviewRepository
    areas: ConfirmedAreasWriter

    async def execute(
        self,
        *,
        job_id: UUID,
        finding_id: str,
        actor: str,
        reason: str,
        expected_review_revision: int,
        expected_confirmation_revision: int,
    ) -> AreaConfirmationReceipt:
        """Запрещает отзыв из устаревшей вкладки или от неизвестного инженера."""
        session = await self.reviews.load(job_id)
        if session is None:
            raise LookupError("Human Review ещё не открыт.")

        if session.revision != expected_review_revision:
            raise ReviewConflictError("Ревизия Human Review устарела.")

        find_vlm_finding(
            session,
            finding_id,
        )

        if not isinstance(actor, str) or not 1 <= len(actor.strip()) <= 128:
            raise ReviewError("Не указан инженер, отзывающий область.")

        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
            raise ReviewError("Для отзыва области необходима причина.")

        return await self.areas.revoke(
            job_id=job_id,
            finding_id=finding_id,
            actor=actor.strip(),
            reason=reason.strip(),
            at=datetime.now(UTC),
            expected_review_revision=expected_review_revision,
            expected_confirmation_revision=expected_confirmation_revision,
        )
