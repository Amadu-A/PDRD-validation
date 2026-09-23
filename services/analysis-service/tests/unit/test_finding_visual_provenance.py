# services/analysis-service/tests/unit/test_finding_visual_provenance.py

"""Tests visual provenance generated findings without extra VLM pass."""

from typing import Any

from pdrd_analysis_service.application.json_schemas import (
    build_normative_check_schema,
)
from pdrd_analysis_service.application.technical_assignment_schema import (
    build_technical_assignment_check_schema,
)
from pdrd_analysis_service.application.use_cases.finding_visual_regions import (
    parse_finding_visual_regions,
)
from pdrd_analysis_service.application.use_cases.normative import (
    CheckPageAgainstNorms,
)
from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    CheckPageAgainstTechnicalAssignment,
)
from pdrd_analysis_service.domain.analysis import (
    FindingVisualRegion,
    GenerationMetrics,
    GenerationResult,
    PageFacts,
)
from pdrd_analysis_service.domain.technical_assignment_validation import (
    TechnicalAssignmentRequirement,
)


def _metrics() -> GenerationMetrics:
    """Возвращает deterministic fake metrics."""
    return GenerationMetrics(
        attempt=1,
        done_reason="stop",
        requested_num_predict=4000,
        total_duration_ms=10.0,
        load_duration_ms=1.0,
        prompt_eval_count=100,
        eval_count=50,
        content_length=300,
        thinking_length=0,
    )


def _facts() -> PageFacts:
    """Возвращает факты условного инженерного листа."""
    return PageFacts(
        discipline="Тепломеханические решения",
        page_type="Схема",
        summary="План и схема резервуаров.",
        objects=("Расходные резервуары",),
        connections=("Резервуары соединены с топливопроводом",),
        labels=(),
        normative_queries=(),
    )


