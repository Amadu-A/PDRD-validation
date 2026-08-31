# services/analysis-service/tests/unit/test_analysis_use_cases.py

"""Unit tests Analysis Service use cases."""

from typing import Any

from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
)
from pdrd_analysis_service.application.use_cases import (
    BuildNormativeQueries,
    CheckPageAgainstNorms,
    FinalizeFindings,
    UnderstandPage,
)
from pdrd_analysis_service.domain.analysis import (
    ExperienceSource,
    FindingDraft,
    GenerationMetrics,
    GenerationResult,
    NormativeSource,
    PageFacts,
)


def metrics() -> GenerationMetrics:
    """Возвращает fake VLM metrics."""
    return GenerationMetrics(
        attempt=1,
        done_reason="stop",
        requested_num_predict=100,
        total_duration_ms=10.0,
        load_duration_ms=1.0,
        prompt_eval_count=10,
        eval_count=20,
        content_length=100,
        thinking_length=0,
    )


class FakeVisionModel:
    """Fake structured VLM."""

    def __init__(
        self,
        responses: list[dict[str, Any]],
    ) -> None:
        """Сохраняет deterministic responses."""
        self.responses = list(
            responses,
        )

        self.prompts: list[str] = []

        self.call_count = 0

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
        """Возвращает следующий response."""
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage

        self.prompts.append(
            prompt,
        )

        self.call_count += 1

        return GenerationResult(
            payload=self.responses.pop(
                0,
            ),
            metrics=metrics(),
        )

    async def is_ready(self) -> bool:
        """Возвращает readiness."""
        return True


class FailingVisionModel:
    """Fake VLM с ошибкой."""

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
        """Всегда падает."""
        raise VisionModelError(
            "test VLM failure",
        )

    async def is_ready(self) -> bool:
        """Возвращает readiness."""
        return False


def page_facts() -> PageFacts:
    """Возвращает тестовые PageFacts."""
    return PageFacts(
        discipline="ЭОМ",
        page_type="scheme",
        summary="Схема электроснабжения",
        objects=("Щит ЩР-1",),
        connections=("ЩР-1 → нагрузка",),
        labels=("PE",),
        normative_queries=("требования к защитному заземлению",),
    )


def normative_source() -> NormativeSource:
    """Возвращает нормативный source."""
    return NormativeSource(
        source_id="N1",
        point_id="point-1",
        score=0.8,
        source_file="PUE.pdf",
        source_path="/norms/PUE.pdf",
        page=51,
        chunk_index=3,
        text="Металлические корпуса подлежат заземлению.",
    )


def finding() -> FindingDraft:
    """Возвращает draft finding."""
    source = normative_source()

    return FindingDraft(
        finding_id="p1-f1",
        page=1,
        page_type="scheme",
        category="normative_control",
        severity="error",
        status="confirmed",
        comment="Корпус не заземлён.",
        evidence="PE-проводник отсутствует.",
        recommendation_draft=("Предусмотреть защитное заземление."),
        confidence=0.91,
        normative_source_ids=("N1",),
        basis="PUE.pdf, PDF стр. 51",
        basis_sources=(source,),
        experience_query=("Корпус без защитного заземления"),
    )


async def test_understand_page_returns_domain_facts() -> None:
    """Проверяет page understanding."""
    model = FakeVisionModel(
        [
            {
                "discipline": "ЭОМ",
                "page_type": "scheme",
                "summary": "Схема",
                "objects": [
                    "ЩР-1",
                ],
                "connections": [
                    "ЩР-1 → двигатель",
                ],
                "labels": [
                    "PE",
                ],
                "normative_queries": [
                    "заземление оборудования",
                ],
            }
        ]
    )

    use_case = UnderstandPage(
        vision_model=model,
        num_predict=1600,
    )

    facts, result_metrics = await use_case.execute(
        page_number=1,
        heuristic_page_type="unknown",
        extracted_text="test",
        image_bytes=b"png",
    )

    assert facts.discipline == "ЭОМ"
    assert facts.page_type == "scheme"

    assert facts.normative_queries == ("заземление оборудования",)

    assert result_metrics.done_reason == "stop"


def test_normative_queries_preserve_model_queries() -> None:
    """Проверяет retrieval query builder."""
    use_case = BuildNormativeQueries(
        max_queries=7,
    )

    queries = use_case.execute(
        page_facts=page_facts(),
        extracted_text="sheet text",
        project_context_texts=("Проектом предусмотрено заземление.",),
    )

    assert queries[0] == "требования к защитному заземлению"

    assert any("Контекст ПЗ проекта" in query for query in queries)


