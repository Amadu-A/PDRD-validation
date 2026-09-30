# services/experience-service/src/pdrd_experience_service/application/use_cases/export_review.py

"""Чтение согласованной проекции Review для серверного формирования PDF."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_experience_service.application.ports.confirmed_areas import (
    ConfirmedAreasReader,
)
from pdrd_experience_service.application.ports.review import ReviewRepository
from pdrd_experience_service.domain.review import ReviewConflictError
from pdrd_experience_service.domain.review_export import export_manifest


@dataclass(frozen=True, slots=True)
class ExportReview:
    """Проверяет утверждение и повторно сверяет ревизию после чтения областей."""

    reviews: ReviewRepository
    areas: ConfirmedAreasReader

    async def execute(self, *, job_id: UUID) -> dict:
        """Gateway повторно запрашивает эту проекцию перед выдачей готового PDF."""
        review = await self.reviews.load(job_id)
        if review is None:
            raise LookupError("Human Review ещё не открыт.")
        review.accepted_for_pdf()
        areas = await self.areas.load_status(review=review)
        current = await self.reviews.load(job_id)
        if current != review:
            raise ReviewConflictError("Review изменён во время подготовки PDF.")
        return export_manifest(review, areas)
