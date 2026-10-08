# services/knowledge-service/tests/unit/test_equipment_facts.py

"""Структурные EQ Facts извлекаются только из строки точной модели."""

from pdrd_knowledge_service.application.equipment_facts import (
    extract_equipment_facts,
)


def test_exact_model_row_produces_fact_with_page_and_scope() -> None:
    """Строка таблицы содержит источник, диапазон, роль и AC/DC."""
    facts = extract_equipment_facts(
        source_id="EQ-1",
        model="DRC-100B",
        variant="",
        pages=[
            {
                "page": 2,
                "text": "DRC-100B | Output voltage | 21...29 V DC\n"
                "DRC-100A | Output voltage | 10...14 V DC",
            }
        ],
    )
    assert len(facts) == 1
    fact = facts[0]
    assert fact["document_source_id"] == "EQ-1"
    assert fact["page"] == 2
    assert fact["property_type"] == "output_voltage"
    assert fact["property_name"] == "Output voltage"
    assert fact["value_type"] == "range"
    assert fact["normalized_value"]["min"] == "21"
    assert fact["normalized_value"]["max"] == "29"
    assert fact["current_type"] == "DC"
    assert fact["applicability"] == "exact_model_line"


def test_ambiguous_multimodel_text_has_no_fact() -> None:
    """Модель в заголовке и общая строка не образуют ложное доказательство."""
    facts = extract_equipment_facts(
        source_id="EQ-2",
        model="DRC-100B",
        variant="",
        pages=[{"page": 1, "text": "DRC-100B\nOutput voltage 24 V DC"}],
    )
    assert facts == []


def test_variant_must_be_explicit_on_fact_row() -> None:
    """Чужое или отсутствующее исполнение не используется."""
    facts = extract_equipment_facts(
        source_id="EQ-3",
        model="NXB-63",
        variant="C16",
        pages=[{"page": 1, "text": "NXB-63 D16 rated current 16 A"}],
    )
    assert facts == []


def test_single_model_heading_applies_to_following_property_row() -> None:
    """Одномодельная таблица даёт факт со строкой и заголовком модели."""
    facts = extract_equipment_facts(
        source_id="EQ-table",
        model="DRC-100B",
        variant="",
        pages=[
            {
                "page": 4,
                "text": "Parameter | DRC-100B\nOutput voltage | 21...29 V DC",
            }
        ],
    )

    assert len(facts) == 1
    assert facts[0]["applicability"] == "single_model_block"
    assert facts[0]["page"] == 4
    assert facts[0]["table_title"] == "Parameter | DRC-100B"
    assert facts[0]["column_label"] == "DRC-100B"


def test_multi_model_table_uses_only_exact_model_column() -> None:
    """Явная колонка модели не смешивается с соседним исполнением."""
    facts = extract_equipment_facts(
        source_id="EQ-multi",
        model="DRC-100B",
        variant="",
        pages=[
            {
                "page": 4,
                "text": (
                    "Parameter | DRC-100A | DRC-100B\nOutput voltage | 12 V | 24 V"
                ),
            }
        ],
    )

    assert len(facts) == 1
    assert facts[0]["value_raw"] == "24"
    assert facts[0]["column_label"] == "DRC-100B"
    assert facts[0]["applicability"] == "exact_model_column"


def test_multi_model_table_rejects_misaligned_row() -> None:
    """Строка с отсутствующей колонкой не получает чужое значение."""
    facts = extract_equipment_facts(
        source_id="EQ-misaligned",
        model="DRC-100B",
        variant="",
        pages=[
            {
                "page": 4,
                "text": "Parameter | DRC-100A | DRC-100B\nOutput voltage | 12 V",
            }
        ],
    )
    assert facts == []


def test_later_table_header_stops_previous_model_scope() -> None:
    """Соседняя таблица другой модели не наследует первую колонку."""
    facts = extract_equipment_facts(
        source_id="EQ-neighbor",
        model="DRC-100B",
        variant="",
        pages=[
            {
                "page": 4,
                "text": (
                    "Parameter | DRC-100B\nOutput voltage | 21...29 V\n"
                    "Parameter | DRC-100A\nOutput voltage | 12 V"
                ),
            }
        ],
    )

    assert [fact["value_raw"] for fact in facts] == ["21...29"]


