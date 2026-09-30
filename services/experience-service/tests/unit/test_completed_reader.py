# services/experience-service/tests/unit/test_completed_reader.py

"""Проверяет доверенный источник данных для открытия Human Review.

Назначение:
- не допускать подмены задания, документа и исходного PDF;
- запрещать использование незавершённого анализа;
- переносить серверные предложенные области без подтверждения;
- сохранять текст замечаний без определённой области;
- проверять взаимодействие адаптера с существующим OpenReview.
"""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pdrd_experience_service.application.ports.analysis_source import (
    CompletedAnalysisArtifacts,
)
from pdrd_experience_service.application.use_cases.review import OpenReview
from pdrd_experience_service.domain.experience_selection import (
    select_experience_candidates,
)
from pdrd_experience_service.domain.review import (
    Decision,
    Rectangle,
    ReviewError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.analysis.completed_reader import (
    VerifiedCompletedAnalysisReader,
)

JOB = UUID(int=501)
DOCUMENT = UUID(int=502)
OTHER = UUID(int=999)

PDF = b"%PDF-1.7\n% Server-side test fixture\n"
BOX = Rectangle(100, 150, 300, 350)


def make_artifacts() -> CompletedAnalysisArtifacts:
    """Создаёт согласованные артефакты одного завершённого PDF-анализа."""
    result = {
        "job_id": str(JOB),
        "document_id": str(DOCUMENT),
        "findings": [
            {
                "finding_id": "F-1",
                "page": 1,
                "comment": "Не показана маркировка двери.",
                "normative_basis": "СП 1",
            },
            {
                "finding_id": "F-2",
                "page": 1,
                "comment": "Замечание без определённой области.",
                "normative_basis": "",
            },
            {
                "finding_id": "H-1",
                "page": 1,
                "comment": "Неподтверждённая гипотеза.",
                "status": "hypothesis",
            },
        ],
    }

    visualization = {
        "job_id": str(JOB),
        "document_id": str(DOCUMENT),
        "pages": [
            {
                "page_number": 1,
                "locations": [
                    {
                        "finding_id": "F-1",
                        "status": "located",
                        "method": "analysis_vlm",
                        "regions": [
                            {
                                "bbox": {
                                    "x_min": 100,
                                    "y_min": 150,
                                    "x_max": 300,
                                    "y_max": 350,
                                },
                                "source": "analysis_vlm",
                                "confidence": 0.81,
                            }
                        ],
                    },
                    {
                        "finding_id": "F-2",
                        "status": "unlocated",
                        "regions": [],
                    },
                ],
            }
        ],
    }

    return CompletedAnalysisArtifacts(
        job_id=JOB,
        document_id=DOCUMENT,
        status="completed",
        source_filename="drawing.pdf",
        source_sha256=hashlib.sha256(PDF).hexdigest(),
        pdf_content=PDF,
        result=result,
        visualization=visualization,
    )


class FakeSource:
    """Имитирует доверенное серверное чтение сохранённых артефактов."""

    def __init__(
        self,
        artifacts: CompletedAnalysisArtifacts,
    ) -> None:
        """Сохраняет подготовленные данные и счётчик вызовов."""
        self.artifacts = artifacts
        self.calls = 0

    async def load_completed(
        self,
        job_id: UUID,
    ) -> CompletedAnalysisArtifacts:
        """Возвращает серверный набор для последующей верификации."""
        self.calls += 1
        return self.artifacts


class FakeReviews:
    """Хранит одну Review-сессию без подключения к PostgreSQL."""

    def __init__(self) -> None:
        """Создаёт пустое тестовое хранилище."""
        self.saved: ReviewSession | None = None

    async def load(
        self,
        job_id: UUID,
    ) -> ReviewSession | None:
        """Возвращает ранее созданный Review указанного задания."""
        if self.saved is not None and self.saved.job_id == job_id:
            return self.saved

        return None

    async def insert(
        self,
        session: ReviewSession,
    ) -> None:
        """Фиксирует первоначальное открытие Review."""
        if self.saved is not None:
            raise AssertionError("Повторное создание Review недопустимо.")

        self.saved = session


@pytest.mark.asyncio
async def test_reader_preserves_proposals_without_confirming_them() -> None:
    """Серверная визуализация не должна превращаться в инженерное решение."""
    source = FakeSource(
        make_artifacts(),
    )

    completed = await VerifiedCompletedAnalysisReader(
        source=source,
    ).load(
        JOB,
    )

    assert completed.job_id == JOB
    assert completed.document_id == DOCUMENT
    assert completed.source_sha256 == hashlib.sha256(PDF).hexdigest()
    assert completed.rendered_pages == (1,)

    # Промежуточная гипотеза исключается из Human Review.
    assert [finding.finding_id for finding in completed.findings] == [
        "F-1",
        "F-2",
    ]

    proposals = completed.findings[0].proposed_regions

    assert len(proposals) == 1
    assert proposals[0].bbox == BOX
    assert proposals[0].confidence == 0.81
    assert completed.findings[1].proposed_regions == ()


@pytest.mark.asyncio
async def test_open_review_preserves_unverified_visualization() -> None:
    """OpenReview получает области, но не выдаёт их за проверенные."""
    repository = FakeReviews()
    source = FakeSource(
        make_artifacts(),
    )

    use_case = OpenReview(
        analyses=VerifiedCompletedAnalysisReader(
            source=source,
        ),
        repository=repository,
    )

    review = await use_case.execute(
        job_id=JOB,
        actor="server:engineer:1",
    )

    assert repository.saved == review
    assert review.pending_count == 2

    first, second = review.findings

    assert first.proposed_regions[0].bbox == BOX
    assert first.issue_box is None
    assert second.proposed_regions == ()
    assert second.issue_box is None

    # Даже после принятия всех текстов предложенная VLM-рамка
    # не может самостоятельно стать обучающим примером.
    for finding in review.findings:
        review = review.decide(
            finding_id=finding.finding_id,
            decision=Decision.ACCEPTED,
            actor="server:engineer:1",
            at=datetime.now(UTC),
            expected_revision=review.revision,
        )

    review = review.approve(
        actor="server:engineer:1",
        at=datetime.now(UTC),
        expected_revision=review.revision,
    )

    assert (
        select_experience_candidates(
            session=review,
            confirmed_areas=(),
        )
        == ()
    )

    assert (
        len(
            review.accepted_for_pdf().findings,
        )
        == 2
    )


@pytest.mark.asyncio
async def test_unfinished_job_cannot_open_review() -> None:
    """Отсутствие статуса completed блокирует создание Review."""
    artifacts = replace(
        make_artifacts(),
        status="processing",
    )

    with pytest.raises(
        LookupError,
        match="завершённого",
    ):
        await VerifiedCompletedAnalysisReader(
            source=FakeSource(artifacts),
        ).load(
            JOB,
        )


@pytest.mark.asyncio
async def test_mismatched_pdf_checksum_is_rejected() -> None:
    """Изменённый исходный документ нельзя связать со старым результатом."""
    artifacts = replace(
        make_artifacts(),
        source_sha256="0" * 64,
    )

    with pytest.raises(ReviewError):
        await VerifiedCompletedAnalysisReader(
            source=FakeSource(artifacts),
        ).load(
            JOB,
        )


@pytest.mark.asyncio
async def test_foreign_job_identity_is_rejected() -> None:
    """Ответ другого задания не должен открывать запрошенный Review."""
    artifacts = replace(
        make_artifacts(),
        job_id=OTHER,
    )

    with pytest.raises(ReviewError):
        await VerifiedCompletedAnalysisReader(
            source=FakeSource(artifacts),
        ).load(
            JOB,
        )


@pytest.mark.asyncio
async def test_foreign_visualization_is_rejected() -> None:
    """Визуализацию чужого документа нельзя связывать с исходным PDF."""
    artifacts = make_artifacts()

    visualization = {
        **artifacts.visualization,
        "document_id": str(OTHER),
    }

    artifacts = replace(
        artifacts,
        visualization=visualization,
    )

    with pytest.raises(ReviewError):
        await VerifiedCompletedAnalysisReader(
            source=FakeSource(artifacts),
        ).load(
            JOB,
        )


@pytest.mark.asyncio
async def test_conflicting_result_metadata_is_rejected() -> None:
    """Явный чужой job_id внутри результата нельзя игнорировать."""
    artifacts = make_artifacts()

    result = {
        **artifacts.result,
        "job_id": str(OTHER),
    }

    artifacts = replace(
        artifacts,
        result=result,
    )

    with pytest.raises(ReviewError):
        await VerifiedCompletedAnalysisReader(
            source=FakeSource(artifacts),
        ).load(
            JOB,
        )


@pytest.mark.asyncio
async def test_missing_pdf_or_rendered_pages_are_rejected() -> None:
    """Не создаём Review при отсутствии исходника или листов."""
    original = make_artifacts()

    without_pdf = replace(
        original,
        pdf_content=b"",
    )

    with pytest.raises(ReviewError):
        await VerifiedCompletedAnalysisReader(
            source=FakeSource(without_pdf),
        ).load(
            JOB,
        )

    without_pages = replace(
        original,
        visualization={
            "job_id": str(JOB),
            "document_id": str(DOCUMENT),
            "pages": [],
        },
    )

    with pytest.raises(ReviewError):
        await VerifiedCompletedAnalysisReader(
            source=FakeSource(without_pages),
        ).load(
            JOB,
        )


@pytest.mark.asyncio
async def test_existing_review_does_not_reload_original_analysis() -> None:
    """Повторное открытие не должно заменять исходную историю Review."""
    repository = FakeReviews()

    source = FakeSource(
        make_artifacts(),
    )

    use_case = OpenReview(
        analyses=VerifiedCompletedAnalysisReader(
            source=source,
        ),
        repository=repository,
    )

    first = await use_case.execute(
        job_id=JOB,
        actor="server:engineer:1",
    )

    second = await use_case.execute(
        job_id=JOB,
        actor="server:engineer:2",
    )

    assert second is first
    assert source.calls == 1
    assert first.opened_by == "server:engineer:1"
