"""Регрессии полного пути EQ: позиции, статические ссылки и разделение доказательств."""

import pytest
from pdrd_analysis_service.domain.equipment_specification import table_equipment_records
from pdrd_equipment_search_service.domain.discovery import (
    discover_links,
    search_queries,
)
from pdrd_equipment_search_service.domain.equipment import EquipmentIdentity


@pytest.mark.parametrize(
    ("name", "model", "expected"),
    [
        ("Счётчик воды", "ВСКМ 90-20", "serial_equipment"),
        ("Шкаф пожарный", "ШПК-320Н", "serial_equipment"),
        (
            'Труба ПЭ100 SDR11.0 - 110*10 "техническая"',
            "ГОСТ Р 70628.1-2023",
            "standard_product",
        ),
        ("Люк чугунный Т (С250)-В.2-70", "ГОСТ 3634-99", "standard_product"),
        ("Песок", "ГОСТ 8736-85", "material"),
        (
            "Колодцы водопроводные из ж/б колец Ø1,5м",
            "ГОСТ 8020-2016",
            "prefabricated_structure",
        ),
        ("Упор из бетона В10 W=0,35м3", "", "designed_structure"),
        ("Резервуар противопожарный V=100м3", "", "designed_structure"),
    ],
)
def test_every_specification_position_has_a_search_strategy(name, model, expected):
    """Проектная конструкция и материал не исчезают из поиска из-за отсутствия бренда."""
    item = {
        "name": name,
        "Caption": name,
        "model": model,
        "manufacturer": "",
        "item_id": "p26-i1",
        "position": "12",
        "quantity": "2",
        "units": "шт.",
        "source_regions": [],
        "source_pages": [26],
        "row_ids": ["p26-r1"],
        "project_code": "",
        "under": "",
    }
    record = table_equipment_records({"items": [item]}, 26)[0]
    assert record["item_type"] == expected
    identity = EquipmentIdentity(
        "",
        record["model"],
        manufacturer_status="unknown",
        model_status=record["model_status"],
        confidence=record["confidence"],
        item_type=record["item_type"],
        name=record["search_name"],
        reference=record["search_reference"],
    )
    assert identity.searchable
    queries = search_queries(identity)
    assert len(set(queries)) >= 3
    assert (
        all("datasheet" not in query for query in queries)
        if expected != "serial_equipment"
        else True
    )
    assert "ГОСТ" in " ".join(queries) if model.startswith("ГОСТ") else True


def test_static_discovery_keeps_relative_download_routes_and_rejects_cycles():
    """Ссылка без расширения остаётся кандидатом, скрипт и javascript не исполняются."""
    content = b"""<title>Meter</title><a href="/download?id=7">Passport PDF</a>
    <a href="/meter">Documentation</a><a href="javascript:alert(1)">Manual</a>
    <script><a href="https://evil.example/manual.pdf">PDF</a></script>"""
    links = discover_links(content, "https://maker.example/meter")
    assert [link.url for link in links] == ["https://maker.example/download?id=7"]


def test_different_material_standards_are_not_deduplicated_into_one_search():
    """Одинаковое имя при разных стандартах и редакциях не означает одинаковую применимость."""
    from pdrd_analysis_service.domain.equipment import InventoryItem

    first = EquipmentIdentity(
        "", "Песок", item_type="material", reference="ГОСТ 8736-85"
    )
    second = EquipmentIdentity(
        "", "Песок", item_type="material", reference="ГОСТ 8736-2014"
    )
    assert first.key != second.key
    inventory = InventoryItem(
        "",
        "Песок",
        "",
        "needs_review",
        1.0,
        (),
        item_type="material",
        search_reference=first.reference,
    )
    assert inventory.key == first.key


def test_shared_model_does_not_confirm_an_independent_position_without_project_parameters():
    """Паспорт общий, но подтверждение и полнота сравнения принадлежат своей позиции."""
    from pdrd_analysis_service.application.equipment_report import position_report

    models = [
        {
            "status": "confirmed",
            "document": {"source_id": "EQ-model", "trust_status": "trusted"},
            "occurrences": [
                {
                    "source_row_ids": ["r1"],
                    "parameters": [{"property_name": "output_voltage"}],
                },
                {"source_row_ids": ["r2"], "parameters": []},
            ],
            "facts": [{"property_name": "Output voltage"}],
            "comparisons": [
                {
                    "page": 26,
                    "object_ref": "1",
                    "property_name": "output_voltage",
                    "status": "matched",
                }
            ],
        }
    ]
    specification = {
        "structured_items": [
            {"item_id": "a", "position": "1", "row_ids": ["r1"], "source_pages": [26]},
            {"item_id": "b", "position": "2", "row_ids": ["r2"], "source_pages": [26]},
        ]
    }
    rows = position_report(models, specification)
    assert rows[0]["status"] == "confirmed"
    assert rows[1]["status"] == "no_data"
    assert rows[1]["property_checks"][0]["status"] == "project_property_missing"
