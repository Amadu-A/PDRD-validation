# services/analysis-service/tests/unit/test_equipment_identification_regression.py

"""Нельзя подменять производителя, соседнюю модель и параметры из маркировки."""

from pdrd_analysis_service.domain.equipment import build_inventory
from pdrd_analysis_service.domain.equipment_identification import (
    ground_equipment_records,
)


def test_model_number_is_not_a_project_voltage():
    """Число 12 в РИП-12 не является источником проектного напряжения 12 В."""
    raw = {
        "manufacturer": "Рубеж",
        "model": "РИП-12",
        "variant": "исп. 51",
        "confidence": 0.95,
        "status": "resolved",
        "evidence_text": "Источник питания РИП-12 исп. 51",
        "parameters": [
            {
                "property_name": "output_voltage",
                "value_raw": "12",
                "unit_raw": "В",
                "evidence_text": "РИП-12",
            }
        ],
    }
    records = ground_equipment_records([raw], "Источник питания РИП-12 исп. 51")
    assert records[0]["manufacturer"] == ""
    assert records[0]["parameters"][0]["evidence_status"] == "needs_review"
    assert records[0]["parameters"][0]["value_raw"] == "12"


def test_explicit_quantity_is_kept_with_unit_alias():
    """Подписанное значение сохраняется при эквивалентных V и В."""
    raw = {
        "model": "DRC-100B",
        "status": "resolved",
        "confidence": 0.95,
        "evidence_text": "PS1 DRC-100B 30 V",
        "parameters": [
            {
                "property_name": "output_voltage",
                "value_raw": "30",
                "unit_raw": "V",
                "evidence_text": "PS1 30 В",
            }
        ],
    }
    records = ground_equipment_records([raw], "PS1 DRC-100B 30 В")
    assert records[0]["parameters"][0].get("evidence_status") != "needs_review"


def test_neighbor_model_and_foreign_word_do_not_prove_identity():
    """Сигнал-100 и слово «зарубежный» не подтверждают Сигнал-10 производителя Рубеж."""
    records = ground_equipment_records(
        [
            {
                "manufacturer": "Рубеж",
                "model": "Сигнал-10",
                "status": "resolved",
                "confidence": 0.99,
                "evidence_text": "зарубежный Сигнал-100",
                "manufacturer_evidence_text": "зарубежный Сигнал-100",
            }
        ],
        "зарубежный Сигнал-100",
    )
    assert records[0]["manufacturer"] == ""
    assert records[0]["model_status"] == "needs_review"


def test_spacing_duplicates_merge_but_articles_remain_distinct():
    """Типографика не создаёт дубль; разные артикулы не сливаются."""
    rows = [
        {
            "model": "ИО 102-29",
            "variant": "исп. 51",
            "article": "SKU1",
            "status": "needs_review",
            "confidence": 0.9,
        },
        {
            "model": "ИО102—29",
            "variant": "исполнение 51",
            "article": "SKU1",
            "status": "needs_review",
            "confidence": 0.9,
        },
        {
            "model": "ИО102-29",
            "variant": "исп. 51",
            "article": "SKU2",
            "status": "needs_review",
            "confidence": 0.9,
        },
    ]
    inventory = build_inventory(
        [{"page_number": 1, "facts": {"equipment_identities": rows}}]
    )
    assert len(inventory) == 2
    assert len(inventory[0].occurrences) == 2
    assert {item.article for item in inventory} == {"SKU1", "SKU2"}
