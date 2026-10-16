# services/analysis-service/tests/unit/test_equipment_program_proof.py

"""Паспортный вывод требует точного snapshot и воспроизводимого сравнения."""

from dataclasses import replace

from pdrd_analysis_service.application.engineering_program import (
    verified_equipment_check,
)
from pdrd_analysis_service.application.equipment_pipeline import compare_equipment_stage
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.transport.http.schemas import FindingDraftPayload

from .test_engineering_program import ForbiddenVision
from .test_equipment_pipeline import _pages, _search


async def test_official_equipment_proof_survives_http_and_uses_zero_finalization_calls():
    """Сравнение точной модификации повторяется из данных, а не из категории equipment."""
    raw = compare_equipment_stage(_pages(), _search())["candidates_by_page"]["2"][0]
    draft = FindingDraftPayload.model_validate_json(
        FindingDraftPayload.model_validate(raw).model_dump_json()
    ).to_domain()
    assert verified_equipment_check(draft)
    finalizer = FinalizeFindings(ForbiddenVision(), 2000, 8, 1000, 0.5)
    _, final, metrics = await finalizer.execute(
        findings=(draft,), experience_by_finding={}
    )
    assert final[0].status == "confirmed" and metrics["vlm_call_count"] == 0
    assert not verified_equipment_check(
        replace(draft, comment="Прибор гарантированно сгорит")
    )


def test_other_variant_and_unverified_manual_do_not_confirm_failure():
    """Неподходящая буква исполнения исключает паспорт; pending остаётся вопросом инженеру."""
    search = _search()
    search["results"][0]["document"]["model"] = "DRC-100A"
    assert compare_equipment_stage(_pages(), search)["candidates_by_page"] == {}
    search = _search()
    search["results"][0]["document"]["trust_status"] = "unverified_user_override"
    raw = compare_equipment_stage(_pages(), search)["candidates_by_page"]["2"][0]
    assert raw["status"] == "needs_review"
    assert not verified_equipment_check(
        FindingDraftPayload.model_validate(raw).to_domain()
    )


def test_model_code_does_not_create_missing_supply_parameter():
    """Полное обозначение ELHART без подтверждённого питания не превращается в вывод о несовместимости."""
    pages = _pages()
    item = pages[0]["facts"]["equipment_identities"][0]
    item.update(manufacturer="ELHART", model="ECV1-L-RR-RS", parameters=[])
    search = _search()
    search["results"][0]["identity"].update(manufacturer="ELHART", model="ECV1-L-RR-RS")
    assert compare_equipment_stage(pages, search)["candidates_by_page"] == {}
