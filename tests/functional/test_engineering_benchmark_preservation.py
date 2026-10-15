# tests/functional/test_engineering_benchmark_preservation.py

"""Проверяет сохранение всех 35 групп benchmark; не оценивает способность реальной VLM найти их."""

import json
from pathlib import Path
from types import SimpleNamespace

from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.analysis import FindingDraft, GenerationMetrics

ROOT = Path(__file__).resolve().parents[2]


class PreservationModel:
    """Эмулирует сохранение заданных кандидатов; обнаружение ошибок здесь не оценивается."""

    async def generate_json(self, **request):
        """Пропускает только синтетические ID, не обращаясь к настоящей модели."""
        return SimpleNamespace(
            payload={
                "findings": [
                    {
                        "finding_id": identifier,
                        "decision": "keep",
                        "rejection_reason": "",
                        "comment": "Конкретное решение требует проверки инженером.",
                        "recommendation": "Проверить основание принятого решения.",
                        "normative_source_ids": [],
                        "experience_source_ids": [],
                    }
                    for identifier in request["schema"]["properties"]["findings"][
                        "items"
                    ]["properties"]["finding_id"]["enum"]
                ]
            },
            metrics=GenerationMetrics(1, "stop", 1000, 1, 0, 1, 1, 1, 0),
        )


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
    _, final, metrics = await FinalizeFindings(
        PreservationModel(), 1000, 10, 600, 0.55
    ).execute(
        findings=tuple(candidates),
        experience_by_finding={},
    )
    assert len(final) == 35 and metrics["vlm_call_count"] == 4
    by_id = {item.finding_id: item for item in final}
    for candidate in candidates:
        saved = by_id[candidate.finding_id]
        assert saved.evidence_locations == candidate.evidence_locations
        assert saved.evidence == candidate.evidence and saved.status == "needs_review"
        assert saved.basis_sources == ()
