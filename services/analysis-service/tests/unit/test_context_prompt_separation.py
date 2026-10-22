# services/analysis-service/tests/unit/test_context_prompt_separation.py

"""ПЗ сохраняется с собственной страницей и не попадает в native-проверки другого листа."""

from dataclasses import replace

from pdrd_analysis_service.application.prompts import build_normative_check_prompt
from pdrd_analysis_service.application.use_cases.normative import CheckPageAgainstNorms
from pdrd_analysis_service.domain.project_context import ProjectContextSource

from .test_analysis_use_cases import FakeVisionModel, page_facts
from .test_engineering_review import _fact


async def test_pz_full_text_is_available_without_relabelling_physical_page():
    """Конец длинной цитаты доступен модели, а факт ПЗ не становится фактом титульной страницы."""
    text = (
        "Расчётный расход на наружное пожаротушение принят 10 л/с. "
        + "Условия. " * 1000
        + "КОНЕЦ_ЦИТАТЫ"
    )
    source = ProjectContextSource("PZ1", 0.8, 16, 1, text)
    model = FakeVisionModel([{"summary": "Проверено", "violations": []}])
    _, findings, _ = await CheckPageAgainstNorms(model, 2600, 10, 700, True).execute(
        page_number=1,
        extracted_text="Титульный лист.",
        page_facts=page_facts(),
        normative_sources=(),
        image_bytes=b"test",
        project_context_sources=(source,),
    )
    prompt = model.prompts[0]
    native = prompt.split("PAGE TEXT:\n", 1)[1].split("PROJECT CONTEXT SOURCES", 1)[0]
    assert native.strip() == "Титульный лист."
    assert '"page":16' in prompt and "КОНЕЦ_ЦИТАТЫ" in prompt
    assert "принят 10 л/с" not in native
    assert findings == ()


def test_prompt_compaction_preserves_real_facts_zero_and_geometry():
    """Пустые поля удаляются из представления, но значения, условия и координаты остаются."""
    fact = _fact(
        "0",
        identifier="P0",
        property_name="Напор",
        unit_raw="м",
        scope_operating_mode="останов",
        evidence="P0: напор 0 м при останове.",
        visual_regions=[
            {"x_min": 0, "y_min": 0, "x_max": 50, "y_max": 50, "confidence": 0.9}
        ],
    )
    facts = replace(page_facts(), document_facts=(fact,))
    prompt = build_normative_check_prompt(
        page_number=3,
        extracted_text=fact.evidence_text,
        page_facts=facts,
        normative_sources=(),
        normative_text_limit=700,
    )
    assert '"value_raw":"0"' in prompt and '"x_min":0' in prompt
    assert '"scope_operating_mode":"останов"' in prompt
    assert '"table_id":""' not in prompt
    assert facts.document_facts[0] == fact
