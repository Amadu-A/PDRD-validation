# services/analysis-service/tests/unit/test_finding_localization_prompt.py

"""Tests prompt semantics evidence-area localization."""

from pdrd_analysis_service.application.use_cases.finding_localization import (
    build_finding_localization_prompt,
)
from pdrd_analysis_service.domain.visualization import (
    FindingLocalizationTarget,
)


def test_absent_object_prompt_requests_evidence_area_not_fake_object_bbox() -> None:
    """Отсутствующий объект локализуется по видимому контексту проверки."""
    prompt = build_finding_localization_prompt(
        page_number=23,
        extracted_text=("Схема сети\nТ2.1, Ду50\nТ1.1, Ду50\nКотельная"),
        findings=(
            FindingLocalizationTarget(
                finding_id="F-1",
                comment=("На листе выявлено несоответствие."),
                evidence=(
                    "Линии T1.1 и T2.1 не содержат изображения запорного клапана."
                ),
            ),
        ),
    )

    assert "ОБЛАСТЬ ДОКАЗАТЕЛЬСТВА ОТСУТСТВИЯ" in prompt

    assert "НЕ придумывай bbox самого отсутствующего объекта" in prompt

    assert "status=unlocated, bbox=null используй только" in prompt

    assert "T1.1" in prompt
    assert "Т1.1" in prompt
