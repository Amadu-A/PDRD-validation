# services/analysis-service/tests/unit/test_technical_assignment_finding_sources.py

"""Unit tests first-class T evidence in findings."""

from typing import Any

from pdrd_analysis_service.application.use_cases.finalization import (
    FinalizeFindings,
)
from pdrd_analysis_service.application.use_cases.normative import (
    CheckPageAgainstNorms,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
    GenerationMetrics,
    GenerationResult,
    NormativeSource,
    PageFacts,
    TechnicalAssignmentConflictCandidate,
    TechnicalAssignmentSource,
)


def make_page_facts() -> PageFacts:
    """Создаёт test page facts."""
    return PageFacts(
        discipline="КИПиА",
        page_type="Схема автоматизации",
        summary="Шкаф управления насосом.",
        objects=("Шкаф управления",),
        connections=("Сигнальный кабель",),
        labels=("ША-1",),
        normative_queries=("требования к шкафу управления",),
    )


def make_normative_source() -> NormativeSource:
    """Создаёт N-source."""
    return NormativeSource(
        source_id="N1",
        point_id="n-point",
        score=0.9,
        document_id="normative-id",
        section_id="section-id",
        category_id=None,
        source_sha256="a" * 64,
        source_file="СП 76.pdf",
        source_path=None,
        page=10,
        chunk_index=1,
        text="Нормативное требование.",
    )


def make_technical_assignment_source() -> TechnicalAssignmentSource:
    """Создаёт T-source."""
    return TechnicalAssignmentSource(
        source_id="T1",
        point_id="t-point",
        score=0.92,
        technical_assignment_id="technical-assignment-id",
        analysis_document_id="analysis-document-id",
        section_id="section-id",
        source_sha256="b" * 64,
        source_file="Техническое задание.pdf",
        page=7,
        text="Заказчик требует исполнение шкафа IP54.",
        normative_refs=("СП 76.13330.2016",),
    )


def make_metrics() -> GenerationMetrics:
    """Создаёт fake generation metrics."""
    return GenerationMetrics(
        attempt=1,
        done_reason="stop",
        requested_num_predict=100,
        total_duration_ms=1.0,
        load_duration_ms=0.1,
        prompt_eval_count=10,
        eval_count=10,
        content_length=100,
        thinking_length=0,
    )


class FakeVisionModel:
    """Fake VLM для проверки T binding."""

    def __init__(
        self,
        response: dict[str, Any],
    ) -> None:
        """Сохраняет response."""
        self.response = response

        self.schema: dict[str, Any] | None = None

        self.prompt: str | None = None

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Возвращает deterministic response."""
        assert prompt
        assert num_predict > 0
        assert seed > 0
        assert stage

        del image_bytes

        self.schema = schema

        self.prompt = prompt

        return GenerationResult(
            payload=self.response,
            metrics=make_metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Всегда ready."""
        return True


async def test_t_only_finding_is_customer_requirement() -> None:
    """T-only finding допустим и не становится нормативным."""
    model = FakeVisionModel(
        {
            "summary": "Найдено требование ТЗ.",
            "violations": [
                {
                    "category": "normative_control",
                    "severity": "warning",
                    "status": "confirmed",
                    "comment": "Степень защиты ниже требования ТЗ.",
                    "evidence": "На листе указано IP31.",
                    "recommendation_draft": "Предусмотреть IP54.",
                    "confidence": 0.92,
                    "normative_source_ids": [],
                    "technical_assignment_source_ids": [
                        "T1",
                    ],
                    "user_package_source_ids": [],
                }
            ],
        }
    )

    use_case = CheckPageAgainstNorms(
        vision_model=model,
        num_predict=2600,
        max_issues=10,
        normative_text_limit=700,
    )

    _, findings, _ = await use_case.execute(
        page_number=3,
        extracted_text="Шкаф ША-1, IP31",
        page_facts=make_page_facts(),
        normative_sources=(),
        technical_assignment_sources=(make_technical_assignment_source(),),
        image_bytes=b"png",
    )

    assert (
        len(
            findings,
        )
        == 1
    )

    finding = findings[0]

    assert finding.category == "customer_requirements"

    assert finding.normative_source_ids == ()

    assert finding.basis == ""

    assert finding.basis_sources == ()

    assert finding.technical_assignment_source_ids == ("T1",)

    assert finding.technical_assignment_basis_sources == (
        make_technical_assignment_source(),
    )

    assert model.schema is not None

    violation_schema = model.schema["properties"]["violations"]["items"]

    assert "technical_assignment_source_ids" in violation_schema["properties"]


