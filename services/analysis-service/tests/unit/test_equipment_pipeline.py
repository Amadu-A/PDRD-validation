# services/analysis-service/tests/unit/test_equipment_pipeline.py

"""Проверяет сквозной контракт EQ-кандидата перед общим Finalization."""

from pdrd_analysis_service.application.equipment_pipeline import compare_equipment_stage
from pdrd_analysis_service.transport.http.schemas import FindingDraftPayload


def _pages() -> list[dict]:
    """Возвращает проектное значение с доказательством на листе."""
    return [
        {
            "page_number": 2,
            "page_type": "schematic",
            "facts": {
                "equipment_identities": [
                    {
                        "manufacturer": "MEAN WELL",
                        "model": "DRC-100B",
                        "variant": "",
                        "status": "resolved",
                        "confidence": 0.97,
                        "object_ref": "PS1",
                        "evidence_text": "PS1 DRC-100B",
                        "visual_regions": [],
                        "parameters": [
                            {
                                "property_name": "output_voltage",
                                "value_raw": "30",
                                "unit_raw": "V",
                                "role": "output",
                                "current_type": "DC",
                                "mode": "",
                                "evidence_text": "PS1 30 V DC",
                            }
                        ],
                    }
                ]
            },
        }
    ]


def _search(source_id: str = "EQ-snapshot-1") -> dict:
    """Возвращает сохранённый документ и факт той же модели."""
    return {
        "status": "completed",
        "results": [
            {
                "identity": {
                    "manufacturer": "MEAN WELL",
                    "model": "DRC-100B",
                    "variant": "",
                },
                "status": "found",
                "document": {
                    "source_id": source_id,
                    "revision": "B",
                    "sha256": "a" * 64,
                    "final_url": "https://www.meanwell.com/drc-100b.pdf",
                    "trust_status": "trusted",
                },
                "facts": [
                    {
                        "document_source_id": source_id,
                        "property_name": "output_voltage",
                        "value_raw": "21...29",
                        "unit_raw": "V",
                        "role": "output",
                        "current_type": "DC",
                        "mode": "",
                        "variant": "",
                        "page": 3,
                        "snippet": "DRC-100B Output voltage 21...29 V DC",
                        "confidence": 0.9,
                        "applicability": "exact_model_line",
                    }
                ],
            }
        ],
    }


def test_eq_candidate_keeps_snapshot_and_passes_finalization_schema() -> None:
    """Кандидат сохраняет точный snapshot, значения и исходное место проекта."""
    result = compare_equipment_stage(_pages(), _search())
    candidate = result["candidates_by_page"]["2"][0]
    draft = FindingDraftPayload.model_validate(candidate).to_domain()

    assert result["status"] == "completed"
    assert draft.status == "confirmed"
    assert draft.finding_id.startswith("EQ-")
    assert draft.equipment_documentation_source_ids == ("EQ-snapshot-1",)
    assert draft.equipment_documentation_basis_sources[0].page == 3
    assert draft.equipment_details["project_value"] == "30"
    assert draft.equipment_details["manufacturer_value"] == "21...29"


def test_eq_does_not_use_fact_from_another_snapshot() -> None:
    """Факт другого документа не становится доказательством замечания."""
    search = _search()
    search["results"][0]["facts"][0]["document_source_id"] = "other-document"

    result = compare_equipment_stage(_pages(), search)

    assert result["candidates_by_page"] == {}
    assert result["status"] == "incomplete"


def test_table_scope_requires_engineer_review() -> None:
    """Соседняя строка таблицы не создаёт подтверждённое нарушение."""
    search = _search()
    fact = search["results"][0]["facts"][0]
    fact["confidence"] = 0.8
    fact["applicability"] = "single_model_block"

    result = compare_equipment_stage(_pages(), search)
    candidate = result["candidates_by_page"]["2"][0]

    assert candidate["status"] == "needs_review"
    assert candidate["equipment_details"]["comparison_status"] == "needs_review"


def test_conflicting_manufacturer_values_require_review() -> None:
    """Противоречивые значения одной ревизии не выбираются произвольно."""
    search = _search()
    other = {
        **search["results"][0]["facts"][0],
        "value_raw": "30...35",
        "snippet": "DRC-100B Output voltage 30...35 V DC",
    }
    search["results"][0]["facts"].append(other)

    result = compare_equipment_stage(_pages(), search)

    assert {candidate["status"] for candidate in result["candidates_by_page"]["2"]} == {
        "needs_review"
    }


def test_multiple_saved_revisions_require_review() -> None:
    """Несколько разных snapshot не дают автоматически выбрать одну ревизию."""
    search = _search()
    search["results"][0]["document"]["revision_ambiguous"] = True

    result = compare_equipment_stage(_pages(), search)
    candidate = result["candidates_by_page"]["2"][0]

    assert candidate["status"] == "needs_review"
    assert result["status"] == "incomplete"
    assert any("несколько ревизий" in warning for warning in result["warnings"])


def test_phase_and_condition_guard_confirmed_finding() -> None:
    """Чужая фазность не сравнивается, особое условие требует проверки."""
    search = _search()
    fact = search["results"][0]["facts"][0]
    fact["phase"] = "three"
    assert compare_equipment_stage(_pages(), search)["candidates_by_page"] == {}

    pages = _pages()
    pages[0]["facts"]["equipment_identities"][0]["parameters"][0]["phase"] = "3-phase"
    fact["condition"] = "at full load"
    result = compare_equipment_stage(pages, search)
    assert result["candidates_by_page"]["2"][0]["status"] == "needs_review"


def test_partial_eq_reason_reaches_common_report_diagnostics() -> None:
    """Причина исчерпания бюджета модели не теряется в общем результате."""
    search = _search()
    search["results"][0]["warning"] = "Исчерпан общий бюджет загрузки документов."
    result = compare_equipment_stage(_pages(), search)
    assert result["status"] == "incomplete"
    assert "Исчерпан общий бюджет загрузки документов." in result["warnings"]
