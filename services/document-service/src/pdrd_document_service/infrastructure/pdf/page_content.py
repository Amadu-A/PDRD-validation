# services/document-service/src/pdrd_document_service/infrastructure/pdf/page_content.py

"""Отличает текстовую спецификацию от смешанного листа по геометрии исходного PDF."""

import fitz


def page_content_metadata(
    page: fitz.Page, specification_boxes: list, tables: list
) -> dict:
    """Разрешает программный маршрут только при отсутствии необъяснённой графики.

    Таблица, текст и проверенный основной штамп объясняют свои области.
    Изображение/кривая/связь вне этих областей оставляет лист на визуальном пути.
    Стандартная рамка страницы сама по себе не считается инженерной схемой.
    """
    table_boxes = [fitz.Rect(box) for box in specification_boxes]
    stamps = []
    candidate = fitz.Rect(
        page.rect.width * 0.5,
        page.rect.height * 0.78,
        page.rect.width,
        page.rect.height,
    )
    candidate_text = page.get_textbox(candidate).casefold()
    if (
        "лист" in candidate_text
        and "изм" in candidate_text
        and ("подп" in candidate_text or "дата" in candidate_text)
    ):
        stamps.append(candidate)
    for table in tables:
        box = fitz.Rect(table.bbox)
        if box.x0 < page.rect.width * 0.45 or box.y0 < page.rect.height * 0.75:
            continue
        text = page.get_textbox(box).casefold()
        if "лист" in text and ("изм" in text or "подп" in text):
            stamps.append(box)
    outside_lines, text_boxes = [], []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            box = fitz.Rect(line["bbox"])
            if _covered(box, table_boxes):
                continue
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                outside_lines.append(text)
                text_boxes.append(box)
    outside_images = sum(
        not _covered(fitz.Rect(image["bbox"]), stamps)
        for image in page.get_image_info()
    )
    outside_graphics = 0
    for drawing in page.get_drawings():
        box = fitz.Rect(drawing["rect"])
        if _covered(box, [*table_boxes, *stamps, *text_boxes]):
            continue
        if all(_page_border(item, page.rect) for item in drawing["items"]):
            continue
        outside_graphics += 1
    outside_text = "\n".join(outside_lines)
    # Нетабличные инструкции и формулы требуют чтения, даже если у листа нет изображений.
    substantive_text = any(len(line) > 180 for line in outside_lines)
    requires_vision = bool(outside_images or outside_graphics or substantive_text)
    return {
        "version": 1,
        "route": "mixed" if requires_vision else "pdf_table",
        "table_regions": [_normalized(box, page) for box in table_boxes],
        "outside_text": outside_text,
        "outside_images": outside_images,
        "outside_graphics": outside_graphics,
        "requires_vision": requires_vision,
        "reason": "non_table_content"
        if requires_vision
        else "text_table_and_title_block",
    }


def _covered(box: fitz.Rect, regions: list[fitz.Rect]) -> bool:
    """Принимает геометрию лишь при полном покрытии одной известной областью."""
    return any(
        fitz.Rect(region.x0 - 2, region.y0 - 2, region.x1 + 2, region.y1 + 2).contains(
            box
        )
        for region in regions
    )


def _page_border(item: tuple, page: fitz.Rect) -> bool:
    """Учитывает только прямоугольную рамку и её линии возле края листа."""
    if item[0] == "re":
        box = fitz.Rect(item[1])
        return box.width >= page.width * 0.9 and box.height >= page.height * 0.9
    if item[0] != "l":
        return False
    start, end = item[1:3]
    return (
        abs(start.x - end.x) < 0.1
        and (start.x < page.width * 0.05 or start.x > page.width * 0.95)
    ) or (
        abs(start.y - end.y) < 0.1
        and (start.y < page.height * 0.05 or start.y > page.height * 0.95)
    )


def _normalized(box: fitz.Rect, page: fitz.Page) -> dict:
    """Сохраняет область таблицы в действующей системе визуальных координат."""
    box = box * page.rotation_matrix if page.rotation else box
    return {
        "x_min": max(0, int(box.x0 / page.rect.width * 1000)),
        "y_min": max(0, int(box.y0 / page.rect.height * 1000)),
        "x_max": min(1000, int(box.x1 / page.rect.width * 1000 + 0.999)),
        "y_max": min(1000, int(box.y1 / page.rect.height * 1000 + 0.999)),
    }