async def test_mixed_n_and_t_sources_are_preserved() -> None:
    """Один finding может иметь N и T evidence одновременно."""
    model = FakeVisionModel(
        {
            "summary": "Найдено несоответствие.",
            "violations": [
                {
                    "category": "equipment",
                    "severity": "error",
                    "status": "confirmed",
                    "comment": "Исполнение шкафа не соответствует требованиям.",
                    "evidence": "На листе указан IP31.",
                    "recommendation_draft": "Скорректировать исполнение.",
                    "confidence": 0.95,
                    "normative_source_ids": [
                        "N1",
                    ],
                    "technical_assignment_source_ids": [
                        "T1",
                    ],
                    "user_package_source_ids": [],
                }
            ],
        }
    )

    use_case = CheckPageAgainstNorms(
        vision_model=model,
        num_predict=2600,
        max_issues=10,
        normative_text_limit=700,
    )

    _, findings, _ = await use_case.execute(
        page_number=3,
        extracted_text="Шкаф ША-1, IP31",
        page_facts=make_page_facts(),
        normative_sources=(make_normative_source(),),
        technical_assignment_sources=(make_technical_assignment_source(),),
        conflict_candidates=(
            TechnicalAssignmentConflictCandidate(
                technical_assignment_source_id="T1",
                normative_source_ids=("N1",),
                reason="Проверить совместимость требований.",
            ),
        ),
        image_bytes=b"png",
    )

    assert (
        len(
            findings,
        )
        == 1
    )

    finding = findings[0]

    assert finding.normative_source_ids == ("N1",)

    assert finding.basis_sources == (make_normative_source(),)

    assert finding.technical_assignment_source_ids == ("T1",)

    assert finding.technical_assignment_basis_sources == (
        make_technical_assignment_source(),
    )

    assert model.prompt is not None

    assert "CONFLICT CANDIDATES" in model.prompt

    assert "T1" in model.prompt

    assert "N1" in model.prompt


def make_t_finding() -> FindingDraft:
    """Создаёт T-only draft для finalization."""
    return FindingDraft(
        finding_id="p3-f1",
        page=3,
        page_type="Схема автоматизации",
        category="customer_requirements",
        severity="warning",
        status="confirmed",
        comment="Степень защиты ниже требования ТЗ.",
        evidence="На листе указан IP31.",
        recommendation_draft="Предусмотреть IP54.",
        confidence=0.91,
        normative_source_ids=(),
        basis="",
        basis_sources=(),
        experience_query="IP54 по ТЗ",
        technical_assignment_source_ids=("T1",),
        technical_assignment_basis_sources=(make_technical_assignment_source(),),
    )


async def test_finalization_preserves_t_basis_sources() -> None:
    """Finalization не теряет provenance ТЗ."""
    model = FakeVisionModel(
        {
            "summary": "done",
            "findings": [
                {
                    "finding_id": "p3-f1",
                    "comment": "Исполнение шкафа ниже требования ТЗ.",
                    "recommendation": "Предусмотреть IP54.",
                    "experience_source_ids": [],
                }
            ],
        }
    )

    use_case = FinalizeFindings(
        vision_model=model,
        num_predict=1800,
        batch_size=2,
        experience_context_limit=600,
        experience_min_score=0.55,
    )

    _, finalized, _ = await use_case.execute(
        findings=(make_t_finding(),),
        experience_by_finding={},
    )

    assert (
        len(
            finalized,
        )
        == 1
    )

    finding = finalized[0]

    assert finding.basis_sources == ()

    assert finding.technical_assignment_basis_sources == (
        make_technical_assignment_source(),
    )