def test_phase_and_operating_condition_are_preserved() -> None:
    """Фазность и условная характеристика не теряются при извлечении."""
    facts = extract_equipment_facts(
        source_id="EQ-conditional",
        model="NXB-63",
        variant="",
        pages=[
            {
                "page": 1,
                "text": "NXB-63 3-phase Input voltage 380...415 V AC at full load",
            }
        ],
    )
    assert len(facts) == 1
    assert facts[0]["phase"] == "three"
    assert facts[0]["condition"]
    assert facts[0]["confidence"] < 0.85


def test_discrete_eq_values_preserve_type_and_original_names() -> None:
    """Перечисление и boolean привязаны к точной модели и исходной строке."""
    for property_name, value, value_type, normalized in (
        (
            "Tripping characteristic",
            "B,C,D",
            "enumeration",
            {"values": ["B", "C", "D"]},
        ),
        ("Protection degree", "IP65", "enumeration", {"values": ["IP65"]}),
        ("Requires earthing", "yes", "boolean", {"value": True}),
    ):
        facts = extract_equipment_facts(
            source_id="EQ-discrete",
            model="NXB-63",
            variant="",
            pages=[{"page": 1, "text": f"NXB-63 | {property_name} | {value}"}],
        )
        assert len(facts) == 1
        assert facts[0]["value_type"] == value_type
        assert facts[0]["normalized_value"] == normalized
        assert facts[0]["property_name"] == property_name


def test_suffix_model_is_not_an_exact_documentation_fact() -> None:
    """Дополнительный суффикс модели не теряется на границе маркировки."""
    assert (
        extract_equipment_facts(
            source_id="EQ-suffix",
            model="DRC-100B",
            variant="",
            pages=[{"page": 1, "text": "DRC-100B-A output voltage 12 V DC"}],
        )
        == []
    )


def test_text_documentation_value_is_preserved_for_review() -> None:
    """Текст не превращается в категорию нарушения или числовую величину."""
    facts = extract_equipment_facts(
        source_id="EQ-text",
        model="RKE2C0730LT",
        variant="",
        pages=[
            {
                "page": 1,
                "text": "RKE2C0730LT | Contact configuration | 2 changeover contacts",
            }
        ],
    )
    assert len(facts) == 1
    assert facts[0]["value_type"] == "text"
    assert facts[0]["normalized_value"] == {"value": "2 changeover contacts"}


def test_shared_row_of_neighbor_models_keeps_ambiguity() -> None:
    """Общая строка двух моделей требует проверки применимости."""
    facts = extract_equipment_facts(
        source_id="EQ-shared-row",
        model="DRC-100B",
        variant="",
        pages=[{"page": 1, "text": "DRC-100A / DRC-100B Output voltage 10...14 V DC"}],
    )
    assert len(facts) == 1
    assert facts[0]["confidence"] < 0.85
    assert facts[0]["applicability"] == "ambiguous_model_line"


def test_model_numeric_suffix_is_not_interpreted_as_property_unit() -> None:
    """Буква A в обозначении модели не становится величиной тока."""
    facts = extract_equipment_facts(
        source_id="EQ-model-A",
        model="DRC-100A",
        variant="",
        pages=[{"page": 1, "text": "DRC-100A Output voltage 10...14 V DC"}],
    )
    assert len(facts) == 1 and facts[0]["value_raw"] == "10...14"


def test_qualified_numeric_range_keeps_condition() -> None:
    """Отрицание диапазона не теряется при разборе физической величины."""
    facts = extract_equipment_facts(
        source_id="EQ-negated",
        model="DRC-100B",
        variant="",
        pages=[{"page": 1, "text": "DRC-100B Output voltage not 21...29 V DC"}],
    )
    assert facts[0]["condition"] and facts[0]["confidence"] < 0.85
