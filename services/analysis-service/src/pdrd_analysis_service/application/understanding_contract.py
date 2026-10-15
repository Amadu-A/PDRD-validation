# services/analysis-service/src/pdrd_analysis_service/application/understanding_contract.py

"""Компактный запрос Understanding без удаления полей, фактов или ёмкости массивов."""

from pdrd_analysis_service.application.json_schemas import build_page_facts_schema
from pdrd_analysis_service.application.prompts import build_page_understanding_prompt


def build_understanding_contract(
    *,
    page_number: int,
    heuristic_page_type: str,
    extracted_text: str,
    max_facts: int = 12,
    max_equipment: int = 0,
    max_graphical_targets: int = 0,
) -> tuple[str, dict]:
    """Разрешает опускать пустые атрибуты записей, сохраняя все известные данные и grounding."""
    schema = build_page_facts_schema(
        max_facts,
        max_equipment,
        max_graphical_targets,
    )
    fields = schema["properties"]
    if "document_facts" in fields:
        fields["document_facts"]["items"]["required"] = ["kind", "evidence_text"]
    if "equipment_identities" in fields:
        fields["equipment_identities"]["items"]["required"] = [
            "model",
            "evidence_text",
            "status",
            "confidence",
        ]
    record_fields = " и ".join(
        name for name in ("document_facts", "equipment_identities") if name in fields
    )
    sparse_instruction = (
        f"В {record_fields} опускай только неизвестные или пустые необязательные атрибуты "
        "вместо многократного вывода пустых строк и массивов. "
        if record_fields
        else ""
    )
    prompt = build_page_understanding_prompt(
        page_number=page_number,
        heuristic_page_type=heuristic_page_type,
        extracted_text=extracted_text,
        max_facts=max_facts,
        max_equipment=max_equipment,
        max_graphical_targets=max_graphical_targets,
    ) + (
        "\nКомпактная запись ответа: summary — одно-два коротких предложения; "
        "objects и labels — короткие уникальные обозначения, connections — отдельные связи. "
        "Не повторяй развёрнутые описания в нескольких полях. "
        f"{sparse_instruction}"
        "Все явно известные значения, идентификаторы, условия scope, таблицы, строки, "
        "единицы, исполнение, производитель с доказательством и читаемые области "
        "обязательно сохраняй в соответствующих полях схемы. "
        "Не объединяй разные объекты, свойства или режимы ради краткости; "
        "не заменяй дословные доказательства пересказом. "
        "Не добавляй неизвестные значения для заполнения схемы."
    )
    return prompt, schema
