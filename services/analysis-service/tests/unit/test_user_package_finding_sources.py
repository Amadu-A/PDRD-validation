# services/analysis-service/tests/unit/test_user_package_finding_sources.py

"""Unit tests first-class user-package evidence in findings."""

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
    UserPackageSource,
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
        source_file="ГОСТ.pdf",
        source_path=None,
        page=10,
        chunk_index=1,
        text="Нормативное требование.",
    )


def make_user_source() -> UserPackageSource:
    """Создаёт U-source."""
    return UserPackageSource(
        source_id="U1",
        point_id="u-point",
        score=0.88,
        document_id="package-id",
        section_id="section-id",
        category_id="package-category",
        source_sha256="b" * 64,
        source_file="Требования заказчика.pdf",
        source_path=None,
        page=4,
        chunk_index=0,
        text="Заказчик требует исполнение IP54.",
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
    """Fake VLM для проверки binding N/U sources."""

    def __init__(
        self,
        response: dict[str, Any],
    ) -> None:
        """Сохраняет response."""
        self.response = response

        self.schema: dict[str, Any] | None = None

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

        return GenerationResult(
            payload=self.response,
            metrics=make_metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


async def test_user_package_only_finding_keeps_basis_metadata() -> None:
    """U-only finding сохраняет source и не становится нормативным."""
    model = FakeVisionModel(
        {
            "summary": "Найдено требование заказчика.",
            "violations": [
                {
                    "category": "normative_control",
                    "severity": "warning",
                    "status": "confirmed",
                    "comment": ("Степень защиты шкафа ниже требования заказчика."),
                    "evidence": "На листе указано IP31.",
                    "recommendation_draft": "Указать исполнение IP54.",
                    "confidence": 0.91,
                    "normative_source_ids": [],
                    "user_package_source_ids": ["U1"],
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
        user_package_sources=(make_user_source(),),
        image_bytes=b"png",
    )

    assert len(findings) == 1

    finding = findings[0]

    assert finding.category == "customer_requirements"

    assert finding.normative_source_ids == ()

    assert finding.basis == ""

    assert finding.basis_sources == ()

    assert finding.user_package_source_ids == ("U1",)

    assert finding.user_package_basis_sources == (make_user_source(),)

    assert model.schema is not None

    violation_schema = model.schema["properties"]["violations"]["items"]

    assert "user_package_source_ids" in violation_schema["properties"]


async def test_mixed_finding_preserves_n_and_u_sources() -> None:
    """Один finding может иметь раздельные N- и U-evidence одновременно."""
    model = FakeVisionModel(
        {
            "summary": "Найдено общее несоответствие.",
            "violations": [
                {
                    "category": "equipment",
                    "severity": "error",
                    "status": "confirmed",
                    "comment": "Исполнение шкафа не соответствует требованиям.",
                    "evidence": "На листе указано IP31.",
                    "recommendation_draft": "Скорректировать исполнение шкафа.",
                    "confidence": 0.93,
                    "normative_source_ids": ["N1"],
                    "user_package_source_ids": ["U1"],
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
        user_package_sources=(make_user_source(),),
        image_bytes=b"png",
    )

    assert len(findings) == 1

    finding = findings[0]

    assert finding.normative_source_ids == ("N1",)

    assert finding.basis_sources == (make_normative_source(),)

    assert finding.user_package_source_ids == ("U1",)

    assert finding.user_package_basis_sources == (make_user_source(),)


def make_user_package_finding() -> FindingDraft:
    """Создаёт U-only draft finding для finalization."""
    source = make_user_source()

    return FindingDraft(
        finding_id="p3-f1",
        page=3,
        page_type="Схема автоматизации",
        category="customer_requirements",
        severity="warning",
        status="confirmed",
        comment="Степень защиты ниже требования заказчика.",
        evidence="На листе указано IP31.",
        recommendation_draft="Указать исполнение IP54.",
        confidence=0.91,
        normative_source_ids=(),
        basis="",
        basis_sources=(),
        experience_query="IP54 по требованию заказчика",
        user_package_source_ids=("U1",),
        user_package_basis_sources=(source,),
    )


async def test_finalization_preserves_user_package_basis_sources() -> None:
    """Stylistic finalization не теряет U-source provenance."""
    model = FakeVisionModel(
        {
            "summary": "done",
            "findings": [
                {
                    "finding_id": "p3-f1",
                    "comment": "Шкаф не соответствует требованию заказчика.",
                    "recommendation": "Предусмотреть исполнение IP54.",
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
        findings=(make_user_package_finding(),),
        experience_by_finding={},
    )

    assert len(finalized) == 1

    finding = finalized[0]

    assert finding.basis_sources == ()

    assert finding.user_package_basis_sources == (make_user_source(),)
