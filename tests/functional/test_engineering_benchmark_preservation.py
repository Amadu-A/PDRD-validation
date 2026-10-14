# tests/functional/test_engineering_benchmark_preservation.py

"""Проверяет сохранение всех 35 групп benchmark; не оценивает способность реальной VLM найти их."""

import json
from pathlib import Path

from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.analysis import FindingDraft

ROOT = Path(__file__).resolve().parents[2]


class NoModel:
    """Запрещает передачу эталонных комментариев в VLM и лишние GPU-вызовы."""

    async def generate_json(self, **request):
        """Подтверждённые исходным evidence фактические подозрения проходят без генерации."""
        raise AssertionError("Сохранение доказанных фактов не должно запрашивать VLM.")


async def test_all_benchmark_page_anchors_survive_factual_finalization() -> None:
    """Все исходные страницы, ID и доказательства сохраняются без повышения статуса подозрения."""
    matrix = json.loads(
        (ROOT / "tests/fixtures/engineering/review-cases.json").read_text(
            encoding="utf-8-sig"
        )
    )
    candidates = []
    for case in matrix["cases"]:
        locations = tuple(
            {
                "page": page,
                "source_id": f"D-{case['case_id']}-{page}",
                "text": "Исходный проверенный факт",
                "visual_regions": [
                    {
                        "x_min": 100,
                        "y_min": 200,
                        "x_max": 400,
                        "y_max": 500,
                        "confidence": 0.9,
                    }
                ],
            }
            for page in case["source_pages"]
        )
        candidates.append(
            FindingDraft(
                finding_id=case["case_id"],
                page=case["source_pages"][0],
                page_type="drawing",
                category="scheme_logic",
                severity="warning",
                status="needs_review",
                comment="Конкретное решение требует проверки инженером.",
                evidence="Исходный проверенный факт",
                recommendation_draft="",
                confidence=0.7,
                normative_source_ids=(),
                basis="",
                basis_sources=(),
                experience_query="",
                evidence_locations=locations,
                origin_assertions=(
                    {"origin": "engineering_verification", "evidence_grounded": True},
                ),
            )
        )
    _, final, metrics = await FinalizeFindings(NoModel(), 1000, 10, 600, 0.55).execute(
        findings=tuple(candidates),
        experience_by_finding={},
    )
    assert len(final) == 35 and metrics["vlm_call_count"] == 0
    by_id = {item.finding_id: item for item in final}
    for candidate in candidates:
        saved = by_id[candidate.finding_id]
        assert saved.evidence_locations == candidate.evidence_locations
        assert saved.evidence == candidate.evidence and saved.status == "needs_review"
        assert saved.basis_sources == ()
