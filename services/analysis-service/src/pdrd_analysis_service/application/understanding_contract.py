# services/analysis-service/src/pdrd_analysis_service/application/understanding_contract.py

"""Компактный запрос Understanding без удаления полей, фактов или ёмкости массивов."""

from pdrd_analysis_service.application.json_schemas import build_page_facts_schema
from pdrd_analysis_service.application.prompts import build_page_understanding_prompt
from pdrd_analysis_service.domain.visual_specification import visual_rows_schema


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
        fields["specification_rows"] = visual_rows_schema()
        schema["required"].append("specification_rows")
        fields["specification_notes"] = {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string", "maxLength": 700},
                    "bbox": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {"type": "number", "minimum": 0, "maximum": 1000},
                    },
                },
                "required": ["text", "bbox"],
            },
        }
        fields["document_facts"]["items"]["required"] = [
            "kind",
            "subject_type",
            "identifier",
            "property_type",
            "property_name",
            "value_raw",
            "unit_raw",
            "evidence_text",
            "visual_regions",
        ]
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
    if max_facts > 0:
        # Порядок полей влияет на порядок генерации: строки не должны оставаться
        # после развёрнутых интерпретаций той же таблицы.
        schema["properties"] = {
            "specification_rows": fields["specification_rows"],
            **fields,
        }
        prompt += (
            "\nЕсли видна графическая спецификация без прочитанной программно таблицы, "
            "сохрани читаемые строки в specification_rows по группам: system, дословный header, "
            "его bbox и rows. У строки — position, полное name с материалом/типоразмером, quantity, unit, bbox. "
            "Остальные непустые столбцы сохраняй в reference, manufacturer, model, article, note. "
            "system — только тег сети из заголовка. "
            "header — дословный заголовок группы с этим тегом, не шапка колонок. "
            "Все bbox — массивы [x_min,y_min,x_max,y_max] в 0..1000. Заголовок группы записывай один раз. "
            "Не переноси данные соседней строки или другой системы; неизвестную строку не достраивай. "
            "Строки specification_rows не дублируй в document_facts и equipment_identities: "
            "D и EQ этих строк будут сформированы программно. В equipment_identities оставь оборудование вне строк. "
            "Различай количество поставки, длину сети, DN, наружный диаметр, стенку и SDR. "
            "Позиция спецификации не доказывает связь с участком схемы. "
            "Непустые столбцы не пересказывай повторно в цитатах и summary. "
            "При непустом specification_rows массивы document_facts и equipment_identities строго пустые: "
            "все позиции будут получены из specification_rows. Заголовки, штамп, примечания и сведения "
            "вне таблицы сохраняй дословно в specification_notes (text,bbox), включая предупреждения и ссылки. "
            "Начни ответ со specification_rows. Без такой таблицы запиши specification_rows: []."
        )
    return (
        "Ответ запиши компактным JSON в одну строку без отступов и пустых необязательных полей.\n"
        + prompt,
        schema,
    )
