# services/knowledge-service/tests/unit/test_equipment_extraction_recovery.py

"""Реальная текстовая структура паспорта и восстановление после пустого extraction."""

import pytest
from pdrd_knowledge_service.application.equipment_fact_index import EquipmentFactIndex
from pdrd_knowledge_service.application.equipment_facts import extract_equipment_facts

from .test_equipment_fact_index import MemoryCache


@pytest.mark.parametrize(
    "heading", ["DRC-100B", "MEAN WELL DRC-100B", "Модель: DRC-100B"]
)
def test_multiline_characteristic_keeps_its_model_scope(heading):
    """Маркировка в заголовке и значение на следующей строке дают проверяемый факт."""
    facts = extract_equipment_facts(
        source_id="EQ-model",
        model="DRC-100B",
        variant="",
        pages=[
            {
                "page": 3,
                "text": f"{heading}\nOutput voltage\n21...29 V DC\nМодель: DRC-100A\nOutput voltage 10 V DC",
            }
        ],
    )
    assert len(facts) == 1
    assert facts[0]["value_raw"] == "21...29" and facts[0]["page"] == 3
    assert heading in facts[0]["table_title"]
    assert (
        "Output voltage" in facts[0]["snippet"]
        and "21...29 V DC" in facts[0]["snippet"]
    )


async def test_empty_result_does_not_prevent_later_visual_evidence():
    """Нулевой набор не блокирует допустимое извлечение той же версии по тому же SHA."""
    cache = MemoryCache()
    index = EquipmentFactIndex(cache)
    args = {
        "source_id": "EQ-model",
        "sha256": "a" * 64,
        "manufacturer": "MEAN WELL",
        "model": "DRC-100B",
        "variant": "",
        "pages": [],
    }
    empty = await index.extract_or_get(**args)
    assert not empty["facts"]
    visual = {
        "page": 2,
        "property_name": "Output voltage",
        "value_raw": "21...29",
        "unit_raw": "V",
        "snippet": "DRC-100B Output voltage 21...29 V",
    }
    result = await index.extract_or_get(**args, vision_facts=(visual,))
    assert result["facts"] and result["facts"][0]["page"] == 2
    assert cache.writes == 1


def test_multilevel_model_columns_keep_channel_and_rating_role():
    """Модель, канал и вид величины не заимствуются у соседней колонки."""
    facts = extract_equipment_facts(
        source_id="EQ-matrix",
        model="PS-24B",
        variant="",
        pages=[
            {
                "page": 2,
                "text": (
                    "MODEL | | PS-12A | | PS-24B |\n"
                    " | OUTPUT NUMBER | CH1 | CH2 | CH1 | CH2\n"
                    " | DC VOLTAGE | 12V | 12V | 24V | 24V\n"
                    " | RATED CURRENT | 4A | 2A | 2A | 1A\n"
                    " | CURRENT RANGE | 0 ~ 4A | | 0 ~ 3A |\n"
                    " | VOLTAGE ADJ. RANGE | 10 ~ 15V | | 20 ~ 30V |"
                ),
            }
        ],
    )
    assert len(facts) == 4
    assert {f["configuration"] for f in facts} == {"CH1"}
    assert {f["mode"] for f in facts} == {"nominal", "rated", "range", "adjustment"}
    assert [f["value_raw"] for f in facts] == ["24", "2", "0 ~ 3", "20 ~ 30"]
    assert all(f["table_title"].startswith("MODEL") for f in facts)


def test_product_block_extracts_russian_ranges_and_discrete_property():
    """Описание перед характеристиками и одинаковые единицы у пределов не теряют данные."""
    text = (
        "Датчик-Ш\n"
        + "Описание изделия\n" * 20
        + "Оптико-электронный\nНапряжение питания\nот 8 до 30 В\n"
        + "Потребляемый ток\n12 мА\nТемпературный диапазон\nот -30 °С до +50 °С\n"
        + "Степень защиты, обеспечиваемая оболочкой\nIP41\n"
        + "Модель: Датчик-Другой\nНапряжение питания 500 В"
    )
    facts = extract_equipment_facts(
        source_id="EQ-html",
        model="Датчик-Ш",
        variant="",
        pages=[{"page": 1, "text": text}],
    )
    assert {f["property_type"] for f in facts} == {
        "input_voltage",
        "input_current",
        "ambient_temperature",
        "protection_degree",
    }
    assert all("500" not in f["value_raw"] for f in facts)
    assert all(f["confidence"] < 0.85 for f in facts)


def test_multiline_text_mode_is_not_mistaken_for_its_label():
    """Имя текстового свойства не подменяет его значение на следующей строке."""
    facts = extract_equipment_facts(
        source_id="EQ-mode",
        model="PS-24",
        variant="",
        pages=[{"page": 1, "text": "Модель: PS-24\nРежим работы\nнепрерывный"}],
    )
    assert len(facts) == 1 and facts[0]["value_raw"] == "непрерывный"
    assert facts[0]["applicability"] == "single_model_block"
