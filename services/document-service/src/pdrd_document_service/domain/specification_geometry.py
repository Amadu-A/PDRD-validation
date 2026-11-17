# services/document-service/src/pdrd_document_service/domain/specification_geometry.py

"""Доказанная привязка внешней позиции к исходным подписям одной строки PDF."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation


def label_key(text: str) -> str:
    """Нормализует только Unicode и оформление, не инженерные значения."""
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", text).casefold())


def _quantity(text: str):
    """Читает явное число; формулы и составные количества не угадываются."""
    if not re.fullmatch(r"\s*[+-]?\d+(?:[.,]\d+)?\s*", text):
        return None
    try:
        return Decimal(text.strip().replace(",", "."))
    except InvalidOperation:
        return None


def _contains_name(name: str, text: str) -> bool:
    """Не принимает числовой префикс 110x10 за размер 110x100."""
    source = label_key(text)
    start = source.find(name)
    if start < 0:
        return False
    end = start + len(name)
    return not (
        (name[-1].isdigit() and end < len(source) and source[end].isdigit())
        or (name[0].isdigit() and start and source[start - 1].isdigit())
    )


def bind_pdf_row(original: dict, native: dict) -> list[dict]:
    """Возвращает реальные bbox при доказанном имени, строке, единице и количестве.

    Состав приходит от Spec. Здесь проверяется только связь с геометрией;
    неполное или неоднозначное совпадение оставляет geometry_unresolved.
    Границы областей не интерполируются.
    """
    spans, lines = native.get("native_spans", []), native.get("graphic_lines", [])
    # ГОСТ в Caption — запись проекта, а не нормативное подтверждение.
    parts = re.split(r"\s+(?=(?:ГОСТ|СП|ТУ)\s)", original["Caption"], maxsplit=1)
    name = label_key(parts[0])
    reference = label_key(parts[1]) if len(parts) == 2 else ""
    value = _quantity(original["Quantity"])
    if not name or value is None:
        return []
    anchors = [span for span in spans if _contains_name(name, span["text"])]
    row_lines = sorted(
        {
            round((line["y1"] + line["y2"]) / 2, 3)
            for line in lines
            if abs(line["y1"] - line["y2"]) <= 0.5
            and abs(line["x1"] - line["x2"]) >= 300
        }
    )
    matches = []
    for anchor in anchors:
        center = (anchor["y_min"] + anchor["y_max"]) / 2
        above, below = (
            [y for y in row_lines if y < center],
            [y for y in row_lines if y > center],
        )
        if not above or not below:
            continue
        top, bottom = max(above), min(below)
        row = [
            span
            for span in spans
            if span["y_min"] >= top - 1 and span["y_max"] <= bottom + 1
        ]
        if anchor not in row:
            continue
        text = " ".join(
            span["text"] for span in sorted(row, key=lambda s: (s["x_min"], s["y_min"]))
        )
        if reference and reference not in label_key(text):
            continue
        if any(
            original.get(key) and label_key(original[key]) not in label_key(text)
            for key in ("Brand", "Article")
        ):
            continue
        units = [
            span
            for span in row
            if label_key(span["text"]) == label_key(original["Units"])
            and span["x_min"] > anchor["x_max"]
        ]
        for unit in units:
            numbers = sorted(
                (
                    span
                    for span in row
                    if span["x_min"] > unit["x_max"]
                    and _quantity(span["text"]) is not None
                ),
                key=lambda s: s["x_min"],
            )
            if numbers and _quantity(numbers[0]["text"]) == value:
                matches.append([anchor, unit, numbers[0]])
    if len(matches) != 1:
        return []
    return [
        {key: span[key] for key in ("x_min", "y_min", "x_max", "y_max")}
        for span in matches[0]
    ]
