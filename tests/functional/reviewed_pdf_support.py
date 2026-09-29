# tests/functional/reviewed_pdf_support.py

"""Тестовый адаптер подтверждений с CAS; настоящий PostgreSQL проверяется отдельно."""

from pdrd_experience_service.domain.area_confirmation import (
    AreaConfirmationReceipt,
    build_confirmation,
    content_signature,
)
from pdrd_experience_service.domain.review import ReviewConflictError
from pdrd_experience_service.domain.review_export import AreaStatus


class MemoryAreas:
    """Хранит подтверждения отдельно от операционного Review."""

    def __init__(self, reviews):
        """Принимает источник неизменяемых снимков Review."""
        self.reviews, self.rows = reviews, {}

    async def load_status(self, *, review):
        """Сверяет подпись с текущим текстом и геометрией."""
        result = []
        for finding in review.findings:
            entry = self.rows.get(finding.finding_id)
            if entry is None:
                continue
            confirmation, revision, active = entry
            valid = active and confirmation.content_signature == content_signature(
                review, finding
            )
            result.append(
                AreaStatus(
                    finding.finding_id,
                    revision,
                    valid,
                    confirmation.regions if valid else (),
                )
            )
        return tuple(result)

    async def save(self, *, confirmation, expected_confirmation_revision):
        """Проверяет обе версии перед записью тестового состояния."""
        current = await self.reviews.load(confirmation.job_id)
        previous = self.rows.get(confirmation.finding_id)
        revision = previous[1] if previous else 0
        if (
            current.revision != confirmation.review_revision
            or revision != expected_confirmation_revision
        ):
            raise ReviewConflictError("Устаревшая версия.")
        self.rows[confirmation.finding_id] = (confirmation, revision + 1, True)
        return AreaConfirmationReceipt(
            confirmation.job_id, confirmation.finding_id, revision + 1, True
        )

    async def revoke(
        self,
        *,
        job_id,
        finding_id,
        expected_review_revision,
        expected_confirmation_revision,
        **kwargs,
    ):
        """Сохраняет прежние координаты при отзыве."""
        current = await self.reviews.load(job_id)
        confirmation, revision, _ = self.rows[finding_id]
        if (
            current.revision != expected_review_revision
            or revision != expected_confirmation_revision
        ):
            raise ReviewConflictError("Устаревшая версия.")
        self.rows[finding_id] = (confirmation, revision + 1, False)
        return AreaConfirmationReceipt(job_id, finding_id, revision + 1, False)


class MemoryReviewConfirmationCommitter:
    """Проверяет команду целиком до записи; реальные откаты проверяет PostgreSQL."""

    def __init__(self, reviews, areas):
        """Внедряет те же снимки и области, которые читают HTTP-маршруты."""
        self.reviews, self.areas = reviews, areas

    async def save(self, *, review, expected_revision, confirmations):
        """Идемпотентно записывает области вместе с решением тестового Review."""
        current = await self.reviews.load(review.job_id)
        if current.revision != expected_revision:
            raise ReviewConflictError("Устаревшая версия.")
        rows = dict(self.areas.rows)
        for area in confirmations:
            assert (
                build_confirmation(
                    session=review,
                    finding_id=area.finding_id,
                    regions=area.regions,
                    mode=area.mode,
                    note=area.note,
                    actor=area.confirmed_by,
                    at=area.confirmed_at,
                    expected_review_revision=review.revision,
                )
                == area
            )
            previous = rows.get(area.finding_id)
            if (
                previous
                and previous[2]
                and (
                    previous[0].content_signature == area.content_signature
                    and previous[0].regions == area.regions
                )
            ):
                continue
            rows[area.finding_id] = (area, (previous[1] if previous else 0) + 1, True)
        if review != current:
            await self.reviews.update(review, expected_revision=expected_revision)
        self.areas.rows = rows
