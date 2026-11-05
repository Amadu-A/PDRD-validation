# services/document-service/src/pdrd_document_service/infrastructure/pdf/sidebar.py

"""Геометрия правой зоны PDF без изменения исходного листа и координат Review."""

import re
from dataclasses import dataclass
from html import escape

import fitz


@dataclass(frozen=True)
class SidebarCard:
    """Локальная раскладка экспорта; не записывается в сохранённый Review."""

    rect: fitz.Rect
    text: str
    font_size: float
    body_height: float


def card_style(font_size: float) -> str:
    """Использует одинаковую типографику при измерении и записи FreeText."""
    return (
        f"font-family: sans-serif; font-size: {font_size}pt; color: #202735; margin: 0;"
    )


def card_html(text: str) -> str:
    """Экранирует пользовательский текст до передачи HTML-движку PDF."""
    return "<p>" + escape(text).replace("\n", "<br>") + "</p>"


def summary_text(title: str, *, located: bool) -> str:
    """Оставляет человекочитаемый вывод; техническое происхождение хранится в отчёте."""
    text = re.sub(r"\b(?:D-p\d+-[\w-]+|assertion-[\w-]+|EQ-[a-f0-9]{12,})\b", "", title)
    text = " ".join(text.split()).strip(" ;,")
    if not located:
        text = "Точное место на листе не определено. " + text
    if len(text) > 600:
        text = text[:580].rsplit(" ", 1)[0].rstrip(" ,;.") + "…"
    return text + "\nПолный текст и основания — в отчёте."


def plan_cards(
    original: fitz.Rect, entries: list[tuple[str, float]]
) -> tuple[list[SidebarCard], float]:
    """Автоматически наращивает число колонок, не уменьшая заданный шрифт."""
    gap, padding = 10.0, 6.0
    width = max(220.0, min(480.0, original.width * 0.32))
    x, y = original.width + gap, gap
    cards = []
    for text, font_size in entries:
        width = max(width, font_size * 20)
        story = fitz.Story(
            html=card_html(text),
            user_css="body {margin:0;} p {" + card_style(font_size) + "}",
        )
        _, filled = story.place(fitz.Rect(0, 0, width - 2 * padding, 100000))
        body_height = fitz.Rect(filled).y1 + font_size * 0.75
        height = body_height + font_size * 1.5 + 3 * padding
        if height > original.height - 2 * gap:
            # Очень длинная запись получает краткую ссылку без обрезки полного отчёта.
            text = (
                text[:180].rsplit(" ", 1)[0] + "…\nПолный текст и основания — в отчёте."
            )
            story = fitz.Story(
                html=card_html(text),
                user_css="body {margin:0;} p {" + card_style(font_size) + "}",
            )
            _, filled = story.place(fitz.Rect(0, 0, width - 2 * padding, 100000))
            body_height = fitz.Rect(filled).y1 + font_size * 0.75
            height = body_height + font_size * 1.5 + 3 * padding
        if y + height > original.height - gap:
            x += width + gap
            y = gap
        rect = fitz.Rect(x, y, x + width, y + height)
        cards.append(SidebarCard(rect, text, font_size, body_height))
        y += height + gap
    return cards, max(
        (card.rect.x1 for card in cards), default=original.width
    ) + gap - original.width


def _raw_box(page: fitz.Page, key: str) -> list[float]:
    """Читает унаследованные PDF-боксы в исходной системе координат PDF."""
    doc, xref = page.parent, page.xref
    while xref:
        kind, value = doc.xref_get_key(xref, key)
        if kind == "array":
            return [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", value)]
        kind, parent = doc.xref_get_key(xref, "Parent")
        xref = int(parent.split()[0]) if kind == "xref" else 0
    return list(page.mediabox)


def extend_right(page: fitz.Page, width: float) -> fitz.Page:
    """Расширяет визуальный правый край с учётом поворота и ненулевых боксов.

    Исходные потоки ограничиваются прежним CropBox: скрытое за ним содержимое
    не появляется в новой зоне. Потоки, нативные аннотации и масштаб сохраняются.
    """
    doc = page.parent
    crop = _raw_box(page, "CropBox")
    contents = page.get_contents()
    if contents:
        prefix = doc.get_new_xref()
        doc.update_object(prefix, "<<>>")
        doc.update_stream(
            prefix,
            (
                f"q {crop[0]} {crop[1]} {crop[2] - crop[0]} {crop[3] - crop[1]} re W n\n"
            ).encode(),
        )
        suffix = doc.get_new_xref()
        doc.update_object(suffix, "<<>>")
        doc.update_stream(suffix, b"\nQ\n")
        doc.xref_set_key(
            page.xref,
            "Contents",
            "[" + " ".join(f"{xref} 0 R" for xref in (prefix, *contents, suffix)) + "]",
        )
    for key in ("MediaBox", "CropBox"):
        box = _raw_box(page, key) if key == "MediaBox" else crop.copy()
        index, sign = {0: (2, 1), 90: (3, 1), 180: (0, -1), 270: (1, -1)}[page.rotation]
        box[index] += sign * width
        doc.xref_set_key(page.xref, key, "[" + " ".join(str(v) for v in box) + "]")
    return doc.reload_page(page)
