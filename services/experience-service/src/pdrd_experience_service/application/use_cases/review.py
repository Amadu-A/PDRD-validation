# services/experience-service/src/pdrd_experience_service/application/use_cases/review.py

"""Открытие и команды Human Review через атомарное хранилище с ревизиями."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pdrd_experience_service.application.ports.review import (
    CompletedAnalysisReader,
    ReviewRepository,
)
from pdrd_experience_service.application.ports.review_confirmation import (
    ReviewConfirmationCommitter,
)
from pdrd_experience_service.domain.review import (
    ApprovedReview,
    Decision,
    Rectangle,
    ReviewConflictError,
    ReviewSession,
)
from pdrd_experience_service.domain.review_confirmation import (
    reviewed_area_confirmations,
)


@dataclass(frozen=True, slots=True)
class OpenReview:
    """Открывает Review по исходному анализу, полученному сервером."""

    analyses: CompletedAnalysisReader
    repository: ReviewRepository

    async def execute(
        self,
        *,
        job_id: UUID,
        actor: str,
    ) -> ReviewSession:
        """Сохраняет оригиналы один раз, включая конкурентное открытие Review."""
        existing = await self.repository.load(job_id)

        if existing is not None:
            return existing

        source = await self.analyses.load(job_id)

        if source.job_id != job_id:
            raise ValueError("Analysis reader returned a different job.")

        session = ReviewSession.open(
            job_id=job_id,
            document_id=source.document_id,
            source_filename=source.source_filename,
            source_sha256=source.source_sha256,
            originals=source.findings,
            rendered_pages=source.rendered_pages,
            actor=actor,
            at=datetime.now(UTC),
        )

        try:
            await self.repository.insert(session)
        except ReviewConflictError:
            # Параллельное открытие не подменяет оригинал уже созданного Review.
            existing = await self.repository.load(job_id)
            if existing is None:
                raise
            return existing

        return session


@dataclass(frozen=True, slots=True)
class ChangeReview:
    """Исполняет команды с проверкой ревизии через внедрённые порты хранения."""

    repository: ReviewRepository
    confirmations: ReviewConfirmationCommitter | None = None

    async def decide(
        self,
        *,
        job_id: UUID,
        finding_id: str,
        decision: Decision,
        actor: str,
        expected_revision: int,
    ) -> ReviewSession:
        """Атомарно сохраняет решение и принятые им текущие области замечания."""
        current = await self._require(job_id)
        at = datetime.now(UTC)

        updated = current.decide(
            finding_id=finding_id,
            decision=decision,
            actor=actor,
            at=at,
            expected_revision=expected_revision,
        )

        areas = reviewed_area_confirmations(
            review=updated, finding_id=finding_id, actor=actor, at=at
        )
        if self.confirmations is not None:
            await self.confirmations.save(
                review=updated,
                expected_revision=current.revision,
                confirmations=areas,
            )
        elif areas:
            raise RuntimeError("Атомарное сохранение решения и области не подключено.")
        elif updated is not current:
            await self.repository.update(
                updated,
                expected_revision=current.revision,
            )

        return updated

    async def edit(
        self,
        *,
        job_id: UUID,
        finding_id: str,
        text: str,
        normative_basis: str,
        actor: str,
        expected_revision: int,
    ) -> ReviewSession:
        """Persist a correction and invalidate previous export approval."""
        current = await self._require(job_id)

        updated = current.edit(
            finding_id=finding_id,
            text=text,
            normative_basis=normative_basis,
            actor=actor,
            at=datetime.now(UTC),
            expected_revision=expected_revision,
        )

        if updated is not current:
            await self.repository.update(
                updated,
                expected_revision=current.revision,
            )

        return updated

    async def add_manual(
        self,
        *,
        job_id: UUID,
        finding_id: str,
        page_number: int,
        text: str,
        normative_basis: str,
        issue_box: Rectangle,
        callout_box: Rectangle,
        actor: str,
        expected_revision: int,
    ) -> ReviewSession:
        """Persist Gold geometry and source metadata on an approved page."""
        current = await self._require(job_id)

        updated = current.add_manual(
            finding_id=finding_id,
            page_number=page_number,
            text=text,
            normative_basis=normative_basis,
            issue_box=issue_box,
            callout_box=callout_box,
            actor=actor,
            at=datetime.now(UTC),
            expected_revision=expected_revision,
        )

        await self.repository.update(
            updated,
            expected_revision=current.revision,
        )

        return updated

    async def reset_decision(
        self,
        *,
        job_id: UUID,
        finding_id: str,
        actor: str,
        expected_revision: int,
    ) -> ReviewSession:
        """Сохраняет отмену решения отдельным событием без удаления истории."""
        current = await self._require(job_id)
        updated = current.reset_decision(
            finding_id=finding_id,
            actor=actor,
            at=datetime.now(UTC),
            expected_revision=expected_revision,
        )
        if updated is not current:
            await self.repository.update(updated, expected_revision=current.revision)
        return updated

    async def change_geometry(
        self,
        *,
        job_id: UUID,
        finding_id: str,
        regions: tuple[Rectangle, ...],
        callout_box: Rectangle | None,
        actor: str,
        expected_revision: int,
    ) -> ReviewSession:
        """Сохраняет геометрию без подтверждения; её примет следующая зелёная галочка."""
        current = await self._require(job_id)
        updated = current.change_geometry(
            finding_id=finding_id,
            regions=regions,
            callout_box=callout_box,
            actor=actor,
            at=datetime.now(UTC),
            expected_revision=expected_revision,
        )
        if updated is not current:
            await self.repository.update(updated, expected_revision=current.revision)
        return updated

    async def approve(
        self,
        *,
        job_id: UUID,
        actor: str,
        expected_revision: int,
    ) -> ReviewSession:
        """Утверждает полный Review и его принятые области одним серверным действием."""
        current = await self._require(job_id)
        at = datetime.now(UTC)

        updated = current.approve(
            actor=actor,
            at=at,
            expected_revision=expected_revision,
        )

        areas = reviewed_area_confirmations(review=updated, actor=actor, at=at)
        if self.confirmations is not None:
            await self.confirmations.save(
                review=updated,
                expected_revision=current.revision,
                confirmations=areas,
            )
        elif areas:
            raise RuntimeError("Атомарное сохранение решения и области не подключено.")
        elif updated is not current:
            await self.repository.update(
                updated,
                expected_revision=current.revision,
            )

        return updated

    async def approved_snapshot(
        self,
        *,
        job_id: UUID,
    ) -> ApprovedReview:
        """Read only a fully approved revision to be passed to PDF writer."""
        current = await self._require(job_id)

        return current.accepted_for_pdf()

    async def _require(
        self,
        job_id: UUID,
    ) -> ReviewSession:
        """Resolve session without creating one implicitly on a write."""
        current = await self.repository.load(job_id)

        if current is None:
            raise LookupError(f"Review session {job_id} was not opened.")

        return current
