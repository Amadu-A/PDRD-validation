# services/analysis-service/tests/unit/test_duplicate_finalization.py

"""Детерминированный повтор сохраняется вне VLM-финализатора."""

from pdrd_analysis_service.application.use_cases.finalization import (
    FinalizeFindings,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
)


class MustNotCallVisionModel:
    """Запрещает непредусмотренные повторные запросы к VLM."""

    async def generate_json(self, **kwargs):
        """Детерминированное замечание не должно запускать модель."""
        raise AssertionError(f"Unexpected VLM call: {sorted(kwargs)}")


async def test_verified_repeated_tag_survives_without_finalizer_vlm_call() -> None:
    """Проверяет сохранение факта повторения, а не выдуманного нарушения."""
    finding = FindingDraft(
        finding_id="p22-dpos-8-9-3",
        page=22,
        page_type="Схема",
        category="marking",
        severity="warning",
        status="needs_review",
        comment="Проверить повторное позиционное обозначение 8.9.3.",
        evidence="В исходном тексте листа повторяется 8.9.3.",
        recommendation_draft="Сопоставить элементы.",
        confidence=0.65,
        normative_source_ids=(),
        basis="",
        basis_sources=(),
        experience_query="",
    )
    use_case = FinalizeFindings(
        vision_model=MustNotCallVisionModel(),
        num_predict=1800,
        batch_size=2,
        experience_context_limit=600,
        experience_min_score=0.55,
    )
    _summary, findings, metrics = await use_case.execute(
        findings=(finding,), experience_by_finding={}
    )
    assert len(findings) == 1
    assert findings[0].finding_id == "p22-dpos-8-9-3"
    assert findings[0].status == "needs_review"
    assert findings[0].basis_sources == ()
    assert metrics["candidate_count"] == metrics["kept_count"] == 1
    assert metrics["deterministic_review_count"] == 1
    assert metrics["vlm_call_count"] == 0
    assert metrics["rejected_count"] == 0
