# services/analysis-service/tests/unit/test_equipment_specification.py

"""Табличные EQ-данные не запрашиваются повторно у VLM и сохраняют все позиции."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pdrd_analysis_service.application.ports.vision_model import VisionModelError
from pdrd_analysis_service.application.specification_refinement import (
    RefineSpecification,
)
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult
from pdrd_analysis_service.domain.equipment import build_inventory
from pdrd_analysis_service.domain.equipment_specification import (
    SpecificationCancelled,
    SpecificationContractError,
    specification_report,
    validated_specification,
)


def specification(count=2, *, ambiguous=False):
    """Готовит полноценный контракт с отдельными строками, количествами и областями."""
    items, rows = [], []
    for index in range(count):
        identifier = f"spec-p0025-t01-r{index:04d}"
        bbox = {"x_min": 10, "y_min": index + 1, "x_max": 900, "y_max": index + 2}
        cells = [{"text": f"Прибор {index}", "bbox": bbox}]
        rows.append(
            {
                "page": 25,
                "row_id": identifier,
                "kind": "item",
                "bbox": bbox,
                "raw_cells": cells,
            }
        )
        items.append(
            {
                "item_id": identifier,
                "row_ids": [identifier],
                "raw_values": [cells],
                "source_pages": [25],
                "source_regions": [{"page": 25, "row_id": identifier, **bbox}],
                "position": str(index + 1),
                "name": f"Прибор {index}",
                "model": "MODEL-10",
                "manufacturer": "Завод",
                "variant": "",
                "article": "",
                "units": "шт.",
                "quantity": str(index + 1),
                "project_code": "IOS-1",
                "under": "",
                "Caption": f"Прибор {index}; MODEL-10; Завод",
                "Units": "шт.",
                "Quantity": str(index + 1),
                "UserComment1": "IOS-1",
                "Under": "",
                "parameters": [],
                "needs_review": ambiguous,
            }
        )
    return {
        "version": 1,
        "page": 25,
        "status": "reliable",
        "items": items,
        "raw_rows": rows,
        "raw_row_count": count,
        "coverage_complete": True,
        "warnings": [],
        "project_code": "IOS-1",
    }


def metrics():
    """Возвращает наблюдаемый успешный вызов, не симулируя расход реальной GPU."""
    return GenerationMetrics(1, "stop", 3200, 1.0, 0.0, 100, 80, 300, 0)


def model():
    """Отвечает только на факты страницы или на ограниченные переданные строки."""

    async def generate(**request):
        """Не извлекает оборудование заново в надёжной табличной ветке."""
        if request["stage"].startswith("specification_rows:"):
            inputs = json.loads(request["prompt"].split("Исходные строки: ", 1)[1])
            fields = request["schema"]["properties"]["items"]["items"]["properties"]
            payload = {
                "items": [{key: item.get(key, "") for key in fields} for item in inputs]
            }
        else:
            payload = {
                "discipline": "ОВ",
                "page_type": "specification",
                "summary": "Описание листа",
                "objects": ["Приборы"],
                "connections": [],
                "labels": [],
                "normative_queries": ["Требования к установке"],
                "document_facts": [],
            }
        return GenerationResult(payload, metrics())

    return SimpleNamespace(generate_json=AsyncMock(side_effect=generate))


@pytest.mark.parametrize("context", [False, True])
async def test_more_than_forty_table_items_bypass_vlm_equipment_array_and_keep_occurrences(
    context,
):
    """Все 65 позиций сохраняются; поиск группируется по одной модели без потери количества."""
    provider, source = model(), specification(65)
    facts, _ = await UnderstandPage(provider, 3200).execute(
        page_number=25,
        heuristic_page_type="specification",
        extracted_text="Спецификация",
        image_bytes=b"image",
        use_document_context=context,
        use_equipment_web_search=True,
        equipment_specification=source,
    )
    provider.generate_json.assert_awaited_once()
    request = provider.generate_json.await_args.kwargs
    assert "equipment_identities" not in request["schema"]["properties"]
    assert "Для каждого объекта верни equipment_identities" not in request["prompt"]
    assert ("document_facts" in request["schema"]["properties"]) is context
    assert len(facts.equipment_identities) == 65
    assert facts.summary == "Описание листа" and facts.normative_queries
    pages = [
        {
            "page_number": 25,
            "facts": {
                "equipment_identities": list(facts.equipment_identities),
                "equipment_specification": facts.equipment_specification,
            },
        }
    ]
    inventory = build_inventory(pages)
    assert len(inventory) == 1 and len(inventory[0].occurrences) == 65
    assert [row.quantity for row in inventory[0].occurrences] == [
        str(i) for i in range(1, 66)
    ]
    report = specification_report(pages)
    assert len(report["items"]) == len(report["structured_items"]) == 65
    assert report["raw_row_count"] == 65


async def test_unavailable_table_uses_original_vision_route_and_disabled_eq_ignores_specification():
    """Скан или ненадёжная таблица использует существующий визуальный маршрут."""
    provider = model()
    source = specification()
    source.update(status="unavailable", coverage_complete=False)
    use_case = UnderstandPage(provider, 3200)
    await use_case.execute(
        page_number=25,
        heuristic_page_type="table",
        extracted_text="",
        image_bytes=b"scan",
        use_equipment_web_search=True,
        equipment_specification=source,
    )
    assert (
        provider.generate_json.await_args.kwargs["schema"]["properties"][
            "equipment_identities"
        ]["maxItems"]
        == 40
    )
    facts, _ = await use_case.execute(
        page_number=25,
        heuristic_page_type="table",
        extracted_text="",
        image_bytes=b"scan",
        use_equipment_web_search=False,
        equipment_specification={"invalid": True},
    )
    assert facts.equipment_specification is None and facts.equipment_identities == ()


@pytest.mark.parametrize("mutation", ["count", "row_id", "geometry", "missing_item"])
def test_broken_source_contract_is_rejected(mutation):
    """Контроль полноты проверяется независимо от присланного coverage_complete."""
    source = specification()
    if mutation == "count":
        source["raw_row_count"] += 1
    if mutation == "row_id":
        source["raw_rows"][1]["row_id"] = source["raw_rows"][0]["row_id"]
    if mutation == "geometry":
        source["items"][0]["source_regions"][0]["x_max"] = -1
    if mutation == "missing_item":
        source["items"].pop()
    with pytest.raises(SpecificationContractError):
        validated_specification(source, 25)


async def test_ambiguous_rows_use_bounded_text_batches_and_never_send_images():
    """35 неоднозначных строк разбиваются; исходные поля и состав остаются неизменными."""
    source, provider = specification(35, ambiguous=True), model()
    before = deepcopy(source["raw_rows"])
    await RefineSpecification(provider, batch_characters=6000, max_calls=10).execute(
        source
    )
    assert provider.generate_json.await_count > 1
    for request in provider.generate_json.await_args_list:
        assert request.kwargs["image_bytes"] is None
        inputs = json.loads(request.kwargs["prompt"].split("Исходные строки: ", 1)[1])
        assert len(inputs) <= 20
        assert len(json.dumps(inputs, ensure_ascii=False)) <= 6050
    assert source["raw_rows"] == before
    assert len(source["items"]) == 35


async def test_provider_failure_and_invented_fields_keep_original_positions():
    """Недоступность короткого уточнения не прерывает основное понимание страницы."""
    provider, source = model(), specification(1, ambiguous=True)

    async def generate(**request):
        """Выдаёт реальный контракт ошибки уточнения и допустимые основные факты."""
        if request["stage"].startswith("specification_rows:"):
            raise VisionModelError("output budget")
        return GenerationResult(
            {
                "discipline": "ОВ",
                "page_type": "table",
                "summary": "Лист",
                "objects": [],
                "connections": [],
                "labels": [],
                "normative_queries": [],
            },
            metrics(),
        )

    provider.generate_json.side_effect = generate
    facts, _ = await UnderstandPage(provider, 3200).execute(
        page_number=25,
        heuristic_page_type="table",
        extracted_text="",
        image_bytes=b"page",
        use_equipment_web_search=True,
        equipment_specification=source,
    )
    assert len(facts.equipment_specification["items"]) == 1
    assert any(
        "исходные позиции сохранены" in text
        for text in facts.equipment_specification["warnings"]
    )
    assert provider.generate_json.await_count == 2


async def test_cancel_between_row_refinement_and_main_call_stops_new_gpu_work():
    """Уже завершённая порция сохранена; после отмены основной VLM-вызов не начинается."""
    source, provider = specification(1, ambiguous=True), model()
    should_stop = AsyncMock(side_effect=[False, True])
    with pytest.raises(SpecificationCancelled):
        await UnderstandPage(provider, 3200).execute(
            page_number=25,
            heuristic_page_type="table",
            extracted_text="",
            image_bytes=b"page",
            use_equipment_web_search=True,
            equipment_specification=source,
            should_stop=should_stop,
        )
    assert provider.generate_json.await_count == 1


@pytest.mark.parametrize("change", ["manufacturer", "model", "quantity", "name"])
async def test_refinement_rejects_invented_or_replaced_source_fields(change):
    """Модель не может заменить исходное значение или угадать отсутствующий завод."""
    source, vision = specification(1, ambiguous=True), model()
    if change == "manufacturer":
        source["items"][0]["manufacturer"] = ""
    before = deepcopy(source["items"])
    original = vision.generate_json.side_effect

    async def changed(**request):
        """Возвращает правильную схему, но неподтверждённое значение."""
        result = await original(**request)
        result.payload["items"][0][change] = "Придуманное значение"
        return result

    vision.generate_json.side_effect = changed
    await RefineSpecification(vision).execute(source)
    for field in (
        "manufacturer",
        "model",
        "quantity",
        "name",
        "raw_values",
        "row_ids",
        "source_regions",
    ):
        assert source["items"][0][field] == before[0][field]
    assert source["items"][0]["needs_review"]
    assert source["items"][0]["refinement_status"] == "rejected_unproven"


async def test_refinement_accepts_only_literal_label_in_own_raw_cell():
    """Пустая модель заполняется по явной подписи; исходные ячейки и количество сохранены."""
    source, vision = specification(1, ambiguous=True), model()
    item = source["items"][0]
    item["model"] = ""
    item["raw_values"][0][0]["text"] = "модель: MODEL-10"
    original = vision.generate_json.side_effect

    async def labelled(**request):
        """Уточняет только поле, подтверждённое той же исходной строкой."""
        result = await original(**request)
        result.payload["items"][0]["model"] = "MODEL-10"
        return result

    vision.generate_json.side_effect = labelled
    await RefineSpecification(vision).execute(source)
    assert (
        item["model"] == "MODEL-10" and item["refinement_status"] == "source_grounded"
    )
    assert item["raw_values"][0][0]["text"] == "модель: MODEL-10"
    assert (item["quantity"], item["Quantity"]) == ("1", "1")


async def test_refinement_budget_and_oversized_rows_preserve_every_position():
    """Размер и четыре вызова ограничивают GPU, не количество сохранённых позиций."""
    source, vision = specification(10, ambiguous=True), model()
    source["items"][0]["name"] = "Очень длинное наименование " * 1000
    await RefineSpecification(vision, batch_size=1).execute(source)
    assert vision.generate_json.await_count == 4
    assert len(source["items"]) == 10
    assert source["items"][0]["name"].endswith("наименование ")
    assert all(item["needs_review"] for item in source["items"])
    assert any("превышает размер" in message for message in source["warnings"])
    assert any("Лимит уточнения" in message for message in source["warnings"])


async def test_refinement_cannot_remove_or_duplicate_rows():
    """Даже JSON корректной формы с повторным row_id не меняет состав спецификации."""
    source, vision = specification(2, ambiguous=True), model()
    before = deepcopy(source["items"])
    original = vision.generate_json.side_effect

    async def duplicate(**request):
        """Имитирует потерю второй строки при формально допустимом количестве элементов."""
        result = await original(**request)
        result.payload["items"][1] = deepcopy(result.payload["items"][0])
        return result

    vision.generate_json.side_effect = duplicate
    await RefineSpecification(vision).execute(source)
    assert source["items"] == before
    assert any("изменило состав" in warning for warning in source["warnings"])


def test_global_report_checks_cross_page_row_ownership_independently():
    """Межстраничный row_id без владельца не признаётся полным по присланному флагу."""
    source = specification(1)
    source["raw_rows"][0]["kind"] = "cross_page_continuation"
    source["items"] = []
    validated_specification(source, 25)
    report = specification_report([{"facts": {"equipment_specification": source}}])
    assert not report["coverage_complete"]
