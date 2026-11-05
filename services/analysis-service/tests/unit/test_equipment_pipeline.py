# services/analysis-service/tests/unit/test_equipment_pipeline.py

"""Проверяет сквозной контракт EQ-кандидата перед общим Finalization."""

from pdrd_analysis_service.application.equipment_pipeline import compare_equipment_stage
from pdrd_analysis_service.transport.http.schemas import FindingDraftPayload


def test_job_override_compares_values_without_granting_global_trust():
    """Разрешение пользователя включает расчёт, но сохраняет источник и вопрос инженеру."""
    search = _search()
    search["allow_unverified"] = True
    search["results"][0]["document"]["trust_status"] = "unverified_user_override"
    mismatch = compare_equipment_stage(_pages(), search)
    assert mismatch["comparisons"][0]["status"] == "mismatch_candidate"
    assert mismatch["candidates_by_page"]["2"][0]["status"] == "needs_review"
    assert (
        mismatch["models"][0]["document"]["trust_status"] == "unverified_user_override"
    )
    assert mismatch["models"][0]["reason_code"] == "source_unverified"
    search["results"][0]["facts"][0]["value_raw"] = "21...35"
    matched = compare_equipment_stage(_pages(), search)
    assert matched["comparisons"][0]["status"] == "matched"
    assert matched["candidates_by_page"] == {}
    search["allow_unverified"] = False
    assert compare_equipment_stage(_pages(), search)["comparisons"] == []


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
    assert result["candidates_by_page"] == {}
    assert result["diagnostics"][0]["reason_code"] == "fact_identity_not_proven"
    assert result["diagnostics"][0]["comparisons"]


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

    assert result["candidates_by_page"] == {}
    assert len(result["diagnostics"]) == 1
    assert result["diagnostics"][0]["reason_code"] == "conflicting_document_values"
    assert len(result["diagnostics"][0]["comparisons"]) == 2


def test_multiple_saved_revisions_require_review() -> None:
    """Несколько разных snapshot не дают автоматически выбрать одну ревизию."""
    search = _search()
    search["results"][0]["document"]["revision_ambiguous"] = True

    result = compare_equipment_stage(_pages(), search)
    assert result["candidates_by_page"] == {}
    assert result["diagnostics"][0]["reason_code"] == "fact_evidence_insufficient"
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
    assert result["candidates_by_page"] == {}
    assert result["diagnostics"][0]["reason_code"] == "conditions_not_proven"


def test_partial_eq_reason_reaches_common_report_diagnostics() -> None:
    """Причина исчерпания бюджета модели не теряется в общем результате."""
    search = _search()
    search["results"][0]["warning"] = "Исчерпан общий бюджет загрузки документов."
    result = compare_equipment_stage(_pages(), search)
    assert result["status"] == "incomplete"
    assert "Исчерпан общий бюджет загрузки документов." in result["warnings"]


def test_equipment_finding_query_keeps_property_values_and_project_context() -> None:
    """EQ расхождение запускает N/E поиск по конкретному инженерному факту."""
    pages = _pages()
    pages[0]["facts"]["equipment_identities"][0]["equipment_type"] = "блок питания"
    candidate = compare_equipment_stage(pages, _search())["candidates_by_page"]["2"][0]
    query = candidate["experience_query"]
    assert "выходное напряжение" in query and "output_voltage" in query
    assert "MEAN WELL DRC-100B" in query and "PS1" in query
    assert "30 V" in query and "21...29 V" in query
    assert "PS1 30 V DC" in query and "блок питания" in query and "схема" in query
    assert candidate["normative_source_ids"] == []
    assert (
        FindingDraftPayload.model_validate(candidate).to_domain().experience_query
        == query
    )


def test_review_equipment_query_does_not_assert_confirmed_mismatch() -> None:
    """Неоднозначная характеристика остаётся вопросом для нормативного поиска."""
    search = _search()
    search["results"][0]["facts"][0]["value_raw"] = "24"
    candidate = compare_equipment_stage(_pages(), search)["candidates_by_page"]["2"][0]
    assert candidate["status"] == "needs_review"
    assert (
        "Применимость характеристики требует проверки инженером"
        in candidate["experience_query"]
    )
    assert "Обнаружено расхождение" not in candidate["experience_query"]


