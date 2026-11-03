# services/analysis-service/src/pdrd_analysis_service/domain/specification_strategy.py

"""Выбирает вид документа по исходной позиции, не назначая ей вымышленный бренд."""

import re


def specification_strategy(item: dict) -> dict:
    """Сохраняет публичную маркировку отдельно от проектного контекста и количества."""
    name = str(item.get("name") or "").strip()
    model = str(item.get("model") or "").strip()
    reference = model if re.match(r"^(?:ГОСТ|СП\s|ТУ\s|серия\s)", model, re.I) else ""
    serial = bool(model and not reference and not item.get("needs_review"))
    if re.search(r"\b(?:песок|бетон|щебень|арматура)\b", name, re.I) and not re.search(
        r"упор|резервуар|колод", name, re.I
    ):
        kind = "material"
    elif re.search(r"колод|кольц|ж/б", name, re.I):
        kind = "prefabricated_structure"
    elif re.search(r"резервуар|упор", name, re.I) and not serial:
        kind = "designed_structure"
    elif re.search(
        r"труба|отвод|люк|фитинг|фланец|тройник|втулка|задвижка|гильз", name, re.I
    ):
        kind = "standard_product"
    else:
        kind = "serial_equipment" if serial else "unidentified"
    # Только узнаваемое наименование изделия и техническая маркировка; Under,
    # шифр проекта, комментарии и соседние тексты в Интернет не передаются.
    public = re.sub(r"[^\w\s.,°Øø×*/+()=\-]", " ", name)[:180].strip()
    return {
        "item_type": kind,
        "search_name": public,
        "search_reference": reference[:100],
        "search_model": model
        if serial
        else public or model or "Неидентифицированное изделие",
        "search_confirmed": serial or bool(public),
    }


def specification_parameters(name: str) -> list[dict]:
    """Читает только явно подписанные размеры, объём и PN из наименования позиции."""
    result = []
    pipe = re.search(r"\b(\d+(?:[.,]\d+)?)\s*[×xх*]\s*(\d+(?:[.,]\d+)?)", name)
    values = []
    if pipe and re.search(r"труб|отвод|втулк", name, re.I):
        values.extend(
            (("outer_diameter", pipe[1], "мм"), ("wall_thickness", pipe[2], "мм"))
        )
    volume = re.search(r"\bV\s*=\s*(\d+(?:[.,]\d+)?)\s*м[3³]", name, re.I)
    if volume:
        values.append(("volume", volume[1], "м3"))
    pressure = re.search(r"\bPN\s*(\d+(?:[.,]\d+)?)\b", name, re.I)
    if pressure:
        values.append(("rated_pressure", pressure[1], "бар"))
    for prop, value, unit in values:
        result.append(
            {
                "property_name": prop,
                "value_raw": value,
                "unit_raw": unit,
                "evidence_text": name,
                "evidence_status": "confirmed",
            }
        )
    return result
