# services/analysis-service/tests/unit/test_normative_enrichment.py

"""Regression tests non-destructive normative enrichment TZ-5.2."""

from typing import Any

from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
)
from pdrd_analysis_service.application.prompts import (
    build_finalization_prompt,
)
from pdrd_analysis_service.application.use_cases import (
    FinalizeFindings,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
    GenerationMetrics,
    GenerationResult,
    NormativeSource,
)


def metrics() -> GenerationMetrics:
    """Возвращает deterministic fake metrics."""
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
        responses: list[
            dict[
                str,
                Any,
            ]
        ],
    ) -> None:
        """Сохраняет deterministic responses."""
        self.responses = list(
            responses,
        )

        self.prompts: list[str] = []

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
        """Возвращает следующий fake response."""
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage

        self.prompts.append(
            prompt,
        )

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
    """Fake VLM с ошибкой финализации."""

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
        """Всегда выбрасывает VisionModelError."""
        raise VisionModelError(
            "test finalization failure",
        )

    async def is_ready(self) -> bool:
        """Возвращает readiness."""
        return False


def old_normative_source() -> NormativeSource:
    """Возвращает тематически похожий старый N-source."""
    return NormativeSource(
        source_id="N1",
        point_id="old-point",
        score=0.84,
        source_file="PUE.pdf",
        source_path=None,
        page=62,
        chunk_index=3,
        text=(
            "Прокладка защитных проводников "
            "через стены должна выполняться "
            "без соединений и ответвлений."
        ),
        document_id="old-document",
        section_id="section-1",
        category_id=None,
        source_sha256="a" * 64,
    )


def enrichment_source() -> NormativeSource:
    """Возвращает targeted N-candidate."""
    return NormativeSource(
        source_id="NQ1",
        point_id="new-point",
        score=0.93,
        source_file="GOST_Example.pdf",
        source_path=None,
        page=17,
        chunk_index=5,
        text=(
            "На принципиальной схеме должно быть "
            "указано обозначение проверяемого элемента."
        ),
        document_id="new-document",
        section_id="section-1",
        category_id=None,
        source_sha256="b" * 64,
    )


def engineering_finding() -> FindingDraft:
    """Возвращает source-less инженерное замечание."""
    return FindingDraft(
        finding_id="p1-f1",
        page=1,
        page_type="scheme",
        category="marking",
        severity="warning",
        status="needs_review",
        comment=("На двух частях схемы одно обозначение указано по-разному."),
        evidence=(
            "В первой части листа указано X1, во второй для того же элемента — X2."
        ),
        recommendation_draft=(
            "Уточнить корректное обозначение и привести лист к единому варианту."
        ),
        confidence=0.88,
        normative_source_ids=(),
        basis="",
        basis_sources=(),
        experience_query=("Несогласованное обозначение X1/X2."),
    )


def normative_finding() -> FindingDraft:
    """Возвращает finding со старым N-source."""
    source = old_normative_source()

    return FindingDraft(
        finding_id="p2-f1",
        page=2,
        page_type="scheme",
        category="normative_control",
        severity="warning",
        status="confirmed",
        comment=("На схеме не указаны тип и количество заземляющих проводников."),
        evidence=("На листе отсутствует информация о типе и количестве проводников."),
        recommendation_draft=("Проверить необходимость указания этих данных на схеме."),
        confidence=0.8,
        normative_source_ids=("N1",),
        basis="PUE.pdf, PDF стр. 62",
        basis_sources=(source,),
        experience_query=("Отсутствуют данные о защитных проводниках."),
    )


def build_use_case(
    model: FakeVisionModel | FailingVisionModel,
) -> FinalizeFindings:
    """Создаёт use case с test settings."""
    return FinalizeFindings(
        vision_model=model,
        num_predict=1800,
        batch_size=3,
        experience_context_limit=600,
        experience_min_score=0.55,
    )