def test_reference_values_remain_lossless_diagnostics_without_findings():
    """84 произвольных справочных значения не становятся 84 претензиями к изделию."""
    search = _search()
    template = search["results"][0]["facts"][0]
    search["results"][0]["facts"] = [
        dict(
            template,
            value_raw=str(value),
            page=value,
            applicability="technical_reference",
            confidence=0.6,
        )
        for value in range(1, 85)
    ]
    result = compare_equipment_stage(_pages(), search)
    assert result["candidates_by_page"] == {}
    assert len(result["comparisons"]) == 84
    assert len(result["diagnostics"]) == 1
    assert len(result["diagnostics"][0]["comparisons"]) == 84
    assert result["models"][0]["facts"] == search["results"][0]["facts"]


def test_repeated_passport_fact_has_one_finding_and_all_pages():
    """Повтор паспорта не размножает вопрос; разные физические объекты сохраняются."""
    from copy import deepcopy

    search = _search()
    search["results"][0]["facts"].append(dict(search["results"][0]["facts"][0], page=4))
    pages = _pages()
    other = deepcopy(pages[0])
    other["page_number"] = 3
    pages.append(other)
    result = compare_equipment_stage(pages, search)
    rows = [row for values in result["candidates_by_page"].values() for row in values]
    assert len(rows) == 1
    assert {
        source["page"] for source in rows[0]["equipment_documentation_basis_sources"]
    } == {3, 4}
    assert {
        origin["page"]
        for origin in rows[0]["origin_assertions"]
        if origin["origin"] == "merged_equipment_comparison"
    } == {2, 3}
    pages[1]["facts"]["equipment_identities"][0]["object_ref"] = "PS2"
    assert (
        sum(
            map(
                len,
                compare_equipment_stage(pages, search)["candidates_by_page"].values(),
            )
        )
        == 2
    )


def test_pipe_and_reservoir_reference_values_do_not_become_product_passports():
    """110 мм против 1 м, стенка 10 против 2 мм и объёмы разных баков остаются диагностикой."""
    for prop, project, unit, reference_value, reference_unit in (
        ("outer_diameter", "110", "мм", "1", "м"),
        ("wall_thickness", "10", "мм", "2", "мм"),
        ("volume", "100", "м³", "6", "м³"),
        ("volume", "50", "м³", "1", "м³"),
    ):
        pages, search = _pages(), _search()
        parameter = pages[0]["facts"]["equipment_identities"][0]["parameters"][0]
        parameter.update(
            property_name=prop,
            value_raw=project,
            unit_raw=unit,
            role="",
            current_type="",
        )
        fact = search["results"][0]["facts"][0]
        fact.update(
            property_name=prop,
            value_raw=reference_value,
            unit_raw=reference_unit,
            role="",
            current_type="",
            applicability="technical_reference",
            confidence=0.6,
        )
        result = compare_equipment_stage(pages, search)
        assert result["candidates_by_page"] == {}
        assert len(result["comparisons"]) == 1
        assert result["diagnostics"][0]["reason_code"] == "fact_identity_not_proven"


async def test_resume_does_not_return_legacy_reference_findings_to_report():
    """Повторная финализация старого результата сохраняет справочное сравнение в диагностике."""
    from pdrd_analysis_service.application.use_cases.finalization import (
        FinalizeFindings,
    )

    candidate = compare_equipment_stage(_pages(), _search())["candidates_by_page"]["2"][
        0
    ]
    candidate["origin_assertions"][0]["facts"][0]["applicability"] = (
        "technical_reference"
    )
    finding = FindingDraftPayload.model_validate(candidate).to_domain()

    class NoVision:
        """Запрещает заменять проверку применимости новой модельной интерпретацией."""

        async def generate_json(self, **kwargs):
            raise AssertionError(
                "Справочный факт не должен попадать в модель Finalization"
            )

    use_case = FinalizeFindings(
        vision_model=NoVision(),
        num_predict=1000,
        batch_size=4,
        experience_context_limit=500,
        experience_min_score=0.5,
    )
    _, findings, metrics = await use_case.execute(
        findings=(finding,), experience_by_finding={}
    )
    assert findings == ()
    assert metrics["rejected_count"] == 1
    assert metrics["rejected_findings"][0]["candidate"]["origin_assertions"]


def test_neighbor_model_or_article_fact_does_not_borrow_document_identity():
    """Доверие к паспорту не исправляет чужую строку модели, производителя или артикула."""
    for field, value in (
        ("model", "DRC-100A"),
        ("manufacturer", "Другой завод"),
        ("article", "OTHER-1"),
    ):
        search = _search()
        search["results"][0]["facts"][0][field] = value
        result = compare_equipment_stage(_pages(), search)
        assert result["candidates_by_page"] == {}
        assert (
            result["diagnostics"][0]["reason_code"] == "fact_product_identity_mismatch"
        )