class _NormativeVisionModel:
    """Fake VLM, возвращающая comparative finding с двумя regions."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует captured prompt."""
        self.prompt = ""

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
        """Возвращает deterministic structured finding."""
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage.startswith(
            "normative_check:",
        )
        assert image_bytes

        self.prompt = prompt

        return GenerationResult(
            payload={
                "summary": "Найдено несоответствие.",
                "violations": [
                    {
                        "category": "marking",
                        "severity": "warning",
                        "status": "needs_review",
                        "comment": (
                            "Номер листа в верхнем углу "
                            "не совпадает с основной надписью."
                        ),
                        "evidence": ("Вверху указан 9, в основной надписи — 5."),
                        "recommendation_draft": "",
                        "confidence": 0.92,
                        "visual_regions": [
                            {
                                "x_min": 900,
                                "y_min": 5,
                                "x_max": 955,
                                "y_max": 55,
                                "confidence": 0.95,
                                "label": "номер страницы 9",
                            },
                            {
                                "x_min": 905,
                                "y_min": 925,
                                "x_max": 970,
                                "y_max": 990,
                                "confidence": 0.96,
                                "label": "Лист 5",
                            },
                        ],
                        "normative_source_ids": [],
                        "technical_assignment_source_ids": [],
                        "user_package_source_ids": [],
                    }
                ],
            },
            metrics=_metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


class _TechnicalAssignmentVisionModel:
    """Fake T-first VLM с visual provenance."""

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
        """Возвращает violated T requirement с bbox."""
        assert "visual_regions" in prompt
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage.startswith(
            "technical_assignment_check:",
        )
        assert image_bytes

        return GenerationResult(
            payload={
                "decisions": {
                    "T-R1": {
                        "status": "violated",
                        "confidence": 0.91,
                    },
                },
                "issues": [
                    {
                        "requirement_id": "T-R1",
                        "severity": "warning",
                        "comment": "Не выполнено требование ТЗ.",
                        "evidence": "На листе показано иное решение.",
                        "recommendation_draft": "Уточнить решение.",
                        "visual_regions": [
                            {
                                "x_min": 100,
                                "y_min": 200,
                                "x_max": 300,
                                "y_max": 350,
                                "confidence": 0.88,
                                "label": "проверяемый узел",
                            }
                        ],
                    }
                ],
            },
            metrics=_metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def test_normative_schema_requires_visual_regions() -> None:
    """Новый candidate contract всегда содержит visual_regions."""
    schema = build_normative_check_schema(
        source_ids=(),
        max_issues=10,
    )

    item = schema["properties"]["violations"]["items"]

    assert "visual_regions" in item["properties"]

    assert "visual_regions" in item["required"]

    visual_schema = item["properties"]["visual_regions"]

    assert visual_schema["maxItems"] == 4


def test_parser_discards_invalid_visual_regions() -> None:
    """Некорректный VLM bbox не ломает analysis pipeline."""
    regions = parse_finding_visual_regions(
        [
            {
                "x_min": 100,
                "y_min": 200,
                "x_max": 300,
                "y_max": 400,
                "confidence": 0.9,
                "label": "valid",
            },
            {
                "x_min": 500,
                "y_min": 500,
                "x_max": 400,
                "y_max": 600,
                "confidence": 0.9,
                "label": "invalid",
            },
        ]
    )

    assert regions == (
        FindingVisualRegion(
            x_min=100,
            y_min=200,
            x_max=300,
            y_max=400,
            confidence=0.9,
            label="valid",
        ),
    )


async def test_normative_check_preserves_visual_regions_and_business_matrix() -> None:
    """Finding хранит regions из того же VLM-вызова, где был найден."""
    model = _NormativeVisionModel()

    use_case = CheckPageAgainstNorms(
        vision_model=model,
        num_predict=14000,
        max_issues=50,
        normative_text_limit=700,
    )

    _, findings, _ = await use_case.execute(
        page_number=9,
        extracted_text="Вверху 9. Основная надпись: Лист 5.",
        page_facts=_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )

    assert (
        len(
            findings,
        )
        == 1
    )

    finding = findings[0]

    assert (
        len(
            finding.visual_regions,
        )
        == 2
    )

    assert finding.visual_regions[0].label == "номер страницы 9"

    assert finding.visual_regions[1].label == "Лист 5"

    assert "BUSINESS CHECK MATRIX" in model.prompt
    assert "ЛОГИКА РАБОТЫ И СХЕМЫ" in model.prompt
    assert "VISUAL EVIDENCE REGIONS" in model.prompt


async def test_t_first_preserves_visual_region_in_finding() -> None:
    """Independent T-first finding тоже хранит исходный VLM bbox."""
    requirement = TechnicalAssignmentRequirement(
        point_id="point-1",
        requirement_id="T-R1",
        requirement_index=1,
        page=4,
        strength="explicit",
        scopes=("heating",),
        normative_refs=(),
        source_text="Требование.",
        text="Должно быть предусмотрено требуемое решение.",
    )

    use_case = CheckPageAgainstTechnicalAssignment(
        vision_model=(_TechnicalAssignmentVisionModel()),
        num_predict=4000,
        batch_size=48,
        requirement_text_limit=1800,
    )

    (
        _summary,
        decisions,
        findings,
        _metrics_result,
    ) = await use_case.execute(
        page_number=19,
        page_type="Схема",
        extracted_text="Проверяемый узел.",
        page_facts=_facts(),
        image_bytes=b"png",
        technical_assignment_id=("11111111-1111-4111-8111-111111111111"),
        analysis_document_id=("22222222-2222-4222-8222-222222222222"),
        section_id=("33333333-3333-4333-8333-333333333333"),
        source_file="ТЗ.pdf",
        source_sha256="a" * 64,
        requirements=(requirement,),
    )

    assert decisions[0].visual_regions[0].label == "проверяемый узел"

    assert findings[0].visual_regions == decisions[0].visual_regions


def test_t_first_schema_requires_visual_regions_for_issue() -> None:
    """T-first issue contract тоже хранит visual provenance."""
    schema = build_technical_assignment_check_schema(("T-R1",))

    issue = schema["properties"]["issues"]["items"]

    assert "visual_regions" in issue["properties"]

    assert "visual_regions" in issue["required"]