async def test_source_less_finding_can_gain_normative_basis() -> None:
    """Targeted N можно пришить, не меняя существование finding."""
    candidate = enrichment_source()

    model = FakeVisionModel(
        [
            {
                "summary": "done",
                "findings": [
                    {
                        "finding_id": "p1-f1",
                        "comment": (
                            "Обозначение одного элемента на листе не согласовано."
                        ),
                        "recommendation": (
                            "Уточнить обозначение и привести его к единому варианту."
                        ),
                        "experience_source_ids": [],
                        "normative_source_ids": [
                            "NQ1",
                        ],
                    }
                ],
            }
        ]
    )

    _, finalized, result_metrics = await build_use_case(
        model,
    ).execute(
        findings=(engineering_finding(),),
        experience_by_finding={},
        normative_candidates=(candidate,),
    )

    assert (
        len(
            finalized,
        )
        == 1
    )

    result = finalized[0]

    assert result.finding_id == "p1-f1"

    assert result.category == "marking"

    assert result.status == "needs_review"

    assert result.basis_sources == (candidate,)

    assert "GOST_Example.pdf" in result.basis

    assert result_metrics["normative_candidates_count"] == 1


async def test_irrelevant_old_normative_can_be_detached_without_losing_finding() -> (
    None
):
    """Слабый N удаляется из basis, но finding обязательно остаётся."""
    model = FakeVisionModel(
        [
            {
                "summary": "done",
                "findings": [
                    {
                        "finding_id": "p2-f1",
                        "comment": (
                            "Необходимо проверить полноту "
                            "данных о защитных проводниках."
                        ),
                        "recommendation": (
                            "Проверить, требуется ли эта информация "
                            "именно на данном типе схемы."
                        ),
                        "experience_source_ids": [],
                        "normative_source_ids": [],
                    }
                ],
            }
        ]
    )

    _, finalized, _ = await build_use_case(
        model,
    ).execute(
        findings=(normative_finding(),),
        experience_by_finding={},
    )

    assert (
        len(
            finalized,
        )
        == 1
    )

    result = finalized[0]

    assert result.finding_id == "p2-f1"

    assert result.basis_sources == ()

    assert result.basis == ""

    assert result.category == "other"

    assert result.status == "needs_review"


async def test_no_normative_match_never_deletes_engineering_finding() -> None:
    """Отсутствие подходящего N не влияет на существование finding."""
    model = FakeVisionModel(
        [
            {
                "summary": "done",
                "findings": [
                    {
                        "finding_id": "p1-f1",
                        "comment": ("Обозначение X1/X2 требует проверки."),
                        "recommendation": ("Уточнить правильное обозначение."),
                        "experience_source_ids": [],
                        "normative_source_ids": [],
                    }
                ],
            }
        ]
    )

    _, finalized, _ = await build_use_case(
        model,
    ).execute(
        findings=(engineering_finding(),),
        experience_by_finding={},
        normative_candidates=(),
    )

    assert (
        len(
            finalized,
        )
        == 1
    )

    result = finalized[0]

    assert result.finding_id == "p1-f1"

    assert result.status == "needs_review"

    assert result.basis_sources == ()


async def test_finalization_error_preserves_original_finding_and_basis() -> None:
    """Ошибка enrichment не уничтожает уже найденное замечание."""
    original = normative_finding()

    _, finalized, result_metrics = await build_use_case(
        FailingVisionModel(),
    ).execute(
        findings=(original,),
        experience_by_finding={},
        normative_candidates=(enrichment_source(),),
    )

    assert (
        len(
            finalized,
        )
        == 1
    )

    result = finalized[0]

    assert result.finding_id == original.finding_id

    assert result.comment == original.comment

    assert result.basis == original.basis

    assert result.basis_sources == (old_normative_source(),)

    assert result_metrics["fallback_count"] == 1


def test_finalization_prompt_declares_non_destructive_enrichment() -> None:
    """Prompt явно запрещает удалять finding из-за N retrieval."""
    prompt = build_finalization_prompt(
        findings=(engineering_finding(),),
        experience_by_finding={},
        experience_context_limit=600,
        normative_candidates=(enrichment_source(),),
    )

    assert "НИКОГДА не удаляй finding" in prompt

    assert "Finding всё равно обязательно возвращается." in prompt

    assert "NORMATIVE CANDIDATES" in prompt

    assert "Тематического сходства недостаточно" in prompt

    assert '"source_id":"NQ1"' in prompt

    assert "GOST_Example.pdf" in prompt
