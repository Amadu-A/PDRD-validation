# services/analysis-service/tests/unit/test_project_context.py

"""Unit tests Analysis Project Context use cases."""

from typing import Any

import pytest
from pdrd_analysis_service.application.use_cases.project_context import (
    AugmentProjectContext,
    BuildProjectContextQuery,
    ValidateProjectContext,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
    PageFacts,
)
from pdrd_analysis_service.domain.project_context import (
    InvalidProjectContextError,
    ProjectContextClassification,
    ProjectContextPage,
    ProjectContextPageKind,
    ProjectContextSource,
    ProjectContextValidation,
)


class FakeVisionModel:
    """Fake structured model Project Context tests."""

    def __init__(
        self,
        *,
        kind: str = "explanatory_note",
        confidence: float = 0.95,
    ) -> None:
        """Сохраняет fake classification."""
        self.kind = kind

        self.confidence = confidence

        self.calls = 0

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[
            str,
            Any,
        ],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Возвращает classification для всех enum pages."""
        self.calls += 1

        assert prompt
        assert schema
        assert num_predict
        assert seed

        assert stage.startswith(
            "project_context_validation:",
        )

        assert image_bytes is None

        page_numbers = schema["properties"]["pages"]["items"]["properties"]["page"][
            "enum"
        ]

        return GenerationResult(
            payload={
                "pages": [
                    {
                        "page": (page_number),
                        "kind": (self.kind),
                        "confidence": (self.confidence),
                        "reason": ("Тестовая классификация."),
                    }
                    for page_number in page_numbers
                ]
            },
            metrics=GenerationMetrics(
                attempt=1,
                done_reason="stop",
                requested_num_predict=100,
                total_duration_ms=1.0,
                load_duration_ms=0.0,
                prompt_eval_count=1,
                eval_count=1,
                content_length=10,
                thinking_length=0,
            ),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def pages() -> tuple[
    ProjectContextPage,
    ...,
]:
    """Возвращает достаточно длинные страницы ПЗ."""
    return (
        ProjectContextPage(
            page_number=2,
            text=(
                "Пояснительная записка. "
                "Проектом предусматривается "
                "система электроснабжения. " * 3
            ),
        ),
        ProjectContextPage(
            page_number=3,
            text=(
                "Описание технических решений. "
                "Оборудование выбирается "
                "по условиям эксплуатации. " * 3
            ),
        ),
    )


def cached_validation(
    *,
    kind: ProjectContextPageKind = (ProjectContextPageKind.EXPLANATORY_NOTE),
    confidence: float = 0.95,
) -> ProjectContextValidation:
    """Возвращает reusable validation snapshot."""
    classifications = tuple(
        ProjectContextClassification(
            page_number=page.page_number,
            kind=kind,
            confidence=confidence,
            reason=("Cached classification."),
        )
        for page in pages()
    )

    return ProjectContextValidation(
        enabled=True,
        pages_count=len(
            classifications,
        ),
        classifications=(classifications),
        warnings=(),
    )


@pytest.mark.asyncio
async def test_validate_project_context_accepts_note() -> None:
    """Принимает уверенно распознанную ПЗ."""
    vision_model = FakeVisionModel()

    use_case = ValidateProjectContext(
        vision_model=vision_model,
        classify_batch_size=8,
        classify_num_predict=1200,
        min_text_length=80,
        reject_confidence=0.75,
    )

    (
        result,
        metrics,
    ) = await use_case.execute(
        enabled=True,
        pages=pages(),
    )

    assert result.enabled is True

    assert result.pages_count == 2

    assert result.warnings == ()

    assert (
        len(
            metrics,
        )
        == 1
    )

    assert vision_model.calls == 1


@pytest.mark.asyncio
async def test_validate_project_context_rejects_drawing() -> None:
    """Отклоняет уверенно определённый drawing."""
    vision_model = FakeVisionModel(
        kind="drawing",
        confidence=0.95,
    )

    use_case = ValidateProjectContext(
        vision_model=vision_model,
        classify_batch_size=8,
        classify_num_predict=1200,
        min_text_length=80,
        reject_confidence=0.75,
    )

    with pytest.raises(
        InvalidProjectContextError,
        match="не только",
    ):
        await use_case.execute(
            enabled=True,
            pages=pages(),
        )

    assert vision_model.calls == 1


@pytest.mark.asyncio
async def test_cached_validation_skips_vlm() -> None:
    """Warm PZ cache не вызывает VLM повторно."""
    vision_model = FakeVisionModel()

    use_case = ValidateProjectContext(
        vision_model=vision_model,
        classify_batch_size=8,
        classify_num_predict=1200,
        min_text_length=80,
        reject_confidence=0.75,
    )

    (
        result,
        metrics,
    ) = await use_case.execute(
        enabled=True,
        pages=pages(),
        cached_validation=(cached_validation()),
    )

    assert result.enabled is True

    assert result.pages_count == 2

    assert result.warnings == ()

    assert metrics == ()

    assert vision_model.calls == 0


@pytest.mark.asyncio
async def test_cached_validation_reapplies_reject_policy() -> None:
    """Cached classification проходит текущую reject policy."""
    vision_model = FakeVisionModel()

    use_case = ValidateProjectContext(
        vision_model=vision_model,
        classify_batch_size=8,
        classify_num_predict=1200,
        min_text_length=80,
        reject_confidence=0.75,
    )

    with pytest.raises(
        InvalidProjectContextError,
        match="не только",
    ):
        await use_case.execute(
            enabled=True,
            pages=pages(),
            cached_validation=(
                cached_validation(
                    kind=(ProjectContextPageKind.DRAWING),
                    confidence=0.95,
                )
            ),
        )

    assert vision_model.calls == 0


@pytest.mark.asyncio
async def test_cached_validation_rejects_other_page_range() -> None:
    """Нельзя применить cache validation к другому диапазону."""
    source = cached_validation()

    invalid = ProjectContextValidation(
        enabled=True,
        pages_count=2,
        classifications=(
            ProjectContextClassification(
                page_number=10,
                kind=(ProjectContextPageKind.EXPLANATORY_NOTE),
                confidence=0.99,
                reason="Wrong page.",
            ),
            ProjectContextClassification(
                page_number=11,
                kind=(ProjectContextPageKind.EXPLANATORY_NOTE),
                confidence=0.99,
                reason="Wrong page.",
            ),
        ),
        warnings=source.warnings,
    )

    vision_model = FakeVisionModel()

    use_case = ValidateProjectContext(
        vision_model=vision_model,
        classify_batch_size=8,
        classify_num_predict=1200,
        min_text_length=80,
        reject_confidence=0.75,
    )

    with pytest.raises(
        InvalidProjectContextError,
        match="другого диапазона",
    ):
        await use_case.execute(
            enabled=True,
            pages=pages(),
            cached_validation=invalid,
        )

    assert vision_model.calls == 0


def test_project_context_query_uses_page_facts() -> None:
    """Query содержит факты текущего листа."""
    use_case = BuildProjectContextQuery()

    query = use_case.execute(
        page_facts=PageFacts(
            discipline="ЭОМ",
            page_type="scheme",
            summary="Схема ЩР-1",
            objects=("ЩР-1",),
            connections=("PE",),
            labels=("QF1",),
            normative_queries=(),
        ),
        extracted_text=("ЩР-1 QF1 PE"),
    )

    assert "ЩР-1" in query

    assert "PE" in query

    assert "ЭОМ" in query


def test_project_context_augmentation_is_not_normative() -> None:
    """Augmentation явно отделяет ПЗ от нормативов."""
    use_case = AugmentProjectContext(
        context_text_limit=900,
    )

    result = use_case.execute(
        extracted_text=("Текст листа."),
        sources=(
            ProjectContextSource(
                source_id="PZ1",
                score=0.8,
                page=5,
                chunk_index=2,
                text=("Описание проектного решения."),
            ),
        ),
    )

    assert "ТЕКСТ АНАЛИЗИРУЕМОЙ" in result.analysis_text

    assert "PZ1" in result.analysis_text

    assert "а не нормативом" in result.analysis_text

    assert result.project_context_texts == ("Описание проектного решения.",)