async def test_normative_check_filters_compliance() -> None:
    """Проверяет удаление compliance confirmation."""
    model = FakeVisionModel(
        [
            {
                "summary": "Проверка завершена.",
                "violations": [
                    {
                        "category": "normative_control",
                        "severity": "info",
                        "status": "confirmed",
                        "comment": ("Заземление соответствует требованиям."),
                        "evidence": ("PE подключён."),
                        "recommendation_draft": "",
                        "confidence": 0.9,
                        "normative_source_ids": [
                            "N1",
                        ],
                    },
                    {
                        "category": "normative_control",
                        "severity": "error",
                        "status": "confirmed",
                        "comment": ("Металлический корпус не заземлён."),
                        "evidence": ("PE-проводник отсутствует."),
                        "recommendation_draft": ("Предусмотреть PE."),
                        "confidence": 0.95,
                        "normative_source_ids": [
                            "N1",
                        ],
                    },
                ],
            }
        ]
    )

    use_case = CheckPageAgainstNorms(
        vision_model=model,
        num_predict=2600,
        max_issues=10,
        normative_text_limit=700,
    )

    _, findings, _ = await use_case.execute(
        page_number=1,
        extracted_text="test",
        page_facts=page_facts(),
        normative_sources=(normative_source(),),
        image_bytes=b"png",
    )

    assert (
        len(
            findings,
        )
        == 1
    )

    assert findings[0].finding_id == "p1-f1"

    assert findings[0].normative_source_ids == ("N1",)

    assert "PUE.pdf" in findings[0].basis

    assert "Категория:" in findings[0].experience_query


async def test_normative_check_without_sources_skips_vlm() -> None:
    """Проверяет fast path без нормативных sources."""
    model = FakeVisionModel(
        [],
    )

    use_case = CheckPageAgainstNorms(
        vision_model=model,
        num_predict=2600,
        max_issues=10,
        normative_text_limit=700,
    )

    summary, findings, result_metrics = await use_case.execute(
        page_number=1,
        extracted_text="test",
        page_facts=page_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )

    assert findings == ()
    assert "не найдены" in summary

    assert result_metrics.done_reason == "no_normative_sources"

    assert model.call_count == 0


async def test_finalize_filters_low_score_experience() -> None:
    """Проверяет threshold Базы Опыта."""
    model = FakeVisionModel(
        [
            {
                "summary": "done",
                "findings": [
                    {
                        "finding_id": "p1-f1",
                        "comment": ("Отсутствует защитное заземление."),
                        "recommendation": ("Предусмотреть PE-проводник."),
                        "experience_source_ids": [
                            "E1",
                            "E2",
                        ],
                    }
                ],
            }
        ]
    )

    use_case = FinalizeFindings(
        vision_model=model,
        num_predict=1800,
        batch_size=2,
        experience_context_limit=600,
        experience_min_score=0.55,
    )

    low = ExperienceSource(
        source_id="E1",
        point_id="1",
        score=0.40,
        project_id="project",
        issue_id="low",
        issue_text="нерелевантно",
        status=None,
        verified_fixed=False,
        before_page=None,
        after_page=None,
        before_context="low",
        after_context="low",
    )

    high = ExperienceSource(
        source_id="E2",
        point_id="2",
        score=0.81,
        project_id="project",
        issue_id="high",
        issue_text="заземление",
        status=None,
        verified_fixed=True,
        before_page=1,
        after_page=2,
        before_context="без PE",
        after_context="с PE",
    )

    _, finalized, result_metrics = await use_case.execute(
        findings=(finding(),),
        experience_by_finding={
            "p1-f1": (
                low,
                high,
            )
        },
    )

    assert (
        len(
            finalized,
        )
        == 1
    )

    assert finalized[0].experience_sources == (high,)

    assert '"source_id":"E1"' not in model.prompts[0]

    assert '"source_id":"E2"' in model.prompts[0]

    assert result_metrics["experience_min_score"] == 0.55


async def test_finalize_uses_fallback_on_vlm_error() -> None:
    """Проверяет, что stylistic VLM не теряет finding."""
    use_case = FinalizeFindings(
        vision_model=FailingVisionModel(),
        num_predict=1800,
        batch_size=2,
        experience_context_limit=600,
        experience_min_score=0.55,
    )

    _, finalized, result_metrics = await use_case.execute(
        findings=(finding(),),
        experience_by_finding={},
    )

    assert (
        len(
            finalized,
        )
        == 1
    )

    assert finalized[0].comment == finding().comment

    assert result_metrics["fallback_count"] == 1
