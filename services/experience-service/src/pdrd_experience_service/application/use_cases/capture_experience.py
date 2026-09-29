# services/experience-service/src/pdrd_experience_service/application/use_cases/capture_experience.py

"""Материализация утверждённых примеров из проверенного оригинала и координат.

Повторный вызов не создаёт дубли. Проверка под блокировкой PostgreSQL
закрывает гонки изменения Review и отзыва областей во время crop.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from pdrd_experience_service.application.ports.analysis_source import (
    AnalysisSourceReader,
)
from pdrd_experience_service.application.ports.catalog import (
    CatalogRepository,
    CropRenderer,
    CropStore,
)
from pdrd_experience_service.application.ports.confirmed_areas import (
    ConfirmedAreasReader,
)
from pdrd_experience_service.application.ports.review import ReviewRepository
from pdrd_experience_service.core.observability import log_execution_time
from pdrd_experience_service.domain.catalog import Example
from pdrd_experience_service.domain.experience_selection import (
    select_experience_candidates,
)
from pdrd_experience_service.domain.review import ReviewConflictError, ReviewError


@dataclass(frozen=True, slots=True)
class CaptureExperience:
    """Сохраняет все пригодные примеры текущего утверждённого задания."""

    reviews: ReviewRepository
    areas: ConfirmedAreasReader
    source: AnalysisSourceReader
    renderer: CropRenderer
    crops: CropStore
    catalog: CatalogRepository

    @log_execution_time(operation="experience_capture")
    async def execute(
        self, *, job_id: UUID, expected_revision: int, actor: str
    ) -> dict:
        """Не принимает текст, координаты или происхождение от браузера."""
        review = await self.reviews.load(job_id)
        if review is None:
            raise LookupError("Human Review не найден.")
        review.accepted_for_pdf()
        if review.revision != expected_revision:
            raise ReviewConflictError("Review изменён перед сохранением Experience.")
        statuses = await self.areas.load_status(review=review)
        confirmed = await self.areas.load_confirmed(job_id=job_id)
        candidates = select_experience_candidates(
            session=review, confirmed_areas=confirmed
        )
        source = await self.source.load_completed(job_id)
        if (
            source.job_id != job_id
            or source.document_id != review.document_id
            or source.status != "completed"
            or source.source_sha256 != review.source_sha256
            or hashlib.sha256(source.pdf_content).hexdigest() != review.source_sha256
            or not source.pdf_content.startswith(b"%PDF-")
        ):
            raise ReviewError("Исходный PDF не соответствует утверждённому Review.")
        examples = []
        at = datetime.now(UTC)
        for candidate in candidates:
            example_id = uuid5(
                NAMESPACE_URL,
                f"pdrd-experience:{candidate.example_key}:review:{candidate.approved_revision}",
            )
            existing = await self.catalog.get(example_id)
            if existing is not None:
                continue
            images = await self.renderer.render(
                pdf=source.pdf_content,
                page_number=candidate.page_number,
                regions=candidate.issue_regions,
            )
            if len(images) != len(candidate.issue_regions):
                raise RuntimeError("Document Service вернул неполный crop.")
            crops = tuple([await self.crops.put(content) for content in images])
            examples.append(
                Example(
                    id=example_id,
                    source=candidate,
                    crops=crops,
                    revision=0,
                    document_title=review.source_filename,
                    text=candidate.text,
                    normative_basis=candidate.normative_basis,
                    normative_reference="",
                    active=True,
                    rejection_reason="",
                    negative_target="",
                    created_at=at,
                    updated_at=at,
                    curated_by=actor,
                )
            )
        if await self.reviews.load(job_id) != review:
            raise ReviewConflictError("Review изменён во время подготовки crop.")
        current = await self.areas.load_status(review=review)
        if current != statuses:
            raise ReviewConflictError("Подтверждённые области изменены во время crop.")
        result = await self.catalog.capture(
            review=review,
            area_versions=tuple((area.finding_id, area.revision) for area in statuses),
            examples=tuple(examples),
            actor=actor,
        )
        return {
            **result,
            "eligible": len(candidates),
            "excluded": len(review.findings) - len(candidates),
        }
