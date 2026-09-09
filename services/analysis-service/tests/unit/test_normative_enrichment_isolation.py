# services/analysis-service/tests/unit/test_normative_enrichment_isolation.py

"""Regression tests finding-local normative enrichment TZ-5.2."""

from typing import Any

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
    """Возвращает deterministic metrics."""
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
    """Fake VLM finding-local finalization."""

    def __init__(
        self,
        responses: list[
            dict[
                str,
                Any,
            ]
        ],
    ) -> None:
        """Сохраняет responses и prompts."""
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
        """Возвращает следующий response."""
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage
        assert image_bytes is None

        self.prompts.append(
            prompt,
        )

        return GenerationResult(
            payload=self.responses.pop(
                0,
            ),
            metrics=metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def finding(
    *,
    finding_id: str,
    comment: str,
) -> FindingDraft:
    """Создаёт source-less engineering finding."""
    return FindingDraft(
        finding_id=finding_id,
        page=1,
        page_type="scheme",
        category="marking",
        severity="warning",
        status="needs_review",
        comment=comment,
        evidence=("На листе видны два различающихся обозначения одного элемента."),
        recommendation_draft=(
            "Уточнить корректное обозначение "
            "и привести документацию к единому варианту."
        ),
        confidence=0.8,
        normative_source_ids=(),
        basis="",
        basis_sources=(),
        experience_query=comment,
    )


def candidate(
    *,
    source_id: str,
    point_id: str,
    source_file: str,
) -> NormativeSource:
    """Создаёт enrichment N-source."""
    return NormativeSource(
        source_id=source_id,
        point_id=point_id,
        score=0.91,
        source_file=source_file,
        source_path=None,
        page=17,
        chunk_index=3,
        text="Тестовое нормативное требование.",
        document_id=f"document-{point_id}",
        section_id="section-1",
        category_id=None,
        source_sha256="a" * 64,
    )


def build_use_case(
    model: FakeVisionModel,
) -> FinalizeFindings:
    """Создаёт finalization use case."""
    return FinalizeFindings(
        vision_model=model,
        num_predict=1800,
        batch_size=2,
        experience_context_limit=600,
        experience_min_score=0.55,
    )


async def test_candidate_from_other_finding_cannot_be_attached() -> None:
    """N соседнего finding физически не может попасть в basis."""
    first = finding(
        finding_id="p1-f1",
        comment="Первое замечание.",
    )

    second = finding(
        finding_id="p1-f2",
        comment="Второе замечание.",
    )

    first_candidate = candidate(
        source_id="NQ1_1",
        point_id="first",
        source_file="first.pdf",
    )

    second_candidate = candidate(
        source_id="NQ2_1",
        point_id="second",
        source_file="second.pdf",
    )

    model = FakeVisionModel(
        [
            {
                "summary": "done",
                "findings": [
                    {
                        "finding_id": "p1-f1",
                        "comment": "Первое замечание.",
                        "recommendation": "Проверить первое замечание.",
                        "experience_source_ids": [],
                        "normative_source_ids": [
                            "NQ2_1",
                        ],
                    }
                ],
            },
            {
                "summary": "done",
                "findings": [
                    {
                        "finding_id": "p1-f2",
                        "comment": "Второе замечание.",
                        "recommendation": "Проверить второе замечание.",
                        "experience_source_ids": [],
                        "normative_source_ids": [
                            "NQ1_1",
                        ],
                    }
                ],
            },
        ]
    )

    _, finalized, result_metrics = await build_use_case(
        model,
    ).execute(
        findings=(
            first,
            second,
        ),
        experience_by_finding={},
        normative_candidates_by_finding={
            "p1-f1": (first_candidate,),
            "p1-f2": (second_candidate,),
        },
    )

    assert (
        len(
            finalized,
        )
        == 2
    )

    assert finalized[0].basis_sources == ()
    assert finalized[1].basis_sources == ()

    assert finalized[0].finding_id == "p1-f1"
    assert finalized[1].finding_id == "p1-f2"

    assert result_metrics["isolated_normative_enrichment"] is True

    assert result_metrics["effective_batch_size"] == 1

    assert "NQ1_1" in model.prompts[0]
    assert "NQ2_1" not in model.prompts[0]

    assert "NQ2_1" in model.prompts[1]
    assert "NQ1_1" not in model.prompts[1]


async def test_each_finding_can_attach_only_its_candidate() -> None:
    """Корректные finding-local N-sources сохраняют provenance."""
    first = finding(
        finding_id="p1-f1",
        comment="Первое замечание.",
    )

    second = finding(
        finding_id="p1-f2",
        comment="Второе замечание.",
    )

    first_candidate = candidate(
        source_id="NQ1_1",
        point_id="first",
        source_file="first.pdf",
    )

    second_candidate = candidate(
        source_id="NQ2_1",
        point_id="second",
        source_file="second.pdf",
    )

    model = FakeVisionModel(
        [
            {
                "summary": "done",
                "findings": [
                    {
                        "finding_id": "p1-f1",
                        "comment": "Первое замечание.",
                        "recommendation": "Проверить первое замечание.",
                        "experience_source_ids": [],
                        "normative_source_ids": [
                            "NQ1_1",
                        ],
                    }
                ],
            },
            {
                "summary": "done",
                "findings": [
                    {
                        "finding_id": "p1-f2",
                        "comment": "Второе замечание.",
                        "recommendation": "Проверить второе замечание.",
                        "experience_source_ids": [],
                        "normative_source_ids": [
                            "NQ2_1",
                        ],
                    }
                ],
            },
        ]
    )

    _, finalized, _ = await build_use_case(
        model,
    ).execute(
        findings=(
            first,
            second,
        ),
        experience_by_finding={},
        normative_candidates_by_finding={
            "p1-f1": (first_candidate,),
            "p1-f2": (second_candidate,),
        },
    )

    assert finalized[0].basis_sources == (first_candidate,)

    assert finalized[1].basis_sources == (second_candidate,)

    assert "first.pdf" in finalized[0].basis
    assert "second.pdf" in finalized[1].basis


async def test_free_text_cannot_claim_another_normative_document() -> None:
    """ГОСТ/PUE остаётся в basis, а не в галлюцинированном comment."""
    original = finding(
        finding_id="p1-f1",
        comment=("На листе обнаружена несогласованность маркировки."),
    )

    gost = candidate(
        source_id="NQ1_1",
        point_id="gost",
        source_file="01_GOST_21.408_2013.pdf",
    )

    model = FakeVisionModel(
        [
            {
                "summary": "done",
                "findings": [
                    {
                        "finding_id": "p1-f1",
                        "comment": ("Несогласованность нарушает PUE 7.1.29."),
                        "recommendation": ("Исправить по ПУЭ 7.1.29."),
                        "experience_source_ids": [],
                        "normative_source_ids": [
                            "NQ1_1",
                        ],
                    }
                ],
            }
        ]
    )

    _, finalized, _ = await build_use_case(
        model,
    ).execute(
        findings=(original,),
        experience_by_finding={},
        normative_candidates_by_finding={"p1-f1": (gost,)},
    )

    result = finalized[0]

    assert result.basis_sources == (gost,)

    assert result.comment == original.comment

    assert result.recommendation == original.recommendation_draft

    assert "PUE" not in result.comment
    assert "ПУЭ" not in result.recommendation

    assert "01_GOST_21.408_2013.pdf" in result.basis
